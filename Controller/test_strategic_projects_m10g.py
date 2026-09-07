import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_controller import SYSTEM_INSTRUCTIONS
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, serialized_chars
from decision_context import DecisionContextBuilder, serialize_context
from decision_handoff import DecisionHandoffError, prepare_handoff, validate_handoff
from progress_tracking import empty_stall_metadata, update_open_loop_stalls
from prompt_runtime import RIMGPT_PROMPT_VERSION
from state_store import StateStore
from strategic_projects import build_project_context
from test_state_diff import base_state, pawn
from tool_registry import DEFAULT_TOOL_REGISTRY, ActiveToolSet


def project_input(project_id=None, tasks=None, *, status="active", criteria=None):
    return {
        "id": project_id,
        "objective": "Establish resilient early infrastructure",
        "rationale": "Power, food production, and defense can advance in parallel",
        "status": status,
        "success_criteria": criteria or [
            "Reliable powered work area exists",
            "Meal production is operational",
            "A defensible initial position is complete",
        ],
        "blockers": [],
        "tasks": tasks or [
            task("research", "Continue Batteries research", status="background", mode="background"),
            task("power", "Establish power generation"),
            task("lighting", "Connect lighting to the grid", depends_on=["power"]),
            task("cooking", "Establish operational cooking infrastructure"),
            task("defense", "Build a meaningful initial defensive position"),
        ],
    }


def task(key, objective, *, task_id=None, status="pending", mode="active", depends_on=None, blockers=None):
    return {
        "id": task_id,
        "key": key,
        "objective": objective,
        "status": status,
        "mode": mode,
        "depends_on": depends_on or [],
        "blockers": blockers or [],
    }


def finish_arguments(projects, resolved_projects=None):
    return {
        "assessment": "Maintain parallel early-colony work",
        "open_loops": [],
        "resolved_loops": [],
        "projects": projects,
        "resolved_projects": resolved_projects or [],
    }


def retained_project(project, *, task_updates=None, status=None):
    updates = task_updates or {}
    tasks = []
    for item in project["tasks"]:
        update = updates.get(item["key"], {})
        tasks.append(task(
            item["key"],
            update.get("objective", item["objective"]),
            task_id=item["id"],
            status=update.get("status", item["status"]),
            mode=update.get("mode", item["mode"]),
            depends_on=update.get("depends_on", item["dependsOn"]),
            blockers=update.get("blockers", item["blockers"]),
        ))
    return project_input(
        project["id"],
        tasks,
        status=status or project["status"],
        criteria=project["successCriteria"],
    )


def project_state(version=100, lineage="lineage-a"):
    state = base_state(version=version, lineage=lineage)
    state["research"]["current"] = {
        "defName": "Batteries", "label": "Batteries", "progress": 420.0, "cost": 600.0
    }
    state["colonists"].extend([pawn("Pawn_B"), pawn("Pawn_C")])
    for colonist in state["colonists"]:
        colonist["currentJob"] = None
    state["operations"]["labor"] = {
        "capableIdleColonistCount": 3,
        "pendingWork": {"blueprints": 2, "frames": 1},
        "obviousBlockers": [],
    }
    return state


