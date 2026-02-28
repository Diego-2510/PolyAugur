#!/usr/bin/env python3
"""
PolyAugur Production Runner
Usage:
    python run.py              # Continuous polling loop
    python run.py --once       # Single cycle (testing)
    python run.py --cycles 5   # Run exactly 5 cycles

Author: Diego Ringleb | Phase 7 | 2026-02-28
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import config


def setup_logging():
    """Configure logging to both console and file."""
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
    parser = argparse.ArgumentParser(description="PolyAugur - Polymarket Insider Signal Detection")
    parser.add_argument('--once', action='store_true', help='Run a single detection cycle')
    parser.add_argument('--cycles', type=int, default=None, help='Run N cycles then stop')
    parser.add_argument('--interval', type=int, default=None, help='Override poll interval (seconds)')
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("polyaugur")

    if args.interval:
        config.POLL_INTERVAL_SEC = args.interval

    logger.info("=" * 60)
    logger.info("🚀 PolyAugur v0.7 — Polymarket Insider Signal Detection")
    logger.info(f"   Poll interval:  {config.POLL_INTERVAL_SEC}s")
    logger.info(f"   Mistral model:  {config.MISTRAL_MODEL}")
    logger.info(f"   DB path:        {config.SIGNAL_DB_PATH}")
    logger.info(f"   Telegram:       {'✅ enabled' if config.TELEGRAM_BOT_TOKEN else '❌ disabled'}")
    logger.info(f"   CLOB analysis:  ✅ enabled")
    logger.info(f"   Max pages:      {config.MAX_PAGES} ({config.MAX_PAGES * config.MARKETS_PER_PAGE} markets)")
    logger.info("=" * 60)

    from src.orchestrator import Orchestrator
    orch = Orchestrator()

    if args.once:
        logger.info("Mode: single cycle (--once)")
        summary = orch.run_cycle()
        logger.info(f"Done: {summary.get('signal_count', 0)} signals in {summary.get('cycle_time_sec', 0)}s")
    else:
        max_cycles = args.cycles or None
        if max_cycles:
            logger.info(f"Mode: {max_cycles} cycles")
        else:
            logger.info("Mode: continuous polling (Ctrl+C to stop)")
        orch.run(max_cycles=max_cycles)


if __name__ == "__main__":
    main()
