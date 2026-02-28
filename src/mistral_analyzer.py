"""
PolyAugur Mistral Analyzer - LLM-powered Signal Validation
Two-tier architecture: AnomalyDetector pre-screens → Mistral validates top candidates only.
Author: Diego Ringleb | Phase 4 | 2026-02-28
Architecture: mache-es-sehr-viel-ausfuhrlicher.md [file:1] Abschnitt 6
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

# ── Response Schema ──────────────────────────────────────────────────────
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

Your task: Analyze the provided market data and determine if unusual activity suggests informed/insider trading.

Rules:
1. ALWAYS respond in valid JSON format only.
2. confidence_score must be 0.0-1.0 based on evidence strength.
3. Only recommend trading if confidence_score > 0.70.
4. Be conservative — false positives are costly.
5. reasoning must be max 200 characters.
6. anomaly_type: one of [volume_spike, new_large_holder, coordinated_buying, smart_reversal, price_conviction, none]
7. recommended_trade: one of [BUY_YES, BUY_NO, HOLD]
8. risk_level: one of [low, medium, high]

Few-shot Example (anomaly):
Input: Fed rate cut market, volume 4.2x baseline, 2 new wallets, 150k YES positions
Output: {"anomaly_detected": true, "confidence_score": 0.87, "anomaly_type": "coordinated_buying", "reasoning": "Volume 4.2x baseline in 30min, 2 new wallets 150k, pre-announcement pattern", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.12, "risk_level": "medium", "holding_period_hours": 6, "supporting_evidence": ["5.2x volume spike", "New wallets age <7 days"], "counter_evidence": ["Fed announcements are public"]}

Few-shot Example (false positive):
Input: NFL Super Bowl market, volume 2.1x. Note: spike after halftime.
Output: {"anomaly_detected": false, "confidence_score": 0.12, "anomaly_type": "none", "reasoning": "Volume spike is normal reaction to live event, not insider activity", "recommended_trade": "HOLD", "recommended_position_size_pct": 0.0, "risk_level": "low", "holding_period_hours": 0, "supporting_evidence": [], "counter_evidence": ["Live event explains volume spike"]}"""


