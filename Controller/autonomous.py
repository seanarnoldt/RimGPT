#!/usr/bin/env python3
"""Run bounded autonomous RimGPT decision scheduling."""

from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from agent_controller import AgentController
from autonomous_scheduler import (
    DEFAULT_MAX_AUTONOMOUS_DECISIONS,
    DEFAULT_MAX_IMMEDIATE_FOLLOWUPS,
    DEFAULT_MAX_PROBLEM_ATTEMPTS,
    DEFAULT_MAX_SESSION_SPEND,
    DEFAULT_MAX_SESSION_MODEL_REQUESTS,
    DEFAULT_NORMAL_COOLDOWN_TICKS,
    DEFAULT_URGENT_VERIFICATION_GRACE_TICKS,
    DEFAULT_VERIFICATION_GRACE_TICKS,
    AutonomousScheduler,
    SchedulerConfig,
)
from bridge import RimWorldBridge, RimWorldBridgeError
from context_telemetry import Pricing
from decision_trigger import (
    DEFAULT_IDLE_PERSISTENCE_TICKS,
    DEFAULT_REVIEW_INTERVAL_TICKS,
    TriggerEvaluator,
)
from state_observer import StateObserver


DEFAULT_BRIDGE_URL = "http://127.0.0.1:47831"
DEFAULT_MODEL = "gpt-5.6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded autonomous RimGPT decision scheduling")
    parser.add_argument("--bridge-url", default=os.environ.get("RIMGPT_BRIDGE_URL", DEFAULT_BRIDGE_URL))
    parser.add_argument("--interval", type=float, default=2.0, help="State polling interval in wall-clock seconds")
    parser.add_argument("--review-ticks", type=int, default=DEFAULT_REVIEW_INTERVAL_TICKS)
    parser.add_argument("--idle-persistence-ticks", type=int, default=DEFAULT_IDLE_PERSISTENCE_TICKS)
    parser.add_argument("--cooldown-ticks", type=int, default=DEFAULT_NORMAL_COOLDOWN_TICKS)
    parser.add_argument("--max-decisions", type=int, default=DEFAULT_MAX_AUTONOMOUS_DECISIONS)
    parser.add_argument("--max-immediate-followups", type=int, default=DEFAULT_MAX_IMMEDIATE_FOLLOWUPS)
    parser.add_argument("--max-problem-attempts", type=int, default=DEFAULT_MAX_PROBLEM_ATTEMPTS)
    parser.add_argument("--verification-grace-ticks", type=int, default=DEFAULT_VERIFICATION_GRACE_TICKS)
    parser.add_argument(
        "--urgent-verification-grace-ticks",
        type=int,
        default=DEFAULT_URGENT_VERIFICATION_GRACE_TICKS,
    )
    parser.add_argument(
        "--max-session-model-requests",
        type=int,
        default=int(os.environ.get("RIMGPT_MAX_SESSION_MODEL_REQUESTS", DEFAULT_MAX_SESSION_MODEL_REQUESTS)),
    )
    parser.add_argument(
        "--max-session-spend",
        type=float,
        default=float(os.environ.get("RIMGPT_MAX_SESSION_SPEND", DEFAULT_MAX_SESSION_SPEND)),
        help="Maximum calculable API spend in USD for this autonomous process",
    )
    parser.add_argument(
        "--allow-unpriced-session",
        action="store_true",
        help="Explicitly allow autonomy without dollar telemetry; request and decision caps still apply",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    for name in (
        "interval", "review_ticks", "idle_persistence_ticks", "cooldown_ticks",
        "max_decisions", "max_immediate_followups", "max_problem_attempts", "max_session_spend",
        "max_session_model_requests", "verification_grace_ticks", "urgent_verification_grace_ticks",
    ):
        if getattr(args, name) <= 0:
            print(f"[ERROR] --{name.replace('_', '-')} must be positive")
            return 2
    if not os.environ.get("OPENAI_API_KEY"):
        print("[ERROR] OPENAI_API_KEY is not set")
        return 2

    pricing = Pricing.from_environment()
    if args.max_session_spend is not None and not pricing.base_configured and not args.allow_unpriced_session:
        print(
            "[ERROR] Autonomous spend cap requires calculable pricing. Configure "
            "RIMGPT_INPUT_COST_PER_MILLION and RIMGPT_OUTPUT_COST_PER_MILLION, or explicitly "
            "pass --allow-unpriced-session."
        )
        return 2

    bridge = RimWorldBridge(args.bridge_url)
    evaluator = TriggerEvaluator(
        review_interval_ticks=args.review_ticks,
        idle_persistence_ticks=args.idle_persistence_ticks,
    )
    observer = StateObserver(bridge, evaluator)
    controller = AgentController(
        bridge=bridge,
        model=os.environ.get("RIMGPT_MODEL", DEFAULT_MODEL),
        pricing=pricing,
    )
    scheduler = AutonomousScheduler(
        observer,
        controller,
        bridge,
        config=SchedulerConfig(
            normal_cooldown_ticks=args.cooldown_ticks,
            max_decisions=args.max_decisions,
            max_immediate_followups=args.max_immediate_followups,
            max_problem_attempts=args.max_problem_attempts,
            max_session_spend=args.max_session_spend,
            max_session_model_requests=args.max_session_model_requests,
            verification_grace_ticks=args.verification_grace_ticks,
            urgent_verification_grace_ticks=args.urgent_verification_grace_ticks,
            allow_unpriced_session=args.allow_unpriced_session,
        ),
    )
    print("[AUTONOMY] Scheduler started; Ctrl+C stops without changing game speed")
    try:
        while not scheduler.halted:
            try:
                scheduler.step()
            except (RimWorldBridgeError, ValueError) as exc:
                print(f"[ERROR] Observation failed: {exc}")
            if not scheduler.halted:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[AUTONOMY] Scheduler stopped by user")
        return 0
    return 1 if scheduler.halted else 0


if __name__ == "__main__":
    raise SystemExit(main())