class StrategicProjectsM10GTests(unittest.TestCase):
    def test_project_supports_parallel_tasks_and_deterministic_ids(self):
        first = prepare_handoff(finish_arguments([project_input()]), None)
        again = prepare_handoff(finish_arguments([project_input()]), None)
        project = first["projects"][0]
        self.assertEqual(project["id"], again["projects"][0]["id"])
        self.assertRegex(project["id"], r"^P-[0-9a-f]{6}$")
        self.assertEqual(len(project["tasks"]), 5)
        self.assertEqual(len({item["id"] for item in project["tasks"]}), 5)
        self.assertEqual(
            {item["key"] for item in project["tasks"] if not item["dependsOn"]},
            {"research", "power", "cooking", "defense"},
        )

    def test_completed_dependency_unlocks_next_task_and_survives_handoff(self):
        first = prepare_handoff(finish_arguments([project_input()]), None)
        project = first["projects"][0]
        second = prepare_handoff(finish_arguments([
            retained_project(project, task_updates={"power": {"status": "completed"}})
        ]), first)
        context = build_project_context(second, {"signals": []}, None, project_state(), {})
        summary = context["projects"][0]
        power_id = next(item["id"] for item in second["projects"][0]["tasks"] if item["key"] == "power")
        lighting_id = next(item["id"] for item in second["projects"][0]["tasks"] if item["key"] == "lighting")
        self.assertIn(power_id, summary["completedTaskIds"])
        self.assertIn(lighting_id, summary["availableTaskIds"])
        with self.assertRaisesRegex(DecisionHandoffError, "completed project task"):
            prepare_handoff(finish_arguments([
                retained_project(second["projects"][0], task_updates={"power": {"status": "pending"}})
            ]), second)

    def test_blocked_branch_does_not_drop_unrelated_available_work(self):
        first = prepare_handoff(finish_arguments([project_input()]), None)
        blocked = prepare_handoff(finish_arguments([
            retained_project(first["projects"][0], task_updates={
                "power": {"status": "blocked", "blockers": ["No construction material"]}
            })
        ]), first)
        summary = build_project_context(blocked, {"signals": []}, None, project_state(), {})["projects"][0]
        keys_by_id = {item["id"]: item["key"] for item in blocked["projects"][0]["tasks"]}
        self.assertEqual({keys_by_id[item] for item in summary["availableTaskIds"]}, {"cooking", "defense"})
        self.assertEqual(keys_by_id[summary["attentionTasks"][0]["taskId"]], "power")
        self.assertEqual(keys_by_id[summary["waitingTasks"][0]["taskId"]], "lighting")

    def test_background_progress_does_not_suppress_parallel_work(self):
        handoff = prepare_handoff(finish_arguments([project_input()]), None)
        context = build_project_context(handoff, {"signals": []}, None, project_state(), {})
        continuity = context["workContinuity"]
        self.assertIn("research", {item["type"] for item in continuity["background"]})
        self.assertIn("construction", {item["type"] for item in continuity["background"]})
        self.assertGreaterEqual(continuity["availableProjectTasks"], 3)
        self.assertTrue(continuity["parallelWorkRecommended"])

    def test_alert_or_token_defense_progress_does_not_complete_project(self):
        handoff = prepare_handoff(finish_arguments([project_input()]), None)
        state = project_state()
        state["awareness"] = {"activeAlerts": [], "activeLetters": [], "recentEvents": []}
        progress = {
            "categories": ["construction", "awareness"],
            "signals": [
                {"type": "constructionCountChanged", "category": "construction", "defName": "Barricade"},
                {"type": "alertResolved", "category": "awareness", "label": "Need defenses"},
            ],
        }
        summary = build_project_context(handoff, progress, None, state, {})["projects"][0]
        self.assertEqual(summary["completion"]["completedTasks"], 0)
        self.assertTrue(summary["completion"]["requiresAuthoritativeCriteria"])
        self.assertEqual(handoff["projects"][0]["status"], "active")
        with self.assertRaisesRegex(DecisionHandoffError, "every persisted success criterion"):
            prepare_handoff(finish_arguments([], [{
                "id": handoff["projects"][0]["id"],
                "resolution": "completed",
                "reason": "The warning disappeared after one barricade",
                "criteria_met": ["A defensible initial position is complete"],
            }]), handoff)

    def test_stalled_branch_can_be_replanned_without_dropping_other_tasks(self):
        first = prepare_handoff(finish_arguments([project_input()]), None)
        identity = {"lineageId": "lineage-a", "mapId": "map-7"}
        metadata = empty_stall_metadata(identity)
        unchanged = prepare_handoff(finish_arguments([retained_project(first["projects"][0])]), first)
        metadata = update_open_loop_stalls(metadata, first, unchanged, {"categories": []})
        metadata = update_open_loop_stalls(metadata, unchanged, unchanged, {"categories": []})
        context = build_project_context(unchanged, {"signals": []}, metadata, project_state(), {})
        self.assertTrue(context["projects"][0]["attentionTasks"])

        replanned = prepare_handoff(finish_arguments([
            retained_project(unchanged["projects"][0], task_updates={
                "defense": {"objective": "Validate and build a compact defensive firing position"}
            })
        ]), unchanged)
        self.assertEqual(len(replanned["projects"][0]["tasks"]), 5)
        self.assertIn("cooking", {item["key"] for item in replanned["projects"][0]["tasks"]})

    def test_project_metadata_is_bounded_colony_scoped_and_not_a_transcript(self):
        handoff = prepare_handoff(finish_arguments([project_input()]), None)
        self.assertLessEqual(len(handoff["projects"]), 3)
        self.assertLessEqual(len(handoff["projects"][0]["tasks"]), 8)
        serialized = json.dumps(handoff)
        self.assertNotIn("transcript", serialized.lower())
        self.assertNotIn("tool_history", serialized.lower())
        self.assertNotIn("raw_model", serialized.lower())
        with self.assertRaises(DecisionHandoffError):
            prepare_handoff(finish_arguments([project_input()] * 4), None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = StateStore(root, logger=lambda _: None)
            state_a = project_state(lineage="lineage-a")
            first.update_current_state(state_a)
            first.commit_successful_decision(state_a, handoff)
            second = StateStore(root, logger=lambda _: None)
            state_b = project_state(lineage="lineage-b")
            second.update_current_state(state_b)
            self.assertIsNone(second.get_decision_handoff())

    def test_v1_handoff_migrates_without_losing_open_loops(self):
        legacy = {
            "schemaVersion": 1,
            "assessment": "Continue shelter",
            "openLoops": [{
                "id": "L-abcdef",
                "objective": "Finish shelter",
                "nextAction": "Complete walls",
                "status": "pending",
                "reason": "Colonists need beds indoors",
            }],
        }
        migrated = validate_handoff(legacy)
        self.assertEqual(migrated["schemaVersion"], 2)
        self.assertEqual(migrated["openLoops"], legacy["openLoops"])
        self.assertEqual(migrated["projects"], [])

    def test_representative_context_is_bounded_and_never_sends_full_state(self):
        state = project_state()
        handoff = prepare_handoff(finish_arguments([project_input()]), None)
        with tempfile.TemporaryDirectory() as directory:
            logs = []
            store = StateStore(Path(directory), logger=logs.append)
            store.update_current_state(state)
            store.commit_successful_decision(state, handoff)
            context = DecisionContextBuilder(store, logger=logs.append).build()
        estimated_tokens = (
            len(SYSTEM_INSTRUCTIONS)
            + serialized_chars(ActiveToolSet(DEFAULT_TOOL_REGISTRY).schemas())
            + len(serialize_context(context))
        ) // 3
        self.assertLess(estimated_tokens, DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST)
        self.assertLess(serialized_chars(context["strategicProjects"]), 4_000)
        self.assertTrue(any("fullStateSent=false" in item for item in logs))
        self.assertNotIn('"buildings":', serialize_context(context))

    def test_prompt_encodes_parallel_and_success_criteria_policy(self):
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10g")
        self.assertIn("productive in parallel", SYSTEM_INSTRUCTIONS)
        self.assertIn("background progress", SYSTEM_INSTRUCTIONS)
        self.assertIn("Alert disappearance alone is insufficient", SYSTEM_INSTRUCTIONS)
        self.assertIn("single token action", SYSTEM_INSTRUCTIONS)
        self.assertIn("replan that branch while preserving healthy unrelated tasks", SYSTEM_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
