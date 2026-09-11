import copy
import json
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_controller import AgentController
from context_telemetry import Pricing
from decision_handoff import prepare_handoff
from decision_outcome import DecisionOutcome
from rimgpt import main
from state_store import StateStore, snapshot_version
from test_state_diff import base_state


def call(name, arguments, call_id):
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=json.dumps(arguments),
        call_id=call_id,
    )


def finish_call(assessment="Decision complete"):
    return call(
        "finish_decision",
        {"assessment": assessment, "open_loops": [], "resolved_loops": []},
        "finish",
    )


def response(response_id, output, *, usage=None):
    return SimpleNamespace(
        id=response_id,
        output=output,
        status="completed",
        output_text="",
        usage=usage,
    )


class FakeResponses:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class CycleBridge:
    def __init__(self, initial, final=None, *, command_status="completed"):
        self.state = copy.deepcopy(initial)
        self.final = copy.deepcopy(final if final is not None else initial)
        self.command_status = command_status
        self.submitted = []
        self.get_state_calls = 0

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        result = copy.deepcopy(self.state)
        self.get_state_calls += 1
        if self.get_state_calls == 1:
            self.state = copy.deepcopy(self.final)
        return result

    def submit_command(self, command):
        self.submitted.append(copy.deepcopy(command))
        return {
            "commandId": "cmd-1",
            "startedAt": time.monotonic(),
            "submitElapsedSeconds": 0.0,
        }

    def wait_for_commands(self, submissions):
        return {
            "cmd-1": {
                "commandId": "cmd-1",
                "status": self.command_status,
                "success": True if self.command_status == "completed" else None,
                "elapsedSeconds": 0.01,
            }
        }

    def reconcile_command(self, command_id, *, started_at=None):
        return {
            "commandId": command_id,
            "status": self.command_status,
            "success": True if self.command_status == "completed" else None,
            "elapsedSeconds": 0.02,
        }

    def wait_for_state_after(self, after_version, timeout_ms=3000):
        self.state = copy.deepcopy(self.final)
        return copy.deepcopy(self.state)


class FailingBridge(CycleBridge):
    def health(self):
        raise RuntimeError("bridge unavailable")


class FinalStateFailingBridge(CycleBridge):
    def get_state(self):
        if self.get_state_calls:
            raise RuntimeError("final state unavailable")
        return super().get_state()


