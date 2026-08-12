"""
PolyAugur Mistral Analyzer - LLM-powered Signal Validation
Phase 15: Blacklist-Mode compatible.

Sprint-0 additions:
- Observation-change metrics use actual elapsed time.
- Linearized hourly price change is explicitly not presented as a forecast.
- Untrusted LLM output is validated against the JSON-Schema contract before
  local safety overrides are applied.

Author: Diego Ringleb | Phase 15 | 2026-03-17
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mistralai import Mistral

import config
from src.llm_contract import LLMOutputContractError, parse_signal_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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

TIME HORIZON RULE:
- Markets closing in <= 0 days: ALWAYS set recommended_trade=HOLD.
- Markets closing in >365 days: NEVER flag. confidence_score<0.20, anomaly_detected=false.
- Markets closing in 90–365 days: Very unlikely insider. confidence_score<0.40 unless extreme evidence.
- Markets closing in <30 days: Can be insider-tradeable. Evaluate normally.
- Markets closing in <7 days: High temporal relevance.
- Markets closing in 1–3 days: If flagging, set holding_period_hours <= 24.

OBSERVATION METRICS RULE:
- Price-change metrics are measured between two actual polling observations.
- The elapsed time is supplied explicitly.
- "Price change linearly normalized to one hour" is only a mathematical scaling of the observed move.
- It is NOT an observed one-hour return and NOT a forecast.
- Do not treat a very large linearized value from a short interval as independent evidence without considering the actual elapsed time and raw observed price change.

EXTREME PRICE RULE:
- YES price < 0.01: Do NOT recommend BUY_NO. Set recommended_trade=HOLD.
- YES price > 0.99: Do NOT recommend BUY_YES. Set recommended_trade=HOLD.
- YES price < 0.03 OR > 0.97: Prefer HOLD unless evidence is unusually strong.

COMPETING CANDIDATES RULE:
- If multiple markets exist for the SAME election/event with DIFFERENT candidates/outcomes,
  do NOT recommend BUY_YES for more than ONE candidate.
- Identify the candidate with the strongest signal.
- For others set anomaly_detected=false or recommended_trade=HOLD.

IDEAL INSIDER SIGNAL TYPES:
- Military/geopolitical decisions with private advance knowledge
- Central-bank decisions or nominations
- Regulatory decisions
- Corporate M&A and executive changes
- Ceasefire or private diplomatic negotiations

NOT insider signals:
- Long-term election markets
- Sports outcomes
- Long-term crypto price targets
- Viral/social-media-driven spikes

WHALE INTELLIGENCE CONTEXT:
- whale_count >= 3 AND top_wallet_pct >= 40% can support a signal.
- directional_bias >= 85% with burst_score >= 3.0 can support coordinated activity.
- 0 whales with high volume may indicate retail activity.
- Whale data is SUPPORTING evidence, never sufficient on its own.

Few-shot Example 1:
Input: "Will Trump nominate Michelle Bowman as Fed chair?", volume 9x baseline, closes in 20 days
Output: {"anomaly_detected": true, "confidence_score": 0.87, "anomaly_type": "volume_spike", "reasoning": "Fed nomination plus unusual short-horizon volume activity.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.10, "risk_level": "medium", "holding_period_hours": 48, "supporting_evidence": ["9x volume spike", "Short time horizon"], "counter_evidence": ["Nominations can change"]}

Few-shot Example 2:
Input: "Will Nikki Haley win 2028 US Presidential Election?", volume 6x baseline, closes in 900 days
Output: {"anomaly_detected": false, "confidence_score": 0.08, "anomaly_type": "none", "reasoning": "Long horizon provides no specific evidence of informed activity.", "recommended_trade": "HOLD", "recommended_position_size_pct": 0.0, "risk_level": "low", "holding_period_hours": 0, "supporting_evidence": [], "counter_evidence": ["900 days to resolution"]}

Few-shot Example 3:
Input: "Will US conduct airstrike on Iran before March 15?", volume 36x baseline, closes in 7 days
Output: {"anomaly_detected": true, "confidence_score": 0.91, "anomaly_type": "smart_reversal", "reasoning": "Extreme short-horizon volume and price movement on a potentially information-sensitive event.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.08, "risk_level": "high", "holding_period_hours": 24, "supporting_evidence": ["36x volume spike", "7-day horizon"], "counter_evidence": ["Geopolitical markets have high false-positive risk"]}

Few-shot Example 4:
Input: "Will Russia-Ukraine ceasefire be announced before April?", volume 12x baseline, closes in 14 days, 5 whale trades, directional bias 91% BUY
Output: {"anomaly_detected": true, "confidence_score": 0.89, "anomaly_type": "coordinated_buying", "reasoning": "Short-horizon volume anomaly with concentrated directional activity.", "recommended_trade": "BUY_YES", "recommended_position_size_pct": 0.08, "risk_level": "high", "holding_period_hours": 36, "supporting_evidence": ["12x volume spike", "5 whale trades", "91% directional bias"], "counter_evidence": ["Negotiations can fail"]}

Few-shot Example 5:
Input: Three markets for the same mayoral election.
Rule: Only the strongest candidate signal may receive a non-HOLD recommendation.

Few-shot Example 6:
Input: "Will the Fed decrease rates after today's meeting?", volume 23x baseline, closes in 0 days
Output: {"anomaly_detected": true, "confidence_score": 0.91, "anomaly_type": "price_conviction", "reasoning": "Strong anomaly but market closes today; no actionable window remains.", "recommended_trade": "HOLD", "recommended_position_size_pct": 0.0, "risk_level": "high", "holding_period_hours": 0, "supporting_evidence": ["23x volume spike"], "counter_evidence": ["Market closes today"]}"""


