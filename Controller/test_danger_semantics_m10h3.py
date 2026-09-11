import json
import tempfile
import unittest
from pathlib import Path

from agent_controller import SYSTEM_INSTRUCTIONS
from colony_state_query import ColonyStateQuery
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, measure_context
from decision_context import DecisionContextBuilder, build_bootstrap_state, build_current_summary, serialize_context
from state_diff import StateDiff
from state_store import StateStore
from test_state_diff import base_state
from tool_registry import DEFAULT_TOOL_REGISTRY, ActiveToolSet, select_initial_tool_groups


def danger(thing_id, reason, *, faction=None, downed=False):
    return {
        "id": thing_id,
        "type": "pawn",
        "defName": "Vulture" if reason == "manhunter" else "Human",
        "label": "vulture" if reason == "manhunter" else "raider",
        "faction": faction,
        "dangerReason": reason,
        "position": {"x": 80, "z": 90},
        "downed": downed,
        "weapon": None,
    }


class DangerSemanticsM10H3Tests(unittest.TestCase):
    def test_hostile_faction_and_manhunter_are_counted_as_active_dangers(self):
        state = base_state()
        state["threats"] = [
            danger("Raider_1", "hostileFaction", faction="Pirates"),
            danger("Vulture_1", "manhunter"),
        ]
        summary = build_current_summary(state)
        self.assertEqual(summary["threats"], {"status": "active", "active": 2})
        bootstrap = build_bootstrap_state(state)
        self.assertEqual(
            [item["dangerReason"] for item in bootstrap["immediateThreats"]],
            ["hostileFaction", "manhunter"],
        )

    def test_harmless_wildlife_produces_no_threat_or_combat_preload(self):
        state = base_state()
        state["threats"] = []
        summary = build_current_summary(state)
        context = {"currentSummary": summary}
        self.assertEqual(summary["threats"], {"status": "none", "active": 0})
        self.assertEqual(select_initial_tool_groups(context), ())

    def test_visible_manhunter_preloads_existing_combat_capability(self):
        state = base_state()
        state["threats"] = [danger("Vulture_1", "manhunter")]
        context = {"currentSummary": build_current_summary(state)}
        self.assertEqual(select_initial_tool_groups(context), ("combat",))

    def test_focused_threat_read_preserves_reason_without_extra_combat_state(self):
        state = base_state()
        state["threats"] = [danger("Vulture_1", "manhunter")]
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            store.update_current_state(state)
            result = ColonyStateQuery(store).get("threats")
        self.assertEqual(result["data"][0]["dangerReason"], "manhunter")
        self.assertLess(len(json.dumps(result)), 1_000)

    def test_danger_reason_changes_are_visible_in_semantic_delta(self):
        before = base_state()
        before["threats"] = [danger("Pawn_1", "hostileFaction", faction="Pirates")]
        after = base_state(version=121)
        after["threats"] = [danger("Pawn_1", "aggressiveMentalState", faction="Pirates")]
        changed = StateDiff.compare(before, after)["changes"]["threats"]["changed"][0]
        self.assertEqual(changed["dangerReason"]["from"], "hostileFaction")
        self.assertEqual(changed["dangerReason"]["to"], "aggressiveMentalState")

    def test_state_builder_uses_vanilla_active_threat_and_explicit_visibility_semantics(self):
        source = (
            Path(__file__).parent.parent / "Source" / "RimGPTStateBuilder.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("GenHostility.IsActiveThreatToPlayer(pawn, false)", source)
        self.assertIn("mentalState is MentalState_Manhunter", source)
        self.assertIn("mentalState.ForceHostileTo(Faction.OfPlayer)", source)
        self.assertIn("pawn.Dead || pawn.Downed || !pawn.Spawned || pawn.Map != map", source)
        self.assertIn("pawn.Position.Fogged(map)", source)
        self.assertNotIn("pawn.Faction == null || !pawn.HostileTo", source)
        self.assertNotIn('IndexOf("Manhunter"', source)

    def test_representative_manhunter_context_stays_below_guard_without_full_state(self):
        baseline = base_state()
        current = base_state(version=121)
        current["threats"] = [danger("Vulture_1", "manhunter")]
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            store.update_current_state(baseline)
            store.set_decision_baseline(baseline)
            store.update_current_state(current)
            context = DecisionContextBuilder(store, logger=lambda _: None).build()
        items = [{"role": "user", "content": [{"type": "input_text", "text": serialize_context(context)}]}]
        tools = ActiveToolSet(DEFAULT_TOOL_REGISTRY)
        tools.enable("combat")
        breakdown = measure_context(
            instructions=SYSTEM_INSTRUCTIONS,
            tools=tools.schemas(),
            input_items=items,
            state=current,
            accumulated_tool_result_chars=0,
            carried_context_chars=0,
            context_payload=context,
        )
        self.assertLess(breakdown.estimated_input_tokens, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertFalse(breakdown.full_state_sent)


if __name__ == "__main__":
    unittest.main()
