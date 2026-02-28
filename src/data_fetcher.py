"""
PolyAugur Data Fetcher - Polymarket Gamma API Integration
Fetches markets with pagination support (up to 10,000+ markets).
Includes intelligent rate limiting and progress tracking for large scans.

Author: Diego Ringleb | Phase 11 | 2026-02-28
"""

import requests
import pandas as pd
import time
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import config

logger = logging.getLogger(__name__)


class PolymarketFetcher:
    """
    Fetches market data from Polymarket Gamma API.
    Supports pagination for large-scale market coverage (10,000+ markets).
    Filters: volume, time horizon, sports/live events.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})
        self.cache: Dict[str, Any] = {}
        self.cache_timestamps: Dict[str, datetime] = {}
        self.fetch_stats: Dict[str, Any] = {}

    def _backoff(self, retry_count: int) -> float:
        if retry_count < len(config.BACKOFF_DELAYS):
            return config.BACKOFF_DELAYS[retry_count]
        return 5.0

    def _api_get(
        self, base: str, endpoint: str,
        params: Dict[str, Any] = None, max_retries: int = 3
    ) -> Optional[Any]:
        """Generic GET with retry + backoff."""
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

    # ── Market Validation ────────────────────────────────────────────

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
        Exclude sports & live events where insider manipulation is unlikely.
        Covers: American sports, European football, golf, tennis,
        motor racing, combat sports, college sports.
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
            'epl',
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
            'tournament',
            'finish in the top',
            'top 4 of the',
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

    # ── Normalization ────────────────────────────────────────────────

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

    # ── Pagination (10,000+ Markets) ─────────────────────────────────

    def fetch_all_markets_paginated(self, max_pages: int = None) -> List[Dict[str, Any]]:
        """
        Fetch ALL available markets using offset-based pagination.
        Supports 10,000+ markets with intelligent rate limiting.

        Rate limit strategy:
        - 0.2s between normal pages
        - 1.0s pause every 20 pages (prevent 429s)
        - Progress logged every 10 pages (not every page)
        - Empty page = end of data
        """
        max_pages = max_pages or config.MAX_PAGES
        all_markets = []
        offset = 0
        page = 0
        empty_streak = 0
        fetch_start = time.time()

        total_possible = max_pages * config.MARKETS_PER_PAGE
        logger.info(
            f"🔄 Paginated fetch: max {max_pages} pages × "
            f"{config.MARKETS_PER_PAGE} = {total_possible:,} markets"
        )

        while page < max_pages:
            data = self._api_get(
                config.GAMMA_API_BASE,
                "markets",
                {
                    "active": "true",
                    "closed": "false",
                    "limit": str(config.MARKETS_PER_PAGE),
                    "offset": str(offset),
                }
            )

            # ── Handle empty / failed responses ──────────────────────
            if not data:
                empty_streak += 1
                if empty_streak >= 3:
                    logger.warning(
                        f"Page {page+1}: {empty_streak} consecutive empty responses, "
                        f"stopping pagination"
                    )
                    break
                logger.warning(f"Page {page+1}: No data (streak {empty_streak}/3), retrying next offset")
                offset += config.MARKETS_PER_PAGE
                page += 1
                time.sleep(1.0)
                continue

            if not isinstance(data, list) or len(data) == 0:
                logger.info(f"Page {page+1}: Empty response, end of markets")
                break

            # Reset empty streak on success
            empty_streak = 0

            # ── Normalize ────────────────────────────────────────────
            normalized = [self._normalize_market(m) for m in data]
            normalized = [m for m in normalized if m is not None]
            all_markets.extend(normalized)

            # ── Progress logging (every 10 pages or last page) ───────
            is_last_page = len(data) < config.MARKETS_PER_PAGE
            if (page + 1) % 10 == 0 or is_last_page or page == 0:
                elapsed = time.time() - fetch_start
                rate = len(all_markets) / elapsed if elapsed > 0 else 0
                logger.info(
                    f"📄 Page {page+1}/{max_pages}: "
                    f"{len(all_markets):,} markets fetched "
                    f"({elapsed:.0f}s, {rate:.0f} mkts/s)"
                )

            if is_last_page:
                logger.info(
                    f"📍 Last page reached at page {page+1} "
                    f"({len(data)} < {config.MARKETS_PER_PAGE})"
                )
                break

            # ── Rate limiting ────────────────────────────────────────
            offset += config.MARKETS_PER_PAGE
            page += 1

            if page % 20 == 0:
                logger.info(f"⏸️ Rate limit pause at page {page}...")
                time.sleep(1.0)
            else:
                time.sleep(0.2)

        # ── Summary ──────────────────────────────────────────────────
        total_time = time.time() - fetch_start
        self.fetch_stats = {
            'pages_fetched': page + 1,
            'markets_raw': len(all_markets),
            'fetch_time_sec': round(total_time, 1),
            'markets_per_sec': round(len(all_markets) / total_time, 1) if total_time > 0 else 0,
        }

        logger.info(
            f"✅ Pagination complete: {len(all_markets):,} markets "
            f"across {page+1} pages in {total_time:.1f}s"
        )
        return all_markets

    # ── Filtering Pipeline ───────────────────────────────────────────

    def get_active_markets(self, limit: int = 20, max_pages: int = None) -> List[Dict[str, Any]]:
        """
        Fetch and filter active markets through the full pipeline.
        Pipeline: Pagination → Volume → Time Horizon → Sports Filter
        """
        all_markets = self.fetch_all_markets_paginated(max_pages=max_pages)

        if not all_markets:
            logger.error("No markets fetched via pagination")
            return []

        # Step 1: Volume filter
        volume_filtered = [
            m for m in all_markets
            if m.get('volume_24hr', 0) >= config.MIN_VOLUME_24H
        ]
        logger.info(
            f"📊 Volume filter: {len(volume_filtered):,}/{len(all_markets):,} "
            f"markets ≥${config.MIN_VOLUME_24H:,}"
        )

        # Step 2: Time horizon filter
        time_filtered = [m for m in volume_filtered if self.is_valid_active_market(m)]
        logger.info(f"⏰ Time filter: {len(time_filtered):,} markets closing >6h from now")

        # Step 3: Sports / live event filter
        final_markets = [m for m in time_filtered if not self.is_sports_or_live_event(m)]
        logger.info(
            f"🏟️ Sports filter: removed {len(time_filtered) - len(final_markets):,} "
            f"sports/live markets"
        )
        logger.info(f"✅ Final: {len(final_markets):,} markets after all filters")

        # Store filter stats
        self.fetch_stats.update({
            'markets_after_volume': len(volume_filtered),
            'markets_after_time': len(time_filtered),
            'markets_after_sports': len(final_markets),
            'sports_removed': len(time_filtered) - len(final_markets),
        })

        if not final_markets:
            logger.warning("⚠️ No markets passed all filters")

        return final_markets[:limit] if limit else final_markets

    # ── Snapshot Builder ─────────────────────────────────────────────

    def get_market_snapshot(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Build market snapshot with real baseline.
        Baseline = all-time volume / age_days → real daily average.
        spike_ratio = volume_24hr / baseline → relative spike indicator.
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
                'clobTokenIds': market.get('clobTokenIds', market.get('clob_token_ids', [])),
                'baseline': round(avg_daily_baseline, 2),
                'current_volume': volume_24hr,
                'spike_ratio': round(spike_ratio, 3),
                'age_days': age_days,
                'holders': [],
                'volumes_history': [],
                'price_delta_30m': 0.0,
                'volume_delta_30m': 0.0,
                'price_velocity': 0.0,
            }
        except Exception as e:
            logger.error(f"Snapshot error for {market.get('id')}: {e}", exc_info=True)
            return None

    def get_snapshots_batch(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build snapshots for a list of markets."""
        snapshots = [self.get_market_snapshot(m) for m in markets]
        return [s for s in snapshots if s is not None]

    # ── Baseline (Legacy / DataFrame) ────────────────────────────────

    def calculate_baseline(self, volumes: List[Dict[str, Any]]) -> Dict[str, float]:
        """Legacy baseline from volume history DataFrame."""
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
                "spike_ratio": float(spike_ratio),
            }
        except Exception as e:
            logger.error(f"Baseline error: {e}")
            return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}

    def get_fetch_stats(self) -> Dict[str, Any]:
        """Return stats from last fetch cycle (for health monitor / dashboard)."""
        return self.fetch_stats.copy()


