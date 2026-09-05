import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from context_telemetry import Pricing, serialized_chars
from state_store import StateStore, snapshot_version
from test_state_diff import base_state


def call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


def response(response_id, output, output_text=""):
    return SimpleNamespace(id=response_id, output=output, status="completed", output_text=output_text, usage=None)


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class V3CycleBridge:
    def __init__(self, initial, final):
        self.initial = copy.deepcopy(initial)
        self.final = copy.deepcopy(final)
        self.current = copy.deepcopy(initial)
        self.submitted = []
        self.waited_after = []
        self.recipe_calls = []

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        return copy.deepcopy(self.current)

    def list_recipes(self, worktable_id):
        self.recipe_calls.append(worktable_id)
        return {
            "worktableId": worktable_id,
            "recipes": [{"recipeDef": "MakeMealSimple", "label": "make simple meal", "currentlyAvailable": True}],
        }

    def submit_command(self, command):
        self.submitted.append(copy.deepcopy(command))
        return {"commandId": f"cmd-{len(self.submitted)}", "startedAt": time.monotonic(), "submitElapsedSeconds": 0.0}

    def wait_for_commands(self, submissions):
        return {
            item["commandId"]: {
                "commandId": item["commandId"],
                "status": "completed",
                "success": True,
                "elapsedSeconds": 0.01,
                "data": {},
            }
            for item in submissions
        }

    def wait_for_state_after(self, after_version, timeout_ms=3000):
        self.waited_after.append((after_version, timeout_ms))
        self.current = copy.deepcopy(self.final)
        return copy.deepcopy(self.current)


class V3ModelCycleTests(unittest.TestCase):
    def make_state(self, version):
        state = base_state(version=version)
        state["operations"]["rawMarker"] = "OPERATIONS_MUST_NOT_BE_INITIAL_CONTEXT"
        state["operations"]["equipment"] = {"availableWeapons": [{"id": "Gun_1", "defName": "Gun_Revolver", "label": "revolver", "position": {"x": 5, "z": 5}}]}
        state["operations"]["apparel"] = {"available": [{"id": "Hat_1", "defName": "Apparel_CowboyHat", "label": "cowboy hat", "position": {"x": 6, "z": 6}}]}
        state["operations"]["fuel"] = [{"id": "Generator_1", "defName": "FueledGenerator", "label": "generator", "targetFuelLevel": 25.0}]
        state["operations"]["power"] = {"networks": [{"id": "powerNet-0", "netWatts": 100.0}]}
        return state

    def run_cycle(self, group, read_call, write_call, expected_command, *, include_recipes=False):
        with tempfile.TemporaryDirectory() as directory:
            initial = self.make_state(200)
            final = self.make_state(201)
            bridge = V3CycleBridge(initial, final)
            steps = [
                response("r1", [call("enable_capability", {"name": group}, "enable")]),
                response("r2", [read_call]),
            ]
            if include_recipes:
                steps.append(response("r3", [call("list_recipes", {"worktable_id": "Building_Bench"}, "recipes")]))
                steps.append(response("r4", [write_call]))
                steps.append(response("r5", [], "Done."))
            else:
                steps.append(response("r3", [write_call]))
                steps.append(response("r4", [], "Done."))
            responses = FakeResponses(steps)
            store = StateStore(Path(directory), logger=lambda _: None)
            controller = AgentController(
                bridge=bridge,
                model="test-model",
                client=SimpleNamespace(responses=responses),
                state_store=store,
                pricing=Pricing(),
            )

            controller.run_once({"type": "v3Mock"})

            self.assertEqual(bridge.submitted, [expected_command])
            self.assertEqual(bridge.waited_after, [(200, 3000)])
            self.assertEqual(snapshot_version(store.get_decision_baseline()), 201)
            self.assertIsNone(controller.termination_reason)
            self.assertIn(write_call.name, {item["name"] for item in responses.calls[1]["tools"]})
            self.assertNotIn("OPERATIONS_MUST_NOT_BE_INITIAL_CONTEXT", responses.calls[0]["input"][0]["content"][0]["text"])
            self.assertNotIn('"operations"', responses.calls[0]["input"][0]["content"][0]["text"])
            self.assertTrue(all((len(SYSTEM_INSTRUCTIONS) + serialized_chars(item["tools"]) + serialized_chars(item["input"])) // 3 < 30_000 for item in responses.calls))
            final_input = responses.calls[-1]["input"]
            command_output = next(item for item in final_input if item.get("type") == "function_call_output")
            compact = json.loads(command_output["output"])
            self.assertTrue(compact["success"])
            self.assertNotIn("commandId", compact)
            return bridge, responses

    def test_equipment_cycle_activates_reads_equips_and_refreshes_state(self):
        self.run_cycle(
            "equipment",
            call("get_colony_state", {"section": "equipment"}, "equipment-state"),
            call("equip_weapon", {"pawn_id": "Pawn_A", "thing_id": "Gun_1"}, "equip"),
            {"command": "equipWeapon", "pawnId": "Pawn_A", "thingId": "Gun_1"},
        )

    def test_production_cycle_reads_recipes_adds_bill_and_refreshes_state(self):
        bridge, _ = self.run_cycle(
            "production",
            call("get_colony_state", {"section": "worktables"}, "worktables-state"),
            call("add_bill", {"worktable_id": "Building_Bench", "recipe_def": "MakeMealSimple", "repeat_mode": "untilX", "target_count": 20}, "add-bill"),
            {"command": "addBill", "worktableId": "Building_Bench", "recipeDef": "MakeMealSimple", "repeatMode": "untilX", "targetCount": 20},
            include_recipes=True,
        )
        self.assertEqual(bridge.recipe_calls, ["Building_Bench"])

    def test_work_cycle_activates_reads_and_prioritizes_haul(self):
        self.run_cycle(
            "work",
            call("get_colony_state", {"section": "work"}, "work-state"),
            call("prioritize_haul", {"pawn_id": "Pawn_A", "thing_id": "Steel_1"}, "haul"),
            {"command": "prioritizeHaul", "pawnId": "Pawn_A", "thingId": "Steel_1"},
        )

    def test_power_cycle_activates_reads_switches_and_refreshes_state(self):
        self.run_cycle(
            "power",
            call("get_colony_state", {"section": "power"}, "power-state"),
            call("set_power_switch", {"thing_id": "Switch_1", "on": True}, "switch"),
            {"command": "setPowerSwitch", "thingId": "Switch_1", "on": True},
        )


if __name__ == "__main__":
    unittest.main()
