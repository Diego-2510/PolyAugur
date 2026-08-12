#!/usr/bin/env python3
"""PolyAugur command-line runtime."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime

import config


def setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/polyaugur_{date_str}.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PolyAugur")
    parser.add_argument("--once", action="store_true", help="Run one detection cycle")
    parser.add_argument("--cycles", type=int, default=None, help="Run N cycles")
    parser.add_argument("--interval", type=int, default=None, help="Override poll interval")
    parser.add_argument("--check", action="store_true", help="Check outcomes only")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--health", action="store_true", help="Run pre-flight checks only")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip pre-flight checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging()
    logger = logging.getLogger("polyaugur")

    if args.interval is not None:
        if args.interval <= 0:
            logger.error("--interval must be greater than zero")
            return 2
        config.POLL_INTERVAL_SEC = args.interval

    if args.health:
        from src.health import main as health_main

        return health_main()

    if args.stats:
        from src.signal_store import SignalStore

        store = SignalStore(config.SIGNAL_DB_PATH)
        stats = store.get_stats()
        print("\nPolyAugur signal stats")
        print("=" * 40)
        for key, value in stats.items():
            print(f"   {key:20s}: {value}")
        return 0

    if args.check:
        from src.performance_tracker import PerformanceTracker
        from src.signal_store import SignalStore

        store = SignalStore(config.SIGNAL_DB_PATH)
        summary = PerformanceTracker(store).check_outcomes()
        print(f"\nOutcome check: {summary}")
        return 0

    if not args.skip_preflight:
        from src.health import HealthMonitor, _format_result

        results = HealthMonitor().preflight_check()
        logger.info("Pre-flight check:")
        for name, value in results.items():
            logger.info("   %s: %s", name, _format_result(name, value))

        critical = {"gamma_api", "db_writable"}
        if config.TRADE_ANALYSIS_ENABLED:
            critical.add("clob_api")
        failed = [name for name in critical if results.get(name) is not True]
        if failed:
            logger.error("Critical pre-flight checks failed: %s", ", ".join(sorted(failed)))
            return 1

    logger.info("=" * 60)
    logger.info("PolyAugur — prediction-market anomaly detection research system")
    logger.info("Poll interval: %ss", config.POLL_INTERVAL_SEC)
    logger.info(
        "LLM-assisted review: %s", "configured" if config.MISTRAL_API_KEY else "fallback only"
    )
    logger.info("DB: %s", config.SIGNAL_DB_PATH)
    logger.info("Telegram: %s", "configured" if config.TELEGRAM_BOT_TOKEN else "disabled")
    logger.info("CLOB analysis: %s", "enabled" if config.TRADE_ANALYSIS_ENABLED else "disabled")
    logger.info("Market scan cap: %s", config.MAX_PAGES * config.MARKETS_PER_PAGE)
    logger.info("=" * 60)

    from src.health import HealthMonitor
    from src.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    health = HealthMonitor()

    if args.once:
        try:
            summary = orchestrator.run_cycle()
            health.record_cycle(summary)
            logger.info(
                "Done: %s signals in %ss",
                summary.get("signal_count", 0),
                summary.get("cycle_time_sec", 0),
            )
            return 0
        except Exception as exc:
            health.record_error(str(exc))
            logger.error("Cycle failed: %s", exc, exc_info=True)
            return 1

    cycle = 0
    while True:
        try:
            summary = orchestrator.run_cycle()
            health.record_cycle(summary)
            cycle += 1

            if health.should_send_ping():
                health.send_health_ping()

            if args.cycles and cycle >= args.cycles:
                logger.info("Max cycles (%s) reached", args.cycles)
                return 0

            logger.info("Sleeping %ss...", config.POLL_INTERVAL_SEC)
            time.sleep(config.POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            return 0
        except Exception as exc:
            health.record_error(str(exc))
            logger.error("Cycle error: %s", exc, exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
