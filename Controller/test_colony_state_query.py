import copy
import tempfile
import unittest
from pathlib import Path

from colony_state_query import ColonyStateQuery, ColonyStateQueryError, VALID_SECTIONS, serialized_chars
from state_store import StateStore
from test_state_diff import base_state


def rich_state(version=120):
    state = base_state(version=version)
    state["colonists"][0]["primaryEquipment"] = {"id": "Gun_1", "defName": "Gun_Revolver", "label": "revolver"}
    state["colonists"][0]["apparel"] = [{"id": "Jacket_1", "defName": "Apparel_Jacket", "label": "jacket"}]
    state["operations"]["worktables"][0]["bills"] = [
        {"id": "Bill_1", "recipeDef": "MakeMealSimple", "label": "Make simple meal", "repeatMode": "untilX", "targetCount": 10, "repeatCount": 1, "suspended": False}
    ]
    state["operations"]["worktables"].append(
        {"id": "Human_BillGiver", "defName": "Human", "label": "pawn bill giver", "position": {"x": 1, "z": 1}, "operational": True, "bills": []}
    )
    state["threats"] = [{"id": "Raider_1", "type": "pawn", "defName": "Human", "label": "raider", "faction": "Pirates", "position": {"x": 100, "z": 100}, "downed": False}]
    return state


class ColonyStateQueryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(Path(self.temporary.name), logger=lambda _: None)
        self.state = rich_state()
        self.store.update_current_state(self.state)
        self.query = ColonyStateQuery(self.store)

    def test_all_allowlisted_sections_are_available_and_bounded(self):
        for section in VALID_SECTIONS:
            with self.subTest(section=section):
                result = self.query.get(section)
                self.assertEqual(result["section"], section)
                self.assertEqual(result["snapshotVersion"], 120)
                self.assertLessEqual(serialized_chars(result), 12_000)

    def test_pawns_are_compact_and_work_is_separate(self):
        pawn = self.query.get("pawns")["data"][0]
        self.assertEqual(pawn["id"], "Pawn_A")
        self.assertNotIn("skills", pawn)
        work = self.query.get("work")["data"][0]
        self.assertEqual(work["id"], "Pawn_A")
        self.assertEqual(work["work"][0]["defName"], "Research")

    def test_resources_research_and_environment_are_authoritative_aggregates(self):
        resources = self.query.get("resources")["data"]
        self.assertEqual(resources["available"]["wood"], 180)
        research = self.query.get("research")["data"]
        self.assertEqual(research["current"]["defName"], "Electricity")
        environment = self.query.get("environment")["data"]
        self.assertEqual(environment["environment"]["weather"], "Clear")

    def test_buildings_zones_and_v3_operations_are_structured(self):
        self.assertEqual(self.query.get("buildings")["data"]["entries"][0]["id"], "Building_Bench")
        self.assertEqual(self.query.get("zones")["data"]["zones"][0]["id"], "zone-1")
        self.assertEqual(self.query.get("equipment")["data"]["assigned"][0]["primary"]["id"], "Gun_1")
        self.assertEqual(self.query.get("apparel")["data"]["wornByPawn"][0]["worn"][0]["id"], "Jacket_1")
        self.assertEqual(self.query.get("beds")["data"][0]["id"], "Bed_1")
        self.assertEqual(self.query.get("bills")["data"][0]["bills"][0]["id"], "Bill_1")
        self.assertEqual(self.query.get("power")["data"]["power"]["networks"][0]["id"], "powerNet-0")
        self.assertEqual(self.query.get("threats")["data"][0]["id"], "Raider_1")

    def test_worktables_filter_nonbuilding_bill_givers(self):
        result = self.query.get("worktables")["data"]
        self.assertEqual([item["id"] for item in result["entries"]], ["Building_Bench"])
        self.assertEqual(result["filteredNonBuildingBillGivers"], 1)

    def test_invalid_section_is_rejected_with_allowlist(self):
        with self.assertRaises(ColonyStateQueryError) as caught:
            self.query.get("operations.secret")
        self.assertIn("Valid sections: pawns, work", str(caught.exception))

    def test_large_section_truncates_within_explicit_bound(self):
        state = rich_state(version=121)
        state["buildings"] = [
            {"id": f"Building_{index}", "type": "building", "defName": f"ModBuilding_{index}", "label": "x" * 300, "position": {"x": index, "z": index}}
            for index in range(500)
        ]
        self.store.update_current_state(state)
        query = ColonyStateQuery(self.store, max_section_chars=3000, max_entries=200)
        result = query.get("buildings")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(serialized_chars(result), 3000)

    def test_query_reads_latest_current_state_not_baseline_or_memory(self):
        self.store.set_decision_baseline(self.state)
        self.store.apply_memory_update({"unresolvedProblems": ["Wood is definitely zero"]})
        newer = copy.deepcopy(self.state)
        newer["snapshot"]["version"] = 121
        newer["resources"]["available"]["wood"] = 777
        self.store.update_current_state(newer)
        result = self.query.get("resources")
        self.assertEqual(result["snapshotVersion"], 121)
        self.assertEqual(result["data"]["available"]["wood"], 777)


if __name__ == "__main__":
    unittest.main()
