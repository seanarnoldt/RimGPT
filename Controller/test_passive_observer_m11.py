import copy
import tempfile
import unittest
from pathlib import Path

from decision_handoff import prepare_handoff
from decision_trigger import DecisionTrigger, TriggerContext, TriggerEvaluator
from progress_tracking import empty_stall_metadata
from state_observer import StateObserver
from state_store import StateIdentity, StateStore
from test_progress_verification_m10e import room
from test_state_diff import base_state, changed_state, pawn


class FakeBridge:
    def __init__(self, states):
        self.states = [copy.deepcopy(item) for item in states]
        self.calls = []

    def get_state(self):
        self.calls.append(("GET", "/state"))
        return copy.deepcopy(self.states.pop(0))


def advance(state, ticks=60):
    result = changed_state(state)
    result["snapshot"]["ticksGame"] = state["snapshot"]["ticksGame"] + ticks
    result["game"]["ticksGame"] = state["game"]["ticksGame"] + ticks
    return result


def danger(thing_id):
    return {
        "id": thing_id,
        "type": "pawn",
        "defName": "Human",
        "label": "raider",
        "faction": "Pirates",
        "dangerReason": "hostileFaction",
        "position": {"x": 80, "z": 90},
        "downed": False,
        "weapon": None,
    }


def project_handoff(objective="Build cooler", *, mode="active"):
    return prepare_handoff({
        "assessment": "Continue work",
        "open_loops": [],
        "resolved_loops": [],
        "resolved_projects": [],
        "projects": [{
            "objective": "Finish infrastructure",
            "rationale": "Needed for survival",
            "status": "active",
            "success_criteria": ["Infrastructure is complete"],
            "blockers": [],
            "tasks": [{
                "key": "build",
                "objective": objective,
                "status": "pending",
                "mode": mode,
                "depends_on": [],
                "blockers": [],
            }],
        }],
    }, None)


def dependent_project_handoff():
    return prepare_handoff({
        "assessment": "Continue work",
        "open_loops": [],
        "resolved_loops": [],
        "resolved_projects": [],
        "projects": [{
            "objective": "Build a research workspace",
            "rationale": "Unlock research work",
            "status": "active",
            "success_criteria": ["Research infrastructure is complete"],
            "blockers": [],
            "tasks": [
                {
                    "key": "bench", "objective": "Build SimpleResearchBench",
                    "status": "active", "mode": "active", "depends_on": [], "blockers": [],
                },
                {
                    "key": "research", "objective": "Begin research",
                    "status": "pending", "mode": "active", "depends_on": ["bench"], "blockers": [],
                },
            ],
        }],
    }, None)


def trigger_context(handoff, no_progress=0):
    task = handoff["projects"][0]["tasks"][0]
    identity = StateIdentity("lineage-a").as_dict()
    metadata = empty_stall_metadata(identity)
    metadata["projectTasks"] = [{
        "id": task["id"],
        "fingerprint": "abc123",
        "repeatedCycles": 3,
        "noRelevantProgressCycles": no_progress,
    }]
    return TriggerContext(handoff=handoff, stall_metadata=metadata)


