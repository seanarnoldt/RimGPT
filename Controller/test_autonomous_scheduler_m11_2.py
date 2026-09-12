import copy
import unittest

from agent_controller import SYSTEM_INSTRUCTIONS
from autonomous_scheduler import AutonomousScheduler, SchedulerConfig
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE
from decision_outcome import DecisionOutcome
from decision_trigger import DecisionTrigger, TriggerEvaluator
from test_state_diff import base_state, changed_state


def no_trigger(ticks=1000):
    return DecisionTrigger(False, "none", "none", "nothing", None, ticks)


def event(kind="periodic_review", *, priority="normal", fingerprint="fp-1", ticks=1000, evidence=None):
    return DecisionTrigger(True, kind, priority, kind, fingerprint, ticks, evidence)


def outcome(*, success=True, uncertain=False, cost=0.1, ticks=1001):
    return DecisionOutcome(
        success=success,
        termination_reason=None if success else "failed",
        final_snapshot_version=2,
        final_ticks_game=ticks,
        colony_lineage_id="lineage-a",
        current_map_id="map-7",
        handoff=None,
        model_requests=2,
        write_commands=0,
        uncertain_commands_remained=uncertain,
        cycle_cost=cost,
        dry_run=False,
    )


def state(ticks=1000, *, idle=0):
    value = base_state(ticks=ticks)
    value["operations"]["labor"] = {
        "capableIdleColonistCount": idle,
        "capableIdleColonists": [
            {"id": f"Pawn_{index}", "name": f"Pawn {index}"} for index in range(idle)
        ],
        "pendingWork": {},
        "obviousBlockers": [],
    }
    return value


class FakeObserver:
    def __init__(self, observations):
        self.observations = [(item, copy.deepcopy(snapshot)) for item, snapshot in observations]
        self.current = None
        self.acknowledged = []

    def observe_once(self):
        trigger, self.current = self.observations.pop(0)
        self.current = copy.deepcopy(self.current)
        return trigger

    def acknowledge_trigger(self, trigger):
        self.acknowledged.append(trigger)


class FakeController:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def run_once(self, trigger):
        self.calls.append(copy.deepcopy(trigger))
        return self.outcomes.pop(0)


class FakeBridge:
    def __init__(self, *, fail_pause=False):
        self.pause_calls = []
        self.fail_pause = fail_pause

    def send_command_and_wait(self, command):
        self.pause_calls.append(copy.deepcopy(command))
        if self.fail_pause:
            raise RuntimeError("bridge unavailable")
        return {"status": "completed", "success": True}


def scheduler(observations, outcomes=(), **config):
    observer = FakeObserver(observations)
    controller = FakeController(outcomes)
    bridge = FakeBridge()
    value = AutonomousScheduler(
        observer, controller, bridge,
        config=SchedulerConfig(**config),
        logger=lambda _line: None,
    )
    return value, observer, controller, bridge


