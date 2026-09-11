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


def diagnostic(
    *,
    available=0,
    forbidden=0,
    required=20,
    remaining=20,
    capable=3,
    enabled=2,
    idle_enabled=1,
    primary=None,
):
    missing = max(0, remaining - available)
    if primary is None:
        if missing:
            primary = "missingMaterials"
        elif capable == 0:
            primary = "noCapableConstructor"
        elif enabled == 0:
            primary = "constructionDisabled"
        else:
            primary = "none"
    blockers = [] if primary == "none" else [primary]
    return {
        "gameLoaded": True,
        "targetAvailable": True,
        "target": {
            "id": "Blueprint_123",
            "stage": "blueprint",
            "defName": "Campfire",
            "label": "campfire",
            "position": {"x": 124, "z": 123},
            "stuffDef": None,
        },
        "requirements": [{
            "defName": "WoodLog",
            "label": "wood",
            "required": required,
            "remaining": remaining,
            "delivered": required - remaining,
            "available": available,
            "forbidden": forbidden,
            "missing": missing,
        }],
        "labor": {"capable": capable, "enabled": enabled, "idleEnabled": idle_enabled},
        "blockers": blockers,
        "primaryBlocker": primary,
    }


class ConstructionDiagnosticM10H1Tests(unittest.TestCase):
    def setUp(self):
        self.formatter = ModelToolResultFormatter(logger=lambda _: None)

    def format(self, value):
        return self.formatter.format("diagnose_construction", value, {"thing_id": "Blueprint_123"})

    def test_campfire_missing_wood_reports_exact_shortage(self):
        result = self.format(diagnostic(available=0, required=20, remaining=20))
        self.assertTrue(result["success"])
        self.assertEqual(result["primaryBlocker"], "missingMaterials")
        self.assertEqual(result["requirements"][0]["defName"], "WoodLog")
        self.assertEqual(result["requirements"][0]["missing"], 20)

    def test_enough_available_material_has_no_shortage_or_invented_blocker(self):
        result = self.format(diagnostic(available=25))
        self.assertEqual(result["requirements"][0]["missing"], 0)
        self.assertEqual(result["primaryBlocker"], "none")
        self.assertEqual(result["blockers"], [])

    def test_forbidden_material_is_distinct_from_available(self):
        result = self.format(diagnostic(available=0, forbidden=30))
        material = result["requirements"][0]
        self.assertEqual(material["available"], 0)
        self.assertEqual(material["forbidden"], 30)
        self.assertEqual(material["missing"], 20)
        self.assertEqual(result["primaryBlocker"], "missingMaterials")

    def test_labor_blockers_are_preserved(self):
        incapable = self.format(diagnostic(available=20, capable=0, enabled=0, idle_enabled=0))
        disabled = self.format(diagnostic(available=20, capable=3, enabled=0, idle_enabled=0))
        self.assertEqual(incapable["primaryBlocker"], "noCapableConstructor")
        self.assertEqual(incapable["labor"]["capable"], 0)
        self.assertEqual(disabled["primaryBlocker"], "constructionDisabled")
        self.assertEqual(disabled["labor"], {"capable": 3, "enabled": 0, "idleEnabled": 0})

    def test_unknown_hidden_or_non_player_target_is_generic_and_safe(self):
        raw = {
            "gameLoaded": True,
            "targetAvailable": False,
            "requirements": [],
            "primaryBlocker": "unavailableOrInvalidTarget",
        }
        result = self.format(raw)
        self.assertTrue(result["success"])
        self.assertFalse(result["targetAvailable"])
        self.assertNotIn("target", result)
        self.assertEqual(result["primaryBlocker"], "unavailableOrInvalidTarget")

    def test_result_is_compact_and_drops_unrecognized_bridge_fields(self):
        raw = diagnostic(available=0, forbidden=10)
        raw["internalPath"] = "must not reach model"
        result = self.format(raw)
        self.assertLess(serialized_chars(result), 1_000)
        self.assertNotIn("internalPath", json.dumps(result))

    def test_tool_is_core_read_only_and_guard_is_unchanged(self):
        registration = DEFAULT_TOOL_REGISTRY.registration("diagnose_construction")
        self.assertIsNotNone(registration)
        self.assertEqual(registration.group, CORE_GROUP)
        self.assertTrue(registration.read_only)
        self.assertTrue(is_read_only_tool("diagnose_construction"))
        self.assertIn("diagnose_construction", {
            item["name"] for item in ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas()
        })
        self.assertEqual(DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, 30_000)
        self.assertLess(serialized_chars(ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas()), 10_000)

    def test_bridge_uses_focused_get_endpoint(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"targetAvailable": False, "primaryBlocker": "unavailableOrInvalidTarget"}
        bridge = RimWorldBridge()
        bridge.session.request = Mock(return_value=response)
        result = bridge.diagnose_construction("Blueprint_123")
        self.assertEqual(result["primaryBlocker"], "unavailableOrInvalidTarget")
        bridge.session.request.assert_called_once_with(
            "GET",
            "http://127.0.0.1:47831/construction/diagnose",
            timeout=5.0,
            params={"thingId": "Blueprint_123"},
        )

    def test_main_thread_endpoint_uses_verified_hidden_safe_apis(self):
        root = Path(__file__).parent.parent / "Source"
        diagnostic_source = (root / "RimGPTConstructionDiagnosticJson.cs").read_text(encoding="utf-8")
        component = (root / "RimGPTGameComponent.cs").read_text(encoding="utf-8")
        http = (root / "RimGPTHttpBridge.cs").read_text(encoding="utf-8")
        self.assertIn('path == "/construction/diagnose"', http)
        self.assertIn("RimGPTReadRequestType.ConstructionDiagnostic", component)
        self.assertIn("constructible.TotalMaterialCost()", diagnostic_source)
        self.assertIn("constructible.ThingCountNeeded(pair.Key)", diagnostic_source)
        self.assertIn("thing.Position.Fogged(map)", diagnostic_source)
        self.assertIn("thing.Faction != Faction.OfPlayer", diagnostic_source)
        self.assertIn("thing.IsForbidden(Faction.OfPlayer)", diagnostic_source)
        self.assertIn("pawn.WorkTypeIsDisabled(construction)", diagnostic_source)

    def test_prompt_version_advanced_for_focused_routing(self):
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10h2")


if __name__ == "__main__":
    unittest.main()
