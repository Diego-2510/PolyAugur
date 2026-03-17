"""
PolyAugur Anomaly Detector - Multi-Layer Insider Signal Detection
Phase 14: Research-based insider taxonomy with explicit exclusions.

Insider taxonomy (from real Polymarket cases):
  CONFIRMED real insider cases:
    - Military operations (Venezuela $400k, Iran $1.2M, Iran supreme leader $553k)
    - FOMC/Fed decisions (members know hours before)
    - FDA drug approvals (panel members, pharma execs)
    - Tech company internals (OpenAI employee fired, Google Year in Search $1M)
    - Government personnel decisions (cabinet firings, Kristi Noem case)
    - M&A / CEO changes (bankers, board members)
    - Intelligence-based geopolitical (Israeli operatives charged for Polymarket bets)
    - Elections with fraud (Bolivian local elections, short-horizon)

  EXPLICITLY EXCLUDED (no insider edge possible):
    - Tweet/post count predictions (publicly countable)
    - Crypto price predictions (no single person knows)
    - Weather markets
    - Sports outcomes
    - Entertainment awards
    - Long-horizon generic elections (>90 days, no fraud signal)

Design: EXCLUSION_KEYWORDS applied first — any match → multiplier = 0.0
        Then CRITICAL (×1.40) and ELEVATED (×1.15) tiers.

Author: Diego Ringleb | Phase 14 | 2026-03-17
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

    Phase 14 changes vs Phase 12.2:
    - Explicit EXCLUSION_KEYWORDS applied before any boost
      (tweets, post counts, crypto prices, weather → multiplier = 0.0)
    - Volume scoring retiered: 2.5x→0.08, 3.5x→0.18, 5x→0.27, 8x→0.35
      (prevents automatic base_score = 0.60 for every pre-filtered market)
    - CRITICAL keywords tightened: removed 'nominate'/'appoint' standalone,
      added explicit government personnel + intelligence patterns
    - Elections split: short-horizon (<30d) in fraud-risk regions = ELEVATED,
      long-horizon generic = no boost
    - Elon Musk tweet counts, crypto prices explicitly excluded
    Target: 0–3 ultra-precise signals per cycle.
    """

    # ── Explicit exclusions: NO insider advantage possible ────────────────
    # Applied FIRST. Any match → multiplier forced to 0.0.
    EXCLUSION_KEYWORDS = [
        # Countable public activity — no insider edge
        'tweet', 'tweets', 'post ', 'posts ', 'how many times',
        'how often', 'retweet', 'followers', 'subscribers',
        # Crypto price movements — determined by market, no single insider
        'bitcoin', 'ethereum', 'crypto price', 'btc price', 'eth price',
        'will btc', 'will eth', 'reach $', 'above $', 'below $',
        'price of bitcoin', 'price of ethereum',
        # Crypto market cap rankings — algorithmic, no insider
        'market cap rank', 'flippening',
        # Weather / nature — no insider
        'rain', 'hurricane', 'earthquake', 'wildfire', 'flood', 'blizzard',
        'temperature', 'snow in',
        # Entertainment — no insider (unless Oscar voting leaks, but not meaningful)
        'oscar', 'grammy', 'emmy', 'golden globe', 'nobel prize literature',
        'box office', 'streaming views', 'album sales',
        # Sports — already filtered by data_fetcher but double-guard
        'super bowl', 'world series', 'nba finals', 'stanley cup',
        # Generic sentiment / polling — no actionable insider
        'approval rating', 'poll shows', 'favorability',
        # Long-range crypto ecosystem (no regulatory insider needed)
        'will solana', 'will bnb', 'will xrp', 'will dogecoin', 'will shiba',
        'memecoin', 'nft ',
    ]

    def __init__(self):
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        logger.info(f"🔍 AnomalyDetector initialized (threshold: {self.confidence_threshold})")

    # ==================== LAYER 1: VOLUME SPIKE ====================

    def detect_volume_spike(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect unusual volume relative to THIS market's own baseline.

        Phase 14 retier — prevents automatic max score for every 2.5x spike:
          >= 8.0x → 0.35 (critical — very rare, genuine anomaly)
          >= 5.0x → 0.27 (high — strong signal)
          >= 3.5x → 0.18 (moderate-high)
          >= 2.5x → 0.08 (moderate — entry threshold from elite pre-filter)
          <  2.5x → 0.00 (pre-filter should have caught this)
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

        if spike_ratio >= 8.0:
            score = 0.35
            severity = 'critical'
        elif spike_ratio >= 5.0:
            score = 0.27
            severity = 'high'
        elif spike_ratio >= 3.5:
            score = 0.18
            severity = 'moderate_high'
        elif spike_ratio >= 2.5:
            score = 0.08
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
        Unchanged from Phase 12.2 — logic is sound.
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
        Phase 14: Research-based insider taxonomy.

        Step 0 — EXCLUSION CHECK:
          If any exclusion keyword matches → multiplier = 0.0 immediately.
          (tweet counts, crypto prices, weather, entertainment)

        Step 1 — TIME HORIZON:
          > 365d → ×0.30 | > 180d → ×0.55 | > 90d → ×0.80
          ≤ 14d  → ×1.25 | ≤ 30d  → ×1.10

        Step 2 — CRITICAL topics (×1.40):
          Confirmed real Polymarket insider categories:
          Military operations, Fed/FOMC, FDA approvals,
          Executive appointments (by POTUS specifically),
          Corporate M&A, CEO changes, government personnel firings,
          intelligence/espionage operations.

        Step 3 — ELEVATED topics (×1.15):
          Plausible insider advantage:
          Ceasefire/peace deals, sanctions, indictments/arrests,
          tech regulation, OPEC, antitrust, short-horizon elections
          in fraud-risk contexts (<30d), government shutdowns.

        Step 4 — RECENCY SURGE:
          > 60% of all-time volume in last 24h → ×1.40
          > 35% → ×1.15

        Returns multiplier 0.0–1.80.
        """
        question = snapshot.get('question', '').lower()
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        volume_total = float(snapshot.get('volume', volume_24hr))
        end_date_iso = snapshot.get('end_date_iso', '')

        multiplier = 1.0
        reasons = []

        # ── Step 0: EXCLUSION CHECK (applied before everything else) ────────
        # Any exclusion match → this market has no insider potential → kill it.
        exclusion_matched = [kw for kw in self.EXCLUSION_KEYWORDS if kw in question]
        if exclusion_matched:
            return {
                'multiplier': 0.0,
                'reasons': [f"excluded:{exclusion_matched[0].strip()}"],
                'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0,
                'excluded': True
            }

        # ── Step 1: Time Horizon ─────────────────────────────────────────────
        days_to_close = None
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

        # ── Step 2: CRITICAL insider topics (×1.40) ──────────────────────────
        #
        # Rule: A SPECIFIC named person or institution with EXCLUSIVE
        # pre-announcement knowledge determines the outcome.
        # Source: Real Polymarket insider cases (Forbes, Atlantic, NPR, Wired)
        #
        critical_topics = [
            # ── Military / Intelligence (Pentagon, NSC, CIA, IDF) ─────────
            # Cases: Venezuela $400k, Iran $1.2M, Iranian airstrike $553k
            'airstrike', 'air strike', 'missile strike',
            'military operation', 'military action', 'military strike',
            'troops deploy', 'troop withdrawal', 'invasion',
            'declare war', 'declaration of war',
            'nuclear launch', 'nuclear strike', 'nuclear test',
            'covert operation', 'special forces',
            'targeted killing', 'drone strike',
            'attack on ', 'strike on ',

            # ── Federal Reserve / Central Banks (FOMC members) ────────────
            # Cases: Rate decisions known by Fed governors before announcement
            'federal reserve', 'fed rate', 'fomc',
            'rate cut', 'rate hike', 'rate pause',
            'interest rate decision', 'emergency rate',
            'powell announce', 'fed chair',
            'basis point', 'bps cut', 'bps hike',
            'ecb rate', 'bank of england rate', 'boe rate',

            # ── FDA / Drug Approvals (panel members, pharma insiders) ─────
            # Cases: FDA panelists, senior pharma executives
            'fda approv', 'fda reject', 'fda decision',
            'drug approv', 'drug reject',
            'emergency use authorization',
            'breakthrough therapy', 'accelerated approval',
            'clinical trial result', 'phase 3 result',
            'vaccine approv', 'vaccine authoriz',

            # ── Executive / Presidential Orders (POTUS, senior staff) ─────
            # Cases: Cabinet firings (Noem), pardons, executive orders
            'executive order', 'presidential order',
            'pardon ', 'commute sentence', 'clemency',
            'fired by trump', 'fired by president', 'removed by president',
            'cabinet fired', 'secretary fired', 'director fired',
            'appointed by president', 'nominated by president',
            'resign from cabinet', 'step down from cabinet',

            # ── Corporate M&A / CEO (board members, i-bankers) ───────────
            # Cases: Classic insider trading category, now on prediction markets
            'merger', 'acquisition', 'takeover bid', 'buyout',
            'hostile takeover', 'leveraged buyout',
            'ceo resign', 'ceo fired', 'ceo step', 'ceo replace',
            'chief executive resign', 'chief executive fired',
            'board of directors', 'shareholder vote',
            'ipo price', 'ipo date',

            # ── SEC / Regulatory Approvals (commissioners, staff) ─────────
            # Cases: ETF approvals, enforcement actions
            'sec approv', 'sec reject', 'sec ruling',
            'etf approv', 'etf reject', 'etf decision',
            'sec enforcement', 'sec charges',
            'cftc ruling', 'cftc approv',

            # ── Tech Company Internals (employees, contractors) ───────────
            # Cases: OpenAI employee fired, Google Year in Search $1M
            'openai', 'google search trend', 'year in search',
            'google announce', 'apple announce', 'meta announce',
            'microsoft announce',
            'gpt-5', 'gpt-6', 'gemini ultra',
            'product launch', 'new model release',
        ]

        # ── Step 3: ELEVATED insider topics (×1.15) ───────────────────────
        #
        # Rule: Insider advantage is PLAUSIBLE — diplomats, prosecutors,
        # intelligence analysts, election officials, energy ministers.
        # Short-horizon elections included: fraud/early count access possible.
        #
        elevated_topics = [
            # ── Diplomatic / Peace negotiations (mediators, diplomats) ────
            'ceasefire', 'peace deal', 'peace agreement', 'peace talks',
            'treaty sign', 'diplomatic agreement',
            'hostage deal', 'hostage release',
            'nato summit', 'g7 ', 'g20 ',

            # ── Sanctions / Trade policy (trade reps, lobbyists) ─────────
            'sanction', 'trade deal', 'trade agreement',
            'tariff on ', 'tariff announ', 'trade war',
            'export ban', 'import ban',

            # ── Legal / DOJ / Prosecution (prosecutors, grand jury) ───────
            'indictment', 'grand jury', 'arraignment',
            'doj charge', 'doj indict', 'criminal charge',
            'arrest warrant', 'extradition',
            'impeachment', 'articles of impeachment',
            'conviction', 'guilty verdict', 'acquittal',

            # ── Government operations (congressional leadership) ──────────
            'government shutdown', 'debt ceiling', 'continuing resolution',
            'budget deal', 'spending bill',
            'default on debt', 'treasury default',

            # ── Geopolitical flashpoints (intelligence agencies) ──────────
            'north korea', 'taiwan strait', 'south china sea',
            'nato article 5', 'nato deploy',
            'coup attempt', 'coup succeed',
            'regime change', 'government collapse',

            # ── OPEC / Energy policy (ministers, insiders) ───────────────
            'opec', 'opec+', 'oil production cut', 'oil production increase',
            'drilling ban', 'pipeline approv', 'pipeline reject',
            'lng export', 'natural gas pipeline',

            # ── Tech regulation / antitrust (investigators, staffers) ─────
            'antitrust lawsuit', 'antitrust ruling',
            'break up ', 'forced divestiture',
            'ban tiktok', 'tiktok ban',
            'crypto regulation', 'crypto ban', 'stablecoin bill',
            'crypto bill', 'digital asset law',

            # ── Elections: SHORT horizon only (<= 35 days)  ──────────────
            # Fraud/early count: election officials, party insiders can know.
            # BUT only if market closes within 35 days (not generic long-range).
            # Longer elections checked separately with days_to_close guard below.
            'mayoral election', 'gubernatorial election',
            'special election', 'by-election', 'snap election',
            'runoff election', 'runoff vote',
            'recall election', 'recall vote',
            'referendum vote', 'ballot measure',
            'vote count', 'election result',
        ]

        critical_matched = [t for t in critical_topics if t in question]
        elevated_matched = [t for t in elevated_topics if t in question]

        if critical_matched:
            multiplier *= 1.40
            reasons.append(f"critical_insider:{critical_matched[0].strip()}")

        elif elevated_matched:
            # Extra guard for elections: only boost if closing soon
            # Long-horizon elections (>35d) get no elevated boost —
            # insider advantage evaporates over time.
            election_keywords = [
                'mayoral election', 'gubernatorial election', 'special election',
                'by-election', 'snap election', 'runoff election', 'runoff vote',
                'recall election', 'recall vote', 'vote count', 'election result'
            ]
            is_election_keyword = any(kw in question for kw in election_keywords)

            if is_election_keyword and days_to_close is not None and days_to_close > 35:
                # Long-horizon election → no boost (fraud risk too diffuse)
                reasons.append(f"election_too_far:{days_to_close}d_no_boost")
            else:
                multiplier *= 1.15
                reasons.append(f"elevated_insider:{elevated_matched[0].strip()}")

        # ── Step 4: Sudden volume surge ──────────────────────────────────────
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
            'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0,
            'excluded': False
        }

    # ==================== AGGREGATION ====================

    def detect_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main detection pipeline: Aggregate all layers → final score.

        Score = (volume + price + holder) × topic_time_multiplier

        Special case: if topic_sensitivity returns multiplier = 0.0
        (exclusion matched), score is forced to 0.0 regardless of
        volume/price signals.

        Threshold: score >= CONFIDENCE_THRESHOLD → flag for Mistral
        """
        try:
            volume_result = self.detect_volume_spike(snapshot)
            price_result  = self.detect_price_anomaly(snapshot)
            holder_result = self.detect_holder_anomalies(snapshot)
            topic_result  = self.calculate_topic_sensitivity(snapshot)

            base_score = (
                volume_result['score'] +
                price_result['score'] +
                holder_result['score']
            )

            # Exclusion: multiplier = 0.0 kills the signal completely
            final_score = round(base_score * topic_result['multiplier'], 3)
            anomaly_detected = final_score >= self.confidence_threshold

            result = {
                'anomaly_detected': anomaly_detected,
                'score': final_score,
                'base_score': round(base_score, 3),
                'topic_multiplier': topic_result['multiplier'],
                'ready_for_mistral': anomaly_detected,

                'breakdown': {
                    'volume_spike':      volume_result,
                    'price_anomaly':     price_result,
                    'holder_behavior':   holder_result,
                    'topic_sensitivity': topic_result
                },

                'market_id':   snapshot.get('id'),
                'question':    snapshot.get('question', 'Unknown'),
                'volume_24hr': snapshot.get('volume_24hr', 0),
                'timestamp':   datetime.now(timezone.utc).isoformat()
            }

            if anomaly_detected:
                logger.info(
                    f"🚨 ANOMALY: {snapshot.get('question', '')[:55]} | "
                    f"Score: {final_score:.3f} "
                    f"(base: {base_score:.2f} × {topic_result['multiplier']:.2f}) | "
                    f"Vol: ${snapshot.get('volume_24hr', 0):,.0f} | "
                    f"Reasons: {topic_result['reasons']}"
                )
            elif topic_result.get('excluded'):
                logger.debug(
                    f"🚫 Excluded: {snapshot.get('question', '')[:55]} | "
                    f"Reason: {topic_result['reasons']}"
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

        detected_count  = sum(1 for r in results if r.get('anomaly_detected'))
        excluded_count  = sum(1 for r in results if r.get('breakdown', {})
                              .get('topic_sensitivity', {}).get('excluded', False))

        logger.info(
            f"📊 Batch: {len(results)} markets analyzed | "
            f"{detected_count} anomalies ({detected_count/max(len(results),1)*100:.0f}%) | "
            f"{excluded_count} excluded by keyword"
        )
        return results


# ==================== TEST SUITE ====================

def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Anomaly Detector Test - Phase 14 (Precision Insider)")
    logger.info("=" * 60)

    from datetime import timedelta
    detector = AnomalyDetector()
    now = datetime.now(timezone.utc)

    def snap(id_, question, spike=5.0, days=14, recency=0.65, vol=150_000, liq=80_000, price=0.30):
        baseline = vol / spike
        return {
            'id': id_, 'question': question,
            'volume_24hr': vol, 'current_volume': vol, 'baseline': baseline,
            'yes_price': price, 'no_price': 1 - price, 'spread': abs(price - (1 - price)),
            'liquidity': liq, 'volume': vol / recency if recency > 0 else vol * 2,
            'holders': [], 'end_date_iso': (now + timedelta(days=days)).isoformat()
        }

    tests = [
        # ── Should NOT flag (exclusions) ────────────────────────────────
        ("❌ Elon tweets 1120-1159",
         snap('e1', 'Will Elon Musk post 1120-1159 tweets in March 2026?'),
         False),
        ("❌ Bitcoin above $100k",
         snap('e2', 'Will Bitcoin be above $100,000 on March 31?'),
         False),
        ("❌ Weather: rain in NYC",
         snap('e3', 'Will it rain in New York City this weekend?', spike=3.0),
         False),
        ("❌ Oscar Best Picture",
         snap('e4', 'Will Dune win the Oscar for Best Picture?', spike=3.0),
         False),

        # ── Should NOT flag (no insider, no exclusion) ───────────────────
        ("❌ 2028 US Election (far away)",
         snap('e5', 'Will Trump win the 2028 US Presidential Election?',
              spike=4.0, days=900, recency=0.10),
         False),
        ("❌ Generic protest",
         snap('e6', 'Will there be protests in Washington DC this weekend?',
              spike=3.0, days=3, recency=0.50),
         False),

        # ── Should FLAG (critical) ────────────────────────────────────────
        ("✅ US airstrike on Iran (5d)",
         snap('c1', 'Will the US conduct an airstrike on Iran before March 22?',
              spike=8.0, days=5, recency=0.80, vol=180_000, liq=60_000),
         True),
        ("✅ Fed emergency rate cut (7d)",
         snap('c2', 'Will the Fed announce an emergency rate cut this week?',
              spike=6.0, days=7, recency=0.70, vol=200_000, liq=90_000),
         True),
        ("✅ FOMC rate decision (3d)",
         snap('c3', 'Will the FOMC cut rates by 50bps at this meeting?',
              spike=5.0, days=3, recency=0.65, vol=150_000, liq=70_000),
         True),
        ("✅ FDA drug approval (10d)",
         snap('c4', 'Will the FDA approve Pfizer\'s new cancer drug this week?',
              spike=5.5, days=10, recency=0.72, vol=120_000, liq=50_000),
         True),
        ("✅ CEO fired (7d)",
         snap('c5', 'Will the CEO step down from the company this week?',
              spike=7.0, days=7, recency=0.75, vol=200_000, liq=80_000),
         True),
        ("✅ OpenAI product launch (5d)",
         snap('c6', 'Will OpenAI announce a new model release this week?',
              spike=6.0, days=5, recency=0.70, vol=160_000, liq=75_000),
         True),

        # ── Should FLAG (elevated) ────────────────────────────────────────
        ("✅ Ceasefire deal (14d)",
         snap('v1', 'Will Russia and Ukraine sign a ceasefire agreement by April 1?',
              spike=4.0, days=14, recency=0.55, vol=350_000, liq=200_000),
         True),
        ("✅ DOJ indictment (10d)",
         snap('v2', 'Will the DOJ indict Trump ally this week?',
              spike=4.5, days=10, recency=0.60, vol=180_000, liq=100_000),
         True),
        ("✅ Bolivian mayoral election (12d)",
         snap('v3', 'Will Iván Arias win the 2026 La Paz mayoral election?',
              spike=3.8, days=12, recency=0.50, vol=140_000, liq=80_000),
         True),
        ("❌ Bolivian mayoral election (60d) — too far",
         snap('v4', 'Will Waldo win the 2026 La Paz mayoral election?',
              spike=4.0, days=60, recency=0.40, vol=140_000, liq=80_000),
         False),
        ("✅ OPEC production cut (7d)",
         snap('v5', 'Will OPEC+ announce an oil production cut this weekend?',
              spike=5.0, days=7, recency=0.68, vol=160_000, liq=90_000),
         True),
    ]

    passed = 0
    failed = 0
    print(f"\n{'Test':<45} {'Expected':<10} {'Got':<8} {'Score':<8} {'Mult':<6} {'Status'}")
    print("-" * 100)

    for label, snapshot, expected in tests:
        r = detector.detect_anomaly(snapshot)
        got = r['anomaly_detected']
        ok  = got == expected
        if ok:
            passed += 1
        else:
            failed += 1
        status = "✅ PASS" if ok else "❌ FAIL"
        reasons = r['breakdown']['topic_sensitivity']['reasons']
        print(
            f"{label:<45} {str(expected):<10} {str(got):<8} "
            f"{r['score']:<8.3f} {r['topic_multiplier']:<6} "
            f"{status}  {reasons}"
        )

    print(f"\n{'='*60}")
    print(f"Results: {passed}/{passed+failed} passed | {failed} failed")
    if failed == 0:
        print("✅ Phase 14 Anomaly Detector: ALL TESTS PASSED")
    else:
        print("❌ Phase 14 Anomaly Detector: FAILURES DETECTED — review above")
    print("=" * 60)


if __name__ == "__main__":
    main()
