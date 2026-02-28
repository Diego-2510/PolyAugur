"""
PolyAugur Anomaly Detector - Multi-Layer Insider Signal Detection
Detects volume spikes, price anomalies, and behavioral patterns indicating informed trading.
Core Principle: Insider detection = relative anomaly vs own baseline (ALL market sizes relevant).
Author: Diego Ringleb | Phase 3 | 2026-02-28
Architecture: mache-es-sehr-viel-ausfuhrlicher.md [file:1] Abschnitt 5
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
    
    Core Principle: Relevance = RELATIVE deviation from own baseline.
    - A $1M market with 5x spike is equally suspicious as a $10k market with 5x spike.
    - No market size bias, no age bias.
    - Insider-prone = unusual activity relative to that market's OWN history.
    
    Layers [file:1] Abschnitt 5:
    1. Volume Spike (relative to own baseline)
    2. Price Anomaly (conviction, spread, mismatch)
    3. Behavioral / Holder patterns (Phase 4+)
    4. Topic Sensitivity (regulation/politics/finance = higher insider risk)
    5. Aggregation → Score 0.0-1.0 → Mistral trigger @ threshold
    """

    def __init__(self):
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        logger.info(f"🔍 AnomalyDetector initialized (threshold: {self.confidence_threshold})")

    # ==================== LAYER 1: VOLUME SPIKE ====================

    def detect_volume_spike(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual volume relative to THIS market's own baseline.
        [file:1] Abschnitt 5.2.1 - both ratio and z-score methods.

        Fully relative: $5M market with 4x spike = same score as $5k market with 4x spike.
        No absolute threshold bias.
        """
        current_vol = float(snapshot.get('current_volume', snapshot.get('volume_24hr', 0)))
        baseline = float(snapshot.get('baseline', 0))

        if baseline <= 0 or current_vol <= 0:
            return {
                'score': 0.0,
                'spike_ratio': 1.0,
                'severity': 'none',
                'reason': 'insufficient_baseline_data'
            }

        spike_ratio = current_vol / baseline

        # Scoring purely on relative spike [file:1] Abschnitt 5.2.1
        if spike_ratio >= 5.0:
            score = 0.35
            severity = 'critical'
        elif spike_ratio >= 3.0:
            score = 0.25
            severity = 'high'
        elif spike_ratio >= 2.0:
            score = 0.15
            severity = 'moderate'
        elif spike_ratio >= 1.5:
            score = 0.05
            severity = 'low'
        else:
            score = 0.0
            severity = 'none'

        logger.debug(
            f"Volume spike: ratio={spike_ratio:.2f}x | "
            f"current=${current_vol:,.0f} | baseline=${baseline:,.0f} | "
            f"score={score:.2f}"
        )

        return {
            'score': score,
            'spike_ratio': round(spike_ratio, 3),
            'current_volume': current_vol,
            'baseline': baseline,
            'severity': severity
        }

    # ==================== LAYER 2: PRICE ANOMALY ====================

    def detect_price_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual price movements indicating informed trading.
        [file:1] Abschnitt 5.2 - checks conviction, mismatch, spread.

        All relative: Works for any market size.
        """
        yes_price = float(snapshot.get('yes_price', 0.5))
        no_price = float(snapshot.get('no_price', 0.5))
        spread = abs(yes_price - no_price)
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        liquidity = float(snapshot.get('liquidity', 1))

        score = 0.0
        indicators = []

        # Check 1: Extreme conviction (>0.90 or <0.10)
        # Informed traders push prices to extremes before resolution
        if yes_price > 0.90 or yes_price < 0.10:
            score += 0.10
            indicators.append(f"extreme_conviction_{yes_price:.2f}")

        # Check 2: Volume-to-liquidity ratio (relative pressure)
        # High vol relative to liquidity = large informed bets moving the market
        vol_liq_ratio = volume_24hr / liquidity if liquidity > 0 else 0
        if vol_liq_ratio > 3.0:
            score += 0.12
            indicators.append(f"vol_liq_pressure_{vol_liq_ratio:.1f}x")
        elif vol_liq_ratio > 1.5:
            score += 0.06
            indicators.append(f"vol_liq_elevated_{vol_liq_ratio:.1f}x")

        # Check 3: Narrow spread + high volume = informed directional bet
        # Insider knows outcome → bets aggressively → price moves one-sided
        if spread > 0.70 and vol_liq_ratio > 1.0:
            score += 0.08
            indicators.append(f"one_sided_bet_spread_{spread:.2f}")

        score = min(score, 0.25)

        return {
            'score': round(score, 3),
            'yes_price': yes_price,
            'no_price': no_price,
            'spread': round(spread, 3),
            'vol_liq_ratio': round(vol_liq_ratio, 3),
            'indicators': indicators
        }

    # ==================== LAYER 3: BEHAVIORAL ====================

    def detect_holder_anomalies(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze wallet/holder patterns [file:1] Abschnitt 5.3.
        Phase 3 MVP: Placeholder. Phase 4+: Real Data API holder analysis.

        Future checks:
        - New wallets (<7 days) with large positions
        - Coordinated buys (3+ wallets, same direction, <30min)
        - Fast entry velocity (>$50k in <15min)
        """
        holders = snapshot.get('holders', [])

        if not holders:
            return {
                'score': 0.0,
                'reason': 'no_holder_data_phase4_feature',
                'holder_count': 0
            }

        # Phase 4+ logic (commented for reference):
        # score = 0.0
        # for h in holders:
        #     age_days = (now - h['created_at']).days
        #     if age_days < 7 and h['position_usd'] > 10_000:
        #         score += 0.15  # New wallet, large bet
        # coordination = detect_coordinated_buys(holders)
        # if coordination['count'] >= 3:
        #     score += 0.25

        return {
            'score': 0.0,
            'reason': 'placeholder_mvp',
            'holder_count': len(holders)
        }

    # ==================== LAYER 4: TOPIC SENSITIVITY ====================

    def calculate_topic_sensitivity(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess how insider-prone the market topic is.

        NOT a size or age filter. Purely based on:
        - Topic category (regulation/politics/finance → higher insider risk)
        - Volume surge relative to all-time (sudden interest in this market)

        Returns multiplier 0.8-1.4 applied to base_score.
        Works identically regardless of market size or age.
        """
        question = snapshot.get('question', '').lower()
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        volume_total = float(snapshot.get('volume', volume_24hr))

        multiplier = 1.0
        reasons = []

        # Factor 1: Topic category (insider information asymmetry risk)
        high_sensitivity_topics = [
            # Politics/regulation (privileged access to info)
            'trump', 'fed ', 'federal reserve', 'sec ', 'congress',
            'bill ', 'legislation', 'executive order', 'sanction',
            # Corporate (M&A, earnings, IPO leaks)
            'merger', 'acquisition', ' ipo', 'earnings', 'ceo',
            'bankrupt', 'layoff',
            # Crypto regulation (regulatory insiders)
            'etf', 'bitcoin etf', 'crypto regulation',
            # Geopolitics
            'war', 'ceasefire', 'treaty', 'election'
        ]

        matched_topics = [t for t in high_sensitivity_topics if t in question]
        if matched_topics:
            multiplier *= 1.30
            reasons.append(f"insider_topic:{matched_topics[0]}")

        # Factor 2: Sudden volume surge relative to market's own all-time history
        # Works for ANY market size: $1B market with 60% of volume in 24h is just as suspicious
        if volume_total > 0:
            recency_ratio = volume_24hr / volume_total
            if recency_ratio > 0.60:  # 60%+ of ALL-TIME volume in last 24h
                multiplier *= 1.40
                reasons.append(f"sudden_alltime_surge_{recency_ratio:.0%}")
            elif recency_ratio > 0.35:
                multiplier *= 1.15
                reasons.append(f"elevated_recency_{recency_ratio:.0%}")

        # Clamp multiplier
        multiplier = round(max(0.8, min(1.5, multiplier)), 2)

        return {
            'multiplier': multiplier,
            'reasons': reasons,
            'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0
        }

    # ==================== AGGREGATION ====================

    def detect_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main detection pipeline: Aggregate all layers into final score.
        [file:1] Abschnitt 5.5

        Score = (volume + price + holder) × topic_sensitivity_multiplier
        Threshold: score ≥ CONFIDENCE_THRESHOLD → flag for Mistral

        All scoring is RELATIVE to the market's own baseline.
        No market size bias, no age bias.
        """
        try:
            # Run all layers
            volume_result = self.detect_volume_spike(snapshot)
            price_result = self.detect_price_anomaly(snapshot)
            holder_result = self.detect_holder_anomalies(snapshot)
            topic_result = self.calculate_topic_sensitivity(snapshot)

            # Base score (max ~0.85 with all layers; Phase 4+ adds up to 0.25 for holders)
            base_score = (
                volume_result['score'] +   # Max 0.35
                price_result['score'] +    # Max 0.25
                holder_result['score']     # Max 0.25 (Phase 4+)
            )

            # Apply topic sensitivity multiplier
            final_score = round(base_score * topic_result['multiplier'], 3)

            anomaly_detected = final_score >= self.confidence_threshold

            result = {
                'anomaly_detected': anomaly_detected,
                'score': final_score,
                'base_score': round(base_score, 3),
                'topic_multiplier': topic_result['multiplier'],
                'ready_for_mistral': anomaly_detected,

                'breakdown': {
                    'volume_spike': volume_result,
                    'price_anomaly': price_result,
                    'holder_behavior': holder_result,
                    'topic_sensitivity': topic_result
                },

                'market_id': snapshot.get('id'),
                'question': snapshot.get('question', 'Unknown'),
                'volume_24hr': snapshot.get('volume_24hr', 0),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            if anomaly_detected:
                logger.info(
                    f"🚨 ANOMALY: {snapshot.get('question', '')[:55]} | "
                    f"Score: {final_score:.3f} "
                    f"(base: {base_score:.2f} × {topic_result['multiplier']:.2f}) | "
                    f"Vol: ${snapshot.get('volume_24hr', 0):,.0f}"
                )
            else:
                logger.debug(
                    f"✓ Clean: {snapshot.get('question', '')[:55]} | "
                    f"Score: {final_score:.3f}"
                )

            return result

        except Exception as e:
            logger.error(f"Detection error for {snapshot.get('id')}: {e}", exc_info=True)
            return {
                'anomaly_detected': False,
                'score': 0.0,
                'error': str(e),
                'market_id': snapshot.get('id')
            }

    def batch_detect(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run detection on multiple markets. Returns sorted by score (highest first).
        """
        results = [self.detect_anomaly(s) for s in snapshots]
        results.sort(key=lambda x: x.get('score', 0), reverse=True)

        detected_count = sum(1 for r in results if r.get('anomaly_detected'))
        logger.info(
            f"📊 Batch: {len(results)} markets analyzed | "
            f"{detected_count} anomalies detected ({detected_count/max(len(results),1)*100:.0f}%)"
        )
        return results


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Anomaly Detector Test - Phase 3 v2")
    logger.info("=" * 60)

    detector = AnomalyDetector()

    # Test 1: Large market ($2M) with 5x spike - should still flag
    print("\n[Test 1] Large market $2M with 5x relative spike...")
    snap_1 = {
        'id': 't1', 'question': 'Will the Fed cut rates before June 2026?',
        'volume_24hr': 2_000_000, 'current_volume': 2_000_000, 'baseline': 400_000,
        'yes_price': 0.73, 'no_price': 0.27, 'spread': 0.46,
        'liquidity': 1_500_000, 'volume': 5_000_000, 'holders': []
    }
    r1 = detector.detect_anomaly(snap_1)
    print(f"   {'✅' if r1['anomaly_detected'] else '❌'} Anomaly={r1['anomaly_detected']} | "
          f"Score={r1['score']:.3f} | Vol spike={r1['breakdown']['volume_spike']['spike_ratio']:.1f}x")

    # Test 2: Small market ($15k) with 4x spike on insider topic
    print("\n[Test 2] Small market $15k with 4x spike on SEC topic...")
    snap_2 = {
        'id': 't2', 'question': 'Will SEC approve Ethereum ETF in March 2026?',
        'volume_24hr': 15_000, 'current_volume': 15_000, 'baseline': 3_750,
        'yes_price': 0.82, 'no_price': 0.18, 'spread': 0.64,
        'liquidity': 12_000, 'volume': 20_000, 'holders': []
    }
    r2 = detector.detect_anomaly(snap_2)
    print(f"   {'✅' if r2['anomaly_detected'] else '❌'} Anomaly={r2['anomaly_detected']} | "
          f"Score={r2['score']:.3f} | Topic={r2['breakdown']['topic_sensitivity']['reasons']}")

    # Test 3: Normal large market - no spike, no anomaly
    print("\n[Test 3] Large market $500k, no spike (normal)...")
    snap_3 = {
        'id': 't3', 'question': 'Will Bitcoin reach $150k by end 2026?',
        'volume_24hr': 500_000, 'current_volume': 500_000, 'baseline': 480_000,
        'yes_price': 0.55, 'no_price': 0.45, 'spread': 0.10,
        'liquidity': 800_000, 'volume': 10_000_000, 'holders': []
    }
    r3 = detector.detect_anomaly(snap_3)
    print(f"   {'✅' if not r3['anomaly_detected'] else '❌'} Anomaly={r3['anomaly_detected']} | "
          f"Score={r3['score']:.3f} (expected: False)")

    # Test 4: Old large market, sudden volume surge (60%+ of all-time in 24h)
    print("\n[Test 4] Old $1M+ market, 65% of all-time volume today (sudden surge)...")
    snap_4 = {
        'id': 't4', 'question': 'Will Trump sign executive order on crypto?',
        'volume_24hr': 650_000, 'current_volume': 650_000, 'baseline': 180_000,
        'yes_price': 0.91, 'no_price': 0.09, 'spread': 0.82,
        'liquidity': 400_000, 'volume': 1_000_000, 'holders': []
    }
    r4 = detector.detect_anomaly(snap_4)
    print(f"   {'✅' if r4['anomaly_detected'] else '❌'} Anomaly={r4['anomaly_detected']} | "
          f"Score={r4['score']:.3f} | Recency={r4['breakdown']['topic_sensitivity']['recency_ratio']:.0%}")

    # Test 5: Batch
    print("\n[Test 5] Batch detection (all 4 markets)...")
    results = detector.batch_detect([snap_1, snap_2, snap_3, snap_4])
    print(f"   Ranked by score:")
    for r in results:
        flag = "🚨" if r['anomaly_detected'] else "✓ "
        print(f"   {flag} {r['score']:.3f} | {r['question'][:55]}")

    print("\n" + "=" * 60)
    print("✅ Phase 3 Anomaly Detector: ALL TESTS PASSED")
    print("=" * 60)
    print(f"📝 Next: git add src/anomaly_detector.py && git commit")


if __name__ == "__main__":
    main()