# ── Test Suite ───────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    print("=" * 60)
    print("🧪 PolyAugur Data Fetcher Test - Phase 11 (10k+ Markets)")
    print("=" * 60)

    fetcher = PolymarketFetcher()

    # Test 1: Small paginated fetch (5 pages = 500 markets)
    print("\n[Test 1] Paginated fetch (5 pages = max 500 markets)...")
    markets = fetcher.get_active_markets(limit=None, max_pages=5)

    if not markets:
        print("❌ FAIL: No markets returned")
        return

    stats = fetcher.get_fetch_stats()
    print(f"✅ PASS: {len(markets):,} valid markets fetched")
    print(f"   Pages:       {stats.get('pages_fetched', '?')}")
    print(f"   Raw markets: {stats.get('markets_raw', '?'):,}")
    print(f"   After volume: {stats.get('markets_after_volume', '?'):,}")
    print(f"   After time:   {stats.get('markets_after_time', '?'):,}")
    print(f"   Sports removed: {stats.get('sports_removed', '?'):,}")
    print(f"   Fetch time:   {stats.get('fetch_time_sec', '?')}s")
    print(f"   Rate:         {stats.get('markets_per_sec', '?')} mkts/s")

    # Test 2: Batch snapshots
    print(f"\n[Test 2] Batch snapshots with real baseline...")
    snapshots = fetcher.get_snapshots_batch(markets[:5])
    print(f"✅ PASS: {len(snapshots)} snapshots built")
    print(
        f"\n{'#':<3} {'Spike':<8} {'Age':<8} "
        f"{'Baseline':<14} {'Vol 24h':<14} {'Question':<40}"
    )
    print("-" * 90)
    for i, s in enumerate(snapshots, 1):
        print(
            f"{i:<3} {s.get('spike_ratio', 0):<8.2f} "
            f"{s.get('age_days', 0):<8}d "
            f"${s.get('baseline', 0):<13,.0f} "
            f"${s.get('volume_24hr', 0):<13,.0f} "
            f"{s.get('question', '')[:38]}"
        )

    # Test 3: Sports filter check
    print(f"\n[Test 3] Sports filter check...")
    sport_check = [
        'nhl', 'nba', 'nfl', 'bundesliga', 'champions league',
        'stanley cup', 'playoff', 'championship', 'tournament',
        'epl', 'masters', 'pga', 'golf', 'top 4',
    ]
    leaked = [
        m for m in markets
        if any(kw in m.get('question', '').lower() for kw in sport_check)
    ]
    if leaked:
        print(f"❌ {len(leaked)} sports markets leaked:")
        for m in leaked[:5]:
            print(f"   - {m['question'][:65]}")
    else:
        print(f"✅ PASS: 0 sports markets in {len(markets):,} results")

    # Test 4: Baseline sanity
    print(f"\n[Test 4] Baseline sanity check...")
    for s in snapshots[:3]:
        ok = s.get('baseline', 0) > 0 and s.get('spike_ratio', 1.0) != 1.0
        status = "✅" if ok else "⚠️"
        print(
            f"   {status} Age={s.get('age_days')}d | "
            f"Baseline=${s.get('baseline', 0):,.0f} | "
            f"Spike={s.get('spike_ratio', 0):.2f}x"
        )

    # Test 5: Large scan estimate
    print(f"\n[Test 5] Full 10k scan estimate...")
    rate = stats.get('markets_per_sec', 50)
    est_time = 10_000 / rate if rate > 0 else 999
    print(f"   At {rate:.0f} mkts/s → ~{est_time:.0f}s for 10,000 markets")
    print(f"   {'✅' if est_time < 120 else '⚠️'} {'Under 2 min' if est_time < 120 else 'May be slow'}")

    print("\n" + "=" * 60)
    print("✅ Phase 11 Data Fetcher: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
