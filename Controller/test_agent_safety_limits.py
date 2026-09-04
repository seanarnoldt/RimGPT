import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_controller import (
    DEFAULT_MAX_TOOL_ROUNDS,
    AgentController,
    failed_call_signature,
)


class SafetyLimitBridge:
    def __init__(self):
        self.submitted_commands = []

    def submit_command(self, command):
        self.submitted_commands.append(dict(command))
        return {
            "commandId": f"cmd-{len(self.submitted_commands)}",
            "startedAt": 0.0,
            "submitElapsedSeconds": 0.0,
        }

    def wait_for_commands(self, submissions):
        return {}


class AgentSafetyLimitTests(unittest.TestCase):
    def test_default_max_tool_rounds_is_eight(self):
        controller = object.__new__(AgentController)
        with patch("agent_controller.OpenAI"):
            AgentController.__init__(controller, bridge=SafetyLimitBridge(), model="test")

        self.assertEqual(DEFAULT_MAX_TOOL_ROUNDS, 8)
        self.assertEqual(controller.max_tool_rounds, 8)

    def test_write_command_limit_blocks_extra_submissions(self):
        controller = object.__new__(AgentController)
        controller.bridge = SafetyLimitBridge()
        controller.dry_run = False
        controller.uncertain_commands = {}
        controller.max_write_commands = 1
        controller.write_commands = 0
        controller.failed_call_counts = {}
        controller.repeated_failed_call_limit = 3
        controller.termination_reason = None

        calls = [
            SimpleNamespace(name="set_speed", call_id="call-1", arguments=json.dumps({"speed": 1})),
            SimpleNamespace(name="set_speed", call_id="call-2", arguments=json.dumps({"speed": 2})),
        ]

        outputs = controller._execute_tool_call_batch(calls)

        self.assertEqual(len(controller.bridge.submitted_commands), 1)
        self.assertEqual(controller.termination_reason, "max write commands reached (1)")
        self.assertEqual(json.loads(outputs[1]["output"])["success"], False)

    def test_failed_call_signature_normalizes_argument_order(self):
        first = failed_call_signature("move", {"z": 2, "x": 1}, {"error": " Bad   Cell "})
        second = failed_call_signature("move", {"x": 1, "z": 2}, {"error": "bad cell"})

        self.assertEqual(first, second)

    def test_repeated_failed_call_sets_termination_reason(self):
        controller = object.__new__(AgentController)
        controller.failed_call_counts = {}
        controller.repeated_failed_call_limit = 2
        controller.termination_reason = None

        result = {"success": False, "error": "same failure"}
        controller._record_failed_call("move", {"x": 1, "z": 2}, result)
        controller._record_failed_call("move", {"z": 2, "x": 1}, result)

        self.assertIn("repeated failed call detected", controller.termination_reason)


if __name__ == "__main__":
    unittest.main()
