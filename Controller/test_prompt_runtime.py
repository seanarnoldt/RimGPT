import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from context_telemetry import ModelContextLimitError, Pricing, extract_usage
from prompt_runtime import (
    RIMGPT_PROMPT_VERSION,
    build_prompt_cache_key,
    compacted_output_as_input,
    detect_responses_features,
    prompt_cache_request_fields,
)
from state_store import StateStore, snapshot_version
from test_state_diff import base_state


class SupportedResponses:
    def __init__(self, *, compact_output=None, compact_error=None):
        self.create_calls = []
        self.compact_calls = []
        self.compact_output = compact_output or [
            {"type": "compaction", "encrypted_content": "opaque-cycle-context"}
        ]
        self.compact_error = compact_error

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(id=f"response-{len(self.create_calls)}", output=[], usage=None)

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        if self.compact_error:
            raise self.compact_error
        return SimpleNamespace(output=copy.deepcopy(self.compact_output), usage=None)


class UnsupportedResponses:
    def __init__(self):
        self.create_calls = []

    def create(self, *, model, instructions, tools, input, previous_response_id=None):
        self.create_calls.append({
            "model": model,
            "instructions": instructions,
            "tools": tools,
            "input": input,
            "previous_response_id": previous_response_id,
        })
        return SimpleNamespace(id=f"response-{len(self.create_calls)}", output=[], usage=None)


