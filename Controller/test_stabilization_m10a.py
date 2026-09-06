import copy
import json
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace

from openai._utils import maybe_transform
from openai.types.responses.response_compact_params import ResponseCompactParams
from openai.types.responses.response_compaction_item import ResponseCompactionItem

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from bridge import RimWorldBridge
from context_telemetry import Pricing, extract_usage
from dry_run_proposals import DryRunProposalLedger
from model_tool_result import ModelToolResultFormatter
from prompt_runtime import compacted_output_as_input
from state_store import StateStore, snapshot_version
from test_state_diff import base_state
from tool_registry import DEFAULT_TOOL_REGISTRY, ActiveToolSet
from tools import TOOLS


def call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


def response(response_id, output, output_text=""):
    return SimpleNamespace(id=response_id, output=output, status="completed", output_text=output_text, usage=None)


class FakeResponses:
    def __init__(self, responses):
        self.pending = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.pending.pop(0)


class DryRunBridge:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.submitted = []
        self.map_requests = []

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        return copy.deepcopy(self.state)

    def inspect_map(self, min_x, min_z, max_x, max_z):
        self.map_requests.append((min_x, min_z, max_x, max_z))
        width = abs(max_x - min_x) + 1
        height = abs(max_z - min_z) + 1
        if width > 40 or height > 40:
            return {"error": "regionTooLarge", "maxWidth": 40, "maxHeight": 40}
        return {
            "schemaVersion": 2,
            "gameLoaded": True,
            "bounds": {"minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z},
            "terrainRows": [],
            "things": [],
            "zones": [],
        }

    def list_build_options(self, category=None, search=None):
        return {"options": [{"defName": "Wall", "label": "wall", "requiredTerrainAffordance": "Medium"}]}

    def list_growable_plants(self):
        return {"plants": [{"defName": "Plant_Rice", "label": "rice plant"}]}

    def check_zone_placement(self, zone_type, min_x, min_z, max_x, max_z):
        return {"valid": True, "requestedCells": 36, "validCells": 36, "invalid": []}

    def check_build_placements(self, placements):
        return {"valid": True, "requested": len(placements), "validCount": len(placements), "invalid": []}

    def submit_command(self, command):
        self.submitted.append(command)
        raise AssertionError("dry-run must not submit commands")


class InspectBridge(RimWorldBridge):
    def __init__(self):
        self.requests = []

    def _request_json(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        return {"gameLoaded": True}


class StabilizationM10ATests(unittest.TestCase):
    def test_ledger_normalizes_duplicates_distinguishes_zones_and_is_bounded(self):
        ledger = DryRunProposalLedger(maximum=2)
        first = ledger.add("set_speed", {"speed": 0})
        repeat = ledger.add("set_speed", {"speed": 0})
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeat["duplicate"])
        self.assertIn("Set speed to 0", ledger.summaries())

        zone_a = {"min_x": 1, "min_z": 2, "max_x": 6, "max_z": 7}
        self.assertFalse(ledger.add("create_stockpile", zone_a)["duplicate"])
        self.assertTrue(ledger.add("create_stockpile", dict(reversed(list(zone_a.items()))))["duplicate"])
        self.assertFalse(ledger.add("create_stockpile", {**zone_a, "max_x": 7})["duplicate"])
        self.assertEqual(len(ledger), 2)

    def test_allow_all_and_blueprint_order_are_normalized(self):
        ledger = DryRunProposalLedger()
        self.assertFalse(ledger.add("allow_all", {})["duplicate"])
        self.assertTrue(ledger.add("allow_all", {})["duplicate"])
        placements = [{"build_def": "Wall", "x": 1, "z": 2}, {"build_def": "Door", "x": 2, "z": 2}]
        self.assertFalse(ledger.add("place_blueprints", {"placements": placements})["duplicate"])
        self.assertTrue(ledger.add("place_blueprints", {"placements": list(reversed(placements))})["duplicate"])

    def test_dry_run_result_is_explicit_and_duplicate_is_not_reproposed(self):
        controller = object.__new__(AgentController)
        controller.dry_run = True
        controller.uncertain_commands = {}
        controller._begin_cycle()
        proposal = call("set_speed", {"speed": 0}, "speed-1")
        first, second = controller._execute_tool_call_batch([proposal, call("set_speed", {"speed": 0}, "speed-2")])
        first_result = json.loads(first["output"])
        second_result = json.loads(second["output"])
        self.assertEqual(
            {key: first_result[key] for key in ("dryRun", "proposed", "executed")},
            {"dryRun": True, "proposed": True, "executed": False},
        )
        self.assertFalse(second_result["proposed"])
        self.assertTrue(second_result["duplicateProposal"])

    def test_ledger_cycle_scope_normal_absence_and_store_isolation(self):
        dry = object.__new__(AgentController)
        dry.dry_run = True
        dry._begin_cycle()
        dry._get_dry_run_proposal_ledger().add("allow_all", {})
        self.assertEqual(len(dry.dry_run_proposals), 1)
        dry._begin_cycle()
        self.assertEqual(len(dry.dry_run_proposals), 0)

        live = object.__new__(AgentController)
        live.dry_run = False
        live._begin_cycle()
        self.assertIsNone(live.dry_run_proposals)

        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            state = base_state(version=10)
            store.update_current_state(state)
            store.set_decision_baseline(state)
            dry.state_store = store
            dry._get_dry_run_proposal_ledger().add("set_speed", {"speed": 0})
            self.assertNotIn("dryRunProposals", json.dumps(store.get_current_state()))
            self.assertEqual(snapshot_version(store.get_decision_baseline()), 10)

    def test_active_capability_idempotence_and_metadata(self):
        active = ActiveToolSet(DEFAULT_TOOL_REGISTRY, 3)
        first = active.enable("construction")
        schemas = active.schemas()
        second = active.enable("construction")
        self.assertFalse(first["alreadyEnabled"])
        self.assertTrue(second["alreadyEnabled"])
        self.assertEqual(second["toolsAdded"], 0)
        self.assertEqual(active.dynamic_groups, ("construction",))
        self.assertEqual(active.schemas(), schemas)

        controller = object.__new__(AgentController)
        controller.dry_run = True
        controller.active_tools = active
        controller.tool_registry = DEFAULT_TOOL_REGISTRY
        controller.dry_run_proposals = DryRunProposalLedger()
        context = controller._decorate_cycle_context({"contextVersion": 1})
        self.assertEqual(context["activeCapabilities"], ["construction"])
        self.assertEqual(context["dryRunProposals"], [])

    def test_prompt_and_schema_explain_stabilized_behavior(self):
        self.assertIn("do not call enable_capability for it again", SYSTEM_INSTRUCTIONS)
        self.assertIn("15x15 to 20x20", SYSTEM_INSTRUCTIONS)
        self.assertIn("dryRunProposals", SYSTEM_INSTRUCTIONS)
        schema = next(item for item in TOOLS if item["name"] == "inspect_map")
        self.assertIn("max_x-min_x+1", schema["description"])
        self.assertIn("at most 40", schema["description"])

    def test_inspect_map_accepts_40_and_rejects_41_locally(self):
        bridge = InspectBridge()
        self.assertTrue(bridge.inspect_map(0, 0, 39, 39)["gameLoaded"])
        self.assertEqual(len(bridge.requests), 1)
        rejected = bridge.inspect_map(0, 0, 40, 40)
        self.assertEqual(rejected["error"], "regionTooLarge")
        self.assertEqual(rejected["maxWidth"], 40)
        self.assertEqual(rejected["requestedWidth"], 41)
        self.assertEqual(len(bridge.requests), 1)

    def test_region_too_large_fields_survive_formatter(self):
        raw = {
            "success": True,
            "result": {"error": "regionTooLarge", "maxWidth": 40, "maxHeight": 40, "requestedWidth": 41},
        }
        result = ModelToolResultFormatter(logger=lambda _: None).format("inspect_map", raw, {})
        self.assertEqual(result["reason"], "regionTooLarge")
        self.assertEqual(result["maxWidth"], 40)
        self.assertEqual(result["requestedWidth"], 41)

    def test_native_compaction_item_passes_without_model_dump_warning(self):
        item = ResponseCompactionItem(id="cmp_1", encrypted_content="opaque", type="compaction")
        native = compacted_output_as_input(SimpleNamespace(output=[item]))
        self.assertIs(native[0], item)
        payload = {"model": "gpt-5.6", "previous_response_id": "resp_1", "input": native}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            transformed = maybe_transform(payload, ResponseCompactParams)
        self.assertEqual(transformed["input"][0]["type"], "compaction")
        self.assertEqual(caught, [])

    def test_pricing_components_and_missing_configuration(self):
        response_value = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=6000,
                output_tokens=500,
                input_tokens_details=SimpleNamespace(cached_tokens=4000, cache_write_tokens=1000),
            )
        )
        usage = extract_usage(response_value, Pricing(10.0, 2.0, 20.0, 12.0))
        self.assertAlmostEqual(usage.uncached_input_cost or 0, 0.01)
        self.assertAlmostEqual(usage.cached_input_cost or 0, 0.008)
        self.assertAlmostEqual(usage.cache_write_cost or 0, 0.012)
        self.assertAlmostEqual(usage.output_cost or 0, 0.01)
        self.assertAlmostEqual(usage.estimated_cost or 0, 0.04)
        self.assertIsNone(extract_usage(response_value, Pricing()).estimated_cost)

        controller = object.__new__(AgentController)
        controller.pricing = Pricing(10.0, 2.0, 20.0, 12.0)
        controller.model = "test-model"
        controller.cycle_cost = 0.0
        controller.session_cost = 0.0
        controller._log_response_usage(response_value, 1, "test", "response")
        controller._log_response_usage(response_value, 2, "test", "response")
        self.assertAlmostEqual(controller.cycle_cost, 0.08)
        self.assertAlmostEqual(controller.session_cost, 0.08)

    def test_stabilized_starter_cycle_finishes_in_six_requests(self):
        state = base_state(version=300)
        bridge = DryRunBridge(state)
        steps = [
            response("r1", [
                call("list_capabilities", {}, "caps"),
                call("inspect_map", {"min_x": 96, "min_z": 93, "max_x": 136, "max_z": 133}, "map-large"),
            ]),
            response("r2", [
                call("enable_capability", {"name": "utility"}, "utility"),
                call("enable_capability", {"name": "construction"}, "construction"),
                call("enable_capability", {"name": "zones"}, "zones"),
                call("inspect_map", {"min_x": 100, "min_z": 100, "max_x": 117, "max_z": 117}, "map-focused"),
            ]),
            response("r3", [
                call("set_speed", {"speed": 0}, "pause"),
                call("allow_all", {}, "allow"),
                call("list_build_options", {"category": None, "search": "wall"}, "builds"),
                call("list_growable_plants", {}, "plants"),
            ]),
            response("r4", [
                call("check_zone_placement", {"zone_type": "growing", "min_x": 102, "min_z": 102, "max_x": 107, "max_z": 107}, "zone-check"),
                call("check_build_placements", {"placements": [{"build_def": "Wall", "x": 110, "z": 110, "rotation": "North", "stuff_def": "WoodLog"}]}, "build-check"),
            ]),
            response("r5", [
                call("create_growing_zone", {"min_x": 102, "min_z": 102, "max_x": 107, "max_z": 107, "plant_def": "Plant_Rice", "minimum_valid_cells": 30}, "farm"),
                call("place_blueprints", {"placements": [{"build_def": "Wall", "x": 110, "z": 110, "rotation": "North", "stuff_def": "WoodLog"}]}, "room"),
            ]),
            response("r6", [], "Starter plan validated and proposed without changing RimWorld."),
        ]
        fake = FakeResponses(steps)
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            controller = AgentController(
                bridge=bridge,
                model="test-model",
                dry_run=True,
                client=SimpleNamespace(responses=fake),
                state_store=store,
                pricing=Pricing(),
            )
            controller.run_once({"type": "m10aMock"})

        self.assertEqual(len(fake.calls), 6)
        self.assertIsNone(controller.termination_reason)
        self.assertEqual(bridge.submitted, [])
        self.assertEqual(bridge.state["game"]["speed"], 1)
        self.assertEqual(bridge.map_requests[0], (96, 93, 136, 133))
        self.assertEqual(bridge.map_requests[1], (100, 100, 117, 117))
        all_outputs = [json.loads(item["output"]) for request in fake.calls[1:] for item in request["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]
        large_failure = next(item for item in all_outputs if item.get("reason") == "regionTooLarge")
        self.assertEqual(large_failure["maxWidth"], 40)
        continuation_text = json.dumps([request["input"] for request in fake.calls[1:]])
        self.assertIn("activeCapabilities", continuation_text)
        self.assertIn("Set speed to 0", continuation_text)
        self.assertIn("Allow all forbidden items", continuation_text)
        self.assertNotIn('"operations"', fake.calls[0]["input"][0]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
