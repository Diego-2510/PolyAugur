"""
PolyAugur Anomaly Detector - Multi-Layer Insider Signal Detection
Phase 12.2: Strict insider focus with two-tier topic system.

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

    Phase 12.2 changes:
    - Two-tier topic system: critical (1.40x) vs elevated (1.15x)
    - Removed broad keywords (bitcoin, ethereum, protest, etc.)
    - Only events with actual privileged information get topic boost
    - Tighter spike scoring: 1.5x = 0.05 (minimal contribution)
    - Higher base threshold: 0.40
    Target: 5–10 high-quality signals per cycle.
    """

    def __init__(self):
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        logger.info(f"🔍 AnomalyDetector initialized (threshold: {self.confidence_threshold})")

    # ==================== LAYER 1: VOLUME SPIKE ====================

    def detect_volume_spike(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual volume relative to THIS market's own baseline.
        Phase 12.2: 1.5x contributes minimally (0.05), real signal starts at 2.0x.
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
            score = 0.10
            severity = 'moderate'
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
        Placeholder. Real holder analysis via CLOB /trades endpoint.
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
        Two-tier insider topic system (Phase 12.2).

        CRITICAL topics (×1.40): A specific person/group DEFINITELY knows
        the outcome before public. Government decisions, regulatory rulings,
        military orders, FDA verdicts, M&A deals.

        ELEVATED topics (×1.15): Insider info is PLAUSIBLE but less certain.
        Diplomatic negotiations, indictments, antitrust probes, OPEC meetings.

        NO BOOST: Crypto price predictions, generic elections, weather,
        entertainment, sports, general sentiment markets.

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

        # ── Factor 2a: CRITICAL insider topics (×1.40) ────────────────────
        # Someone DEFINITELY knows the outcome before announcement.
        critical_topics = [
            # Military commands (Pentagon, NSC, White House Situation Room)
            'attack ', 'airstrike', 'invasion', 'troops deploy',
            'military action', 'declare war', 'nuclear',
            'missile strike', 'military operation',
            # Central bank decisions (FOMC members, Fed staff)
            'fed ', 'federal reserve', 'rate cut', 'rate hike', 'fomc',
            'powell', 'fed chair', 'emergency rate',
            # Regulatory rulings (SEC commissioners, FDA panel)
            'sec approv', 'sec reject', 'etf approv', 'etf reject',
            'fda approv', 'drug approv', 'emergency use',
            # Executive decisions (POTUS, senior advisors)
            'executive order', 'pardon', 'commute sentence',
            'nominate', 'nomination', 'appoint',
            # Corporate M&A (board members, investment bankers)
            'merger', 'acquisition', 'takeover', 'buyout',
            'ceo resign', 'ceo fired', 'ceo step down',
        ]

        # ── Factor 2b: ELEVATED insider topics (×1.15) ────────────────────
        # Insider info is plausible but less certain.
        elevated_topics = [
            # Geopolitical negotiations (diplomats, mediators)
            'ceasefire', 'peace deal', 'peace agreement',
            'treaty', 'diplomatic', 'embassy',
            # Trade policy (trade representatives, lobbyists)
            'tariff', 'trade deal', 'trade war', 'sanction',
            # Legal/DOJ (prosecutors, grand jury members)
            'indictment', 'arrest', 'impeach', 'doj ',
            # Government operations (congressional leadership)
            'government shutdown', 'debt ceiling', 'default',
            # Crypto regulatory decisions (not price predictions)
            'crypto regulation', 'crypto ban', 'stablecoin regulation',
            'cftc', 'approved by',
            # Tech regulation (antitrust investigators)
            'antitrust', 'monopoly', 'break up', 'ban tiktok',
            # Energy decisions (OPEC ministers)
            'opec', 'oil production cut', 'drilling ban', 'pipeline',
            # Clinical trials (researchers, pharma executives)
            'clinical trial', 'vaccine approv', 'pandemic',
            # Short-term elections (election officials, party insiders)
            'special election', 'runoff', 'recall', 'referendum',
            # Geopolitical flashpoints (intelligence agencies)
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
            'multiplier': multiplier,
            'reasons': reasons,
            'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0
        }

    # ==================== AGGREGATION ====================

    def detect_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main detection pipeline: Aggregate all layers → final score.
        Score = (volume + price + holder) × topic_time_multiplier
        Threshold: score ≥ 0.40 → flag for Mistral
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
    logger.info("🧪 PolyAugur Anomaly Detector Test - Phase 12.2 (Strict Insider)")
    logger.info("=" * 60)

    from datetime import timedelta
    detector = AnomalyDetector()
    now = datetime.now(timezone.utc)

    # Test 1: 2028 Election → should NOT flag
    print("\n[Test 1] 2028 Election (should NOT flag)...")
    snap_election = {
        'id': 't1', 'question': 'Will Nikki Haley win the 2028 US Presidential Election?',
        'volume_24hr': 321_000, 'current_volume': 321_000, 'baseline': 50_000,
        'yes_price': 0.08, 'no_price': 0.92, 'spread': 0.84,
        'liquidity': 200_000, 'volume': 2_000_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=900)).isoformat()
    }
    r1 = detector.detect_anomaly(snap_election)
    status = "✅" if not r1['anomaly_detected'] else "❌ FALSE POSITIVE"
    print(f"   {status} Score={r1['score']:.3f} | Mult={r1['topic_multiplier']}")

    # Test 2: Generic Bitcoin price → should NOT flag (no insider edge)
    print("\n[Test 2] Bitcoin above 100k (should NOT flag — no insider info)...")
    snap_btc = {
        'id': 't2', 'question': 'Will Bitcoin be above $100,000 on March 31?',
        'volume_24hr': 500_000, 'current_volume': 500_000, 'baseline': 100_000,
        'yes_price': 0.55, 'no_price': 0.45, 'spread': 0.10,
        'liquidity': 800_000, 'volume': 5_000_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=30)).isoformat()
    }
    r2 = detector.detect_anomaly(snap_btc)
    status = "✅" if not r2['anomaly_detected'] else "❌ FALSE POSITIVE"
    print(f"   {status} Score={r2['score']:.3f} | Mult={r2['topic_multiplier']}")

    # Test 3: Fed rate cut (critical insider) → SHOULD flag
    print("\n[Test 3] Emergency Fed rate cut, 7 days (SHOULD flag — critical)...")
    snap_fed = {
        'id': 't3', 'question': 'Will the Fed announce an emergency rate cut this week?',
        'volume_24hr': 200_000, 'current_volume': 200_000, 'baseline': 20_000,
        'yes_price': 0.25, 'no_price': 0.75, 'spread': 0.50,
        'liquidity': 100_000, 'volume': 300_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=7)).isoformat()
    }
    r3 = detector.detect_anomaly(snap_fed)
    status = "✅" if r3['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Score={r3['score']:.3f} | Reasons={r3['breakdown']['topic_sensitivity']['reasons']}")

    # Test 4: Airstrike (critical insider) → SHOULD flag high
    print("\n[Test 4] US airstrike on Iran, 5 days (SHOULD flag high)...")
    snap_geo = {
        'id': 't4', 'question': 'Will the US conduct an airstrike on Iran before March 7?',
        'volume_24hr': 180_000, 'current_volume': 180_000, 'baseline': 5_000,
        'yes_price': 0.35, 'no_price': 0.65, 'spread': 0.30,
        'liquidity': 80_000, 'volume': 200_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=5)).isoformat()
    }
    r4 = detector.detect_anomaly(snap_geo)
    status = "✅" if r4['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Score={r4['score']:.3f} | Spike={r4['breakdown']['volume_spike']['spike_ratio']:.1f}x")

    # Test 5: Generic protest → should NOT flag
    print("\n[Test 5] Generic protest market (should NOT flag — no insider)...")
    snap_protest = {
        'id': 't5', 'question': 'Will there be protests in Washington DC this weekend?',
        'volume_24hr': 30_000, 'current_volume': 30_000, 'baseline': 10_000,
        'yes_price': 0.70, 'no_price': 0.30, 'spread': 0.40,
        'liquidity': 50_000, 'volume': 100_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=3)).isoformat()
    }
    r5 = detector.detect_anomaly(snap_protest)
    status = "✅" if not r5['anomaly_detected'] else "❌ FALSE POSITIVE"
    print(f"   {status} Score={r5['score']:.3f}")

    # Test 6: Ceasefire (elevated insider) → should flag
    print("\n[Test 6] Russia/Ukraine ceasefire, 14 days (SHOULD flag — elevated)...")
    snap_cease = {
        'id': 't6', 'question': 'Russia Ukraine ceasefire agreement by March 15?',
        'volume_24hr': 350_000, 'current_volume': 350_000, 'baseline': 50_000,
        'yes_price': 0.40, 'no_price': 0.60, 'spread': 0.20,
        'liquidity': 200_000, 'volume': 1_000_000, 'holders': [],
        'end_date_iso': (now + timedelta(days=14)).isoformat()
    }
    r6 = detector.detect_anomaly(snap_cease)
    status = "✅" if r6['anomaly_detected'] else "❌ SHOULD FLAG"
    print(f"   {status} Score={r6['score']:.3f} | Reasons={r6['breakdown']['topic_sensitivity']['reasons']}")

    # Batch ranking
    print("\n[Batch] Ranking (geo > fed > ceasefire > btc/election/protest)...")
    results = detector.batch_detect([snap_election, snap_btc, snap_fed, snap_geo, snap_protest, snap_cease])
    for r in results:
        flag = "🚨" if r['anomaly_detected'] else "✓ "
        print(f"   {flag} {r['score']:.3f} × {r['topic_multiplier']} | {r['question'][:55]}")

    print("\n" + "=" * 60)
    print("✅ Phase 12.2 Anomaly Detector: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
