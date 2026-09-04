#!/usr/bin/env python3
import argparse
import os
import sys

from dotenv import load_dotenv

from agent_controller import AgentController
from bridge import RimWorldBridge, RimWorldBridgeError


DEFAULT_BRIDGE_URL = "http://127.0.0.1:47831"
DEFAULT_MODEL = "gpt-5.6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one manual RimGPT AI decision cycle.")
    parser.add_argument("--dry-run", action="store_true", help="Ask the model but do not execute bridge commands.")
    parser.add_argument(
        "--bridge-url",
        default=os.environ.get("RIMGPT_BRIDGE_URL", DEFAULT_BRIDGE_URL),
        help=f"RimGPT bridge URL. Defaults to {DEFAULT_BRIDGE_URL}.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=4,
        help="Maximum model tool-call rounds before stopping.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

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
    )

    try:
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