class DecisionOutcomeTests(unittest.TestCase):
    def make_store(self, state=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = StateStore(Path(temporary.name), logger=lambda _: None)
        if state is not None:
            store.update_current_state(state)
        return store

    def controller(self, bridge, responses, store, **overrides):
        return AgentController(
            bridge=bridge,
            model="test-model",
            client=SimpleNamespace(responses=responses),
            state_store=store,
            pricing=overrides.pop("pricing", Pricing()),
            **overrides,
        )

    def test_success_returns_snapshot_handoff_and_commits(self):
        initial = base_state(version=40, ticks=1000)
        final = base_state(version=41, ticks=1060)
        store = self.make_store()
        responses = FakeResponses([response("r1", [finish_call("Continue research")])])
        controller = self.controller(CycleBridge(initial, final), responses, store)

        outcome = controller.run_once({"type": "m10h4Mock"})

        self.assertIsInstance(outcome, DecisionOutcome)
        self.assertTrue(outcome.success)
        self.assertIsNone(outcome.termination_reason)
        self.assertEqual(outcome.final_snapshot_version, 41)
        self.assertEqual(outcome.final_ticks_game, 1060)
        self.assertEqual(outcome.colony_lineage_id, "lineage-a")
        self.assertEqual(outcome.current_map_id, "map-7")
        self.assertEqual(outcome.handoff["assessment"], "Continue research")
        self.assertEqual(outcome.model_requests, 1)
        self.assertEqual(outcome.write_commands, 0)
        self.assertFalse(outcome.uncertain_commands_remained)
        self.assertFalse(outcome.dry_run)
        self.assertEqual(store.get_decision_handoff(), outcome.handoff)
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 41)
        with self.assertRaises(FrozenInstanceError):
            outcome.success = False

    def test_model_failure_returns_failure_without_advancing_prior_decision(self):
        baseline = base_state(version=30)
        store = self.make_store(baseline)
        prior = prepare_handoff(
            {"assessment": "Keep", "open_loops": [], "resolved_loops": []},
            None,
        )
        store.commit_successful_decision(baseline, prior)
        controller = self.controller(
            CycleBridge(base_state(version=31)),
            FakeResponses([RuntimeError("mock model failure")]),
            store,
        )

        outcome = controller.run_once()

        self.assertFalse(outcome.success)
        self.assertIn("mock model failure", outcome.termination_reason or "")
        self.assertEqual(outcome.final_snapshot_version, 31)
        self.assertEqual(outcome.model_requests, 1)
        self.assertEqual(store.get_decision_handoff(), prior)
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 30)

    def test_bridge_failure_returns_structured_failure(self):
        state = base_state(version=10)
        controller = self.controller(FailingBridge(state), FakeResponses([]), self.make_store())

        outcome = controller.run_once()

        self.assertFalse(outcome.success)
        self.assertIn("bridge unavailable", outcome.termination_reason or "")
        self.assertIsNone(outcome.final_snapshot_version)
        self.assertEqual(outcome.model_requests, 0)

    def test_tool_round_limit_returns_failure(self):
        state = base_state(version=50)
        read = call("get_colony_state", {"section": "resources"}, "read")
        responses = FakeResponses([response("r1", [read]), response("r2", [read])])
        controller = self.controller(CycleBridge(state), responses, self.make_store(), max_tool_rounds=1)

        outcome = controller.run_once()

        self.assertFalse(outcome.success)
        self.assertIn("max tool-call rounds", outcome.termination_reason or "")
        self.assertEqual(outcome.model_requests, 2)

    def test_context_and_final_state_failures_return_outcomes(self):
        state = base_state(version=55)
        context_controller = self.controller(
            CycleBridge(state), FakeResponses([]), self.make_store()
        )
        context_controller.context_builder = SimpleNamespace(
            build=Mock(side_effect=RuntimeError("context unavailable"))
        )

        context_outcome = context_controller.run_once()

        self.assertFalse(context_outcome.success)
        self.assertIn("context unavailable", context_outcome.termination_reason or "")
        self.assertEqual(context_outcome.final_snapshot_version, 55)

        final_store = self.make_store()
        final_controller = self.controller(
            FinalStateFailingBridge(state),
            FakeResponses([response("r1", [finish_call()])]),
            final_store,
        )

        final_outcome = final_controller.run_once()

        self.assertFalse(final_outcome.success)
        self.assertIn("final state unavailable", final_outcome.termination_reason or "")
        self.assertIsNone(final_store.get_decision_baseline())
        self.assertIsNone(final_store.get_decision_handoff())

    def test_uncertain_command_returns_failure_and_activity_counts(self):
        initial = base_state(version=60)
        final = base_state(version=61)
        responses = FakeResponses([
            response("r1", [call("set_speed", {"speed": 1}, "write")]),
            response("r2", []),
        ])
        bridge = CycleBridge(initial, final, command_status="queued")
        store = self.make_store()
        controller = self.controller(bridge, responses, store)

        outcome = controller.run_once()

        self.assertFalse(outcome.success)
        self.assertTrue(outcome.uncertain_commands_remained)
        self.assertIn("remained uncertain", outcome.termination_reason or "")
        self.assertEqual(outcome.model_requests, 2)
        self.assertEqual(outcome.write_commands, 1)
        self.assertIsNone(store.get_decision_baseline())
        self.assertIsNone(store.get_decision_handoff())

    def test_successful_write_reports_counts_and_calculated_cost(self):
        initial = base_state(version=70)
        final = base_state(version=71)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        responses = FakeResponses([
            response("r1", [call("set_speed", {"speed": 1}, "write")], usage=usage),
            response("r2", [finish_call()], usage=usage),
        ])
        controller = self.controller(
            CycleBridge(initial, final),
            responses,
            self.make_store(),
            pricing=Pricing(input_per_million=10.0, output_per_million=20.0),
        )

        outcome = controller.run_once()

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.model_requests, 2)
        self.assertEqual(outcome.write_commands, 1)
        self.assertAlmostEqual(outcome.cycle_cost or 0.0, 0.0028)

    def test_dry_run_returns_success_without_persistence_or_writes(self):
        state = base_state(version=80)
        store = self.make_store()
        bridge = CycleBridge(state)
        controller = self.controller(
            bridge,
            FakeResponses([response("r1", [finish_call("Dry proposal")])]),
            store,
            dry_run=True,
        )

        outcome = controller.run_once()

        self.assertTrue(outcome.success)
        self.assertTrue(outcome.dry_run)
        self.assertEqual(outcome.handoff["assessment"], "Dry proposal")
        self.assertEqual(bridge.submitted, [])
        self.assertIsNone(store.get_decision_baseline())
        self.assertIsNone(store.get_decision_handoff())

    def test_cli_remains_one_shot_and_may_ignore_outcome(self):
        run_once = Mock(return_value=DecisionOutcome(
            success=True,
            termination_reason=None,
            final_snapshot_version=1,
            final_ticks_game=10,
            colony_lineage_id="lineage-a",
            current_map_id="map-7",
            handoff=None,
            model_requests=1,
            write_commands=0,
            uncertain_commands_remained=False,
            cycle_cost=None,
            dry_run=False,
        ))
        fake_controller = SimpleNamespace(run_once=run_once)
        with patch("rimgpt.load_dotenv"), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True
        ), patch("sys.argv", ["rimgpt.py"]), patch(
            "rimgpt.AgentController", return_value=fake_controller
        ):
            result = main()

        self.assertEqual(result, 0)
        run_once.assert_called_once_with({"type": "manualCycle"})


if __name__ == "__main__":
    unittest.main()
