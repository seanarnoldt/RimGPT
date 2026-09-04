import unittest

from agent_controller import AgentController, snapshot_version


class FreshStateBridge:
    def __init__(self):
        self.wait_calls = []

    def wait_for_state_after(self, after_version, timeout_ms=2000):
        self.wait_calls.append((after_version, timeout_ms))
        return {
            "schemaVersion": 2,
            "snapshot": {"version": after_version + 1, "capturedAtUtc": "2026-01-01T00:00:00Z"},
            "game": {"loaded": True, "paused": True, "speed": 0, "ticksGame": 10},
            "colony": {"colonistCount": 1, "prisonerCount": 0, "animalCount": 0},
            "colonists": [],
            "threats": [],
            "resources": {"food": {"meals": 0, "totalNutrition": 0}},
        }


class StaleStateBridge:
    def get_state(self):
        return {"schemaVersion": 2, "snapshot": {"version": 7}, "game": {"loaded": True}, "colonists": [], "threats": []}

    def wait_for_state_after(self, after_version, timeout_ms=2000):
        return {
            "fresh": False,
            "afterVersion": after_version,
            "currentVersion": after_version,
            "state": {
                "schemaVersion": 2,
                "snapshot": {"version": after_version},
                "game": {"loaded": True, "paused": False, "speed": 1},
                "colony": {"colonistCount": 1},
                "colonists": [],
                "threats": [],
                "resources": {"food": {}},
            },
        }


class StateBarrierTests(unittest.TestCase):
    def test_snapshot_version_parses_missing_and_present_values(self):
        self.assertEqual(snapshot_version(None), 0)
        self.assertEqual(snapshot_version({}), 0)
        self.assertEqual(snapshot_version({"snapshot": {"version": "42"}}), 42)

    def test_fresh_state_message_waits_for_newer_snapshot_after_writes(self):
        controller = object.__new__(AgentController)
        controller.bridge = FreshStateBridge()
        controller.current_state = {"snapshot": {"version": 3}}

        message = controller._fresh_state_message(after_version=3, require_newer=True)

        self.assertEqual(controller.bridge.wait_calls, [(3, 3000)])
        self.assertEqual(snapshot_version(controller.current_state), 4)
        self.assertIn("Fresh authoritative", message["content"][0]["text"])

    def test_stale_wait_result_is_not_labeled_authoritative(self):
        controller = object.__new__(AgentController)
        controller.bridge = StaleStateBridge()
        controller.current_state = {"snapshot": {"version": 7}}

        message = controller._fresh_state_message(after_version=7, require_newer=True)

        self.assertEqual(snapshot_version(controller.current_state), 7)
        self.assertIn("may be stale", message["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
