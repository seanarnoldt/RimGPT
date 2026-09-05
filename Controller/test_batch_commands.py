import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_controller import AgentController
from bridge import CommandStatusUnreachable, RimWorldBridge, RimWorldBridgeError


class PollingBridge(RimWorldBridge):
    def __init__(self, statuses):
        self.statuses = {command_id: list(values) for command_id, values in statuses.items()}

    def get_command_status(self, command_id):
        values = self.statuses[command_id]
        if len(values) > 1:
            return values.pop(0)
        return values[0]


class SubmitFailureBridge(RimWorldBridge):
    def __init__(self):
        self.sent_command = None
        self.status_queries = []

    def send_command(self, command):
        self.sent_command = command
        raise RimWorldBridgeError("POST timed out")

    def get_command_status(self, command_id):
        self.status_queries.append(command_id)
        return {"commandId": command_id, "status": "queued"}


class BatchControllerBridge:
    def __init__(self):
        self.submitted_commands = []
        self.wait_batches = []

    def submit_command(self, command):
        command_id = f"cmd-{len(self.submitted_commands) + 1}"
        self.submitted_commands.append(dict(command))
        return {
            "accepted": True,
            "commandId": command_id,
            "command": dict(command),
            "startedAt": 100.0 + len(self.submitted_commands),
            "submitElapsedSeconds": 0.01,
        }

    def wait_for_commands(self, submissions):
        self.wait_batches.append([item["commandId"] for item in submissions])
        return {
            item["commandId"]: {
                "commandId": item["commandId"],
                "status": "completed",
                "success": True,
                "message": "ok",
                "elapsedSeconds": 0.08,
            }
            for item in submissions
        }


class BatchCommandTests(unittest.TestCase):
    def test_wait_for_commands_polls_pending_commands_together(self):
        bridge = PollingBridge(
            {
                "a": [
                    {"commandId": "a", "status": "queued"},
                    {"commandId": "a", "status": "completed", "success": True, "message": "done a"},
                ],
                "b": [
                    {"commandId": "b", "status": "queued"},
                    {"commandId": "b", "status": "completed", "success": True, "message": "done b"},
                ],
            }
        )

        with patch("bridge.time.monotonic", side_effect=[0.0, 0.1, 0.1, 0.1, 0.2, 0.2, 0.2]):
            with patch("bridge.time.sleep"):
                results = bridge.wait_for_commands(
                    [
                        {"commandId": "a", "startedAt": 0.0},
                        {"commandId": "b", "startedAt": 0.0},
                    ],
                    timeout=5.0,
                )

        self.assertEqual(results["a"]["status"], "completed")
        self.assertEqual(results["b"]["status"], "completed")

    def test_wait_for_commands_returns_uncertain_queued_after_timeout(self):
        bridge = PollingBridge({"a": [{"commandId": "a", "status": "queued"}]})

        with patch("bridge.time.monotonic", side_effect=[0.0, 6.0, 6.1, 6.1]):
            results = bridge.wait_for_commands([{"commandId": "a", "startedAt": 0.0}], timeout=5.0)

        self.assertEqual(results["a"]["status"], "queued")
        self.assertIsNone(results["a"]["success"])
        self.assertTrue(results["a"]["uncertain"])

    def test_submit_command_reconciles_client_generated_id_after_post_timeout(self):
        bridge = SubmitFailureBridge()

        with patch("bridge.uuid.uuid4", return_value=SimpleNamespace(hex="clientid")):
            submission = bridge.submit_command({"command": "setSpeed", "speed": 1})

        self.assertEqual(bridge.sent_command["commandId"], "clientid")
        self.assertEqual(bridge.status_queries, ["clientid"])
        self.assertEqual(submission["commandId"], "clientid")
        self.assertTrue(submission["submitUncertain"])

    def test_submit_command_reports_unreachable_when_post_and_reconcile_fail(self):
        bridge = SubmitFailureBridge()

        def unreachable(_command_id):
            raise RimWorldBridgeError("GET failed")

        bridge.get_command_status = unreachable

        with patch("bridge.uuid.uuid4", return_value=SimpleNamespace(hex="clientid")):
            with self.assertRaises(CommandStatusUnreachable) as caught:
                bridge.submit_command({"command": "setSpeed", "speed": 1})

        self.assertEqual(caught.exception.command_id, "clientid")

    def test_agent_controller_submits_full_tool_round_before_waiting(self):
        controller = object.__new__(AgentController)
        controller.bridge = BatchControllerBridge()
        controller.dry_run = False
        controller.uncertain_commands = {}

        calls = [
            SimpleNamespace(
                type="function_call",
                name="set_speed",
                call_id="call-1",
                arguments=json.dumps({"speed": 1}),
            ),
            SimpleNamespace(
                type="function_call",
                name="draft",
                call_id="call-2",
                arguments=json.dumps({"pawn_id": "Thing_Human123"}),
            ),
        ]

        outputs = controller._execute_tool_call_batch(calls)

        self.assertEqual(controller.bridge.submitted_commands[0]["command"], "setSpeed")
        self.assertEqual(controller.bridge.submitted_commands[1]["command"], "draft")
        self.assertEqual(controller.bridge.wait_batches, [["cmd-1", "cmd-2"]])
        self.assertEqual([output["call_id"] for output in outputs], ["call-1", "call-2"])
        first_result = json.loads(outputs[0]["output"])
        second_result = json.loads(outputs[1]["output"])
        self.assertEqual(first_result, {"success": True, "speed": 1})
        self.assertEqual(
            second_result,
            {"success": True, "pawnId": "Thing_Human123", "drafted": True},
        )
        self.assertEqual(controller.raw_tool_results[0]["result"]["command"]["command"], "setSpeed")
        self.assertEqual(controller.raw_tool_results[1]["result"]["command"]["command"], "draft")


if __name__ == "__main__":
    unittest.main()
