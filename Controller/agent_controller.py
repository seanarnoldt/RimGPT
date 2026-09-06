import copy
import json
import time
from typing import Any

from openai import OpenAI

from bridge import CommandStatusUnreachable, RimWorldBridge, RimWorldBridgeError
from colony_state_query import ColonyStateQuery
from context_telemetry import (
    DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST,
    DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE,
    ContextBreakdown,
    ModelContextLimitError,
    ModelRequestLimitError,
    Pricing,
    extract_usage,
    measure_context,
    serialized_chars,
)
from decision_context import DecisionContextBuilder, DecisionContextError, build_current_summary, serialize_context
from decision_handoff import DecisionHandoffError, fallback_handoff, handoff_chars, prepare_handoff
from dry_run_proposals import DryRunProposalLedger
from model_tool_result import ModelToolResultFormatter
from prompt_runtime import (
    DEFAULT_COMPACT_THRESHOLD_TOKENS,
    DEFAULT_MAX_COMPACTIONS_PER_CYCLE,
    DEFAULT_PROMPT_CACHE_MODE,
    RIMGPT_PROMPT_VERSION,
    compacted_output_as_input,
    detect_responses_features,
    prompt_cache_request_fields,
)
from state_diff import StateDiff
from state_store import StateStore, StateStoreError
from tool_registry import (
    DEFAULT_MAX_ACTIVE_TOOL_GROUPS,
    DEFAULT_TOOL_REGISTRY,
    ActiveToolSet,
    select_initial_tool_groups,
)
from tools import convert_blueprint_placements, tool_call_to_bridge_command

DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_MAX_TOTAL_TOOL_CALLS = 100
DEFAULT_MAX_WRITE_COMMANDS = 75
DEFAULT_REPEATED_FAILED_CALL_LIMIT = 3


