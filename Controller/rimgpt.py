#!/usr/bin/env python3
import argparse
import os
import sys

from dotenv import load_dotenv

from agent_controller import (
    DEFAULT_MAX_TOOL_ROUNDS,
    DEFAULT_MAX_TOTAL_TOOL_CALLS,
    DEFAULT_MAX_WRITE_COMMANDS,
    DEFAULT_REPEATED_FAILED_CALL_LIMIT,
    AgentController,
)
from bridge import RimWorldBridge, RimWorldBridgeError
from context_telemetry import DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST, DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE


DEFAULT_BRIDGE_URL = "http://127.0.0.1:47831"
DEFAULT_MODEL = "gpt-5.6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one manual RimGPT AI decision cycle.")
    parser.add_argument("--dry-run", action="store_true", help="Ask the model but do not execute bridge commands.")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run a development test sequence capped at five top-level decision cycles.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        help="Number of top-level decision cycles for --test (1 through 5).",
    )
    parser.add_argument(
        "--bridge-url",
        default=os.environ.get("RIMGPT_BRIDGE_URL", DEFAULT_BRIDGE_URL),
        help=f"RimGPT bridge URL. Defaults to {DEFAULT_BRIDGE_URL}.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=DEFAULT_MAX_TOOL_ROUNDS,
        help="Maximum model tool-call rounds before stopping.",
    )
    parser.add_argument(
        "--max-total-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOTAL_TOOL_CALLS,
        help="Maximum total model tool calls per decision cycle.",
    )
    parser.add_argument(
        "--max-write-commands",
        type=int,
        default=DEFAULT_MAX_WRITE_COMMANDS,
        help="Maximum submitted write commands per decision cycle.",
    )
    parser.add_argument(
        "--repeated-failed-call-limit",
        type=int,
        default=DEFAULT_REPEATED_FAILED_CALL_LIMIT,
        help="Terminate after this many substantially identical failed calls.",
    )
    parser.add_argument(
        "--max-input-tokens-per-request",
        type=int,
        default=environment_int("RIMGPT_MAX_INPUT_TOKENS_PER_REQUEST", DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST),
        help="Preflight input-token ceiling for every Responses API request.",
    )
    parser.add_argument(
        "--max-model-requests-per-cycle",
        type=int,
        default=environment_int("RIMGPT_MAX_MODEL_REQUESTS_PER_CYCLE", DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE),
        help="Maximum Responses API requests in one top-level decision cycle.",
    )
    return parser.parse_args()


def environment_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc


def resolve_cycle_count(args: argparse.Namespace) -> int:
    if args.max_cycles is not None and not args.test:
        raise ValueError("--max-cycles is only valid with --test")
    if not args.test:
        return 1
    cycles = 1 if args.max_cycles is None else args.max_cycles
    if cycles < 1 or cycles > 5:
        raise ValueError("--test --max-cycles must be between 1 and 5")
    return cycles


def main() -> int:
    load_dotenv()
    try:
        args = parse_args()
    except argparse.ArgumentTypeError as exc:
        print(f"[ERROR] {exc}")
        return 2

    if args.max_tool_rounds < 1:
        print("[ERROR] --max-tool-rounds must be at least 1")
        return 2
    if args.max_total_tool_calls < 1:
        print("[ERROR] --max-total-tool-calls must be at least 1")
        return 2
    if args.max_write_commands < 1:
        print("[ERROR] --max-write-commands must be at least 1")
        return 2
    if args.repeated_failed_call_limit < 1:
        print("[ERROR] --repeated-failed-call-limit must be at least 1")
        return 2
    if args.max_input_tokens_per_request < 1:
        print("[ERROR] --max-input-tokens-per-request must be at least 1")
        return 2
    if args.max_model_requests_per_cycle < 1:
        print("[ERROR] --max-model-requests-per-cycle must be at least 1")
        return 2
    try:
        cycles = resolve_cycle_count(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2

    if not os.environ.get("OPENAI_API_KEY"):
        print("[ERROR] OPENAI_API_KEY is not set")
        return 2

    model = os.environ.get("RIMGPT_MODEL", DEFAULT_MODEL)
    bridge = RimWorldBridge(args.bridge_url)
    controller = AgentController(
        bridge=bridge,
        model=model,
        dry_run=args.dry_run,
        max_tool_rounds=args.max_tool_rounds,
        max_total_tool_calls=args.max_total_tool_calls,
        max_write_commands=args.max_write_commands,
        repeated_failed_call_limit=args.repeated_failed_call_limit,
        max_input_tokens_per_request=args.max_input_tokens_per_request,
        max_model_requests_per_cycle=args.max_model_requests_per_cycle,
    )

    try:
        for cycle_index in range(cycles):
            if cycles > 1:
                print(f"[MODEL] Development test cycle {cycle_index + 1}/{cycles}")
            controller.run_once()
    except RimWorldBridgeError as exc:
        print(f"[ERROR] {exc}")
        return 1
    except KeyboardInterrupt:
        print("[ERROR] Interrupted")
        return 130
    except Exception as exc:
        print(f"[ERROR] Unexpected failure: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