class TriggerEvaluatorM11Tests(unittest.TestCase):
    def setUp(self):
        self.evaluator = TriggerEvaluator(review_interval_ticks=1_000)
        self.initial = base_state(ticks=1_000)
        self.assertFalse(self.evaluator.evaluate(None, self.initial).should_trigger)

    def evaluate(self, current, context=None):
        return self.evaluator.evaluate(self.initial, current, context)

    def test_identical_snapshot_tick_only_and_paused_states_do_not_trigger(self):
        self.assertFalse(self.evaluate(copy.deepcopy(self.initial)).should_trigger)
        tick_only = advance(self.initial, 60)
        self.assertFalse(self.evaluate(tick_only).should_trigger)
        paused = advance(self.initial, 1_500)
        paused["game"]["paused"] = True
        self.assertFalse(self.evaluate(paused).should_trigger)

    def test_new_additional_and_unchanged_threats_are_deterministic(self):
        one = advance(self.initial)
        one["threats"] = [danger("Raider_1")]
        first = self.evaluate(one)
        self.assertEqual((first.priority, first.kind), ("urgent", "new_threat"))
        unchanged = advance(one)
        self.assertFalse(self.evaluator.evaluate(one, unchanged).should_trigger)
        two = advance(unchanged)
        two["threats"].append(danger("Raider_2"))
        second = self.evaluator.evaluate(unchanged, two)
        self.assertEqual((second.priority, second.kind), ("urgent", "new_threat"))
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_newly_downed_triggers_once(self):
        downed = advance(self.initial)
        downed["colonists"][0]["health"]["downed"] = True
        downed["colonists"][0]["health"]["summary"] = "downed"
        self.assertEqual(self.evaluate(downed).kind, "colonist_downed")
        later = advance(downed)
        self.assertFalse(self.evaluator.evaluate(downed, later).should_trigger)

    def test_risk_escalation_is_urgent_but_stable_mild_risk_does_not_repeat(self):
        mild = advance(self.initial)
        mild["colonists"][0]["health"]["hediffs"] = [
            {"defName": "Heatstroke", "label": "heatstroke", "severity": 0.05}
        ]
        self.assertFalse(self.evaluate(mild).should_trigger)
        urgent = advance(mild)
        urgent["colonists"][0]["health"]["hediffs"][0]["severity"] = 0.42
        decision = self.evaluator.evaluate(mild, urgent)
        self.assertEqual((decision.kind, decision.priority), ("risk_escalation", "urgent"))
        stable = advance(urgent)
        self.assertFalse(self.evaluator.evaluate(urgent, stable).should_trigger)
        worse = advance(stable)
        worse["colonists"][0]["health"]["hediffs"][0]["severity"] = 0.82
        self.assertEqual(self.evaluator.evaluate(stable, worse).kind, "risk_escalation")

    def test_new_important_awareness_event_triggers_once(self):
        self.initial["awareness"] = {"activeAlerts": [], "activeLetters": [], "recentEvents": []}
        current = advance(self.initial)
        current["awareness"]["recentEvents"] = [{
            "id": "message-1", "type": "ThreatBig", "severity": "High",
            "title": "Threat", "text": "Ancient danger", "ticksGame": 1_060,
        }]
        self.assertEqual(self.evaluate(current).kind, "important_awareness")
        later = advance(current)
        self.assertFalse(self.evaluator.evaluate(current, later).should_trigger)

    def test_research_completion_triggers_but_progress_does_not(self):
        progress = advance(self.initial)
        progress["research"]["current"]["progress"] += 20
        self.assertFalse(self.evaluate(progress).should_trigger)
        completed = advance(self.initial)
        completed["research"]["current"] = None
        completed["research"]["completed"] = [{"defName": "Electricity", "label": "Electricity"}]
        decision = self.evaluate(completed)
        self.assertEqual((decision.kind, decision.priority), ("research_complete", "normal"))

    def test_routine_construction_and_crop_growth_do_not_trigger(self):
        before = copy.deepcopy(self.initial)
        before["buildings"].append({
            "id": "Blueprint_Wall_1", "type": "blueprint", "defName": "Blueprint_Wall",
            "position": {"x": 40, "z": 40}, "rotation": "North",
        })
        frame = advance(before)
        frame["buildings"][-1] = {
            "id": "Frame_Wall_1", "type": "frame", "defName": "Frame_Wall",
            "position": {"x": 40, "z": 40}, "rotation": "North",
        }
        self.assertFalse(self.evaluator.evaluate(before, frame).should_trigger)

        growing = advance(self.initial)
        growing["map"]["zones"][0].update({
            "observedCells": 60, "plantedCells": 20, "unsownEligibleCells": 40,
            "growingCells": 20, "harvestableCells": 0,
            "plantingComplete": False, "growingState": "partiallyPlanted",
        })
        self.assertFalse(self.evaluate(growing).should_trigger)

    def test_authoritative_task_completion_can_unlock_dependent_work(self):
        before = copy.deepcopy(self.initial)
        before["buildings"] = [{
            "id": "Blueprint_Bench_1", "type": "blueprint", "defName": "Blueprint_SimpleResearchBench",
            "position": {"x": 55, "z": 52}, "rotation": "North",
        }]
        after = advance(before)
        after["buildings"] = [copy.deepcopy(self.initial["buildings"][0])]
        handoff = dependent_project_handoff()
        decision = self.evaluator.evaluate(before, after, TriggerContext(handoff=handoff))
        self.assertEqual(decision.kind, "project_task_satisfied")
        self.assertIn("unlock", decision.reason)

    def test_meaningful_food_and_resource_deterioration_trigger(self):
        food = advance(self.initial)
        food["resources"]["available"]["food"]["totalNutrition"] = 9.0
        self.assertEqual(self.evaluate(food).kind, "resource_deterioration")

        evaluator = TriggerEvaluator(review_interval_ticks=10_000)
        evaluator.evaluate(None, self.initial)
        materials = advance(self.initial)
        materials["resources"]["available"]["steel"] = 100
        self.assertEqual(evaluator.evaluate(self.initial, materials).kind, "resource_deterioration")

    def test_genuine_project_stall_triggers_and_completion_specificity_is_respected(self):
        handoff = project_handoff("Build cooler")
        context = trigger_context(handoff, no_progress=3)
        current = advance(self.initial)
        self.assertEqual(self.evaluate(current, context).kind, "project_task_stalled")

        growing_handoff = project_handoff("Plant the potato field")
        growing_context = trigger_context(growing_handoff, no_progress=3)
        planted = advance(self.initial)
        planted["map"]["zones"][0].update({
            "observedCells": 60, "plantedCells": 60, "unsownEligibleCells": 0,
            "growingCells": 60, "harvestableCells": 0,
            "plantingComplete": True, "growingState": "planted",
        })
        self.assertNotEqual(self.evaluate(planted, growing_context).kind, "project_task_stalled")

        unrelated = project_handoff("Plant a healroot field")
        unrelated_context = trigger_context(unrelated, no_progress=3)
        specificity_evaluator = TriggerEvaluator(review_interval_ticks=10_000)
        specificity_evaluator.evaluate(None, planted)
        planted_later = advance(planted)
        self.assertEqual(
            specificity_evaluator.evaluate(planted, planted_later, unrelated_context).kind,
            "project_task_stalled",
        )

    def test_completed_shelter_is_not_a_false_stall(self):
        handoff = project_handoff("Complete enclosed roofed starter shelter")
        context = trigger_context(handoff, no_progress=3)
        current = advance(self.initial)
        current["operations"]["beds"][0]["room"] = room(enclosed=True)
        self.assertNotEqual(self.evaluate(current, context).kind, "project_task_stalled")

    def test_blocker_clear_membership_and_idle_actionable_work_trigger_normally(self):
        blocked = copy.deepcopy(self.initial)
        blocked["operations"]["labor"] = {
            "capableIdleColonistCount": 0,
            "pendingWork": {"blueprints": 1, "frames": 0},
            "obviousBlockers": [{"category": "construction", "reason": "No material"}],
        }
        cleared = advance(blocked)
        cleared["operations"]["labor"]["obviousBlockers"] = []
        self.assertEqual(self.evaluator.evaluate(blocked, cleared).kind, "blocker_cleared")

        joined = advance(self.initial)
        joined["colonists"].append(pawn("Pawn_B"))
        self.assertEqual(self.evaluate(joined).kind, "colony_membership_changed")

        idle = advance(self.initial)
        idle["operations"]["labor"] = {"capableIdleColonistCount": 1, "pendingWork": {}, "obviousBlockers": []}
        idle_context = trigger_context(project_handoff("Build cooler"), no_progress=0)
        self.assertEqual(self.evaluate(idle, idle_context).kind, "idle_actionable_work")

    def test_periodic_review_uses_game_ticks_and_requires_acknowledgement(self):
        due = advance(self.initial, 1_000)
        first = self.evaluate(due)
        self.assertEqual(first.kind, "periodic_review")
        later = advance(due, 60)
        self.assertFalse(self.evaluator.evaluate(due, later).should_trigger)
        self.evaluator.acknowledge_review(later["snapshot"]["ticksGame"])
        next_due = advance(later, 1_000)
        self.assertEqual(self.evaluator.evaluate(later, next_due).kind, "periodic_review")

    def test_snapshot_reset_is_safe_but_colony_or_map_change_rebaselines(self):
        reset = advance(self.initial)
        reset["snapshot"]["version"] = 1
        self.assertFalse(self.evaluate(reset).should_trigger)
        other = advance(self.initial)
        other["game"]["colonyLineageId"] = "lineage-b"
        self.assertFalse(self.evaluate(other).should_trigger)
        moved = advance(self.initial)
        moved["game"]["currentMapId"] = "map-8"
        moved["map"]["id"] = "map-8"
        self.assertFalse(self.evaluate(moved).should_trigger)

    def test_fingerprint_history_is_bounded(self):
        evaluator = TriggerEvaluator(max_fingerprints=3)
        evaluator.evaluate(None, self.initial)
        previous = self.initial
        for index in range(5):
            current = advance(previous)
            current["threats"] = list(previous["threats"]) + [danger(f"Raider_{index}")]
            self.assertTrue(evaluator.evaluate(previous, current).should_trigger)
            previous = current
        self.assertEqual(evaluator.fingerprint_count, 3)


