import json
from typing import Any

from openai import OpenAI

from bridge import RimWorldBridge, RimWorldBridgeError
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
                return current

            print(f"[MODEL] Tool-call round {round_index + 1}: {len(tool_calls)} call(s)")
            outputs: list[dict[str, Any]] = []
            for call in tool_calls:
                outputs.append(self._execute_tool_call(call))

            print("[MODEL] Sending tool results back to model")
            current = self.client.responses.create(
                model=self.model,
                instructions=SYSTEM_INSTRUCTIONS,
                tools=TOOLS,
                previous_response_id=current.id,
                input=outputs,
            )

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

        try:
            result = self.bridge.send_command_and_wait(bridge_command)
            print(f"[RESULT] {result}")
            return function_output(call_id, result)
        except RimWorldBridgeError as exc:
            result = {"success": False, "error": str(exc), "command": bridge_command}
            print(f"[ERROR] {exc}")
            return function_output(call_id, result)


def function_output(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(result, separators=(",", ":")),
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
