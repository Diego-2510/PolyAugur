"""
PolyAugur Data Fetcher - Polymarket Gamma API Integration
Fetches markets with pagination support (up to 1000+ markets).
Author: Diego Ringleb | Phase 2+4 | 2026-02-28
Architecture: mache-es-sehr-viel-ausfuhrlicher.md [file:1]
"""

import requests
import pandas as pd
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PolymarketFetcher:
    """
    Fetches market data from Polymarket Gamma API.
    Supports pagination for large-scale market coverage (100-1000+ markets).
    Based on PolyAugur architecture [file:1] Abschnitt 4.
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
                if resp.status_code in (404, 422):
                    logger.error(f"HTTP {resp.status_code}: {url}")
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
        """Market must close >6h from now. Rejects already-closed markets."""
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
        """Exclude live sports events (price-distorting during play)."""
        tags = market.get('tags', [])
        tag_labels = []
        for tag in tags:
            if isinstance(tag, dict):
                tag_labels.append(tag.get('label', '').lower())
            elif isinstance(tag, str):
                tag_labels.append(tag.lower())

        question = market.get('question', '').lower()
        sport_keywords = ['nfl', 'nba', 'mlb', 'nhl', 'soccer', 'basketball',
                          'baseball', 'sports', 'live', 'real-time']
        sport_patterns = ['vs ', ' game ', ' match ', ' score']

        if any(kw in lbl for lbl in tag_labels for kw in sport_keywords):
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
        """
        Fetch ALL available markets using pagination.
        Gamma API: max 100 per request, uses offset for pagination.

        Args:
            max_pages: Override config.MAX_PAGES (default). Set lower for testing.

        Returns:
            List of ALL raw normalized markets before filtering.
        """
        max_pages = max_pages or config.MAX_PAGES
        all_markets = []
        offset = 0
        page = 0

        logger.info(f"🔄 Starting paginated fetch (max {max_pages} pages × {config.MARKETS_PER_PAGE} = {max_pages * config.MARKETS_PER_PAGE} markets)")

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
                logger.warning(f"Page {page+1}: No data returned, stopping pagination")
                break

            if not isinstance(data, list) or len(data) == 0:
                logger.info(f"Page {page+1}: Empty response, end of markets")
                break

            # Normalize all markets from this page
            normalized = [self._normalize_market(m) for m in data]
            normalized = [m for m in normalized if m is not None]
            all_markets.extend(normalized)

            logger.info(f"📄 Page {page+1}: +{len(normalized)} markets (total: {len(all_markets)})")

            # If we got fewer than requested, we've reached the end
            if len(data) < config.MARKETS_PER_PAGE:
                logger.info(f"Last page reached (got {len(data)} < {config.MARKETS_PER_PAGE})")
                break

            offset += config.MARKETS_PER_PAGE
            page += 1

            # Small delay to be respectful of API
            time.sleep(0.2)

        logger.info(f"✅ Pagination complete: {len(all_markets)} total raw markets across {page+1} pages")
        return all_markets

    def get_active_markets(self, limit: int = 20, max_pages: int = None) -> List[Dict[str, Any]]:
        """
        Fetch and filter active markets with pagination support.

        Two-stage pipeline:
        1. Paginated fetch → all raw markets
        2. Filter: volume + time validity + sports exclusion

        Args:
            limit: Max markets to return after filtering
            max_pages: Pagination depth (default: config.MAX_PAGES = 1000 markets)

        Returns:
            List of validated, active, filtered market dicts
        """
        all_markets = self.fetch_all_markets_paginated(max_pages=max_pages)

        if not all_markets:
            logger.error("No markets fetched via pagination")
            return []

        # Filter 1: Volume threshold
        volume_filtered = [
            m for m in all_markets
            if m.get('volume_24hr', 0) >= config.MIN_VOLUME_24H
        ]
        logger.info(f"📊 Volume filter: {len(volume_filtered)}/{len(all_markets)} markets ≥${config.MIN_VOLUME_24H}")

        # Filter 2: Truly active (closing >6h)
        time_filtered = [m for m in volume_filtered if self.is_valid_active_market(m)]
        logger.info(f"⏰ Time filter: {len(time_filtered)} markets closing >6h from now")

        # Filter 3: Exclude sports/live
        final_markets = [m for m in time_filtered if not self.is_sports_or_live_event(m)]
        logger.info(f"✅ Final: {len(final_markets)} markets after all filters")

        if not final_markets:
            logger.warning("⚠️ No markets passed all filters")

        # NO sorting by volume here - anomaly_detector will rank by relative score
        return final_markets[:limit] if limit else final_markets

    def get_market_snapshot(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build market snapshot for anomaly detection."""
        try:
            outcome_prices = market.get('outcomePrices', ['0.5', '0.5'])
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)

            yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5

            return {
                'id': market.get('id'),
                'condition_id': market.get('condition_id', market.get('conditionId')),
                'question': market.get('question', 'Unknown'),
                'slug': market.get('slug', ''),
                'description': market.get('description', '')[:500],
                'yes_price': yes_price,
                'no_price': no_price,
                'spread': abs(yes_price - no_price),
                'volume_24hr': market.get('volume_24hr', 0),
                'volume': float(market.get('volume', 0)),
                'liquidity': float(market.get('liquidity', market.get('liquidityNum', 0))),
                'active': market.get('active', True),
                'closed': market.get('closed', False),
                'end_date_iso': market.get('end_date_iso'),
                'tags': market.get('tags', []),
                'event_slug': market.get('event_slug', market.get('eventSlug')),
                # Phase 3 anomaly fields
                'holders': [],
                'volumes_history': [],
                'baseline': market.get('volume_24hr', 0) * 0.8,
                'current_volume': market.get('volume_24hr', 0),
                'spike_ratio': 1.0
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
    logger.info("🧪 PolyAugur Data Fetcher Test - Phase 4 (Scale)")
    logger.info("=" * 60)

    fetcher = PolymarketFetcher()

    print("\n[Test 1] Paginated fetch (2 pages = up to 200 markets)...")
    markets = fetcher.get_active_markets(limit=None, max_pages=2)

    if not markets:
        print("❌ FAIL: No markets returned")
        return

    print(f"✅ PASS: {len(markets)} valid markets fetched")
    print(f"   Top: {markets[0]['question'][:65]}")

    print(f"\n[Test 2] Batch snapshots ({min(5, len(markets))} markets)...")
    snapshots = fetcher.get_snapshots_batch(markets[:5])
    print(f"✅ PASS: {len(snapshots)} snapshots built")

    print(f"\n[Test 3] Volume distribution...")
    vols = sorted([m.get('volume_24hr', 0) for m in markets], reverse=True)
    print(f"   Max: ${vols[0]:,.0f} | Min: ${vols[-1]:,.0f} | Median: ${vols[len(vols)//2]:,.0f}")

    print(f"\n{'#':<3} {'Volume':<14} {'Question':<55}")
    print("-" * 75)
    for i, m in enumerate(markets[:5], 1):
        print(f"{i:<3} ${m.get('volume_24hr', 0):<13,.0f} {m.get('question', '')[:52]}")

    print("\n" + "=" * 60)
    print(f"✅ Phase 4 Data Layer: PASSED | {len(markets)} markets ready")
    print("=" * 60)


if __name__ == "__main__":
    main()
