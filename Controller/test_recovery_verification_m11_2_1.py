import copy
import unittest
from unittest.mock import patch

from autonomous import main
from autonomous_scheduler import AutonomousScheduler, SchedulerConfig, meaningful_progress
from context_telemetry import Pricing
from decision_trigger import DecisionTrigger
from test_autonomous_scheduler_m11_2 import (
    FakeBridge,
    FakeController,
    FakeObserver,
    event,
    no_trigger,
    outcome,
    state,
)


def idle_trigger(priority="normal"):
    return DecisionTrigger(
        True,
        "productive_idleness",
        priority,
        "persistent idle",
        "idle-fingerprint",
        1000,
        {"capableIdleColonists": 1, "capableIdlePawnIds": ["Pawn_0"]},
    )


def make_scheduler(observations, outcomes, **overrides):
    observer = FakeObserver(observations)
    controller = FakeController(outcomes)
    bridge = FakeBridge()
    config = SchedulerConfig(
        verification_grace_ticks=overrides.pop("verification_grace_ticks", 100),
        urgent_verification_grace_ticks=overrides.pop("urgent_verification_grace_ticks", 10),
        **overrides,
    )
    return AutonomousScheduler(
        observer, controller, bridge, config=config, logger=lambda _line: None
    ), controller, bridge


