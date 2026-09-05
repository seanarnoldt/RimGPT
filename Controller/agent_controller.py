import json
import time
from typing import Any

from openai import OpenAI

from bridge import CommandStatusUnreachable, RimWorldBridge, RimWorldBridgeError
from context_telemetry import (
    DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST,
    DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE,
    ModelContextLimitError,
    ModelRequestLimitError,
    Pricing,
    extract_usage,
    measure_context,
    serialized_chars,
)
from state_store import StateStore, StateStoreError
from tools import TOOLS, convert_blueprint_placements, is_read_only_tool, tool_call_to_bridge_command

DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_MAX_TOTAL_TOOL_CALLS = 100
DEFAULT_MAX_WRITE_COMMANDS = 75
DEFAULT_REPEATED_FAILED_CALL_LIMIT = 3


SYSTEM_INSTRUCTIONS = """You are playing RimWorld through a restricted control interface.

You are the colony's strategic controller.

Your objective is to keep the colony alive, improve its long-term position, and eventually achieve the game's victory condition.

You may make decisions independently.

You currently have only a limited toolset. Do not assume you can perform actions that are not exposed as tools.

You may now allow starting supplies, choose research, designate visible mining/cutting/harvesting/hunting targets, and request unambiguous prioritized hauling.

You may inspect bounded visible map regions, create stockpile and growing zones, place construction blueprints, cancel player orders, and designate visible player structures for deconstruction.

Treat the supplied RimWorld state as authoritative.

Do not invent pawn IDs, map coordinates, work types, resources, threats, or other game state.

Use colonists[].work as the authoritative work capability and priority view. Never assign work if capable=false. Do not assign a work priority if the current priority already equals the desired value. Repeat set_work_priority only if a fresh authoritative state shows it did not persist.

Do not invent buildDef, stuffDef, plantDef, or zone IDs. Use list_build_options, get_build_info, list_growable_plants, and the supplied state when exact defs or IDs are uncertain.

Inspect relevant map regions before committing major construction, growing zones, or storage zones.

Growing zones require normal RimWorld zone validity, not only fertile terrain. Prefer contiguous cells where inspect_map reports canCreateGrowingZone=true, use check_zone_placement before creating farms, and pass minimum_valid_cells to create_growing_zone to avoid accidental one- or two-cell farms.

Prefer reversible and low-risk actions when information is incomplete.

Do not repeatedly issue an action if the previous result indicates it has already succeeded.

Remember that top-level resource scalar counts represent currently available supplies. Check resources.forbidden, resources.totalVisible, and mapThings.forbidden before concluding supplies do not exist on the visible map.

Use allow_all near scenario start when visible starting supplies are forbidden and need to become available.

For resources, distinguish currently available supplies from visible forbidden supplies. A zero available count does not mean there are no visible forbidden supplies.

Designations and movement use current-map RimWorld x/z coordinates only. Do not interact with hidden or fogged information.

Blueprint placement only creates normal construction blueprints; colonists still need resources, access, work priorities, and time to build them.

Terrain support matters for buildings. Use inspect_map terrain affordances and build option requiredTerrainAffordance before placing blueprints.

Before placing a large construction batch, preferably call check_build_placements with the planned placements. If several cells fail validation, adjust the plan instead of repeatedly attempting the same cells.

Do not assume every visually open cell can support every structure.

Avoid building steel walls at game start unless there is a specific strategic reason. Wood is generally less valuable as a long-term material, but steel is strategically important for machinery and early infrastructure.

Prefer compact, practical early colony layouts. At game start prioritize immediate survival: supplies, shelter, food, beds, basic storage, research, and power as appropriate. Do not overbuild when resources are scarce.

prioritize_job is intentionally conservative and may fail when a normal player right-click action is ambiguous; treat that as a signal to use a narrower available tool or explain what capability is missing.

Because the current control surface is incomplete, it is acceptable to take no action and explain what additional capability would be needed."""


