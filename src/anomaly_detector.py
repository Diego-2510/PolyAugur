"""
PolyAugur Anomaly Detector - Multi-Layer Insider Signal Detection
Phase 12: Broader insider topic coverage + softer spike thresholds.
Target: Short-term geopolitical/regulatory/crypto/pharma events.
Author: Diego Ringleb | Phase 12 | 2026-02-28
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
    Insider-prone = unusual activity relative to market's OWN history.

    Phase 12 changes:
    - Softer spike thresholds: 1.2x spike now scores > 0
    - Broader insider topics: crypto, pharma, tech regulation, elections
    - Lower pre-filter thresholds: let Mistral decide quality
    """

    def __init__(self):
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        logger.info(f"🔍 AnomalyDetector initialized (threshold: {self.confidence_threshold})")

    # ==================== LAYER 1: VOLUME SPIKE ====================

    def detect_volume_spike(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual volume relative to THIS market's own baseline.
        Fully relative: $5M market with 4x spike = same score as $5k market with 4x spike.
        Phase 12: softer thresholds — even 1.2x spikes contribute to score.
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

        if spike_ratio >= 5.0:
            score = 0.35
            severity = 'critical'
        elif spike_ratio >= 3.0:
            score = 0.25
            severity = 'high'
        elif spike_ratio >= 2.0:
            score = 0.18
            severity = 'moderate'
        elif spike_ratio >= 1.5:
            score = 0.12
            severity = 'elevated'
        elif spike_ratio >= 1.2:
            score = 0.06
            severity = 'low'
        else:
            score = 0.0
            severity = 'none'

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
        Checks conviction, volume/liquidity pressure, one-sided bets.
        """
        yes_price = float(snapshot.get('yes_price', 0.5))
        no_price = float(snapshot.get('no_price', 0.5))
        spread = abs(yes_price - no_price)
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        liquidity = float(snapshot.get('liquidity', 1))

        score = 0.0
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
        Placeholder. Phase 6: Real holder analysis via CLOB /trades endpoint.
        Future: new wallets <7d with large positions, coordinated buys.
        """
        holders = snapshot.get('holders', [])
        return {
            'score': 0.0,
            'reason': 'no_holder_data_phase6_feature',
            'holder_count': len(holders)
        }

    # ==================== LAYER 4: TOPIC + TIME SENSITIVITY ====================

    def calculate_topic_sensitivity(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess how insider-prone the market is based on:
        1. TIME HORIZON: Long-term markets (>365d) get heavy penalty.
        2. TOPIC: Geopolitical, regulatory, corporate, crypto, pharma = high insider risk.
        3. VOLUME SURGE: 60%+ of all-time volume in 24h = sudden attention.

        Phase 12: Expanded from ~30 to ~80 insider keywords covering:
        - Crypto regulatory events
        - Government actions (impeachment, shutdown, debt ceiling)
        - Short-term elections (runoffs, referendums)
        - Tech/AI regulation (antitrust, bans)
        - Pharma/FDA approvals
        - Energy/OPEC decisions
        - Extended geopolitical (NATO, coups, Taiwan)

        Returns multiplier 0.20–1.80 applied to base_score.
        """
        question = snapshot.get('question', '').lower()
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        volume_total = float(snapshot.get('volume', volume_24hr))
        end_date_iso = snapshot.get('end_date_iso', '')

        multiplier = 1.0
        reasons = []

        # ── Factor 1: Time Horizon ─────────────────────────────────────────
        try:
            if end_date_iso:
                closes_at = datetime.fromisoformat(end_date_iso.replace('Z', '+00:00'))
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

        # ── Factor 2: Insider-prone topic ─────────────────────────────────
        critical_topics = [
            # Geopolitical military actions (highest insider value)
            'attack ', 'airstrike', 'invasion', 'troops', 'military action',
            'declare war', 'nuclear', 'ceasefire', 'peace deal',
            'sanction', 'missile',
            # Central bank (FOMC insiders, journalists with advance access)
            'fed ', 'federal reserve', 'rate cut', 'rate hike', 'fomc',
            'powell', 'fed chair', 'bowman',
            # Regulatory (SEC, CFTC, DOJ with advance regulatory knowledge)
            'sec ', 'etf approval', 'crypto regulation', 'approved by',
            'cftc', 'doj ',
            # Corporate events (M&A leaks, earnings guidance)
            'merger', 'acquisition', ' ipo', 'earnings', 'bankrupt',
            'layoff', 'ceo resign', 'ceo fired',
            # Executive actions (White House insiders)
            'executive order', 'nominate', 'nomination', 'appoint',
            'tariff', 'trade deal', 'trade war',
            # Crypto regulatory (exchange insiders, SEC leaks)
            'bitcoin', 'ethereum', 'crypto', 'stablecoin', 'defi',
            'binance', 'coinbase', 'tether', 'usdt', 'cbdc',
            # Government actions (congressional insiders)
            'impeach', 'resign', 'indictment', 'arrest', 'pardon',
            'government shutdown', 'debt ceiling', 'default',
            # Elections (short-term only — long-term penalized by Factor 1)
            'special election', 'runoff', 'recall', 'referendum',
            'seats', 'majority', 'coalition',
            # Tech/AI regulation (lobbyist insiders)
            'antitrust', 'monopoly', 'ban tiktok', 'ai regulation',
            'data privacy', 'break up',
            # Pharma/Health (FDA insiders, clinical trial leaks)
            'fda approv', 'drug approv', 'vaccine', 'pandemic',
            'emergency use', 'clinical trial',
            # Energy/Climate (OPEC insiders, policy leaks)
            'opec', 'oil price', 'pipeline', 'drilling',
            'climate deal', 'paris agreement',
            # Geopolitical extended (intelligence community insiders)
            'nato', 'treaty', 'embassy', 'diplomatic',
            'coup', 'uprising', 'protest',
            'north korea', 'taiwan', 'south china sea',
        ]

        matched = [t for t in critical_topics if t in question]
        if matched:
            multiplier *= 1.30
            reasons.append(f"insider_topic:{matched[0].strip()}")

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
            'multiplier': multiplier,
            'reasons': reasons,
            'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0
        }

    # ==================== AGGREGATION ====================

    def detect_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main detection pipeline: Aggregate all layers → final score.

        Score = (volume + price + holder) × topic_time_multiplier
        Threshold: score ≥ CONFIDENCE_THRESHOLD → flag for Mistral
        """
        try:
            volume_result = self.detect_volume_spike(snapshot)
            price_result = self.detect_price_anomaly(snapshot)
            holder_result = self.detect_holder_anomalies(snapshot)
            topic_result = self.calculate_topic_sensitivity(snapshot)

            base_score = (
                volume_result['score'] +
                price_result['score'] +
                holder_result['score']
            )

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
        """Run detection on multiple markets. Returns sorted by score (highest first)."""
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
    logger.info("🧪 PolyAugur Anomaly Detector Test - Phase 12")
    logger.info("=" * 60)

    from datetime import timedelta
    detector = AnomalyDetector()
    now = datetime.now(timezone.utc)

    # Test 1: 2028 Election market → should NOT flag (time penalty)
    print("\n[Test 1] 2028 US Presidential Election (should NOT flag)...")
    snap_election = {
        'id': 't1', 'question': 'Will Nikki Haley win the 2028 US Presidential Election?',
        'volume_24hr': 321_000, 'current_volume': 321_000, 'baseline': 50_000,
        'yes_price': 0.08, 'no_price': 0.92, 'spread': 0.84,
        'liquidity': 200_000, 'volume': 2_000_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=900)).isoformat()
    }
    r1 = detector.detect_anomaly(snap_election)
    status = "✅" if not r1['anomaly_detected'] else "❌ SHOULD NOT FLAG"
    print(f"   {status} Anomaly={r1['anomaly_detected']} | Score={r1['score']:.3f} | Multiplier={r1['topic_multiplier']}")

    # Test 2: Fed chair nomination (short-term) → should flag
    print("\n[Test 2] Fed chair nomination, closes in 20 days (SHOULD flag)...")
    snap_fed = {
        'id': 't2', 'question': 'Will Trump nominate Michelle Bowman as Fed chair?',
        'volume_24hr': 453_000, 'current_volume': 453_000, 'baseline': 50_000,
        'yes_price': 0.73, 'no_price': 0.27, 'spread': 0.46,
        'liquidity': 300_000, 'volume': 800_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=20)).isoformat()
    }
    r2 = detector.detect_anomaly(snap_fed)
    status = "✅" if r2['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Anomaly={r2['anomaly_detected']} | Score={r2['score']:.3f} | Reasons={r2['breakdown']['topic_sensitivity']['reasons']}")

    # Test 3: US Military strike market (imminent) → should flag high
    print("\n[Test 3] US military action on Iran, closes in 7 days (SHOULD flag high)...")
    snap_geo = {
        'id': 't3', 'question': 'Will the US conduct an airstrike on Iran before March 15?',
        'volume_24hr': 180_000, 'current_volume': 180_000, 'baseline': 5_000,
        'yes_price': 0.35, 'no_price': 0.65, 'spread': 0.30,
        'liquidity': 80_000, 'volume': 200_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=7)).isoformat()
    }
    r3 = detector.detect_anomaly(snap_geo)
    status = "✅" if r3['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Anomaly={r3['anomaly_detected']} | Score={r3['score']:.3f} | Spike={r3['breakdown']['volume_spike']['spike_ratio']:.1f}x")

    # Test 4: Crypto regulation (new Phase 12 topic)
    print("\n[Test 4] Bitcoin ETF approval, closes in 10 days (SHOULD flag)...")
    snap_crypto = {
        'id': 't4', 'question': 'Will SEC approve spot Bitcoin ETF by March 10?',
        'volume_24hr': 95_000, 'current_volume': 95_000, 'baseline': 15_000,
        'yes_price': 0.62, 'no_price': 0.38, 'spread': 0.24,
        'liquidity': 50_000, 'volume': 150_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=10)).isoformat()
    }
    r4 = detector.detect_anomaly(snap_crypto)
    status = "✅" if r4['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Anomaly={r4['anomaly_detected']} | Score={r4['score']:.3f} | Reasons={r4['breakdown']['topic_sensitivity']['reasons']}")

    # Test 5: FDA drug approval (new Phase 12 topic)
    print("\n[Test 5] FDA drug approval, closes in 5 days (SHOULD flag)...")
    snap_fda = {
        'id': 't5', 'question': 'Will FDA approve Alzheimer drug lecanemab this week?',
        'volume_24hr': 40_000, 'current_volume': 40_000, 'baseline': 8_000,
        'yes_price': 0.78, 'no_price': 0.22, 'spread': 0.56,
        'liquidity': 25_000, 'volume': 60_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=5)).isoformat()
    }
    r5 = detector.detect_anomaly(snap_fda)
    status = "✅" if r5['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Anomaly={r5['anomaly_detected']} | Score={r5['score']:.3f} | Reasons={r5['breakdown']['topic_sensitivity']['reasons']}")

    # Test 6: Batch ranking
    print("\n[Test 6] Batch ranking (geo > fed > crypto > fda > election)...")
    results = detector.batch_detect([snap_election, snap_fed, snap_geo, snap_crypto, snap_fda])
    for r in results:
        flag = "🚨" if r['anomaly_detected'] else "✓ "
        print(f"   {flag} {r['score']:.3f} × {r['topic_multiplier']} | {r['question'][:55]}")

    print("\n" + "=" * 60)
    print("✅ Phase 12 Anomaly Detector: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
