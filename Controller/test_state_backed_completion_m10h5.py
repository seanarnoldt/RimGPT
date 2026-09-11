import copy
import json
import unittest
from pathlib import Path

from agent_controller import SYSTEM_INSTRUCTIONS
from progress_tracking import (
    build_progress_signals,
    build_stall_context,
    empty_stall_metadata,
    update_open_loop_stalls,
)
from prompt_runtime import RIMGPT_PROMPT_VERSION
from state_diff import StateDiff
from state_store import StateIdentity
from strategic_projects import build_project_context
from test_progress_verification_m10e import room
from test_state_diff import base_state


def growing_zone(state, *, planted, unsown, growing, harvestable, phase, complete):
    zone = state["map"]["zones"][0]
    zone.update({
        "observedCells": zone["cellCount"],
        "plantedCells": planted,
        "unsownEligibleCells": unsown,
        "growingCells": growing,
        "harvestableCells": harvestable,
        "plantingComplete": complete,
        "growingState": phase,
    })
    return state


def loop(objective, next_action=None, loop_id="L-grow01"):
    return {
        "id": loop_id,
        "objective": objective,
        "nextAction": next_action or objective,
        "status": "pending",
        "reason": "test",
    }


def handoff(item):
    return {"openLoops": [item], "projects": []}


def repeat_stall(item, progress):
    identity = StateIdentity("lineage-a").as_dict()
    metadata = empty_stall_metadata(identity)
    first = handoff(item)
    metadata = update_open_loop_stalls(metadata, None, first, progress)
    metadata = update_open_loop_stalls(metadata, first, first, progress)
    return metadata, build_stall_context(metadata, first, progress)


def project_task(objective, task_id="T-plant01"):
    return {
        "id": task_id,
        "key": "phase",
        "objective": objective,
        "status": "active",
        "mode": "active",
        "dependsOn": [],
        "blockers": [],
    }


def project_handoff(task):
    return {"openLoops": [], "projects": [{
        "id": "P-test01",
        "status": "active",
        "successCriteria": ["authoritative state"],
        "tasks": [task],
    }]}


