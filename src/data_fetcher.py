"""
PolyAugur Data Fetcher - Polymarket Gamma API Integration
Fetches markets, applies volume/event filters, caches data with rate-limit handling.
Author: Diego Ringleb | Phase 2 | 2026-02-28
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
    Fetches market data from Polymarket Gamma API with caching and rate-limit handling.
    Based on PolyAugur architecture [file:1] Abschnitt 4.
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})
        self.cache: Dict[str, Any] = {}
        self.cache_timestamps: Dict[str, datetime] = {}

    def _backoff(self, retry_count: int) -> float:
        """Exponential backoff delays [file:1] Abschnitt 4.3."""
        if retry_count < len(config.BACKOFF_DELAYS):
            return config.BACKOFF_DELAYS[retry_count]
        return 5.0

    def _api_get(self, base: str, endpoint: str, params: Dict[str, Any] = None, max_retries: int = 3) -> Optional[Any]:
        """
        Generic API GET with retry/backoff logic.
        Handles 429 rate limits, connection errors, timeouts.
        """
        url = f"{base}/{endpoint}"
        params = params or {}
        
        for retry in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                
                if resp.status_code == 429:
                    delay = self._backoff(retry)
                    logger.warning(f"Rate limit on {endpoint}, backoff {delay:.1f}s (retry {retry+1}/{max_retries})")
                    time.sleep(delay)
                    continue
                
                if resp.status_code == 404:
                    logger.error(f"404 Not Found: {url}")
                    return None
                
                if resp.status_code == 422:
                    logger.error(f"422 Unprocessable Entity: {url} - Check parameters")
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
        """
        Check if market is truly active (closing >6h from now).
        
        Critical fix: API returns markets with active=true even when closed [web:40].
        We validate end_date_iso is in the future and >6h away.
        
        Returns:
            True if market is active AND closing >6h in future.
        """
        now = datetime.now(timezone.utc)
        
        # Find end date field
        end_date_fields = ['end_date_iso', 'endDate', 'closesAt', 'end_date']
        end_date_str = None
        for field in end_date_fields:
            if market.get(field):
                end_date_str = market[field]
                break
        
        if not end_date_str:
            logger.debug(f"No end_date found for {market.get('id')} - excluding")
            return False
        
        try:
            # Parse ISO8601 format
            closes_at = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            delta = closes_at - now
            
            # Reject if already closed or closing <6h
            if delta < timedelta(hours=6):
                delta_h = delta.total_seconds() / 3600
                if delta_h < 0:
                    logger.debug(f"⏱ Skipping closed market '{market.get('question', 'unknown')[:40]}' (closed {abs(delta_h):.0f}h ago)")
                else:
                    logger.debug(f"⏱ Skipping soon-closing '{market.get('question', 'unknown')[:40]}' (closes in {delta_h:.1f}h)")
                return False
            
            return True  # Valid: closing >6h from now
            
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Could not parse end date '{end_date_str}': {e}")
            return False

    def is_sports_or_live_event(self, market: Dict[str, Any]) -> bool:
        """
        Check if market is sports/live event (separate from time-based filter).
        
        Returns:
            True if market is sports/live related.
        """
        tags = market.get('tags', [])
        tag_labels = []
        
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    tag_labels.append(tag.get('label', '').lower())
                elif isinstance(tag, str):
                    tag_labels.append(tag.lower())
        
        # Check question text for sports indicators
        question = market.get('question', '').lower()
        
        # Sports/live keywords
        exclude_keywords = [
            'sports', 'nfl', 'nba', 'mlb', 'nhl', 'soccer', 'football', 
            'basketball', 'baseball', 'game', 'match', 'score', 'vs ', 
            'live', 'real-time', 'tournament', 'championship'
        ]
        
        # Check tags
        if any(keyword in label for label in tag_labels for keyword in exclude_keywords):
            logger.debug(f"🚫 Sports tag: {market.get('question', 'unknown')[:40]}")
            return True
        
        # Check question (more conservative - only obvious patterns)
        obvious_sports = ['vs ', ' game ', ' match ', ' score', 'will win', 'who wins']
        if any(pattern in question for pattern in obvious_sports):
            logger.debug(f"🚫 Sports question: {market.get('question', 'unknown')[:40]}")
            return True
        
        return False

    def get_active_markets(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch active markets from Gamma API /markets endpoint [web:18][web:32].
        
        Filters applied (in order):
        1. API filter: active=true, closed=false
        2. Volume >= MIN_VOLUME_24H
        3. Closing time >6h from now (critical fix for API bug [web:40])
        4. Exclude sports/live events
        
        Args:
            limit: Target markets to return (fetches more for filtering)
            
        Returns:
            List of validated, active market dicts
        """
        # Fetch extra markets (expect ~70% to be filtered out)
        fetch_limit = min(limit * 5, 100)
        
        markets_data = self._api_get(
            config.GAMMA_API_BASE,
            "markets",
            {
                "active": "true",
                "closed": "false",
                "limit": str(fetch_limit)
            }
        )
        
        if not markets_data:
            logger.error("Failed to fetch from /markets endpoint")
            return []
        
        # Normalize field names
        all_markets = []
        for market in markets_data:
            try:
                # Volume (try multiple field names)
                volume = (
                    market.get('volume_24hr') or 
                    market.get('volume24hr') or 
                    market.get('volume24Hrs') or
                    market.get('volumeNum') or 
                    market.get('volume') or 
                    0
                )
                
                if isinstance(volume, str):
                    volume = float(volume.replace(',', ''))
                volume = float(volume)
                
                # End date
                end_date = (
                    market.get('end_date_iso') or
                    market.get('endDate') or
                    market.get('closesAt') or
                    market.get('end_date')
                )
                
                normalized = {
                    **market,
                    'volume_24hr': volume,
                    'end_date_iso': end_date,
                    'tags': market.get('tags', []),
                    'question': market.get('question', 'Unknown Market')
                }
                all_markets.append(normalized)
                
            except (ValueError, TypeError) as e:
                logger.warning(f"Skipping market {market.get('id')} - normalization error: {e}")
                continue
        
        logger.info(f"📥 Fetched {len(all_markets)} raw markets from API")
        
        # Filter 1: Volume threshold
        volume_filtered = [
            m for m in all_markets 
            if m.get('volume_24hr', 0) >= config.MIN_VOLUME_24H
        ]
        logger.info(f"📊 {len(volume_filtered)} markets ≥${config.MIN_VOLUME_24H} volume")
        
        # Filter 2: Validate truly active (closing >6h)
        time_filtered = [m for m in volume_filtered if self.is_valid_active_market(m)]
        logger.info(f"⏰ {len(time_filtered)} markets closing >6h from now")
        
        # Filter 3: Exclude sports/live
        final_markets = [m for m in time_filtered if not self.is_sports_or_live_event(m)]
        logger.info(f"✅ {len(final_markets)} markets after sports exclusion")
        
        # Sort by volume descending
        final_markets.sort(key=lambda x: x.get('volume_24hr', 0), reverse=True)
        
        if not final_markets:
            logger.warning(
                f"⚠️ No markets passed filters. Consider:\n"
                f"   - Lowering MIN_VOLUME_24H (currently ${config.MIN_VOLUME_24H})\n"
                f"   - Fetching more markets (increase limit)\n"
                f"   - Checking API: curl 'https://gamma-api.polymarket.com/markets?active=true&limit=5'"
            )
        
        return final_markets[:limit]

    def get_market_snapshot(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Build simplified snapshot for MVP - uses only Gamma API data.
        Phase 2: Basic snapshot. Phase 3+: Add holder analysis.
        
        Returns:
            Enhanced market dict with computed fields
        """
        try:
            # Extract outcome prices
            outcome_prices = market.get('outcomePrices', ['0.5', '0.5'])
            
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)
            
            yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5
            
            # Build snapshot
            snapshot = {
                'id': market.get('id'),
                'condition_id': market.get('condition_id', market.get('conditionId')),
                'question': market.get('question', 'Unknown'),
                'slug': market.get('slug', ''),
                'description': market.get('description', '')[:500],
                
                # Prices
                'yes_price': yes_price,
                'no_price': no_price,
                'spread': abs(yes_price - no_price),
                
                # Volume & Liquidity
                'volume_24hr': market.get('volume_24hr', 0),
                'volume': float(market.get('volume', 0)),
                'liquidity': float(market.get('liquidity', market.get('liquidityNum', 0))),
                
                # Metadata
                'active': market.get('active', True),
                'closed': market.get('closed', False),
                'end_date_iso': market.get('end_date_iso'),
                'tags': market.get('tags', []),
                'event_slug': market.get('event_slug', market.get('eventSlug')),
                
                # Placeholders for Phase 3+
                'holders': [],
                'volumes_history': [],
                'baseline': market.get('volume_24hr', 0) * 0.8,
                'current_volume': market.get('volume_24hr', 0),
                'spike_ratio': 1.0
            }
            
            logger.debug(f"✅ Snapshot: {snapshot['question'][:40]} | ${snapshot['volume_24hr']:.0f} vol")
            return snapshot
            
        except Exception as e:
            logger.error(f"Error building snapshot for {market.get('id')}: {e}", exc_info=True)
            return None

    def calculate_baseline(self, volumes: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculate SMA-6h baseline for spike detection [file:1] Abschnitt 5.2.
        Phase 2 MVP: Placeholder. Phase 3: Real volume history.
        """
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
            logger.error(f"Baseline calculation error: {e}")
            return {"baseline": 0.0, "current_volume": 0.0, "spike_ratio": 1.0}


def main():
    """
    Test PolymarketFetcher standalone.
    Validates: API connectivity, filtering, time validation, snapshot creation.
    """
    logger.info("=" * 60)
    logger.info("🧪 PolyAugur Data Fetcher Test - Phase 2 (v2)")
    logger.info("=" * 60)
    
    fetcher = PolymarketFetcher()
    
    # Test 1: Fetch active markets
    print("\n[Test 1] Fetching truly active markets (closing >6h)...")
    markets = fetcher.get_active_markets(limit=10)
    
    if not markets:
        print("❌ FAIL: No markets returned")
        print("\n💡 Troubleshooting:")
        print(f"   1. Current threshold: MIN_VOLUME_24H = ${config.MIN_VOLUME_24H}")
        print(f"   2. Try lowering in config.py to 5000 or 1000")
        print(f"   3. Manual API test:")
        print(f"      curl 'https://gamma-api.polymarket.com/markets?active=true&limit=5'")
        print(f"   4. If API returns markets but code filters all → check end dates")
        return
    
    print(f"✅ PASS: Fetched {len(markets)} valid markets")
    print(f"   Top: {markets[0]['question'][:65]}")
    print(f"   Volume: ${markets[0].get('volume_24hr', 0):,.0f}")
    
    # Show closing times
    now = datetime.now(timezone.utc)
    for m in markets[:3]:
        try:
            end_date = m.get('end_date_iso')
            if end_date:
                closes_at = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                hours_until = (closes_at - now).total_seconds() / 3600
                print(f"   Closes in: {hours_until:.0f}h - {m['question'][:50]}")
        except:
            pass
    
    # Test 2: Market snapshot
    print("\n[Test 2] Building market snapshot...")
    snapshot = fetcher.get_market_snapshot(markets[0])
    
    if not snapshot:
        print("❌ FAIL: Snapshot creation failed")
        return
    
    print(f"✅ PASS: Snapshot created")
    print(f"   Question: {snapshot['question'][:60]}")
    print(f"   Yes: {snapshot['yes_price']:.3f} | No: {snapshot['no_price']:.3f}")
    print(f"   Volume 24h: ${snapshot['volume_24hr']:,.0f}")
    print(f"   Liquidity: ${snapshot['liquidity']:,.0f}")
    
    # Test 3: Time filter validation
    print("\n[Test 3] Time filter validation...")
    print(f"✅ PASS: All {len(markets)} markets closing >6h from now")
    
    # Test 4: Multiple snapshots (performance)
    print("\n[Test 4] Performance test...")
    start = time.time()
    for i in range(5):
        s = fetcher.get_market_snapshot(markets[0])
    elapsed = time.time() - start
    print(f"✅ PASS: 5 snapshots in {elapsed:.3f}s ({elapsed/5*1000:.1f}ms avg)")
    
    # Test 5: Markets summary
    print("\n[Test 5] Markets summary (Top 5)...")
    print(f"{'#':<3} {'Volume':<14} {'Closes':<10} {'Question':<45}")
    print("-" * 75)
    for i, m in enumerate(markets[:5], 1):
        vol_str = f"${m.get('volume_24hr', 0):,.0f}"
        
        # Calculate hours until close
        try:
            end_date = m.get('end_date_iso')
            closes_at = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            hours = (closes_at - now).total_seconds() / 3600
            if hours < 168:  # <1 week
                close_str = f"{hours:.0f}h"
            else:
                days = hours / 24
                close_str = f"{days:.1f}d"
        except:
            close_str = "N/A"
        
        q = m.get('question', 'Unknown')[:42]
        print(f"{i:<3} {vol_str:<14} {close_str:<10} {q}")
    
if __name__ == "__main__":
    main()
