import copy
import unittest

from progress_tracking import authoritative_completion_for, build_progress_signals
from test_progress_verification_m10e import room
from test_state_backed_completion_m10h5 import growing_zone
from test_state_diff import base_state


def completion_for(state, objective):
    progress = build_progress_signals(copy.deepcopy(state), copy.deepcopy(state))
    return authoritative_completion_for({"objective": objective}, progress)


def planted_potatoes():
    return growing_zone(
        base_state(), planted=60, unsown=0, growing=60, harvestable=0,
        phase="planted", complete=True,
    )


def enclosed_shelter():
    state = base_state()
    state["operations"]["beds"][0]["room"] = room(enclosed=True)
    return state


class CompletionEvidenceSpecificityM10H51Tests(unittest.TestCase):
    def test_matching_specific_crop_is_satisfied(self):
        evidence = completion_for(planted_potatoes(), "Plant the potato field")
        self.assertIn("Plant_Potato", evidence[0])

    def test_unmatched_specific_crop_is_not_satisfied(self):
        self.assertEqual(completion_for(planted_potatoes(), "Plant a healroot field"), [])

    def test_generic_crop_field_can_use_aggregate_completion(self):
        evidence = completion_for(planted_potatoes(), "Establish crop fields")
        self.assertIn("planting complete", evidence[0])

    def test_generic_shelter_is_satisfied_by_enclosed_roofed_room(self):
        evidence = completion_for(enclosed_shelter(), "Create usable shelter")
        self.assertIn("enclosed substantially roofed room", evidence[0])

    def test_room_does_not_satisfy_cooler_or_heater(self):
        state = enclosed_shelter()
        self.assertEqual(completion_for(state, "Build cooler"), [])
        self.assertEqual(completion_for(state, "Build heater"), [])
        self.assertEqual(completion_for(state, "Build a cooler in the enclosed room"), [])

    def test_matching_specific_building_is_satisfied(self):
        evidence = completion_for(base_state(), "Build SimpleResearchBench")
        self.assertEqual(evidence, ["completed building present: SimpleResearchBench"])

    def test_unrelated_building_does_not_satisfy_specific_building(self):
        self.assertEqual(completion_for(base_state(), "Build HiTechResearchBench"), [])

    def test_global_bed_capacity_only_satisfies_aggregate_sleep_objective(self):
        state = enclosed_shelter()
        aggregate = completion_for(state, "Ensure every colonist has somewhere to sleep")
        self.assertIn("usable bed capacity", aggregate[0])
        self.assertEqual(completion_for(state, "Build a bed"), [])
        self.assertEqual(completion_for(state, "Build a cooler beside the beds"), [])

    def test_specific_zone_id_requires_matching_zone(self):
        state = planted_potatoes()
        self.assertTrue(completion_for(state, "Finish planting zone-1"))
        self.assertEqual(completion_for(state, "Finish planting zone-99"), [])


if __name__ == "__main__":
    unittest.main()
