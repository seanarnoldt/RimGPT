import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController
from context_telemetry import Pricing
from state_store import StateStore, snapshot_version
from test_state_diff import base_state


def response(response_id, output, *, status="completed", output_text=""):
    return SimpleNamespace(id=response_id, output=output, status=status, output_text=output_text, usage=None)


def function_call(call_id="call-1"):
    return SimpleNamespace(type="function_call", call_id=call_id, name="get_colony_state", arguments='{"section":"resources"}')


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeBridge:
    def __init__(self, states):
        self.states = [copy.deepcopy(state) for state in states]
        self.get_calls = 0

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        index = min(self.get_calls, len(self.states) - 1)
        self.get_calls += 1
        return copy.deepcopy(self.states[index])


class WriteBridge(FakeBridge):
    def submit_command(self, command):
        return {"commandId": "cmd-1", "startedAt": time.monotonic(), "submitElapsedSeconds": 0.0}

    def wait_for_commands(self, submissions):
        return {"cmd-1": {"commandId": "cmd-1", "status": "completed", "success": True, "elapsedSeconds": 0.01}}


class CompactModelCycleTests(unittest.TestCase):
    def make_store(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = StateStore(Path(temporary.name), logger=lambda _: None)
        baseline = base_state(version=120)
        store.update_current_state(baseline)
        store.set_decision_baseline(baseline)
        store.apply_memory_update({"currentGoals": ["MEMORY_CYCLE_SENTINEL"]})
        return store, baseline

    def make_controller(self, bridge, fake_responses, store, **overrides):
        return AgentController(
            bridge=bridge,
            model="test-model",
            client=SimpleNamespace(responses=fake_responses),
            state_store=store,
            pricing=Pricing(),
            **overrides,
        )

    def test_complete_mocked_cycle_uses_compact_context_and_advances_final_baseline(self):
        store, _ = self.make_store()
        current = base_state(version=121, ticks=1060)
        current["resources"]["available"]["wood"] = 222
        current["operations"]["rawMarker"] = "FULL_STATE_MUST_NOT_BE_SENT"
        final = base_state(version=122, ticks=1120)
        final["resources"]["available"]["wood"] = 333
        fake = FakeResponses([
            response("r1", [function_call()]),
            response("r2", [], output_text="Colony remains stable."),
        ])
        controller = self.make_controller(FakeBridge([current, current, final]), fake, store)

        controller.run_once({"type": "manualTest"})

        initial_text = fake.calls[0]["input"][0]["content"][0]["text"]
        self.assertIn("strategicMemory", initial_text)
        self.assertIn("MEMORY_CYCLE_SENTINEL", initial_text)
        self.assertIn("currentSummary", initial_text)
        self.assertIn("changesSinceLastDecision", initial_text)
        self.assertIn('"type":"manualTest"', initial_text)
        self.assertNotIn("FULL_STATE_MUST_NOT_BE_SENT", initial_text)
        self.assertNotIn('"operations"', initial_text)
        tool_outputs = [item for item in fake.calls[1]["input"] if item.get("type") == "function_call_output"]
        self.assertIn('"section":"resources"', tool_outputs[0]["output"])
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 122)
        self.assertIsNone(controller.termination_reason)

    def test_token_limit_abort_preserves_baseline(self):
        store, _ = self.make_store()
        current = base_state(version=121)
        fake = FakeResponses([response("unused", [])])
        controller = self.make_controller(FakeBridge([current]), fake, store, max_input_tokens_per_request=1)
        controller.run_once()
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 120)
        self.assertEqual(fake.calls, [])

    def test_model_request_limit_abort_preserves_baseline(self):
        store, _ = self.make_store()
        current = base_state(version=121)
        fake = FakeResponses([response("r1", [function_call()])])
        controller = self.make_controller(FakeBridge([current, current]), fake, store, max_model_requests_per_cycle=1)
        controller.run_once()
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 120)
        self.assertIn("Maximum model requests", controller.termination_reason or "")

    def test_api_error_preserves_baseline(self):
        store, _ = self.make_store()
        fake = FakeResponses([RuntimeError("mock API failure")])
        controller = self.make_controller(FakeBridge([base_state(version=121)]), fake, store)
        controller.run_once()
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 120)
        self.assertIn("mock API failure", controller.termination_reason or "")

    def test_incomplete_response_preserves_baseline(self):
        store, _ = self.make_store()
        fake = FakeResponses([response("r1", [], status="incomplete")])
        controller = self.make_controller(FakeBridge([base_state(version=121)]), fake, store)
        controller.run_once()
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 120)

    def test_command_execution_alone_does_not_advance_baseline(self):
        store, _ = self.make_store()
        controller = self.make_controller(WriteBridge([base_state(version=121)]), FakeResponses([]), store)
        call = SimpleNamespace(type="function_call", call_id="write-1", name="set_speed", arguments='{"speed":1}')
        controller._execute_tool_call_batch([call])
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 120)


if __name__ == "__main__":
    unittest.main()
