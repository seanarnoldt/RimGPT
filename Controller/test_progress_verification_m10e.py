import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, Pricing, serialized_chars
from decision_context import DecisionContextBuilder
from decision_handoff import prepare_handoff
from model_tool_result import ModelToolResultFormatter
from progress_tracking import (
    MAX_STALL_LOOPS,
    MAX_VERIFICATION_STRATEGIES,
    build_progress_signals,
    build_stall_context,
    empty_stall_metadata,
    record_verification_outcome,
    update_open_loop_stalls,
    validate_stall_metadata,
)
from prompt_runtime import RIMGPT_PROMPT_VERSION
from state_store import StateIdentity, StateStore
from test_decision_handoff import FakeResponses, call, finish, response
from test_state_diff import base_state


def room(*, enclosed=True, temperature=21.5, room_id="room-7-4"):
    return {
        "id": room_id,
        "indoors": enclosed,
        "enclosed": enclosed,
        "usesOutdoorTemperature": not enclosed,
        "suitableForTemperatureControl": enclosed,
        "cellCount": 24 if enclosed else 120,
        "roofedCellCount": 24 if enclosed else 10,
        "roofCoverage": 1.0 if enclosed else 0.0833,
        "temperature": temperature,
        "bounds": {"minX": 40, "minZ": 40, "maxX": 45, "maxZ": 43},
    }


def room_result(*, enclosed=True):
    return {
        "schemaVersion": 2,
        "gameLoaded": True,
        "mapId": "map-7",
        "position": {"x": 42, "z": 42},
        "roofedAtCell": enclosed,
        "room": room(enclosed=enclosed, temperature=21.5 if enclosed else 38.0),
    }


def shelter_loop(loop_id=None):
    return {
        "id": loop_id,
        "objective": "Make the starter shelter temperature safe",
        "next_action": "Complete the wall and verify the room is enclosed",
        "status": "pending",
        "reason": "Passive cooling requires an enclosed room",
    }


class RoomCycleBridge:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.room_calls = []
        self.map_calls = 0
        self.submitted = []

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        return copy.deepcopy(self.state)

    def inspect_room_at(self, x, z):
        self.room_calls.append((x, z))
        return room_result(enclosed=True)

    def inspect_map(self, *args):
        self.map_calls += 1
        raise AssertionError("room verification must not use inspect_map")


