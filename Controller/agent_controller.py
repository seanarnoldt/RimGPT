import json
import time
from typing import Any

from openai import OpenAI

from bridge import CommandStatusTimeout, CommandStatusUnreachable, RimWorldBridge, RimWorldBridgeError
from tools import TOOLS, tool_call_to_bridge_command


SYSTEM_INSTRUCTIONS = """You are playing RimWorld through a restricted control interface.

You are the colony's strategic controller.

Your objective is to keep the colony alive, improve its long-term position, and eventually achieve the game's victory condition.

You may make decisions independently.

You currently have only a limited toolset. Do not assume you can perform actions that are not exposed as tools.

Treat the supplied RimWorld state as authoritative.

Do not invent pawn IDs, map coordinates, work types, resources, threats, or other game state.

Prefer reversible and low-risk actions when information is incomplete.

Do not repeatedly issue an action if the previous result indicates it has already succeeded.

Remember that resource counts may currently omit forbidden/unavailable starting supplies. Do not interpret a zero resource count as absolute proof that no such items physically exist on the map.

Because the current control surface is incomplete, it is acceptable to take no action and explain what additional capability would be needed."""


class AgentController:
    def __init__(
        self,
        bridge: RimWorldBridge,
        model: str,
        dry_run: bool = False,
        max_tool_rounds: int = 4,
    ) -> None:
        self.bridge = bridge
        self.model = model
        self.dry_run = dry_run
        self.max_tool_rounds = max_tool_rounds
        self.client = OpenAI()
        self.uncertain_commands: dict[str, dict[str, Any]] = {}

    def run_once(self) -> None:
        print("[STATE] Checking RimGPT bridge health")
        health = self.bridge.health()
        print(f"[STATE] Bridge health: {health.get('status')} ({health.get('bridge')})")

        state = self.bridge.get_state()
        print(f"[STATE] {summarize_state(state)}")

        print(f"[MODEL] Requesting one decision cycle from {self.model}")
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_INSTRUCTIONS,
            tools=TOOLS,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Review this RimWorld State API v1 snapshot and decide whether to use the "
                                "available tools. After any tool results, provide a concise final assessment.\n\n"
                                + json.dumps(state, separators=(",", ":"))
                            ),
                        }
                    ],
                }
            ],
        )

        final_response = self._handle_tool_rounds(response)
        assessment = getattr(final_response, "output_text", "") or collect_output_text(final_response)
        if assessment:
            print(f"[MODEL] Final assessment: {assessment.strip()}")
        else:
            print("[MODEL] Final assessment: no text returned")

    def _handle_tool_rounds(self, response: Any) -> Any:
        current = response
        for round_index in range(self.max_tool_rounds):
            tool_calls = get_function_calls(current)
            if not tool_calls:
                self._reconcile_uncertain_commands()
                return current

            print(f"[MODEL] Tool-call round {round_index + 1}: {len(tool_calls)} call(s)")
            outputs: list[dict[str, Any]] = []
            for call in tool_calls:
                outputs.append(self._execute_tool_call(call))

            outputs.extend(self._reconcile_uncertain_commands())
            post_action_state = self._fresh_state_message()

            print("[MODEL] Sending tool results and fresh state back to model")
            current = self.client.responses.create(
                model=self.model,
                instructions=SYSTEM_INSTRUCTIONS,
                tools=TOOLS,
                previous_response_id=current.id,
                input=outputs + [post_action_state],
            )

        self._reconcile_uncertain_commands()
        print("[ERROR] Reached max tool-call rounds; stopping to avoid an infinite loop")
        return current

    def _execute_tool_call(self, call: Any) -> dict[str, Any]:
        name = getattr(call, "name", "")
        call_id = getattr(call, "call_id", "")
        raw_arguments = getattr(call, "arguments", "{}")

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            result = {"success": False, "error": f"Invalid tool arguments JSON: {exc}"}
            print(f"[ERROR] {name}: {result['error']}")
            return function_output(call_id, result)

        print(f"[ACTION] {name}({format_arguments(arguments)})")

        try:
            bridge_command = tool_call_to_bridge_command(name, arguments)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
            print(f"[ERROR] {name}: {result['error']}")
            return function_output(call_id, result)

        if self.dry_run:
            result = {
                "success": True,
                "dryRun": True,
                "message": "Command was proposed but not executed because --dry-run is active.",
                "command": bridge_command,
            }
            print(f"[RESULT] dry-run proposed {bridge_command}")
            return function_output(call_id, result)

        started = time.monotonic()
        try:
            result = self.bridge.send_command_and_wait(bridge_command)
            elapsed = result.get("elapsedSeconds", round(time.monotonic() - started, 3))
            print(f"[RESULT] completed in {elapsed:.2f}s: {result}")
            return function_output(call_id, result)
        except CommandStatusTimeout as exc:
            command_id = exc.command_id
            self.uncertain_commands[command_id] = {
                "call_id": call_id,
                "command": bridge_command,
                "started_at": started,
                "reason": "still-queued",
            }
            result = {
                "commandId": command_id,
                "status": "queued",
                "success": None,
                "uncertain": True,
                "message": f"Command is still queued after {exc.elapsed:.2f}s and will be reconciled later.",
                "command": bridge_command,
            }
            print(f"[WARNING] command still queued after {exc.elapsed:.1f}s: {command_id}")
            return function_output(call_id, result)
        except CommandStatusUnreachable as exc:
            command_id = exc.command_id
            self.uncertain_commands[command_id] = {
                "call_id": call_id,
                "command": bridge_command,
                "started_at": started,
                "reason": "unknown-unreachable",
            }
            result = {
                "commandId": command_id,
                "status": "unknown",
                "success": None,
                "uncertain": True,
                "error": exc.error,
                "message": "Command was accepted, but status could not be verified. It will be reconciled later.",
                "command": bridge_command,
            }
            print(f"[WARNING] command status unknown/unreachable after {exc.elapsed:.1f}s: {command_id}")
            return function_output(call_id, result)
        except RimWorldBridgeError as exc:
            result = {"success": False, "error": str(exc), "command": bridge_command}
            print(f"[ERROR] {exc}")
            return function_output(call_id, result)

    def _reconcile_uncertain_commands(self) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for command_id, pending in list(self.uncertain_commands.items()):
            started_at = pending.get("started_at")
            try:
                status = self.bridge.reconcile_command(command_id, started_at=started_at)
            except RimWorldBridgeError as exc:
                elapsed = time.monotonic() - started_at if isinstance(started_at, float) else 0.0
                result = {
                    "commandId": command_id,
                    "status": "unknown",
                    "success": None,
                    "uncertain": True,
                    "error": str(exc),
                    "message": "Command status is still unreachable during reconciliation.",
                    "command": pending.get("command"),
                }
                print(f"[WARNING] command status still unknown/unreachable after {elapsed:.1f}s: {command_id}")
                updates.append(reconciliation_message(result))
                continue

            status_value = status.get("status")
            elapsed = status.get("elapsedSeconds", 0.0)
            if status_value == "completed":
                print(f"[RESULT] reconciled after {elapsed:.1f}s: {status}")
                status["reconciled"] = True
                updates.append(reconciliation_message(status))
                del self.uncertain_commands[command_id]
            elif status_value == "queued":
                status["success"] = None
                status["uncertain"] = True
                status["message"] = "Command is still queued during reconciliation."
                status["command"] = pending.get("command")
                print(f"[WARNING] command still queued after {elapsed:.1f}s: {command_id}")
                updates.append(reconciliation_message(status))
            else:
                status["success"] = None
                status["uncertain"] = True
                status["message"] = "Command returned an unknown status during reconciliation."
                status["command"] = pending.get("command")
                print(f"[WARNING] command status unknown after {elapsed:.1f}s: {command_id}")
                updates.append(reconciliation_message(status))

        return updates

    def _fresh_state_message(self) -> dict[str, Any]:
        try:
            state = self.bridge.get_state()
            print(f"[STATE] Post-action {summarize_state(state)}")
            text = (
                "Fresh authoritative RimWorld state after the attempted actions. "
                "Use this state, not only action acknowledgements, for your next assessment.\n\n"
                + json.dumps(state, separators=(",", ":"))
            )
        except RimWorldBridgeError as exc:
            print(f"[ERROR] Could not retrieve post-action state: {exc}")
            text = f"Fresh RimWorld state could not be retrieved after actions: {exc}"

        return {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }


