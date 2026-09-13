import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, function_output
from context_telemetry import (
    DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST,
    DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE,
    Pricing,
)
from continuation_headroom import (
    DEFAULT_FINALIZATION_HEADROOM_TOKENS,
    fit_read_results,
    minimize_for_finalization,
)
from prompt_runtime import DEFAULT_MAX_COMPACTIONS_PER_CYCLE
from state_store import StateStore
from test_autonomous_context_headroom_m11_2_2 import Bridge, FakeResponses, dense_map, tool_call
from test_state_diff import base_state


class ActionHeadroomM1124Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake = FakeResponses()
        self.state = base_state(version=910)
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
    def shelter_reads():
        calls = []
        outputs = []
        for index, (search, def_name) in enumerate(
            (("sleeping spot", "SleepingSpot"), ("wall", "Wall"), ("door", "Door"))
        ):
            call_id = f"shelter-{index}"
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
        post_state = {
            "role": "user",
            "content": [{"type": "input_text", "text": "authoritative compact state"}],
        }
        return calls, outputs, post_state, {"currentSummary": {}, "fullStateSent": False}

    def enable_action_surface(self):
        for group in ("construction", "production", "equipment"):
            self.assertTrue(self.controller._get_active_tools().enable(group)["success"])
        self.controller.compaction_count = 1
        self.controller.model_request_count = 5

    def test_reduction_is_monotonic_for_tiny_results(self):
        cases = [
            (
                tool_call("list_build_options", {"category": None, "search": "wall"}, "small-target"),
                function_output("small-target", {"success": True, "options": [{"defName": "Wall"}]}),
            ),
            (
                tool_call("get_colony_state", {"section": "buildings"}, "small-state"),
                function_output("small-state", {"success": True, "count": 0}),
            ),
        ]
        for call, output in cases:
            with self.subTest(call_id=call.call_id):
                original_size = len(output["output"])
                fitted, reductions, _ = fit_read_results(
                    [output], [call], lambda _: 29_000, 28_000
                )
                self.assertEqual(fitted, [output])
                self.assertEqual(reductions, [])
                self.assertEqual(len(fitted[0]["output"]), original_size)
                self.assertEqual(minimize_for_finalization([output], [call]), [output])

    def test_small_targeted_research_and_equipment_reads_are_preserved(self):
        calls = [
            tool_call("get_colony_state", {"section": "research"}, "research"),
            tool_call("get_colony_state", {"section": "equipment"}, "equipment"),
            tool_call("get_colony_state", {"section": "apparel"}, "apparel"),
        ]
        outputs = [
            function_output("research", {"success": True, "available": [{"defName": "MicroelectronicsBasics"}]}),
            function_output("equipment", {"success": True, "items": [{"id": "Thing_Gun1", "defName": "Gun_Revolver"}]}),
            function_output("apparel", {"success": True, "items": [{"id": "Thing_Apparel1", "defName": "Apparel_Parka"}]}),
        ]

        fitted, reductions, _ = fit_read_results(
            outputs, calls, lambda _: 29_000, 28_000
        )

        self.assertEqual(fitted, outputs)
        self.assertEqual(reductions, [])

    def test_large_spatial_and_broad_catalog_results_reduce_before_targeted_reference(self):
        calls = [
            tool_call("inspect_map", {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96}, "map"),
            tool_call("list_build_options", {"category": "production", "search": None}, "broad"),
            tool_call("get_build_info", {"def_name": "Wall"}, "target"),
        ]
        outputs = [
            function_output("map", dense_map()),
            function_output("broad", {"success": True, "options": [{"defName": f"B{i}", "text": "x" * 100} for i in range(30)]}),
            function_output("target", {"success": True, "defName": "Wall", "size": [1, 1]}),
        ]

        fitted, reductions, _ = fit_read_results(
            outputs,
            calls,
            lambda values: 27_000 if "terrainRows" not in values[0]["output"] else 31_000,
            28_000,
            preserve_action_critical=True,
        )

        self.assertEqual([item.tool for item in reductions], ["inspect_map"])
        self.assertEqual(fitted[2], outputs[2])
        map_result = json.loads(fitted[0]["output"])
        self.assertFalse(map_result["geometryIncluded"])
        self.assertFalse(map_result["partialGeometry"])

    def test_shelter_catalog_reads_keep_exact_defs_for_one_action_round_then_finalize(self):
        self.enable_action_surface()
        self.controller.carried_context_chars = 55_000
        calls, outputs, post_state, context = self.shelter_reads()

        fitted = self.controller._fit_continuation_headroom(outputs, calls, post_state, context)

        self.assertEqual(fitted, outputs)
        self.assertTrue(self.controller._context_action_only)
        action_tools, mode = self.controller._request_tool_surface()
        action_names = {tool["name"] for tool in action_tools}
        self.assertEqual(mode, "context-action-only")
        self.assertIn("place_blueprints", action_names)
        self.assertIn("finish_decision", action_names)
        self.assertNotIn("list_build_options", action_names)
        self.assertNotIn("inspect_map", action_names)

        self.controller.tool_result_chars_this_round = sum(len(item["output"]) for item in fitted)
        self.controller.accumulated_tool_result_chars = self.controller.tool_result_chars_this_round
        self.controller._request_model(
            fitted + [post_state],
            state=self.state,
            previous_response_id="prior",
            context_payload=context,
        )

        request = self.fake.calls[0]
        self.assertLessEqual(self.controller.model_request_count, DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE)
        self.assertIn("place_blueprints", {tool["name"] for tool in request["tools"]})
        self.assertFalse(self.controller._context_action_only)
        self.assertTrue(self.controller._context_finalization_only)
        final_tools, final_mode = self.controller._request_tool_surface()
        self.assertEqual(final_mode, "context-finalization-only")
        self.assertEqual([tool["name"] for tool in final_tools], ["finish_decision"])
        final_estimate = self.controller._measure_model_request(
            self.controller._with_decision_budget(
                [
                    function_output("write", {"success": True, "message": "Blueprints placed"}),
                    post_state,
                ]
            ),
            final_tools,
            self.state,
            context,
            carried_context_chars=self.controller.carried_context_chars,
        ).estimated_input_tokens
        self.assertLessEqual(final_estimate, 28_000)

    def test_unachievable_28k_reserve_is_explicit_but_hard_limit_remains_available(self):
        self.enable_action_surface()
        self.controller.carried_context_chars = 65_000
        self.controller._action_burst_consumed = True
        calls, outputs, post_state, context = self.shelter_reads()

        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            fitted = self.controller._fit_continuation_headroom(outputs, calls, post_state, context)

        self.assertTrue(self.controller._context_finalization_only)
        self.assertIn("headroomReserveUnachievable=true", log.getvalue())
        self.assertEqual(fitted, outputs)
        final_tools, _ = self.controller._request_tool_surface()
        estimated = self.controller._measure_model_request(
            self.controller._with_decision_budget(fitted + [post_state]),
            final_tools,
            self.state,
            context,
            carried_context_chars=self.controller.carried_context_chars,
        ).estimated_input_tokens
        self.assertGreater(estimated, 28_000)
        self.assertLessEqual(estimated, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)

    def test_finalization_withheld_detail_is_a_controller_limit_not_gameplay_blocker(self):
        call = tool_call("get_build_info", {"def_name": "Wall"}, "build")
        output = function_output(
            "build",
            {"success": True, "defName": "Wall", "description": "x" * 5_000},
        )

        minimized = minimize_for_finalization([output], [call])
        result = json.loads(minimized[0]["output"])

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "controllerContextLimit")
        self.assertTrue(result["controllerLimited"])
        self.assertFalse(result["authoritativeGameplayBlocker"])

        map_call = tool_call(
            "inspect_map",
            {"min_x": 80, "min_z": 80, "max_x": 98, "max_z": 96},
            "map",
        )
        map_result = json.loads(
            minimize_for_finalization([function_output("map", dense_map())], [map_call])[0]["output"]
        )
        self.assertFalse(map_result["geometryIncluded"])
        self.assertFalse(map_result["partialGeometry"])
        self.assertNotIn("terrainRows", map_result)

        failed_map = function_output("map", {"success": False, "reason": "bridgeUnavailable"})
        self.assertEqual(minimize_for_finalization([failed_map], [map_call]), [failed_map])

    def test_limits_component_lifecycle_and_command_safety_are_unchanged(self):
        self.assertEqual(DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, 30_000)
        self.assertEqual(DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE, 8)
        self.assertEqual(DEFAULT_MAX_COMPACTIONS_PER_CYCLE, 1)
        self.assertEqual(DEFAULT_FINALIZATION_HEADROOM_TOKENS, 2_000)

        self.enable_action_surface()
        self.controller._context_action_only = True
        schemas, _ = self.controller._request_tool_surface()
        for schema in schemas:
            name = schema["name"]
            registration = self.controller.tool_registry.registration(name)
            self.assertTrue(name == "finish_decision" or not registration.read_only)

        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "Defs" / "RimGPTGameComponentDefs.xml").exists())
        component = (root / "Source" / "RimGPTGameComponent.cs").read_text(encoding="utf-8")
        self.assertIn("RimGPTGameComponent(Game game)", component)
        self.assertIn("override void GameComponentUpdate()", component)


if __name__ == "__main__":
    unittest.main()
