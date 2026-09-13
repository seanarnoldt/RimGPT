import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, function_output
from autonomous_scheduler import (
    DEFAULT_MAX_AUTONOMOUS_DECISIONS,
    DEFAULT_MAX_SESSION_MODEL_REQUESTS,
    DEFAULT_MAX_SESSION_SPEND,
)
from context_telemetry import (
    DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST,
    DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE,
    ModelContextLimitError,
    Pricing,
)
from continuation_headroom import (
    DEFAULT_ACTION_BURST_HARD_LIMIT_TOKENS,
    DEFAULT_ACTION_BURST_TARGET_TOKENS,
    DEFAULT_FINALIZATION_HEADROOM_TOKENS,
    fit_read_results,
    has_exact_action_critical_result,
)
from prompt_runtime import DEFAULT_MAX_COMPACTIONS_PER_CYCLE, RIMGPT_PROMPT_VERSION
from state_store import StateStore
from test_autonomous_context_headroom_m11_2_2 import Bridge, FakeResponses, dense_map, tool_call
from test_state_diff import base_state


class ActionBurstContextBudgetM1125Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake = FakeResponses()
        self.state = base_state(version=930)
        self.controller = AgentController(
            bridge=Bridge(self.state),
            model="test-model",
            client=SimpleNamespace(responses=self.fake),
            state_store=StateStore(Path(temporary.name), logger=lambda _: None),
            pricing=Pricing(),
        )
        self.controller._begin_cycle()
        self.controller.current_state = copy.deepcopy(self.state)

    def enable_large_action_surface(self):
        for group in ("construction", "zones", "equipment"):
            self.assertTrue(self.controller._get_active_tools().enable(group)["success"])

    @staticmethod
    def progression_shape():
        calls = []
        outputs = []
        for index, (search, def_name) in enumerate(
            (
                ("wall", "Wall"),
                ("door", "Door"),
                ("research bench", "SimpleResearchBench"),
                ("campfire", "Campfire"),
            )
        ):
            call_id = f"target-{index}"
            calls.append(
                tool_call(
                    "list_build_options",
                    {"category": None, "search": search},
                    call_id,
                )
            )
            outputs.append(
                function_output(
                    call_id,
                    {
                        "success": True,
                        "count": 1,
                        "options": [
                            {
                                "defName": def_name,
                                "label": search,
                                "requiredTerrainAffordance": "Light",
                            }
                        ],
                    },
                )
            )

        broad_catalog = {
            "success": True,
            "options": [
                {"defName": f"Building_{index}", "label": "x" * 80}
                for index in range(60)
            ],
        }
        broad_buildings = {
            "success": True,
            "section": "buildings",
            "buildings": [
                {
                    "id": f"Building_{index}",
                    "defName": "GenericBuilding",
                    "description": "x" * 100,
                }
                for index in range(40)
            ],
        }
        calls.extend(
            [
                tool_call(
                    "inspect_map",
                    {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96},
                    "map",
                ),
                tool_call(
                    "list_build_options",
                    {"category": None, "search": None},
                    "broad-catalog",
                ),
                tool_call("get_colony_state", {"section": "buildings"}, "broad-state"),
            ]
        )
        outputs.extend(
            [
                function_output("map", dense_map()),
                function_output("broad-catalog", broad_catalog),
                function_output("broad-state", broad_buildings),
            ]
        )
        post_state = {
            "role": "user",
            "content": [{"type": "input_text", "text": "authoritative compact state"}],
        }
        context = {"currentSummary": {}, "fullStateSent": False}
        return calls, outputs, post_state, context

    def estimate_selected_request(self, outputs, post_state, context):
        tools, _ = self.controller._request_tool_surface()
        return self.controller._measure_model_request(
            self.controller._with_decision_budget(outputs + [post_state]),
            tools,
            self.state,
            context,
            carried_context_chars=self.controller.carried_context_chars,
        ).estimated_input_tokens

    def sized_input(self, target_tokens):
        low = 0
        high = 120_000
        while low < high:
            middle = (low + high) // 2
            candidate = [
                {"role": "user", "content": [{"type": "input_text", "text": "x" * middle}]}
            ]
            tools, _ = self.controller._request_tool_surface()
            estimated = self.controller._measure_model_request(
                self.controller._with_decision_budget(candidate),
                tools,
                self.state,
                None,
                carried_context_chars=self.controller.carried_context_chars,
            ).estimated_input_tokens
            if estimated < target_tokens:
                low = middle + 1
            else:
                high = middle
        payload = [{"role": "user", "content": [{"type": "input_text", "text": "x" * low}]}]
        return payload

    def test_paid_progression_shape_uses_one_burst_then_normal_finalization(self):
        self.enable_large_action_surface()
        self.controller.compaction_count = 1
        self.controller.model_request_count = 5
        self.controller.carried_context_chars = 60_000
        calls, outputs, post_state, context = self.progression_shape()

        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            fitted = self.controller._fit_continuation_headroom(
                outputs, calls, post_state, context
            )

        for index in range(4):
            self.assertEqual(fitted[index], outputs[index])
        map_result = json.loads(fitted[4]["output"])
        self.assertNotIn("terrainRows", map_result)
        self.assertFalse(map_result["geometryIncluded"])
        self.assertFalse(map_result["partialGeometry"])
        self.assertTrue(json.loads(fitted[5]["output"])["resultReduced"])
        self.assertTrue(json.loads(fitted[6]["output"])["resultReduced"])
        reduction_lines = [line for line in log.getvalue().splitlines() if "tool=" in line]
        self.assertEqual(
            [line.split("tool=", 1)[1].split(" ", 1)[0] for line in reduction_lines],
            ["inspect_map", "list_build_options", "get_colony_state"],
        )

        self.assertTrue(self.controller._context_action_only)
        self.assertTrue(self.controller._action_burst_pending)
        selected_estimate = self.estimate_selected_request(fitted, post_state, context)
        self.assertGreater(selected_estimate, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertLess(selected_estimate, DEFAULT_ACTION_BURST_TARGET_TOKENS)

        normal_flag = self.controller._context_action_only
        self.controller._context_action_only = False
        normal_estimate = self.estimate_selected_request(fitted, post_state, context)
        self.controller._context_action_only = normal_flag
        self.assertGreater(normal_estimate, 28_000)
        self.assertLess(normal_estimate, DEFAULT_ACTION_BURST_HARD_LIMIT_TOKENS)

        self.controller.tool_result_chars_this_round = sum(len(item["output"]) for item in fitted)
        self.controller.accumulated_tool_result_chars = self.controller.tool_result_chars_this_round
        with contextlib.redirect_stdout(log):
            self.controller._request_model(
                fitted + [post_state],
                state=self.state,
                previous_response_id="prior",
                context_payload=context,
            )

        request = self.fake.calls[0]
        names = {schema["name"] for schema in request["tools"]}
        self.assertIn("place_blueprints", names)
        self.assertIn("finish_decision", names)
        self.assertNotIn("inspect_map", names)
        self.assertNotIn("list_build_options", names)
        self.assertTrue(self.controller._action_burst_consumed)
        self.assertFalse(self.controller._action_burst_pending)
        self.assertTrue(self.controller._context_finalization_only)
        self.assertIn("actionBurst=true", log.getvalue())
        self.assertIn("targetTokens=34000", log.getvalue())
        self.assertIn("hardLimitTokens=36000", log.getvalue())
        self.assertIn("actionBurstConsumed=true", log.getvalue())

        final_tools, final_mode = self.controller._request_tool_surface()
        self.assertEqual(final_mode, "context-finalization-only")
        self.assertEqual([tool["name"] for tool in final_tools], ["finish_decision"])
        final_input = [
            function_output("write", {"success": True, "message": "Blueprints placed"}),
            post_state,
        ]
        final_estimate = self.controller._measure_model_request(
            self.controller._with_decision_budget(final_input),
            final_tools,
            self.state,
            context,
            carried_context_chars=self.controller.carried_context_chars,
        ).estimated_input_tokens
        self.assertLessEqual(final_estimate, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.controller._request_model(final_input, state=self.state, context_payload=context)
        self.assertEqual(len(self.fake.calls), 2)

    def test_eligible_33_to_35k_burst_is_allowed_and_cannot_repeat(self):
        self.enable_large_action_surface()
        self.controller._context_action_only = True
        self.controller._action_burst_pending = True
        payload = self.sized_input(34_500)

        self.controller._request_model(payload, state=self.state)

        self.assertEqual(len(self.fake.calls), 1)
        self.assertTrue(self.controller._action_burst_consumed)
        self.controller.carried_context_chars = 0
        self.controller.accumulated_tool_result_chars = 0
        self.controller.tool_result_chars_this_round = 0
        self.controller._context_finalization_only = False
        self.controller._context_action_only = True
        self.controller._action_burst_pending = True
        second = self.sized_input(31_000)
        with self.assertRaises(ModelContextLimitError) as raised:
            self.controller._request_model(second, state=self.state)
        self.assertEqual(raised.exception.limit, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertEqual(len(self.fake.calls), 1)

    def test_noncritical_31k_and_burst_over_36k_are_rejected(self):
        normal = self.sized_input(31_000)
        with self.assertRaises(ModelContextLimitError) as normal_error:
            self.controller._request_model(normal, state=self.state)
        self.assertEqual(normal_error.exception.limit, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertEqual(self.fake.calls, [])

        self.controller._context_action_only = True
        self.controller._action_burst_pending = True
        oversized_burst = self.sized_input(36_500)
        with self.assertRaises(ModelContextLimitError) as burst_error:
            self.controller._request_model(oversized_burst, state=self.state)
        self.assertEqual(burst_error.exception.limit, DEFAULT_ACTION_BURST_HARD_LIMIT_TOKENS)
        self.assertEqual(self.fake.calls, [])

        self.controller._action_burst_pending = False
        self.controller._context_action_only = False
        self.controller._context_finalization_only = True
        oversized_finalization = self.sized_input(30_500)
        with self.assertRaises(ModelContextLimitError) as final_error:
            self.controller._request_model(oversized_finalization, state=self.state)
        self.assertEqual(final_error.exception.limit, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertEqual(self.fake.calls, [])

    def test_normal_sized_action_continuation_does_not_consume_burst(self):
        self.enable_large_action_surface()
        self.controller.compaction_count = 1
        self.controller.model_request_count = 5
        self.controller.carried_context_chars = 50_000
        calls, outputs, post_state, context = self.progression_shape()

        fitted = self.controller._fit_continuation_headroom(outputs, calls, post_state, context)

        self.assertTrue(self.controller._context_action_only)
        self.assertLessEqual(self.estimate_selected_request(fitted, post_state, context), 28_000)
        self.assertFalse(self.controller._action_burst_pending)
        self.assertFalse(self.controller._action_burst_consumed)

    def test_exact_evidence_excludes_broad_reads_and_includes_validators_and_narrow_maps(self):
        broad_calls = [
            tool_call("list_build_options", {"category": None, "search": None}, "catalog"),
            tool_call(
                "inspect_map",
                {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96},
                "map",
            ),
        ]
        broad_outputs = [
            function_output("catalog", {"success": True, "options": [{"defName": "Wall"}]}),
            function_output("map", dense_map()),
        ]
        self.assertFalse(has_exact_action_critical_result(broad_outputs, broad_calls))

        validator = tool_call("check_build_placements", {"placements": []}, "validator")
        validator_output = function_output(
            "validator",
            {"success": True, "valid": True, "requested": 4, "validCount": 4, "invalid": []},
        )
        self.assertTrue(has_exact_action_critical_result([validator_output], [validator]))

        narrow = dense_map(10, 10)
        narrow["bounds"] = {"minX": 80, "minZ": 80, "maxX": 89, "maxZ": 89}
        narrow_call = tool_call(
            "inspect_map",
            {"min_x": 80, "min_z": 80, "max_x": 89, "max_z": 89},
            "narrow",
        )
        self.assertTrue(
            has_exact_action_critical_result([function_output("narrow", narrow)], [narrow_call])
        )

    def test_reduction_and_safeguard_constants_are_unchanged_outside_burst(self):
        call = tool_call("get_colony_state", {"section": "buildings"}, "small")
        output = function_output("small", {"success": True, "count": 0})
        fitted, reductions, _ = fit_read_results(
            [output], [call], lambda _: 29_000, 28_000
        )
        self.assertEqual(fitted, [output])
        self.assertEqual(reductions, [])
        self.assertEqual(DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, 30_000)
        self.assertEqual(DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE, 8)
        self.assertEqual(DEFAULT_MAX_COMPACTIONS_PER_CYCLE, 1)
        self.assertEqual(DEFAULT_FINALIZATION_HEADROOM_TOKENS, 2_000)
        self.assertEqual(DEFAULT_ACTION_BURST_TARGET_TOKENS, 34_000)
        self.assertEqual(DEFAULT_ACTION_BURST_HARD_LIMIT_TOKENS, 36_000)
        self.assertEqual(DEFAULT_MAX_AUTONOMOUS_DECISIONS, 20)
        self.assertEqual(DEFAULT_MAX_SESSION_MODEL_REQUESTS, 80)
        self.assertEqual(DEFAULT_MAX_SESSION_SPEND, 5.0)
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m11.2.5")


if __name__ == "__main__":
    unittest.main()