def function_output(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(result, separators=(",", ":")),
    }


def reconciliation_message(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "Command reconciliation update:\n" + json.dumps(result, separators=(",", ":")),
            }
        ],
    }


def get_function_calls(response: Any) -> list[Any]:
    return [item for item in getattr(response, "output", []) if getattr(item, "type", None) == "function_call"]


def collect_output_text(response: Any) -> str:
    chunks: list[str] = []
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []):
            text = getattr(content, "text", None)
            if text:
                chunks.append(str(text))
    return "\n".join(chunks)


def summarize_state(state: dict[str, Any]) -> str:
    game = state.get("game", {})
    if not game.get("loaded"):
        return "No save/map loaded"

    colony = state.get("colony", {})
    colonists = state.get("colonists", [])
    threats = state.get("threats", [])
    resources = state.get("resources", {})
    food = resources.get("food", {}) if isinstance(resources.get("food"), dict) else {}
    names = ", ".join(str(p.get("name", p.get("id", "unknown"))) for p in colonists[:5])
    if len(colonists) > 5:
        names += ", ..."

    return (
        f"loaded={game.get('loaded')} paused={game.get('paused')} speed={game.get('speed')} "
        f"ticks={game.get('ticksGame')} colonists={colony.get('colonistCount')} "
        f"prisoners={colony.get('prisonerCount')} animals={colony.get('animalCount')} "
        f"threats={len(threats)} meals={food.get('meals')} nutrition={food.get('totalNutrition')} "
        f"colonistNames=[{names}]"
    )


def format_arguments(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in arguments.items())