class MistralAnalyzer:
    """
    Validate candidate anomaly signals using Mistral or the existing fallback.

    LLM scores remain model outputs. They are not interpreted here as
    calibrated probabilities.
    """

    MAX_SIGNALS_PER_GROUP = 2

    def __init__(self):
        if not config.MISTRAL_API_KEY:
            logger.warning("⚠️ MISTRAL_API_KEY not set – will use rule-based fallback")
            self.client = None
        else:
            self.client = Mistral(api_key=config.MISTRAL_API_KEY)

        self.call_count = 0
        self.error_count = 0

    @staticmethod
    def _format_optional_metric(
        value: Any,
        *,
        signed: bool = False,
        decimals: int = 4,
    ) -> str:
        """Format an optional numeric observation without inventing zeroes."""
        if value is None:
            return "N/A"

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "N/A"

        if signed:
            return f"{numeric:+.{decimals}f}"
        return f"{numeric:.{decimals}f}"

    def _build_whale_section(self, snapshot: Dict[str, Any]) -> str:
        whale_count = snapshot.get("whale_count", 0)
        suspicious = snapshot.get("trade_suspicious", False)

        if whale_count == 0 and not suspicious:
            return ""

        return f"""
WHALE INTELLIGENCE (from CLOB on-chain trades)
- Whale trades (>$5k): {whale_count}
- Whale volume %: {snapshot.get("whale_volume_pct", 0):.0%}
- Top wallet %: {snapshot.get("top_wallet_pct", 0):.0%}
- Unique wallets: {snapshot.get("unique_wallets", 0)}
- Directional bias: {snapshot.get("directional_bias", 0.5):.0%} {snapshot.get("dominant_side", "NONE")}
- Timing burst: {snapshot.get("burst_score", 1.0):.1f}x (last 1h vs avg)
- Suspicious: {suspicious}
"""

    def _build_user_prompt(
        self,
        snapshot: Dict[str, Any],
        anomaly_result: Dict[str, Any],
    ) -> str:
        breakdown = anomaly_result.get("breakdown", {})
        volume = breakdown.get("volume_spike", {})
        price = breakdown.get("price_anomaly", {})
        topic = breakdown.get("topic_sensitivity", {})

        end_date = snapshot.get("end_date_iso", "Unknown")
        days_to_close: Any = "Unknown"

        try:
            if end_date and end_date != "Unknown":
                closes_at = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days
        except (ValueError, TypeError):
            pass

        timing_warning = ""
        if isinstance(days_to_close, int):
            if days_to_close <= 0:
                timing_warning = (
                    "\n⚠️ WARNING: This market closes TODAY "
                    "or is already closed. Apply TIME HORIZON "
                    "RULE: recommended_trade MUST be HOLD."
                )
            elif days_to_close == 1:
                timing_warning = (
                    "\n⚠️ WARNING: Market closes TOMORROW. "
                    "If flagging, set holding_period_hours <= 12."
                )

        whale_section = self._build_whale_section(snapshot)

        yes_price = float(snapshot.get("yes_price", 0.5))
        price_warning = ""

        if yes_price < 0.01:
            price_warning = (
                "\n⚠️ WARNING: YES price < $0.01. recommended_trade MUST be HOLD for BUY_NO."
            )
        elif yes_price > 0.99:
            price_warning = (
                "\n⚠️ WARNING: YES price > $0.99. recommended_trade MUST be HOLD for BUY_YES."
            )
        elif yes_price < 0.03 or yes_price > 0.97:
            price_warning = (
                "\n⚠️ WARNING: "
                f"YES price={yes_price:.3f} is near-resolved. "
                "Be skeptical of an actionable recommendation."
            )

        price_change_text = self._format_optional_metric(
            snapshot.get("price_change_since_last_observation"),
            signed=True,
        )
        volume_change_text = self._format_optional_metric(
            snapshot.get("volume_24h_change_since_last_observation"),
            signed=True,
            decimals=2,
        )
        elapsed_text = self._format_optional_metric(
            snapshot.get("seconds_since_last_observation"),
            decimals=1,
        )
        hourly_change_text = self._format_optional_metric(
            snapshot.get("price_change_per_hour_linearized"),
            signed=True,
        )

        question = snapshot.get("question", "").lower()
        competing_rule = ""

        if "election" in question or "mayoral" in question:
            competing_rule = (
                " Apply COMPETING CANDIDATES RULE if this is "
                "one of multiple candidate markets for the same election."
            )

        whale_instruction = " Factor in WHALE INTELLIGENCE if provided." if whale_section else ""

        return f"""MARKET SNAPSHOT
Question: {snapshot.get("question", "Unknown")}
Description: {snapshot.get("description", "N/A")[:200]}
Closes in: {days_to_close} days ({end_date}){timing_warning}{price_warning}

PRICING & VOLUME
- YES Price: {yes_price:.3f} | NO Price: {snapshot.get("no_price", 0.5):.3f}
- Spread: {snapshot.get("spread", 0):.3f}
- 24h Volume: ${snapshot.get("volume_24hr", 0):,.0f}
- Liquidity: ${snapshot.get("liquidity", 0):,.0f}
- All-time Volume: ${snapshot.get("volume", 0):,.0f}

OBSERVATION CHANGE METRICS
- Price change since previous observation: {price_change_text}
- Rolling-24h volume change since previous observation: {volume_change_text}
- Elapsed time since previous observation: {elapsed_text} seconds
- Price change linearly normalized to one hour: {hourly_change_text}
  (normalization only; not a forecast or an observed one-hour move)

ANOMALY PRE-DETECTION
- Volume Spike Ratio: {volume.get("spike_ratio", 1.0):.2f}x baseline
- Volume Severity: {volume.get("severity", "none")}
- Price Indicators: {price.get("indicators", [])}
- Vol/Liquidity Ratio: {price.get("vol_liq_ratio", 0):.2f}x
- Topic Sensitivity: {topic.get("reasons", [])}
- Time Horizon Multiplier: {topic.get("multiplier", 1.0):.2f}
- Pre-screen Score: {anomaly_result.get("score", 0):.3f}
{whale_section}
QUESTION: Is this unusual activity likely (1) informed/insider trading, (2) retail hype, or (3) normal market activity?
Apply TIME HORIZON RULE first. Apply EXTREME PRICE RULE if relevant.{competing_rule}{whale_instruction}
RESPOND ONLY IN JSON FORMAT"""

    def _build_batch_prompt(
        self,
        items: List[
            tuple[
                Dict[str, Any],
                Dict[str, Any],
            ]
        ],
    ) -> str:
        markets_text = ""

        for index, (snapshot, anomaly_result) in enumerate(items, 1):
            markets_text += f"\n--- MARKET {index} ---\n"
            markets_text += self._build_user_prompt(
                snapshot,
                anomaly_result,
            )
            markets_text += "\n"

        election_count = sum(
            1
            for snapshot, _ in items
            if (
                "election" in snapshot.get("question", "").lower()
                or "mayoral" in snapshot.get("question", "").lower()
                or "gubernatorial" in snapshot.get("question", "").lower()
            )
        )

        group_hint = ""
        if election_count > 1:
            group_hint = (
                "\n⚠️ IMPORTANT: "
                f"{election_count} election-related markets detected in this batch. "
                "Apply COMPETING CANDIDATES RULE — flag at most ONE candidate "
                "per election/event.\n"
            )

        return f"""Analyze these {len(items)} Polymarket markets for insider/anomalous activity.
Apply TIME HORIZON RULE, EXTREME PRICE RULE, OBSERVATION METRICS RULE, and COMPETING CANDIDATES RULE to each market.
Respond with a JSON array of exactly {len(items)} objects, one per market, in the same order.
{group_hint}
{markets_text}

RESPOND WITH JSON ARRAY ONLY: [{{"anomaly_detected": ..., "confidence_score": ..., ...}}, ...]"""

    def _parse_and_validate(
        self,
        raw: str,
        expected_count: int = 1,
        snapshots: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Validate untrusted LLM output before applying local safety overrides.

        Invalid JSON, missing fields, wrong types, unsupported enum values,
        unexpected fields, or incorrect batch cardinality invalidate the
        complete LLM response.

        No model-produced value is coerced before schema validation.
        """
        try:
            validated = parse_signal_response(
                raw,
                expected_count=expected_count,
            )
        except LLMOutputContractError as exc:
            self.error_count += 1
            logger.error("Invalid Mistral output: %s", exc)
            return None

        for index, item in enumerate(validated):
            # Preserve the existing conservative confidence ceiling.
            item["confidence_score"] = min(
                0.95,
                item["confidence_score"],
            )

            yes_price = None
            if snapshots and index < len(snapshots):
                yes_price = snapshots[index].get(
                    "yes_price",
                    0.5,
                )

            if yes_price is None:
                continue

            try:
                yes_price = float(yes_price)
            except (TypeError, ValueError):
                self.error_count += 1
                logger.error(
                    "Invalid yes_price for Mistral safety override: %r",
                    yes_price,
                )
                return None

            trade = item["recommended_trade"]

            if yes_price < 0.01 and trade == "BUY_NO":
                logger.info(
                    "Price override: BUY_NO -> HOLD (yes_price=%.4f)",
                    yes_price,
                )
                item["recommended_trade"] = "HOLD"
                item["recommended_position_size_pct"] = 0.0
                item["holding_period_hours"] = 0
                item["counter_evidence"].append(
                    f"Price override: yes_price={yes_price:.4f} — BUY_NO payout < $0.01 per dollar"
                )

            elif yes_price > 0.99 and trade == "BUY_YES":
                logger.info(
                    "Price override: BUY_YES -> HOLD (yes_price=%.4f)",
                    yes_price,
                )
                item["recommended_trade"] = "HOLD"
                item["recommended_position_size_pct"] = 0.0
                item["holding_period_hours"] = 0
                item["counter_evidence"].append(
                    f"Price override: yes_price={yes_price:.4f} — BUY_YES has no upside remaining"
                )

        return validated

    def _apply_group_dedup(
        self,
        items: List[
            tuple[
                Dict[str, Any],
                Dict[str, Any],
            ]
        ],
        results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Apply the existing election-candidate post-processing guard."""

        def get_group_key(
            snapshot: Dict[str, Any],
        ) -> Optional[str]:
            question = snapshot.get("question", "").lower()
            end_date = snapshot.get("end_date_iso", "")

            election_markers = [
                "mayoral",
                "gubernatorial",
                "la paz",
                "santa cruz",
                "cochabamba",
                "municipal election",
                "mayoral election",
                "runoff",
            ]

            matched_marker = next(
                (marker for marker in election_markers if marker in question),
                None,
            )

            if not matched_marker:
                return None

            if isinstance(end_date, str):
                date_bucket = end_date[:10] or "unknown"
            else:
                date_bucket = "unknown"

            return f"{date_bucket}::{matched_marker}"

        groups: Dict[str, List[int]] = defaultdict(list)

        for index, (snapshot, _) in enumerate(items):
            key = get_group_key(snapshot)
            if key:
                groups[key].append(index)

        override_indices = set()

        for group_key, indices in groups.items():
            if len(indices) <= self.MAX_SIGNALS_PER_GROUP:
                continue

            flagged = [
                index
                for index in indices
                if (
                    results[index].get("anomaly_detected")
                    and results[index].get("recommended_trade") != "HOLD"
                )
            ]

            if len(flagged) <= self.MAX_SIGNALS_PER_GROUP:
                continue

            flagged_sorted = sorted(
                flagged,
                key=lambda index: results[index].get(
                    "confidence_score",
                    0,
                ),
                reverse=True,
            )

            for index in flagged_sorted[self.MAX_SIGNALS_PER_GROUP :]:
                override_indices.add(index)
                logger.info(
                    "🔧 Group dedup override: HOLD ← %s (group=%s, conf=%.2f)",
                    items[index][0].get("question", "")[:50],
                    group_key,
                    results[index].get("confidence_score", 0),
                )

        for index in override_indices:
            results[index]["recommended_trade"] = "HOLD"
            results[index]["recommended_position_size_pct"] = 0.0
            results[index]["holding_period_hours"] = 0
            results[index].setdefault(
                "counter_evidence",
                [],
            ).append(
                "Group dedup: competing candidate market — stronger signal exists in same group"
            )

        if override_indices:
            logger.info(
                "📊 Group dedup: %s signals overridden to HOLD",
                len(override_indices),
            )

        return results

    def _rule_based_fallback(
        self,
        snapshot: Dict[str, Any],
        anomaly_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        score = anomaly_result.get("score", 0)
        volume = anomaly_result.get(
            "breakdown",
            {},
        ).get(
            "volume_spike",
            {},
        )
        spike_ratio = volume.get("spike_ratio", 1.0)
        yes_price = snapshot.get("yes_price", 0.5)
        topic = anomaly_result.get(
            "breakdown",
            {},
        ).get(
            "topic_sensitivity",
            {},
        )
        multiplier = topic.get("multiplier", 1.0)

        end_date = snapshot.get("end_date_iso", "")
        days_to_close = None

        try:
            if end_date:
                closes_at = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
                days_to_close = (closes_at - datetime.now(timezone.utc)).days
        except (ValueError, TypeError):
            pass

        if days_to_close is not None and days_to_close <= 0:
            return {
                "anomaly_detected": spike_ratio >= 5.0,
                "confidence_score": 0.0,
                "anomaly_type": ("volume_spike" if spike_ratio >= 5.0 else "none"),
                "reasoning": (
                    f"Rule-based: market closes today (days={days_to_close}). Too late to trade."
                ),
                "recommended_trade": "HOLD",
                "recommended_position_size_pct": 0.0,
                "risk_level": "high",
                "holding_period_hours": 0,
                "supporting_evidence": (
                    [f"Spike {spike_ratio:.1f}x"] if spike_ratio >= 5.0 else []
                ),
                "counter_evidence": ["Market closes today — no actionable edge"],
                "source": "rule_based_fallback",
            }

        if multiplier < 0.5 or score < 0.50:
            return {
                "anomaly_detected": False,
                "confidence_score": 0.0,
                "anomaly_type": "none",
                "reasoning": (f"Rule-based: time_multiplier={multiplier:.2f} or score too low"),
                "recommended_trade": "HOLD",
                "recommended_position_size_pct": 0.0,
                "risk_level": "low",
                "holding_period_hours": 0,
                "supporting_evidence": [],
                "counter_evidence": ["Long time horizon or insufficient signal"],
                "source": "rule_based_fallback",
            }

        if yes_price < 0.01:
            trade = "HOLD"
        elif yes_price > 0.99:
            trade = "HOLD"
        elif score >= 0.60 and spike_ratio >= 3.0:
            trade = "BUY_YES" if yes_price < 0.70 else "HOLD"
        else:
            trade = "HOLD"

        confidence = min(score * 0.80, 0.70) if trade != "HOLD" else 0.0

        anomaly_type = "volume_spike" if score >= 0.60 else "none"

        return {
            "anomaly_detected": score >= 0.60,
            "confidence_score": round(confidence, 3),
            "anomaly_type": anomaly_type,
            "reasoning": (f"Rule-based fallback: score={score:.2f}, spike={spike_ratio:.1f}x"),
            "recommended_trade": trade,
            "recommended_position_size_pct": (0.05 if trade != "HOLD" else 0.0),
            "risk_level": ("high" if score >= 0.70 else "medium"),
            "holding_period_hours": 6,
            "supporting_evidence": [f"Volume spike {spike_ratio:.1f}x baseline"],
            "counter_evidence": ["No LLM validation available"],
            "source": "rule_based_fallback",
        }

    def analyze_single(
        self,
        snapshot: Dict[str, Any],
        anomaly_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self.client:
            return self._rule_based_fallback(
                snapshot,
                anomaly_result,
            )

        if self.call_count >= config.MAX_MISTRAL_CALLS_PER_CYCLE:
            logger.warning(
                "Mistral call budget (%s) exhausted",
                config.MAX_MISTRAL_CALLS_PER_CYCLE,
            )
            return self._rule_based_fallback(
                snapshot,
                anomaly_result,
            )

        prompt = self._build_user_prompt(
            snapshot,
            anomaly_result,
        )

        try:
            response = self.client.chat.complete(
                model=config.MISTRAL_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                response_format={
                    "type": "json_object",
                },
                temperature=0.1,
                max_tokens=512,
            )

            self.call_count += 1
            raw = response.choices[0].message.content

            results = self._parse_and_validate(
                raw,
                expected_count=1,
                snapshots=[snapshot],
            )

            if not results:
                return self._rule_based_fallback(
                    snapshot,
                    anomaly_result,
                )

            signal = results[0]
            signal["source"] = "mistral"
            signal["market_id"] = snapshot.get("id")
            signal["question"] = snapshot.get(
                "question",
                "Unknown",
            )
            signal["timestamp"] = datetime.now(timezone.utc).isoformat()

            logger.info(
                "🧠 Mistral: %s | Anomaly=%s | Confidence=%.2f | Trade=%s",
                snapshot.get("question", "")[:50],
                signal.get("anomaly_detected"),
                signal.get("confidence_score", 0),
                signal.get("recommended_trade"),
            )

            return signal

        except Exception as exc:
            self.error_count += 1
            logger.error(
                "Mistral API error: %s",
                exc,
            )
            return self._rule_based_fallback(
                snapshot,
                anomaly_result,
            )

    def analyze_batch(
        self,
        items: List[
            tuple[
                Dict[str, Any],
                Dict[str, Any],
            ]
        ],
    ) -> List[Dict[str, Any]]:
        """Analyze multiple markets using batched prompts."""
        if not items:
            return []

        results: List[Dict[str, Any]] = []
        batch_size = config.MISTRAL_BATCH_SIZE

        for start in range(
            0,
            len(items),
            batch_size,
        ):
            batch = items[start : start + batch_size]
            batch_snapshots = [snapshot for snapshot, _ in batch]

            if self.call_count >= config.MAX_MISTRAL_CALLS_PER_CYCLE:
                logger.warning("Mistral budget exhausted, using fallback for remaining")

                for snapshot, anomaly_result in batch:
                    results.append(
                        self._rule_based_fallback(
                            snapshot,
                            anomaly_result,
                        )
                    )
                continue

            if len(batch) == 1:
                results.append(
                    self.analyze_single(
                        batch[0][0],
                        batch[0][1],
                    )
                )
                continue

            if not self.client:
                for snapshot, anomaly_result in batch:
                    results.append(
                        self._rule_based_fallback(
                            snapshot,
                            anomaly_result,
                        )
                    )
                continue

            try:
                prompt = self._build_batch_prompt(batch)

                response = self.client.chat.complete(
                    model=config.MISTRAL_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    response_format={
                        "type": "json_object",
                    },
                    temperature=0.1,
                    max_tokens=1024,
                )

                self.call_count += 1
                raw = response.choices[0].message.content

                parsed = self._parse_and_validate(
                    raw,
                    expected_count=len(batch),
                    snapshots=batch_snapshots,
                )

                if parsed and len(parsed) == len(batch):
                    for index, signal in enumerate(parsed):
                        snapshot = batch[index][0]
                        signal["source"] = "mistral_batch"
                        signal["market_id"] = snapshot.get("id")
                        signal["question"] = snapshot.get("question")
                        signal["timestamp"] = datetime.now(timezone.utc).isoformat()
                        results.append(signal)

                    logger.info(
                        "🧠 Mistral batch: %s markets analyzed in 1 call",
                        len(batch),
                    )

                else:
                    logger.warning("Batch parse mismatch, falling back")

                    for snapshot, anomaly_result in batch:
                        results.append(
                            self._rule_based_fallback(
                                snapshot,
                                anomaly_result,
                            )
                        )

            except Exception as exc:
                self.error_count += 1
                logger.error(
                    "Batch Mistral error: %s",
                    exc,
                )

                for snapshot, anomaly_result in batch:
                    results.append(
                        self._rule_based_fallback(
                            snapshot,
                            anomaly_result,
                        )
                    )

        if results:
            results = self._apply_group_dedup(
                items,
                results,
            )

        return results

    def reset_cycle_counters(self):
        self.call_count = 0
        self.error_count = 0