STABLE_PROMPT_PREFIX = """You are playing RimWorld through a restricted control interface.

You are the colony's strategic controller.

Your objective is to keep the colony alive, improve its long-term position, and eventually achieve the game's victory condition.

You may make decisions independently.

You currently have only a limited toolset. Do not assume you can perform actions that are not exposed as tools.

Only currently enabled tools appear directly. If a needed gameplay action is absent, use list_capabilities and enable_capability instead of assuming RimGPT cannot perform it. Enable only the groups needed for the current plan; enabled groups persist for this decision cycle and reset on the next cycle.

If a capability is already listed in activeCapabilities, do not call enable_capability for it again.

You may now allow starting supplies, choose research, designate visible mining/cutting/harvesting/hunting targets, and request unambiguous prioritized hauling.

You may inspect bounded visible map regions, create stockpile and growing zones, place construction blueprints, cancel player orders, and designate visible player structures for deconstruction.

The initial decision context is compact. currentSummary is current strategic state, and changesSinceLastDecision contains meaningful changes since the last successfully completed strategic decision.

progressSinceLastDecision contains deterministic evidence that prior actions advanced, including construction stages, room topology, pending work, cleared blockers, and completed designations. Use it to recognize completed prerequisites without rediscovering the whole area.

strategicMemory records prior plans and decisions; it is not current authoritative state. Live state always overrides memory.

previousDecision is a short-lived execution handoff, not authoritative state or conversation history. Use current authoritative state to evaluate its open loops. Never assume an unresolved item still needs execution if current state shows it is complete or obsolete; resolve it explicitly instead.

stallRecovery is bounded controller guidance, not a transcript. If it marks a repeated action or failed verification strategy as stalled, do not repeat that strategy unchanged. Use a narrower verification tool, change the prerequisite, or explicitly preserve the blocker while retaining the parent objective.

Unresolved committed actions from the previous decision are priorities. Before merely restating the same need, either execute it, make concrete progress, explicitly defer or block it, or resolve it from current state. Every prior loop must be retained in finish_decision or explicitly resolved as completed, cancelled, or invalidated.

Make meaningful progress, but do not try to solve the entire colony in one decision cycle. Use open loops to carry unfinished work into later cycles. decisionBudget.requestsRemaining includes the response you are currently producing. When that budget is low, stop discovery and finalize. Prefer acting on validated information over repeatedly gathering more information. finish_decision is the required normal terminal action.

If exact current information is needed, call get_colony_state for only the relevant section or use an existing targeted read tool. For room, enclosure, roof, or room-temperature questions use inspect_room_at. Use inspect_map only for actual spatial placement and planning. Do not query every state section reflexively. Start with the summary, delta, and progress signals, then retrieve only details whose uncertainty matters to this decision.

Do not invent pawn IDs, resource counts, building existence, map coordinates, work types, threats, or other game state. Use targeted validators and catalog tools when planning construction or zones.

It is valid to take no action when the colony is stable.

Use colonists[].work as the authoritative work capability and priority view. Never assign work if capable=false. Do not assign a work priority if the current priority already equals the desired value. Repeat set_work_priority only if a fresh authoritative state shows it did not persist.

Do not invent buildDef, stuffDef, plantDef, or zone IDs. Use list_build_options, get_build_info, list_growable_plants, and the supplied state when exact defs or IDs are uncertain.

Inspect relevant map regions before committing major construction, growing zones, or storage zones.

Inspect the smallest useful map area. Prefer roughly 15x15 to 20x20 planning regions when practical, using the map overview and important locations to choose focused coordinates. Expand only when the first region is insufficient; do not reflexively request about 40x40 for ordinary starter planning. Do not re-inspect overlapping territory in the same cycle unless new exact information is needed.

Growing zones require normal RimWorld zone validity, not only fertile terrain. Prefer contiguous cells where inspect_map reports canCreateGrowingZone=true, use check_zone_placement before creating farms, and pass minimum_valid_cells to create_growing_zone to avoid accidental one- or two-cell farms.

Prefer reversible and low-risk actions when information is incomplete.

Do not repeatedly issue an action if the previous result indicates it has already succeeded.

Remember that top-level resource scalar counts represent currently available supplies. Check resources.forbidden, resources.totalVisible, and mapThings.forbidden before concluding supplies do not exist on the visible map.

Use allow_all near scenario start when visible starting supplies are forbidden and need to become available.

For resources, distinguish currently available supplies from visible forbidden supplies. A zero available count does not mean there are no visible forbidden supplies.

Designations and movement use current-map RimWorld x/z coordinates only. Do not interact with hidden or fogged information.

Blueprint placement only creates normal construction blueprints; colonists still need resources, access, work priorities, and time to build them.

Terrain support matters for buildings. Use inspect_map terrain affordances and build option requiredTerrainAffordance before placing blueprints.

Player-visible alerts, letters, and recent events in currentSummary.awareness are authoritative gameplay warnings. Treat high-severity warnings such as an ancient danger as strategic constraints; never infer or expose contents that remain hidden.

For temperature-sensitive shelter, physical blueprint validity is not enough. Use inspect_room_at at an interior cell to verify that the occupied space is enclosed, indoors, substantially roofed, and uses room temperature before relying on a cooler, heater, bed, or workstation there.

When a strategic goal is blocked, convert the blocker into an executable prerequisite using available tools, then preserve the parent goal as an open loop. For example, obtain visible resources, enable capable labor, or place a required generic work facility before expecting the parent work to proceed. Do not merely restate a known blocker across cycles.

When a prerequisite succeeds, verify it with the narrowest relevant read tool, then advance or resolve its open loop. If the same verification fails repeatedly or authoritative progress remains unchanged, stop repeating it and record the blocker or choose another prerequisite.

Use currentSummary.labor to notice capable idle colonists, pending work, and obvious work blockers. If colonists are idle while a goal is blocked on obtainable visible resources or an available prerequisite, prefer a concrete enabling action over passive waiting.

Before placing a large construction batch, preferably call check_build_placements with the planned placements. If several cells fail validation, adjust the plan instead of repeatedly attempting the same cells.

inspect_map uses a terrain palette with row runs encoded as [xStart,length,terrainPaletteId], plus exact explicit things and grouped plant coordinate cells. Terrain and things use room references: resolve rN values through the shared rooms table; outdoor and unroomed are explicit sentinels, and outdoorRoom carries exterior temperature facts. Use inspect_map to identify a candidate plan, then use the exact build or zone validator before mutation.

Successful batch and validator results summarize successes and list only failures. Missing per-cell success entries do not mean execution was omitted. If truncated=true, query a smaller region or narrower catalog when omitted detail matters.

The controller automatically supplies a compact authoritative post-tool state update. Do not re-query a colony-state section only to confirm a successful command unless the next decision requires exact details from that section.

Do not assume every visually open cell can support every structure.

Avoid building steel walls at game start unless there is a specific strategic reason. Wood is generally less valuable as a long-term material, but steel is strategically important for machinery and early infrastructure.

Prefer compact, practical early colony layouts. At game start prioritize immediate survival: supplies, shelter, food, beds, basic storage, research, and power as appropriate. Do not overbuild when resources are scarce.

In dry-run mode, mutation results marked proposed=true and executed=false were not applied to RimWorld, so authoritative state is expected to remain unchanged. Treat dryRunProposals as the cycle-local plan and do not repeat an already-proposed mutation solely because live state did not change.

Before equipment, apparel, bed, bill, power, fuel, or allowed-area actions, read the relevant current state section and use only returned stable IDs. Query recipes before adding unfamiliar bills. Use direct work orders for targeted immediate tasks, not as a substitute for sensible work priorities. For multi-step plans that require a stable state, you may pause first and restore an appropriate speed afterward; the controller never forces a pause automatically.

prioritize_job is intentionally conservative and may fail when a normal player right-click action is ambiguous; treat that as a signal to use a narrower available tool or explain what capability is missing.

Because the current control surface is incomplete, it is acceptable to take no action and explain what additional capability would be needed."""

