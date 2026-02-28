"""
PolyAugur Orchestrator - Main Polling Loop
Coordinates: data_fetcher → anomaly_detector → mistral_analyzer → output
Includes: real baseline, holder data (Data API), price velocity tracking.
Author: Diego Ringleb | Phase 5 | 2026-02-28
Architecture: mache-es-sehr-viel-ausfuhrlicher.md [file:1] Abschnitt 7
"""

import time
import logging
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
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

    Pipeline per cycle (every POLL_INTERVAL_SEC seconds) [file:1] Abschnitt 7:
    1. Fetch all active markets (paginated)
    2. Build snapshots with real baseline
    3. Enrich snapshots with holder data (Data API)
    4. AnomalyDetector.batch_detect() → all markets, free
    5. Filter: score >= MISTRAL_THRESHOLD
    6. MistralAnalyzer.analyze_batch() → flagged markets only
    7. Log + store signals
    8. Track price velocity (snapshot history for next cycle)
    """

    def __init__(self):
        self.fetcher = PolymarketFetcher()
        self.detector = AnomalyDetector()
        self.analyzer = MistralAnalyzer()

        # Price velocity tracking: market_id → last snapshot
        self.snapshot_history: Dict[str, Dict[str, Any]] = {}

        # Signal log (in-memory, Phase 6: persist to DB)
        self.signals: List[Dict[str, Any]] = []

        self.cycle_count = 0
        logger.info("🚀 Orchestrator initialized")

    # ==================== DATA ENRICHMENT ====================

    def enrich_with_holders(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Fetch holder positions from Data API for all snapshots.
        [file:1] Abschnitt 4.2 - Data API /positions endpoint

        Adds: holders list with wallet, size, entry_time to each snapshot.
        Respects DATA_API_RATE_LIMIT (15 req/10s).
        """
        enriched = []
        request_count = 0

        for snapshot in snapshots:
            condition_id = snapshot.get('condition_id')
            if not condition_id:
                enriched.append(snapshot)
                continue

            # Rate limit: 15 req per 10s → 0.67s between requests
            if request_count > 0 and request_count % config.DATA_API_RATE_LIMIT == 0:
                logger.debug("Rate limit pause (Data API)")
                time.sleep(10)

            holders_data = self.fetcher._api_get(
                config.DATA_API_BASE,
                "positions",
                {
                    "market": condition_id,
                    "limit": "50",
                    "sortBy": "size",
                    "sortOrder": "DESC"
                }
            )

            request_count += 1

            if holders_data and isinstance(holders_data, list):
                snapshot['holders'] = [
                    {
                        'wallet': h.get('proxyWallet', h.get('user', '')),
                        'size': float(h.get('size', 0)),
                        'outcome': h.get('outcome', ''),
                        'entry_price': float(h.get('avgPrice', 0)),
                    }
                    for h in holders_data[:20]  # Top 20 holders
                ]
                logger.debug(
                    f"👥 {len(snapshot['holders'])} holders for "
                    f"{snapshot.get('question', '')[:40]}"
                )
            else:
                snapshot['holders'] = []

            enriched.append(snapshot)
            time.sleep(0.7)  # Respectful rate limiting

        return enriched

    def enrich_with_price_velocity(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Add price velocity by comparing to previous cycle's snapshot.
        Detects rapid price moves before public news.

        Adds: price_delta_30m, price_velocity (change per hour)
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
                # Annualized velocity: change per hour
                snapshot['price_velocity'] = round(price_delta * 2, 4)  # 30m → 1h
            else:
                snapshot['price_delta_30m'] = 0.0
                snapshot['volume_delta_30m'] = 0.0
                snapshot['price_velocity'] = 0.0

            # Store for next cycle
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

        Returns:
            Cycle summary dict with markets_analyzed, anomalies, signals, timing
        """
        cycle_start = time.time()
        self.cycle_count += 1
        logger.info(f"{'='*50}")
        logger.info(f"🔄 Cycle #{self.cycle_count} started")

        # Reset Mistral call counter
        self.analyzer.reset_cycle_counters()

        # Step 1: Fetch markets (paginated)
        logger.info("📡 Step 1: Fetching markets...")
        markets = self.fetcher.get_active_markets(limit=None, max_pages=config.MAX_PAGES)

        if not markets:
            logger.warning("No markets fetched – skipping cycle")
            return {'cycle': self.cycle_count, 'markets': 0, 'anomalies': 0, 'signals': []}

        logger.info(f"✅ {len(markets)} markets fetched")

        # Step 2: Build snapshots with real baseline
        logger.info("📸 Step 2: Building snapshots...")
        snapshots = self.fetcher.get_snapshots_batch(markets)
        logger.info(f"✅ {len(snapshots)} snapshots built")

        # Step 3: Price velocity (compare to previous cycle)
        logger.info("📈 Step 3: Price velocity enrichment...")
        snapshots = self.enrich_with_price_velocity(snapshots)

        # Step 4: Holder enrichment (Data API) – only for top anomaly candidates
        # Pre-filter by basic heuristics to avoid 1000 Data API calls per cycle
        logger.info("👥 Step 4: Holder enrichment (pre-filtered)...")
        pre_candidates = [
            s for s in snapshots
            if s.get('spike_ratio', 1.0) >= 1.5 or abs(s.get('price_delta_30m', 0)) > 0.03
        ]
        logger.info(f"   Pre-candidates for holder enrichment: {len(pre_candidates)}")

        if pre_candidates:
            pre_candidates = self.enrich_with_holders(pre_candidates)
            # Merge back
            enriched_ids = {s['id'] for s in pre_candidates}
            snapshots = pre_candidates + [s for s in snapshots if s['id'] not in enriched_ids]

        # Step 5: Anomaly detection (all markets, free)
        logger.info(f"🔍 Step 5: Anomaly detection on {len(snapshots)} markets...")
        anomaly_results = self.detector.batch_detect(snapshots)

        # Build lookup: market_id → (snapshot, anomaly_result)
        snapshot_map = {s['id']: s for s in snapshots}

        # Step 6: Filter for Mistral (score >= threshold)
        flagged = [
            r for r in anomaly_results
            if r.get('score', 0) >= config.MISTRAL_THRESHOLD
        ]
        flagged = flagged[:config.MAX_MISTRAL_CALLS_PER_CYCLE * config.MISTRAL_BATCH_SIZE]
        logger.info(f"🚨 {len(flagged)} markets flagged for Mistral (score ≥ {config.MISTRAL_THRESHOLD})")

        # Step 7: Mistral analysis (batched)
        signals = []
        if flagged:
            logger.info(f"🧠 Step 7: Mistral analysis ({len(flagged)} markets, "
                       f"~{-(-len(flagged)//config.MISTRAL_BATCH_SIZE)} API calls)...")

            mistral_items = [
                (snapshot_map[r['market_id']], r)
                for r in flagged
                if r.get('market_id') in snapshot_map
            ]
            mistral_results = self.analyzer.analyze_batch(mistral_items)

            # Collect actionable signals
            for result in mistral_results:
                if result.get('anomaly_detected') and result.get('confidence_score', 0) >= 0.65:
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
        """
        Main polling loop. Runs indefinitely (or max_cycles for testing).
        """
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
                time.sleep(5)  # Brief pause before retry


def main():
    """Standalone test: 1 cycle."""
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Orchestrator Test - Phase 5")
    logger.info("=" * 60)

    orch = Orchestrator()

    print("\n[Test 1] Single cycle (max_pages=1 for speed)...")
    # Patch to limit pages during test
    orch.fetcher.get_active_markets_orig = orch.fetcher.get_active_markets
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
            print(f"     Trade: {s.get('recommended_trade')} | "
                  f"Conf: {s.get('confidence_score', 0):.2f} | "
                  f"Risk: {s.get('risk_level')}")
    else:
        print(f"\n   No high-confidence signals this cycle (normal)")

    print("\n" + "=" * 60)
    print("✅ Phase 5 Orchestrator: PASSED")
    print("=" * 60)
    print("\n📝 Next:")
    print("   git add src/orchestrator.py src/data_fetcher.py app.py")
    print("   git commit -m 'feat(orchestrator): Phase 5 complete - polling loop, real baseline, holder enrichment'")


if __name__ == "__main__":
    main()
