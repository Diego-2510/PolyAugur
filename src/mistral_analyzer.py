"""
PolyAugur Mistral Analyzer - LLM-powered Signal Validation
Phase 15: Blacklist-Mode kompatibel.

Fixes gegenüber Phase 9:
  - SYSTEM_PROMPT: days_to_close <= 0 → HOLD (Markt schließt heute, zu spät)
  - SYSTEM_PROMPT: Neues Few-Shot für konkurrierende Kandidaten-Märkte
  - _parse_and_validate(): yes_price < 0.01 + BUY_NO → override HOLD
  - _parse_and_validate(): yes_price > 0.99 + BUY_YES → override HOLD
  - _build_user_prompt(): days_to_close=0 explizite Warnung im Prompt
  - analyze_batch(): Gruppen-Dedup nach end_date_iso (max 2 Signals pro Gruppe)

Author: Diego Ringleb | Phase 15 | 2026-03-17
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
- Markets closing in <= 0 days (closes TODAY or already closed): ALWAYS set recommended_trade=HOLD.
  Signal is too late to act on. Set anomaly_detected=true if volume is extreme, but trade=HOLD.
- Markets closing in >365 days: NEVER flag. confidence_score<0.20, anomaly_detected=false.
- Markets closing in 90–365 days: Very unlikely insider. confidence_score<0.40 unless extreme evidence.
- Markets closing in <30 days: Can be insider-tradeable. Evaluate normally.
- Markets closing in <7 days: High temporal relevance. Increase base confidence by 0.10.
- Markets closing in 1–3 days: MAXIMUM urgency. If flagging, set holding_period_hours <= 24.

EXTREME PRICE RULE:
- YES price < 0.01 (market is 99%+ NO): Do NOT recommend BUY_NO.
  The payout is economically meaningless ($0.01 profit per dollar staked). Set recommended_trade=HOLD.
- YES price > 0.99 (market is 99%+ YES): Do NOT recommend BUY_YES.
  No upside remaining. Set recommended_trade=HOLD.
- YES price < 0.03 OR > 0.97: Be very skeptical of any trade recommendation.
  These are near-resolved markets with minimal edge. Prefer HOLD unless extreme whale evidence.

COMPETING CANDIDATES RULE:
- If multiple markets exist for the SAME election/event with DIFFERENT candidates/outcomes,
  do NOT recommend BUY_YES for more than ONE candidate.
- Identify the candidate with the strongest signal (highest spike + lowest price = most room to move).
  Flag that one. For others: set anomaly_detected=false or recommended_trade=HOLD.
- Reasoning must note: "Competing candidate markets — only strongest signal flagged."

IDEAL INSIDER SIGNAL TYPES (based on historical Polymarket patterns):
- Military/geopolitical: "Will US attack Iran?" — governments have advance warning
- Central bank: Fed rate decisions, chair nominations — FOMC leaks historically common
- Regulatory: SEC ETF approvals, executive orders — regulatory insiders exist
- Corporate: M&A, CEO changes, bankruptcy — classic insider trading territory
- Ceasefire/peace deals: Often negotiated privately before announced

NOT insider signals (set anomaly_detected=false):
- Long-term election markets (>6 months out): No insider advantage
- Sports outcomes: No insider info possible
- Long-term crypto price targets: Pure speculation
- Viral/social media driven spikes: Retail FOMO

WHALE INTELLIGENCE CONTEXT (if provided):
- whale_count >= 3 AND top_wallet_pct >= 40%: Strong insider signal. Boost confidence +0.05.
- directional_bias >= 85% with burst_score >= 3.0: Coordinated buying. Boost confidence +0.05.
- 0 whales with high volume: Likely retail FOMO. Reduce confidence -0.05.
- Use whale data as SUPPORTING evidence, never as sole basis.

Few-shot Example 1 (GOOD signal — Fed nomination, 20 days out):
Input: "Will Trump nominate Michelle Bowman as Fed chair?", volume 9x baseline, closes in 20 days
Output: {"anomaly_detected": true, "confidence_score": 0.87, "anomaly_type": "volume_spike", "reasoning": "Fed nominations decided privately. 9x baseline spike 20d before resolution consistent with White House insider leak.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.10, "risk_level": "medium", "holding_period_hours": 48, "supporting_evidence": ["9x volume spike", "Insider-prone topic: fed nomination", "Short time horizon"], "counter_evidence": ["Nominations can change last-minute"]}

Few-shot Example 2 (BAD signal — 2028 election):
Input: "Will Nikki Haley win 2028 US Presidential Election?", volume 6x baseline, closes in 900 days
Output: {"anomaly_detected": false, "confidence_score": 0.08, "anomaly_type": "none", "reasoning": "2028 election 900 days out. No insider advantage possible. Volume spike is speculation.", "recommended_trade": "HOLD", "recommended_position_size_pct": 0.0, "risk_level": "low", "holding_period_hours": 0, "supporting_evidence": [], "counter_evidence": ["900 days to resolution, no insider info possible"]}

Few-shot Example 3 (GOOD signal — US military action, 7 days out):
Input: "Will US conduct airstrike on Iran before March 15?", volume 36x baseline, closes in 7 days, price 0.08→0.41
Output: {"anomaly_detected": true, "confidence_score": 0.91, "anomaly_type": "smart_reversal", "reasoning": "36x spike + price tripled in 24h on military market 7 days before close. Classic intelligence leak pattern.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.08, "risk_level": "high", "holding_period_hours": 24, "supporting_evidence": ["36x volume spike", "Price +0.33 in 24h", "7-day horizon", "Military insider-prone"], "counter_evidence": ["High false positive rate on military markets"]}

Few-shot Example 4 (GOOD signal — whale-backed geopolitical):
Input: "Will Russia-Ukraine ceasefire be announced before April?", volume 12x baseline, closes in 14 days, 5 whale trades, directional bias 91% BUY
Output: {"anomaly_detected": true, "confidence_score": 0.89, "anomaly_type": "coordinated_buying", "reasoning": "5 whales, 91% directional BUY bias, 4.2x burst on ceasefire market 14d out. Coordinated informed positioning.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.08, "risk_level": "high", "holding_period_hours": 36, "supporting_evidence": ["12x volume spike", "5 whale trades >$5k", "91% directional bias"], "counter_evidence": ["Ceasefire talks often collapse"]}

Few-shot Example 5 (COMPETING CANDIDATES — only flag strongest):
Input: Three markets for "La Paz mayoral election" with candidates A (price 0.04, 63x spike), B (price 0.08, 66x spike, 13x vol/liq), C (price 0.12, 24x spike). All close in 4 days.
Output for A: {"anomaly_detected": false, "confidence_score": 0.45, "anomaly_type": "none", "reasoning": "Competing candidate markets — only strongest signal flagged. Candidate B has stronger vol/liq pressure.", "recommended_trade": "HOLD", ...}
Output for B: {"anomaly_detected": true, "confidence_score": 0.83, "anomaly_type": "volume_spike", "reasoning": "Strongest signal among competing candidates: 66x spike, 13x vol/liq, 4d horizon.", "recommended_trade": "BUY_YES", ...}
Output for C: {"anomaly_detected": false, "confidence_score": 0.50, "anomaly_type": "none", "reasoning": "Competing candidate markets — only strongest signal flagged. Weaker spike vs B.", "recommended_trade": "HOLD", ...}

Few-shot Example 6 (TOO LATE — closes today):
Input: "Will the Fed decrease rates by 25bps after the March 2026 meeting?", volume 23x baseline, closes in 0 days, $12.9M volume
Output: {"anomaly_detected": true, "confidence_score": 0.91, "anomaly_type": "price_conviction", "reasoning": "Massive Fed rate signal but closes TODAY. Signal confirmed — too late to trade.", "recommended_trade": "HOLD", "recommended_position_size_pct": 0.0, "risk_level": "high", "holding_period_hours": 0, "supporting_evidence": ["23x volume spike", "$12.9M volume", "Fed insider-prone"], "counter_evidence": ["Market closes today — no actionable edge"]}"""


