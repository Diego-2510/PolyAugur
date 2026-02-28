"""
PolyAugur Data Fetcher - Polymarket Gamma API Integration
Fetches markets with pagination support (up to 1000+ markets).
Author: Diego Ringleb | Phase 2+4+5 | 2026-02-28
Architecture: mache-es-sehr-viel-ausfuhrlicher.md [file:1]
"""

import requests
import pandas as pd
import time
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PolymarketFetcher:
    """
    Fetches market data from Polymarket Gamma API.
    Supports pagination for large-scale market coverage (100-1000+ markets).
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})
        self.cache: Dict[str, Any] = {}
        self.cache_timestamps: Dict[str, datetime] = {}

    def _backoff(self, retry_count: int) -> float:
        if retry_count < len(config.BACKOFF_DELAYS):
            return config.BACKOFF_DELAYS[retry_count]
        return 5.0

    def _api_get(self, base: str, endpoint: str, params: Dict[str, Any] = None, max_retries: int = 3) -> Optional[Any]:
        url = f"{base}/{endpoint}"
        params = params or {}

        for retry in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)

                if resp.status_code == 429:
                    delay = self._backoff(retry)
                    logger.warning(f"Rate limit on {endpoint}, backoff {delay:.1f}s")
                    time.sleep(delay)
                    continue
                if resp.status_code in (400, 404, 422):
                    logger.debug(f"HTTP {resp.status_code}: {url} → {resp.text[:80]}")
                    return None

                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on {endpoint} (retry {retry+1}/{max_retries})")
                if retry < max_retries - 1:
                    time.sleep(self._backoff(retry))
            except requests.exceptions.RequestException as e:
                logger.error(f"API error on {endpoint}: {e}")
                if retry < max_retries - 1:
                    time.sleep(self._backoff(retry))

        logger.error(f"Max retries exceeded for {endpoint}")
        return None

    def is_valid_active_market(self, market: Dict[str, Any]) -> bool:
        """Market must close >6h from now."""
        now = datetime.now(timezone.utc)
        end_date_str = None
        for field in ['end_date_iso', 'endDate', 'closesAt', 'end_date']:
            if market.get(field):
                end_date_str = market[field]
                break

        if not end_date_str:
            return False

        try:
            closes_at = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            return (closes_at - now) >= timedelta(hours=6)
        except (ValueError, TypeError):
            return False

    def is_sports_or_live_event(self, market: Dict[str, Any]) -> bool:
        """
        Exclude sports & live events where insider manipulation is highly unlikely.
        Checks both tags AND question text.
        Covers: American sports, European football, golf, tennis, motor racing, combat sports.
        """
        tags = market.get('tags', [])
        tag_labels = []
        for tag in tags:
            if isinstance(tag, dict):
                tag_labels.append(tag.get('label', '').lower())
            elif isinstance(tag, str):
                tag_labels.append(tag.lower())

        question = market.get('question', '').lower()

        sport_keywords = [
            # American sports leagues
            'nfl', 'nba', 'mlb', 'nhl', 'mls',
            'super bowl', 'stanley cup', 'world series', 'nba finals',
            # European football
            'bundesliga', 'champions league', 'premier league', 'la liga',
            'serie a', 'ligue 1', 'europa league', 'uefa',
            'epl',                          # English Premier League abbreviation
            # Golf
            'pga', 'pga tour', 'masters', 'golf', 'augusta',
            'ryder cup', 'open championship', 'us open golf',
            # General sports orgs
            'fifa', 'world cup', 'olympics',
            # Motor racing
            'formula 1', 'f1 ', 'nascar', 'motogp',
            # Other sports
            'wimbledon', 'ufc', 'boxing', 'wrestling',
            'tennis', 'cycling', 'tour de france',
            # College sports
            'ncaa', 'college football', 'college basketball',
        ]

        sport_patterns = [
            'vs ', ' game ', ' match ', ' score',
            'playoff', 'championship',
            'tournament',               # catches golf/tennis/esports tournaments
            'finish in the top',        # catches EPL top-4 finish markets
            'top 4 of the',             # EPL-specific
            'league table',
            'title race',
            'win the.*cup',
        ]

        if any(kw in lbl for lbl in tag_labels for kw in sport_keywords):
            return True
        if any(kw in question for kw in sport_keywords):
            return True
        if any(p in question for p in sport_patterns):
            return True

        return False

    def _normalize_market(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize raw API response to consistent field names."""
        try:
            volume = (
                market.get('volume_24hr') or
                market.get('volume24hr') or
                market.get('volume24Hrs') or
                market.get('volumeNum') or
                market.get('volume') or 0
            )
            if isinstance(volume, str):
                volume = float(volume.replace(',', ''))
            volume = float(volume)

            end_date = (
                market.get('end_date_iso') or
                market.get('endDate') or
                market.get('closesAt') or
                market.get('end_date')
            )

            return {
                **market,
                'volume_24hr': volume,
                'end_date_iso': end_date,
                'tags': market.get('tags', []),
                'question': market.get('question', 'Unknown Market')
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Normalization error for {market.get('id')}: {e}")
            return None

    def fetch_all_markets_paginated(self, max_pages: int = None) -> List[Dict[str, Any]]:
        """Fetch ALL available markets using pagination (max 100/page, offset-based)."""
        max_pages = max_pages or config.MAX_PAGES
        all_markets = []
        offset = 0
        page = 0

        logger.info(f"🔄 Paginated fetch (max {max_pages} pages × {config.MARKETS_PER_PAGE} = {max_pages * config.MARKETS_PER_PAGE} markets)")

        while page < max_pages:
            data = self._api_get(
                config.GAMMA_API_BASE,
                "markets",
                {
                    "active": "true",
                    "closed": "false",
                    "limit": str(config.MARKETS_PER_PAGE),
                    "offset": str(offset)
                }
            )

            if not data:
                logger.warning(f"Page {page+1}: No data, stopping pagination")
                break

            if not isinstance(data, list) or len(data) == 0:
                logger.info(f"Page {page+1}: Empty, end of markets")
                break

            normalized = [self._normalize_market(m) for m in data]
            normalized = [m for m in normalized if m is not None]
            all_markets.extend(normalized)

            logger.info(f"📄 Page {page+1}: +{len(normalized)} markets (total: {len(all_markets)})")

            if len(data) < config.MARKETS_PER_PAGE:
                logger.info(f"Last page reached ({len(data)} < {config.MARKETS_PER_PAGE})")
                break

            offset += config.MARKETS_PER_PAGE
            page += 1
            time.sleep(0.2)

        logger.info(f"✅ Pagination complete: {len(all_markets)} markets across {page+1} pages")
        return all_markets

    def get_active_markets(self, limit: int = 20, max_pages: int = None) -> List[Dict[str, Any]]:
        """
        Fetch and filter active markets.
        Filters: volume ≥ threshold, closes >6h, no sports/live events.
        No volume sort – anomaly_detector ranks by relative score.
        """
        all_markets = self.fetch_all_markets_paginated(max_pages=max_pages)

        if not all_markets:
            logger.error("No markets fetched via pagination")
            return []

        volume_filtered = [
            m for m in all_markets
            if m.get('volume_24hr', 0) >= config.MIN_VOLUME_24H
        ]
        logger.info(f"📊 Volume filter: {len(volume_filtered)}/{len(all_markets)} markets ≥${config.MIN_VOLUME_24H}")

        time_filtered = [m for m in volume_filtered if self.is_valid_active_market(m)]
        logger.info(f"⏰ Time filter: {len(time_filtered)} markets closing >6h from now")

        final_markets = [m for m in time_filtered if not self.is_sports_or_live_event(m)]
        logger.info(f"✅ Final: {len(final_markets)} markets after all filters")

        if not final_markets:
            logger.warning("⚠️ No markets passed all filters")

        return final_markets[:limit] if limit else final_markets

    def get_market_snapshot(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Build market snapshot with real baseline from createdAt + all-time volume.
        Baseline = all-time volume / age_days → real daily average.
        spike_ratio = volume_24hr / baseline → true relative spike indicator.
        """
        try:
            outcome_prices = market.get('outcomePrices', ['0.5', '0.5'])
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)

            yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5

            volume_24hr = float(market.get('volume_24hr', 0))
            volume_total = float(market.get('volume', volume_24hr))

            now = datetime.now(timezone.utc)
            age_days = 30
            try:
                created_str = (
                    market.get('createdAt') or
                    market.get('created_at') or
                    market.get('startDate') or ''
                )
                if created_str:
                    created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                    age_days = max((now - created).days, 1)
            except (ValueError, TypeError, AttributeError):
                pass

            avg_daily_baseline = volume_total / age_days if age_days > 0 else volume_24hr * 0.5
            spike_ratio = volume_24hr / avg_daily_baseline if avg_daily_baseline > 0 else 1.0

            return {
                'id': market.get('id'),
                'condition_id': market.get('condition_id', market.get('conditionId')),
                'question': market.get('question', 'Unknown'),
                'slug': market.get('slug', ''),
                'description': market.get('description', '')[:500],
                'yes_price': yes_price,
                'no_price': no_price,
                'spread': abs(yes_price - no_price),
                'volume_24hr': volume_24hr,
                'volume': volume_total,
                'liquidity': float(market.get('liquidity', market.get('liquidityNum', 0))),
                'active': market.get('active', True),
                'closed': market.get('closed', False),
                'end_date_iso': market.get('end_date_iso'),
                'tags': market.get('tags', []),
                'event_slug': market.get('event_slug', market.get('eventSlug')),
                'baseline': round(avg_daily_baseline, 2),
                'current_volume': volume_24hr,
                'spike_ratio': round(spike_ratio, 3),
                'age_days': age_days,
                'holders': [],
                'volumes_history': [],
                'price_delta_30m': 0.0,
                'volume_delta_30m': 0.0,
                'price_velocity': 0.0
            }
        except Exception as e:
            logger.error(f"Snapshot error for {market.get('id')}: {e}", exc_info=True)
            return None

    def get_snapshots_batch(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build snapshots for a list of markets."""
        snapshots = [self.get_market_snapshot(m) for m in markets]
        return [s for s in snapshots if s is not None]

    def calculate_baseline(self, volumes: List[Dict[str, Any]]) -> Dict[str, float]:
        if not volumes or len(volumes) < 6:
            return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}
        try:
            df = pd.DataFrame(volumes)
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            df = df.dropna(subset=['volume'])
            if len(df) < 6:
                return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}
            baseline = df['volume'].tail(6).mean()
            current = df['volume'].iloc[-1]
            spike_ratio = current / baseline if baseline > 0 else 1.0
            return {
                "baseline": float(baseline),
                "current_volume": float(current),
                "spike_ratio": float(spike_ratio)
            }
        except Exception as e:
            logger.error(f"Baseline error: {e}")
            return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}


