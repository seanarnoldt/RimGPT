#!/usr/bin/env python3
"""Run the read-only RimGPT passive observer."""

from __future__ import annotations

import argparse
import time

from bridge import RimWorldBridge, RimWorldBridgeError
from decision_trigger import DEFAULT_REVIEW_INTERVAL_TICKS, TriggerEvaluator
from state_observer import StateObserver
from state_store import snapshot_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe RimWorld state without running the AI controller")
    parser.add_argument("--bridge-url", default="http://127.0.0.1:47831")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in wall-clock seconds")
    parser.add_argument("--review-ticks", type=int, default=DEFAULT_REVIEW_INTERVAL_TICKS)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.review_ticks <= 0:
        parser.error("--review-ticks must be positive")

    observer = StateObserver(
        RimWorldBridge(args.bridge_url),
        TriggerEvaluator(review_interval_ticks=args.review_ticks),
    )
    try:
        while True:
            try:
                decision = observer.observe_once()
                current = observer.current or {}
                line = (
                    f"ticks={decision.ticks_game if decision.ticks_game is not None else 'unknown'} "
                    f"snapshot={snapshot_version(current) if snapshot_version(current) is not None else 'unknown'} "
                    f"trigger={'true' if decision.should_trigger else 'false'}"
                )
                if decision.should_trigger:
                    line += f' priority={decision.priority} kind={decision.kind} reason={decision.reason!r}'
                print(line, flush=True)
            except (RimWorldBridgeError, ValueError) as exc:
                print(f"[ERROR] {exc}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Observer stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
