import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from context_telemetry import Pricing, measure_context, serialized_chars
from model_tool_result import ModelToolResultFormatter
from state_store import StateStore
from test_state_diff import base_state
from tool_registry import (
    CORE_GROUP,
    DEFAULT_TOOL_REGISTRY,
    ActiveToolSet,
    select_initial_tool_groups,
)


def call(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        type="function_call",
        name=name,
        call_id=call_id,
        arguments=json.dumps(arguments),
    )


def response(response_id, output, output_text=""):
    return SimpleNamespace(
        id=response_id,
        output=output,
        status="completed",
        output_text=output_text,
        usage=None,
    )


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class ReadBridge:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state or base_state(version=20))
        self.build_reads = 0
        self.submitted = []

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        return copy.deepcopy(self.state)

    def list_build_options(self, category=None, search=None):
        self.build_reads += 1
        return {"gameLoaded": True, "options": [{"defName": "Wall", "label": "wall"}]}

    def inspect_map(self, min_x, min_z, max_x, max_z):
        return {
            "gameLoaded": True,
            "bounds": {"minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z},
            "terrainPalette": [{"id": 0, "terrain": "Soil", "buildable": True}],
            "terrainRows": [{"z": min_z, "runs": [[min_x, max_x - min_x + 1, 0]]}],
            "things": [],
            "plantGroups": [],
            "zones": [],
            "truncated": False,
        }

    def check_build_placements(self, placements):
        return {
            "gameLoaded": True,
            "requested": len(placements),
            "validCount": len(placements),
            "placements": [
                {"index": index, "valid": True, "reason": None}
                for index, _ in enumerate(placements)
            ],
        }

    def submit_command(self, command):
        self.submitted.append(command)
        raise AssertionError("Inactive tool reached bridge mutation routing")


class ToolRegistryTests(unittest.TestCase):
    def test_registry_has_unique_tools_valid_groups_and_executors(self):
        registrations = DEFAULT_TOOL_REGISTRY.all_registrations()
        names = [item.name for item in registrations]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 55)
        self.assertTrue(all(item.group in DEFAULT_TOOL_REGISTRY.group_names for item in registrations))
        self.assertTrue(all(item.executor for item in registrations))

    def test_core_is_small_and_contains_discovery_and_state(self):
        active = ActiveToolSet(DEFAULT_TOOL_REGISTRY)
        names = [item["name"] for item in active.schemas()]
        self.assertEqual(active.groups, (CORE_GROUP,))
        self.assertIn("get_colony_state", names)
        self.assertIn("list_capabilities", names)
        self.assertIn("enable_capability", names)
        self.assertLess(serialized_chars(active.schemas()), 10_000)
        self.assertLess(serialized_chars(active.schemas()), DEFAULT_TOOL_REGISTRY.schema_chars_for_groups(DEFAULT_TOOL_REGISTRY.group_names))

    def test_capability_list_is_complete_compact_and_does_not_leak_schemas(self):
        entries = DEFAULT_TOOL_REGISTRY.capability_list((CORE_GROUP,))
        self.assertEqual([item["name"] for item in entries], list(DEFAULT_TOOL_REGISTRY.non_core_group_names))
        self.assertLess(serialized_chars(entries), 2_000)
        self.assertNotIn("parameters", json.dumps(entries))
        self.assertNotIn("properties", json.dumps(entries))

    def test_groups_expose_expected_catalogs_and_actions(self):
        expected = {
            "construction": {"list_build_options", "get_build_info", "check_build_placements", "place_blueprints"},
            "zones": {"list_growable_plants", "check_zone_placement", "create_growing_zone"},
            "production": {"list_recipes", "add_bill"},
            "equipment": {"equip_weapon", "wear_apparel", "assign_bed"},
            "power": {"set_power_switch", "set_target_fuel_level"},
            "work": {"set_work_priority", "prioritize_haul"},
        }
        for group, names in expected.items():
            with self.subTest(group=group):
                active_names = {item["name"] for item in DEFAULT_TOOL_REGISTRY.schemas_for_groups((CORE_GROUP, group))}
                self.assertTrue(names.issubset(active_names))

    def test_activation_persists_supports_multiple_groups_and_resets(self):
        active = ActiveToolSet(DEFAULT_TOOL_REGISTRY, max_dynamic_groups=3)
        first = active.enable("construction")
        self.assertTrue(first["success"])
        self.assertGreater(first["toolsAdded"], 0)
        self.assertTrue(active.is_active("place_blueprints"))
        self.assertEqual(active.enable("construction")["toolsAdded"], 0)
        self.assertTrue(active.enable("zones")["success"])
        self.assertEqual(active.groups, ("core", "construction", "zones"))
        active.reset()
        self.assertEqual(active.groups, ("core",))
        self.assertFalse(active.is_active("place_blueprints"))

    def test_activation_limit_unknown_and_enable_all_are_rejected(self):
        active = ActiveToolSet(DEFAULT_TOOL_REGISTRY, max_dynamic_groups=2)
        self.assertTrue(active.enable("construction")["success"])
        self.assertTrue(active.enable("zones")["success"])
        self.assertEqual(active.enable("production")["reason"], "activeCapabilityLimitReached")
        self.assertEqual(active.enable("all")["reason"], "capabilityMustBeExplicitNonCoreGroup")
        unknown = active.enable("not-a-group")
        self.assertEqual(unknown["reason"], "unknownCapability")
        self.assertIn("construction", unknown["availableCapabilities"])

    def test_initial_selection_is_deterministic_and_only_preloads_combat_for_threat(self):
        stable = {"currentSummary": {"threats": {"status": "none", "active": 0}}}
        threatened = {"currentSummary": {"threats": {"status": "active", "active": 1}}}
        self.assertEqual(select_initial_tool_groups(stable), ())
        self.assertEqual(select_initial_tool_groups(stable), select_initial_tool_groups(copy.deepcopy(stable)))
        self.assertEqual(select_initial_tool_groups(threatened), ("combat",))

        controller = object.__new__(AgentController)
        controller._configure_initial_tools(threatened)
        self.assertTrue(controller._get_active_tools().is_active("draft"))
        controller._begin_cycle()
        self.assertFalse(controller._get_active_tools().is_active("draft"))

    def test_identical_active_groups_serialize_tools_in_deterministic_order(self):
        first = DEFAULT_TOOL_REGISTRY.schemas_for_groups(("core", "construction", "zones"))
        second = DEFAULT_TOOL_REGISTRY.schemas_for_groups(("zones", "core", "construction"))
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual([tool["name"] for tool in first], [tool["name"] for tool in second])

    def test_inactive_tool_is_rejected_before_bridge_execution(self):
        controller = object.__new__(AgentController)
        controller.bridge = ReadBridge()
        controller.dry_run = False
        controller.uncertain_commands = {}
        output = controller._execute_tool_call_batch([
            call("place_blueprints", {"placements": []})
        ])[0]
        result = json.loads(output["output"])
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "toolCapabilityNotEnabled")
        self.assertEqual(result["requiredCapability"], "construction")
        self.assertEqual(controller.bridge.submitted, [])

    def test_active_read_tool_routes_normally(self):
        controller = object.__new__(AgentController)
        controller.bridge = ReadBridge()
        controller.dry_run = False
        controller.uncertain_commands = {}
        controller._get_active_tools().enable("construction")
        output = controller._execute_tool_call_batch([
            call("list_build_options", {"category": None, "search": "wall"})
        ])[0]
        result = json.loads(output["output"])
        self.assertTrue(result["success"])
        self.assertEqual(controller.bridge.build_reads, 1)

    def test_controller_capability_tools_list_enable_and_enforce_limit(self):
        controller = object.__new__(AgentController)
        controller.bridge = ReadBridge()
        controller.dry_run = False
        controller.uncertain_commands = {}
        listed = controller._execute_tool_call_batch([call("list_capabilities", {})])[0]
        listed_result = json.loads(listed["output"])
        self.assertTrue(listed_result["success"])
        self.assertEqual(len(listed_result["capabilities"]), len(DEFAULT_TOOL_REGISTRY.non_core_group_names))
        enabled = controller._execute_tool_call_batch([call("enable_capability", {"name": "construction"})])[0]
        enabled_result = json.loads(enabled["output"])
        self.assertTrue(enabled_result["success"])
        self.assertTrue(controller._get_active_tools().is_active("place_blueprints"))

    def test_activation_only_applies_to_the_next_model_round(self):
        controller = object.__new__(AgentController)
        controller.bridge = ReadBridge()
        controller.dry_run = False
        controller.uncertain_commands = {}
        outputs = controller._execute_tool_call_batch([
            call("enable_capability", {"name": "construction"}, "enable"),
            call("place_blueprints", {"placements": []}, "hidden-write"),
        ])
        self.assertTrue(json.loads(outputs[0]["output"])["success"])
        rejected = json.loads(outputs[1]["output"])
        self.assertEqual(rejected["reason"], "toolCapabilityNotEnabled")
        self.assertEqual(controller.bridge.submitted, [])

    def test_responses_continuation_receives_new_and_persistent_schemas(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = base_state(version=20)
        store = StateStore(Path(temporary.name), logger=lambda _: None)
        fake = FakeResponses([
            response("r1", [call("enable_capability", {"name": "construction"}, "enable")]),
            response("r2", [call(
                "inspect_map",
                {"min_x": 100, "min_z": 100, "max_x": 104, "max_z": 104},
                "inspect",
            )]),
            response("r3", [call(
                "check_build_placements",
                {
                    "placements": [{
                        "build_def": "Wall",
                        "x": 101,
                        "z": 101,
                        "rotation": "North",
                        "stuff_def": "WoodLog",
                    }]
                },
                "validate",
            )]),
            response("r4", [], "Done."),
        ])
        controller = AgentController(
            bridge=ReadBridge(state),
            model="test-model",
            client=SimpleNamespace(responses=fake),
            state_store=store,
            pricing=Pricing(),
        )

        controller.run_once({"type": "manualTest"})

        request_names = [{item["name"] for item in request["tools"]} for request in fake.calls]
        initial_text = fake.calls[0]["input"][0]["content"][0]["text"]
        self.assertNotIn("list_build_options", request_names[0])
        self.assertIn("list_build_options", request_names[1])
        self.assertIn("list_build_options", request_names[2])
        self.assertIn("list_build_options", request_names[3])
        self.assertEqual(fake.calls[1]["previous_response_id"], "r1")
        self.assertEqual(fake.calls[2]["previous_response_id"], "r2")
        self.assertEqual(fake.calls[3]["previous_response_id"], "r3")
        self.assertEqual(fake.calls[1]["input"][0]["call_id"], "enable")
        self.assertEqual(fake.calls[2]["input"][0]["call_id"], "inspect")
        self.assertEqual(fake.calls[3]["input"][0]["call_id"], "validate")
        self.assertNotIn(initial_text, json.dumps(fake.calls[1]["input"], default=str))
        self.assertNotIn(initial_text, json.dumps(fake.calls[2]["input"], default=str))
        self.assertNotIn(initial_text, json.dumps(fake.calls[3]["input"], default=str))
        self.assertNotIn('"operations"', json.dumps(fake.calls[1]["input"], default=str))
        self.assertEqual(store.get_decision_baseline()["snapshot"]["version"], 20)

    def test_representative_grouped_cycles_remain_under_context_limit(self):
        formatter = ModelToolResultFormatter(logger=lambda _: None)
        compact_results = {
            "enable": formatter.format(
                "enable_capability",
                {"success": True, "result": {"success": True, "enabled": "construction", "toolsAdded": 8}},
                {"name": "construction"},
            ),
            "inspect": {
                "success": True,
                "bounds": {"minX": 100, "minZ": 100, "maxX": 129, "maxZ": 129},
                "terrainPalette": [{"id": 0, "terrain": "Soil", "buildable": True}],
                "terrainRows": [{"z": z, "runs": [[100, 30, 0]]} for z in range(100, 130)],
                "things": [],
                "plantGroups": [],
                "zones": [],
                "truncated": False,
            },
            "validate": {"success": True, "valid": True, "requested": 25, "validCount": 25, "invalid": []},
            "place": {"success": True, "requested": 25, "placed": 25, "failed": []},
            "recipes": {"success": True, "worktableId": "Bench1", "recipes": [{"recipeDef": "MakeMealSimple", "available": True}]},
            "bill": {"success": True, "billId": "Bill1", "recipe": "MakeMealSimple", "targetCount": 20},
        }

        cycles = (
            (("core",), ("core", "construction"), ("core", "construction"), ("core", "construction"), ("core", "construction")),
            (("core",), ("core", "production"), ("core", "production"), ("core", "production")),
            (("core",), ("core", "construction"), ("core", "construction", "zones")),
        )
        results = (
            (None, compact_results["enable"], compact_results["inspect"], compact_results["validate"], compact_results["place"]),
            (None, compact_results["enable"], compact_results["recipes"], compact_results["bill"]),
            (None, compact_results["enable"], compact_results["enable"]),
        )
        for groups_by_round, results_by_round in zip(cycles, results):
            with self.subTest(groups=groups_by_round[-1]):
                carried = 0
                accumulated = 0
                estimates = []
                for groups, result in zip(groups_by_round, results_by_round):
                    if result is None:
                        input_items = [{"role": "user", "content": [{"type": "input_text", "text": "x" * 1_500}]}]
                        this_round = 0
                    else:
                        output = json.dumps(result, separators=(",", ":"))
                        input_items = [{"type": "function_call_output", "call_id": "call", "output": output}]
                        this_round = len(output)
                        accumulated += this_round
                    breakdown = measure_context(
                        instructions=SYSTEM_INSTRUCTIONS,
                        tools=DEFAULT_TOOL_REGISTRY.schemas_for_groups(groups),
                        input_items=input_items,
                        state=base_state(version=20),
                        accumulated_tool_result_chars=accumulated,
                        carried_context_chars=carried,
                        tool_result_chars_this_round=this_round,
                    )
                    estimates.append(breakdown.estimated_input_tokens)
                    carried += serialized_chars(input_items) + 300
                self.assertLess(max(estimates), 30_000)


if __name__ == "__main__":
    unittest.main()
