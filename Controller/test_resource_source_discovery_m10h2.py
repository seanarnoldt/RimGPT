import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from bridge import RimWorldBridge
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, serialized_chars
from model_tool_result import ModelToolResultFormatter
from prompt_runtime import RIMGPT_PROMPT_VERSION
from tool_registry import CORE_GROUP, DEFAULT_TOOL_REGISTRY, ActiveToolSet
from tools import is_read_only_tool


def resource_result(*, resource_def="WoodLog", available=0, forbidden=0, sources=None, **extra):
    return {
        "gameLoaded": True,
        "resourceKnown": True,
        "supported": True,
        "resourceDef": resource_def,
        "label": "wood" if resource_def == "WoodLog" else resource_def,
        "available": available,
        "forbidden": forbidden,
        "sources": sources or [],
        "sourceTypes": sorted({item["type"] for item in (sources or [])}),
        "truncated": False,
        **extra,
    }


class ResourceSourceDiscoveryM10H2Tests(unittest.TestCase):
    def setUp(self):
        self.formatter = ModelToolResultFormatter(logger=lambda _: None)

    def format(self, value, resource_def="WoodLog"):
        return self.formatter.format("find_resource_sources", value, {"resource_def": resource_def})

    def test_zero_stored_wood_can_report_visible_cuttable_sources(self):
        raw = resource_result(sources=[{
            "type": "cuttablePlant",
            "defName": "Plant_TreeOak",
            "label": "oak tree",
            "count": 14,
            "sample": [{"x": 120, "z": 133}, {"x": 122, "z": 135}],
        }])
        result = self.format(raw)
        self.assertEqual(result["available"], 0)
        self.assertEqual(result["sources"][0]["type"], "cuttablePlant")
        self.assertEqual(result["sources"][0]["count"], 14)
        self.assertEqual(result["sourceTypes"], ["cuttablePlant"])

    def test_steel_items_distinguish_available_and_forbidden(self):
        raw = resource_result(
            resource_def="Steel",
            available=75,
            forbidden=40,
            sources=[{
                "type": "haulableItem",
                "defName": "Steel",
                "label": "steel",
                "count": 2,
                "quantity": 115,
                "sample": [
                    {"id": "Thing_Steel1", "x": 4, "z": 5, "quantity": 75, "forbidden": False},
                    {"id": "Thing_Steel2", "x": 8, "z": 9, "quantity": 40, "forbidden": True},
                ],
            }],
        )
        result = self.format(raw, "Steel")
        self.assertEqual(result["available"], 75)
        self.assertEqual(result["forbidden"], 40)
        self.assertFalse(result["sources"][0]["sample"][0]["forbidden"])
        self.assertTrue(result["sources"][0]["sample"][1]["forbidden"])

    def test_visible_mineable_steel_is_a_distinct_source(self):
        raw = resource_result(resource_def="Steel", sources=[{
            "type": "mineableDeposit",
            "defName": "MineableSteel",
            "label": "compacted steel",
            "count": 3,
            "nominalYield": 120,
            "sample": [{"x": 40, "z": 41}],
        }])
        result = self.format(raw, "Steel")
        self.assertEqual(result["sources"], [{
            "type": "mineableDeposit",
            "defName": "MineableSteel",
            "label": "compacted steel",
            "count": 3,
            "nominalYield": 120,
            "sample": [{"x": 40, "z": 41}],
        }])
        self.assertNotIn("MineableComponents", json.dumps(result))

    def test_unknown_resource_is_reported_cleanly(self):
        raw = {
            "gameLoaded": True,
            "resourceKnown": False,
            "supported": False,
            "resourceDef": "InventedResource",
            "available": 0,
            "forbidden": 0,
            "sources": [],
            "sourceTypes": [],
            "reason": "unknownOrUnsupportedResourceDef",
        }
        result = self.format(raw, "InventedResource")
        self.assertFalse(result["resourceKnown"])
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason"], "unknownOrUnsupportedResourceDef")

    def test_model_result_is_bounded_and_allowlisted(self):
        sources = []
        for group in range(20):
            sources.append({
                "type": "cuttablePlant",
                "defName": f"Plant_Tree{group:02d}",
                "label": f"tree {group}",
                "count": 100,
                "sample": [{"x": i, "z": group} for i in range(30)],
                "hiddenInternal": "not model facing",
            })
        raw = resource_result(sources=sources, hiddenInternal="not model facing")
        result = self.format(raw)
        self.assertLessEqual(len(result["sources"]), 8)
        self.assertTrue(all(len(source["sample"]) <= 6 for source in result["sources"]))
        self.assertLessEqual(sum(len(source["sample"]) for source in result["sources"]), 16)
        self.assertLess(serialized_chars(result), 5_000)
        self.assertNotIn("hiddenInternal", json.dumps(result))

    def test_tool_is_core_read_only_and_safeguards_are_unchanged(self):
        registration = DEFAULT_TOOL_REGISTRY.registration("find_resource_sources")
        self.assertIsNotNone(registration)
        self.assertEqual(registration.group, CORE_GROUP)
        self.assertTrue(registration.read_only)
        self.assertTrue(is_read_only_tool("find_resource_sources"))
        self.assertIn("find_resource_sources", {
            item["name"] for item in ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas()
        })
        self.assertEqual(DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, 30_000)
        self.assertLess(serialized_chars(ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas()), 11_000)

    def test_bridge_uses_focused_get_endpoint(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = resource_result()
        bridge = RimWorldBridge()
        bridge.session.request = Mock(return_value=response)
        result = bridge.find_resource_sources("WoodLog")
        self.assertTrue(result["supported"])
        bridge.session.request.assert_called_once_with(
            "GET",
            "http://127.0.0.1:47831/resources/sources",
            timeout=5.0,
            params={"resourceDef": "WoodLog"},
        )

    def test_main_thread_query_uses_visibility_yield_and_designation_semantics_without_mutation(self):
        root = Path(__file__).parent.parent / "Source"
        source = (root / "RimGPTResourceSourceJson.cs").read_text(encoding="utf-8")
        component = (root / "RimGPTGameComponent.cs").read_text(encoding="utf-8")
        http = (root / "RimGPTHttpBridge.cs").read_text(encoding="utf-8")
        self.assertIn('path == "/resources/sources"', http)
        self.assertIn("RimGPTReadRequestType.ResourceSources", component)
        self.assertIn("ThingRequestGroup.HaulableEver", source)
        self.assertIn("thing.Position.Fogged(map)", source)
        self.assertIn("plant.def.plant.choppedThingDef != result.ResourceDef || plant.YieldNow() <= 0", source)
        self.assertIn("designator.CanDesignateThing(plant).Accepted", source)
        self.assertIn("mineable.def.building.mineableThing != result.ResourceDef", source)
        self.assertIn("designator.CanDesignateThing(mineable).Accepted", source)
        self.assertNotIn(".DesignateThing(", source)

    def test_m10h1_diagnostic_contract_is_unchanged(self):
        raw = {
            "gameLoaded": True,
            "targetAvailable": True,
            "target": {"id": "Blueprint_1", "stage": "blueprint", "defName": "Campfire"},
            "requirements": [{
                "defName": "WoodLog", "required": 20, "remaining": 20,
                "delivered": 0, "available": 0, "forbidden": 0, "missing": 20,
            }],
            "labor": {"capable": 2, "enabled": 1, "idleEnabled": 0},
            "blockers": ["missingMaterials"],
            "primaryBlocker": "missingMaterials",
        }
        result = self.formatter.format("diagnose_construction", raw, {"thing_id": "Blueprint_1"})
        self.assertEqual(result["requirements"][0]["defName"], "WoodLog")
        self.assertEqual(result["primaryBlocker"], "missingMaterials")

    def test_prompt_version_advanced(self):
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10h2")


if __name__ == "__main__":
    unittest.main()
