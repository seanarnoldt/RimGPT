import copy
import tempfile
import unittest
from pathlib import Path

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from colony_state_query import ColonyStateQuery, serialized_chars
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST
from decision_context import DecisionContextBuilder
from model_tool_result import ModelToolResultFormatter
from state_diff import StateDiff
from state_store import StateStore
from test_state_diff import base_state


def room(room_id: str, temperature: float) -> dict:
    return {
        "id": room_id,
        "indoors": True,
        "enclosed": True,
        "usesOutdoorTemperature": False,
        "suitableForTemperatureControl": True,
        "cellCount": 24,
        "roofedCellCount": 24,
        "roofCoverage": 1.0,
        "temperature": temperature,
        "bounds": {"minX": 40, "minZ": 40, "maxX": 45, "maxZ": 43},
    }


def building_state(version: int = 120) -> dict:
    state = base_state(version=version)
    walls = [
        {
            "id": f"Wall_{index}",
            "type": "building",
            "defName": "Wall",
            "position": {"x": 40 + index % 20, "z": 40 + index // 20},
            "rotation": "North",
        }
        for index in range(120)
    ]
    state["buildings"] = state["buildings"] + walls + [
        {
            "id": "Bed_1",
            "type": "building",
            "defName": "Bed",
            "position": {"x": 52, "z": 52},
            "rotation": "North",
        },
        {
            "id": "Generator_1",
            "type": "building",
            "defName": "WoodFiredGenerator",
            "position": {"x": 60, "z": 60},
            "rotation": "North",
            "powered": True,
        },
        {
            "id": "PassiveCooler_1",
            "type": "building",
            "defName": "PassiveCooler",
            "position": {"x": 53, "z": 52},
            "rotation": "North",
        },
        {
            "id": "Blueprint_Wall_1",
            "type": "blueprint",
            "defName": "Blueprint_Wall",
            "position": {"x": 64, "z": 40},
            "rotation": "North",
            "stuffDef": "WoodLog",
        },
    ]
    state["operations"]["fuel"].append(
        {
            "id": "PassiveCooler_1",
            "defName": "PassiveCooler",
            "fuel": 8.0,
            "fuelCapacity": 25.0,
            "needsFuel": True,
        }
    )
    return state


class ContextCleanupM10E1Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(Path(self.temporary.name), logger=lambda _: None)

    def test_buildings_query_keeps_essential_operational_and_construction_facts_compact(self):
        self.store.update_current_state(building_state())
        result = ColonyStateQuery(self.store).get("buildings")
        data = result["data"]
        entries = {item["id"]: item for item in data["entries"]}

        self.assertLess(serialized_chars(result), 3_000)
        self.assertEqual(data["total"], 125)
        self.assertEqual(data["omittedCompletedCount"], 120)
        self.assertEqual(entries["Building_Bench"]["operation"]["kind"], "worktable")
        self.assertEqual(entries["Bed_1"]["operation"]["kind"], "bed")
        self.assertEqual(entries["PassiveCooler_1"]["operation"]["kind"], "fuel")
        self.assertTrue(entries["Generator_1"]["powered"])
        self.assertEqual(entries["Blueprint_Wall_1"]["type"], "blueprint")
        self.assertEqual(entries["Blueprint_Wall_1"]["position"], {"x": 64, "z": 40})

    def test_same_colony_snapshot_restart_ignores_recreated_map_ids_and_volatile_room_values(self):
        baseline = base_state(version=120, ticks=1_000)
        baseline["mapThings"]["haulable"] = [
            {
                "id": f"ChunkGranite_old_{index}",
                "defName": "ChunkGranite",
                "position": {"x": 30 + index, "z": 45},
                "stackCount": 1,
                "forbidden": False,
            }
            for index in range(70)
        ]
        baseline["buildings"][0]["room"] = room("room-0-25", 21.2)
        current = copy.deepcopy(baseline)
        current["snapshot"].update({"version": 1, "ticksGame": 1_100})
        current["game"]["ticksGame"] = 1_100
        for index, item in enumerate(current["mapThings"]["haulable"]):
            item["id"] = f"ChunkGranite_new_{index}"
        current["buildings"][0]["room"] = room("room-0-27", 23.8)

        delta = StateDiff.compare(baseline, current, max_delta_chars=100_000)
        self.assertTrue(delta["snapshotStreamReset"])
        self.assertNotIn("mapThings", delta["changes"])
        self.assertNotIn("construction", delta["changes"])
        self.assertLess(serialized_chars(delta), 5_000)

    def test_real_building_additions_and_removals_survive_restart_normalization(self):
        baseline = base_state()
        current = copy.deepcopy(baseline)
        current["snapshot"]["version"] += 1
        current["buildings"].append(
            {"id": "Wall_New", "type": "building", "defName": "Wall", "position": {"x": 70, "z": 70}}
        )
        added = StateDiff.compare(baseline, current)["changes"]["construction"]["added"]
        self.assertEqual(added[0]["id"], "Wall_New")

        removed = StateDiff.compare(current, base_state(version=122))["changes"]["construction"]["removed"]
        self.assertEqual(removed[0]["id"], "Wall_New")

    def test_repeated_unchanged_buildings_read_returns_reuse_marker_and_fresh_state_is_allowed(self):
        self.store.update_current_state(building_state())
        controller = object.__new__(AgentController)
        controller.state_store = self.store
        controller.colony_state_query = ColonyStateQuery(self.store)
        controller.state_read_cache = {}

        first = controller._get_colony_state_result("buildings")
        second = controller._get_colony_state_result("buildings")
        self.assertNotIn("reused", first)
        self.assertTrue(second["reused"])
        self.assertLess(serialized_chars(second), 300)

        newer = building_state(version=121)
        newer["buildings"].append(
            {"id": "Blueprint_New", "type": "blueprint", "defName": "Blueprint_Bed", "position": {"x": 65, "z": 42}}
        )
        self.store.update_current_state(newer)
        fresh = controller._get_colony_state_result("buildings")
        self.assertNotIn("reused", fresh)
        self.assertEqual(fresh["snapshotVersion"], 121)

        controller._clear_state_read_cache()
        after_mutation = controller._get_colony_state_result("buildings")
        self.assertNotIn("reused", after_mutation)

    def test_representative_reused_building_results_stay_below_context_guard_without_full_state(self):
        state = building_state()
        self.store.update_current_state(state)
        self.store.set_decision_baseline(state)
        controller = object.__new__(AgentController)
        controller.state_store = self.store
        controller.colony_state_query = ColonyStateQuery(self.store)
        controller.state_read_cache = {}
        formatter = ModelToolResultFormatter(logger=lambda _: None)

        first = formatter.format(
            "get_colony_state",
            {"success": True, "result": controller._get_colony_state_result("buildings")},
            {"section": "buildings"},
        )
        reused = formatter.format(
            "get_colony_state",
            {"success": True, "result": controller._get_colony_state_result("buildings")},
            {"section": "buildings"},
        )
        logs = []
        context = DecisionContextBuilder(self.store, logger=logs.append).build()
        estimated_tokens = (len(SYSTEM_INSTRUCTIONS) + serialized_chars(context) + serialized_chars(first) + serialized_chars(reused)) // 3

        self.assertFalse(context["bootstrap"])
        self.assertTrue(any("fullStateSent=false" in line for line in logs))
        self.assertTrue(reused["reused"])
        self.assertLess(estimated_tokens, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)


if __name__ == "__main__":
    unittest.main()