# Compatibility name used by existing telemetry/tests. Dynamic colony context is
# always supplied separately through Responses input items.
SYSTEM_INSTRUCTIONS = STABLE_PROMPT_PREFIX


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
        max_active_tool_groups: int = DEFAULT_MAX_ACTIVE_TOOL_GROUPS,
        prompt_cache_mode: str = DEFAULT_PROMPT_CACHE_MODE,
        compact_threshold_tokens: int = DEFAULT_COMPACT_THRESHOLD_TOKENS,
        max_compactions_per_cycle: int = DEFAULT_MAX_COMPACTIONS_PER_CYCLE,
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
        self.max_active_tool_groups = max_active_tool_groups
        self.prompt_cache_mode = prompt_cache_mode
        self.compact_threshold_tokens = compact_threshold_tokens
        self.max_compactions_per_cycle = max_compactions_per_cycle
        self.pricing = pricing or Pricing.from_environment()
        self.client = client or OpenAI()
        self.responses_features = detect_responses_features(self.client.responses)
        self.state_store = state_store or StateStore()
        self.context_builder = DecisionContextBuilder(self.state_store)
        self.colony_state_query = ColonyStateQuery(self.state_store)
        self.tool_result_formatter = ModelToolResultFormatter()
        self.tool_registry = DEFAULT_TOOL_REGISTRY
        self.active_tools = ActiveToolSet(self.tool_registry, max_active_tool_groups)
        self.uncertain_commands: dict[str, dict[str, Any]] = {}
        self.current_state: dict[str, Any] | None = None
        self._last_batch_had_write = False
        self.total_tool_calls = 0
        self.write_commands = 0
        self.failed_call_counts: dict[str, int] = {}
        self.termination_reason: str | None = None
        self.model_request_count = 0
        self.accumulated_tool_result_chars = 0
        self.tool_result_chars_this_round = 0
        self.raw_tool_results: list[dict[str, Any]] = []
        self.carried_context_chars = 0
        self.cycle_cost = 0.0
        self.session_cost = 0.0
        self.compaction_count = 0
        self.previous_response_id: str | None = None
        self.dry_run_proposals: DryRunProposalLedger | None = None
        self.pending_decision_handoff: dict[str, Any] | None = None
        self.terminal_decision_finished = False
        self._last_presented_tool_names: set[str] | None = None
        if not self.pricing.base_configured:
            print(
                "[COST] calculation disabled: configure "
                "RIMGPT_INPUT_COST_PER_MILLION and RIMGPT_OUTPUT_COST_PER_MILLION; "
                "cached/cache-write rates are separately configurable."
            )
        elif self.pricing.cached_input_per_million is None:
            print(
                "[COST] cached-input cost unavailable when cache hits occur: configure "
                "RIMGPT_CACHED_INPUT_COST_PER_MILLION."
            )

    def run_once(self, trigger: dict[str, Any] | None = None) -> None:
        self._begin_cycle()
        print("[STATE] Checking RimGPT bridge health")
        health = self.bridge.health()
        print(f"[STATE] Bridge health: {health.get('status')} ({health.get('bridge')})")

        state = self.bridge.get_state()
        self.current_state = self._accept_authoritative_state(state)
        print(f"[STATE] {summarize_state(state)}")

        try:
            decision_context = self._get_context_builder().build(trigger)
        except DecisionContextError as exc:
            self.termination_reason = str(exc)
            print(f"[ERROR] Decision cycle terminated: {exc}")
            return

        self._configure_initial_tools(decision_context)
        decision_context = self._decorate_cycle_context(decision_context)

        print(f"[MODEL] Requesting one decision cycle from {self.model}")
        initial_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Review this compact RimGPT decision context and decide whether to use the "
                            "available tools. Retrieve bounded current detail only when needed. After any "
                            "tool results, finish with finish_decision; ordinary final text remains a fallback.\n\n"
                            + serialize_context(decision_context)
                        ),
                    }
                ],
            }
        ]
        try:
            response = self._request_model(initial_input, state=state, context_payload=decision_context)
        except Exception as exc:
            self.termination_reason = str(exc)
            print(f"[ERROR] {exc}")
            return

        final_response = self._handle_tool_rounds(response)
        assessment = (
            str(self.pending_decision_handoff.get("assessment") or "")
            if self.pending_decision_handoff is not None
            else (getattr(final_response, "output_text", "") or collect_output_text(final_response))
        )
        if assessment:
            print(f"[MODEL] Final assessment: {assessment.strip()}")
        else:
            print("[MODEL] Final assessment: no text returned")

        if not self._response_completed(final_response):
            response_status = getattr(final_response, "status", None)
            self.termination_reason = f"model response did not complete successfully (status={response_status})"
            print(f"[ERROR] Decision cycle terminated: {self.termination_reason}")

        if self.uncertain_commands and self.termination_reason is None:
            self.termination_reason = f"{len(self.uncertain_commands)} command(s) remained uncertain at cycle end"
            print(f"[ERROR] Decision cycle terminated: {self.termination_reason}")

        if self.termination_reason is None and self.pending_decision_handoff is None:
            try:
                self.pending_decision_handoff = fallback_handoff(
                    self.state_store.get_decision_handoff(), assessment
                )
                print("[HANDOFF] No structured finish_decision supplied; retained prior open loops")
            except DecisionHandoffError as exc:
                self.termination_reason = f"could not prepare fallback decision handoff: {exc}"
                print(f"[ERROR] Decision cycle terminated: {self.termination_reason}")

        if self.termination_reason is None:
            try:
                final_state = self.bridge.get_state()
                self.current_state = self._accept_authoritative_state(final_state)
                print(f"[STATE] Final authoritative {summarize_state(final_state)}")
                if self.dry_run:
                    print("[STATESTORE] Dry-run completed without persistent baseline, handoff, or memory changes")
                else:
                    assert self.pending_decision_handoff is not None
                    self.state_store.commit_successful_decision(final_state, self.pending_decision_handoff)
            except (RimWorldBridgeError, StateStoreError) as exc:
                self.termination_reason = f"could not confirm final authoritative state: {exc}"
                print(f"[ERROR] Decision baseline not advanced: {exc}")

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
            finish_calls = [call for call in tool_calls if getattr(call, "name", "") == "finish_decision"]
            if finish_calls and len(tool_calls) != 1:
                self.termination_reason = "finish_decision must be the only tool call in its terminal round"
                print(f"[ERROR] Tool-call loop terminated: {self.termination_reason}")
                return current
            round_start_version = snapshot_version(self.current_state)
            round_start_state = copy.deepcopy(self.current_state)
            outputs = self._execute_tool_call_batch(tool_calls)
            outputs.extend(self._reconcile_uncertain_commands())
            if self.termination_reason is not None:
                print(f"[ERROR] Tool-call loop terminated: {self.termination_reason}")
                return current
            if self.terminal_decision_finished:
                print("[MODEL] Tool-call loop terminated: finish_decision completed locally")
                return current

            post_action_state, post_action_context = self._fresh_state_message(
                round_start_version,
                self._last_batch_had_write and not self.dry_run,
                before_state=round_start_state,
                include_context=True,
            )

            continuation_input = outputs + [post_action_state]
            self.tool_result_chars_this_round = model_result_chars(outputs)
            self.accumulated_tool_result_chars += self.tool_result_chars_this_round
            print("[MODEL] Sending tool results and fresh state back to model")
            try:
                current = self._request_model(
                    continuation_input,
                    state=self.current_state,
                    previous_response_id=current.id,
                    context_payload=post_action_context,
                )
            except Exception as exc:
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
        presented = getattr(self, "_last_presented_tool_names", None)
        active_at_round_start = (
            set(presented)
            if presented is not None
            else {schema["name"] for schema in self._get_active_tools().schemas()}
        )

        for index, call in enumerate(tool_calls):
            name = getattr(call, "name", "")
            call_id = getattr(call, "call_id", "")
            raw_arguments = getattr(call, "arguments", "{}")

            if name not in active_at_round_start:
                registration = self.tool_registry.registration(name)
                result = {
                    "success": False,
                    "error": "toolCapabilityNotEnabled" if registration is not None else "unknownTool",
                }
                if registration is not None:
                    result["requiredCapability"] = registration.group
                print(f"[ERROR] {name}: {result['error']}")
                outputs[index] = self._function_output(call_id, name, result, {})
                continue

            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                result = {"success": False, "error": f"Invalid tool arguments JSON: {exc}"}
                print(f"[ERROR] {name}: {result['error']}")
                outputs[index] = self._function_output(call_id, name, result, {})
                continue

            print(f"[ACTION] {name}({format_arguments(arguments)})")

            registration = self.tool_registry.registration(name)
            if registration is not None and registration.read_only:
                result = self._execute_read_only_tool(name, arguments)
                outputs[index] = self._function_output(call_id, name, result, arguments)
                continue

            try:
                bridge_command = tool_call_to_bridge_command(name, arguments)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
                print(f"[ERROR] {name}: {result['error']}")
                outputs[index] = self._function_output(call_id, name, result, arguments)
                self._record_failed_call(name, arguments, result)
                continue

            if self.dry_run:
                ledger = self._get_dry_run_proposal_ledger()
                proposal = ledger.add(name, arguments)
                duplicate = bool(proposal["duplicate"])
                result = {
                    "dryRun": True,
                    "proposed": not duplicate,
                    "executed": False,
                    "duplicateProposal": duplicate,
                    "proposal": proposal["summary"],
                    "wouldSubmit": bridge_command,
                }
                disposition = "duplicate proposal" if duplicate else "proposed"
                print(f"[RESULT] dry-run {disposition}: {proposal['summary']}")
                outputs[index] = self._function_output(call_id, name, result, arguments)
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
                outputs[index] = self._function_output(call_id, name, result, arguments)
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
                        "name": item["name"],
                        "arguments": item.get("arguments", {}),
                        "command": item["command"],
                        "started_at": time.monotonic() - exc.elapsed,
                        "reason": "unknown-unreachable",
                    }
                    print(f"[WARNING] command status unknown/unreachable after {exc.elapsed:.1f}s: {exc.command_id}")
                    outputs[item["index"]] = self._function_output(
                        item["call_id"], item["name"], result, item.get("arguments", {})
                    )
                    self._record_failed_call(item["name"], item.get("arguments", {}), result)
                except RimWorldBridgeError as exc:
                    result = {"success": False, "error": str(exc), "command": item["command"]}
                    print(f"[ERROR] {exc}")
                    outputs[item["index"]] = self._function_output(
                        item["call_id"], item["name"], result, item.get("arguments", {})
                    )
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
                        print(
                            f"[RESULT] {command_id} completed in {elapsed:.2f}s "
                            f"success={str(result.get('success') is True).lower()}"
                        )
                        if result.get("success") is False:
                            self._record_failed_call(item["name"], item.get("arguments", {}), result)
                    elif result.get("status") == "queued":
                        self.uncertain_commands[command_id] = {
                            "call_id": item["call_id"],
                            "name": item["name"],
                            "arguments": item.get("arguments", {}),
                            "command": item["command"],
                            "started_at": item["started_at"],
                            "reason": "still-queued",
                        }
                        print(f"[WARNING] command still queued after {elapsed:.1f}s: {command_id}")
                    else:
                        self.uncertain_commands[command_id] = {
                            "call_id": item["call_id"],
                            "name": item["name"],
                            "arguments": item.get("arguments", {}),
                            "command": item["command"],
                            "started_at": item["started_at"],
                            "reason": "unknown-unreachable",
                        }
                        print(f"[WARNING] command status unknown/unreachable after {elapsed:.1f}s: {command_id}")

                    outputs[item["index"]] = self._function_output(
                        item["call_id"], item["name"], result, item.get("arguments", {})
                    )

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
        self._get_active_tools()

    def _begin_cycle(self) -> None:
        self.total_tool_calls = 0
        self.write_commands = 0
        self.failed_call_counts = {}
        self.termination_reason = None
        self.model_request_count = 0
        self.accumulated_tool_result_chars = 0
        self.tool_result_chars_this_round = 0
        self.raw_tool_results = []
        self.carried_context_chars = 0
        self.cycle_cost = 0.0
        self.compaction_count = 0
        self.previous_response_id = None
        self.pending_decision_handoff = None
        self.terminal_decision_finished = False
        self._last_presented_tool_names = None
        self._get_active_tools().reset()
        self.dry_run_proposals = DryRunProposalLedger() if getattr(self, "dry_run", False) else None

    def _get_dry_run_proposal_ledger(self) -> DryRunProposalLedger:
        ledger = getattr(self, "dry_run_proposals", None)
        if ledger is None:
            ledger = DryRunProposalLedger()
            self.dry_run_proposals = ledger
        return ledger

    def _decorate_cycle_context(self, context: dict[str, Any]) -> dict[str, Any]:
        decorated = copy.deepcopy(context)
        decorated["activeCapabilities"] = list(self._get_active_tools().dynamic_groups)
        if getattr(self, "dry_run", False):
            decorated["dryRun"] = True
            decorated["dryRunProposals"] = self._get_dry_run_proposal_ledger().summaries()
        return decorated

    def _get_active_tools(self) -> ActiveToolSet:
        registry = getattr(self, "tool_registry", None)
        if registry is None:
            registry = DEFAULT_TOOL_REGISTRY
            self.tool_registry = registry
        active = getattr(self, "active_tools", None)
        if active is None:
            maximum = getattr(self, "max_active_tool_groups", DEFAULT_MAX_ACTIVE_TOOL_GROUPS)
            active = ActiveToolSet(registry, maximum)
            self.active_tools = active
        return active

    def _configure_initial_tools(self, decision_context: dict[str, Any]) -> None:
        preloaded = select_initial_tool_groups(decision_context)
        self._get_active_tools().reset(preloaded)
        for group in preloaded:
            print(f"[TOOLS] capability={group} preloaded")
        self._log_active_tools()

    def _log_active_tools(self) -> None:
        active = self._get_active_tools()
        schemas = active.schemas()
        print(
            "[TOOLS] "
            f"activeGroups={','.join(active.groups)} toolCount={len(schemas)} "
            f"schemaChars={serialized_chars(schemas)}"
        )

    def _decision_budget(self) -> dict[str, Any]:
        return {
            "decisionBudget": {
                "requestsRemaining": max(0, self.max_model_requests_per_cycle - self.model_request_count),
                "finalizationReserved": True,
            }
        }

    def _with_decision_budget(self, input_items: list[Any]) -> list[Any]:
        return [
            *input_items,
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(self._decision_budget(), separators=(",", ":")),
                    }
                ],
            },
        ]

    def _request_tool_surface(self) -> tuple[list[dict[str, Any]], str]:
        remaining = self.max_model_requests_per_cycle - self.model_request_count
        schemas = self._get_active_tools().schemas()
        if remaining <= 1:
            return [schema for schema in schemas if schema.get("name") == "finish_decision"], "finalization-only"
        if remaining == 2:
            immediate = []
            for schema in schemas:
                registration = self.tool_registry.registration(str(schema.get("name") or ""))
                if schema.get("name") == "finish_decision" or (
                    registration is not None and not registration.read_only
                ):
                    immediate.append(schema)
            return immediate, "immediate-work"
        return schemas, "normal"

    def _accept_authoritative_state(self, state: dict[str, Any]) -> dict[str, Any]:
        store = getattr(self, "state_store", None)
        if store is not None:
            try:
                store.update_current_state(state, persist=not getattr(self, "dry_run", False))
            except StateStoreError as exc:
                print(f"[WARNING] Could not persist authoritative state: {exc}")
        return state

    def _get_context_builder(self) -> DecisionContextBuilder:
        builder = getattr(self, "context_builder", None)
        if builder is None:
            builder = DecisionContextBuilder(self.state_store)
            self.context_builder = builder
        return builder

    def _get_colony_state_query(self) -> ColonyStateQuery:
        query = getattr(self, "colony_state_query", None)
        if query is None:
            query = ColonyStateQuery(self.state_store)
            self.colony_state_query = query
        return query

    def _get_tool_result_formatter(self) -> ModelToolResultFormatter:
        formatter = getattr(self, "tool_result_formatter", None)
        if formatter is None:
            formatter = ModelToolResultFormatter()
            self.tool_result_formatter = formatter
        return formatter

    def _model_tool_result(
        self,
        name: str,
        raw_result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        retained = getattr(self, "raw_tool_results", None)
        if retained is None:
            retained = []
            self.raw_tool_results = retained
        retained.append(
            {
                "tool": name,
                "arguments": copy.deepcopy(arguments or {}),
                "result": copy.deepcopy(raw_result),
            }
        )
        retention_limit = getattr(self, "max_total_tool_calls", DEFAULT_MAX_TOTAL_TOOL_CALLS)
        if len(retained) > retention_limit:
            del retained[: len(retained) - retention_limit]
        model_result, telemetry = self._get_tool_result_formatter().format_with_telemetry(
            name, raw_result, arguments
        )
        print(telemetry.as_log_line())
        return model_result

    def _function_output(
        self,
        call_id: str,
        name: str,
        raw_result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return function_output(call_id, self._model_tool_result(name, raw_result, arguments))

    @staticmethod
    def _response_completed(response: Any) -> bool:
        status = getattr(response, "status", None)
        return status in (None, "completed")

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
        context_payload: dict[str, Any] | None = None,
    ) -> Any:
        self._ensure_prompt_runtime_state()
        if self.model_request_count >= self.max_model_requests_per_cycle:
            raise ModelRequestLimitError(
                f"Maximum model requests per decision cycle reached: {self.max_model_requests_per_cycle}"
            )

        active_tools, tool_mode = self._request_tool_surface()
        request_input = self._with_decision_budget(input_items)
        print(
            f"[BUDGET] requestsRemaining={self.max_model_requests_per_cycle - self.model_request_count} "
            f"finalizationReserved=true toolMode={tool_mode}"
        )
        print(
            "[TOOLS] "
            f"requestMode={tool_mode} toolCount={len(active_tools)} "
            f"schemaChars={serialized_chars(active_tools)}"
        )
        cache_fields = prompt_cache_request_fields(
            self.responses_features,
            self.model,
            getattr(self, "prompt_cache_mode", DEFAULT_PROMPT_CACHE_MODE),
        )
        cache_key = str(cache_fields.get("prompt_cache_key", "disabled"))
        breakdown = self._measure_model_request(
            request_input,
            active_tools,
            state,
            context_payload,
            carried_context_chars=self.carried_context_chars,
        )
        print(
            "[PROMPT] "
            f"version={RIMGPT_PROMPT_VERSION} stablePrefixChars={len(SYSTEM_INSTRUCTIONS)} "
            f"dynamicChars={breakdown.dynamic_input_chars} "
            f"activeToolSchemaChars={breakdown.tool_schema_chars} cacheKey={cache_key}"
        )
        print(breakdown.as_log_line())
        threshold = getattr(self, "compact_threshold_tokens", DEFAULT_COMPACT_THRESHOLD_TOKENS)
        maximum_compactions = getattr(self, "max_compactions_per_cycle", DEFAULT_MAX_COMPACTIONS_PER_CYCLE)
        if previous_response_id and breakdown.estimated_input_tokens >= threshold:
            can_compact = (
                self.responses_features.compact
                and self.compaction_count < maximum_compactions
                and self.max_model_requests_per_cycle - self.model_request_count > 2
            )
            if can_compact:
                compacted_input = self._compact_continuation(
                    previous_response_id,
                    request_input,
                    breakdown.estimated_input_tokens,
                    cache_fields,
                    cache_key,
                )
                if compacted_input is not None:
                    input_items = compacted_input
                    previous_response_id = None
                    self.previous_response_id = None
                    self.carried_context_chars = 0
                active_tools, tool_mode = self._request_tool_surface()
                request_input = self._with_decision_budget(input_items)
                breakdown = self._measure_model_request(
                    request_input,
                    active_tools,
                    state,
                    context_payload,
                    carried_context_chars=0 if compacted_input is not None else self.carried_context_chars,
                )
                print(
                    "[COMPACTION] "
                    f"triggered={str(compacted_input is not None).lower()} "
                    f"beforeEstimatedTokens={self._last_compaction_before_tokens} "
                    f"afterEstimatedTokens={breakdown.estimated_input_tokens} "
                    f"cycleCompactions={self.compaction_count}"
                )
                print(
                    f"[BUDGET] requestsRemaining={self.max_model_requests_per_cycle - self.model_request_count} "
                    f"finalizationReserved=true toolMode={tool_mode}"
                )
                print(breakdown.as_log_line())
            else:
                reason = "unsupported"
                if self.responses_features.compact:
                    reason = "limitReached" if self.compaction_count >= maximum_compactions else "finalizationReserve"
                print(
                    "[COMPACTION] "
                    f"triggered=false reason={reason} beforeEstimatedTokens={breakdown.estimated_input_tokens} "
                    f"cycleCompactions={self.compaction_count}"
                )

        # A continuation may carry previous large map results server-side. Give
        # the one permitted native compaction attempt a chance to replace that
        # history before enforcing the unchanged hard guard.
        self._enforce_context_limit(breakdown)

        request_number = self.model_request_count + 1
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "tools": active_tools,
            "input": request_input,
            **cache_fields,
        }
        if tool_mode == "finalization-only":
            request["tool_choice"] = {"type": "function", "name": "finish_decision"}
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        response = self.client.responses.create(**request)
        self.model_request_count = request_number
        self._last_presented_tool_names = {str(schema.get("name")) for schema in active_tools}
        self.previous_response_id = getattr(response, "id", None)
        # A continuation references prior Responses output server-side. Track
        # the response payload we can observe so its growth remains visible to
        # the preflight estimate without re-sending it from this process.
        self.carried_context_chars += serialized_chars(request_input) + serialized_chars(getattr(response, "output", []))
        self._log_response_usage(response, request_number, cache_key, "response")
        return response

    def _enforce_context_limit(self, breakdown: ContextBreakdown) -> None:
        if breakdown.estimated_input_tokens > self.max_input_tokens_per_request:
            print(
                "[CONTEXT LIMIT] "
                f"system={breakdown.system_chars} dynamic={breakdown.dynamic_input_chars} "
                f"tools={breakdown.tool_schema_chars} toolResults={breakdown.accumulated_tool_result_chars} "
                f"fullState={breakdown.full_state_chars} operations={breakdown.operations_chars} "
                f"fullStateSent={str(breakdown.full_state_sent).lower()} "
                f"estimatedInputTokens={breakdown.estimated_input_tokens}"
            )
            raise ModelContextLimitError(breakdown, self.max_input_tokens_per_request)

    def _ensure_prompt_runtime_state(self) -> None:
        if not hasattr(self, "responses_features"):
            self.responses_features = detect_responses_features(self.client.responses)
        if not hasattr(self, "compaction_count"):
            self.compaction_count = 0
        if not hasattr(self, "previous_response_id"):
            self.previous_response_id = None

    def _measure_model_request(
        self,
        input_items: list[dict[str, Any]],
        active_tools: list[dict[str, Any]],
        state: dict[str, Any] | None,
        context_payload: dict[str, Any] | None,
        *,
        carried_context_chars: int,
    ) -> ContextBreakdown:
        return measure_context(
            instructions=SYSTEM_INSTRUCTIONS,
            tools=active_tools,
            input_items=input_items,
            state=state,
            accumulated_tool_result_chars=self.accumulated_tool_result_chars,
            carried_context_chars=carried_context_chars,
            context_payload=context_payload,
            full_state_sent=False,
            tool_result_chars_this_round=getattr(self, "tool_result_chars_this_round", 0),
        )

    def _compact_continuation(
        self,
        previous_response_id: str,
        input_items: list[dict[str, Any]],
        before_estimated_tokens: int,
        cache_fields: dict[str, Any],
        cache_key: str,
    ) -> list[Any] | None:
        self.compaction_count += 1
        self.model_request_count += 1
        request_number = self.model_request_count
        self._last_compaction_before_tokens = before_estimated_tokens
        try:
            compacted = self.client.responses.compact(
                model=self.model,
                instructions=SYSTEM_INSTRUCTIONS,
                previous_response_id=previous_response_id,
                input=input_items,
                **cache_fields,
            )
            compacted_input = compacted_output_as_input(compacted)
        except Exception as exc:
            print(
                "[COMPACTION] "
                f"triggered=true success=false beforeEstimatedTokens={before_estimated_tokens} "
                f"cycleCompactions={self.compaction_count} error={exc}"
            )
            return None

        self._log_response_usage(compacted, request_number, cache_key, "compaction")
        return compacted_input

    def _log_response_usage(self, response: Any, request_number: int, cache_key: str, kind: str) -> None:
        usage = extract_usage(response, self.pricing)
        if usage.estimated_cost is not None:
            self.cycle_cost += usage.estimated_cost
            self.session_cost += usage.estimated_cost
        print(
            "[COST] "
            f"request={request_number} kind={kind} model={self.model} "
            f"inputTokens={format_metric(usage.input_tokens)} "
            f"cachedInputTokens={format_metric(usage.cached_input_tokens)} "
            f"cacheWriteTokens={format_metric(usage.cache_write_tokens)} "
            f"uncachedInputTokens={format_metric(usage.uncached_input_tokens)} "
            f"outputTokens={format_metric(usage.output_tokens)} "
            f"uncachedInputCost={format_cost(usage.uncached_input_cost)} "
            f"cachedInputCost={format_cost(usage.cached_input_cost)} "
            f"cacheWriteCost={format_cost(usage.cache_write_cost)} "
            f"outputCost={format_cost(usage.output_cost)} "
            f"estimatedRequestCost={format_cost(usage.estimated_cost)} "
            f"cycleCost={format_cost(self.cycle_cost if usage.estimated_cost is not None else None)} "
            f"sessionCost={format_cost(self.session_cost if usage.estimated_cost is not None else None)}"
        )
        hit_ratio = None
        if usage.input_tokens is not None and usage.input_tokens > 0 and usage.cached_input_tokens is not None:
            hit_ratio = usage.cached_input_tokens / usage.input_tokens
        print(
            "[CACHE] "
            f"key={cache_key} cachedInputTokens={format_metric(usage.cached_input_tokens)} "
            f"cacheWriteTokens={format_metric(usage.cache_write_tokens)} "
            f"hitRatio={format_ratio(hit_ratio)}"
        )

    def _execute_read_only_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            if name == "get_colony_state":
                result = self._get_colony_state_query().get(arguments["section"])
            elif name == "finish_decision":
                previous_handoff = self.state_store.get_decision_handoff()
                result = prepare_handoff(arguments, previous_handoff)
                self.pending_decision_handoff = result
                self.terminal_decision_finished = True
                previous_ids = {
                    item.get("id") for item in (previous_handoff or {}).get("openLoops", [])
                    if isinstance(item, dict)
                }
                current_ids = {item["id"] for item in result["openLoops"]}
                resolved_ids = sorted(item for item in previous_ids - current_ids if item)
                print(
                    f"[HANDOFF] finish_decision accepted chars={handoff_chars(result)} "
                    f"openLoops={len(result['openLoops'])} resolved={','.join(resolved_ids) or 'none'}"
                )
            elif name == "list_capabilities":
                active = self._get_active_tools()
                result = {
                    "capabilities": self.tool_registry.capability_list(active.groups),
                    "activeCapabilities": list(active.dynamic_groups),
                    "maxDynamicGroups": active.max_dynamic_groups,
                    "activeDynamicGroups": len(active.dynamic_groups),
                }
            elif name == "enable_capability":
                result = self._get_active_tools().enable(arguments["name"])
                if result.get("success") is True:
                    print(f"[TOOLS] capability={arguments['name']} enabled")
                    self._log_active_tools()
            elif name == "inspect_map":
                result = self.bridge.inspect_map(
                    arguments["min_x"],
                    arguments["min_z"],
                    arguments["max_x"],
                    arguments["max_z"],
                )
                if result.get("error") == "regionTooLarge":
                    width = abs(arguments["max_x"] - arguments["min_x"]) + 1
                    height = abs(arguments["max_z"] - arguments["min_z"]) + 1
                    result = {
                        **result,
                        "reason": "regionTooLarge",
                        "requestedWidth": width,
                        "requestedHeight": height,
                    }
            elif name == "inspect_room_at":
                result = self.bridge.inspect_room_at(arguments["x"], arguments["z"])
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
            self._record_verification_result(name, arguments, success=False)
            self._record_failed_call(name, arguments, result)
            return result

        failed = isinstance(result, dict) and (
            result.get("success") is False
            or result.get("gameLoaded") is False
            or bool(result.get("error"))
        )
        self._record_verification_result(name, arguments, success=not failed)
        if failed:
            print(f"[ERROR] {name}: {result.get('reason') or 'capability activation failed'}")
            self._record_failed_call(name, arguments, result)
            return result

        elapsed = time.monotonic() - started
        print(f"[RESULT] {name} completed in {elapsed:.2f}s")
        return {"success": True, "result": result, "elapsedSeconds": round(elapsed, 3)}

    def _record_verification_result(
        self, name: str, arguments: dict[str, Any], *, success: bool
    ) -> None:
        if self.dry_run:
            return
        store = getattr(self, "state_store", None)
        if store is None:
            return
        try:
            store.record_verification_result(name, arguments, success=success)
        except (OSError, StateStoreError) as exc:
            print(f"[WARNING] Could not persist verification stall metadata: {exc}")

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
                model_result = self._model_tool_result(
                    str(pending.get("name") or "command_status"), result, pending.get("arguments", {})
                )
                updates.append(reconciliation_message(model_result))
                continue

            status_value = status.get("status")
            elapsed = status.get("elapsedSeconds", 0.0)
            if status_value == "completed":
                print(
                    f"[RESULT] reconciled after {elapsed:.1f}s "
                    f"success={str(status.get('success') is True).lower()}"
                )
                status["reconciled"] = True
                model_result = self._model_tool_result(
                    str(pending.get("name") or "command_status"), status, pending.get("arguments", {})
                )
                updates.append(reconciliation_message(model_result))
                del self.uncertain_commands[command_id]
            elif status_value == "queued":
                status["success"] = None
                status["uncertain"] = True
                status["message"] = "Command is still queued during reconciliation."
                status["command"] = pending.get("command")
                print(f"[WARNING] command still queued after {elapsed:.1f}s: {command_id}")
                model_result = self._model_tool_result(
                    str(pending.get("name") or "command_status"), status, pending.get("arguments", {})
                )
                updates.append(reconciliation_message(model_result))
            else:
                status["success"] = None
                status["uncertain"] = True
                status["message"] = "Command returned an unknown status during reconciliation."
                status["command"] = pending.get("command")
                print(f"[WARNING] command status unknown after {elapsed:.1f}s: {command_id}")
                model_result = self._model_tool_result(
                    str(pending.get("name") or "command_status"), status, pending.get("arguments", {})
                )
                updates.append(reconciliation_message(model_result))

        return updates

    def _fresh_state_message(
        self,
        after_version: int,
        require_newer: bool,
        *,
        before_state: dict[str, Any] | None = None,
        include_context: bool = False,
    ) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
        stale = False
        note: str | None = None
        try:
            if require_newer:
                state = self.bridge.wait_for_state_after(after_version, timeout_ms=3000)
                if state.get("fresh") is False:
                    stale_state = state.get("state") if isinstance(state.get("state"), dict) else state
                    self.current_state = self._accept_authoritative_state(stale_state)
                    print(f"[WARNING] No post-command snapshot newer than version {after_version}")
                    print(f"[STATE] Post-action stale {summarize_state(stale_state)}")
                    stale = True
                    note = (
                        f"No post-command snapshot newer than version {after_version} was available. "
                        "This compact state may be stale; do not treat it as proof that completed commands failed."
                    )
                else:
                    self.current_state = self._accept_authoritative_state(state)
                    new_version = snapshot_version(state)
                    print(f"[STATE] Post-action authoritative version={new_version} {summarize_state(state)}")
                    note = "Fresh authoritative state after the completed command batch."
            else:
                state = self.bridge.get_state()
                self.current_state = self._accept_authoritative_state(state)
                print(f"[STATE] Current {summarize_state(state)}")
                note = "Current authoritative state after a read-only/no-mutation tool round."
        except RimWorldBridgeError as exc:
            print(f"[ERROR] Could not retrieve post-action state: {exc}")
            context = {
                "contextVersion": 1,
                "postToolState": True,
                "authoritative": False,
                "error": f"Fresh RimWorld state could not be retrieved after actions: {exc}",
            }
        else:
            store = getattr(self, "state_store", None)
            if store is not None:
                context = self._get_context_builder().build_post_tool_context(before_state, stale=stale, note=note)
            else:
                context = {
                    "contextVersion": 1,
                    "postToolState": True,
                    "authoritative": not stale,
                    "currentSummary": build_current_summary(self.current_state or {}),
                    "changesSinceToolRound": StateDiff.compare(before_state, self.current_state),
                    "note": note,
                }

        context = self._decorate_cycle_context(context)
        prefix = "Fresh authoritative RimWorld state" if not stale and context.get("authoritative") else "RimWorld state may be stale"
        text = prefix + ". Compact post-tool context:\n" + serialize_context(context)

        message = {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
        if include_context:
            return message, context
        return message


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


def model_result_chars(outputs: list[dict[str, Any]]) -> int:
    total = 0
    for output in outputs:
        if output.get("type") == "function_call_output":
            total += len(str(output.get("output") or ""))
        else:
            total += serialized_chars(output)
    return total


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


def format_ratio(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "unavailable"


def format_cost(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "unavailable"
