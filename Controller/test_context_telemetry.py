import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from context_telemetry import (
    ModelContextLimitError,
    ModelRequestLimitError,
    Pricing,
    extract_usage,
    measure_context,
)
from rimgpt import main, parse_args, resolve_cycle_count
from tools import TOOLS


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class ContextTelemetryTests(unittest.TestCase):
    def make_controller(self, *, max_input=30_000, max_requests=8, response=None):
        controller = object.__new__(AgentController)
        controller.model = "test-model"
        controller.client = SimpleNamespace(responses=FakeResponses(response or SimpleNamespace(usage=None)))
        controller.max_input_tokens_per_request = max_input
        controller.max_model_requests_per_cycle = max_requests
        controller.pricing = Pricing()
        controller.model_request_count = 0
        controller.accumulated_tool_result_chars = 0
        controller.carried_context_chars = 0
        controller.cycle_cost = 0.0
        controller.session_cost = 0.0
        return controller

    def test_estimator_accepts_ordinary_mocked_request(self):
        controller = self.make_controller()
        response = controller._request_model(
            [{"role": "user", "content": [{"type": "input_text", "text": "small state"}]}],
            state={"game": {"loaded": True}},
        )

        self.assertEqual(response.usage, None)
        self.assertEqual(len(controller.client.responses.calls), 1)
        self.assertEqual(controller.model_request_count, 1)

    def test_oversized_request_is_blocked_before_client_invocation(self):
        controller = self.make_controller(max_input=30_000)
        huge_state = {"payload": "x" * 100_000}

        with self.assertRaises(ModelContextLimitError):
            controller._request_model(
                [{"role": "user", "content": [{"type": "input_text", "text": huge_state["payload"]}]}],
                state=huge_state,
            )

        self.assertEqual(controller.client.responses.calls, [])
        self.assertEqual(controller.model_request_count, 0)

    def test_context_breakdown_identifies_state_operations_and_tool_versions(self):
        state = {
            "colonists": [{"id": "Pawn_1", "name": "A"}],
            "map": {"width": 250, "height": 250},
            "operations": {"worktables": [{"id": "Bench_1"}]},
        }
        breakdown = measure_context(
            instructions=SYSTEM_INSTRUCTIONS,
            tools=TOOLS,
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": "state"}]}],
            state=state,
            accumulated_tool_result_chars=123,
            carried_context_chars=456,
        )

        self.assertGreater(breakdown.tool_schema_chars, 0)
        self.assertGreater(breakdown.v2_tool_schema_chars, 0)
        self.assertGreater(breakdown.v3_tool_schema_chars, 0)
        self.assertGreater(breakdown.operations_chars, 0)
        self.assertGreater(breakdown.colonists_chars, 0)
        self.assertGreater(breakdown.map_overview_chars, 0)
        self.assertEqual(breakdown.accumulated_tool_result_chars, 123)
        self.assertGreater(breakdown.estimated_input_tokens, 0)

    def test_ninth_request_is_blocked_at_cycle_limit_of_eight(self):
        controller = self.make_controller(max_requests=8)
        payload = [{"role": "user", "content": [{"type": "input_text", "text": "x"}]}]
        for _ in range(8):
            controller._request_model(payload, state={})

        with self.assertRaises(ModelRequestLimitError):
            controller._request_model(payload, state={})

        self.assertEqual(len(controller.client.responses.calls), 8)

    def test_usage_telemetry_reads_normal_usage_and_costs(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=200,
                input_tokens_details=SimpleNamespace(cached_tokens=600),
            )
        )
        usage = extract_usage(response, Pricing(10.0, 2.0, 20.0))

        self.assertEqual(usage.input_tokens, 1000)
        self.assertEqual(usage.cached_input_tokens, 600)
        self.assertEqual(usage.uncached_input_tokens, 400)
        self.assertEqual(usage.output_tokens, 200)
        self.assertAlmostEqual(usage.estimated_cost or 0.0, 0.0092)

    def test_usage_telemetry_handles_missing_cached_tokens(self):
        response = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
        usage = extract_usage(response, Pricing(10.0, 2.0, 20.0))

        self.assertIsNone(usage.cached_input_tokens)
        self.assertEqual(usage.uncached_input_tokens, 1000)
        self.assertAlmostEqual(usage.estimated_cost or 0.0, 0.014)

    def test_test_mode_cycle_limits(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.argv", ["rimgpt.py", "--test"]):
                self.assertEqual(resolve_cycle_count(parse_args()), 1)
            with patch("sys.argv", ["rimgpt.py", "--test", "--max-cycles", "5"]):
                self.assertEqual(resolve_cycle_count(parse_args()), 5)
            with patch("sys.argv", ["rimgpt.py", "--test", "--max-cycles", "6"]):
                with self.assertRaises(ValueError):
                    resolve_cycle_count(parse_args())

    def test_invalid_test_cycle_values_are_rejected_before_api_key_or_model_setup(self):
        with patch("rimgpt.load_dotenv"):
            with patch.dict(os.environ, {}, clear=True):
                with patch("sys.argv", ["rimgpt.py", "--test", "--max-cycles", "6"]):
                    self.assertEqual(main(), 2)
                with patch("sys.argv", ["rimgpt.py", "--test", "--max-cycles", "0"]):
                    self.assertEqual(main(), 2)
            with patch("sys.argv", ["rimgpt.py", "--test", "--max-cycles", "0"]):
                with self.assertRaises(ValueError):
                    resolve_cycle_count(parse_args())


if __name__ == "__main__":
    unittest.main()
