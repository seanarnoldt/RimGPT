import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_controller import AgentController, SYSTEM_INSTRUCTIONS
from context_telemetry import Pricing, measure_context, serialized_chars
from decision_context import DecisionContextBuilder, serialize_context
from decision_handoff import (
    DecisionHandoffError,
    MAX_HANDOFF_CHARS,
    fallback_handoff,
    handoff_chars,
    prepare_handoff,
    validate_handoff,
)
from state_store import StateStore, snapshot_version
from test_state_diff import base_state
from tool_registry import DEFAULT_TOOL_REGISTRY, ActiveToolSet


def call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


def response(response_id, output, output_text="", status="completed"):
    return SimpleNamespace(id=response_id, output=output, output_text=output_text, status=status, usage=None)


def finish(open_loops, resolved_loops=None, assessment="Decision complete"):
    return call("finish_decision", {
        "assessment": assessment,
        "open_loops": open_loops,
        "resolved_loops": resolved_loops or [],
    }, "finish")


def new_loop(status="pending", reason="Research capability is absent"):
    return {
        "id": None,
        "objective": "Build a research bench",
        "next_action": "Place a simple research bench blueprint at a validated location",
        "status": status,
        "reason": reason,
    }


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


class CompactFailureResponses(FakeResponses):
    def compact(self, *, model, input, instructions, previous_response_id, **kwargs):
        raise RuntimeError("mock compaction failure")


