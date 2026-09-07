import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_controller import SYSTEM_INSTRUCTIONS
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, measure_context, serialized_chars
from decision_context import DecisionContextBuilder
from decision_handoff import prepare_handoff
from prompt_runtime import RIMGPT_PROMPT_VERSION
from risk_tracking import (
    MAX_RISK_CONTEXT_CHARS,
    MAX_TRACKED_RISKS,
    build_operational_risk_summary,
    empty_risk_metadata,
    update_risk_metadata,
    validate_risk_metadata,
)
from state_store import StateIdentity, StateStore
from test_state_diff import base_state
from tool_registry import DEFAULT_TOOL_REGISTRY, ActiveToolSet, select_initial_tool_groups


def heat_state(severity: float | None, *, version: int, ticks: int = 1_000, lineage: str = "lineage-a") -> dict:
    state = base_state(version=version, ticks=ticks, lineage=lineage)
    pawn = state["colonists"][0]
    pawn["currentJob"] = {"defName": "Research", "label": "researching"}
    pawn["health"]["hediffs"] = [] if severity is None else [
        {"defName": "Heatstroke", "label": "heatstroke", "severity": severity}
    ]
    state["map"]["environment"]["outdoorTemperature"] = 41.0
    state["buildings"][0]["room"] = {
        "id": "room-1",
        "indoors": True,
        "enclosed": True,
        "usesOutdoorTemperature": False,
        "suitableForTemperatureControl": True,
        "temperature": 24.0,
    }
    return state


def research_handoff() -> dict:
    return prepare_handoff({
        "assessment": "Continue research setup while conditions remain tolerable",
        "open_loops": [{
            "id": None,
            "objective": "Establish reliable research capability",
            "next_action": "Complete and operate the research bench",
            "status": "pending",
            "reason": "Long-term technology progress remains important",
        }],
        "resolved_loops": [],
    }, None)