class StateObserverM11Tests(unittest.TestCase):
    def test_initial_observation_is_non_triggering_and_uses_get_only(self):
        bridge = FakeBridge([base_state()])
        with tempfile.TemporaryDirectory() as directory:
            observer = StateObserver(bridge, state_root=directory)
            decision = observer.observe_once()
            self.assertIsInstance(decision, DecisionTrigger)
            self.assertFalse(decision.should_trigger)
            self.assertEqual(bridge.calls, [("GET", "/state")])
            self.assertEqual(list(Path(directory).rglob("*")), [])

    def test_observer_modules_do_not_import_openai_or_expose_command_calls(self):
        root = Path(__file__).parent
        source = (root / "state_observer.py").read_text(encoding="utf-8")
        cli = (root / "observe.py").read_text(encoding="utf-8")
        self.assertNotIn("openai", source.lower() + cli.lower())
        self.assertNotIn("send_command", source)
        self.assertNotIn("POST", source)

    def test_observer_exposes_review_acknowledgement_for_future_scheduler(self):
        state = base_state(ticks=1_000)
        due = advance(state, 1_000)
        bridge = FakeBridge([state, due])
        observer = StateObserver(bridge, TriggerEvaluator(review_interval_ticks=1_000))
        self.assertFalse(observer.observe_once().should_trigger)
        self.assertEqual(observer.observe_once().kind, "periodic_review")
        observer.acknowledge_review()

    def test_observation_does_not_mutate_existing_decision_artifacts(self):
        state = base_state()
        handoff = project_handoff()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = StateStore(root, logger=lambda _message: None)
            store.update_current_state(state)
            store.commit_successful_decision(state, handoff)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")}
            observer = StateObserver(FakeBridge([state, advance(state)]), state_root=root)
            observer.observe_once()
            observer.observe_once()
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
