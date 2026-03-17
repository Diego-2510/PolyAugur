"""
PolyAugur Orchestrator - Main Polling Loop
Phase 15: Blacklist mode, anomaly_score fix, Topic-Gate entfernt.

Pipeline per cycle:
1.   Fetch all active markets (paginated, sports filtered)
2.   Build snapshots with real baseline
3.   Price velocity enrichment
3.5  Elite pre-filter (spike, horizon, recency gates)
4.   AnomalyDetector.batch_detect() → Blacklist exclusion + topic score boosters
5.   Filter: score >= MISTRAL_THRESHOLD, top-MAX markets
6.   MistralAnalyzer.analyze_batch() → confirm >= MISTRAL_CONFIRM_MIN
7.   Trade analysis (CLOB) → confirmed signals only
8.   Whale confidence boost → Deduplicate → Store → Telegram
9.   Performance check (every 10 cycles)

Phase 15 changes vs Phase 14:
- Topic-Gate (Step 4.5) REMOVED — blacklist mode, Mistral is the quality gate
- _process_signal(): anomaly_score now sourced from anomaly_result (fixes anomaly_score=0.0 bug)
- anomaly_result passed through to _process_signal for correct score persistence
- Blacklist logging added to Step 4 summary
- Phase string updated throughout

Author: Diego Ringleb | Phase 15 | 2026-03-17
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any
import config
from src.data_fetcher import PolymarketFetcher
from src.anomaly_detector import AnomalyDetector
from src.mistral_analyzer import MistralAnalyzer
from src.signal_store import SignalStore
from src.telegram_notifier import TelegramNotifier
from src.trade_analyzer import TradeAnalyzer
from src.performance_tracker import PerformanceTracker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main polling loop. Phase 15: Blacklist mode.

    Key changes from Phase 14:
    - REQUIRE_CRITICAL_TOPIC gate removed. All markets with sufficient anomaly
      score pass to Mistral. Topic keywords boost score but do not gate.
    - anomaly_score correctly sourced from AnomalyDetector result (not Mistral dict).
    - Blacklist exclusions logged at Step 4.
    """

    def __init__(self):
        self.fetcher   = PolymarketFetcher()
        self.detector  = AnomalyDetector()
        self.analyzer  = MistralAnalyzer()
        self.trader    = TradeAnalyzer()
        self.store     = SignalStore(config.SIGNAL_DB_PATH)
        self.notifier  = TelegramNotifier()
        self.tracker   = PerformanceTracker(self.store)

        self.snapshot_history: Dict[str, Dict[str, Any]] = {}
        self.cycle_count = 0
        logger.info("🚀 Orchestrator initialized (Phase 15 – Blacklist Mode)")

    # ==================== ENRICHMENT ====================

    def enrich_with_price_velocity(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute cross-cycle price and volume delta."""
        now = datetime.now(timezone.utc)

        for snapshot in snapshots:
            market_id = snapshot.get('id')
            prev = self.snapshot_history.get(market_id)

            if prev:
                price_delta = snapshot['yes_price'] - prev['yes_price']
                vol_delta   = snapshot['volume_24hr'] - prev.get('volume_24hr', 0)
                snapshot['price_delta_30m']  = round(price_delta, 4)
                snapshot['volume_delta_30m'] = round(vol_delta, 0)
                snapshot['price_velocity']   = round(price_delta * 2, 4)
            else:
                snapshot['price_delta_30m']  = 0.0
                snapshot['volume_delta_30m'] = 0.0
                snapshot['price_velocity']   = 0.0

            self.snapshot_history[market_id] = {
                'yes_price':   snapshot['yes_price'],
                'volume_24hr': snapshot['volume_24hr'],
                'timestamp':   now.isoformat()
            }

        return snapshots

    # ==================== CONFIDENCE BOOST ====================

    def _apply_whale_boost(
        self, result: Dict[str, Any], trade_metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Boost confidence score when on-chain evidence supports the signal.

        Boost logic:
        - Suspicious whale activity:        +0.05
        - Directional bias matches trade:   +0.05
        - Burst score >= 3.0:               +0.03
        - Top wallet >= 40%:                +0.02
        - Max total boost: 0.15 (capped)
        """
        boost    = 0.0
        raw_conf = result.get('confidence_score', 0.0)

        if trade_metrics.get('suspicious'):
            boost += 0.05

        trade_dir = result.get('recommended_trade', 'HOLD')
        dom_side  = trade_metrics.get('dominant_side', 'NONE')
        if (trade_dir == 'BUY_YES' and dom_side == 'BUY') or            (trade_dir == 'BUY_NO'  and dom_side == 'SELL'):
            boost += 0.05

        if trade_metrics.get('burst_score', 1.0) >= 3.0:
            boost += 0.03

        if trade_metrics.get('top_wallet_pct', 0) >= 0.40:
            boost += 0.02

        boost        = min(boost, 0.15)
        boosted_conf = min(raw_conf + boost, 0.99)

        result['confidence_raw']   = raw_conf
        result['confidence_boost'] = round(boost, 3)
        result['confidence_score'] = round(boosted_conf, 3)

        if boost > 0:
            logger.info(
                f"🐋 Whale boost: {raw_conf:.2f} → {boosted_conf:.2f} "
                f"(+{boost:.2f}) for {result.get('question', '')[:40]}"
            )

        return result

    # ==================== SIGNAL HANDLING ====================

    def _process_signal(
        self, result: Dict[str, Any],
        snapshot: Dict[str, Any],
        anomaly_result: Dict[str, Any],
        trade_metrics: Dict[str, Any],
        cycle: int
    ) -> bool:
        """
        Persist + notify a single confirmed signal.

        Phase 15 fix: anomaly_score sourced from anomaly_result (AnomalyDetector),
        not from result (Mistral dict). Mistral dict has no 'score' key →
        previously all signals stored anomaly_score=0.0.

        Returns True if signal was new (not duplicate).
        """
        market_id = result.get('market_id', snapshot.get('id', ''))

        if self.store.is_duplicate(market_id):
            logger.debug(f"⏭️ Duplicate skipped: {result.get('question', '')[:45]}")
            return False

        # Phase 15 Fix: anomaly_score aus AnomalyDetector-Result
        anomaly_score = anomaly_result.get('score', result.get('score', 0.0))

        enriched = {
            **result,
            'market_id':          market_id,
            'yes_price':          snapshot.get('yes_price', 0.5),
            'volume_24hr':        snapshot.get('volume_24hr', 0),
            'spike_ratio':        snapshot.get('spike_ratio', 1.0),
            'end_date_iso':       snapshot.get('end_date_iso'),
            'cycle':              cycle,
            'detected_at':        datetime.now(timezone.utc).isoformat(),
            # Phase 15: korrekte anomaly_score Persistierung
            'score':              anomaly_score,
            'anomaly_score':      anomaly_score,
            # Trade / Whale Metrics
            'whale_count':        trade_metrics.get('whale_count', 0),
            'whale_volume_pct':   trade_metrics.get('whale_volume_pct', 0),
            'top_wallet_pct':     trade_metrics.get('top_wallet_pct', 0),
            'unique_wallets':     trade_metrics.get('unique_wallets', 0),
            'directional_bias':   trade_metrics.get('directional_bias', 0.5),
            'dominant_side':      trade_metrics.get('dominant_side', 'NONE'),
            'burst_score':        trade_metrics.get('burst_score', 1.0),
            'trade_suspicious':   trade_metrics.get('suspicious', False),
            'suspicious_reasons': trade_metrics.get('suspicious_reasons', []),
        }

        row_id = self.store.save(enriched)

        sent = self.notifier.send_signal(enriched)
        if sent:
            self.store.mark_telegram_sent(row_id)

        whale_tag = " 🐋" if trade_metrics.get('suspicious') else ""
        boost_tag = ""
        if result.get('confidence_boost', 0) > 0:
            boost_tag = f" (↑{result['confidence_boost']:.0%})"

        logger.info(
            f"📣 SIGNAL #{row_id}: {result.get('question', '')[:45]} | "
            f"Trade={result.get('recommended_trade')} | "
            f"Conf={result.get('confidence_score', 0):.2f}{boost_tag} | "
            f"AnomalyScore={anomaly_score:.3f} | "
            f"Telegram={'✅' if sent else '⏭️'}{whale_tag}"
        )

        return True

    # ==================== MAIN CYCLE ====================

    def run_cycle(self) -> Dict[str, Any]:
        """Execute one full detection cycle."""
        cycle_start = time.time()
        self.cycle_count += 1
        logger.info(f"{'='*50}")
        logger.info(f"🔄 Cycle #{self.cycle_count} started")

        self.analyzer.reset_cycle_counters()
        self.trader.reset_cycle_counters()

        # ── Step 1: Fetch ────────────────────────────────────────────────
        logger.info("📡 Step 1: Fetching markets...")
        markets = self.fetcher.get_active_markets(limit=None, max_pages=config.MAX_PAGES)

        if not markets:
            logger.warning("No markets fetched – skipping cycle")
            return {
                'cycle': self.cycle_count, 'markets_fetched': 0,
                'snapshots_built': 0, 'elite_pre_filtered': 0,
                'anomalies_detected': 0, 'signals': [],
                'signal_count': 0, 'whale_signals': 0
            }
        logger.info(f"✅ {len(markets)} markets fetched")

        # ── Step 2: Snapshots ────────────────────────────────────────────
        logger.info("📸 Step 2: Building snapshots...")
        snapshots = self.fetcher.get_snapshots_batch(markets)
        snapshots_raw_count = len(snapshots)
        logger.info(f"✅ {snapshots_raw_count} snapshots built")

        # ── Step 3: Price velocity ───────────────────────────────────────
        logger.info("📈 Step 3: Price velocity enrichment...")
        snapshots = self.enrich_with_price_velocity(snapshots)

        # ── Step 3.5: Elite Pre-Filter ───────────────────────────────────
        # Hard quantitative gates before AnomalyDetector.
        # Topic-agnostic: does not require insider keyword match.
        logger.info(
            f"🎯 Step 3.5: Elite pre-filter "
            f"(spike≥{config.MIN_SPIKE_RATIO}x, "
            f"≤{config.MAX_DAYS_TO_CLOSE}d, "
            f"recency≥{config.MIN_RECENCY_RATIO:.0%})..."
        )
        now_utc = datetime.now(timezone.utc)
        filtered_snapshots = []

        for s in snapshots:
            # Gate 1: Volume spike must be significant
            if s.get('spike_ratio', 1.0) < config.MIN_SPIKE_RATIO:
                continue

            # Gate 2: Market must close within MAX_DAYS_TO_CLOSE days
            end_date = s.get('end_date_iso')
            if end_date:
                try:
                    closes    = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    days_left = (closes - now_utc).days
                    if days_left > config.MAX_DAYS_TO_CLOSE:
                        continue
                except (ValueError, TypeError):
                    pass  # No valid date → let it through

            # Gate 3: Recent surge — majority of volume must be in last 24h
            vol_total = s.get('volume', 0)
            vol_24h   = s.get('volume_24hr', 0)
            if vol_total > 0:
                recency = vol_24h / vol_total
                if recency < config.MIN_RECENCY_RATIO:
                    continue

            filtered_snapshots.append(s)

        elite_filtered_count = len(snapshots) - len(filtered_snapshots)
        logger.info(
            f"🎯 Elite pre-filter: {len(filtered_snapshots)}/{snapshots_raw_count} "
            f"snapshots passed ({elite_filtered_count} eliminated)"
        )
        snapshots = filtered_snapshots

        # ── Step 4: Anomaly detection (Blacklist Mode) ───────────────────
        # Phase 15: No topic gate. AnomalyDetector applies blacklist exclusions
        # and uses topic keywords as score boosters only.
        # All markets above score threshold → Mistral decides quality.
        logger.info(f"🔍 Step 4: Anomaly detection (Blacklist Mode) on {len(snapshots)} markets...")
        anomaly_results = self.detector.batch_detect(snapshots)
        snapshot_map = {s['id']: s for s in snapshots}

        # Log blacklist exclusions for observability
        blacklisted = [
            r for r in anomaly_results
            if r.get('blacklisted', False)
        ]
        if blacklisted:
            logger.info(
                f"🚫 Blacklist: {len(blacklisted)} markets excluded "
                f"({[r.get('question', '')[:35] for r in blacklisted[:3]]}...)"
            )

        # Remove blacklisted results before scoring
        anomaly_results = [r for r in anomaly_results if not r.get('blacklisted', False)]

        # Log topic distribution for observability
        critical_count  = sum(
            1 for r in anomaly_results
            if any('critical_insider' in reason
                   for reason in r.get('breakdown', {})
                                  .get('topic_sensitivity', {})
                                  .get('reasons', []))
        )
        elevated_count  = sum(
            1 for r in anomaly_results
            if any('elevated_insider' in reason
                   for reason in r.get('breakdown', {})
                                  .get('topic_sensitivity', {})
                                  .get('reasons', []))
            and not any('critical_insider' in reason
                        for reason in r.get('breakdown', {})
                                       .get('topic_sensitivity', {})
                                       .get('reasons', []))
        )
        no_topic_count  = len(anomaly_results) - critical_count - elevated_count

        logger.info(
            f"📊 Topic distribution: 🔴 {critical_count} critical | "
            f"🟡 {elevated_count} elevated | ⚪ {no_topic_count} no-topic"
        )

        # ── Step 5: Filter for Mistral ───────────────────────────────────
        # Phase 15: No topic gate. All markets with score >= MISTRAL_THRESHOLD
        # proceed. Sort by score descending so highest-quality fill quota first.
        flagged = sorted(
            [r for r in anomaly_results if r.get('score', 0) >= config.MISTRAL_THRESHOLD],
            key=lambda r: r.get('score', 0),
            reverse=True
        )
        max_markets = config.MAX_MISTRAL_CALLS_PER_CYCLE * config.MISTRAL_BATCH_SIZE
        flagged = flagged[:max_markets]

        logger.info(
            f"🚨 {len(flagged)} markets flagged for Mistral "
            f"(score ≥ {config.MISTRAL_THRESHOLD}, top-{max_markets} by score)"
        )

        # ── Step 6: Mistral validation ───────────────────────────────────
        confirmed        = []
        # Keep anomaly_results indexed by market_id for _process_signal
        anomaly_map: Dict[str, Dict[str, Any]] = {
            r['market_id']: r for r in anomaly_results if 'market_id' in r
        }

        if flagged:
            n_calls = -(-len(flagged) // config.MISTRAL_BATCH_SIZE)
            logger.info(
                f"🧠 Step 6: Mistral ({len(flagged)} markets, "
                f"~{n_calls} API calls, confirm ≥ {config.MISTRAL_CONFIRM_MIN})..."
            )

            mistral_items = [
                (snapshot_map[r['market_id']], r)
                for r in flagged
                if r.get('market_id') in snapshot_map
            ]
            mistral_results = self.analyzer.analyze_batch(mistral_items)

            for result in mistral_results:
                if (result.get('anomaly_detected')
                        and result.get('confidence_score', 0) >= config.MISTRAL_CONFIRM_MIN):
                    confirmed.append(result)

        logger.info(f"✅ {len(confirmed)} signals confirmed by Mistral")

        # ── Step 7: CLOB Trade Analysis (confirmed only) ─────────────────
        trade_results = {}
        if confirmed and config.TRADE_ANALYSIS_ENABLED:
            confirmed_snapshots = [
                snapshot_map[r['market_id']]
                for r in confirmed
                if r.get('market_id') in snapshot_map
            ]
            confirmed_snapshots = confirmed_snapshots[:config.MAX_TRADE_ANALYSIS_PER_CYCLE]

            logger.info(
                f"🐋 Step 7: CLOB trade analysis on "
                f"{len(confirmed_snapshots)} confirmed signals..."
            )
            trade_results = self.trader.analyze_batch(confirmed_snapshots)

        # ── Step 8: Whale boost + Store + Notify ─────────────────────────
        signals     = []
        new_signals = 0
        whale_signals = 0

        for result in confirmed:
            market_id    = result.get('market_id', '')
            snapshot     = snapshot_map.get(market_id, {})
            trade_met    = trade_results.get(market_id, {})
            anomaly_res  = anomaly_map.get(market_id, {})

            result = self._apply_whale_boost(result, trade_met)

            # Phase 15: anomaly_result passed to _process_signal for score fix
            is_new = self._process_signal(
                result, snapshot, anomaly_res, trade_met, self.cycle_count
            )
            if is_new:
                signals.append(result)
                new_signals += 1
                if trade_met.get('suspicious'):
                    whale_signals += 1

        cycle_time = time.time() - cycle_start

        # ── Step 9: Performance check (every 10 cycles) ──────────────────
        perf_summary = {}
        if self.cycle_count % 10 == 0:
            logger.info("📊 Step 9: Checking signal outcomes...")
            perf_summary = self.tracker.check_outcomes()

            if perf_summary.get('wins', 0) + perf_summary.get('losses', 0) > 0:
                db_stats = self.store.get_stats()
                self.notifier.send_daily_report(db_stats)

        # ── Stats ─────────────────────────────────────────────────────────
        db_stats = self.store.get_stats()
        logger.info(
            f"📦 DB: {db_stats['total_signals']} total | "
            f"{db_stats['signals_24h']} (24h) | "
            f"{db_stats['telegram_unsent']} unsent | "
            f"🐋 {db_stats.get('whale_signals', 0)} whale"
        )

        if db_stats.get('win_rate') is not None:
            logger.info(
                f"📊 Performance: {db_stats['wins']}W / {db_stats['losses']}L | "
                f"WR: {db_stats['win_rate']:.0%}"
            )

        summary = {
            'cycle':              self.cycle_count,
            'timestamp':          datetime.now(timezone.utc).isoformat(),
            'markets_fetched':    len(markets),
            'snapshots_built':    snapshots_raw_count,
            'elite_pre_filtered': elite_filtered_count,
            'snapshots_analyzed': len(snapshots),
            'blacklisted':        len(blacklisted) if 'blacklisted' in dir() else 0,
            'anomalies_detected': len(flagged),
            'mistral_confirmed':  len(confirmed),
            'signals':            signals,
            'signal_count':       new_signals,
            'whale_signals':      whale_signals,
            'mistral_calls':      self.analyzer.call_count,
            'clob_calls':         self.trader.call_count,
            'cycle_time_sec':     round(cycle_time, 2),
            'db_stats':           db_stats,
            'perf_summary':       perf_summary,
        }

        logger.info(
            f"✅ Cycle #{self.cycle_count} complete | "
            f"{len(markets)} markets | "
            f"{snapshots_raw_count} snapshots ({elite_filtered_count} pre-filtered) | "
            f"{len(blacklisted) if 'blacklisted' in dir() else 0} blacklisted | "
            f"{len(flagged)} anomalies | "
            f"{len(confirmed)} confirmed | "
            f"{new_signals} new signals | "
            f"{whale_signals} whale alerts | "
            f"{cycle_time:.1f}s"
        )

        return summary

    def run(self, max_cycles: int = None):
        """Main polling loop."""
        logger.info(
            f"🚀 PolyAugur Phase 15 | Poll: {config.POLL_INTERVAL_SEC}s | "
            f"DB: {config.SIGNAL_DB_PATH} | "
            f"CLOB: {'✅' if config.TRADE_ANALYSIS_ENABLED else '❌'} | "
            f"Confirm: ≥{config.MISTRAL_CONFIRM_MIN:.0%} | "
            f"Mode: Blacklist"
        )

        cycle = 0
        while True:
            try:
                summary = self.run_cycle()
                cycle  += 1

                if max_cycles and cycle >= max_cycles:
                    logger.info(f"Max cycles ({max_cycles}) reached")
                    break

                logger.info(f"💤 Sleeping {config.POLL_INTERVAL_SEC}s...")
                time.sleep(config.POLL_INTERVAL_SEC)

            except KeyboardInterrupt:
                logger.info("⛔ Stopped by user")
                final_stats = self.store.get_stats()
                logger.info(f"📦 Final DB: {final_stats}")
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}", exc_info=True)
                time.sleep(5)


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Orchestrator Test - Phase 15")
    logger.info("=" * 60)

    orch = Orchestrator()

    print("\n[Test 1] Single cycle (Phase 15 — Blacklist Mode)...")
    summary = orch.run_cycle()

    print(f"\n✅ Cycle Summary:")
    print(f"   Markets fetched:     {summary['markets_fetched']}")
    print(f"   Snapshots built:     {summary['snapshots_built']}")
    print(f"   Elite pre-filtered:  {summary['elite_pre_filtered']} eliminated")
    print(f"   Blacklisted:         {summary.get('blacklisted', 0)} excluded")
    print(f"   Snapshots analyzed:  {summary['snapshots_analyzed']}")
    print(f"   Anomalies flagged:   {summary['anomalies_detected']}")
    print(f"   Mistral confirmed:   {summary['mistral_confirmed']}")
    print(f"   New signals:         {summary['signal_count']}")
    print(f"   🐋 Whale signals:    {summary['whale_signals']}")
    print(f"   Mistral calls:       {summary['mistral_calls']}")
    print(f"   CLOB calls:          {summary['clob_calls']}")
    print(f"   Cycle time:          {summary['cycle_time_sec']}s")
    print(f"   DB stats:            {summary['db_stats']}")

    if summary['signals']:
        print(f"\n🚨 New signals:")
        for s in summary['signals']:
            boost     = s.get('confidence_boost', 0)
            boost_str = f" (↑{boost:.0%})" if boost > 0 else ""
            a_score   = s.get('anomaly_score', s.get('score', 0.0))
            print(f"   • {s.get('question', '')[:60]}")
            print(
                f"     Trade: {s.get('recommended_trade')} | "
                f"Conf: {s.get('confidence_score', 0):.2f}{boost_str} | "
                f"AnomalyScore: {a_score:.3f} | "
                f"Risk: {s.get('risk_level')}"
            )
    else:
        print("\n   No new signals this cycle")

    print("\n" + "=" * 60)
    print("✅ Phase 15 Orchestrator: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()