class ProgressVerificationM10ETests(unittest.TestCase):
    def setUp(self):
        self.formatter = ModelToolResultFormatter(logger=lambda _: None)

    def test_room_tool_preserves_enclosed_roof_and_temperature_semantics_under_one_k(self):
        result = self.formatter.format("inspect_room_at", room_result(enclosed=True), {"x": 42, "z": 42})
        self.assertTrue(result["room"]["enclosed"])
        self.assertTrue(result["room"]["indoors"])
        self.assertFalse(result["room"]["usesOutdoorTemperature"])
        self.assertTrue(result["room"]["suitableForTemperatureControl"])
        self.assertEqual(result["room"]["roofedCellCount"], 24)
        self.assertEqual(result["room"]["temperature"], 21.5)
        self.assertLess(serialized_chars(result), 1_000)

    def test_room_tool_preserves_unenclosed_outdoor_temperature_semantics(self):
        result = self.formatter.format("inspect_room_at", room_result(enclosed=False), {"x": 42, "z": 42})
        self.assertFalse(result["room"]["enclosed"])
        self.assertFalse(result["room"]["indoors"])
        self.assertTrue(result["room"]["usesOutdoorTemperature"])
        self.assertFalse(result["room"]["suitableForTemperatureControl"])
        self.assertEqual(result["room"]["temperature"], 38.0)

    def test_unroomed_cell_is_explicit(self):
        raw = {**room_result(), "room": None, "roofedAtCell": False}
        result = self.formatter.format("inspect_room_at", raw, {"x": 42, "z": 42})
        self.assertEqual(result["room"], "unroomed")

    def test_wall_blueprint_completion_produces_construction_progress(self):
        baseline = base_state()
        baseline["buildings"].append({
            "id": "Blueprint_Wall_12", "type": "blueprint", "defName": "Blueprint_Wall",
            "position": {"x": 42, "z": 40}, "rotation": "North",
        })
        current = copy.deepcopy(baseline)
        current["snapshot"]["version"] += 1
        current["buildings"][-1] = {
            "id": "Wall_12", "type": "building", "defName": "Wall",
            "position": {"x": 42, "z": 40}, "rotation": "North",
        }
        progress = build_progress_signals(baseline, current)
        advanced = [item for item in progress["signals"] if item["type"] == "constructionAdvanced"]
        self.assertEqual(advanced[0]["from"]["id"], "Blueprint_Wall_12")
        self.assertEqual(advanced[0]["to"]["id"], "Wall_12")
        self.assertIn("construction", progress["categories"])

    def test_unenclosed_to_enclosed_room_transition_is_visible(self):
        baseline = base_state()
        current = copy.deepcopy(baseline)
        baseline["buildings"][0]["room"] = room(enclosed=False)
        current["buildings"][0]["room"] = room(enclosed=True)
        current["snapshot"]["version"] += 1
        progress = build_progress_signals(baseline, current)
        transition = next(item for item in progress["signals"] if item["type"] == "roomTopologyChanged")
        self.assertFalse(transition["from"]["enclosed"])
        self.assertTrue(transition["to"]["enclosed"])
        self.assertIn("room", progress["categories"])

    def test_room_id_churn_alone_is_not_reported_as_progress(self):
        baseline = base_state()
        current = copy.deepcopy(baseline)
        baseline["buildings"][0]["room"] = room(enclosed=True, room_id="room-7-4")
        current["buildings"][0]["room"] = room(enclosed=True, room_id="room-7-19")
        current["snapshot"]["version"] += 1
        progress = build_progress_signals(baseline, current)
        self.assertNotIn("roomTopologyChanged", [item["type"] for item in progress["signals"]])

    def test_successful_prerequisite_guides_open_loop_advancement(self):
        identity = StateIdentity("lineage-a").as_dict()
        handoff = prepare_handoff({"assessment": "Shelter", "open_loops": [shelter_loop()], "resolved_loops": []}, None)
        progress = {
            "relevantStateChanged": True,
            "categories": ["construction", "room"],
            "signals": [{"type": "roomTopologyChanged", "category": "room"}],
        }
        context = build_stall_context(empty_stall_metadata(identity), handoff, progress)
        self.assertEqual(context["openLoops"][0]["progressSinceBaseline"], ["roomTopologyChanged"])
        self.assertIn("advance or resolve", context["openLoops"][0]["guidance"])

    def test_inert_non_stalled_loops_do_not_consume_context(self):
        identity = StateIdentity("lineage-a").as_dict()
        handoff = prepare_handoff({"assessment": "Shelter", "open_loops": [shelter_loop()], "resolved_loops": []}, None)
        context = build_stall_context(
            empty_stall_metadata(identity),
            handoff,
            {"relevantStateChanged": False, "categories": [], "signals": []},
        )
        self.assertIsNone(context)

    def test_repeated_unchanged_no_progress_loop_is_flagged_stalled(self):
        identity = StateIdentity("lineage-a").as_dict()
        first = prepare_handoff({"assessment": "Shelter", "open_loops": [shelter_loop()], "resolved_loops": []}, None)
        same = prepare_handoff({"assessment": "Still shelter", "open_loops": [{**shelter_loop(first["openLoops"][0]["id"])}], "resolved_loops": []}, first)
        no_progress = {"relevantStateChanged": False, "categories": [], "signals": []}
        metadata = update_open_loop_stalls(empty_stall_metadata(identity), None, first, no_progress)
        metadata = update_open_loop_stalls(metadata, first, same, no_progress)
        context = build_stall_context(metadata, same, no_progress)
        self.assertTrue(context["openLoops"][0]["stalled"])
        self.assertIn("Do not repeat", context["openLoops"][0]["guidance"])

    def test_stall_metadata_is_bounded_colony_scoped_and_contains_no_raw_history(self):
        identity = StateIdentity("lineage-a").as_dict()
        metadata = empty_stall_metadata(identity)
        for index in range(20):
            metadata = record_verification_outcome(
                metadata, "inspect_room_at", {"x": index, "z": index}, success=False
            )
        validated = validate_stall_metadata(metadata, identity)
        self.assertLessEqual(len(validated["failedVerificationStrategies"]), MAX_VERIFICATION_STRATEGIES)
        encoded = json.dumps(validated)
        for forbidden in ("transcript", "output", "result", "reason"):
            self.assertNotIn(f'"{forbidden}"', encoded)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            validate_stall_metadata(validated, StateIdentity("lineage-b").as_dict())

        many = {**validated, "loops": [
            {"id": f"L-{index}", "nextActionFingerprint": f"f-{index}", "repeatedNextActionCycles": 2, "noRelevantProgressCycles": 2}
            for index in range(20)
        ]}
        self.assertLessEqual(len(validate_stall_metadata(many, identity)["loops"]), MAX_STALL_LOOPS)

    def test_state_store_persists_bounded_strategy_but_dry_run_controller_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            store.update_current_state(base_state())
            controller = object.__new__(AgentController)
            controller.state_store = store
            controller.dry_run = True
            controller._record_verification_result("inspect_room_at", {"x": 1, "z": 2}, success=False)
            self.assertFalse(store._stall_path().exists())

            controller.dry_run = False
            controller._record_verification_result("inspect_room_at", {"x": 1, "z": 2}, success=False)
            persisted = json.loads(store._stall_path().read_text(encoding="utf-8"))
            self.assertEqual(persisted["failedVerificationStrategies"][0]["strategy"], "inspect_room_at(1,2)")

    def test_room_verification_cycle_uses_targeted_tool_and_stays_below_guard(self):
        baseline = base_state(version=200)
        baseline["buildings"][0]["room"] = room(enclosed=False)
        current = copy.deepcopy(baseline)
        current["snapshot"]["version"] = 201
        current["buildings"][0]["room"] = room(enclosed=True)
        bridge = RoomCycleBridge(current)
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            store.update_current_state(baseline)
            prior = prepare_handoff({
                "assessment": "Shelter needs enclosure",
                "open_loops": [shelter_loop()],
                "resolved_loops": [],
            }, None)
            store.commit_successful_decision(baseline, prior)
            loop_id = prior["openLoops"][0]["id"]
            fake = FakeResponses([
                response("room-r1", [call("inspect_room_at", {"x": 42, "z": 42}, "verify")]),
                response("room-r2", [finish([], [{
                    "id": loop_id,
                    "resolution": "completed",
                    "reason": "Authoritative room inspection confirms enclosure",
                }], assessment="The shelter is enclosed")]),
            ])
            controller = AgentController(
                bridge=bridge,
                model="test-model",
                client=SimpleNamespace(responses=fake),
                state_store=store,
                pricing=Pricing(),
            )
            controller.run_once()
            self.assertEqual(store.get_decision_handoff()["openLoops"], [])

        self.assertEqual(bridge.room_calls, [(42, 42)])
        self.assertEqual(bridge.map_calls, 0)
        self.assertEqual(len(fake.calls), 2)
        self.assertIsNone(controller.termination_reason)
        self.assertLess(max(
            (len(SYSTEM_INSTRUCTIONS) + serialized_chars(item["tools"]) + serialized_chars(item["input"])) // 3
            for item in fake.calls
        ), DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertTrue(all(call_item.get("input") for call_item in fake.calls))

    def test_context_exposes_progress_stall_guidance_and_never_sends_full_state(self):
        baseline = base_state(version=300)
        current = copy.deepcopy(baseline)
        current["snapshot"]["version"] += 1
        baseline["buildings"][0]["room"] = room(enclosed=False)
        current["buildings"][0]["room"] = room(enclosed=True)
        with tempfile.TemporaryDirectory() as directory:
            logs = []
            store = StateStore(Path(directory), logger=logs.append)
            store.update_current_state(baseline)
            store.set_decision_baseline(baseline)
            store.update_current_state(current)
            context = DecisionContextBuilder(store, logger=logs.append).build()
        self.assertTrue(context["progressSinceLastDecision"]["relevantStateChanged"])
        self.assertTrue(any("fullStateSent=false" in item for item in logs))
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10e1")
        self.assertIn("inspect_room_at", SYSTEM_INSTRUCTIONS)
        self.assertIn("Use inspect_map only for actual spatial placement and planning", SYSTEM_INSTRUCTIONS)

    def test_csharp_endpoint_uses_main_thread_read_queue(self):
        root = Path(__file__).parent.parent / "Source"
        http = (root / "RimGPTHttpBridge.cs").read_text(encoding="utf-8")
        component = (root / "RimGPTGameComponent.cs").read_text(encoding="utf-8")
        spatial = (root / "RimGPTSpatialJson.cs").read_text(encoding="utf-8")
        self.assertIn('path == "/room/at"', http)
        self.assertIn("RimGPTReadRequestType.RoomAt", http)
        self.assertIn("case RimGPTReadRequestType.RoomAt", component)
        self.assertIn("BuildRoomAtJson", spatial)
        self.assertIn("RegionAndRoomQuery.RoomAt", spatial)


if __name__ == "__main__":
    unittest.main()