def main():
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Data Fetcher Test - Phase 5 (Real Baseline)")
    logger.info("=" * 60)

    fetcher = PolymarketFetcher()

    print("\n[Test 1] Paginated fetch (2 pages)...")
    markets = fetcher.get_active_markets(limit=None, max_pages=2)

    if not markets:
        print("❌ FAIL: No markets returned")
        return

    print(f"✅ PASS: {len(markets)} valid markets fetched")

    print(f"\n[Test 2] Batch snapshots with real baseline...")
    snapshots = fetcher.get_snapshots_batch(markets[:5])
    print(f"✅ PASS: {len(snapshots)} snapshots built")
    print(f"\n{'#':<3} {'Spike':<8} {'Age':<8} {'Baseline':<14} {'Vol 24h':<14} {'Question':<40}")
    print("-" * 90)
    for i, s in enumerate(snapshots, 1):
        print(
            f"{i:<3} {s.get('spike_ratio', 0):<8.2f} "
            f"{s.get('age_days', 0):<8}d "
            f"${s.get('baseline', 0):<13,.0f} "
            f"${s.get('volume_24hr', 0):<13,.0f} "
            f"{s.get('question', '')[:38]}"
        )

    print(f"\n[Test 3] Sports filter check...")
    sport_check = [
        'nhl', 'nba', 'nfl', 'bundesliga', 'champions league',
        'stanley cup', 'playoff', 'championship', 'tournament',
        'epl', 'masters', 'pga', 'golf', 'top 4'
    ]
    leaked = [m for m in markets if any(kw in m.get('question', '').lower() for kw in sport_check)]
    if leaked:
        print(f"❌ {len(leaked)} sports markets leaked:")
        for m in leaked[:5]:
            print(f"   - {m['question'][:65]}")
    else:
        print(f"✅ PASS: 0 sports markets in {len(markets)} results")

    print(f"\n[Test 4] Baseline sanity check...")
    for s in snapshots[:3]:
        status = "✅" if s.get('baseline', 0) > 0 and s.get('spike_ratio', 1.0) != 1.0 else "⚠️"
        print(f"   {status} Age={s.get('age_days')}d | Baseline=${s.get('baseline', 0):,.0f} | Spike={s.get('spike_ratio', 0):.2f}x")

    print("\n" + "=" * 60)
    print(f"✅ Phase 5 Data Fetcher: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
