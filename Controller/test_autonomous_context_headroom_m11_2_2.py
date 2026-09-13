import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS, function_output
from context_telemetry import (
    DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST,
    DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE,
    Pricing,
    measure_context,
)
from continuation_headroom import DEFAULT_FINALIZATION_HEADROOM_TOKENS, fit_read_results
from prompt_runtime import DEFAULT_MAX_COMPACTIONS_PER_CYCLE
from state_store import StateStore
from test_state_diff import base_state


def tool_call(name, arguments, call_id):
    return SimpleNamespace(name=name, arguments=json.dumps(arguments), call_id=call_id, type="function_call")


def dense_map(width=19, height=17):
    palette = [
        {
            "id": index,
            "terrain": f"Terrain_{index}",
            "walkable": True,
            "buildable": True,
            "roofed": index % 2 == 0,
            "room": "outdoor",
            "affordances": ["Light", "Medium"],
        }
        for index in range(24)
    ]
    rows = []
    for z in range(height):
        rows.append({"z": 80 + z, "runs": [[80 + x, 1, (x + z) % len(palette)] for x in range(width)]})
    return {
        "success": True,
        "mapId": "Map_0",
        "bounds": {"minX": 80, "minZ": 80, "maxX": 98, "maxZ": 96},
        "terrainPalette": palette,
        "terrainRows": rows,
        "outdoorRoom": {"temperature": 24.0, "usesOutdoorTemperature": True},
        "things": [],
        "plantGroups": [],
        "zones": [],
        "truncated": False,
    }


class FakeResponses:
    def __init__(self):
        self.calls = []
        self.compact_calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="next", output=[], status="completed", output_text="", usage=None)

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        raise AssertionError("The already-consumed compaction must not run again")


class Bridge:
    def __init__(self, state):
        self.state = copy.deepcopy(state)

    def get_state(self):
        return copy.deepcopy(self.state)


class AutonomousContextHeadroomTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake = FakeResponses()
        self.state = base_state(version=900)
        self.controller = AgentController(
            bridge=Bridge(self.state),
            model="test-model",
            client=SimpleNamespace(responses=self.fake),
            state_store=StateStore(Path(temporary.name), logger=lambda _: None),
            pricing=Pricing(),
        )
        self.controller._begin_cycle()
        self.controller.current_state = copy.deepcopy(self.state)

    @staticmethod
    def paid_shape():
        calls = [
            tool_call("list_build_options", {"category": "production"}, "builds-1"),
            tool_call("get_build_info", {"def_name": "SimpleResearchBench"}, "builds-2"),
            tool_call("list_recipes", {"worktable_id": "Bench_1"}, "recipes"),
            tool_call("inspect_map", {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96}, "map"),
        ]
        catalog = {
            "success": True,
            "options": [
                {"defName": f"Building_{index}", "label": "building " + ("x" * 80), "requiredTerrainAffordance": "Medium"}
                for index in range(35)
            ],
        }
        outputs = [
            function_output("builds-1", catalog),
            function_output("builds-2", {"success": True, "defName": "SimpleResearchBench", "description": "x" * 4_000}),
            function_output("recipes", {"success": True, "recipes": catalog["options"]}),
            function_output("map", dense_map()),
        ]
        post_state = {"role": "user", "content": [{"type": "input_text", "text": "authoritative compact state"}]}
        context = {"currentSummary": {}, "fullStateSent": False}
        return calls, outputs, post_state, context

    def test_complete_spatial_result_is_unchanged_when_it_fits(self):
        call = tool_call("inspect_map", {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96}, "map")
        original = function_output("map", dense_map())
        fitted, reductions, estimated = fit_read_results([original], [call], lambda _: 12_000, 28_000)
        self.assertEqual(fitted, [original])
        self.assertEqual(reductions, [])
        self.assertEqual(estimated, 12_000)

    def test_spatial_overflow_contains_no_fabricated_or_partial_geometry(self):
        call = tool_call("inspect_map", {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96}, "map")
        output = function_output("map", dense_map())
        fitted, reductions, estimated = fit_read_results(
            [output], [call], lambda values: 29_000 if "terrainRows" in values[0]["output"] else 20_000, 28_000
        )
        payload = json.loads(fitted[0]["output"])
        self.assertEqual(estimated, 20_000)
        self.assertEqual(len(reductions), 1)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["reason"], "contextHeadroomExceeded")
        self.assertEqual(payload["requestedCellCount"], 19 * 17)
        self.assertFalse(payload["geometryIncluded"])
        self.assertFalse(payload["partialGeometry"])
        self.assertNotIn("terrainRows", payload)
        self.assertNotIn("terrainPalette", payload)

    def test_failures_and_validation_results_are_not_reduced(self):
        failed = function_output("map", {"success": False, "reason": "bridge failure"})
        validation = function_output("check", {"success": True, "valid": False, "invalid": [{"index": 0, "reason": "blocked"}]})
        calls = [tool_call("inspect_map", {}, "map"), tool_call("check_build_placements", {}, "check")]
        fitted, reductions, _ = fit_read_results([failed, validation], calls, lambda _: 35_000, 28_000)
        self.assertEqual(fitted, [failed, validation])
        self.assertEqual(reductions, [])

    def test_reduced_catalog_retains_stable_action_references(self):
        call = tool_call("list_build_options", {"category": "production"}, "catalog")
        output = function_output(
            "catalog",
            {
                "success": True,
                "options": [
                    {
                        "defName": "SimpleResearchBench",
                        "label": "simple research bench",
                        "requiredTerrainAffordance": "Medium",
                        "description": "x" * 5_000,
                    }
                ],
            },
        )
        fitted, reductions, _ = fit_read_results(
            [output], [call], lambda values: 29_000 if "description" in values[0]["output"] else 20_000, 28_000
        )

        payload = json.loads(fitted[0]["output"])
        self.assertEqual(len(reductions), 1)
        self.assertTrue(payload["resultReduced"])
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["references"][0]["defName"], "SimpleResearchBench")
        self.assertEqual(payload["references"][0]["requiredTerrainAffordance"], "Medium")

    def test_paid_shape_reserves_a_valid_finalization_request(self):
        for group in ("construction", "production", "equipment"):
            self.assertTrue(self.controller._get_active_tools().enable(group)["success"])
        self.controller.compaction_count = 1
        self.controller.model_request_count = 5
        # Represents prior catalog/read continuation history already carried by Responses.
        self.controller.carried_context_chars = 44_948
        calls, outputs, post_state, context = self.paid_shape()
        carried_before = self.controller.carried_context_chars
        unreduced_tools, _ = self.controller._request_tool_surface()
        unreduced = measure_context(
            instructions=SYSTEM_INSTRUCTIONS,
            tools=unreduced_tools,
            input_items=self.controller._with_decision_budget(outputs + [post_state]),
            state=self.state,
            accumulated_tool_result_chars=0,
            carried_context_chars=carried_before,
            context_payload=context,
            full_state_sent=False,
        )
        self.assertEqual(unreduced.estimated_input_tokens, 33_380)

        fitted = self.controller._fit_continuation_headroom(outputs, calls, post_state, context)
        map_payload = json.loads(fitted[-1]["output"])
        self.assertEqual(map_payload["reason"], "contextHeadroomExceeded")
        self.assertFalse(map_payload["partialGeometry"])
        self.assertFalse(self.controller._context_finalization_only)

        self.controller.tool_result_chars_this_round = sum(len(item["output"]) for item in fitted)
        self.controller.accumulated_tool_result_chars = self.controller.tool_result_chars_this_round
        self.controller._request_model(
            fitted + [post_state], state=self.state, previous_response_id="prior", context_payload=context
        )
        self.assertEqual(self.fake.compact_calls, [])
        request = self.fake.calls[0]
        self.assertIn("finish_decision", [schema["name"] for schema in request["tools"]])
        breakdown = measure_context(
            instructions=request["instructions"],
            tools=request["tools"],
            input_items=request["input"],
            state=self.state,
            accumulated_tool_result_chars=self.controller.accumulated_tool_result_chars,
            carried_context_chars=carried_before,
            context_payload=context,
            full_state_sent=False,
            tool_result_chars_this_round=self.controller.tool_result_chars_this_round,
        )
        self.assertLessEqual(breakdown.estimated_input_tokens, 28_000)
        self.assertFalse(breakdown.full_state_sent)

    def test_near_exhaustion_switches_to_terminal_surface(self):
        for group in ("construction", "production", "equipment"):
            self.controller._get_active_tools().enable(group)
        self.controller.compaction_count = 1
        self.controller.model_request_count = 5
        self.controller.carried_context_chars = 69_600
        calls, outputs, post_state, context = self.paid_shape()

        fitted = self.controller._fit_continuation_headroom(outputs, calls, post_state, context)

        self.assertTrue(self.controller._context_finalization_only)
        names = [schema["name"] for schema in self.controller._request_tool_surface()[0]]
        self.assertEqual(names, ["finish_decision"])
        map_payload = json.loads(fitted[-1]["output"])
        self.assertFalse(map_payload["geometryIncluded"])
        self.assertFalse(map_payload["partialGeometry"])

    def test_limits_and_single_compaction_are_unchanged(self):
        self.assertEqual(DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, 30_000)
        self.assertEqual(DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE, 8)
        self.assertEqual(DEFAULT_MAX_COMPACTIONS_PER_CYCLE, 1)
        self.assertEqual(DEFAULT_FINALIZATION_HEADROOM_TOKENS, 2_000)


if __name__ == "__main__":
    unittest.main()
