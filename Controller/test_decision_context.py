import copy
import json
import tempfile
import unittest
from pathlib import Path

from context_telemetry import serialized_chars
from decision_context import DecisionContextBuilder, build_current_summary, serialize_context
from state_store import StateStore
from test_state_diff import base_state, pawn


class DecisionContextTests(unittest.TestCase):
    def make_store(self, baseline=None, current=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = StateStore(Path(temporary.name), logger=lambda _: None)
        if baseline is not None:
            store.update_current_state(baseline)
            store.set_decision_baseline(baseline)
        if current is not None:
            store.update_current_state(current)
        return store

    def test_normal_context_contains_memory_summary_delta_and_trigger(self):
        baseline = base_state()
        current = base_state(version=121, ticks=1060)
        current["resources"]["available"]["wood"] = 200
        store = self.make_store(baseline, current)
        store.apply_memory_update({"currentGoals": ["Build a freezer"]})

        context = DecisionContextBuilder(store, logger=lambda _: None).build({"type": "manualTest"})

        self.assertFalse(context["bootstrap"])
        self.assertEqual(context["strategicMemory"]["currentGoals"], ["Build a freezer"])
        self.assertEqual(context["currentSummary"]["snapshotVersion"], 121)
        self.assertIn("resources", context["changesSinceLastDecision"]["changes"])
        self.assertEqual(context["trigger"], {"type": "manualTest"})

    def test_normal_context_does_not_contain_raw_full_state_or_operations(self):
        baseline = base_state()
        current = base_state(version=121)
        current["operations"]["secretRawMarker"] = "RAW_OPERATION_MARKER"
        current["colonists"][0]["secretRawMarker"] = "RAW_PAWN_MARKER"
        context = DecisionContextBuilder(self.make_store(baseline, current), logger=lambda _: None).build()
        payload = serialize_context(context)
        self.assertNotIn("RAW_OPERATION_MARKER", payload)
        self.assertNotIn("RAW_PAWN_MARKER", payload)
        self.assertNotIn('"operations"', payload)

    def test_memory_appears_once_and_summary_is_small(self):
        baseline = base_state()
        current = base_state(version=121)
        store = self.make_store(baseline, current)
        store.apply_memory_update({"nextPriorities": ["MEMORY_SENTINEL"]})
        context = DecisionContextBuilder(store, logger=lambda _: None).build()
        payload = serialize_context(context)
        self.assertEqual(payload.count("MEMORY_SENTINEL"), 1)
        self.assertLess(serialized_chars(context["currentSummary"]), 2500)

    def test_missing_baseline_builds_bounded_bootstrap_with_key_data(self):
        current = base_state()
        current["colonists"].append(pawn("Pawn_B"))
        current["threats"] = [{"id": "Raider_1", "type": "pawn", "defName": "Human", "label": "raider", "position": {"x": 90, "z": 90}}]
        context = DecisionContextBuilder(self.make_store(current=current), logger=lambda _: None).build()
        bootstrap = context["bootstrapState"]
        self.assertTrue(context["bootstrap"])
        self.assertEqual(bootstrap["colonists"][0]["id"], "Pawn_A")
        self.assertIn("available", bootstrap["resources"])
        self.assertEqual(bootstrap["immediateThreats"][0]["id"], "Raider_1")
        self.assertNotIn("needs", bootstrap["colonists"][0])

    def test_identity_change_and_schema_change_require_bootstrap(self):
        baseline = base_state()
        changed_identity = base_state(version=1, lineage="lineage-b")
        identity_context = DecisionContextBuilder(self.make_store(baseline, changed_identity), logger=lambda _: None).build()
        self.assertTrue(identity_context["bootstrap"])

        changed_schema = base_state(version=121, schema=3)
        schema_context = DecisionContextBuilder(self.make_store(baseline, changed_schema), logger=lambda _: None).build()
        self.assertTrue(schema_context["bootstrap"])

    def test_large_bootstrap_is_bounded_and_never_copies_giant_arrays(self):
        current = base_state()
        current["colonists"] = [pawn(f"Pawn_{index}") for index in range(100)]
        current["buildings"] = [
            {"id": f"Building_{index}", "type": "building", "defName": "Wall", "label": "wall", "position": {"x": index, "z": index}, "raw": "x" * 1000}
            for index in range(1000)
        ]
        current["map"]["rawCells"] = [{"x": index, "z": index} for index in range(10000)]
        context = DecisionContextBuilder(
            self.make_store(current=current), max_bootstrap_chars=10_000, logger=lambda _: None
        ).build()
        self.assertLessEqual(serialized_chars(context["bootstrapState"]), 10_000)
        self.assertNotIn("rawCells", json.dumps(context))
        self.assertNotIn('"raw"', json.dumps(context))

    def test_summary_categories_derive_from_current_state(self):
        state = base_state()
        state["colonists"][0]["health"].update({"summary": "downed", "downed": True})
        state["resources"]["available"]["food"] = {"meals": 0, "totalNutrition": 0}
        summary = build_current_summary(state)
        self.assertEqual(summary["health"]["status"], "emergency")
        self.assertEqual(summary["food"]["status"], "critical")
        self.assertEqual(summary["power"]["status"], "surplus")


if __name__ == "__main__":
    unittest.main()