class MistralAnalyzer:
    """
    Validates anomaly signals using Mistral LLM with JSON-mode.

    Two-tier pipeline [file:1] Abschnitt 6:
    Tier 1: AnomalyDetector (fast, free) → runs on ALL markets
    Tier 2: MistralAnalyzer (slow, $0.48/call) → only on flagged markets

    Scale handling:
    - Max config.MAX_MISTRAL_CALLS_PER_CYCLE calls per polling cycle
    - Batches 3 markets per prompt (3x cost savings)
    - Rule-based fallback if API unavailable
    """

    def __init__(self):
        if not config.MISTRAL_API_KEY:
            logger.warning("⚠️ MISTRAL_API_KEY not set – will use rule-based fallback")
            self.client = None
        else:
            self.client = Mistral(api_key=config.MISTRAL_API_KEY)
        self.call_count = 0
        self.error_count = 0

    def _build_user_prompt(self, snapshot: Dict[str, Any], anomaly_result: Dict[str, Any]) -> str:
        """Build structured user prompt from snapshot + pre-detection data [file:1] Abschnitt 6.1.2."""
        bd = anomaly_result.get('breakdown', {})
        vol = bd.get('volume_spike', {})
        price = bd.get('price_anomaly', {})
        topic = bd.get('topic_sensitivity', {})

        return f"""MARKET SNAPSHOT
Question: {snapshot.get('question', 'Unknown')}
Description: {snapshot.get('description', 'N/A')[:200]}

PRICING & VOLUME
- YES Price: {snapshot.get('yes_price', 0.5):.3f} | NO Price: {snapshot.get('no_price', 0.5):.3f}
- Spread: {snapshot.get('spread', 0):.3f}
- 24h Volume: ${snapshot.get('volume_24hr', 0):,.0f}
- Liquidity: ${snapshot.get('liquidity', 0):,.0f}
- All-time Volume: ${snapshot.get('volume', 0):,.0f}

ANOMALY PRE-DETECTION (Layer 1-3 scores)
- Volume Spike Ratio: {vol.get('spike_ratio', 1.0):.2f}x baseline
- Volume Severity: {vol.get('severity', 'none')}
- Price Indicators: {price.get('indicators', [])}
- Vol/Liquidity Ratio: {price.get('vol_liq_ratio', 0):.2f}x
- Topic Sensitivity: {topic.get('reasons', [])}
- Pre-screen Score: {anomaly_result.get('score', 0):.3f}

HOLDERS: {len(snapshot.get('holders', []))} positions tracked (Phase 4+ feature)

QUESTION: Is this unusual activity likely (1) informed/insider trading, (2) retail hype, or (3) normal market activity? Should we follow this bet?
RESPOND ONLY IN JSON FORMAT"""

    def _build_batch_prompt(self, items: List[Dict[str, Any]]) -> str:
        """
        Batch 3 markets into 1 prompt for 3x cost savings [file:1] Abschnitt 6.4.1.
        Returns JSON array with one result per market.
        """
        markets_text = ""
        for i, (snapshot, anomaly_result) in enumerate(items, 1):
            markets_text += f"\n--- MARKET {i} ---\n"
            markets_text += self._build_user_prompt(snapshot, anomaly_result)
            markets_text += "\n"

        return f"""Analyze these {len(items)} Polymarket markets for insider/anomalous activity.
Respond with a JSON array of exactly {len(items)} objects, one per market, in the same order.
Each object must follow the required schema.

{markets_text}

RESPOND WITH JSON ARRAY ONLY: [{{"anomaly_detected": ..., "confidence_score": ..., ...}}, ...]"""

    def _parse_and_validate(self, raw: str, expected_count: int = 1) -> Optional[List[Dict[str, Any]]]:
        """
        Parse JSON response and validate schema [file:1] Abschnitt 6.3.
        Handles single object or array.
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e} | raw: {raw[:200]}")
            return None

        # Normalize to list
        if isinstance(parsed, dict):
            parsed = [parsed]
        elif not isinstance(parsed, list):
            logger.error(f"Unexpected JSON type: {type(parsed)}")
            return None

        validated = []
        for item in parsed:
            # Sanity checks [file:1] Abschnitt 6.3.2
            if 'confidence_score' in item:
                item['confidence_score'] = max(0.0, min(0.95, float(item['confidence_score'])))
            if 'recommended_position_size_pct' in item:
                item['recommended_position_size_pct'] = max(0.0, min(0.15, float(item['recommended_position_size_pct'])))
            if 'holding_period_hours' in item:
                item['holding_period_hours'] = max(0, min(168, int(item['holding_period_hours'])))

            validated.append(item)

        return validated

    def _rule_based_fallback(self, snapshot: Dict[str, Any], anomaly_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rule-based signal when Mistral unavailable [file:1] Abschnitt 6.3.1.
        Converts anomaly score to trading signal with reduced confidence.
        """
        score = anomaly_result.get('score', 0)
        vol = anomaly_result.get('breakdown', {}).get('volume_spike', {})
        spike_ratio = vol.get('spike_ratio', 1.0)
        yes_price = snapshot.get('yes_price', 0.5)

        if score >= 0.60 and spike_ratio >= 3.0:
            trade = 'BUY_YES' if yes_price < 0.70 else 'HOLD'
            confidence = min(score * 0.80, 0.70)  # Reduced confidence for fallback
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
        """
        Analyze one market with Mistral [file:1] Abschnitt 6.
        Falls back to rule-based if API unavailable.
        """
        if not self.client:
            logger.warning("No Mistral client, using rule-based fallback")
            return self._rule_based_fallback(snapshot, anomaly_result)

        if self.call_count >= config.MAX_MISTRAL_CALLS_PER_CYCLE:
            logger.warning(f"Mistral call budget ({config.MAX_MISTRAL_CALLS_PER_CYCLE}) exhausted this cycle")
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
                temperature=0.1,  # Low temperature for consistent JSON
                max_tokens=512
            )
            self.call_count += 1
            raw = response.choices[0].message.content
            results = self._parse_and_validate(raw, expected_count=1)

            if not results:
                logger.warning("Parse failed, using fallback")
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
        """
        Analyze multiple markets using batched prompts for cost efficiency.
        Groups items into chunks of MISTRAL_BATCH_SIZE.

        Args:
            items: List of (snapshot, anomaly_result) tuples

        Returns:
            List of signal dicts, same order as input
        """
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
                # Single item – use single call
                results.append(self.analyze_single(batch[0][0], batch[0][1]))
                continue

            # Multi-item batch
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
                    logger.warning(f"Batch parse mismatch ({len(parsed) if parsed else 0} vs {len(batch)}), falling back")
                    for snapshot, anomaly_result in batch:
                        results.append(self._rule_based_fallback(snapshot, anomaly_result))

            except Exception as e:
                self.error_count += 1
                logger.error(f"Batch Mistral error: {e}")
                for snapshot, anomaly_result in batch:
                    results.append(self._rule_based_fallback(snapshot, anomaly_result))

        return results

    def reset_cycle_counters(self):
        """Reset per-cycle counters (call after each polling cycle)."""
        self.call_count = 0


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Mistral Analyzer Test - Phase 4")
    logger.info("=" * 60)

    analyzer = MistralAnalyzer()

    # Test snapshots
    snap_1 = {
        'id': 't1', 'question': 'Will the Fed cut rates before June 2026?',
        'description': 'Federal Reserve rate decision market',
        'volume_24hr': 200_000, 'volume': 300_000, 'liquidity': 150_000,
        'yes_price': 0.73, 'no_price': 0.27, 'spread': 0.46,
        'holders': [], 'baseline': 40_000, 'current_volume': 200_000
    }
    anomaly_1 = {
        'score': 0.52, 'base_score': 0.40, 'topic_multiplier': 1.30,
        'breakdown': {
            'volume_spike': {'spike_ratio': 5.0, 'severity': 'critical', 'score': 0.35},
            'price_anomaly': {'indicators': ['vol_liq_pressure_1.3x'], 'vol_liq_ratio': 1.3, 'score': 0.06},
            'topic_sensitivity': {'reasons': ['insider_topic:fed'], 'multiplier': 1.30}
        }
    }

    snap_2 = {
        'id': 't2', 'question': 'Will SEC approve Ethereum ETF in Q1 2026?',
        'description': 'SEC Ethereum ETF approval market',
        'volume_24hr': 15_000, 'volume': 20_000, 'liquidity': 12_000,
        'yes_price': 0.88, 'no_price': 0.12, 'spread': 0.76,
        'holders': [], 'baseline': 3_000, 'current_volume': 15_000
    }
    anomaly_2 = {
        'score': 0.61, 'base_score': 0.47, 'topic_multiplier': 1.30,
        'breakdown': {
            'volume_spike': {'spike_ratio': 5.0, 'severity': 'critical', 'score': 0.35},
            'price_anomaly': {'indicators': ['extreme_conviction_0.88', 'vol_liq_pressure_1.3x'], 'vol_liq_ratio': 1.3, 'score': 0.18},
            'topic_sensitivity': {'reasons': ['insider_topic:sec'], 'multiplier': 1.30}
        }
    }

    snap_3 = {
        'id': 't3', 'question': 'Will Bitcoin reach $200k by end 2026?',
        'description': 'Bitcoin price prediction',
        'volume_24hr': 500_000, 'volume': 5_000_000, 'liquidity': 1_000_000,
        'yes_price': 0.40, 'no_price': 0.60, 'spread': 0.20,
        'holders': [], 'baseline': 450_000, 'current_volume': 500_000
    }
    anomaly_3 = {
        'score': 0.05, 'base_score': 0.04, 'topic_multiplier': 1.0,
        'breakdown': {
            'volume_spike': {'spike_ratio': 1.1, 'severity': 'none', 'score': 0.0},
            'price_anomaly': {'indicators': [], 'vol_liq_ratio': 0.5, 'score': 0.04},
            'topic_sensitivity': {'reasons': [], 'multiplier': 1.0}
        }
    }

    if not config.MISTRAL_API_KEY:
        print("\n⚠️ No MISTRAL_API_KEY – testing rule-based fallback")
        print("\n[Test 1] Rule-based fallback (single)...")
        r1 = analyzer.analyze_single(snap_1, anomaly_1)
        print(f"✅ Source={r1.get('source')} | Trade={r1.get('recommended_trade')} | Confidence={r1.get('confidence_score', 0):.2f}")

        print("\n[Test 2] Batch fallback (3 markets)...")
        batch_results = analyzer.analyze_batch([
            (snap_1, anomaly_1),
            (snap_2, anomaly_2),
            (snap_3, anomaly_3)
        ])
        for r in batch_results:
            flag = "🚨" if r.get('anomaly_detected') else "✓ "
            print(f"   {flag} {r.get('question', '')[:50]} | "
                  f"Conf={r.get('confidence_score', 0):.2f} | Trade={r.get('recommended_trade')}")
    else:
        print("\n✅ MISTRAL_API_KEY found – running live tests")
        print("\n[Test 1] Single market analysis...")
        r1 = analyzer.analyze_single(snap_1, anomaly_1)
        print(f"✅ Anomaly={r1.get('anomaly_detected')} | Conf={r1.get('confidence_score', 0):.2f}")
        print(f"   Reasoning: {r1.get('reasoning', '')[:100]}")

        print("\n[Test 2] Batch analysis (3 markets, 1 API call)...")
        batch_results = analyzer.analyze_batch([
            (snap_1, anomaly_1),
            (snap_2, anomaly_2),
            (snap_3, anomaly_3)
        ])
        print(f"✅ Analyzed {len(batch_results)} markets, API calls: {analyzer.call_count}")
        for r in batch_results:
            flag = "🚨" if r.get('anomaly_detected') else "✓ "
            print(f"   {flag} {r.get('question', '')[:50]} | Conf={r.get('confidence_score', 0):.2f}")

    print("\n" + "=" * 60)
    print("✅ Phase 4 Mistral Analyzer: ALL TESTS PASSED")
    print("=" * 60)
    print("📝 Next:")
    print("   1. Add MISTRAL_API_KEY to .env")
    print("   2. git add src/mistral_analyzer.py config.py src/data_fetcher.py")
    print("   3. git commit -m 'feat(mistral): Phase 4 - LLM analysis + pagination scaling'")


if __name__ == "__main__":
    main()
