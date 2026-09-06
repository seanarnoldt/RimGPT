import json
import tempfile
import unittest
from pathlib import Path

from state_diff import StateDiff
from state_store import PERSISTENCE_FORMAT_VERSION, StateStore, identity_from_state


def snapshot(lineage="lineage-a", version=1, schema=2, ticks=100, **changes):
    state = {
        "schemaVersion": schema,
        "snapshot": {"version": version, "ticksGame": ticks, "capturedAtUtc": "2026-01-01T00:00:00Z"},
        "game": {"loaded": True, "colonyLineageId": lineage, "currentMapId": "map-7"},
        "resources": {"steel": 100},
        "colonists": [{"id": "Pawn_1", "needs": {"food": 0.5}, "health": {"pain": 0.0}}],
    }
    state.update(changes)
    return state


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "state"
        self.logs = []

    def tearDown(self):
        self.temporary.cleanup()

    def store(self):
        return StateStore(self.root, logger=self.logs.append)

    def test_fresh_store_initializes_cleanly(self):
        store = self.store()
        self.assertIsNone(store.get_current_state())
        self.assertIsNone(store.get_decision_baseline())
        self.assertFalse(self.root.exists())

    def test_persists_and_loads_current_snapshot_after_restart(self):
        state = snapshot(version=4)
        first = self.store()
        self.assertTrue(first.update_current_state(state))
        second = self.store()
        self.assertFalse(second.update_current_state(state))
        self.assertEqual(second.get_current_state(), state)
        self.assertTrue((second.colony_directory / "current_state.json").is_file())
        self.assertTrue((second.colony_directory / "metadata.json").is_file())

    def test_colonies_use_separate_safe_deterministic_directories(self):
        first = self.store()
        first.update_current_state(snapshot("lineage-a"))
        directory_a = first.colony_directory
        first.update_current_state(snapshot("lineage-b"))
        directory_b = first.colony_directory

        self.assertNotEqual(directory_a, directory_b)
        self.assertRegex(directory_a.name, r"^[0-9a-f]{24}$")
        self.assertTrue((directory_a / "current_state.json").exists())
        self.assertTrue((directory_b / "current_state.json").exists())

    def test_switching_back_finds_original_colony_without_deleting_it(self):
        store = self.store()
        colony_a = snapshot("lineage-a", version=4)
        colony_b = snapshot("lineage-b", version=8)
        store.update_current_state(colony_a)
        directory_a = store.colony_directory
        store.update_current_state(colony_b)
        self.assertTrue((directory_a / "current_state.json").exists())

        restarted = self.store()
        restarted.update_current_state(colony_a)
        self.assertEqual(restarted.get_current_state(), colony_a)
        self.assertEqual(restarted.colony_directory, directory_a)

    def test_malformed_current_state_is_rejected_safely(self):
        store = self.store()
        state = snapshot()
        store.update_current_state(state)
        path = store.colony_directory / "current_state.json"
        path.write_text("{broken", encoding="utf-8")

        restarted = self.store()
        restarted.update_current_state(snapshot(version=2))
        self.assertEqual(restarted.get_current_state()["snapshot"]["version"], 2)
        self.assertTrue(list(path.parent.glob("current_state.json.invalid-*")))

    def test_malformed_metadata_is_rejected_safely(self):
        store = self.store()
        store.update_current_state(snapshot())
        path = store.colony_directory / "metadata.json"
        path.write_text("[]", encoding="utf-8")

        restarted = self.store()
        restarted.update_current_state(snapshot(version=2))
        self.assertEqual(restarted.get_current_state()["snapshot"]["version"], 2)
        self.assertTrue(list(path.parent.glob("metadata.json.invalid-*")))

    def test_unsupported_persistence_format_is_rejected_safely(self):
        store = self.store()
        store.update_current_state(snapshot())
        path = store.colony_directory / "metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["persistenceFormatVersion"], PERSISTENCE_FORMAT_VERSION)
        metadata["persistenceFormatVersion"] = 999
        path.write_text(json.dumps(metadata), encoding="utf-8")

        restarted = self.store()
        restarted.update_current_state(snapshot(version=2))
        self.assertEqual(restarted.get_current_state()["snapshot"]["version"], 2)

    def test_schema_change_invalidates_decision_baseline(self):
        store = self.store()
        state = snapshot(version=4, schema=2)
        store.update_current_state(state)
        store.set_decision_baseline(state)
        self.assertIsNotNone(store.get_decision_baseline())

        store.update_current_state(snapshot(version=5, schema=3))
        self.assertIsNone(store.get_decision_baseline())
        self.assertFalse((store.colony_directory / "decision_baseline.json").exists())

    def test_current_updates_do_not_advance_decision_baseline(self):
        store = self.store()
        baseline = snapshot(version=1)
        store.update_current_state(baseline)
        store.set_decision_baseline(baseline)
        store.update_current_state(snapshot(version=2))

        self.assertEqual(store.get_decision_baseline()["snapshot"]["version"], 1)
        store.clear_decision_baseline()
        self.assertIsNone(store.get_decision_baseline())

    def test_older_snapshot_does_not_overwrite_newer_and_same_is_idempotent(self):
        store = self.store()
        newer = snapshot(version=10, ticks=500)
        store.update_current_state(newer)
        self.assertFalse(store.update_current_state(snapshot(version=10, ticks=501)))
        self.assertFalse(store.update_current_state(snapshot(version=9, ticks=499)))
        self.assertEqual(store.get_current_state()["snapshot"]["version"], 10)

    def test_persisted_bridge_counter_reset_keeps_compatible_baseline(self):
        persisted = snapshot(version=120, ticks=1_000)
        first = self.store()
        first.update_current_state(persisted)
        first.set_decision_baseline(persisted)

        restarted = self.store()
        reset_snapshot = snapshot(version=1, ticks=1_010)
        self.assertTrue(restarted.update_current_state(reset_snapshot))
        self.assertEqual(restarted.get_decision_baseline()["snapshot"]["version"], 120)
        delta = StateDiff.compare(restarted.get_decision_baseline(), restarted.get_current_state())
        self.assertFalse(delta.get("bootstrapRequired", False))
        self.assertTrue(delta["snapshotStreamReset"])
        self.assertTrue(any("recognized bridge snapshot counter reset" in line for line in self.logs))

    def test_ambiguous_persisted_counter_reset_still_invalidates_baseline(self):
        persisted = snapshot(version=120, ticks=1_000)
        first = self.store()
        first.update_current_state(persisted)
        first.set_decision_baseline(persisted)

        restarted = self.store()
        self.assertTrue(restarted.update_current_state(snapshot(version=1, ticks=999)))
        self.assertIsNone(restarted.get_decision_baseline())
        self.assertTrue(any("snapshot lineage reset or ambiguous" in line for line in self.logs))

    def test_different_colony_after_restart_still_starts_fresh(self):
        first = self.store()
        first.update_current_state(snapshot("lineage-a", version=120, ticks=1_000))
        first.set_decision_baseline(snapshot("lineage-a", version=120, ticks=1_000))

        restarted = self.store()
        self.assertTrue(restarted.update_current_state(snapshot("lineage-b", version=1, ticks=1_010)))
        self.assertIsNone(restarted.get_decision_baseline())

    def test_normal_atomic_persistence_leaves_valid_json(self):
        store = self.store()
        state = snapshot(version=6)
        store.update_current_state(state)
        path = store.colony_directory / "current_state.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_identity_ignores_volatile_colony_data_and_membership(self):
        baseline = snapshot()
        changed = snapshot(
            version=99,
            ticks=99999,
            resources={"steel": 0},
            colonists=[
                {"id": "Pawn_1", "needs": {"food": 0.01}, "health": {"pain": 0.9}},
                {"id": "Pawn_2", "needs": {"food": 0.8}, "health": {"pain": 0.0}},
            ],
        )
        self.assertEqual(identity_from_state(baseline).key, identity_from_state(changed).key)
        self.assertNotEqual(identity_from_state(baseline).key, identity_from_state(snapshot("lineage-b")).key)


if __name__ == "__main__":
    unittest.main()
