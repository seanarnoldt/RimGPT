import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController
from context_telemetry import Pricing
from state_store import StateStore
from test_state_diff import base_state
from tool_registry import DEFAULT_TOOL_REGISTRY


def call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


def response(response_id, output):
    return SimpleNamespace(id=response_id, output=output, status="completed", output_text="", usage=None)


def finish_call():
    loops = [
        {
            "id": None,
            "objective": objective,
            "next_action": next_action,
            "status": "pending",
            "reason": reason,
        }
        for objective, next_action, reason in (
            ("Complete starter shelter", "Validate and place the remaining shelter blueprints", "Planning is incomplete"),
            ("Establish food production", "Create the validated rice growing zone", "The colony needs renewable food"),
            ("Build a research bench", "Select and validate a bench location", "Research capability is absent"),
        )
    ]
    return call(
        "finish_decision",
        {"assessment": "Planning progressed; bounded follow-up remains.", "open_loops": loops, "resolved_loops": []},
        "finish",
    )


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.compact_calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0)

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        return SimpleNamespace(output=[{"id": "cmp", "type": "compaction", "encrypted_content": "opaque"}], usage=None)


class ReadBridge:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.inspect_calls = 0

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        return copy.deepcopy(self.state)

    def inspect_map(self, min_x, min_z, max_x, max_z):
        self.inspect_calls += 1
        return {"bounds": {"minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z}, "terrainRows": [], "things": []}

    def list_build_options(self, category=None, search=None):
        return {"options": [{"defName": "Wall", "requiredTerrainAffordance": "Medium"}]}

    def list_growable_plants(self):
        return {"plants": [{"defName": "Plant_Rice", "label": "rice plant"}]}

    def check_zone_placement(self, zone_type, min_x, min_z, max_x, max_z):
        return {"valid": True, "validCells": 36, "invalid": []}

    def check_build_placements(self, placements):
        return {"valid": True, "validCount": len(placements), "invalid": []}


def request_budget(request):
    for item in reversed(request["input"]):
        for content in item.get("content", []):
            text = content.get("text", "")
            if text.startswith('{"decisionBudget"'):
                return json.loads(text)["decisionBudget"]
    raise AssertionError("decisionBudget was not included")


class DecisionBudgetM10CTests(unittest.TestCase):
    def make_controller(self, responses, *, max_requests=8, threshold=20_000):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = base_state(version=500)
        store = StateStore(Path(temporary.name), logger=lambda _: None)
        return AgentController(
            bridge=ReadBridge(state),
            model="test-model",
            client=SimpleNamespace(responses=responses),
            state_store=store,
            pricing=Pricing(),
            max_model_requests_per_cycle=max_requests,
            compact_threshold_tokens=threshold,
        )

    def test_budget_counts_down_and_final_request_is_terminal_only(self):
        fake = FakeResponses([response("r1", []), response("r2", [])])
        controller = self.make_controller(fake)
        controller._begin_cycle()
        controller.model_request_count = 6
        controller._get_active_tools().enable("construction")
        groups_before = controller._get_active_tools().groups

        schemas, mode = controller._request_tool_surface()
        names = {item["name"] for item in schemas}
        self.assertEqual(mode, "immediate-work")
        self.assertIn("finish_decision", names)
        self.assertIn("place_blueprint", names)
        self.assertNotIn("inspect_map", names)
        self.assertNotIn("list_build_options", names)
        self.assertNotIn("enable_capability", names)

        controller.model_request_count = 7
        schemas, mode = controller._request_tool_surface()
        self.assertEqual(mode, "finalization-only")
        self.assertEqual([item["name"] for item in schemas], ["finish_decision"])
        controller._request_model([], state={})
        self.assertEqual(request_budget(fake.calls[0])["requestsRemaining"], 1)
        self.assertEqual(fake.calls[0]["tool_choice"], {"type": "function", "name": "finish_decision"})
        controller._execute_tool_call_batch([
            call("inspect_map", {"min_x": 1, "min_z": 1, "max_x": 2, "max_z": 2}, "blocked")
        ])
        self.assertEqual(controller.raw_tool_results[-1]["result"]["error"], "toolCapabilityNotEnabled")
        self.assertEqual(controller.bridge.inspect_calls, 0)
        self.assertEqual(controller._get_active_tools().groups, groups_before)

    def test_compaction_preserves_penultimate_and_final_requests(self):
        skipped = FakeResponses([response("r7", [])])
        controller = self.make_controller(skipped, threshold=1)
        controller._begin_cycle()
        controller.model_request_count = 6
        controller._request_model([], state={}, previous_response_id="r6")
        self.assertEqual(skipped.compact_calls, [])
        self.assertEqual(request_budget(skipped.calls[0])["requestsRemaining"], 2)

        allowed = FakeResponses([response("r7", [])])
        controller = self.make_controller(allowed, threshold=1)
        controller._begin_cycle()
        controller.model_request_count = 5
        controller._request_model([], state={}, previous_response_id="r5")
        self.assertEqual(len(allowed.compact_calls), 1)
        self.assertEqual(controller.model_request_count, 7)
        self.assertEqual(request_budget(allowed.calls[0])["requestsRemaining"], 2)

    def test_paid_run_failure_pattern_finalizes_in_seven_requests(self):
        fake = FakeResponses([
            response("r1", [call("inspect_map", {"min_x": 90, "min_z": 90, "max_x": 109, "max_z": 109}, "map-1")]),
            response("r2", [
                call("enable_capability", {"name": "construction"}, "construction"),
                call("enable_capability", {"name": "zones"}, "zones"),
            ]),
            response("r3", [
                call("list_build_options", {"category": None, "search": "starter"}, "builds"),
                call("list_growable_plants", {}, "plants"),
            ]),
            response("r4", [call("check_zone_placement", {"zone_type": "growing", "min_x": 95, "min_z": 95, "max_x": 100, "max_z": 100}, "zone")]),
            response("r5", [call("inspect_map", {"min_x": 100, "min_z": 100, "max_x": 114, "max_z": 114}, "map-2")]),
            response("r6", [call("check_build_placements", {"placements": [{"build_def": "Wall", "x": 105, "z": 105, "rotation": "North", "stuff_def": "WoodLog"}]}, "validate")]),
            response("r7", [finish_call()]),
        ])
        controller = self.make_controller(fake)
        controller.run_once({"type": "manualTest"})

        self.assertEqual(len(fake.calls), 7)
        self.assertIsNone(controller.termination_reason)
        self.assertTrue(controller.terminal_decision_finished)
        self.assertEqual([request_budget(item)["requestsRemaining"] for item in fake.calls], [8, 7, 6, 5, 4, 3, 2])
        self.assertNotIn("inspect_map", {tool["name"] for tool in fake.calls[-1]["tools"]})
        handoff = controller.state_store.get_decision_handoff()
        self.assertEqual(
            [item["objective"] for item in handoff["openLoops"]],
            ["Complete starter shelter", "Establish food production", "Build a research bench"],
        )
        self.assertNotIn("operations", fake.calls[0]["input"][0]["content"][0]["text"])

    def test_default_limit_remains_eight(self):
        controller = self.make_controller(FakeResponses([]))
        self.assertEqual(controller.max_model_requests_per_cycle, 8)
        self.assertEqual(DEFAULT_TOOL_REGISTRY.group_names, controller.tool_registry.group_names)


if __name__ == "__main__":
    unittest.main()
