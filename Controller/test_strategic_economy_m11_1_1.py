import copy
import json
import tempfile
import unittest
from pathlib import Path

from colony_state_query import ColonyStateQuery
from decision_context import DecisionContextBuilder, build_current_summary, food_summary
from state_store import StateStore
from strategic_projects import build_project_context
from test_state_diff import base_state


class StrategicEconomyM111Tests(unittest.TestCase):
    def test_wealth_uses_verified_map_watcher_and_serializes_compactly(self):
        root = Path(__file__).parent.parent
        builder = (root / "Source" / "RimGPTStateBuilder.cs").read_text(encoding="utf-8")
        writer = (root / "Source" / "RimGPTStateJsonWriter.cs").read_text(encoding="utf-8")
        self.assertIn("map.wealthWatcher.WealthTotal", builder)
        self.assertIn("map.wealthWatcher.WealthItems", builder)
        self.assertIn("map.wealthWatcher.WealthBuildings", builder)
        self.assertIn("map.wealthWatcher.WealthPawns", builder)
        self.assertNotIn("raidPoints", builder)
        self.assertNotIn("storyteller", builder.lower())
        self.assertIn('json.Append(",\\"wealth\\":{");', writer)

        wealth = build_current_summary(base_state())["wealth"]
        self.assertEqual(
            wealth,
            {"total": 18432, "itemValue": 7200, "buildingValue": 6400, "pawnValue": 4832},
        )
        self.assertLess(len(json.dumps(wealth, separators=(",", ":"))), 100)

    def test_food_days_are_safe_and_sensible_for_zero_low_and_high_food(self):
        zero = base_state()
        zero["resources"]["available"]["food"] = {"meals": 0, "totalNutrition": 0.0}
        self.assertEqual(food_summary(zero, 1)["estimatedFoodDays"], 0.0)

        low = copy.deepcopy(zero)
        low["resources"]["available"]["food"] = {"meals": 2, "totalNutrition": 4.8}
        self.assertEqual(food_summary(low, 2)["estimatedFoodDays"], 1.5)

        high = copy.deepcopy(zero)
        high["resources"]["available"]["food"] = {"meals": 36, "totalNutrition": 48.0}
        self.assertEqual(food_summary(high, 3)["estimatedFoodDays"], 10.0)
        self.assertGreater(food_summary(high, 3)["estimatedFoodDays"], food_summary(low, 2)["estimatedFoodDays"])

    def test_zero_colonists_has_no_invalid_runway_and_food_contract_remains_compatible(self):
        summary = food_summary(base_state(), 0)
        self.assertIsNone(summary["estimatedFoodDays"])
        self.assertIn(summary["status"], ("critical", "low", "adequate", "abundant"))
        self.assertEqual(summary["meals"], 10)

    def test_resources_read_includes_compact_wealth_without_hidden_map_data(self):
        state = base_state()
        state["hiddenMarker"] = "must-not-leak"
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory, logger=lambda _message: None)
            store.update_current_state(state)
            result = ColonyStateQuery(store).get("resources")
        self.assertEqual(
            result["data"]["wealth"],
            {"total": 18432, "itemValue": 7200, "buildingValue": 6400, "pawnValue": 4832},
        )
        self.assertNotIn("must-not-leak", json.dumps(result))
        self.assertLess(len(json.dumps(result, separators=(",", ":"))), 2_000)

    def test_capable_idle_capacity_is_independent_from_project_work(self):
        state = base_state()
        state["operations"]["labor"] = {
            "idleColonistCount": 1,
            "capableIdleColonistCount": 1,
            "idleColonists": [{"id": "Pawn_A", "name": "Ava"}],
            "capableIdleColonists": [{"id": "Pawn_A", "name": "Ava"}],
            "pendingWork": {},
            "obviousBlockers": [],
        }
        summary = build_current_summary(state)["labor"]
        self.assertEqual(summary["capableIdleColonists"], 1)
        self.assertEqual(summary["capableIdlePawnIds"], ["Pawn_A"])

        projects = build_project_context(None, {"signals": []}, None, state, {})
        continuity = projects["workContinuity"]
        self.assertEqual(continuity["capableIdleColonists"], 1)
        self.assertEqual(continuity["capableIdlePawnIds"], ["Pawn_A"])
        self.assertEqual(continuity["availableProjectTasks"], 0)

    def test_context_remains_bounded_and_never_sends_full_state(self):
        state = base_state()
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory, logger=lambda _message: None)
            store.update_current_state(state)
            context = DecisionContextBuilder(store, logger=logs.append).build()
        self.assertEqual(context["currentSummary"]["wealth"]["total"], 18432)
        self.assertIn("estimatedFoodDays", context["currentSummary"]["food"])
        self.assertLess(len(json.dumps(context["currentSummary"], separators=(",", ":"))), 2_500)
        self.assertTrue(any("fullStateSent=false" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