class PromptRuntimeTests(unittest.TestCase):
    def make_controller(
        self,
        responses,
        *,
        threshold=20_000,
        hard_limit=30_000,
        max_compactions=1,
        max_requests=8,
        model="gpt-5.6",
    ):
        controller = object.__new__(AgentController)
        controller.model = model
        controller.client = SimpleNamespace(responses=responses)
        controller.max_input_tokens_per_request = hard_limit
        controller.max_model_requests_per_cycle = max_requests
        controller.prompt_cache_mode = "implicit"
        controller.compact_threshold_tokens = threshold
        controller.max_compactions_per_cycle = max_compactions
        controller.pricing = Pricing()
        controller.model_request_count = 0
        controller.accumulated_tool_result_chars = 0
        controller.tool_result_chars_this_round = 0
        controller.carried_context_chars = 0
        controller.cycle_cost = 0.0
        controller.session_cost = 0.0
        controller.compaction_count = 0
        controller.previous_response_id = None
        return controller

    def test_stable_prefix_and_cache_key_are_independent_of_dynamic_state(self):
        before = SYSTEM_INSTRUCTIONS.encode("utf-8")
        dynamic_a = {"snapshot": "SNAPSHOT_SENTINEL_A", "colony": "COLONY_SENTINEL_A", "memory": "MEMORY_SENTINEL_A"}
        dynamic_b = {"snapshot": "SNAPSHOT_SENTINEL_B", "colony": "COLONY_SENTINEL_B", "memory": "MEMORY_SENTINEL_B"}
        self.assertEqual(before, SYSTEM_INSTRUCTIONS.encode("utf-8"))
        for value in (*dynamic_a.values(), *dynamic_b.values()):
            self.assertNotIn(str(value), SYSTEM_INSTRUCTIONS)
        self.assertEqual(build_prompt_cache_key("gpt-5.6"), build_prompt_cache_key("gpt-5.6"))

    def test_cache_key_changes_only_with_prompt_version_or_model(self):
        baseline = build_prompt_cache_key("gpt-5.6")
        self.assertEqual(baseline, build_prompt_cache_key("gpt-5.6", RIMGPT_PROMPT_VERSION))
        self.assertNotEqual(baseline, build_prompt_cache_key("gpt-5.6", "context-memory-v1-m10"))
        self.assertNotEqual(baseline, build_prompt_cache_key("gpt-5.6-sol"))
        self.assertNotIn("snapshot", baseline)

    def test_cache_options_use_only_detected_supported_fields(self):
        supported = detect_responses_features(SupportedResponses())
        fields = prompt_cache_request_fields(supported, "gpt-5.6", "implicit")
        self.assertEqual(fields["prompt_cache_options"], {"mode": "implicit", "ttl": "30m"})
        self.assertIn("prompt_cache_key", fields)
        disabled = prompt_cache_request_fields(supported, "gpt-5.6", "disabled")
        self.assertEqual(disabled, {})

        unsupported = detect_responses_features(UnsupportedResponses())
        self.assertEqual(prompt_cache_request_fields(unsupported, "gpt-5.6"), {})

    def test_older_model_omits_options_but_keeps_supported_cache_key(self):
        features = detect_responses_features(SupportedResponses())
        fields = prompt_cache_request_fields(features, "gpt-5.5")
        self.assertIn("prompt_cache_key", fields)
        self.assertNotIn("prompt_cache_options", fields)

    def test_compacted_output_is_copied_to_plain_api_input(self):
        item = SimpleNamespace(type="compaction", encrypted_content="opaque", id="compact-1")
        source = SimpleNamespace(output=[item])
        self.assertEqual(
            compacted_output_as_input(source)[0],
            {"type": "compaction", "encrypted_content": "opaque", "id": "compact-1"},
        )

    def test_below_threshold_uses_normal_continuation_without_compaction(self):
        responses = SupportedResponses()
        controller = self.make_controller(responses, threshold=20_000)
        controller._request_model(self.small_input(), state={}, previous_response_id="previous-1")
        self.assertEqual(responses.compact_calls, [])
        self.assertEqual(responses.create_calls[0]["previous_response_id"], "previous-1")
        self.assertEqual(
            responses.create_calls[0]["prompt_cache_key"],
            "rimgpt:context-memory-v1-m10h5:gpt-5.6",
        )
        self.assertEqual(
            responses.create_calls[0]["prompt_cache_options"],
            {"mode": "implicit", "ttl": "30m"},
        )

    def test_above_threshold_compacts_once_and_reestimates_before_create(self):
        responses = SupportedResponses()
        controller = self.make_controller(responses, threshold=1)
        controller._request_model(self.small_input(), state={}, previous_response_id="previous-1")
        self.assertEqual(len(responses.compact_calls), 1)
        self.assertEqual(responses.compact_calls[0]["previous_response_id"], "previous-1")
        self.assertEqual(responses.compact_calls[0]["input"][:-1], self.small_input())
        self.assertIn("decisionBudget", responses.compact_calls[0]["input"][-1]["content"][0]["text"])
        self.assertEqual(responses.create_calls[0]["input"][:-1], responses.compact_output)
        self.assertIn('"requestsRemaining":7', responses.create_calls[0]["input"][-1]["content"][0]["text"])
        self.assertNotIn("previous_response_id", responses.create_calls[0])
        self.assertEqual(controller.compaction_count, 1)
        self.assertEqual(controller.model_request_count, 2)

    def test_continuation_over_hard_guard_attempts_its_one_compaction_first(self):
        responses = SupportedResponses()
        controller = self.make_controller(responses, threshold=20_000, hard_limit=30_000)
        controller._request_model(
            [{"role": "user", "content": [{"type": "input_text", "text": "x" * 100_000}]}],
            state={},
            previous_response_id="previous-1",
        )
        self.assertEqual(len(responses.compact_calls), 1)
        self.assertEqual(len(responses.create_calls), 1)
        self.assertNotIn("previous_response_id", responses.create_calls[0])

    def test_max_compactions_prevents_second_compaction(self):
        responses = SupportedResponses()
        controller = self.make_controller(responses, threshold=1, max_compactions=1)
        controller._request_model(self.small_input(), state={}, previous_response_id="previous-1")
        controller._request_model(self.small_input(), state={}, previous_response_id="previous-2")
        self.assertEqual(len(responses.compact_calls), 1)
        self.assertEqual(responses.create_calls[1]["previous_response_id"], "previous-2")

    def test_compaction_failure_continues_only_when_below_hard_limit(self):
        responses = SupportedResponses(compact_error=RuntimeError("compact unavailable"))
        controller = self.make_controller(responses, threshold=1, hard_limit=30_000)
        controller._request_model(self.small_input(), state={}, previous_response_id="previous-1")
        self.assertEqual(len(responses.compact_calls), 1)
        self.assertEqual(responses.create_calls[0]["previous_response_id"], "previous-1")
        self.assertEqual(controller.compaction_count, 1)

        blocked = SupportedResponses(compact_error=RuntimeError("compact unavailable"))
        controller = self.make_controller(blocked, threshold=1, hard_limit=5_000)
        with self.assertRaises(ModelContextLimitError):
            controller._request_model(
                [{"role": "user", "content": [{"type": "input_text", "text": "x" * 20_000}]}],
                state={},
                previous_response_id="previous-1",
            )
        self.assertEqual(len(blocked.compact_calls), 1)
        self.assertEqual(blocked.create_calls, [])

    def test_post_compaction_hard_limit_blocks_model_request(self):
        responses = SupportedResponses(
            compact_output=[{"type": "compaction", "encrypted_content": "x" * 100_000}]
        )
        controller = self.make_controller(responses, threshold=1, hard_limit=30_000)
        with self.assertRaises(ModelContextLimitError):
            controller._request_model(self.small_input(), state={}, previous_response_id="previous-1")
        self.assertEqual(len(responses.compact_calls), 1)
        self.assertEqual(responses.create_calls, [])

    def test_compaction_abort_does_not_advance_baseline_or_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory), logger=lambda _: None)
            baseline = base_state(version=30)
            store.update_current_state(baseline)
            store.set_decision_baseline(baseline)
            store.apply_memory_update({"currentGoals": ["Keep this goal"]})
            memory_before = store.get_memory()
            responses = SupportedResponses(
                compact_output=[{"type": "compaction", "encrypted_content": "x" * 100_000}]
            )
            controller = self.make_controller(responses, threshold=1, hard_limit=30_000)
            controller.state_store = store
            with self.assertRaises(ModelContextLimitError):
                controller._request_model(self.small_input(), state=baseline, previous_response_id="previous-1")
            self.assertEqual(snapshot_version(store.get_decision_baseline()), 30)
            self.assertEqual(store.get_memory(), memory_before)

    def test_unsupported_compaction_falls_back_below_limit_and_aborts_above_limit(self):
        responses = UnsupportedResponses()
        controller = self.make_controller(responses, threshold=1, hard_limit=30_000)
        controller._request_model(self.small_input(), state={}, previous_response_id="previous-1")
        self.assertEqual(len(responses.create_calls), 1)
        self.assertNotIn("prompt_cache_key", responses.create_calls[0])

        blocked = UnsupportedResponses()
        controller = self.make_controller(blocked, threshold=1, hard_limit=5_000)
        with self.assertRaises(ModelContextLimitError):
            controller._request_model(
                [{"role": "user", "content": [{"type": "input_text", "text": "x" * 20_000}]}],
                state={},
                previous_response_id="previous-1",
            )
        self.assertEqual(blocked.create_calls, [])

    def test_previous_response_id_is_within_cycle_only(self):
        responses = SupportedResponses()
        controller = self.make_controller(responses)
        first = controller._request_model(self.small_input(), state={})
        controller._request_model(self.small_input(), state={}, previous_response_id=first.id)
        self.assertNotIn("previous_response_id", responses.create_calls[0])
        self.assertEqual(responses.create_calls[1]["previous_response_id"], first.id)
        controller._begin_cycle()
        controller._request_model(self.small_input(), state={})
        self.assertNotIn("previous_response_id", responses.create_calls[2])
        self.assertEqual(controller.previous_response_id, "response-3")

    def test_cache_usage_and_cache_write_pricing(self):
        usage = extract_usage(
            SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=6000,
                    output_tokens=500,
                    input_tokens_details=SimpleNamespace(cached_tokens=4000, cache_write_tokens=1000),
                )
            ),
            Pricing(10.0, 2.0, 20.0, 12.0),
        )
        self.assertEqual(usage.cached_input_tokens, 4000)
        self.assertEqual(usage.cache_write_tokens, 1000)
        self.assertEqual(usage.uncached_input_tokens, 2000)
        self.assertAlmostEqual(usage.estimated_cost or 0.0, 0.04)

    def test_partial_or_malformed_cache_usage_degrades_safely(self):
        usage = extract_usage(
            SimpleNamespace(
                usage={
                    "input_tokens": 6000,
                    "output_tokens": "bad",
                    "input_tokens_details": {"cached_tokens": -1, "cache_write_tokens": None},
                }
            ),
            Pricing(),
        )
        self.assertIsNone(usage.cached_input_tokens)
        self.assertIsNone(usage.cache_write_tokens)
        self.assertEqual(usage.uncached_input_tokens, 6000)
        self.assertIsNone(usage.output_tokens)

    @staticmethod
    def small_input():
        return [{"role": "user", "content": [{"type": "input_text", "text": "small continuation"}]}]


if __name__ == "__main__":
    unittest.main()
