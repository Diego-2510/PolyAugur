"""
PolyAugur Anomaly Detector - Multi-Layer Insider Signal Detection
Phase 15: Blacklist-Modus — alle Märkte außer explizit ausgeschlossene.

Design-Änderung Phase 15:
  VORHER (Phase 14): Topic-Keywords als Hard-Gate (Whitelist)
    → Nur CRITICAL/ELEVATED Topics kamen zu Mistral
    → Problem: Fed-Titel mit Unicode-Dashes fielen raus
    → Problem: Unbekannte Insider-Kategorien wurden nie erkannt

  JETZT  (Phase 15): Topic-Keywords als Score-Booster (Blacklist)
    → Alle Märkte mit ausreichendem Spike/Score kommen zu Mistral
    → EXCLUSION_KEYWORDS als einziger Hard-Ausschluss
    → Topic-Multiplier erhöht Score für bekannte Insider-Kategorien
      → bevorzugte Reihenfolge vor Mistral bei Quota-Limit
    → Mistral (≥ 0.80) ist der eigentliche Qualitäts-Filter

EXCLUSION_KEYWORDS (Hard-Blacklist):
  - Tweet/Post-Zähler (kein Insider-Vorteil möglich)
  - Krypto-Preise (Markt-bestimmt, kein Insider)
  - Wetter / Naturereignisse
  - Sport-Outcomes (bereits vom data_fetcher gefiltert, Doppel-Guard)
  - Entertainment-Awards
  - Allgemeine Sentiment/Polling-Märkte

Author: Diego Ringleb | Phase 15 | 2026-03-17
"""

