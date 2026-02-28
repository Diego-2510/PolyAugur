#!/usr/bin/env python3
"""
PolyAugur Production Runner v0.8
Usage:
    python run.py              # Continuous polling
    python run.py --once       # Single cycle
    python run.py --cycles 5   # Run 5 cycles
    python run.py --check      # Check outcomes only (no detection)
    python run.py --stats      # Show DB stats

Author: Diego Ringleb | Phase 8 | 2026-02-28
"""

import argparse
import logging
import os
import sys
from datetime import datetime

import config


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/polyaugur_{date_str}.log", encoding='utf-8'),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
    )


def main():
    parser = argparse.ArgumentParser(description="PolyAugur v0.8")
    parser.add_argument('--once', action='store_true', help='Single detection cycle')
    parser.add_argument('--cycles', type=int, default=None, help='Run N cycles')
    parser.add_argument('--interval', type=int, default=None, help='Override poll interval')
    parser.add_argument('--check', action='store_true', help='Check outcomes only')
    parser.add_argument('--stats', action='store_true', help='Show DB stats and exit')
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("polyaugur")

    if args.interval:
        config.POLL_INTERVAL_SEC = args.interval

    # Stats mode
    if args.stats:
        from src.signal_store import SignalStore
        store = SignalStore(config.SIGNAL_DB_PATH)
        stats = store.get_stats()
        print("\n📊 PolyAugur Signal Stats")
        print("=" * 40)
        for k, v in stats.items():
            print(f"   {k:20s}: {v}")
        recent = store.get_recent(hours=24)
        if recent:
            print(f"\n📋 Last 24h signals ({len(recent)}):")
            for s in recent[:10]:
                whale = "🐋" if s.get('trade_suspicious') else "  "
                print(
                    f"   {whale} {s.get('question', '')[:50]} | "
                    f"{s.get('trade')} | {s.get('confidence', 0):.0%} | "
                    f"{s.get('outcome', 'pending')}"
                )
        return

    # Check outcomes mode
    if args.check:
        from src.signal_store import SignalStore
        from src.performance_tracker import PerformanceTracker
        store = SignalStore(config.SIGNAL_DB_PATH)
        tracker = PerformanceTracker(store)
        summary = tracker.check_outcomes()
        print(f"\n📊 Outcome Check: {summary}")
        return

    # Normal run
    logger.info("=" * 60)
    logger.info("🚀 PolyAugur v0.8 — Insider Signal Detection")
    logger.info(f"   Poll interval:  {config.POLL_INTERVAL_SEC}s")
    logger.info(f"   Mistral:        {config.MISTRAL_MODEL}")
    logger.info(f"   DB:             {config.SIGNAL_DB_PATH}")
    logger.info(f"   Telegram:       {'✅' if config.TELEGRAM_BOT_TOKEN else '❌'}")
    logger.info(f"   CLOB analysis:  {'✅' if config.TRADE_ANALYSIS_ENABLED else '❌'}")
    logger.info(f"   Markets:        {config.MAX_PAGES * config.MARKETS_PER_PAGE} max")
    logger.info("=" * 60)

    from src.orchestrator import Orchestrator
    orch = Orchestrator()

    if args.once:
        summary = orch.run_cycle()
        logger.info(f"Done: {summary.get('signal_count', 0)} signals in {summary.get('cycle_time_sec', 0)}s")
    else:
        orch.run(max_cycles=args.cycles)


if __name__ == "__main__":
    main()
