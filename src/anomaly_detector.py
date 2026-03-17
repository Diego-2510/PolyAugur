"""
PolyAugur Anomaly Detector - Multi-Layer Insider Signal Detection
MODIFIED: Elite Quality Filter — all 5 conditions must converge.

Design principle: Only flag markets where someone with privileged access
(government official, regulator, corporate insider, military commander)
KNOWS the outcome before public announcement.

NOT insider-tradeable: crypto price predictions, generic elections,
weather bets, entertainment outcomes, general political sentiment.

Author: Diego Ringleb | Phase 12.2 | 2026-03-01
"""

import logging
import numpy as np
from typing import Dict, List, Any
from datetime import datetime, timezone
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Multi-layer anomaly detection for Polymarket insider signals.

    ELITE QUALITY FILTER (modified):
    All 5 hard gates must pass before a market is even scored:
      1. Volume spike >= MIN_SPIKE_RATIO (5x)
      2. Vol/Liq ratio >= MIN_VOL_LIQ_RATIO (3.0)
      3. Topic = CRITICAL (if REQUIRE_CRITICAL_TOPIC = True)
      4. Days to close <= MAX_DAYS_TO_CLOSE (14)
      5. Recency ratio >= MIN_RECENCY_RATIO (60%)

    Target: 1–5 ultra-high-quality signals per cycle.
    """

    def __init__(self):
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        logger.info(f"🔍 AnomalyDetector initialized (threshold: {self.confidence_threshold})")

    # ==================== ELITE HARD GATES ====================

    def _passes_elite_gates(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Pre-filter: ALL gates must pass for a market to be scored.
        Returns dict with 'passed' bool and 'failed_gates' list.
        """
        failed = []

        current_vol = float(snapshot.get('current_volume', snapshot.get('volume_24hr', 0)))
        baseline    = float(snapshot.get('baseline', 0))
        liquidity   = float(snapshot.get('liquidity', 1))
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        volume_total= float(snapshot.get('volume', volume_24hr))
        question    = snapshot.get('question', '').lower()
        end_date    = snapshot.get('end_date_iso', '')

        # Gate 1: Volume spike >= MIN_SPIKE_RATIO
        spike_ratio = current_vol / baseline if baseline > 0 else 0
        if spike_ratio < config.MIN_SPIKE_RATIO:
            failed.append(f"spike_ratio_{spike_ratio:.1f}x_<_{config.MIN_SPIKE_RATIO}x")

        # Gate 2: Vol/Liq ratio >= MIN_VOL_LIQ_RATIO
        vol_liq = volume_24hr / liquidity if liquidity > 0 else 0
        if vol_liq < config.MIN_VOL_LIQ_RATIO:
            failed.append(f"vol_liq_{vol_liq:.1f}x_<_{config.MIN_VOL_LIQ_RATIO}x")

        # Gate 3: Topic tier check
        critical_topics = [
            'attack ', 'airstrike', 'invasion', 'troops deploy',
            'military action', 'declare war', 'nuclear',
            'missile strike', 'military operation',
            'fed ', 'federal reserve', 'rate cut', 'rate hike', 'fomc',
            'powell', 'fed chair', 'emergency rate',
            'sec approv', 'sec reject', 'etf approv', 'etf reject',
            'fda approv', 'drug approv', 'emergency use',
            'executive order', 'pardon', 'commute sentence',
            'nominate', 'nomination', 'appoint',
            'merger', 'acquisition', 'takeover', 'buyout',
            'ceo resign', 'ceo fired', 'ceo step down',
        ]
        elevated_topics = [
            'ceasefire', 'peace deal', 'peace agreement',
            'treaty', 'diplomatic', 'embassy',
            'tariff', 'trade deal', 'trade war', 'sanction',
            'indictment', 'arrest', 'impeach', 'doj ',
            'government shutdown', 'debt ceiling', 'default',
            'crypto regulation', 'crypto ban', 'stablecoin regulation',
            'cftc', 'approved by',
            'antitrust', 'monopoly', 'break up', 'ban tiktok',
            'opec', 'oil production cut', 'drilling ban', 'pipeline',
            'clinical trial', 'vaccine approv', 'pandemic',
            'special election', 'runoff', 'recall', 'referendum',
            'coup', 'north korea', 'taiwan', 'south china sea',
            'nato deploy', 'nato article',
        ]
        is_critical = any(t in question for t in critical_topics)
        is_elevated = any(t in question for t in elevated_topics)

        if config.REQUIRE_CRITICAL_TOPIC:
            if not is_critical:
                failed.append("no_critical_topic")
        else:
            if not is_critical and not is_elevated:
                failed.append("no_insider_topic")

        # Gate 4: Days to close <= MAX_DAYS_TO_CLOSE
        try:
            if end_date:
                closes_at    = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days
                if days_to_close > config.MAX_DAYS_TO_CLOSE:
                    failed.append(f"days_to_close_{days_to_close}d_>_{config.MAX_DAYS_TO_CLOSE}d")
            else:
                failed.append("no_end_date")
        except (ValueError, TypeError, AttributeError):
            failed.append("invalid_end_date")

        # Gate 5: Recency ratio >= MIN_RECENCY_RATIO
        recency = volume_24hr / volume_total if volume_total > 0 else 0
        if recency < config.MIN_RECENCY_RATIO:
            failed.append(f"recency_{recency:.0%}_<_{config.MIN_RECENCY_RATIO:.0%}")

        return {
            'passed':      len(failed) == 0,
            'failed_gates': failed,
            'spike_ratio': round(spike_ratio, 2),
            'vol_liq':     round(vol_liq, 2),
            'recency':     round(recency, 3),
            'is_critical': is_critical,
            'is_elevated': is_elevated,
        }

    # ==================== LAYER 1: VOLUME SPIKE ====================

    def detect_volume_spike(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual volume relative to THIS market's own baseline.
        Phase 12.2: 1.5x contributes minimally (0.05), real signal starts at 2.0x.
        """
        current_vol = float(snapshot.get('current_volume', snapshot.get('volume_24hr', 0)))
        baseline    = float(snapshot.get('baseline', 0))

        if baseline <= 0 or current_vol <= 0:
            return {
                'score': 0.0, 'spike_ratio': 1.0,
                'severity': 'none', 'reason': 'insufficient_baseline_data'
            }

        spike_ratio = current_vol / baseline

        if spike_ratio >= 5.0:
            score    = 0.35
            severity = 'critical'
        elif spike_ratio >= 3.0:
            score    = 0.25
            severity = 'high'
        elif spike_ratio >= 2.0:
            score    = 0.10
            severity = 'moderate'
        else:
            score    = 0.0
            severity = 'none'

        return {
            'score':          score,
            'spike_ratio':    round(spike_ratio, 3),
            'current_volume': current_vol,
            'baseline':       baseline,
            'severity':       severity
        }

    # ==================== LAYER 2: PRICE ANOMALY ====================

    def detect_price_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual price movements indicating informed trading.
        Checks conviction, volume/liquidity pressure, one-sided bets.
        """
        yes_price   = float(snapshot.get('yes_price', 0.5))
        no_price    = float(snapshot.get('no_price', 0.5))
        spread      = abs(yes_price - no_price)
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        liquidity   = float(snapshot.get('liquidity', 1))

        score      = 0.0
        indicators = []

        # Extreme conviction (>0.90 or <0.10)
        if yes_price > 0.90 or yes_price < 0.10:
            score += 0.10
            indicators.append(f"extreme_conviction_{yes_price:.2f}")

        # Volume-to-liquidity ratio
        vol_liq_ratio = volume_24hr / liquidity if liquidity > 0 else 0
        if vol_liq_ratio > 3.0:
            score += 0.12
            indicators.append(f"vol_liq_pressure_{vol_liq_ratio:.1f}x")
        elif vol_liq_ratio > 1.5:
            score += 0.06
            indicators.append(f"vol_liq_elevated_{vol_liq_ratio:.1f}x")

        # One-sided bet: high spread + high vol/liq
        if spread > 0.70 and vol_liq_ratio > 1.0:
            score += 0.08
            indicators.append(f"one_sided_bet_spread_{spread:.2f}")

        score = min(score, 0.25)

        return {
            'score':         round(score, 3),
            'yes_price':     yes_price,
            'no_price':      no_price,
            'spread':        round(spread, 3),
            'vol_liq_ratio': round(vol_liq_ratio, 3),
            'indicators':    indicators
        }

    # ==================== LAYER 3: BEHAVIORAL ====================

    def detect_holder_anomalies(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Placeholder. Real holder analysis via CLOB /trades endpoint.
        Future: new wallets <7d with large positions, coordinated buys.
        """
        holders = snapshot.get('holders', [])
        return {
            'score':        0.0,
            'reason':       'no_holder_data_phase6_feature',
            'holder_count': len(holders)
        }

    # ==================== LAYER 4: TOPIC + TIME SENSITIVITY ====================

    def calculate_topic_sensitivity(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Two-tier insider topic system (Phase 12.2).

        CRITICAL topics (×1.40): A specific person/group DEFINITELY knows
        the outcome before public.

        ELEVATED topics (×1.15): Insider info is PLAUSIBLE but less certain.

        Returns multiplier 0.20–1.80 applied to base_score.
        """
        question     = snapshot.get('question', '').lower()
        volume_24hr  = float(snapshot.get('volume_24hr', 0))
        volume_total = float(snapshot.get('volume', volume_24hr))
        end_date_iso = snapshot.get('end_date_iso', '')

        multiplier = 1.0
        reasons    = []

        # ── Factor 1: Time Horizon ─────────────────────────────────────────
        try:
            if end_date_iso:
                closes_at     = datetime.fromisoformat(end_date_iso.replace('Z', '+00:00'))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days

                if days_to_close > 365:
                    multiplier *= 0.30
                    reasons.append(f"long_term_{days_to_close}d")
                elif days_to_close > 180:
                    multiplier *= 0.55
                    reasons.append(f"medium_term_{days_to_close}d")
                elif days_to_close > 90:
                    multiplier *= 0.80
                    reasons.append(f"far_term_{days_to_close}d")
                elif days_to_close <= 14:
                    multiplier *= 1.25
                    reasons.append(f"imminent_{days_to_close}d")
                elif days_to_close <= 30:
                    multiplier *= 1.10
                    reasons.append(f"near_term_{days_to_close}d")
        except (ValueError, TypeError, AttributeError):
            pass

        # ── Factor 2a: CRITICAL insider topics (×1.40) ────────────────────
        critical_topics = [
            'attack ', 'airstrike', 'invasion', 'troops deploy',
            'military action', 'declare war', 'nuclear',
            'missile strike', 'military operation',
            'fed ', 'federal reserve', 'rate cut', 'rate hike', 'fomc',
            'powell', 'fed chair', 'emergency rate',
            'sec approv', 'sec reject', 'etf approv', 'etf reject',
            'fda approv', 'drug approv', 'emergency use',
            'executive order', 'pardon', 'commute sentence',
            'nominate', 'nomination', 'appoint',
            'merger', 'acquisition', 'takeover', 'buyout',
            'ceo resign', 'ceo fired', 'ceo step down',
        ]

        # ── Factor 2b: ELEVATED insider topics (×1.15) ────────────────────
        elevated_topics = [
            'ceasefire', 'peace deal', 'peace agreement',
            'treaty', 'diplomatic', 'embassy',
            'tariff', 'trade deal', 'trade war', 'sanction',
            'indictment', 'arrest', 'impeach', 'doj ',
            'government shutdown', 'debt ceiling', 'default',
            'crypto regulation', 'crypto ban', 'stablecoin regulation',
            'cftc', 'approved by',
            'antitrust', 'monopoly', 'break up', 'ban tiktok',
            'opec', 'oil production cut', 'drilling ban', 'pipeline',
            'clinical trial', 'vaccine approv', 'pandemic',
            'special election', 'runoff', 'recall', 'referendum',
            'coup', 'north korea', 'taiwan', 'south china sea',
            'nato deploy', 'nato article',
        ]

        critical_matched = [t for t in critical_topics if t in question]
        elevated_matched = [t for t in elevated_topics if t in question]

        if critical_matched:
            multiplier *= 1.40
            reasons.append(f"critical_insider:{critical_matched[0].strip()}")
        elif elevated_matched:
            multiplier *= 1.15
            reasons.append(f"elevated_insider:{elevated_matched[0].strip()}")

        # ── Factor 3: Sudden volume surge ─────────────────────────────────
        if volume_total > 0:
            recency_ratio = volume_24hr / volume_total
            if recency_ratio > 0.60:
                multiplier *= 1.40
                reasons.append(f"sudden_alltime_surge_{recency_ratio:.0%}")
            elif recency_ratio > 0.35:
                multiplier *= 1.15
                reasons.append(f"elevated_recency_{recency_ratio:.0%}")

        multiplier = round(max(0.20, min(1.80, multiplier)), 2)

        return {
            'multiplier':    multiplier,
            'reasons':       reasons,
            'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0
        }

    # ==================== AGGREGATION ====================

    def detect_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main detection pipeline:
        1. Run elite hard gates — if ANY fails, skip scoring entirely
        2. Score remaining markets across all layers
        3. Apply topic multiplier
        4. Threshold: score >= CONFIDENCE_THRESHOLD
        """
        try:
            # ── Elite gate pre-filter ─────────────────────────────────────
            gate_result = self._passes_elite_gates(snapshot)

            if not gate_result['passed']:
                logger.debug(
                    f"⛔ GATE FAIL: {snapshot.get('question', '')[:55]} | "
                    f"Failed: {gate_result['failed_gates']}"
                )
                return {
                    'anomaly_detected':  False,
                    'score':             0.0,
                    'gate_failed':       True,
                    'failed_gates':      gate_result['failed_gates'],
                    'ready_for_mistral': False,
                    'market_id':         snapshot.get('id'),
                    'question':          snapshot.get('question', 'Unknown'),
                    'volume_24hr':       snapshot.get('volume_24hr', 0),
                    'timestamp':         datetime.now(timezone.utc).isoformat(),
                }

            # ── All gates passed — run full scoring ───────────────────────
            volume_result = self.detect_volume_spike(snapshot)
            price_result  = self.detect_price_anomaly(snapshot)
            holder_result = self.detect_holder_anomalies(snapshot)
            topic_result  = self.calculate_topic_sensitivity(snapshot)

            base_score = (
                volume_result['score'] +
                price_result['score'] +
                holder_result['score']
            )

            final_score      = round(base_score * topic_result['multiplier'], 3)
            anomaly_detected = final_score >= self.confidence_threshold

            result = {
                'anomaly_detected':  anomaly_detected,
                'score':             final_score,
                'base_score':        round(base_score, 3),
                'topic_multiplier':  topic_result['multiplier'],
                'ready_for_mistral': anomaly_detected,
                'gate_failed':       False,
                'gate_info':         gate_result,

                'breakdown': {
                    'volume_spike':      volume_result,
                    'price_anomaly':     price_result,
                    'holder_behavior':   holder_result,
                    'topic_sensitivity': topic_result,
                },

                'market_id':   snapshot.get('id'),
                'question':    snapshot.get('question', 'Unknown'),
                'volume_24hr': snapshot.get('volume_24hr', 0),
                'timestamp':   datetime.now(timezone.utc).isoformat(),
            }

            if anomaly_detected:
                logger.info(
                    f"🚨 ANOMALY: {snapshot.get('question', '')[:55]} | "
                    f"Score: {final_score:.3f} "
                    f"(base: {base_score:.2f} × {topic_result['multiplier']:.2f}) | "
                    f"Vol: ${snapshot.get('volume_24hr', 0):,.0f} | "
                    f"Gates: ✅ spike={gate_result['spike_ratio']}x "
                    f"vol_liq={gate_result['vol_liq']}x "
                    f"recency={gate_result['recency']:.0%}"
                )
            else:
                logger.debug(
                    f"✓ Gates passed but score low: {snapshot.get('question', '')[:55]} | "
                    f"Score: {final_score:.3f}"
                )

            return result

        except Exception as e:
            logger.error(f"Detection error for {snapshot.get('id')}: {e}", exc_info=True)
            return {
                'anomaly_detected': False,
                'score':            0.0,
                'error':            str(e),
                'market_id':        snapshot.get('id')
            }

    def batch_detect(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run detection on multiple markets. Returns sorted by score (highest first)."""
        results = [self.detect_anomaly(s) for s in snapshots]
        results.sort(key=lambda x: x.get('score', 0), reverse=True)

        detected_count  = sum(1 for r in results if r.get('anomaly_detected'))
        gate_fail_count = sum(1 for r in results if r.get('gate_failed'))

        logger.info(
            f"📊 Batch: {len(results)} markets | "
            f"{gate_fail_count} gate-filtered | "
            f"{len(results) - gate_fail_count} scored | "
            f"{detected_count} anomalies detected"
        )
        return results


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Anomaly Detector Test - ELITE QUALITY FILTER")
    logger.info("=" * 60)

    from datetime import timedelta
    detector = AnomalyDetector()
    now      = datetime.now(timezone.utc)

    # Test 1: 2028 Election → GATE FAIL (long-term + no critical topic)
    print("\n[Test 1] 2028 Election (GATE FAIL expected)...")
    snap = {
        'id': 't1', 'question': 'Will Nikki Haley win the 2028 US Presidential Election?',
        'volume_24hr': 321_000, 'current_volume': 321_000, 'baseline': 50_000,
        'yes_price': 0.08, 'no_price': 0.92, 'liquidity': 200_000,
        'volume': 2_000_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=900)).isoformat()
    }
    r = detector.detect_anomaly(snap)
    print(f"   {'✅ Gate failed (correct)' if r.get('gate_failed') else '❌ Should have gate-failed'} | Failed: {r.get('failed_gates')}")

    # Test 2: Bitcoin price → GATE FAIL (no critical topic)
    print("\n[Test 2] Bitcoin price prediction (GATE FAIL expected)...")
    snap = {
        'id': 't2', 'question': 'Will Bitcoin be above $100,000 on March 31?',
        'volume_24hr': 500_000, 'current_volume': 500_000, 'baseline': 100_000,
        'yes_price': 0.55, 'no_price': 0.45, 'liquidity': 800_000,
        'volume': 5_000_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=14)).isoformat()
    }
    r = detector.detect_anomaly(snap)
    print(f"   {'✅ Gate failed (correct)' if r.get('gate_failed') else '❌ Should have gate-failed'} | Failed: {r.get('failed_gates')}")

    # Test 3: Fed emergency cut, 7 days, all gates met → SHOULD FLAG
    print("\n[Test 3] Emergency Fed rate cut, 7 days (SHOULD FLAG)...")
    snap = {
        'id': 't3', 'question': 'Will the Fed announce an emergency rate cut this week?',
        'volume_24hr': 200_000, 'current_volume': 200_000, 'baseline': 20_000,
        'yes_price': 0.25, 'no_price': 0.75, 'liquidity': 50_000,
        'volume': 210_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=7)).isoformat()
    }
    r = detector.detect_anomaly(snap)
    status = "✅" if r['anomaly_detected'] else f"⚠️  Score={r['score']:.3f} (threshold={config.CONFIDENCE_THRESHOLD})"
    print(f"   {status} | Gate={'✅' if not r.get('gate_failed') else '❌'} | Score={r['score']:.3f}")

    # Test 4: US airstrike on Iran, 5 days → SHOULD FLAG HIGH
    print("\n[Test 4] US airstrike on Iran, 5 days (SHOULD FLAG HIGH)...")
    snap = {
        'id': 't4', 'question': 'Will the US conduct an airstrike on Iran before March 7?',
        'volume_24hr': 180_000, 'current_volume': 180_000, 'baseline': 5_000,
        'yes_price': 0.35, 'no_price': 0.65, 'liquidity': 45_000,
        'volume': 190_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=5)).isoformat()
    }
    r = detector.detect_anomaly(snap)
    status = "✅" if r['anomaly_detected'] else f"⚠️  Score={r['score']:.3f}"
    print(f"   {status} | Spike={r.get('breakdown', {}).get('volume_spike', {}).get('spike_ratio', 0):.1f}x | Score={r['score']:.3f}")

    # Test 5: Ceasefire elevated topic, 15 days → GATE FAIL (>14 days + not critical)
    print("\n[Test 5] Ceasefire 15 days (GATE FAIL — >14d AND not critical)...")
    snap = {
        'id': 't5', 'question': 'Russia Ukraine ceasefire agreement by April 1?',
        'volume_24hr': 350_000, 'current_volume': 350_000, 'baseline': 50_000,
        'yes_price': 0.40, 'no_price': 0.60, 'liquidity': 100_000,
        'volume': 360_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=15)).isoformat()
    }
    r = detector.detect_anomaly(snap)
    print(f"   {'✅ Gate failed (correct)' if r.get('gate_failed') else '⚠️  Passed gates'} | Failed: {r.get('failed_gates')}")

    print("\n" + "=" * 60)
    print("✅ Elite Quality Filter Test: COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