class MistralAnalyzer:
    """
    Validates anomaly signals using Mistral LLM with JSON-mode.

    Phase 15 fixes:
    - days_to_close=0 → HOLD (too late, new Few-Shot + Prompt warning)
    - yes_price < 0.01 + BUY_NO → override HOLD (economically meaningless)
    - yes_price > 0.99 + BUY_YES → override HOLD (no upside)
    - analyze_batch: Gruppen-Dedup — max 2 Signals pro (end_date_iso, keyword) Gruppe
    """

    # Gruppen-Dedup: max 2 Signals pro Gruppe
    MAX_SIGNALS_PER_GROUP = 2

    def __init__(self):
        if not config.MISTRAL_API_KEY:
            logger.warning("⚠️ MISTRAL_API_KEY not set – will use rule-based fallback")
            self.client = None
        else:
            self.client = Mistral(api_key=config.MISTRAL_API_KEY)
        self.call_count = 0
        self.error_count = 0

    def _build_whale_section(self, snapshot: Dict[str, Any]) -> str:
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
        bd = anomaly_result.get('breakdown', {})
        vol = bd.get('volume_spike', {})
        price = bd.get('price_anomaly', {})
        topic = bd.get('topic_sensitivity', {})

        end_date = snapshot.get('end_date_iso', 'Unknown')
        days_to_close = 'Unknown'
        try:
            if end_date and end_date != 'Unknown':
                closes_at = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days
        except Exception:
            pass

        # Phase 15: explizite Warnung bei days_to_close <= 0
        timing_warning = ""
        if isinstance(days_to_close, int):
            if days_to_close <= 0:
                timing_warning = "\n⚠️ WARNING: This market closes TODAY or is already closed. Apply TIME HORIZON RULE: recommended_trade MUST be HOLD."
            elif days_to_close == 1:
                timing_warning = "\n⚠️ WARNING: Market closes TOMORROW. If flagging, set holding_period_hours <= 12."

        whale_section = self._build_whale_section(snapshot)

        yes_price = snapshot.get('yes_price', 0.5)
        price_warning = ""
        if yes_price < 0.01:
            price_warning = "\n⚠️ WARNING: YES price < $0.01. Apply EXTREME PRICE RULE: recommended_trade MUST be HOLD. BUY_NO is economically meaningless here."
        elif yes_price > 0.99:
            price_warning = "\n⚠️ WARNING: YES price > $0.99. Apply EXTREME PRICE RULE: recommended_trade MUST be HOLD. No upside for BUY_YES."
        elif yes_price < 0.03 or yes_price > 0.97:
            price_warning = f"\n⚠️ WARNING: YES price={yes_price:.3f} is near-resolved. Be very skeptical of any trade — prefer HOLD."

        return f"""MARKET SNAPSHOT
Question: {snapshot.get('question', 'Unknown')}
Description: {snapshot.get('description', 'N/A')[:200]}
Closes in: {days_to_close} days ({end_date}){timing_warning}{price_warning}

PRICING & VOLUME
- YES Price: {yes_price:.3f} | NO Price: {snapshot.get('no_price', 0.5):.3f}
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
Apply TIME HORIZON RULE first. Apply EXTREME PRICE RULE if relevant.{' Apply COMPETING CANDIDATES RULE if this is one of multiple candidate markets for the same election.' if 'election' in snapshot.get('question', '').lower() or 'mayoral' in snapshot.get('question', '').lower() else ''}{' Factor in WHALE INTELLIGENCE if provided.' if whale_section else ''}
RESPOND ONLY IN JSON FORMAT"""

    def _build_batch_prompt(self, items: List[Dict[str, Any]]) -> str:
        markets_text = ""
        for i, (snapshot, anomaly_result) in enumerate(items, 1):
            markets_text += f"\n--- MARKET {i} ---\n"
            markets_text += self._build_user_prompt(snapshot, anomaly_result)
            markets_text += "\n"

        # Gruppen-Hinweis wenn multiple Wahl-Märkte im Batch
        election_count = sum(
            1 for s, _ in items
            if 'election' in s.get('question', '').lower()
            or 'mayoral' in s.get('question', '').lower()
            or 'gubernatorial' in s.get('question', '').lower()
        )
        group_hint = ""
        if election_count > 1:
            group_hint = (
                f"\n⚠️ IMPORTANT: {election_count} election-related markets detected in this batch. "
                "Apply COMPETING CANDIDATES RULE — flag at most ONE candidate per election/event.\n"
            )

        return f"""Analyze these {len(items)} Polymarket markets for insider/anomalous activity.
Apply TIME HORIZON RULE, EXTREME PRICE RULE, and COMPETING CANDIDATES RULE to each market.
Respond with a JSON array of exactly {len(items)} objects, one per market, in the same order.
{group_hint}
{markets_text}

RESPOND WITH JSON ARRAY ONLY: [{{"anomaly_detected": ..., "confidence_score": ..., ...}}, ...]"""

    def _parse_and_validate(self, raw: str, expected_count: int = 1, snapshots: List[Dict] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Parse + validate JSON. Phase 15 additions:
          - yes_price < 0.01 + BUY_NO → override HOLD
          - yes_price > 0.99 + BUY_YES → override HOLD
          - confidence_score cap bei 0.95
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e} | raw: {raw[:200]}")
            return None

        if isinstance(parsed, dict):
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
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue

            # Standard field clamping
            if 'confidence_score' in item:
                item['confidence_score'] = max(0.0, min(0.95, float(item['confidence_score'])))
            if 'recommended_position_size_pct' in item:
                item['recommended_position_size_pct'] = max(0.0, min(0.15, float(item['recommended_position_size_pct'])))
            if 'holding_period_hours' in item:
                item['holding_period_hours'] = max(0, min(168, int(item['holding_period_hours'])))

            # Phase 15: Extreme Price Override
            yes_price = None
            if snapshots and idx < len(snapshots):
                yes_price = snapshots[idx].get('yes_price', 0.5)

            if yes_price is not None:
                trade = item.get('recommended_trade', 'HOLD')
                if yes_price < 0.01 and trade == 'BUY_NO':
                    logger.info(
                        f"🔧 Price override: BUY_NO → HOLD (yes_price={yes_price:.4f} < 0.01, "
                        f"payout economically meaningless)"
                    )
                    item['recommended_trade'] = 'HOLD'
                    item['recommended_position_size_pct'] = 0.0
                    item['holding_period_hours'] = 0
                    item.setdefault('counter_evidence', []).append(
                        f"Price override: yes_price={yes_price:.4f} — BUY_NO payout < $0.01 per dollar"
                    )
                elif yes_price > 0.99 and trade == 'BUY_YES':
                    logger.info(
                        f"🔧 Price override: BUY_YES → HOLD (yes_price={yes_price:.4f} > 0.99, no upside)"
                    )
                    item['recommended_trade'] = 'HOLD'
                    item['recommended_position_size_pct'] = 0.0
                    item['holding_period_hours'] = 0
                    item.setdefault('counter_evidence', []).append(
                        f"Price override: yes_price={yes_price:.4f} — BUY_YES has no upside remaining"
                    )

            validated.append(item)

        return validated

    def _apply_group_dedup(
        self,
        items: List[tuple],
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Phase 15: Gruppen-Dedup für konkurrierende Kandidaten-Märkte.

        Problem: La Paz Bürgermeisterwahl hat 3 Kandidaten-Märkte.
        Mistral kann trotz COMPETING CANDIDATES Regel alle 3 flaggen.
        Hier: Post-hoc-Fix. Pro (end_date_iso ± 1 Tag + gemeinsames Keyword)
        nur die MAX_SIGNALS_PER_GROUP stärksten Signals behalten.

        Alle anderen: anomaly_detected=True bleibt, aber recommended_trade=HOLD.
        """
        from collections import defaultdict

        # Gruppe bestimmen: (end_date_bucket, election_keyword)
        def get_group_key(snapshot: Dict) -> Optional[str]:
            question = snapshot.get('question', '').lower()
            end_date = snapshot.get('end_date_iso', '')

            # Election-Keywords als Gruppierungsmerkmal
            election_markers = [
                'mayoral', 'gubernatorial', 'la paz', 'santa cruz', 'cochabamba',
                'municipal election', 'mayoral election', 'runoff'
            ]
            matched_marker = next((m for m in election_markers if m in question), None)
            if not matched_marker:
                return None

            # End-Date auf Tages-Ebene bucketen (±0 Tage)
            try:
                date_bucket = end_date[:10]  # YYYY-MM-DD
            except Exception:
                date_bucket = 'unknown'

            return f"{date_bucket}::{matched_marker}"

        # Signals nach Gruppe sortieren
        groups: Dict[str, List[int]] = defaultdict(list)
        for i, (snapshot, _) in enumerate(items):
            key = get_group_key(snapshot)
            if key:
                groups[key].append(i)

        # Für jede Gruppe: nur MAX_SIGNALS_PER_GROUP behalten
        override_indices = set()
        for group_key, indices in groups.items():
            if len(indices) <= self.MAX_SIGNALS_PER_GROUP:
                continue

            # Flagged indices sortieren nach confidence_score (höchste zuerst)
            flagged = [
                i for i in indices
                if results[i].get('anomaly_detected') and
                results[i].get('recommended_trade') != 'HOLD'
            ]
            if len(flagged) <= self.MAX_SIGNALS_PER_GROUP:
                continue

            # Stärkste behalten, Rest auf HOLD setzen
            flagged_sorted = sorted(
                flagged,
                key=lambda i: results[i].get('confidence_score', 0),
                reverse=True
            )
            to_override = flagged_sorted[self.MAX_SIGNALS_PER_GROUP:]
            for i in to_override:
                override_indices.add(i)
                logger.info(
                    f"🔧 Group dedup override: HOLD ← "
                    f"{items[i][0].get('question', '')[:50]} "
                    f"(group={group_key}, conf={results[i].get('confidence_score', 0):.2f})"
                )

        # Overrides anwenden
        for i in override_indices:
            results[i]['recommended_trade'] = 'HOLD'
            results[i]['recommended_position_size_pct'] = 0.0
            results[i]['holding_period_hours'] = 0
            results[i].setdefault('counter_evidence', []).append(
                "Group dedup: competing candidate market — stronger signal exists in same group"
            )

        if override_indices:
            logger.info(
                f"📊 Group dedup: {len(override_indices)} signals overridden to HOLD"
            )

        return results

    def _rule_based_fallback(self, snapshot: Dict[str, Any], anomaly_result: Dict[str, Any]) -> Dict[str, Any]:
        score = anomaly_result.get('score', 0)
        vol = anomaly_result.get('breakdown', {}).get('volume_spike', {})
        spike_ratio = vol.get('spike_ratio', 1.0)
        yes_price = snapshot.get('yes_price', 0.5)
        topic = anomaly_result.get('breakdown', {}).get('topic_sensitivity', {})
        multiplier = topic.get('multiplier', 1.0)

        # Phase 15: days_to_close=0 → HOLD
        end_date = snapshot.get('end_date_iso', '')
        days_to_close = None
        try:
            if end_date:
                closes_at = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days
        except Exception:
            pass

        if days_to_close is not None and days_to_close <= 0:
            return {
                'anomaly_detected': spike_ratio >= 5.0,
                'confidence_score': 0.0,
                'anomaly_type': 'volume_spike' if spike_ratio >= 5.0 else 'none',
                'reasoning': f'Rule-based: market closes today (days={days_to_close}). Too late to trade.',
                'recommended_trade': 'HOLD',
                'recommended_position_size_pct': 0.0,
                'risk_level': 'high',
                'holding_period_hours': 0,
                'supporting_evidence': [f'Spike {spike_ratio:.1f}x'] if spike_ratio >= 5.0 else [],
                'counter_evidence': ['Market closes today — no actionable edge'],
                'source': 'rule_based_fallback'
            }

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

        # Phase 15: Extreme price check im Fallback
        if yes_price < 0.01:
            trade = 'HOLD'
        elif yes_price > 0.99:
            trade = 'HOLD'
        elif score >= 0.60 and spike_ratio >= 3.0:
            trade = 'BUY_YES' if yes_price < 0.70 else 'HOLD'
        else:
            trade = 'HOLD'

        confidence = min(score * 0.80, 0.70) if trade != 'HOLD' else 0.0
        anomaly_type = 'volume_spike' if score >= 0.60 else 'none'

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
            results = self._parse_and_validate(raw, expected_count=1, snapshots=[snapshot])

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
        """
        Analyze multiple markets using batched prompts.
        Phase 15: snapshots werden an _parse_and_validate übergeben für Price-Override.
        Danach: Gruppen-Dedup.
        """
        if not items:
            return []

        results = []
        batch_size = config.MISTRAL_BATCH_SIZE

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            batch_snapshots = [s for s, _ in batch]

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
                # Phase 15: snapshots mitgeben für Price-Override
                parsed = self._parse_and_validate(
                    raw,
                    expected_count=len(batch),
                    snapshots=batch_snapshots
                )

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

        # Phase 15: Gruppen-Dedup nach allen Batches
        if results:
            results = self._apply_group_dedup(items, results)

        return results

    def reset_cycle_counters(self):
        self.call_count = 0