class StateBackedCompletionM10H5Tests(unittest.TestCase):
    def progress_for(self, state):
        return build_progress_signals(copy.deepcopy(state), copy.deepcopy(state))

    def test_empty_and_partially_planted_fields_remain_incomplete(self):
        for values in (
            dict(planted=0, unsown=60, growing=0, harvestable=0, phase="empty", complete=False),
            dict(planted=24, unsown=36, growing=24, harvestable=0, phase="partiallyPlanted", complete=False),
        ):
            state = growing_zone(base_state(), **values)
            progress = self.progress_for(state)
            metadata, context = repeat_stall(loop("Plant the potato field"), progress)
            self.assertEqual(progress["completionEvidence"]["growing"]["unfinishedPlantingZones"], 1)
            self.assertGreater(metadata["loops"][0]["noRelevantProgressCycles"], 0)
            self.assertTrue(context["openLoops"][0]["stalled"])

    def test_fully_planted_field_without_recent_sowing_is_background_not_stalled(self):
        state = growing_zone(
            base_state(), planted=60, unsown=0, growing=60, harvestable=0,
            phase="planted", complete=True,
        )
        progress = self.progress_for(state)
        metadata, context = repeat_stall(loop("Plant the potato field"), progress)
        zone = progress["completionEvidence"]["growing"]["zones"][0]
        self.assertEqual(zone["state"], "planted")
        self.assertTrue(zone["plantingComplete"])
        self.assertEqual(metadata["loops"][0]["noRelevantProgressCycles"], 0)
        self.assertFalse(context["openLoops"][0]["stalled"])
        self.assertIn("planting complete", context["openLoops"][0]["authoritativeCompletionEvidence"][0])

        projects = build_project_context(
            project_handoff(project_task("Plant the potato field")), progress, metadata, state, {}
        )
        growing = next(item for item in projects["workContinuity"]["background"] if item["type"] == "growing")
        self.assertEqual(growing["plantingCompleteZones"], 1)
        self.assertEqual(projects["projects"][0]["completion"]["stateSatisfiedTasks"], 1)

    def test_harvestable_field_keeps_planting_complete_but_harvest_is_separate(self):
        state = growing_zone(
            base_state(), planted=60, unsown=0, growing=0, harvestable=60,
            phase="harvestable", complete=True,
        )
        progress = self.progress_for(state)
        _, planting = repeat_stall(loop("Plant the potato field"), progress)
        harvest_metadata, harvesting = repeat_stall(loop("Harvest the potato field"), progress)
        self.assertFalse(planting["openLoops"][0]["stalled"])
        self.assertTrue(harvesting["openLoops"][0]["stalled"])
        self.assertGreater(harvest_metadata["loops"][0]["noRelevantProgressCycles"], 0)

    def test_growing_transition_is_progress_before_completion(self):
        before = growing_zone(base_state(), planted=10, unsown=50, growing=10, harvestable=0, phase="partiallyPlanted", complete=False)
        after = growing_zone(copy.deepcopy(before), planted=25, unsown=35, growing=25, harvestable=0, phase="partiallyPlanted", complete=False)
        after["snapshot"]["version"] += 1
        progress = build_progress_signals(before, after)
        self.assertIn("growingZoneAdvanced", [item["type"] for item in progress["signals"]])

    def test_pending_construction_can_stall_but_completed_transition_does_not(self):
        pending = base_state()
        pending["buildings"].append({
            "id": "Blueprint_Cooler_1", "type": "blueprint", "defName": "Blueprint_Cooler",
            "position": {"x": 42, "z": 40}, "rotation": "North",
        })
        pending["operations"]["labor"] = {"pendingWork": {"blueprints": 1, "frames": 0, "haulables": 3}}
        pending_progress = self.progress_for(pending)
        _, pending_context = repeat_stall(loop("Build the cooler"), pending_progress)
        self.assertTrue(pending_context["openLoops"][0]["stalled"])

        completed = copy.deepcopy(pending)
        completed["snapshot"]["version"] += 1
        completed["buildings"][-1] = {
            "id": "Cooler_1", "type": "building", "defName": "Cooler",
            "position": {"x": 42, "z": 40}, "rotation": "North",
        }
        completed["operations"]["labor"]["pendingWork"] = {"blueprints": 0, "frames": 0, "haulables": 0}
        progress = build_progress_signals(pending, completed)
        metadata, context = repeat_stall(loop("Build the cooler"), progress)
        self.assertEqual(metadata["loops"][0]["noRelevantProgressCycles"], 0)
        self.assertFalse(context["openLoops"][0]["stalled"])
        self.assertTrue(any("completed" in item for item in context["openLoops"][0]["authoritativeCompletionEvidence"]))

    def test_completed_enclosed_roofed_shelter_suppresses_inactivity_stall(self):
        state = base_state()
        state["operations"]["beds"][0]["room"] = room(enclosed=True)
        progress = self.progress_for(state)
        _, context = repeat_stall(loop("Complete the enclosed roofed starter shelter"), progress)
        self.assertFalse(context["openLoops"][0]["stalled"])
        self.assertEqual(progress["completionEvidence"]["shelter"]["enclosedRoofedRooms"], 1)

    def test_existing_specific_infrastructure_is_completion_not_inactivity(self):
        state = base_state()
        progress = self.progress_for(state)
        metadata, context = repeat_stall(loop("Build the SimpleResearchBench"), progress)
        self.assertEqual(metadata["loops"][0]["noRelevantProgressCycles"], 0)
        self.assertFalse(context["openLoops"][0]["stalled"])
        self.assertIn("SimpleResearchBench", context["openLoops"][0]["authoritativeCompletionEvidence"][0])

        projects = build_project_context(
            project_handoff(project_task("Build the SimpleResearchBench")), progress, metadata, state, {}
        )["projects"][0]
        self.assertEqual(projects["activeTaskIds"], [])
        self.assertEqual(projects["availableTaskIds"], [])
        self.assertEqual(projects["completion"]["stateSatisfiedTasks"], 1)

    def test_prerequisite_wording_does_not_falsely_complete_parent_objective(self):
        state = base_state()
        state["operations"]["beds"][0]["room"] = room(enclosed=True)
        progress = self.progress_for(state)
        item = loop(
            "Establish a proper meal source",
            "Inspect the shelter-adjacent region and place a stove",
        )
        _, context = repeat_stall(item, progress)
        self.assertNotIn("authoritativeCompletionEvidence", context["openLoops"][0])
        self.assertTrue(context["openLoops"][0]["stalled"])

    def test_research_advancing_is_progress_and_completed_research_is_not_stalled(self):
        before = base_state()
        before["research"]["completed"] = []
        after = copy.deepcopy(before)
        after["snapshot"]["version"] += 1
        after["research"]["current"]["progress"] += 25
        advancing = build_progress_signals(before, after)
        self.assertIn("researchAdvanced", [item["type"] for item in advancing["signals"]])

        done = copy.deepcopy(after)
        done["research"]["current"] = None
        done["research"]["completed"] = [{"defName": "Electricity", "label": "Electricity"}]
        progress = self.progress_for(done)
        _, context = repeat_stall(loop("Research Electricity"), progress)
        self.assertFalse(context["openLoops"][0]["stalled"])
        self.assertIn("research completed", context["openLoops"][0]["authoritativeCompletionEvidence"][0])

        delta = StateDiff.compare(after, done)["changes"]["research"]
        self.assertEqual(delta["completed"][0]["defName"], "Electricity")

    def test_clear_hauling_backlog_is_satisfied_but_real_unmet_work_still_stalls(self):
        state = base_state()
        state["operations"]["labor"] = {"pendingWork": {"haulables": 0, "blueprints": 0, "frames": 0}}
        progress = self.progress_for(state)
        _, hauling = repeat_stall(loop("Haul visible supplies to storage"), progress)
        self.assertFalse(hauling["openLoops"][0]["stalled"])

        _, unmet = repeat_stall(loop("Create a defensive perimeter", loop_id="L-unmet1"), progress)
        self.assertTrue(unmet["openLoops"][0]["stalled"])

    def test_context_is_bounded_and_prompt_encodes_completion_first_rule(self):
        state = growing_zone(
            base_state(), planted=60, unsown=0, growing=60, harvestable=0,
            phase="planted", complete=True,
        )
        progress = self.progress_for(state)
        self.assertLess(len(json.dumps(progress, separators=(",", ":"))), 8_000)
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10h5")
        self.assertIn("inactivity is not a stall", SYSTEM_INSTRUCTIONS)

    def test_bounded_building_snapshot_prioritizes_pending_and_player_construction(self):
        source = (Path(__file__).parent.parent / "Source" / "RimGPTSpatialJson.cs").read_text(encoding="utf-8")
        self.assertIn("StateBuildingPriority(thing, map) != priority", source)
        self.assertIn("thing is Blueprint || thing is Frame", source)
        self.assertIn("home[thing.Position]", source)
        self.assertIn("thing.Faction == Faction.OfPlayer", source)
        operational = (Path(__file__).parent.parent / "Source" / "RimGPTOperationalJson.cs").read_text(encoding="utf-8")
        self.assertIn('RimGPTSpatialJson.WriteRoom(json, "room", RegionAndRoomQuery.GetRoom(bed), map, true)', operational)


if __name__ == "__main__":
    unittest.main()
