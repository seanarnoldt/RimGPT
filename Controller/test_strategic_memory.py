import json
import tempfile
import unittest
from pathlib import Path

from strategic_memory import DEFAULT_MAX_MEMORY_CHARS, MAX_ASSESSMENT_CHARS, memory_telemetry
from state_store import StateStore


def snapshot(lineage="lineage-a", version=1, schema=2, **changes):
    state = {
        "schemaVersion": schema,
        "snapshot": {"version": version, "ticksGame": version * 60},
        "game": {"loaded": True, "colonyLineageId": lineage, "currentMapId": "map-7"},
        "colonists": [{"id": "Pawn_1", "name": "Ava", "primaryEquipment": None}],
        "buildings": [],
        "map": {"zones": []},
    }
    state.update(changes)
    return state


class StrategicMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "state"
        self.logs = []

    def tearDown(self):
        self.temporary.cleanup()

    def store(self):
        return StateStore(self.root, logger=self.logs.append)

    def activate(self, store, state=None):
        state = state or snapshot()
        store.update_current_state(state)
        return state

    def test_fresh_memory_is_clean_and_persistence_survives_restart(self):
        first = self.store()
        state = self.activate(first)
        memory = first.get_memory()
        self.assertEqual(memory["currentGoals"], [])
        self.assertEqual(memory["version"], 1)
        self.assertFalse((first.colony_directory / "memory.json").exists())

        first.apply_memory_update({"currentGoals": ["Finish the starter shelter"]})
        restarted = self.store()
        restarted.update_current_state(state)
        self.assertEqual(restarted.get_memory()["currentGoals"], ["Finish the starter shelter"])
        self.assertTrue((restarted.colony_directory / "memory.json").is_file())

    def test_colony_isolation_and_switching_back_preserve_each_memory(self):
        store = self.store()
        colony_a = self.activate(store, snapshot("lineage-a", version=1))
        store.apply_memory_update({"currentGoals": ["Build freezer"]})
        store.update_current_state(snapshot("lineage-b", version=2))
        self.assertEqual(store.get_memory()["currentGoals"], [])
        store.apply_memory_update({"currentGoals": ["Research batteries"]})

        restarted = self.store()
        restarted.update_current_state(colony_a)
        self.assertEqual(restarted.get_memory()["currentGoals"], ["Build freezer"])
        restarted.update_current_state(snapshot("lineage-b", version=2))
        self.assertEqual(restarted.get_memory()["currentGoals"], ["Research batteries"])

    def test_text_merge_dedupes_but_keeps_distinct_goals_and_priorities(self):
        store = self.store()
        self.activate(store)
        memory = store.apply_memory_update(
            {
                "currentGoals": ["Build freezer", " build   freezer ", "Research Batteries"],
                "nextPriorities": ["Equip colonists", " equip colonists "],
            }
        )
        self.assertEqual(memory["currentGoals"], ["Build freezer", "Research Batteries"])
        self.assertEqual(memory["nextPriorities"], ["Equip colonists"])

    def test_decisions_and_problems_resolve_idempotently(self):
        store = self.store()
        self.activate(store)
        store.apply_memory_update(
            {
                "decisionsToRemember": ["Main sleeping area will be north of stockpile"],
                "unresolvedProblems": ["No research bench"],
            }
        )
        memory = store.apply_memory_update(
            {
                "resolvedDecisions": [" main sleeping area will be north of stockpile "],
                "resolvedProblems": ["no research bench", "does not exist"],
            }
        )
        self.assertEqual(memory["decisions"], [])
        self.assertEqual(memory["unresolvedProblems"], [])
        self.assertEqual(store.apply_memory_update({"resolvedProblems": ["no research bench"]})["unresolvedProblems"], [])

    def test_assessment_and_item_limits_are_bounded(self):
        store = self.store()
        self.activate(store)
        memory = store.apply_memory_update(
            {
                "decisionsToRemember": [f"Decision {index}: " + ("x" * 500) for index in range(100)],
                "assessment": "a" * 2_000,
            }
        )
        self.assertEqual(len(memory["decisions"]), 24)
        self.assertLessEqual(len(memory["lastAssessment"]), MAX_ASSESSMENT_CHARS)
        self.assertLessEqual(memory_telemetry(memory)["chars"], DEFAULT_MAX_MEMORY_CHARS)
        self.assertTrue(any("bounded" in line for line in self.logs if line.startswith("[MEMORY] applied")))
        self.assertEqual(store.apply_memory_update({"assessment": "Fresh assessment"})["lastAssessment"], "Fresh assessment")

    def test_pawn_roles_use_stable_ids_dedupe_and_reconcile_missing_pawns(self):
        store = self.store()
        state = self.activate(store)
        memory = store.apply_memory_update(
            {"pawnRoles": {"Pawn_1": {"name": "Ava", "roles": ["construction", " construction ", "mining"]}}}
        )
        self.assertEqual(memory["pawnRoles"]["Pawn_1"]["roles"], ["construction", "mining"])
        store.update_current_state(snapshot(version=2, colonists=[]))
        memory = store.reconcile_memory()
        self.assertEqual(memory["pawnRoles"], {})
        self.assertEqual(store.get_decision_baseline(), None)
        self.assertEqual(state["colonists"][0]["id"], "Pawn_1")

    def test_locations_are_compact_deduped_and_limited(self):
        store = self.store()
        self.activate(store)
        locations = [
            {"label": "mainBase", "x": 12, "z": 8, "mapId": "map-7", "purpose": "shelter"},
            {"label": " mainbase ", "x": 12, "z": 8, "mapId": "map-7"},
        ]
        locations.extend({"label": f"site-{index}", "x": index, "z": index, "mapId": "map-7"} for index in range(20))
        memory = store.apply_memory_update({"importantLocations": locations})
        self.assertEqual(memory["importantLocations"][0]["label"], "mainBase")
        self.assertEqual(len(memory["importantLocations"]), 12)

    def test_corrupt_or_incompatible_memory_is_quarantined_and_reinitialized(self):
        store = self.store()
        state = self.activate(store)
        store.apply_memory_update({"currentGoals": ["Build freezer"]})
        path = store.colony_directory / "memory.json"
        path.write_text("{broken", encoding="utf-8")
        restarted = self.store()
        restarted.update_current_state(state)
        self.assertEqual(restarted.get_memory()["currentGoals"], [])
        self.assertTrue(list(path.parent.glob("memory.json.invalid-*")))
        self.assertTrue(any("Invalid persisted strategic memory" in line for line in self.logs))

        restarted.apply_memory_update({"currentGoals": ["Build freezer"]})
        invalid = restarted.colony_directory / "memory.json"
        document = json.loads(invalid.read_text(encoding="utf-8"))
        document["version"] = 999
        invalid.write_text(json.dumps(document), encoding="utf-8")
        third = self.store()
        third.update_current_state(state)
        self.assertEqual(third.get_memory()["currentGoals"], [])

    def test_state_schema_change_keeps_memory_and_memory_never_changes_baseline(self):
        store = self.store()
        baseline = self.activate(store, snapshot(version=1, schema=2))
        store.set_decision_baseline(baseline)
        store.apply_memory_update({"currentGoals": ["Build freezer"]})
        self.assertEqual(store.get_decision_baseline()["snapshot"]["version"], 1)
        store.update_current_state(snapshot(version=2, schema=3))
        self.assertEqual(store.get_memory()["currentGoals"], ["Build freezer"])
        self.assertIsNone(store.get_decision_baseline())
        restarted = self.store()
        restarted.update_current_state(snapshot(version=2, schema=3))
        self.assertEqual(restarted.get_memory()["currentGoals"], ["Build freezer"])

    def test_explicit_live_reconciliation_removes_only_proven_obsolete_items(self):
        store = self.store()
        live = snapshot(
            buildings=[{"id": "Bench_1", "type": "building", "defName": "SimpleResearchBench"}],
            colonists=[{"id": "Pawn_1", "name": "Ava", "primaryEquipment": {"id": "Gun_1", "defName": "Gun_Pistol"}}],
            map={"zones": [{"id": "Zone_1", "type": "growing", "plantDef": "Plant_Rice", "cellCount": 20}]},
        )
        self.activate(store, live)
        store.apply_memory_update(
            {
                "currentGoals": ["Build rice growing zone", "Establish refrigeration"],
                "unresolvedProblems": ["No research bench", "Colonists lack weapons", "No refrigeration"],
                "pawnRoles": {"Pawn_1": {"name": "Ava", "roles": ["research"]}},
            }
        )
        memory = store.reconcile_memory()
        self.assertEqual(memory["currentGoals"], ["Establish refrigeration"])
        self.assertEqual(memory["unresolvedProblems"], ["No refrigeration"])
        self.assertIn("Pawn_1", memory["pawnRoles"])

    def test_new_authoritative_state_reconciles_memory_automatically(self):
        store = self.store()
        self.activate(store)
        store.apply_memory_update({"unresolvedProblems": ["No research bench"]})
        store.update_current_state(
            snapshot(version=2, buildings=[{"id": "Bench_1", "type": "building", "defName": "SimpleResearchBench"}])
        )
        self.assertEqual(store.get_memory()["unresolvedProblems"], [])

    def test_ambiguous_state_does_not_guess_resolution_and_memory_writes_are_atomic(self):
        store = self.store()
        self.activate(store)
        store.apply_memory_update({"currentGoals": ["Establish refrigeration"], "unresolvedProblems": ["No reliable power source"]})
        memory = store.reconcile_memory()
        self.assertEqual(memory["currentGoals"], ["Establish refrigeration"])
        self.assertEqual(memory["unresolvedProblems"], ["No reliable power source"])
        path = store.colony_directory / "memory.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), memory)
        self.assertFalse(list(path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