class CycleBridge:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.submitted = []

    def health(self):
        return {"status": "ok", "bridge": "RimGPT"}

    def get_state(self):
        return copy.deepcopy(self.state)

    def inspect_map(self, min_x, min_z, max_x, max_z):
        return {"gameLoaded": True, "bounds": {"minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z}, "terrainRows": [], "things": [], "zones": []}

    def list_build_options(self, category=None, search=None):
        return {"options": [{"defName": "ResearchBenchSimple", "label": "simple research bench", "requiredTerrainAffordance": "Medium"}]}

    def check_build_placements(self, placements):
        return {"requested": len(placements), "validCount": len(placements), "placements": [{"index": 0, "valid": True}]}

    def submit_command(self, command):
        self.submitted.append(copy.deepcopy(command))
        return {"commandId": "cmd-bench", "startedAt": time.monotonic(), "submitElapsedSeconds": 0.0}

    def wait_for_commands(self, submissions):
        return {"cmd-bench": {"commandId": "cmd-bench", "status": "completed", "success": True, "elapsedSeconds": 0.01}}

    def wait_for_state_after(self, after_version, timeout_ms=3000):
        self.state["snapshot"]["version"] = after_version + 1
        return copy.deepcopy(self.state)


class DecisionHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_store(self, state=None):
        store = StateStore(self.root, logger=lambda _: None)
        if state is not None:
            store.update_current_state(state)
        return store

    def test_schema_bounds_ids_statuses_and_resolutions(self):
        first = prepare_handoff({"assessment": "Plan research", "open_loops": [new_loop()], "resolved_loops": []}, None)
        self.assertLessEqual(handoff_chars(first), MAX_HANDOFF_CHARS)
        self.assertRegex(first["openLoops"][0]["id"], r"^L-[0-9a-f]{6}$")
        again = prepare_handoff({"assessment": "Still planned", "open_loops": [new_loop()], "resolved_loops": []}, first)
        self.assertEqual(again["openLoops"][0]["id"], first["openLoops"][0]["id"])
        loop_id = first["openLoops"][0]["id"]
        for status in ("pending", "blocked", "deferred"):
            retained = prepare_handoff({"assessment": status, "open_loops": [{**new_loop(status), "id": loop_id}], "resolved_loops": []}, first)
            self.assertEqual(retained["openLoops"][0]["status"], status)
        for resolution in ("completed", "cancelled", "invalidated"):
            resolved = prepare_handoff({"assessment": resolution, "open_loops": [], "resolved_loops": [{"id": loop_id, "resolution": resolution, "reason": "Authoritative state or strategy changed"}]}, first)
            self.assertEqual(resolved["openLoops"], [])

        invalid_values = [
            {"assessment": "x" * 601, "open_loops": [], "resolved_loops": []},
            {"assessment": "x", "open_loops": [new_loop()] * 7, "resolved_loops": []},
            {"assessment": "x", "open_loops": [{**new_loop(), "objective": "x" * 181}], "resolved_loops": []},
            {"assessment": "x", "open_loops": [{**new_loop(), "next_action": "x" * 221}], "resolved_loops": []},
            {"assessment": "x", "open_loops": [{**new_loop(), "reason": "x" * 221}], "resolved_loops": []},
        ]
        for value in invalid_values:
            with self.subTest(value=list(value)):
                with self.assertRaises(DecisionHandoffError):
                    prepare_handoff(value, None)

    def test_prior_loop_cannot_silently_disappear(self):
        prior = prepare_handoff({"assessment": "Prior", "open_loops": [new_loop()], "resolved_loops": []}, None)
        with self.assertRaisesRegex(DecisionHandoffError, "retained or resolved"):
            prepare_handoff({"assessment": "Oops", "open_loops": [], "resolved_loops": []}, prior)
        fallback = fallback_handoff(prior, "Ordinary final prose")
        self.assertEqual(fallback["openLoops"], prior["openLoops"])

    def test_persistence_restart_isolation_fresh_and_corruption_recovery(self):
        state_a = base_state(version=1)
        handoff = prepare_handoff({"assessment": "A", "open_loops": [new_loop()], "resolved_loops": []}, None)
        first = self.make_store(state_a)
        first.commit_successful_decision(state_a, handoff)
        self.assertTrue((first.colony_directory / "decision_handoff.json").exists())

        restarted = self.make_store(state_a)
        self.assertEqual(restarted.get_decision_handoff(), handoff)
        state_b = base_state(version=1)
        state_b["game"]["colonyLineageId"] = "different-colony"
        restarted.update_current_state(state_b)
        self.assertIsNone(restarted.get_decision_handoff())

        fresh_root = self.root / "fresh"
        fresh = StateStore(fresh_root, logger=lambda _: None)
        fresh.update_current_state(state_b)
        self.assertIsNone(fresh.get_decision_handoff())

        corrupt = first.colony_directory / "decision_handoff.json"
        corrupt.write_text("{broken", encoding="utf-8")
        recovered = self.make_store(state_a)
        self.assertIsNone(recovered.get_decision_handoff())
        self.assertTrue(list(corrupt.parent.glob("decision_handoff.json.invalid-*")))

    def test_context_contains_handoff_once_and_telemetry_counts_it(self):
        state = base_state(version=4)
        store = self.make_store(state)
        handoff = prepare_handoff({"assessment": "Research next", "open_loops": [new_loop()], "resolved_loops": []}, None)
        store.commit_successful_decision(state, handoff)
        context = DecisionContextBuilder(store, logger=lambda _: None).build()
        self.assertEqual(context["previousDecision"], handoff)
        post = DecisionContextBuilder(store, logger=lambda _: None).build_post_tool_context(state)
        self.assertNotIn("previousDecision", post)
        items = [{"role": "user", "content": [{"type": "input_text", "text": serialize_context(context)}]}]
        breakdown = measure_context(instructions=SYSTEM_INSTRUCTIONS, tools=ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas(), input_items=items, state=state, accumulated_tool_result_chars=0, carried_context_chars=0, context_payload=context)
        self.assertEqual(breakdown.decision_handoff_chars, serialized_chars(handoff))
        self.assertFalse(breakdown.full_state_sent)
        self.assertNotIn("function_call_output", items[0]["content"][0]["text"])

    def test_core_terminal_schema_is_bounded(self):
        schemas = ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas()
        self.assertEqual(len(schemas), 8)
        self.assertEqual(schemas[0]["name"], "finish_decision")
        self.assertLess(serialized_chars(schemas), 7_000)

    def test_terminal_tool_is_local_and_uses_no_additional_response(self):
        state = base_state(version=20)
        bridge = CycleBridge(state)
        fake = FakeResponses([response("r1", [finish([new_loop()], assessment="Commit research bench")])])
        store = self.make_store()
        controller = AgentController(bridge=bridge, model="test-model", client=SimpleNamespace(responses=fake), state_store=store, pricing=Pricing())
        controller.run_once()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(bridge.submitted, [])
        self.assertEqual(store.get_decision_handoff()["assessment"], "Commit research bench")
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 20)

    def test_abort_and_direct_tool_do_not_replace_prior_handoff(self):
        state = base_state(version=30)
        prior = prepare_handoff({"assessment": "Keep", "open_loops": [new_loop()], "resolved_loops": []}, None)
        store = self.make_store(state)
        store.commit_successful_decision(state, prior)
        bridge = CycleBridge(state)
        failed = AgentController(bridge=bridge, model="test-model", client=SimpleNamespace(responses=FakeResponses([RuntimeError("API failed")])), state_store=store, pricing=Pricing())
        failed.run_once()
        self.assertEqual(store.get_decision_handoff(), prior)
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 30)

        direct = AgentController(bridge=bridge, model="test-model", client=SimpleNamespace(responses=FakeResponses([])), state_store=store, pricing=Pricing())
        direct._begin_cycle()
        direct._execute_tool_call_batch([finish([{**new_loop(), "id": prior["openLoops"][0]["id"]}], assessment="Not committed")])
        self.assertEqual(store.get_decision_handoff(), prior)

        guarded = AgentController(bridge=bridge, model="test-model", client=SimpleNamespace(responses=FakeResponses([])), state_store=store, pricing=Pricing(), max_input_tokens_per_request=1)
        guarded.run_once()
        self.assertEqual(store.get_decision_handoff(), prior)

        limited_responses = FakeResponses([response("limited-r1", [call("get_colony_state", {"section": "resources"}, "read")])])
        limited = AgentController(bridge=bridge, model="test-model", client=SimpleNamespace(responses=limited_responses), state_store=store, pricing=Pricing(), max_model_requests_per_cycle=1)
        limited.run_once()
        self.assertEqual(store.get_decision_handoff(), prior)

        compact_responses = CompactFailureResponses([
            response("compact-r1", [call("get_colony_state", {"section": "resources"}, "read")]),
            RuntimeError("model failed after compaction fallback"),
        ])
        compacting = AgentController(bridge=bridge, model="test-model", client=SimpleNamespace(responses=compact_responses), state_store=store, pricing=Pricing(), compact_threshold_tokens=1)
        compacting.run_once()
        self.assertEqual(store.get_decision_handoff(), prior)

    def test_dry_run_terminal_does_not_persist_handoff(self):
        state = base_state(version=40)
        prior = prepare_handoff({"assessment": "Real intent", "open_loops": [new_loop()], "resolved_loops": []}, None)
        store = self.make_store(state)
        store.commit_successful_decision(state, prior)
        store.apply_memory_update({"unresolvedProblems": ["No research bench"]})
        paths = {
            "current": store._current_state_path(),
            "baseline": store._baseline_path(),
            "handoff": store._handoff_path(),
            "memory": store._memory_path(),
            "risk": store._risk_path(),
        }
        before = {name: path.read_bytes() for name, path in paths.items()}

        live = base_state(version=41)
        live["buildings"].append({
            "id": "Building_ResearchBench",
            "defName": "ResearchBenchSimple",
            "label": "simple research bench",
        })
        updated = [{**new_loop("deferred", "Dry-run planning only"), "id": prior["openLoops"][0]["id"]}]
        fake = FakeResponses([response("r1", [finish(updated, assessment="Dry proposal")])])
        bridge = CycleBridge(live)
        controller = AgentController(bridge=bridge, model="test-model", dry_run=True, client=SimpleNamespace(responses=fake), state_store=store, pricing=Pricing())
        controller.run_once()
        self.assertEqual(store.get_decision_handoff(), prior)
        self.assertEqual(snapshot_version(store.get_decision_baseline()), 40)
        self.assertEqual(store.get_memory()["unresolvedProblems"], ["No research bench"])
        self.assertEqual({name: path.read_bytes() for name, path in paths.items()}, before)
        self.assertEqual(controller.pending_decision_handoff["assessment"], "Dry proposal")
        self.assertEqual(bridge.submitted, [])
        self.assertIsNotNone(controller.dry_run_proposals)

    def test_dry_run_without_prior_baseline_leaves_it_unset(self):
        store = self.make_store()
        self.assertIsNone(store.get_decision_baseline())
        before = list(self.root.iterdir())

        live = base_state(version=46)
        fake = FakeResponses([response("r1", [finish([new_loop()], assessment="Dry proposal")])])
        controller = AgentController(
            bridge=CycleBridge(live),
            model="test-model",
            dry_run=True,
            client=SimpleNamespace(responses=fake),
            state_store=store,
            pricing=Pricing(),
        )
        controller.run_once()

        self.assertIsNone(store.get_decision_baseline())
        self.assertIsNone(store.get_decision_handoff())
        self.assertFalse(store._baseline_path().exists())
        self.assertFalse(store._handoff_path().exists())
        self.assertEqual(list(self.root.iterdir()), before)

    def test_research_bench_three_cycle_continuity(self):
        state1 = base_state(version=50)
        store = self.make_store()
        cycle1_responses = FakeResponses([response("c1-r1", [finish([new_loop()], assessment="Research capability is missing")])])
        cycle1 = AgentController(bridge=CycleBridge(state1), model="test-model", client=SimpleNamespace(responses=cycle1_responses), state_store=store, pricing=Pricing())
        cycle1.run_once()
        loop_id = store.get_decision_handoff()["openLoops"][0]["id"]

        state2 = base_state(version=51)
        bridge2 = CycleBridge(state2)
        cycle2_responses = FakeResponses([
            response("c2-r1", [call("enable_capability", {"name": "construction"}, "enable")]),
            response("c2-r2", [call("inspect_map", {"min_x": 45, "min_z": 45, "max_x": 59, "max_z": 59}, "inspect")]),
            response("c2-r3", [call("list_build_options", {"category": None, "search": "research bench"}, "catalog")]),
            response("c2-r4", [call("check_build_placements", {"placements": [{"build_def": "ResearchBenchSimple", "x": 50, "z": 50, "rotation": "North", "stuff_def": "WoodLog"}]}, "check")]),
            response("c2-r5", [call("place_blueprint", {"build_def": "ResearchBenchSimple", "x": 50, "z": 50, "rotation": "North", "stuff_def": "WoodLog"}, "place")]),
            response("c2-r6", [finish([{**new_loop(), "id": loop_id}], assessment="Research bench blueprint was ordered")]),
        ])
        cycle2 = AgentController(bridge=bridge2, model="test-model", client=SimpleNamespace(responses=cycle2_responses), state_store=store, pricing=Pricing())
        cycle2.run_once()
        initial2 = cycle2_responses.calls[0]["input"][0]["content"][0]["text"]
        self.assertIn("Build a research bench", initial2)
        self.assertEqual(len(cycle2_responses.calls), 6)
        self.assertEqual(bridge2.submitted[0]["command"], "placeBlueprint")
        self.assertTrue(all("previousDecision" not in json.dumps(request["input"]) for request in cycle2_responses.calls[1:]))
        self.assertEqual(store.get_decision_handoff()["openLoops"][0]["id"], loop_id)

        state3 = base_state(version=60)
        state3["buildings"].append({"id": "Blueprint_ResearchBench", "defName": "ResearchBenchSimple", "label": "simple research bench blueprint"})
        cycle3_responses = FakeResponses([response("c3-r1", [finish([], [{"id": loop_id, "resolution": "completed", "reason": "Authoritative state shows the blueprint"}], "Research bench is now queued")])])
        cycle3 = AgentController(bridge=CycleBridge(state3), model="test-model", client=SimpleNamespace(responses=cycle3_responses), state_store=store, pricing=Pricing())
        cycle3.run_once()
        self.assertIn("ResearchBenchSimple", cycle3_responses.calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(store.get_decision_handoff()["openLoops"], [])


if __name__ == "__main__":
    unittest.main()
