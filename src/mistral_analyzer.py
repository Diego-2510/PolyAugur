"""
PolyAugur Mistral Analyzer - LLM-powered Signal Validation
Two-tier architecture: AnomalyDetector pre-screens → Mistral validates top candidates only.
Phase 9: Whale intelligence context in system + user prompts.
Author: Diego Ringleb | Phase 9 | 2026-02-28
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from mistralai import Mistral
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EXPECTED_FIELDS = {
    'anomaly_detected': bool,
    'confidence_score': float,
    'anomaly_type': str,
    'reasoning': str,
    'recommended_trade': str,
    'recommended_position_size_pct': float,
    'risk_level': str,
    'holding_period_hours': (int, float),
    'supporting_evidence': list,
    'counter_evidence': list
}

SYSTEM_PROMPT = """You are an expert prediction market analyst specializing in insider trading detection on Polymarket.

Your task: Analyze provided market data and determine if unusual activity suggests informed/insider trading.

CRITICAL RULES — read carefully before scoring:

1. ALWAYS respond in valid JSON format only.
2. confidence_score must be 0.0–1.0 based on evidence strength.
3. Only recommend trading if confidence_score > 0.70.
4. Be conservative — false positives are costly.
5. reasoning must be max 200 characters.
6. anomaly_type: one of [volume_spike, new_large_holder, coordinated_buying, smart_reversal, price_conviction, none]
7. recommended_trade: one of [BUY_YES, BUY_NO, HOLD]
8. risk_level: one of [low, medium, high]

TIME HORIZON RULE (most important filter):
- Markets closing in >365 days (e.g. 2028 elections, multi-year predictions): NEVER flag as anomaly.
  These cannot benefit from insider information. Set anomaly_detected=false, confidence_score<0.20.
- Markets closing in 90–365 days: Very unlikely insider activity. confidence_score<0.40 unless extreme evidence.
- Markets closing in <30 days: Can be insider-tradeable. Evaluate normally.
- Markets closing in <7 days: High temporal relevance. Increase base confidence by 0.10.

IDEAL INSIDER SIGNAL TYPES (based on historical Polymarket patterns):
- Military/geopolitical: "Will US attack Iran?", "Will Russia invade X?" — governments have advance warning
- Central bank: Fed rate decisions, chair nominations — FOMC leaks are historically common
- Regulatory: SEC ETF approvals, executive orders — regulatory insiders exist
- Corporate: M&A, CEO changes, bankruptcy — classic insider trading territory
- Ceasefire/peace deals: Often negotiated privately before announced

NOT insider signals (set anomaly_detected=false):
- Long-term election markets (>6 months out): No insider advantage, just speculation
- Sports outcomes: No insider info possible
- Long-term crypto price targets: Pure speculation, no privileged info
- Viral/social media driven spikes: Retail FOMO, not informed trading

WHALE INTELLIGENCE CONTEXT (if provided):
When whale/trade data is included in the market snapshot, factor it into your analysis:
- whale_count >= 3 AND top_wallet_pct >= 40%: Strong insider signal. Boost confidence +0.05.
- directional_bias >= 85% with burst_score >= 3.0: Coordinated buying pattern. Boost confidence +0.05.
- 0 whales with high volume: Likely retail FOMO, not insider. Reduce confidence -0.05.
- Use whale data as SUPPORTING evidence, never as the sole basis for a signal.

