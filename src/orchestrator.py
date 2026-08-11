"""
PolyAugur Orchestrator - Main Polling Loop
Phase 15: Blacklist mode, anomaly_score fix, Topic-Gate entfernt.

Pipeline per cycle:
1.   Fetch all active markets (paginated, sports filtered)
2.   Build snapshots with real baseline
3.   Price observation enrichment using actual elapsed time
3.5  Elite pre-filter (spike, horizon, recency gates)
4.   AnomalyDetector.batch_detect() → Blacklist exclusion + topic score boosters
5.   Filter: score >= MISTRAL_THRESHOLD, top-MAX markets
6.   MistralAnalyzer.analyze_batch() → confirm >= MISTRAL_CONFIRM_MIN
7.   Trade analysis (CLOB) → confirmed signals only
8.   Whale confidence boost → Deduplicate → Store → Telegram
9.   Performance check (every 10 cycles)

Phase 15 changes vs Phase 14:
- Topic-Gate (Step 4.5) REMOVED — blacklist mode, Mistral is the quality gate
- _process_signal(): anomaly_score now sourced from anomaly_result
- anomaly_result passed through to _process_signal for correct score persistence
- Blacklist logging added to Step 4 summary
- Phase string updated throughout

Sprint-0 time-semantics fix:
- Cross-cycle changes use the actual elapsed time between observations.
- No polling cycle is labelled as a fixed 30-minute window.
- Hourly price change is a linear normalization of the observed move,
  not a forecast and not an observed one-hour move.

Author: Diego Ringleb | Phase 15 | 2026-03-17
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
from src.anomaly_detector import AnomalyDetector
from src.data_fetcher import PolymarketFetcher
from src.mistral_analyzer import MistralAnalyzer
from src.performance_tracker import PerformanceTracker
from src.signal_store import SignalStore
from src.telegram_notifier import TelegramNotifier
from src.trade_analyzer import TradeAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main polling loop. Phase 15: Blacklist mode.

    Key changes from Phase 14:
    - REQUIRE_CRITICAL_TOPIC gate removed. All markets with sufficient anomaly
      score pass to Mistral. Topic keywords boost score but do not gate.
    - anomaly_score correctly sourced from AnomalyDetector result.
    - Blacklist exclusions logged at Step 4.
    """

    def __init__(self):
        self.fetcher = PolymarketFetcher()
        self.detector = AnomalyDetector()
        self.analyzer = MistralAnalyzer()
        self.trader = TradeAnalyzer()
        self.store = SignalStore(config.SIGNAL_DB_PATH)
        self.notifier = TelegramNotifier()
        self.tracker = PerformanceTracker(self.store)

        self.snapshot_history: Dict[str, Dict[str, Any]] = {}
        self.cycle_count = 0
        logger.info("🚀 Orchestrator initialized (Phase 15 – Blacklist Mode)")

    # ==================== ENRICHMENT ====================

    def enrich_with_price_velocity(
        self,
        snapshots: List[Dict[str, Any]],
        *,
        observed_at: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Enrich snapshots using the actual time since the previous observation.

        The previous implementation labelled the immediately preceding polling
        cycle as a 30-minute interval even though the service normally polls
        substantially more frequently.

        `price_change_per_hour_linearized` is the observed price change scaled
        linearly to a one-hour interval. It is not a forecast and does not mean
        that a full hour of market data was observed.

        `observed_at` exists primarily to make the calculation deterministic
        in tests. Production callers normally omit it.
        """
        now = observed_at or datetime.now(timezone.utc)

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")

        now = now.astimezone(timezone.utc)

        for snapshot in snapshots:
            # No previous observation means "not measured", not zero movement.
            snapshot["price_change_since_last_observation"] = None
            snapshot["volume_24h_change_since_last_observation"] = None
            snapshot["seconds_since_last_observation"] = None
            snapshot["price_change_per_hour_linearized"] = None

            market_id = snapshot.get("id")
            if not market_id:
                continue

            current_price = float(snapshot["yes_price"])
            current_volume = float(snapshot["volume_24hr"])

            previous = self.snapshot_history.get(market_id)

            if previous:
                previous_timestamp = None
                raw_timestamp = previous.get("timestamp")

                if raw_timestamp:
                    try:
                        previous_timestamp = datetime.fromisoformat(
                            str(raw_timestamp).replace("Z", "+00:00")
                        )
                    except (TypeError, ValueError):
                        logger.debug(
                            "Ignoring invalid previous timestamp for market %s",
                            market_id,
                        )

                if (
                    previous_timestamp is not None
                    and previous_timestamp.tzinfo is not None
                    and previous_timestamp.utcoffset() is not None
                ):
                    previous_timestamp = previous_timestamp.astimezone(
                        timezone.utc
                    )

                    elapsed_seconds = (
                        now - previous_timestamp
                    ).total_seconds()

                    # A wall-clock correction can theoretically produce a
                    # zero or negative interval. Do not fabricate a metric.
                    if elapsed_seconds > 0:
                        price_change = (
                            current_price
                            - float(previous["yes_price"])
                        )

                        volume_change = (
                            current_volume
                            - float(
                                previous.get(
                                    "volume_24hr",
                                    0.0,
                                )
                            )
                        )

                        snapshot[
                            "price_change_since_last_observation"
                        ] = round(
                            price_change,
                            6,
                        )

                        snapshot[
                            "volume_24h_change_since_last_observation"
                        ] = round(
                            volume_change,
                            2,
                        )

                        snapshot[
                            "seconds_since_last_observation"
                        ] = round(
                            elapsed_seconds,
                            3,
                        )

                        snapshot[
                            "price_change_per_hour_linearized"
                        ] = round(
                            price_change
                            * 3600.0
                            / elapsed_seconds,
                            6,
                        )

            self.snapshot_history[market_id] = {
                "yes_price": current_price,
                "volume_24hr": current_volume,
                "timestamp": now.isoformat(),
            }

        return snapshots

    # ==================== CONFIDENCE BOOST ====================

    def _apply_whale_boost(
        self,
        result: Dict[str, Any],
        trade_metrics: Dict[str, Any],
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
        boost = 0.0
        raw_conf = result.get("confidence_score", 0.0)

        if trade_metrics.get("suspicious"):
            boost += 0.05

        trade_dir = result.get("recommended_trade", "HOLD")
        dom_side = trade_metrics.get("dominant_side", "NONE")

        if (
            trade_dir == "BUY_YES"
            and dom_side == "BUY"
        ) or (
            trade_dir == "BUY_NO"
            and dom_side == "SELL"
        ):
            boost += 0.05

        if trade_metrics.get("burst_score", 1.0) >= 3.0:
            boost += 0.03

        if trade_metrics.get("top_wallet_pct", 0) >= 0.40:
            boost += 0.02

        boost = min(boost, 0.15)
        boosted_conf = min(raw_conf + boost, 0.99)

        result["confidence_raw"] = raw_conf
        result["confidence_boost"] = round(boost, 3)
        result["confidence_score"] = round(boosted_conf, 3)

        if boost > 0:
            logger.info(
                "🐋 Whale boost: %.2f → %.2f (+%.2f) for %s",
                raw_conf,
                boosted_conf,
                boost,
                result.get("question", "")[:40],
            )

        return result

    # ==================== SIGNAL HANDLING ====================

    def _process_signal(
        self,
        result: Dict[str, Any],
        snapshot: Dict[str, Any],
        anomaly_result: Dict[str, Any],
        trade_metrics: Dict[str, Any],
        cycle: int,
    ) -> bool:
        """
        Persist + notify a single confirmed signal.

        anomaly_score is sourced from AnomalyDetector rather than the Mistral
        result because the Mistral result does not contain the detector score.

        Returns True if the signal was new.
        """
        market_id = result.get(
            "market_id",
            snapshot.get("id", ""),
        )

        if self.store.is_duplicate(market_id):
            logger.debug(
                "⏭️ Duplicate skipped: %s",
                result.get("question", "")[:45],
            )
            return False

        anomaly_score = anomaly_result.get(
            "score",
            result.get("score", 0.0),
        )

        enriched = {
            **result,
            "market_id": market_id,
            "yes_price": snapshot.get("yes_price", 0.5),
            "volume_24hr": snapshot.get("volume_24hr", 0),
            "spike_ratio": snapshot.get("spike_ratio", 1.0),
            "end_date_iso": snapshot.get("end_date_iso"),
            "cycle": cycle,
            "detected_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "score": anomaly_score,
            "anomaly_score": anomaly_score,
            "whale_count": trade_metrics.get(
                "whale_count",
                0,
            ),
            "whale_volume_pct": trade_metrics.get(
                "whale_volume_pct",
                0,
            ),
            "top_wallet_pct": trade_metrics.get(
                "top_wallet_pct",
                0,
            ),
            "unique_wallets": trade_metrics.get(
                "unique_wallets",
                0,
            ),
            "directional_bias": trade_metrics.get(
                "directional_bias",
                0.5,
            ),
            "dominant_side": trade_metrics.get(
                "dominant_side",
                "NONE",
            ),
            "burst_score": trade_metrics.get(
                "burst_score",
                1.0,
            ),
            "trade_suspicious": trade_metrics.get(
                "suspicious",
                False,
            ),
            "suspicious_reasons": trade_metrics.get(
                "suspicious_reasons",
                [],
            ),
        }

        row_id = self.store.save(enriched)

        sent = self.notifier.send_signal(enriched)

        if sent:
            self.store.mark_telegram_sent(row_id)

        whale_tag = (
            " 🐋"
            if trade_metrics.get("suspicious")
            else ""
        )

        boost_tag = ""

        if result.get("confidence_boost", 0) > 0:
            boost_tag = (
                f" (↑{result['confidence_boost']:.0%})"
            )

        logger.info(
            "📣 SIGNAL #%s: %s | Trade=%s | "
            "Conf=%.2f%s | AnomalyScore=%.3f | "
            "Telegram=%s%s",
            row_id,
            result.get("question", "")[:45],
            result.get("recommended_trade"),
            result.get("confidence_score", 0),
            boost_tag,
            anomaly_score,
            "✅" if sent else "⏭️",
            whale_tag,
        )

        return True

    # ==================== MAIN CYCLE ====================

    def run_cycle(self) -> Dict[str, Any]:
        """Execute one full detection cycle."""
        cycle_start = time.time()
        self.cycle_count += 1

        logger.info("=" * 50)
        logger.info(
            "🔄 Cycle #%s started",
            self.cycle_count,
        )

        self.analyzer.reset_cycle_counters()
        self.trader.reset_cycle_counters()

        # ── Step 1: Fetch ───────────────────────────────────────────
        logger.info("📡 Step 1: Fetching markets...")

        markets = self.fetcher.get_active_markets(
            limit=None,
            max_pages=config.MAX_PAGES,
        )

        if not markets:
            logger.warning(
                "No markets fetched – skipping cycle"
            )

            return {
                "cycle": self.cycle_count,
                "markets_fetched": 0,
                "snapshots_built": 0,
                "elite_pre_filtered": 0,
                "anomalies_detected": 0,
                "signals": [],
                "signal_count": 0,
                "whale_signals": 0,
            }

        logger.info(
            "✅ %s markets fetched",
            len(markets),
        )

        # ── Step 2: Snapshots ───────────────────────────────────────
        logger.info(
            "📸 Step 2: Building snapshots..."
        )

        snapshots = self.fetcher.get_snapshots_batch(
            markets
        )

        snapshots_raw_count = len(snapshots)

        logger.info(
            "✅ %s snapshots built",
            snapshots_raw_count,
        )

        # ── Step 3: Observation metrics ─────────────────────────────
        logger.info(
            "📈 Step 3: Price observation enrichment..."
        )

        snapshots = self.enrich_with_price_velocity(
            snapshots
        )

        # ── Step 3.5: Elite Pre-Filter ──────────────────────────────
        logger.info(
            "🎯 Step 3.5: Elite pre-filter "
            "(spike≥%sx, ≤%sd, recency≥%.0f%%)...",
            config.MIN_SPIKE_RATIO,
            config.MAX_DAYS_TO_CLOSE,
            config.MIN_RECENCY_RATIO * 100,
        )

        now_utc = datetime.now(
            timezone.utc
        )

        filtered_snapshots = []

        for snapshot in snapshots:
            if (
                snapshot.get(
                    "spike_ratio",
                    1.0,
                )
                < config.MIN_SPIKE_RATIO
            ):
                continue

            end_date = snapshot.get(
                "end_date_iso"
            )

            if end_date:
                try:
                    closes = datetime.fromisoformat(
                        end_date.replace(
                            "Z",
                            "+00:00",
                        )
                    )

                    days_left = (
                        closes
                        - now_utc
                    ).days

                    if (
                        days_left
                        > config.MAX_DAYS_TO_CLOSE
                    ):
                        continue

                except (
                    ValueError,
                    TypeError,
                ):
                    pass

            vol_total = snapshot.get(
                "volume",
                0,
            )

            vol_24h = snapshot.get(
                "volume_24hr",
                0,
            )

            if vol_total > 0:
                recency = (
                    vol_24h
                    / vol_total
                )

                if (
                    recency
                    < config.MIN_RECENCY_RATIO
                ):
                    continue

            filtered_snapshots.append(
                snapshot
            )

        elite_filtered_count = (
            len(snapshots)
            - len(filtered_snapshots)
        )

        logger.info(
            "🎯 Elite pre-filter: %s/%s snapshots passed "
            "(%s eliminated)",
            len(filtered_snapshots),
            snapshots_raw_count,
            elite_filtered_count,
        )

        snapshots = filtered_snapshots

        # ── Step 4: Anomaly detection ───────────────────────────────
        logger.info(
            "🔍 Step 4: Anomaly detection "
            "(Blacklist Mode) on %s markets...",
            len(snapshots),
        )

        anomaly_results = (
            self.detector.batch_detect(
                snapshots
            )
        )

        snapshot_map = {
            snapshot["id"]: snapshot
            for snapshot in snapshots
        }

        blacklisted = [
            result
            for result in anomaly_results
            if result.get(
                "blacklisted",
                False,
            )
        ]

        if blacklisted:
            logger.info(
                "🚫 Blacklist: %s markets excluded (%s...)",
                len(blacklisted),
                [
                    result.get(
                        "question",
                        "",
                    )[:35]
                    for result
                    in blacklisted[:3]
                ],
            )

        anomaly_results = [
            result
            for result in anomaly_results
            if not result.get(
                "blacklisted",
                False,
            )
        ]

        critical_count = sum(
            1
            for result in anomaly_results
            if any(
                "critical_insider"
                in reason
                for reason in (
                    result.get(
                        "breakdown",
                        {},
                    )
                    .get(
                        "topic_sensitivity",
                        {},
                    )
                    .get(
                        "reasons",
                        [],
                    )
                )
            )
        )

        elevated_count = sum(
            1
            for result in anomaly_results
            if any(
                "elevated_insider"
                in reason
                for reason in (
                    result.get(
                        "breakdown",
                        {},
                    )
                    .get(
                        "topic_sensitivity",
                        {},
                    )
                    .get(
                        "reasons",
                        [],
                    )
                )
            )
            and not any(
                "critical_insider"
                in reason
                for reason in (
                    result.get(
                        "breakdown",
                        {},
                    )
                    .get(
                        "topic_sensitivity",
                        {},
                    )
                    .get(
                        "reasons",
                        [],
                    )
                )
            )
        )

        no_topic_count = (
            len(anomaly_results)
            - critical_count
            - elevated_count
        )

        logger.info(
            "📊 Topic distribution: 🔴 %s critical | "
            "🟡 %s elevated | ⚪ %s no-topic",
            critical_count,
            elevated_count,
            no_topic_count,
        )

        # ── Step 5: Filter for Mistral ──────────────────────────────
        flagged = sorted(
            [
                result
                for result in anomaly_results
                if result.get(
                    "score",
                    0,
                )
                >= config.MISTRAL_THRESHOLD
            ],
            key=lambda result: result.get(
                "score",
                0,
            ),
            reverse=True,
        )

        max_markets = (
            config.MAX_MISTRAL_CALLS_PER_CYCLE
            * config.MISTRAL_BATCH_SIZE
        )

        flagged = flagged[
            :max_markets
        ]

        logger.info(
            "🚨 %s markets flagged for Mistral "
            "(score ≥ %s, top-%s by score)",
            len(flagged),
            config.MISTRAL_THRESHOLD,
            max_markets,
        )

        # ── Step 6: Mistral validation ──────────────────────────────
        confirmed = []

        anomaly_map: Dict[
            str,
            Dict[str, Any],
        ] = {
            result["market_id"]: result
            for result in anomaly_results
            if "market_id" in result
        }

        if flagged:
            n_calls = -(
                -len(flagged)
                // config.MISTRAL_BATCH_SIZE
            )

            logger.info(
                "🧠 Step 6: Mistral (%s markets, "
                "~%s API calls, confirm ≥ %s)...",
                len(flagged),
                n_calls,
                config.MISTRAL_CONFIRM_MIN,
            )

            mistral_items = [
                (
                    snapshot_map[
                        result["market_id"]
                    ],
                    result,
                )
                for result in flagged
                if result.get(
                    "market_id"
                )
                in snapshot_map
            ]

            mistral_results = (
                self.analyzer.analyze_batch(
                    mistral_items
                )
            )

            for result in mistral_results:
                if (
                    result.get(
                        "anomaly_detected"
                    )
                    and result.get(
                        "confidence_score",
                        0,
                    )
                    >= config.MISTRAL_CONFIRM_MIN
                ):
                    confirmed.append(
                        result
                    )

        logger.info(
            "✅ %s signals confirmed by Mistral",
            len(confirmed),
        )

        # ── Step 7: CLOB Trade Analysis ─────────────────────────────
        trade_results = {}

        if (
            confirmed
            and config.TRADE_ANALYSIS_ENABLED
        ):
            confirmed_snapshots = [
                snapshot_map[
                    result[
                        "market_id"
                    ]
                ]
                for result in confirmed
                if result.get(
                    "market_id"
                )
                in snapshot_map
            ]

            confirmed_snapshots = (
                confirmed_snapshots[
                    : config.MAX_TRADE_ANALYSIS_PER_CYCLE
                ]
            )

            logger.info(
                "🐋 Step 7: CLOB trade analysis on "
                "%s confirmed signals...",
                len(
                    confirmed_snapshots
                ),
            )

            trade_results = (
                self.trader.analyze_batch(
                    confirmed_snapshots
                )
            )

        # ── Step 8: Whale boost + Store + Notify ────────────────────
        signals = []
        new_signals = 0
        whale_signals = 0

        for result in confirmed:
            market_id = result.get(
                "market_id",
                "",
            )

            snapshot = snapshot_map.get(
                market_id,
                {},
            )

            trade_metrics = (
                trade_results.get(
                    market_id,
                    {},
                )
            )

            anomaly_result = (
                anomaly_map.get(
                    market_id,
                    {},
                )
            )

            result = (
                self._apply_whale_boost(
                    result,
                    trade_metrics,
                )
            )

            is_new = self._process_signal(
                result,
                snapshot,
                anomaly_result,
                trade_metrics,
                self.cycle_count,
            )

            if is_new:
                signals.append(result)
                new_signals += 1

                if trade_metrics.get(
                    "suspicious"
                ):
                    whale_signals += 1

        cycle_time = (
            time.time()
            - cycle_start
        )

        # ── Step 9: Performance check ───────────────────────────────
        perf_summary = {}

        if (
            self.cycle_count
            % 10
            == 0
        ):
            logger.info(
                "📊 Step 9: Checking signal outcomes..."
            )

            perf_summary = (
                self.tracker.check_outcomes()
            )

            if (
                perf_summary.get(
                    "wins",
                    0,
                )
                + perf_summary.get(
                    "losses",
                    0,
                )
                > 0
            ):
                db_stats = (
                    self.store.get_stats()
                )

                self.notifier.send_daily_report(
                    db_stats
                )

        db_stats = (
            self.store.get_stats()
        )

        logger.info(
            "📦 DB: %s total | %s (24h) | %s unsent | 🐋 %s whale",
            db_stats["total_signals"],
            db_stats["signals_24h"],
            db_stats[
                "telegram_unsent"
            ],
            db_stats.get(
                "whale_signals",
                0,
            ),
        )

        if (
            db_stats.get(
                "win_rate"
            )
            is not None
        ):
            logger.info(
                "📊 Performance: %sW / %sL | WR: %.0f%%",
                db_stats["wins"],
                db_stats["losses"],
                db_stats["win_rate"]
                * 100,
            )

        summary = {
            "cycle": self.cycle_count,
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "markets_fetched": len(
                markets
            ),
            "snapshots_built": (
                snapshots_raw_count
            ),
            "elite_pre_filtered": (
                elite_filtered_count
            ),
            "snapshots_analyzed": len(
                snapshots
            ),
            "blacklisted": len(
                blacklisted
            ),
            "anomalies_detected": len(
                flagged
            ),
            "mistral_confirmed": len(
                confirmed
            ),
            "signals": signals,
            "signal_count": (
                new_signals
            ),
            "whale_signals": (
                whale_signals
            ),
            "mistral_calls": (
                self.analyzer.call_count
            ),
            "clob_calls": (
                self.trader.call_count
            ),
            "cycle_time_sec": round(
                cycle_time,
                2,
            ),
            "db_stats": db_stats,
            "perf_summary": (
                perf_summary
            ),
        }

        logger.info(
            "✅ Cycle #%s complete | %s markets | "
            "%s snapshots (%s pre-filtered) | "
            "%s blacklisted | %s anomalies | "
            "%s confirmed | %s new signals | "
            "%s whale alerts | %.1fs",
            self.cycle_count,
            len(markets),
            snapshots_raw_count,
            elite_filtered_count,
            len(blacklisted),
            len(flagged),
            len(confirmed),
            new_signals,
            whale_signals,
            cycle_time,
        )

        return summary

    def run(
        self,
        max_cycles: Optional[int] = None,
    ):
        """Main polling loop."""
        logger.info(
            "🚀 PolyAugur Phase 15 | Poll: %ss | "
            "DB: %s | CLOB: %s | Confirm: ≥%.0f%% | "
            "Mode: Blacklist",
            config.POLL_INTERVAL_SEC,
            config.SIGNAL_DB_PATH,
            (
                "✅"
                if config.TRADE_ANALYSIS_ENABLED
                else "❌"
            ),
            config.MISTRAL_CONFIRM_MIN
            * 100,
        )

        cycle = 0

        while True:
            try:
                self.run_cycle()
                cycle += 1

                if (
                    max_cycles
                    and cycle
                    >= max_cycles
                ):
                    logger.info(
                        "Max cycles (%s) reached",
                        max_cycles,
                    )
                    break

                logger.info(
                    "💤 Sleeping %ss...",
                    config.POLL_INTERVAL_SEC,
                )

                time.sleep(
                    config.POLL_INTERVAL_SEC
                )

            except KeyboardInterrupt:
                logger.info(
                    "⛔ Stopped by user"
                )

                final_stats = (
                    self.store.get_stats()
                )

                logger.info(
                    "📦 Final DB: %s",
                    final_stats,
                )
                break

            except Exception as exc:
                logger.error(
                    "Cycle error: %s",
                    exc,
                    exc_info=True,
                )

                time.sleep(5)


def main():
    logger.info("=" * 60)
    logger.info(
        "🧪 PolyAugur Orchestrator Test - Phase 15"
    )
    logger.info("=" * 60)

    orchestrator = Orchestrator()

    print(
        "\n[Test 1] Single cycle "
        "(Phase 15 — Blacklist Mode)..."
    )

    summary = (
        orchestrator.run_cycle()
    )

    print("\n✅ Cycle Summary:")
    print(
        "   Markets fetched:     "
        f"{summary['markets_fetched']}"
    )
    print(
        "   Snapshots built:     "
        f"{summary['snapshots_built']}"
    )
    print(
        "   Elite pre-filtered:  "
        f"{summary['elite_pre_filtered']} eliminated"
    )
    print(
        "   Blacklisted:         "
        f"{summary.get('blacklisted', 0)} excluded"
    )
    print(
        "   Snapshots analyzed:  "
        f"{summary['snapshots_analyzed']}"
    )
    print(
        "   Anomalies flagged:   "
        f"{summary['anomalies_detected']}"
    )
    print(
        "   Mistral confirmed:   "
        f"{summary['mistral_confirmed']}"
    )
    print(
        "   New signals:         "
        f"{summary['signal_count']}"
    )
    print(
        "   🐋 Whale signals:    "
        f"{summary['whale_signals']}"
    )
    print(
        "   Mistral calls:       "
        f"{summary['mistral_calls']}"
    )
    print(
        "   CLOB calls:          "
        f"{summary['clob_calls']}"
    )
    print(
        "   Cycle time:          "
        f"{summary['cycle_time_sec']}s"
    )
    print(
        "   DB stats:            "
        f"{summary['db_stats']}"
    )

    if summary["signals"]:
        print("\n🚨 New signals:")

        for signal in summary[
            "signals"
        ]:
            boost = signal.get(
                "confidence_boost",
                0,
            )

            boost_str = (
                f" (↑{boost:.0%})"
                if boost > 0
                else ""
            )

            anomaly_score = (
                signal.get(
                    "anomaly_score",
                    signal.get(
                        "score",
                        0.0,
                    ),
                )
            )

            print(
                "   • "
                f"{signal.get('question', '')[:60]}"
            )

            print(
                "     Trade: "
                f"{signal.get('recommended_trade')} | "
                "Conf: "
                f"{signal.get('confidence_score', 0):.2f}"
                f"{boost_str} | "
                "AnomalyScore: "
                f"{anomaly_score:.3f} | "
                "Risk: "
                f"{signal.get('risk_level')}"
            )

    else:
        print(
            "\n   No new signals this cycle"
        )

    print("\n" + "=" * 60)
    print(
        "✅ Phase 15 Orchestrator: PASSED"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()