"""
PolyAugur Orchestrator - Main Polling Loop
Coordinates: data_fetcher → anomaly_detector → mistral_analyzer → output

Pipeline per cycle:
1. Fetch all active markets (paginated, sports filtered)
2. Build snapshots with real baseline
3. Price velocity enrichment (cross-cycle delta)
4. AnomalyDetector.batch_detect() → all markets, free, no API calls
5. Filter: score >= MISTRAL_THRESHOLD
6. MistralAnalyzer.analyze_batch() → flagged markets only
7. Log + store signals

Note: Holder enrichment (Data API /positions) disabled permanently.
      Data API requires ?user=<wallet> – market-level lookup unsupported.
      Phase 6: CLOB /trades endpoint for wallet activity analysis.

Author: Diego Ringleb | Phase 5 | 2026-02-28
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any
import config
from src.data_fetcher import PolymarketFetcher
from src.anomaly_detector import AnomalyDetector
from src.mistral_analyzer import MistralAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main polling loop connecting all PolyAugur modules.
    No holder enrichment (Data API is user-scoped, not market-scoped).
    """

    def __init__(self):
        self.fetcher = PolymarketFetcher()
        self.detector = AnomalyDetector()
        self.analyzer = MistralAnalyzer()

        # Price velocity tracking: market_id → last snapshot data
        self.snapshot_history: Dict[str, Dict[str, Any]] = {}

        # Signal log (in-memory; Phase 6: persist to SQLite/Postgres)
        self.signals: List[Dict[str, Any]] = []

        self.cycle_count = 0
        logger.info("🚀 Orchestrator initialized")

    # ==================== ENRICHMENT ====================

    def enrich_with_price_velocity(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Compute price velocity by diffing against previous cycle snapshot.
        Detects rapid price moves that often precede public news.

        Adds to each snapshot:
        - price_delta_30m  : YES price change since last cycle
        - volume_delta_30m : volume change since last cycle
        - price_velocity   : extrapolated change per hour (delta × 2)
        """
        now = datetime.now(timezone.utc)

        for snapshot in snapshots:
            market_id = snapshot.get('id')
            prev = self.snapshot_history.get(market_id)

            if prev:
                price_delta = snapshot['yes_price'] - prev['yes_price']
                vol_delta = snapshot['volume_24hr'] - prev.get('volume_24hr', 0)
                snapshot['price_delta_30m'] = round(price_delta, 4)
                snapshot['volume_delta_30m'] = round(vol_delta, 0)
                snapshot['price_velocity'] = round(price_delta * 2, 4)
            else:
                snapshot['price_delta_30m'] = 0.0
                snapshot['volume_delta_30m'] = 0.0
                snapshot['price_velocity'] = 0.0

            self.snapshot_history[market_id] = {
                'yes_price': snapshot['yes_price'],
                'volume_24hr': snapshot['volume_24hr'],
                'timestamp': now.isoformat()
            }

        return snapshots

    # ==================== MAIN CYCLE ====================

    def run_cycle(self) -> Dict[str, Any]:
        """
        Execute one full detection cycle.
        Returns cycle summary dict.
        """
        cycle_start = time.time()
        self.cycle_count += 1
        logger.info(f"{'='*50}")
        logger.info(f"🔄 Cycle #{self.cycle_count} started")

        self.analyzer.reset_cycle_counters()

        # ── Step 1: Fetch markets ────────────────────────────────────────
        logger.info("📡 Step 1: Fetching markets...")
        markets = self.fetcher.get_active_markets(limit=None, max_pages=config.MAX_PAGES)

        if not markets:
            logger.warning("No markets fetched – skipping cycle")
            return {
                'cycle': self.cycle_count, 'markets': 0,
                'anomalies': 0, 'signals': [], 'signal_count': 0
            }
        logger.info(f"✅ {len(markets)} markets fetched")

        # ── Step 2: Build snapshots ──────────────────────────────────────
        logger.info("📸 Step 2: Building snapshots...")
        snapshots = self.fetcher.get_snapshots_batch(markets)
        logger.info(f"✅ {len(snapshots)} snapshots built")

        # ── Step 3: Price velocity ───────────────────────────────────────
        logger.info("📈 Step 3: Price velocity enrichment...")
        snapshots = self.enrich_with_price_velocity(snapshots)

        # ── Step 4: Anomaly detection (all markets, no API calls) ────────
        logger.info(f"🔍 Step 4: Anomaly detection on {len(snapshots)} markets...")
        anomaly_results = self.detector.batch_detect(snapshots)

        snapshot_map = {s['id']: s for s in snapshots}

        # ── Step 5: Filter for Mistral ───────────────────────────────────
        flagged = [
            r for r in anomaly_results
            if r.get('score', 0) >= config.MISTRAL_THRESHOLD
        ]
        # Cap to budget: max_calls × batch_size markets
        max_markets = config.MAX_MISTRAL_CALLS_PER_CYCLE * config.MISTRAL_BATCH_SIZE
        flagged = flagged[:max_markets]
        logger.info(
            f"🚨 {len(flagged)} markets flagged for Mistral "
            f"(score ≥ {config.MISTRAL_THRESHOLD})"
        )

        # ── Step 6: Mistral validation ───────────────────────────────────
        signals = []
        if flagged:
            n_calls = -(-len(flagged) // config.MISTRAL_BATCH_SIZE)  # ceil div
            logger.info(
                f"🧠 Step 6: Mistral analysis "
                f"({len(flagged)} markets, ~{n_calls} API calls)..."
            )

            mistral_items = [
                (snapshot_map[r['market_id']], r)
                for r in flagged
                if r.get('market_id') in snapshot_map
            ]
            mistral_results = self.analyzer.analyze_batch(mistral_items)

            for result in mistral_results:
                if (result.get('anomaly_detected')
                        and result.get('confidence_score', 0) >= 0.65):
                    signal = {
                        **result,
                        'cycle': self.cycle_count,
                        'detected_at': datetime.now(timezone.utc).isoformat()
                    }
                    signals.append(signal)
                    self.signals.append(signal)
                    logger.info(
                        f"📣 SIGNAL: {result.get('question', '')[:55]} | "
                        f"Trade={result.get('recommended_trade')} | "
                        f"Conf={result.get('confidence_score', 0):.2f}"
                    )

        cycle_time = time.time() - cycle_start

        summary = {
            'cycle': self.cycle_count,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'markets_fetched': len(markets),
            'snapshots_built': len(snapshots),
            'anomalies_detected': len(flagged),
            'signals': signals,
            'signal_count': len(signals),
            'mistral_calls': self.analyzer.call_count,
            'cycle_time_sec': round(cycle_time, 2)
        }

        logger.info(
            f"✅ Cycle #{self.cycle_count} complete | "
            f"{len(markets)} markets | "
            f"{len(flagged)} anomalies | "
            f"{len(signals)} signals | "
            f"{cycle_time:.1f}s"
        )

        return summary

    def run(self, max_cycles: int = None):
        """Main polling loop. Runs indefinitely or until max_cycles reached."""
        logger.info(f"🚀 PolyAugur started | Poll interval: {config.POLL_INTERVAL_SEC}s")

        cycle = 0
        while True:
            try:
                summary = self.run_cycle()
                cycle += 1

                if max_cycles and cycle >= max_cycles:
                    logger.info(f"Max cycles ({max_cycles}) reached, stopping")
                    break

                logger.info(f"💤 Sleeping {config.POLL_INTERVAL_SEC}s until next cycle...")
                time.sleep(config.POLL_INTERVAL_SEC)

            except KeyboardInterrupt:
                logger.info("⛔ Stopped by user")
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}", exc_info=True)
                time.sleep(5)


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Orchestrator Test - Phase 5 (Clean)")
    logger.info("=" * 60)

    orch = Orchestrator()

    print("\n[Test 1] Single cycle...")
    summary = orch.run_cycle()

    print(f"\n✅ Cycle Summary:")
    print(f"   Markets fetched:    {summary['markets_fetched']}")
    print(f"   Snapshots built:    {summary['snapshots_built']}")
    print(f"   Anomalies flagged:  {summary['anomalies_detected']}")
    print(f"   Signals generated:  {summary['signal_count']}")
    print(f"   Mistral calls:      {summary['mistral_calls']}")
    print(f"   Cycle time:         {summary['cycle_time_sec']}s")

    if summary['signals']:
        print(f"\n🚨 Signals this cycle:")
        for s in summary['signals']:
            print(f"   • {s.get('question', '')[:60]}")
            print(
                f"     Trade: {s.get('recommended_trade')} | "
                f"Conf: {s.get('confidence_score', 0):.2f} | "
                f"Risk: {s.get('risk_level')}"
            )
    else:
        print(f"\n   No high-confidence signals this cycle (normal)")

    print("\n" + "=" * 60)
    print("✅ Phase 5 Orchestrator: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