class RiskTriageM10FTests(unittest.TestCase):
    def setUp(self):
        self.identity = StateIdentity("lineage-a").as_dict()

    def test_very_mild_stable_heatstroke_is_monitor_not_urgent(self):
        baseline = heat_state(0.005, version=10)
        current = heat_state(0.006, version=11)
        summary = build_operational_risk_summary(baseline, current, empty_risk_metadata(self.identity))
        risk = summary["items"][0]

        self.assertEqual(risk["trend"], "stable")
        self.assertEqual(risk["triage"], "monitor")
        self.assertFalse(risk["interruptNormalPriorities"])
        self.assertEqual(risk["currentJob"], "Research")
        self.assertTrue(summary["saferRecoverySpaceAvailable"])
        self.assertLess(serialized_chars(summary), MAX_RISK_CONTEXT_CHARS)

    def test_meaningfully_worsening_heatstroke_escalates(self):
        summary = build_operational_risk_summary(
            heat_state(0.005, version=10),
            heat_state(0.22, version=11),
            empty_risk_metadata(self.identity),
        )
        risk = summary["items"][0]
        self.assertEqual(risk["trend"], "worsening")
        self.assertEqual(risk["triage"], "urgent")
        self.assertTrue(risk["interruptNormalPriorities"])

    def test_critical_condition_interrupts_normal_priorities_and_permits_tactical_control(self):
        summary = build_operational_risk_summary(
            heat_state(0.4, version=10),
            heat_state(0.85, version=11),
            empty_risk_metadata(self.identity),
        )
        risk = summary["items"][0]
        self.assertEqual(risk["triage"], "critical")
        self.assertTrue(summary["interruptNormalPriorities"])
        self.assertIn("tactical control is permitted", risk["recommendedResponse"])

    def test_age_vulnerability_alone_does_not_create_immediate_danger(self):
        state = heat_state(None, version=11)
        state["colonists"][0]["age"] = 80
        summary = build_operational_risk_summary(None, state, empty_risk_metadata(self.identity))
        self.assertEqual(summary["overallTriage"], "none")
        self.assertFalse(summary["interruptNormalPriorities"])
        self.assertEqual(summary["items"], [])

    def test_recently_resolved_mild_condition_does_not_reopen_as_emergency(self):
        mild = heat_state(0.005, version=10)
        recovered = heat_state(None, version=11)
        metadata = update_risk_metadata(empty_risk_metadata(self.identity), None, mild)
        metadata = update_risk_metadata(metadata, mild, recovered)
        recurrence = heat_state(0.08, version=12)

        summary = build_operational_risk_summary(recovered, recurrence, metadata)
        risk = summary["items"][0]
        self.assertTrue(risk["recentMinorRecurrence"])
        self.assertEqual(risk["triage"], "monitor")
        self.assertFalse(summary["interruptNormalPriorities"])

        worsened = build_operational_risk_summary(recovered, heat_state(0.3, version=12), metadata)
        self.assertEqual(worsened["items"][0]["triage"], "urgent")

    def test_risk_metadata_is_bounded_colony_scoped_and_contains_no_model_history(self):
        raw = empty_risk_metadata(self.identity)
        raw["conditions"] = [
            {
                "key": f"Pawn_{index}|heatstroke",
                "pawnId": f"Pawn_{index}",
                "condition": "Heatstroke",
                "status": "active",
                "lastSeverity": 0.01,
                "peakSeverity": 0.02,
                "lastTriage": "monitor",
                "observedCycles": 1,
                "cyclesSinceResolved": 0,
                "transcript": "must not persist",
                "modelText": "must not persist",
            }
            for index in range(MAX_TRACKED_RISKS + 10)
        ]
        validated = validate_risk_metadata(raw, self.identity)
        self.assertLessEqual(len(validated["conditions"]), MAX_TRACKED_RISKS)
        encoded = json.dumps(validated)
        self.assertNotIn("transcript", encoded)
        self.assertNotIn("modelText", encoded)
        with self.assertRaisesRegex(ValueError, "colony identity mismatch"):
            validate_risk_metadata(validated, StateIdentity("lineage-b").as_dict())

    def test_model_risk_summary_is_hard_bounded(self):
        current = heat_state(0.25, version=11)
        template = current["colonists"][0]
        current["colonists"] = []
        for index in range(MAX_TRACKED_RISKS + 10):
            pawn = copy.deepcopy(template)
            pawn["id"] = f"Pawn_{index}_" + ("x" * 60)
            pawn["health"]["hediffs"][0]["defName"] = "Heatstroke_" + ("y" * 60)
            current["colonists"].append(pawn)
        summary = build_operational_risk_summary(None, current, empty_risk_metadata(self.identity))
        self.assertLessEqual(serialized_chars(summary), MAX_RISK_CONTEXT_CHARS)
        self.assertTrue(summary["detailsTruncated"])
        self.assertEqual(summary["riskCount"], MAX_TRACKED_RISKS + 10)

    def test_risk_metadata_persists_only_with_successful_decision_and_is_colony_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = heat_state(0.005, version=10)
            store = StateStore(root, logger=lambda _: None)
            store.update_current_state(state)
            self.assertFalse(store._risk_path().exists())

            store.commit_successful_decision(state, research_handoff())
            self.assertTrue(store._risk_path().exists())
            self.assertEqual(store.get_risk_metadata()["conditions"][0]["condition"], "Heatstroke")

            restarted = StateStore(root, logger=lambda _: None)
            restarted.update_current_state(state)
            self.assertEqual(restarted.get_risk_metadata(), store.get_risk_metadata())

            other = heat_state(0.005, version=1, lineage="lineage-b")
            restarted.update_current_state(other)
            self.assertEqual(restarted.get_risk_metadata()["identity"], StateIdentity("lineage-b").as_dict())
            self.assertEqual(restarted.get_risk_metadata()["conditions"], [])

    def test_minor_risk_keeps_productive_parent_objective_active(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = heat_state(0.005, version=10)
            current = heat_state(0.006, version=11)
            store = StateStore(Path(directory), logger=lambda _: None)
            store.update_current_state(baseline)
            handoff = research_handoff()
            store.commit_successful_decision(baseline, handoff)
            store.update_current_state(current)
            context = DecisionContextBuilder(store, logger=lambda _: None).build()

        self.assertEqual(context["operationalRisk"]["overallTriage"], "monitor")
        self.assertFalse(context["operationalRisk"]["interruptNormalPriorities"])
        self.assertEqual(context["previousDecision"]["openLoops"][0]["objective"], "Establish reliable research capability")

    def test_policy_discourages_combat_controls_for_mild_exposure_but_preserves_emergency_use(self):
        combat = next(item for item in DEFAULT_TOOL_REGISTRY.capability_list(("core",)) if item["name"] == "combat")
        draft = DEFAULT_TOOL_REGISTRY.registration("draft").schema
        move = DEFAULT_TOOL_REGISTRY.registration("move").schema

        self.assertIn("Do not enable combat controls", SYSTEM_INSTRUCTIONS)
        self.assertIn("genuine immediate danger", SYSTEM_INSTRUCTIONS)
        self.assertIn("Keep parent strategic objectives active", SYSTEM_INSTRUCTIONS)
        self.assertIn("Do not enable solely for mild non-combat exposure", combat["description"])
        self.assertIn("High-cost tactical intervention", draft["description"])
        self.assertIn("genuine immediate danger", move["description"])
        self.assertEqual(select_initial_tool_groups({
            "currentSummary": {"threats": {"status": "none", "active": 0}},
            "operationalRisk": {"overallTriage": "monitor"},
        }), ())
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10h1")

    def test_representative_context_remains_below_guard_without_full_state(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = heat_state(0.005, version=10)
            current = heat_state(0.006, version=11)
            logs = []
            store = StateStore(Path(directory), logger=lambda _: None)
            store.update_current_state(baseline)
            store.commit_successful_decision(baseline, research_handoff())
            store.update_current_state(current)
            context = DecisionContextBuilder(store, logger=logs.append).build()
            items = [{"role": "user", "content": [{"type": "input_text", "text": json.dumps(context)}]}]
            breakdown = measure_context(
                instructions=SYSTEM_INSTRUCTIONS,
                tools=ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas(),
                input_items=items,
                state=current,
                accumulated_tool_result_chars=0,
                carried_context_chars=0,
                context_payload=context,
            )

        self.assertLess(breakdown.estimated_input_tokens, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertFalse(breakdown.full_state_sent)
        self.assertTrue(any("fullStateSent=false" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