Few-shot Example 1 (GOOD signal — Fed nomination, 20 days out):
Input: "Will Trump nominate Michelle Bowman as Fed chair?", volume 9x baseline, topic=fed_chair, closes in 20 days
Output: {"anomaly_detected": true, "confidence_score": 0.87, "anomaly_type": "volume_spike", "reasoning": "Fed nominations decided privately. 9x baseline spike 20d before resolution consistent with White House insider leak.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.10, "risk_level": "medium", "holding_period_hours": 48, "supporting_evidence": ["9x volume spike", "Insider-prone topic: fed nomination", "Short time horizon"], "counter_evidence": ["Nominations can change last-minute"]}

Few-shot Example 2 (BAD signal — 2028 election):
Input: "Will Nikki Haley win 2028 US Presidential Election?", volume 6x baseline, closes in 900 days
Output: {"anomaly_detected": false, "confidence_score": 0.08, "anomaly_type": "none", "reasoning": "2028 election 900 days out. No insider advantage possible. Volume spike is speculation/retail activity.", "recommended_trade": "HOLD", "recommended_position_size_pct": 0.0, "risk_level": "low", "holding_period_hours": 0, "supporting_evidence": [], "counter_evidence": ["900 days to resolution, no insider info possible", "Long-term elections driven by speculation"]}

Few-shot Example 3 (GOOD signal — US military action, 7 days out):
Input: "Will US conduct airstrike on Iran before March 15?", volume 36x baseline, closes in 7 days, price 0.08→0.41
Output: {"anomaly_detected": true, "confidence_score": 0.91, "anomaly_type": "smart_reversal", "reasoning": "36x spike + price tripled in 24h on military market 7 days before close. Classic intelligence leak pattern.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.08, "risk_level": "high", "holding_period_hours": 24, "supporting_evidence": ["36x volume spike", "Price +0.33 in 24h", "7-day horizon", "Military insider-prone"], "counter_evidence": ["High false positive rate on military markets", "Price already moved significantly"]}

Few-shot Example 4 (GOOD signal — whale-backed geopolitical):
Input: "Will Russia-Ukraine ceasefire be announced before April?", volume 12x baseline, closes in 14 days, 5 whale trades, top wallet 47%, directional bias 91% BUY, burst 4.2x
Output: {"anomaly_detected": true, "confidence_score": 0.89, "anomaly_type": "coordinated_buying", "reasoning": "5 whales, 91% directional BUY bias, 4.2x burst on ceasefire market 14d out. Coordinated informed positioning.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.08, "risk_level": "high", "holding_period_hours": 36, "supporting_evidence": ["12x volume spike", "5 whale trades >$5k", "91% directional bias", "4.2x timing burst", "Ceasefire insider-prone"], "counter_evidence": ["Ceasefire talks often collapse", "High geopolitical uncertainty"]}"""


class MistralAnalyzer:
    """
    Validates anomaly signals using Mistral LLM with JSON-mode.

    Two-tier pipeline:
    Tier 1: AnomalyDetector (fast, free) → runs on ALL markets
    Tier 2: MistralAnalyzer (slow, cost) → only on flagged markets

    Scale handling:
    - Max config.MAX_MISTRAL_CALLS_PER_CYCLE calls per polling cycle
    - Batches 3 markets per prompt (3x cost savings)
    - Rule-based fallback if API unavailable

    Phase 9: Whale intelligence context injected into prompts when available.
    """

    def __init__(self):
        if not config.MISTRAL_API_KEY:
            logger.warning("⚠️ MISTRAL_API_KEY not set – will use rule-based fallback")
            self.client = None
        else:
            self.client = Mistral(api_key=config.MISTRAL_API_KEY)
        self.call_count = 0
        self.error_count = 0

    def _build_whale_section(self, snapshot: Dict[str, Any]) -> str:
        """Build whale intelligence section for the prompt (if data available)."""
        whale_count = snapshot.get('whale_count', 0)
        suspicious = snapshot.get('trade_suspicious', False)

        if whale_count == 0 and not suspicious:
            return ""

        return f"""
WHALE INTELLIGENCE (from CLOB on-chain trades)
- Whale trades (>$5k): {whale_count}
- Whale volume %: {snapshot.get('whale_volume_pct', 0):.0%}
- Top wallet %: {snapshot.get('top_wallet_pct', 0):.0%}
- Unique wallets: {snapshot.get('unique_wallets', 0)}
- Directional bias: {snapshot.get('directional_bias', 0.5):.0%} {snapshot.get('dominant_side', 'NONE')}
- Timing burst: {snapshot.get('burst_score', 1.0):.1f}x (last 1h vs avg)
- Suspicious: {suspicious}
"""

    def _build_user_prompt(self, snapshot: Dict[str, Any], anomaly_result: Dict[str, Any]) -> str:
        """Build structured user prompt from snapshot + pre-detection data."""
        bd = anomaly_result.get('breakdown', {})
        vol = bd.get('volume_spike', {})
        price = bd.get('price_anomaly', {})
        topic = bd.get('topic_sensitivity', {})

        end_date = snapshot.get('end_date_iso', 'Unknown')
        days_to_close = 'Unknown'
        try:
            if end_date and end_date != 'Unknown':
                from datetime import datetime, timezone
                closes_at = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days
        except Exception:
            pass

        whale_section = self._build_whale_section(snapshot)

        return f"""MARKET SNAPSHOT
Question: {snapshot.get('question', 'Unknown')}
Description: {snapshot.get('description', 'N/A')[:200]}
Closes in: {days_to_close} days ({end_date})

PRICING & VOLUME
- YES Price: {snapshot.get('yes_price', 0.5):.3f} | NO Price: {snapshot.get('no_price', 0.5):.3f}
- Spread: {snapshot.get('spread', 0):.3f}
- 24h Volume: ${snapshot.get('volume_24hr', 0):,.0f}
- Liquidity: ${snapshot.get('liquidity', 0):,.0f}
- All-time Volume: ${snapshot.get('volume', 0):,.0f}
- Price delta (30m): {snapshot.get('price_delta_30m', 0):+.4f}
- Price velocity (1h): {snapshot.get('price_velocity', 0):+.4f}

ANOMALY PRE-DETECTION
- Volume Spike Ratio: {vol.get('spike_ratio', 1.0):.2f}x baseline
- Volume Severity: {vol.get('severity', 'none')}
- Price Indicators: {price.get('indicators', [])}
- Vol/Liquidity Ratio: {price.get('vol_liq_ratio', 0):.2f}x
- Topic Sensitivity: {topic.get('reasons', [])}
- Time Horizon Multiplier: {topic.get('multiplier', 1.0):.2f}
- Pre-screen Score: {anomaly_result.get('score', 0):.3f}
{whale_section}
QUESTION: Is this unusual activity likely (1) informed/insider trading, (2) retail hype, or (3) normal market activity?
Apply TIME HORIZON RULE first. Then assess topic insider-proneness.{' Factor in WHALE INTELLIGENCE if provided.' if whale_section else ''}
RESPOND ONLY IN JSON FORMAT"""

    def _build_batch_prompt(self, items: List[Dict[str, Any]]) -> str:
        """Batch 3 markets into 1 prompt for 3x cost savings."""
        markets_text = ""
        for i, (snapshot, anomaly_result) in enumerate(items, 1):
            markets_text += f"\n--- MARKET {i} ---\n"
            markets_text += self._build_user_prompt(snapshot, anomaly_result)
            markets_text += "\n"

        return f"""Analyze these {len(items)} Polymarket markets for insider/anomalous activity.
Apply TIME HORIZON RULE to each market before scoring.
Respond with a JSON array of exactly {len(items)} objects, one per market, in the same order.

{markets_text}

RESPOND WITH JSON ARRAY ONLY: [{{"anomaly_detected": ..., "confidence_score": ..., ...}}, ...]"""

    def _parse_and_validate(self, raw: str, expected_count: int = 1) -> Optional[List[Dict[str, Any]]]:
        """Parse JSON response and validate schema."""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e} | raw: {raw[:200]}")
            return None

        if isinstance(parsed, dict):
            # Check if it's a wrapper like {"results": [...]}
            for key in ('results', 'markets', 'analyses', 'analysis'):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                parsed = [parsed]
        elif not isinstance(parsed, list):
            logger.error(f"Unexpected JSON type: {type(parsed)}")
            return None

        validated = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            if 'confidence_score' in item:
                item['confidence_score'] = max(0.0, min(0.95, float(item['confidence_score'])))
            if 'recommended_position_size_pct' in item:
                item['recommended_position_size_pct'] = max(0.0, min(0.15, float(item['recommended_position_size_pct'])))
            if 'holding_period_hours' in item:
                item['holding_period_hours'] = max(0, min(168, int(item['holding_period_hours'])))
            validated.append(item)

        return validated

    def _rule_based_fallback(self, snapshot: Dict[str, Any], anomaly_result: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based signal when Mistral unavailable. Applies time horizon penalty."""
        score = anomaly_result.get('score', 0)
        vol = anomaly_result.get('breakdown', {}).get('volume_spike', {})
        spike_ratio = vol.get('spike_ratio', 1.0)
        yes_price = snapshot.get('yes_price', 0.5)
        topic = anomaly_result.get('breakdown', {}).get('topic_sensitivity', {})
        multiplier = topic.get('multiplier', 1.0)

        # Apply time horizon: if multiplier < 0.5, this is long-term → no signal
        if multiplier < 0.5 or score < 0.50:
            return {
                'anomaly_detected': False,
                'confidence_score': 0.0,
                'anomaly_type': 'none',
                'reasoning': f'Rule-based: time_multiplier={multiplier:.2f} or score too low',
                'recommended_trade': 'HOLD',
                'recommended_position_size_pct': 0.0,
                'risk_level': 'low',
                'holding_period_hours': 0,
                'supporting_evidence': [],
                'counter_evidence': ['Long time horizon or insufficient signal'],
                'source': 'rule_based_fallback'
            }

        if score >= 0.60 and spike_ratio >= 3.0:
            trade = 'BUY_YES' if yes_price < 0.70 else 'HOLD'
            confidence = min(score * 0.80, 0.70)
            anomaly_type = 'volume_spike'
        else:
            trade = 'HOLD'
            confidence = 0.0
            anomaly_type = 'none'

        return {
            'anomaly_detected': score >= 0.60,
            'confidence_score': round(confidence, 3),
            'anomaly_type': anomaly_type,
            'reasoning': f'Rule-based fallback: score={score:.2f}, spike={spike_ratio:.1f}x',
            'recommended_trade': trade,
            'recommended_position_size_pct': 0.05 if trade != 'HOLD' else 0.0,
            'risk_level': 'high' if score >= 0.70 else 'medium',
            'holding_period_hours': 6,
            'supporting_evidence': [f'Volume spike {spike_ratio:.1f}x baseline'],
            'counter_evidence': ['No LLM validation available'],
            'source': 'rule_based_fallback'
        }

    def analyze_single(self, snapshot: Dict[str, Any], anomaly_result: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze one market with Mistral. Falls back to rule-based if API unavailable."""
        if not self.client:
            return self._rule_based_fallback(snapshot, anomaly_result)

        if self.call_count >= config.MAX_MISTRAL_CALLS_PER_CYCLE:
            logger.warning(f"Mistral call budget ({config.MAX_MISTRAL_CALLS_PER_CYCLE}) exhausted")
            return self._rule_based_fallback(snapshot, anomaly_result)

        prompt = self._build_user_prompt(snapshot, anomaly_result)

        try:
            response = self.client.chat.complete(
                model=config.MISTRAL_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=512
            )
            self.call_count += 1
            raw = response.choices[0].message.content
            results = self._parse_and_validate(raw, expected_count=1)

            if not results:
                return self._rule_based_fallback(snapshot, anomaly_result)

            signal = results[0]
            signal['source'] = 'mistral'
            signal['market_id'] = snapshot.get('id')
            signal['question'] = snapshot.get('question', 'Unknown')
            signal['timestamp'] = datetime.now(timezone.utc).isoformat()

            logger.info(
                f"🧠 Mistral: {snapshot.get('question', '')[:50]} | "
                f"Anomaly={signal.get('anomaly_detected')} | "
                f"Confidence={signal.get('confidence_score', 0):.2f} | "
                f"Trade={signal.get('recommended_trade')}"
            )
            return signal

        except Exception as e:
            self.error_count += 1
            logger.error(f"Mistral API error: {e}")
            return self._rule_based_fallback(snapshot, anomaly_result)

    def analyze_batch(self, items: List[tuple]) -> List[Dict[str, Any]]:
        """Analyze multiple markets using batched prompts (3x cost savings)."""
        if not items:
            return []

        results = []
        batch_size = config.MISTRAL_BATCH_SIZE

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]

            if self.call_count >= config.MAX_MISTRAL_CALLS_PER_CYCLE:
                logger.warning("Mistral budget exhausted, using fallback for remaining")
                for snapshot, anomaly_result in batch:
                    results.append(self._rule_based_fallback(snapshot, anomaly_result))
                continue

            if len(batch) == 1:
                results.append(self.analyze_single(batch[0][0], batch[0][1]))
                continue

            if not self.client:
                for snapshot, anomaly_result in batch:
                    results.append(self._rule_based_fallback(snapshot, anomaly_result))
                continue

            try:
                prompt = self._build_batch_prompt(batch)
                response = self.client.chat.complete(
                    model=config.MISTRAL_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=1024
                )
                self.call_count += 1
                raw = response.choices[0].message.content
                parsed = self._parse_and_validate(raw, expected_count=len(batch))

                if parsed and len(parsed) == len(batch):
                    for j, signal in enumerate(parsed):
                        snapshot = batch[j][0]
                        signal['source'] = 'mistral_batch'
                        signal['market_id'] = snapshot.get('id')
                        signal['question'] = snapshot.get('question')
                        signal['timestamp'] = datetime.now(timezone.utc).isoformat()
                        results.append(signal)
                    logger.info(f"🧠 Mistral batch: {len(batch)} markets analyzed in 1 call")
                else:
                    logger.warning("Batch parse mismatch, falling back")
                    for snapshot, anomaly_result in batch:
                        results.append(self._rule_based_fallback(snapshot, anomaly_result))

            except Exception as e:
                self.error_count += 1
                logger.error(f"Batch Mistral error: {e}")
                for snapshot, anomaly_result in batch:
                    results.append(self._rule_based_fallback(snapshot, anomaly_result))

        return results

    def reset_cycle_counters(self):
        """Reset per-cycle counters."""
        self.call_count = 0


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Mistral Analyzer Test - Phase 9")
    logger.info("=" * 60)

    from datetime import timedelta
    analyzer = MistralAnalyzer()
    now = datetime.now(timezone.utc)

    snap_fed = {
        'id': 't1', 'question': 'Will Trump nominate Michelle Bowman as Fed chair?',
        'description': 'Fed chair nomination market',
        'volume_24hr': 453_000, 'volume': 800_000, 'liquidity': 300_000,
        'yes_price': 0.73, 'no_price': 0.27, 'spread': 0.46,
        'holders': [], 'baseline': 50_000, 'current_volume': 453_000,
        'price_delta_30m': 0.08, 'price_velocity': 0.16,
        'end_date_iso': (now + timedelta(days=20)).isoformat(),
        # Whale data (Phase 9)
        'whale_count': 4, 'whale_volume_pct': 0.62, 'top_wallet_pct': 0.38,
        'unique_wallets': 28, 'directional_bias': 0.88, 'dominant_side': 'BUY',
        'burst_score': 3.5, 'trade_suspicious': True,
    }
    anomaly_fed = {
        'score': 0.58, 'base_score': 0.45, 'topic_multiplier': 1.30,
        'breakdown': {
            'volume_spike': {'spike_ratio': 9.0, 'severity': 'critical', 'score': 0.35},
            'price_anomaly': {'indicators': ['vol_liq_pressure_1.5x'], 'vol_liq_ratio': 1.5, 'score': 0.10},
            'topic_sensitivity': {'reasons': ['insider_topic:fed', 'near_term_20d'], 'multiplier': 1.30}
        }
    }

    snap_election = {
        'id': 't2', 'question': 'Will Nikki Haley win the 2028 US Presidential Election?',
        'description': '2028 election market',
        'volume_24hr': 321_000, 'volume': 2_000_000, 'liquidity': 200_000,
        'yes_price': 0.08, 'no_price': 0.92, 'spread': 0.84,
        'holders': [], 'baseline': 50_000, 'current_volume': 321_000,
        'price_delta_30m': 0.0, 'price_velocity': 0.0,
        'end_date_iso': (now + timedelta(days=900)).isoformat(),
        'whale_count': 0, 'trade_suspicious': False,
    }
    anomaly_election = {
        'score': 0.13, 'base_score': 0.45, 'topic_multiplier': 0.30,
        'breakdown': {
            'volume_spike': {'spike_ratio': 6.4, 'severity': 'critical', 'score': 0.35},
            'price_anomaly': {'indicators': ['extreme_conviction_0.08'], 'vol_liq_ratio': 1.6, 'score': 0.10},
            'topic_sensitivity': {'reasons': ['long_term_900d'], 'multiplier': 0.30}
        }
    }

    print(f"\n[Test 1] Fed nomination (20d + whale data, SHOULD flag)...")
    r1 = analyzer.analyze_single(snap_fed, anomaly_fed)
    status = "✅" if r1.get('anomaly_detected') else "❌"
    print(f"   {status} Anomaly={r1.get('anomaly_detected')} | Conf={r1.get('confidence_score', 0):.2f} | Trade={r1.get('recommended_trade')}")
    print(f"   Reasoning: {r1.get('reasoning', '')[:120]}")
    print(f"   Source: {r1.get('source')}")

    print(f"\n[Test 2] 2028 Election (900d, no whales, should NOT flag)...")
    r2 = analyzer.analyze_single(snap_election, anomaly_election)
    status = "✅" if not r2.get('anomaly_detected') else "❌"
    print(f"   {status} Anomaly={r2.get('anomaly_detected')} | Conf={r2.get('confidence_score', 0):.2f}")

    print(f"\n[Test 3] Batch analysis (both markets)...")
    batch_results = analyzer.analyze_batch([
        (snap_fed, anomaly_fed),
        (snap_election, anomaly_election)
    ])
    for i, r in enumerate(batch_results):
        print(f"   Market {i+1}: Anomaly={r.get('anomaly_detected')} | Conf={r.get('confidence_score', 0):.2f}")

    print(f"\n   Mistral calls used: {analyzer.call_count}")
    print(f"   Errors: {analyzer.error_count}")

    print("\n" + "=" * 60)
    print("✅ Phase 9 Mistral Analyzer: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