import re
import logging
from typing import Dict, List, Any
from datetime import datetime, timezone
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Multi-layer anomaly detection für Polymarket Insider-Signale.

    Phase 15 — Blacklist-Modus:
    - EXCLUSION_KEYWORDS: Hard-Ausschluss (multiplier = 0.0)
    - Topic-Multiplier: Score-Booster für bekannte Insider-Kategorien
      (kein Gate mehr — nur Priorisierung)
    - CONFIDENCE_THRESHOLD = 0.45: erreichbar auch ohne Topic-Boost
    - Mistral bei ≥ 0.80 ist der echte Qualitäts-Filter
    """

    # ── Hard-Blacklist: KEIN Insider-Vorteil möglich ─────────────────────
    # Jeder Match hier → multiplier = 0.0 → Score = 0 → kommt nie zu Mistral.
    # Diese Liste muss präzise sein — zu breit = False Negatives.
    EXCLUSION_KEYWORDS = [
        # Öffentlich zählbare Aktivität — kein Insider-Edge
        'tweet', 'tweets', 'post ', 'posts ', 'how many times',
        'how often', 'retweet', 'followers', 'subscribers',
        # Krypto-Preisbewegungen — marktbestimmt, kein einzelner Insider kennt es
        'bitcoin', 'ethereum', 'btc price', 'eth price',
        'will btc', 'will eth', 'reach $', 'above $', 'below $',
        'price of bitcoin', 'price of ethereum',
        'will solana', 'will bnb', 'will xrp', 'will dogecoin', 'will shiba',
        'memecoin', 'nft ',
        # Krypto Market Cap Rankings
        'market cap rank', 'flippening',
        # Wetter / Natur
        'rain', 'hurricane', 'earthquake', 'wildfire', 'flood', 'blizzard',
        'temperature', 'snow in',
        # Entertainment
        'oscar', 'grammy', 'emmy', 'golden globe', 'nobel prize literature',
        'box office', 'streaming views', 'album sales',
        # Sport (Doppel-Guard neben data_fetcher)
        'super bowl', 'world series', 'nba finals', 'stanley cup',
        # Allgemeines Sentiment / Polling
        'approval rating', 'poll shows', 'favorability',
    ]

    def __init__(self):
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        logger.info(f"🔍 AnomalyDetector initialized | Phase 15 Blacklist | threshold: {self.confidence_threshold}")

    # ==================== UNICODE NORMALIZATION ====================

    @staticmethod
    def _normalize_question(question: str) -> str:
        """
        Polymarket-Titel enthalten häufig Unicode-Sonderzeichen:
          – (En-dash U+2013), — (Em-dash U+2014), … (U+2026)

        Diese brechen Space-basierte Keyword-Matches:
          'fed pause–pause–cut' → 'fed ' matcht nicht gegen 'fed–'

        Fix: alle Unicode-Dashes → ASCII-Space, collapse, lowercase.
        """
        normalized = re.sub(r'[\u2010-\u2015\u2212\u2E3A\u2E3B]', ' ', question)
        normalized = normalized.replace('\u2026', ' ')
        normalized = normalized.replace('\u2018', "'").replace('\u2019', "'")
        normalized = normalized.replace('\u201C', '"').replace('\u201D', '"')
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized.lower()

    # ==================== LAYER 1: VOLUME SPIKE ====================

    def detect_volume_spike(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Spike-Tiers (Phase 14 — unverändert):
          >= 8.0x → 0.35 | >= 5.0x → 0.27
          >= 3.5x → 0.18 | >= 2.5x → 0.08 | < 2.5x → 0.00
        """
        current_vol = float(snapshot.get('current_volume', snapshot.get('volume_24hr', 0)))
        baseline    = float(snapshot.get('baseline', 0))

        if baseline <= 0 or current_vol <= 0:
            return {'score': 0.0, 'spike_ratio': 1.0, 'severity': 'none',
                    'reason': 'insufficient_baseline_data'}

        spike_ratio = current_vol / baseline

        if spike_ratio >= 8.0:
            score, severity = 0.35, 'critical'
        elif spike_ratio >= 5.0:
            score, severity = 0.27, 'high'
        elif spike_ratio >= 3.5:
            score, severity = 0.18, 'moderate_high'
        elif spike_ratio >= 2.5:
            score, severity = 0.08, 'moderate'
        else:
            score, severity = 0.0, 'none'

        return {
            'score': score,
            'spike_ratio': round(spike_ratio, 3),
            'current_volume': current_vol,
            'baseline': baseline,
            'severity': severity
        }

    # ==================== LAYER 2: PRICE ANOMALY ====================

    def detect_price_anomaly(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Ungewöhnliche Preisbewegungen als Indikator für informierten Handel."""
        yes_price   = float(snapshot.get('yes_price', 0.5))
        no_price    = float(snapshot.get('no_price', 0.5))
        spread      = abs(yes_price - no_price)
        volume_24hr = float(snapshot.get('volume_24hr', 0))
        liquidity   = float(snapshot.get('liquidity', 1))

        score      = 0.0
        indicators = []

        if yes_price > 0.90 or yes_price < 0.10:
            score += 0.10
            indicators.append(f"extreme_conviction_{yes_price:.2f}")

        vol_liq_ratio = volume_24hr / liquidity if liquidity > 0 else 0
        if vol_liq_ratio > 3.0:
            score += 0.12
            indicators.append(f"vol_liq_pressure_{vol_liq_ratio:.1f}x")
        elif vol_liq_ratio > 1.5:
            score += 0.06
            indicators.append(f"vol_liq_elevated_{vol_liq_ratio:.1f}x")

        if spread > 0.70 and vol_liq_ratio > 1.0:
            score += 0.08
            indicators.append(f"one_sided_bet_spread_{spread:.2f}")

        return {
            'score': round(min(score, 0.25), 3),
            'yes_price': yes_price,
            'no_price': no_price,
            'spread': round(spread, 3),
            'vol_liq_ratio': round(vol_liq_ratio, 3),
            'indicators': indicators
        }

    # ==================== LAYER 3: BEHAVIORAL ====================

    def detect_holder_anomalies(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'score': 0.0,
            'reason': 'no_holder_data_phase6_feature',
            'holder_count': len(snapshot.get('holders', []))
        }

    # ==================== LAYER 4: TOPIC SENSITIVITY ====================

    def calculate_topic_sensitivity(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 15: Topic als Score-Booster, nicht als Gate.

        Step 0 — EXCLUSION CHECK (Hard-Blacklist):
          Treffer → multiplier = 0.0 → Markt wird nie zu Mistral gesendet.
          Dies ist der EINZIGE Hard-Ausschluss.

        Step 1 — TIME HORIZON:
          Zeitliche Nähe erhöht Insider-Relevanz.

        Step 2 — TOPIC BOOST (Optional, kein Gate):
          CRITICAL (×1.40): bestätigte Insider-Kategorien aus echten Fällen
          ELEVATED (×1.15): plausible Insider-Advantage
          KEIN MATCH: multiplier bleibt 1.0 → Markt kommt trotzdem durch
                      wenn Score ≥ CONFIDENCE_THRESHOLD

        Step 3 — RECENCY SURGE:
          Aktiver Surge erhöht Score zusätzlich.
        """
        raw_question = snapshot.get('question', '')
        question     = self._normalize_question(raw_question)

        volume_24hr  = float(snapshot.get('volume_24hr', 0))
        volume_total = float(snapshot.get('volume', volume_24hr))
        end_date_iso = snapshot.get('end_date_iso', '')

        multiplier = 1.0
        reasons    = []

        # ── Step 0: HARD EXCLUSION ────────────────────────────────────────
        exclusion_matched = [kw for kw in self.EXCLUSION_KEYWORDS if kw in question]
        if exclusion_matched:
            return {
                'multiplier': 0.0,
                'reasons': [f"excluded:{exclusion_matched[0].strip()}"],
                'recency_ratio': round(volume_24hr / volume_total, 3) if volume_total > 0 else 0,
                'excluded': True
            }

        # ── Step 1: Time Horizon ─────────────────────────────────────────
        days_to_close = None
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

        # ── Step 2: TOPIC BOOST (Score-Booster, kein Gate) ───────────────
        #
        # Kein Match → multiplier bleibt 1.0 → Markt kommt trotzdem durch.
        # Match → Markt bekommt höheren Score → bevorzugt wenn Mistral-Quota voll.
        #
        critical_topics = [
            # Militär / Geheimdienst
            'airstrike', 'air strike', 'missile strike',
            'military operation', 'military action', 'military strike',
            'troops deploy', 'troop withdrawal', 'invasion',
            'declare war', 'declaration of war',
            'nuclear launch', 'nuclear strike', 'nuclear test',
            'covert operation', 'special forces',
            'targeted killing', 'drone strike',
            'attack on ', 'strike on ',
            # Federal Reserve / Zentralbanken
            'federal reserve', 'the fed', 'fed rate', 'fed pause', 'fed cut',
            'fed hike', 'fomc', 'fed decision', 'fed decide',
            'rate cut', 'rate hike', 'rate pause',
            'interest rate decision', 'emergency rate',
            'powell', 'fed chair',
            'basis point', 'bps cut', 'bps hike',
            'ecb rate', 'bank of england rate', 'boe rate',
            'pause pause', 'pause cut', 'cut pause', 'hike cut',
            'next three decisions', 'next three meetings',
            # FDA / Medikamenten-Zulassungen
            'fda approv', 'fda reject', 'fda decision',
            'drug approv', 'drug reject',
            'emergency use authorization',
            'breakthrough therapy', 'accelerated approval',
            'clinical trial result', 'phase 3 result',
            'vaccine approv', 'vaccine authoriz',
            # Exekutiv- / Präsidialentscheidungen
            'executive order', 'presidential order',
            'pardon ', 'commute sentence', 'clemency',
            'fired by trump', 'fired by president', 'removed by president',
            'cabinet fired', 'secretary fired', 'director fired',
            'appointed by president', 'nominated by president',
            'resign from cabinet', 'step down from cabinet',
            # M&A / CEO
            'merger', 'acquisition', 'takeover bid', 'buyout',
            'hostile takeover', 'leveraged buyout',
            'ceo resign', 'ceo fired', 'ceo step', 'ceo replace',
            'chief executive resign', 'chief executive fired',
            'board of directors', 'shareholder vote',
            'ipo price', 'ipo date',
            # SEC / Regulierung
            'sec approv', 'sec reject', 'sec ruling',
            'etf approv', 'etf reject', 'etf decision',
            'sec enforcement', 'sec charges',
            'cftc ruling', 'cftc approv',
            # Tech-Firmen-Interna
            'openai', 'google search trend', 'year in search',
            'google announce', 'apple announce', 'meta announce',
            'microsoft announce',
            'gpt 5', 'gpt 6', 'gemini ultra',
            'product launch', 'new model release',
        ]

        elevated_topics = [
            # Diplomatisch / Frieden
            'ceasefire', 'peace deal', 'peace agreement', 'peace talks',
            'treaty sign', 'diplomatic agreement',
            'hostage deal', 'hostage release',
            'nato summit', 'g7 ', 'g20 ',
            # Sanktionen / Handelspolitik
            'sanction', 'trade deal', 'trade agreement',
            'tariff on ', 'tariff announ', 'trade war',
            'export ban', 'import ban',
            # Justiz / DOJ
            'indictment', 'grand jury', 'arraignment',
            'doj charge', 'doj indict', 'criminal charge',
            'arrest warrant', 'extradition',
            'impeachment', 'articles of impeachment',
            'conviction', 'guilty verdict', 'acquittal',
            # Regierungsbetrieb
            'government shutdown', 'debt ceiling', 'continuing resolution',
            'budget deal', 'spending bill',
            'default on debt', 'treasury default',
            # Geopolitische Flashpoints
            'north korea', 'taiwan strait', 'south china sea',
            'nato article 5', 'nato deploy',
            'coup attempt', 'coup succeed',
            'regime change', 'government collapse',
            # OPEC / Energie
            'opec', 'opec+', 'oil production cut', 'oil production increase',
            'drilling ban', 'pipeline approv', 'pipeline reject',
            'lng export', 'natural gas pipeline',
            # Tech-Regulierung / Kartellrecht
            'antitrust lawsuit', 'antitrust ruling',
            'break up ', 'forced divestiture',
            'ban tiktok', 'tiktok ban',
            'crypto regulation', 'crypto ban', 'stablecoin bill',
            'crypto bill', 'digital asset law',
            # Wahlen (kurzfristig — Betrug/frühe Auszählung möglich)
            'mayoral election', 'gubernatorial election',
            'special election', 'by election', 'snap election',
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
            # Wahl-Keywords: Boost nur bei kurzem Horizont (≤ 35d)
            election_keywords = [
                'mayoral election', 'gubernatorial election', 'special election',
                'by election', 'snap election', 'runoff election', 'runoff vote',
                'recall election', 'recall vote', 'vote count', 'election result'
            ]
            is_election_kw = any(kw in question for kw in election_keywords)
            if is_election_kw and days_to_close is not None and days_to_close > 35:
                reasons.append(f"election_too_far:{days_to_close}d_no_boost")
            else:
                multiplier *= 1.15
                reasons.append(f"elevated_insider:{elevated_matched[0].strip()}")

        # ── Step 3: Recency Surge ─────────────────────────────────────────
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
        Score = (volume + price + holder) × topic_multiplier

        Phase 15:
          - multiplier = 0.0 (Exclusion) → Score = 0.0, nie zu Mistral
          - multiplier = 1.0 (kein Topic-Match) → Score basiert rein auf
            Volume + Price → kommt durch wenn ≥ CONFIDENCE_THRESHOLD (0.45)
          - multiplier > 1.0 (Topic-Boost) → höherer Score, bevorzugte Position
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
            final_score      = round(base_score * topic_result['multiplier'], 3)
            anomaly_detected = final_score >= self.confidence_threshold

            result = {
                'anomaly_detected': anomaly_detected,
                'score':            final_score,
                'base_score':       round(base_score, 3),
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
                topic_tag = ''
                for r in topic_result['reasons']:
                    if 'critical_insider' in r:
                        topic_tag = ' 🔴'
                        break
                    elif 'elevated_insider' in r:
                        topic_tag = ' 🟡'
                        break
                    elif 'excluded' in r:
                        topic_tag = ' 🚫'
                        break
                logger.info(
                    f"🚨 ANOMALY{topic_tag}: {snapshot.get('question', '')[:50]} | "
                    f"Score: {final_score:.3f} "
                    f"(base: {base_score:.2f} × {topic_result['multiplier']:.2f}) | "
                    f"Vol: ${snapshot.get('volume_24hr', 0):,.0f} | "
                    f"Reasons: {topic_result['reasons']}"
                )
            elif topic_result.get('excluded'):
                logger.debug(
                    f"🚫 Excluded: {snapshot.get('question', '')[:55]} | "
                    f"{topic_result['reasons']}"
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
                'anomaly_detected': False, 'score': 0.0,
                'error': str(e), 'market_id': snapshot.get('id'),
                'breakdown': {'topic_sensitivity': {'reasons': [], 'excluded': False}}
            }

    def batch_detect(self, snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Alle Snapshots analysieren. Sortiert nach Score (höchste zuerst)."""
        results = [self.detect_anomaly(s) for s in snapshots]
        results.sort(key=lambda x: x.get('score', 0), reverse=True)

        detected_count = sum(1 for r in results if r.get('anomaly_detected'))
        excluded_count = sum(
            1 for r in results
            if r.get('breakdown', {}).get('topic_sensitivity', {}).get('excluded', False)
        )
        critical_count = sum(
            1 for r in results
            if any('critical_insider' in reason
                   for reason in r.get('breakdown', {})
                   .get('topic_sensitivity', {}).get('reasons', []))
        )
        elevated_count = sum(
            1 for r in results
            if any('elevated_insider' in reason
                   for reason in r.get('breakdown', {})
                   .get('topic_sensitivity', {}).get('reasons', []))
        )

        logger.info(
            f"📊 Batch: {len(results)} markets | "
            f"{detected_count} flagged ({detected_count/max(len(results),1)*100:.0f}%) | "
            f"🔴 {critical_count} critical | 🟡 {elevated_count} elevated | "
            f"🚫 {excluded_count} excluded"
        )
        return results