class AgentController:
    def __init__(
        self,
        bridge: RimWorldBridge,
        model: str,
        dry_run: bool = False,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        max_total_tool_calls: int = DEFAULT_MAX_TOTAL_TOOL_CALLS,
        max_write_commands: int = DEFAULT_MAX_WRITE_COMMANDS,
        repeated_failed_call_limit: int = DEFAULT_REPEATED_FAILED_CALL_LIMIT,
        max_input_tokens_per_request: int = DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST,
        max_model_requests_per_cycle: int = DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE,
        pricing: Pricing | None = None,
        client: Any | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self.bridge = bridge
        self.model = model
        self.dry_run = dry_run
        self.max_tool_rounds = max_tool_rounds
        self.max_total_tool_calls = max_total_tool_calls
        self.max_write_commands = max_write_commands
        self.repeated_failed_call_limit = repeated_failed_call_limit
        self.max_input_tokens_per_request = max_input_tokens_per_request
        self.max_model_requests_per_cycle = max_model_requests_per_cycle
        self.pricing = pricing or Pricing.from_environment()
        self.client = client or OpenAI()
        self.state_store = state_store or StateStore()
        self.uncertain_commands: dict[str, dict[str, Any]] = {}
        self.current_state: dict[str, Any] | None = None
        self._last_batch_had_write = False
        self.total_tool_calls = 0
        self.write_commands = 0
        self.failed_call_counts: dict[str, int] = {}
        self.termination_reason: str | None = None
        self.model_request_count = 0
        self.accumulated_tool_result_chars = 0
        self.carried_context_chars = 0
        self.cycle_cost = 0.0
        self.session_cost = 0.0

    def run_once(self) -> None:
        self._begin_cycle()
        print("[STATE] Checking RimGPT bridge health")
        health = self.bridge.health()
        print(f"[STATE] Bridge health: {health.get('status')} ({health.get('bridge')})")

        state = self.bridge.get_state()
        self.current_state = self._accept_authoritative_state(state)
        print(f"[STATE] {summarize_state(state)}")
        self._measure_state_delta()

        print(f"[MODEL] Requesting one decision cycle from {self.model}")
        initial_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Review this RimWorld State API snapshot and decide whether to use the "
                            "available tools. After any tool results, provide a concise final assessment.\n\n"
                            + json.dumps(state, separators=(",", ":"))
                        ),
                    }
                ],
            }
        ]
        try:
            response = self._request_model(initial_input, state=state)
        except (ModelContextLimitError, ModelRequestLimitError) as exc:
            self.termination_reason = str(exc)
            print(f"[ERROR] {exc}")
            return

        final_response = self._handle_tool_rounds(response)
        assessment = getattr(final_response, "output_text", "") or collect_output_text(final_response)
        if assessment:
            print(f"[MODEL] Final assessment: {assessment.strip()}")
        else:
            print("[MODEL] Final assessment: no text returned")

        if self.termination_reason is None and self.current_state is not None:
            try:
                self.state_store.set_decision_baseline(self.current_state)
            except StateStoreError as exc:
                print(f"[WARNING] Could not persist decision baseline: {exc}")

    def _handle_tool_rounds(self, response: Any) -> Any:
        self._ensure_safety_state()
        current = response
        for round_index in range(self.max_tool_rounds):
            tool_calls = get_function_calls(current)
            if not tool_calls:
                self._reconcile_uncertain_commands()
                print("[MODEL] Tool-call loop terminated: model returned no tool calls")
                return current

            if self.total_tool_calls + len(tool_calls) > self.max_total_tool_calls:
                self.termination_reason = (
                    f"max total model tool calls exceeded "
                    f"({self.total_tool_calls + len(tool_calls)} > {self.max_total_tool_calls})"
                )
                print(f"[ERROR] Tool-call loop terminated: {self.termination_reason}")
                self._reconcile_uncertain_commands()
                return current

            self.total_tool_calls += len(tool_calls)
            print(f"[MODEL] Tool-call round {round_index + 1}: {len(tool_calls)} call(s)")
            round_start_version = snapshot_version(self.current_state)
            outputs = self._execute_tool_call_batch(tool_calls)
            outputs.extend(self._reconcile_uncertain_commands())
            if self.termination_reason is not None:
                print(f"[ERROR] Tool-call loop terminated: {self.termination_reason}")
                return current

            post_action_state = self._fresh_state_message(round_start_version, self._last_batch_had_write and not self.dry_run)

            continuation_input = outputs + [post_action_state]
            self.accumulated_tool_result_chars += sum(
                serialized_chars(output) for output in outputs if output.get("type") == "function_call_output"
            )
            print("[MODEL] Sending tool results and fresh state back to model")
            try:
                current = self._request_model(
                    continuation_input,
                    state=self.current_state,
                    previous_response_id=current.id,
                )
            except (ModelContextLimitError, ModelRequestLimitError) as exc:
                self.termination_reason = str(exc)
                print(f"[ERROR] Tool-call loop terminated: {exc}")
                return current

        self._reconcile_uncertain_commands()
        self.termination_reason = f"max tool-call rounds reached ({self.max_tool_rounds})"
        print(f"[ERROR] Tool-call loop terminated: {self.termination_reason}")
        return current

    def _execute_tool_call_batch(self, tool_calls: list[Any]) -> list[dict[str, Any]]:
        self._ensure_safety_state()
        prepared: list[dict[str, Any]] = []
        outputs: list[dict[str, Any] | None] = [None] * len(tool_calls)
        self._last_batch_had_write = False

        for index, call in enumerate(tool_calls):
            name = getattr(call, "name", "")
            call_id = getattr(call, "call_id", "")
            raw_arguments = getattr(call, "arguments", "{}")

            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                result = {"success": False, "error": f"Invalid tool arguments JSON: {exc}"}
                print(f"[ERROR] {name}: {result['error']}")
                outputs[index] = function_output(call_id, result)
                continue

            print(f"[ACTION] {name}({format_arguments(arguments)})")

            try:
                bridge_command = tool_call_to_bridge_command(name, arguments)
            except Exception as exc:
                if is_read_only_tool(name):
                    result = self._execute_read_only_tool(name, arguments)
                    outputs[index] = function_output(call_id, result)
                else:
                    result = {"success": False, "error": str(exc)}
                    print(f"[ERROR] {name}: {result['error']}")
                    outputs[index] = function_output(call_id, result)
                    self._record_failed_call(name, arguments, result)
                continue

            if self.dry_run:
                result = {
                    "success": True,
                    "dryRun": True,
                    "message": "Command was proposed but not executed because --dry-run is active.",
                    "command": bridge_command,
                }
                print(f"[RESULT] dry-run proposed {bridge_command}")
                outputs[index] = function_output(call_id, result)
                continue

            if self.write_commands >= getattr(self, "max_write_commands", DEFAULT_MAX_WRITE_COMMANDS):
                result = {
                    "success": False,
                    "error": (
                        f"Write-command safety limit reached "
                        f"({self.write_commands}/{getattr(self, 'max_write_commands', DEFAULT_MAX_WRITE_COMMANDS)}); command was not submitted."
                    ),
                    "command": bridge_command,
                }
                self.termination_reason = f"max write commands reached ({getattr(self, 'max_write_commands', DEFAULT_MAX_WRITE_COMMANDS)})"
                print(f"[ERROR] {name}: {result['error']}")
                outputs[index] = function_output(call_id, result)
                self._record_failed_call(name, arguments, result)
                continue

            self.write_commands += 1
            prepared.append(
                {
                    "index": index,
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "command": bridge_command,
                }
            )

        if prepared:
            self._last_batch_had_write = True
            submitted: list[dict[str, Any]] = []
            by_command_id: dict[str, dict[str, Any]] = {}

            for item in prepared:
                try:
                    submission = self.bridge.submit_command(item["command"])
                    command_id = str(submission["commandId"])
                    item["command_id"] = command_id
                    item["started_at"] = submission["startedAt"]
                    submitted.append(submission)
                    by_command_id[command_id] = item
                    print(
                        f"[ACTION] submitted {item['name']} commandId={command_id} "
                        f"in {submission.get('submitElapsedSeconds', 0.0):.2f}s"
                    )
                except CommandStatusUnreachable as exc:
                    result = {
                        "commandId": exc.command_id,
                        "status": "unknown",
                        "success": None,
                        "uncertain": True,
                        "error": exc.error,
                        "message": "Command submission status could not be verified. It will be reconciled later.",
                        "command": item["command"],
                    }
                    self.uncertain_commands[exc.command_id] = {
                        "call_id": item["call_id"],
                        "command": item["command"],
                        "started_at": time.monotonic() - exc.elapsed,
                        "reason": "unknown-unreachable",
                    }
                    print(f"[WARNING] command status unknown/unreachable after {exc.elapsed:.1f}s: {exc.command_id}")
                    outputs[item["index"]] = function_output(item["call_id"], result)
                    self._record_failed_call(item["name"], item.get("arguments", {}), result)
                except RimWorldBridgeError as exc:
                    result = {"success": False, "error": str(exc), "command": item["command"]}
                    print(f"[ERROR] {exc}")
                    outputs[item["index"]] = function_output(item["call_id"], result)
                    self._record_failed_call(item["name"], item.get("arguments", {}), result)

            if submitted:
                results = self.bridge.wait_for_commands(submitted)
                for submission in submitted:
                    command_id = str(submission["commandId"])
                    item = by_command_id[command_id]
                    result = results.get(command_id)
                    if result is None:
                        result = {
                            "commandId": command_id,
                            "status": "unknown",
                            "success": None,
                            "uncertain": True,
                            "error": "No command status was returned by batch wait",
                        }

                    result["command"] = item["command"]
                    elapsed = float(result.get("elapsedSeconds", 0.0))
                    if result.get("status") == "completed":
                        print(f"[RESULT] {command_id} completed in {elapsed:.2f}s: {result}")
                        if result.get("success") is False:
                            self._record_failed_call(item["name"], item.get("arguments", {}), result)
                    elif result.get("status") == "queued":
                        self.uncertain_commands[command_id] = {
                            "call_id": item["call_id"],
                            "command": item["command"],
                            "started_at": item["started_at"],
                            "reason": "still-queued",
                        }
                        print(f"[WARNING] command still queued after {elapsed:.1f}s: {command_id}")
                    else:
                        self.uncertain_commands[command_id] = {
                            "call_id": item["call_id"],
                            "command": item["command"],
                            "started_at": item["started_at"],
                            "reason": "unknown-unreachable",
                        }
                        print(f"[WARNING] command status unknown/unreachable after {elapsed:.1f}s: {command_id}")

                    outputs[item["index"]] = function_output(item["call_id"], result)

        return [output for output in outputs if output is not None]

    def _record_failed_call(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        self._ensure_safety_state()
        signature = failed_call_signature(name, arguments, result)
        count = self.failed_call_counts.get(signature, 0) + 1
        self.failed_call_counts[signature] = count
        if count >= getattr(self, "repeated_failed_call_limit", DEFAULT_REPEATED_FAILED_CALL_LIMIT):
            self.termination_reason = (
                f"repeated failed call detected for {name} "
                f"({count} substantially identical failures)"
            )
            print(f"[ERROR] Tool-call loop termination pending: {self.termination_reason}")

    def _ensure_safety_state(self) -> None:
        if not hasattr(self, "max_tool_rounds"):
            self.max_tool_rounds = DEFAULT_MAX_TOOL_ROUNDS
        if not hasattr(self, "max_total_tool_calls"):
            self.max_total_tool_calls = DEFAULT_MAX_TOTAL_TOOL_CALLS
        if not hasattr(self, "max_write_commands"):
            self.max_write_commands = DEFAULT_MAX_WRITE_COMMANDS
        if not hasattr(self, "repeated_failed_call_limit"):
            self.repeated_failed_call_limit = DEFAULT_REPEATED_FAILED_CALL_LIMIT
        if not hasattr(self, "total_tool_calls"):
            self.total_tool_calls = 0
        if not hasattr(self, "write_commands"):
            self.write_commands = 0
        if not hasattr(self, "failed_call_counts"):
            self.failed_call_counts = {}
        if not hasattr(self, "termination_reason"):
            self.termination_reason = None

    def _begin_cycle(self) -> None:
        self.total_tool_calls = 0
        self.write_commands = 0
        self.failed_call_counts = {}
        self.termination_reason = None
        self.model_request_count = 0
        self.accumulated_tool_result_chars = 0
        self.carried_context_chars = 0
        self.cycle_cost = 0.0

    def _accept_authoritative_state(self, state: dict[str, Any]) -> dict[str, Any]:
        store = getattr(self, "state_store", None)
        if store is not None:
            try:
                store.update_current_state(state)
            except StateStoreError as exc:
                print(f"[WARNING] Could not persist authoritative state: {exc}")
        return state

    def _measure_state_delta(self) -> None:
        store = getattr(self, "state_store", None)
        if store is None:
            return
        try:
            delta = store.get_changes_since_last_decision()
            if delta.get("bootstrapRequired"):
                print(f"[DELTA] bootstrapRequired reason={delta.get('reason')}")
        except Exception as exc:
            print(f"[WARNING] Could not calculate local state delta: {exc}")

    def _request_model(
        self,
        input_items: list[dict[str, Any]],
        *,
        state: dict[str, Any] | None,
        previous_response_id: str | None = None,
    ) -> Any:
        if self.model_request_count >= self.max_model_requests_per_cycle:
            raise ModelRequestLimitError(
                f"Maximum model requests per decision cycle reached: {self.max_model_requests_per_cycle}"
            )

        breakdown = measure_context(
            instructions=SYSTEM_INSTRUCTIONS,
            tools=TOOLS,
            input_items=input_items,
            state=state,
            accumulated_tool_result_chars=self.accumulated_tool_result_chars,
            carried_context_chars=self.carried_context_chars,
        )
        print(breakdown.as_log_line())
        if breakdown.estimated_input_tokens > self.max_input_tokens_per_request:
            print(
                "[CONTEXT LIMIT] "
                f"system={breakdown.system_chars} dynamic={breakdown.dynamic_input_chars} "
                f"tools={breakdown.tool_schema_chars} toolResults={breakdown.accumulated_tool_result_chars} "
                f"fullState={breakdown.full_state_chars} operations={breakdown.operations_chars} "
                f"estimatedInputTokens={breakdown.estimated_input_tokens}"
            )
            raise ModelContextLimitError(breakdown, self.max_input_tokens_per_request)

        request_number = self.model_request_count + 1
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "tools": TOOLS,
            "input": input_items,
        }
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        response = self.client.responses.create(**request)
        self.model_request_count = request_number
        # A continuation references prior Responses output server-side. Track
        # the response payload we can observe so its growth remains visible to
        # the preflight estimate without re-sending it from this process.
        self.carried_context_chars += serialized_chars(input_items) + serialized_chars(getattr(response, "output", []))
        usage = extract_usage(response, self.pricing)
        if usage.estimated_cost is not None:
            self.cycle_cost += usage.estimated_cost
            self.session_cost += usage.estimated_cost
        print(
            "[COST] "
            f"request={request_number} model={self.model} inputTokens={format_metric(usage.input_tokens)} "
            f"cachedInputTokens={format_metric(usage.cached_input_tokens)} "
            f"uncachedInputTokens={format_metric(usage.uncached_input_tokens)} "
            f"outputTokens={format_metric(usage.output_tokens)} "
            f"estimatedRequestCost={format_cost(usage.estimated_cost)} "
            f"cycleCost={format_cost(self.cycle_cost if usage.estimated_cost is not None else None)} "
            f"sessionCost={format_cost(self.session_cost if usage.estimated_cost is not None else None)}"
        )
        return response

    def _execute_read_only_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            if name == "inspect_map":
                result = self.bridge.inspect_map(
                    arguments["min_x"],
                    arguments["min_z"],
                    arguments["max_x"],
                    arguments["max_z"],
                )
            elif name == "list_build_options":
                result = self.bridge.list_build_options(arguments.get("category"), arguments.get("search"))
            elif name == "get_build_info":
                result = self.bridge.get_build_info(arguments["def_name"])
            elif name == "list_growable_plants":
                result = self.bridge.list_growable_plants()
            elif name == "list_recipes":
                result = self.bridge.list_recipes(arguments["worktable_id"])
            elif name == "check_build_placements":
                result = self.bridge.check_build_placements(convert_blueprint_placements(arguments["placements"]))
            elif name == "check_zone_placement":
                result = self.bridge.check_zone_placement(
                    arguments["zone_type"],
                    arguments["min_x"],
                    arguments["min_z"],
                    arguments["max_x"],
                    arguments["max_z"],
                )
            else:
                raise ValueError(f"Unsupported read-only tool: {name}")
        except Exception as exc:
            print(f"[ERROR] {name}: {exc}")
            result = {"success": False, "error": str(exc)}
            self._record_failed_call(name, arguments, result)
            return result

        elapsed = time.monotonic() - started
        print(f"[RESULT] {name} completed in {elapsed:.2f}s")
        return {"success": True, "result": result, "elapsedSeconds": round(elapsed, 3)}

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

    def _fresh_state_message(self, after_version: int, require_newer: bool) -> dict[str, Any]:
        try:
            if require_newer:
                state = self.bridge.wait_for_state_after(after_version, timeout_ms=3000)
                if state.get("fresh") is False:
                    stale_state = state.get("state") if isinstance(state.get("state"), dict) else state
                    self.current_state = self._accept_authoritative_state(stale_state)
                    print(f"[WARNING] No post-command snapshot newer than version {after_version}")
                    print(f"[STATE] Post-action stale {summarize_state(stale_state)}")
                    text = (
                        f"No post-command snapshot newer than version {after_version} was available. "
                        "This state may be stale; do not treat it as authoritative proof that completed commands failed.\n\n"
                        + json.dumps(stale_state, separators=(",", ":"))
                    )
                else:
                    self.current_state = self._accept_authoritative_state(state)
                    new_version = snapshot_version(state)
                    print(f"[STATE] Post-action authoritative version={new_version} {summarize_state(state)}")
                    text = (
                        "Fresh authoritative RimWorld state generated after the completed command batch. "
                        "Use this state, not only action acknowledgements, for your next assessment.\n\n"
                        + json.dumps(state, separators=(",", ":"))
                    )
            else:
                state = self.bridge.get_state()
                self.current_state = self._accept_authoritative_state(state)
                print(f"[STATE] Current {summarize_state(state)}")
                text = (
                    "Current RimWorld state after a read-only/no-mutation tool round.\n\n"
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


def snapshot_version(state: dict[str, Any] | None) -> int:
    if not isinstance(state, dict):
        return 0
    snapshot = state.get("snapshot", {})
    if not isinstance(snapshot, dict):
        return 0
    try:
        return int(snapshot.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def failed_call_signature(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
    reason = str(result.get("error") or result.get("message") or "")
    normalized_reason = " ".join(reason.lower().split())
    return json.dumps(
        {
            "name": name,
            "arguments": normalize_for_signature(arguments),
            "reason": normalized_reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_for_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_for_signature(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        normalized_items = [normalize_for_signature(item) for item in value]
        if len(normalized_items) > 20:
            return normalized_items[:20] + [{"truncatedCount": len(normalized_items) - 20}]
        return normalized_items
    return value


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


def format_metric(value: int | None) -> str:
    return str(value) if value is not None else "unavailable"


def format_cost(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "unavailable"