class AutonomousSchedulerM112Tests(unittest.TestCase):
    def test_no_trigger_makes_no_model_call(self):
        value, _, controller, _ = scheduler([(no_trigger(), state())])
        self.assertEqual(value.step().status, "observed")
        self.assertEqual(controller.calls, [])

    def test_normal_trigger_runs_once_and_periodic_review_is_acknowledged(self):
        observations = [(event(), state()), (no_trigger(1001), state(1001))]
        value, observer, controller, _ = scheduler(observations, [outcome()])
        self.assertEqual(value.step().status, "decided")
        self.assertEqual(len(controller.calls), 1)
        self.assertEqual(observer.acknowledged[0].kind, "periodic_review")

    def test_same_trigger_is_suppressed_but_new_urgent_evidence_bypasses_cooldown(self):
        first = event(fingerprint="same", ticks=1000)
        urgent = event("new_threat", priority="urgent", fingerprint="threat-2", ticks=1002)
        observations = [
            (first, state()), (no_trigger(1001), state(1001)),
            (first, state(1002)),
            (urgent, state(1002)), (no_trigger(1003), state(1003)),
        ]
        value, _, controller, _ = scheduler(observations, [outcome(), outcome(ticks=1003)])
        value.step()
        self.assertEqual(value.step().status, "observed")
        self.assertEqual(value.step().status, "recovery_pending")
        self.assertEqual(len(controller.calls), 2)

    def test_normal_trigger_waits_for_game_tick_cooldown(self):
        observations = [
            (event(fingerprint="first", ticks=1000), state()), (no_trigger(1001), state(1001)),
            (event(fingerprint="second", ticks=1100), state(1100)),
            (no_trigger(4000), state(4000)), (no_trigger(4001), state(4001)),
        ]
        value, _, controller, _ = scheduler(observations, [outcome(), outcome(ticks=4001)])
        self.assertEqual(value.step().status, "decided")
        self.assertEqual(value.step().status, "cooldown")
        self.assertEqual(value.step().status, "decided")
        self.assertEqual(len(controller.calls), 2)

    def test_worsening_urgent_condition_retriggers_with_new_fingerprint(self):
        observations = [
            (event("risk_escalation", priority="urgent", fingerprint="risk-1"), state()),
            (no_trigger(1001), state(1001)),
            (event("risk_escalation", priority="urgent", fingerprint="risk-2", ticks=1002), state(1002)),
            (no_trigger(1003), state(1003)),
        ]
        value, _, controller, _ = scheduler(observations, [outcome(), outcome(ticks=1003)])
        value.step()
        value.step()
        self.assertEqual(len(controller.calls), 2)

    def test_same_raw_trigger_fingerprint_is_scoped_to_colony(self):
        first_state = state()
        second_state = state(4000)
        second_state["game"]["colonyLineageId"] = "lineage-b"
        observations = [
            (event(fingerprint="same"), first_state), (no_trigger(1001), state(1001)),
            (event(fingerprint="same", ticks=4000), second_state),
            (no_trigger(4001), copy.deepcopy(second_state)),
        ]
        value, _, controller, _ = scheduler(observations, [outcome(), outcome(ticks=4001)])
        value.step()
        value.step()
        self.assertEqual(len(controller.calls), 2)
        self.assertNotEqual(controller.calls[0]["fingerprint"], controller.calls[1]["fingerprint"])

    def test_productive_idleness_is_persistent_and_independent_of_projects(self):
        evaluator = TriggerEvaluator(review_interval_ticks=100_000, idle_persistence_ticks=100)
        first = state(1000, idle=1)
        self.assertFalse(evaluator.evaluate(None, first).should_trigger)
        momentary = state(1050, idle=1)
        self.assertFalse(evaluator.evaluate(first, momentary).should_trigger)
        productive = state(1100, idle=1)
        decision = evaluator.evaluate(momentary, productive)
        self.assertEqual(decision.kind, "productive_idleness")
        self.assertEqual(decision.evidence["capableIdlePawnIds"], ["Pawn_0"])

        cleared = state(1160, idle=0)
        self.assertFalse(evaluator.evaluate(productive, cleared).should_trigger)

    def test_progress_resets_problem_attempts(self):
        before, after = state(1000, idle=2), state(1001, idle=1)
        trigger = event("productive_idleness", fingerprint="idle", evidence={"capableIdleColonists": 2})
        value, _, _, _ = scheduler([(trigger, before), (no_trigger(1001), after)], [outcome()])
        result = value.step()
        self.assertTrue(result.meaningful_progress)
        self.assertEqual(value._problem_attempts, {})

    def test_unchanged_blocker_increments_attempt_and_requests_recovery(self):
        trigger = event("construction_blocked", fingerprint="blocked")
        value, _, controller, _ = scheduler(
            [(trigger, state()), (no_trigger(1001), state(1001))], [outcome()]
        )
        self.assertEqual(value.step().status, "recovery_pending")
        self.assertEqual(next(iter(value._problem_attempts.values())), 1)
        self.assertEqual(controller.calls[0]["recoveryAttempt"], 0)

    def test_three_completed_no_progress_attempts_receive_recovery_context_then_pause(self):
        trigger = event("productive_idleness", fingerprint="idle")
        trigger = DecisionTrigger(
            True, trigger.kind, trigger.priority, trigger.reason, trigger.fingerprint,
            trigger.ticks_game, {"capableIdleColonists": 1, "capableIdlePawnIds": ["Pawn_0"]},
        )
        observations = []
        for tick in range(1000, 1006):
            observations.append((no_trigger(tick) if tick % 2 else trigger, state(tick, idle=1)))
        observations[0] = (trigger, state(1000, idle=1))
        value, _, controller, bridge = scheduler(
            observations, [outcome(ticks=1001), outcome(ticks=1003), outcome(ticks=1005)]
        )
        self.assertEqual(value.step().status, "recovery_pending")
        self.assertEqual(value.step().status, "recovery_pending")
        self.assertEqual(value.step().status, "halted")
        self.assertEqual([call["recoveryAttempt"] for call in controller.calls], [0, 2, 3])
        self.assertEqual(controller.calls[0]["capableIdlePawnIds"], ["Pawn_0"])
        self.assertIn("prior completed decision", controller.calls[1]["recoveryGuidance"])
        self.assertEqual(bridge.pause_calls, [{"command": "setSpeed", "speed": 0}])
        self.assertEqual(value.halt_reason, "repeated_no_progress")
        self.assertEqual(value.step().status, "halted")
        self.assertEqual(len(controller.calls), 3)

    def test_repeated_failed_outcomes_halt_and_pause_once(self):
        trigger = event("construction_blocked", fingerprint="blocked")
        observations = [
            (trigger, state()), (no_trigger(1001), state(1001)),
            (no_trigger(1002), state(1002)), (no_trigger(1003), state(1003)),
        ]
        value, _, controller, bridge = scheduler(
            observations, [outcome(success=False), outcome(success=False)],
        )
        self.assertEqual(value.step().status, "decision_failed")
        self.assertEqual(value.step().status, "halted")
        self.assertEqual(value.halt_reason, "repeated_failed_outcomes")
        self.assertEqual(len(controller.calls), 2)
        self.assertEqual(len(bridge.pause_calls), 1)

    def test_strategic_doctrine_and_existing_request_guards_remain_explicit(self):
        for phrase in (
            "Survival comes first, but survival alone is not success",
            "Persistent capable idleness",
            "Research is a strong low-wealth fallback",
            "avoid unnecessary wealth accumulation",
            "approximate runway",
            "legitimate victory path",
        ):
            self.assertIn(phrase, SYSTEM_INSTRUCTIONS)
        self.assertEqual(DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, 30_000)
        self.assertEqual(DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE, 8)

    def test_repeated_uncertain_cycles_halt(self):
        trigger = event("construction_blocked", fingerprint="blocked")
        observations = [
            (trigger, state()), (no_trigger(1001), state(1001)),
            (no_trigger(1002), state(1002)), (no_trigger(1003), state(1003)),
        ]
        value, _, _, bridge = scheduler(
            observations,
            [outcome(success=False, uncertain=True), outcome(success=False, uncertain=True)],
        )
        value.step()
        self.assertEqual(value.step().status, "halted")
        self.assertEqual(value.halt_reason, "repeated_uncertain_commands")
        self.assertEqual(len(bridge.pause_calls), 1)

    def test_session_decision_spend_and_followup_caps(self):
        periodic = event()
        observations = [
            (periodic, state()), (no_trigger(1001), state(1001)),
            (event(fingerprint="fp-2", ticks=4000), state(4000)),
        ]
        value, _, controller, bridge = scheduler(
            observations, [outcome()], max_decisions=1,
        )
        self.assertEqual(value.step().status, "halted")
        self.assertEqual(value.halt_reason, "session_decision_limit")
        self.assertEqual(len(controller.calls), 1)
        self.assertEqual(len(bridge.pause_calls), 1)

        trigger = event("productive_idleness", fingerprint="idle")
        spend, _, _, _ = scheduler(
            [(trigger, state(idle=1)), (no_trigger(1001), state(1001, idle=1))],
            [outcome(cost=0.5)], max_session_spend=0.5,
        )
        self.assertEqual(spend.step().status, "halted")
        self.assertEqual(spend.halt_reason, "session_spend_limit")

        follow, _, follow_controller, _ = scheduler(
            [
                (trigger, state(idle=1)), (no_trigger(1001), state(1001, idle=1)),
                (no_trigger(1002), state(1002, idle=1)), (no_trigger(1003), state(1003, idle=1)),
                (no_trigger(1004), state(1004, idle=1)),
            ],
            [outcome(), outcome(ticks=1003)], max_immediate_followups=1,
        )
        follow.step()
        follow.step()
        self.assertEqual(follow.step().status, "halted")
        self.assertEqual(follow.halt_reason, "immediate_followup_limit")
        self.assertEqual(len(follow_controller.calls), 2)

    def test_unknown_cost_is_not_reported_as_dollar_total(self):
        logs = []
        trigger = event("productive_idleness", fingerprint="idle")
        observer = FakeObserver([(trigger, state(idle=1)), (no_trigger(1001), state(1001, idle=1))])
        controller = FakeController([outcome(cost=None)])
        bridge = FakeBridge(fail_pause=True)
        value = AutonomousScheduler(
            observer, controller, bridge,
            config=SchedulerConfig(max_problem_attempts=1), logger=logs.append,
        )
        self.assertEqual(value.step().status, "halted")
        self.assertIn("sessionCost=unavailable", logs)
        self.assertEqual(len(bridge.pause_calls), 1)


if __name__ == "__main__":
    unittest.main()