class RecoveryVerificationM1121Tests(unittest.TestCase):
    def test_unrelated_research_advance_does_not_resolve_same_idle_pawn(self):
        before = state(1000, idle=1)
        after = state(1100, idle=1)
        after["research"]["current"]["progress"] += 100
        self.assertFalse(meaningful_progress(idle_trigger(), before, after))

    def test_unrelated_crop_growth_does_not_resolve_same_idle_pawn(self):
        before = state(1000, idle=1)
        after = copy.deepcopy(before)
        after["snapshot"]["ticksGame"] = 1100
        after["game"]["ticksGame"] = 1100
        zone = after["map"]["zones"][0]
        zone["plantedCells"] = int(zone.get("plantedCells") or 0) + 1
        zone["growingState"] = "partiallyPlanted"
        self.assertFalse(meaningful_progress(idle_trigger(), before, after))

    def test_affected_idle_pawn_becoming_productive_resolves_problem(self):
        self.assertTrue(meaningful_progress(idle_trigger(), state(idle=1), state(1010, idle=0)))

        replacement = state(1010, idle=1)
        replacement["operations"]["labor"]["capableIdleColonists"][0]["id"] = "Pawn_1"
        self.assertTrue(meaningful_progress(idle_trigger(), state(idle=1), replacement))

    def test_successful_decision_waits_through_grace_then_runs_attempt_two(self):
        trigger = idle_trigger()
        observations = [
            (trigger, state(1000, idle=1)),
            (no_trigger(1001), state(1001, idle=1)),
            (no_trigger(1050), state(1050, idle=1)),
            (no_trigger(1099), state(1099, idle=1)),
            (no_trigger(1101), state(1101, idle=1)),
            (no_trigger(1102), state(1102, idle=1)),
        ]
        scheduler, controller, _ = make_scheduler(
            observations, [outcome(ticks=1001), outcome(ticks=1102)]
        )
        self.assertEqual(scheduler.step().status, "verification_pending")
        self.assertEqual(scheduler.step().status, "verification_wait")
        self.assertEqual(scheduler.step().status, "verification_wait")
        self.assertEqual(len(controller.calls), 1)
        self.assertEqual(scheduler.step().status, "verification_pending")
        self.assertEqual(len(controller.calls), 2)
        self.assertEqual(controller.calls[1]["recoveryAttempt"], 2)

    def test_progress_during_grace_cancels_retry(self):
        trigger = idle_trigger()
        scheduler, controller, _ = make_scheduler(
            [
                (trigger, state(1000, idle=1)),
                (no_trigger(1001), state(1001, idle=1)),
                (no_trigger(1050), state(1050, idle=0)),
            ],
            [outcome(ticks=1001)],
        )
        self.assertEqual(scheduler.step().status, "verification_pending")
        self.assertEqual(scheduler.step().status, "verification_resolved")
        self.assertEqual(len(controller.calls), 1)

    def test_worsening_urgent_event_interrupts_verification_wait(self):
        idle = idle_trigger()
        urgent = event("new_threat", priority="urgent", fingerprint="threat", ticks=1050)
        threatened = state(1050, idle=1)
        threatened["threats"] = [{"id": "Raider_1", "health": {"downed": False}}]
        observations = [
            (idle, state(1000, idle=1)),
            (no_trigger(1001), state(1001, idle=1)),
            (urgent, threatened),
            (no_trigger(1051), state(1051, idle=1)),
        ]
        scheduler, controller, _ = make_scheduler(
            observations, [outcome(ticks=1001), outcome(ticks=1051)]
        )
        scheduler.step()
        result = scheduler.step()
        self.assertEqual(result.trigger.kind, "new_threat")
        self.assertEqual(len(controller.calls), 2)

    def test_paused_ticks_do_not_burn_attempts_or_call_model_again(self):
        trigger = idle_trigger()
        paused = state(1001, idle=1)
        paused["game"]["paused"] = True
        observations = [
            (trigger, state(1000, idle=1)),
            (no_trigger(1001), paused),
            (no_trigger(1001), paused),
            (no_trigger(1001), paused),
        ]
        scheduler, controller, bridge = make_scheduler(observations, [outcome(ticks=1001)])
        self.assertEqual(scheduler.step().status, "verification_pending")
        self.assertEqual(scheduler.step().status, "verification_wait")
        self.assertEqual(scheduler.step().status, "verification_wait")
        self.assertEqual(len(controller.calls), 1)
        self.assertEqual(next(iter(scheduler._problem_attempts.values())), 1)
        self.assertEqual(bridge.pause_calls, [])

    def test_pricing_is_required_unless_unpriced_session_is_explicit(self):
        observer = FakeObserver([(no_trigger(), state())])
        controller = FakeController([], pricing_available=False)
        with self.assertRaisesRegex(ValueError, "requires calculable pricing"):
            AutonomousScheduler(observer, controller, FakeBridge(), config=SchedulerConfig())

        allowed = AutonomousScheduler(
            observer,
            controller,
            FakeBridge(),
            config=SchedulerConfig(allow_unpriced_session=True),
        )
        self.assertFalse(allowed.session_cost_calculable)

    def test_cli_refuses_unpriced_paid_startup_before_controller_creation(self):
        with patch("autonomous.load_dotenv"), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True
        ), patch("sys.argv", ["autonomous.py"]), patch(
            "autonomous.Pricing.from_environment", return_value=Pricing()
        ), patch("autonomous.AgentController") as controller:
            self.assertEqual(main(), 2)
        controller.assert_not_called()

    def test_unpriced_opt_in_still_has_hard_session_request_cap_and_one_pause(self):
        trigger = idle_trigger()
        observer = FakeObserver([
            (trigger, state(1000, idle=1)),
            (no_trigger(1001), state(1001, idle=1)),
        ])
        controller = FakeController([outcome(cost=None, ticks=1001)], pricing_available=False)
        bridge = FakeBridge()
        scheduler = AutonomousScheduler(
            observer,
            controller,
            bridge,
            config=SchedulerConfig(
                allow_unpriced_session=True,
                max_session_model_requests=2,
                verification_grace_ticks=100,
            ),
            logger=lambda _line: None,
        )
        result = scheduler.step()
        self.assertEqual(result.status, "halted")
        self.assertEqual(scheduler.halt_reason, "session_model_request_limit")
        self.assertEqual(bridge.pause_calls, [{"command": "setSpeed", "speed": 0}])
        self.assertEqual(scheduler.step().status, "halted")
        self.assertEqual(len(bridge.pause_calls), 1)


if __name__ == "__main__":
    unittest.main()
