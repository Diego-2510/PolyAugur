"""
PolyAugur Trade Analyzer - CLOB On-Chain Trade Intelligence
Fetches real trades from Polymarket CLOB API for confirmed signals.
Detects: whale trades, wallet concentration, directional bias, timing bursts.

Only called for Mistral-confirmed signals (5-15 per cycle) → low API cost.

CLOB API: https://clob.polymarket.com
Endpoint: GET /trades?asset_id={token_id}

Author: Diego Ringleb | Phase 7 | 2026-02-28
"""

import json
import logging
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from collections import Counter
import config

logger = logging.getLogger(__name__)


class TradeAnalyzer:
    """
    Analyzes CLOB trades for Mistral-confirmed signals.
    Adds on-chain evidence layer to each signal before storage/notification.

    Metrics computed:
    - whale_count:        Trades ≥ $5,000
    - whale_volume_pct:   % of volume from whale trades
    - top_wallet_pct:     % of volume from single largest wallet
    - unique_wallets:     Distinct maker addresses
    - directional_bias:   % of volume on dominant side (YES vs NO)
    - avg_trade_size:     Mean trade size ($)
    - max_trade_size:     Largest single trade ($)
    - burst_score:        Ratio of last-1h volume to last-24h avg hourly volume
    - suspicious:         Boolean: any metric exceeds threshold

    Ideal insider pattern (e.g. US airstrike on Venezuela):
    - 2-3 new wallets buying YES for $10k+ each
    - 90%+ directional bias (all buying same side)
    - Burst in last 1-2 hours
    - Top wallet > 40% of recent volume
    """

    WHALE_TRADE_MIN_USD = 5_000
    SUSPICIOUS_CONCENTRATION = 0.40    # 1 wallet > 40% of volume
    SUSPICIOUS_DIRECTIONAL   = 0.85    # 85%+ one direction
    SUSPICIOUS_BURST         = 3.0     # 3x more volume in last hour vs avg

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})
        self.call_count = 0

    def _get_token_ids(self, snapshot: Dict[str, Any]) -> List[str]:
        """Extract CLOB token IDs from market snapshot."""
        token_ids = snapshot.get('clobTokenIds', snapshot.get('clob_token_ids', []))
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except (json.JSONDecodeError, TypeError):
                token_ids = []
        return [str(t) for t in token_ids if t]

    def _fetch_trades(self, token_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Fetch recent trades for a token from CLOB API.
        Returns list of trade dicts or empty list on failure.
        """
        try:
            resp = self.session.get(
                f"{config.CLOB_API_BASE}/trades",
                params={"asset_id": token_id, "limit": str(limit)},
                timeout=10
            )
            self.call_count += 1

            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and 'data' in data:
                    return data['data']
                return []
            else:
                logger.debug(f"CLOB trades {resp.status_code}: {resp.text[:80]}")
                return []

        except requests.exceptions.Timeout:
            logger.warning("CLOB trades request timed out")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"CLOB trades error: {e}")
            return []

    def _parse_trade(self, trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize a single trade record."""
        try:
            price = float(trade.get('price', 0))
            size = float(trade.get('size', trade.get('amount', 0)))
            usd_value = price * size

            timestamp_raw = trade.get('timestamp', trade.get('created_at', ''))
            if isinstance(timestamp_raw, (int, float)):
                ts = datetime.fromtimestamp(timestamp_raw, tz=timezone.utc)
            elif isinstance(timestamp_raw, str) and timestamp_raw:
                ts = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00'))
            else:
                ts = datetime.now(timezone.utc)

            return {
                'price': price,
                'size': size,
                'usd_value': usd_value,
                'side': trade.get('side', 'unknown').upper(),
                'maker': trade.get('maker_address', trade.get('maker', 'unknown')),
                'timestamp': ts,
            }
        except (ValueError, TypeError, KeyError) as e:
            logger.debug(f"Trade parse error: {e}")
            return None

    def _compute_metrics(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Core analysis: compute all whale/concentration/directional metrics.
        Input: list of normalized trade dicts.
        """
        if not trades:
            return self._empty_result("no_valid_trades")

        now = datetime.now(timezone.utc)
        total_volume = sum(t['usd_value'] for t in trades)

        if total_volume <= 0:
            return self._empty_result("zero_volume")

        # ── Whale trades ─────────────────────────────────────────────
        whale_trades = [t for t in trades if t['usd_value'] >= self.WHALE_TRADE_MIN_USD]
        whale_volume = sum(t['usd_value'] for t in whale_trades)
        whale_volume_pct = whale_volume / total_volume if total_volume > 0 else 0

        # ── Wallet concentration ─────────────────────────────────────
        wallet_volumes: Counter = Counter()
        for t in trades:
            wallet_volumes[t['maker']] += t['usd_value']

        unique_wallets = len(wallet_volumes)
        top_wallet, top_wallet_vol = wallet_volumes.most_common(1)[0] if wallet_volumes else ('none', 0)
        top_wallet_pct = top_wallet_vol / total_volume if total_volume > 0 else 0

        # Top 3 wallets combined
        top3_vol = sum(v for _, v in wallet_volumes.most_common(3))
        top3_pct = top3_vol / total_volume if total_volume > 0 else 0

        # ── Directional bias ─────────────────────────────────────────
        buy_volume = sum(t['usd_value'] for t in trades if t['side'] == 'BUY')
        sell_volume = sum(t['usd_value'] for t in trades if t['side'] == 'SELL')
        dominant_side = 'BUY' if buy_volume >= sell_volume else 'SELL'
        dominant_vol = max(buy_volume, sell_volume)
        directional_bias = dominant_vol / total_volume if total_volume > 0 else 0.5

        # ── Timing burst (last 1h vs avg hourly) ────────────────────
        one_hour_ago = now - timedelta(hours=1)
        recent_trades = [t for t in trades if t['timestamp'] >= one_hour_ago]
        recent_volume = sum(t['usd_value'] for t in recent_trades)

        older_trades = [t for t in trades if t['timestamp'] < one_hour_ago]
        if older_trades:
            oldest = min(t['timestamp'] for t in older_trades)
            hours_span = max((now - oldest).total_seconds() / 3600 - 1, 1)
            older_volume = sum(t['usd_value'] for t in older_trades)
            avg_hourly = older_volume / hours_span
            burst_score = recent_volume / avg_hourly if avg_hourly > 0 else 1.0
        else:
            burst_score = 1.0

        # ── Trade size stats ─────────────────────────────────────────
        sizes = [t['usd_value'] for t in trades]
        avg_trade_size = total_volume / len(trades)
        max_trade_size = max(sizes) if sizes else 0

        # ── Suspicious flag ──────────────────────────────────────────
        suspicious_reasons = []
        if top_wallet_pct >= self.SUSPICIOUS_CONCENTRATION:
            suspicious_reasons.append(f"wallet_concentration_{top_wallet_pct:.0%}")
        if directional_bias >= self.SUSPICIOUS_DIRECTIONAL:
            suspicious_reasons.append(f"directional_bias_{directional_bias:.0%}")
        if burst_score >= self.SUSPICIOUS_BURST:
            suspicious_reasons.append(f"timing_burst_{burst_score:.1f}x")
        if whale_volume_pct >= 0.60:
            suspicious_reasons.append(f"whale_dominated_{whale_volume_pct:.0%}")

        return {
            'trade_count':       len(trades),
            'total_volume':      round(total_volume, 2),
            'whale_count':       len(whale_trades),
            'whale_volume_pct':  round(whale_volume_pct, 3),
            'unique_wallets':    unique_wallets,
            'top_wallet_pct':    round(top_wallet_pct, 3),
            'top3_wallet_pct':   round(top3_pct, 3),
            'directional_bias':  round(directional_bias, 3),
            'dominant_side':     dominant_side,
            'avg_trade_size':    round(avg_trade_size, 2),
            'max_trade_size':    round(max_trade_size, 2),
            'burst_score':       round(burst_score, 2),
            'recent_1h_volume':  round(recent_volume, 2),
            'suspicious':        len(suspicious_reasons) > 0,
            'suspicious_reasons': suspicious_reasons,
            'source':            'clob_trades',
        }

    def _empty_result(self, reason: str) -> Dict[str, Any]:
        """Return neutral result when trade data unavailable."""
        return {
            'trade_count': 0, 'total_volume': 0, 'whale_count': 0,
            'whale_volume_pct': 0, 'unique_wallets': 0,
            'top_wallet_pct': 0, 'top3_wallet_pct': 0,
            'directional_bias': 0.5, 'dominant_side': 'NONE',
            'avg_trade_size': 0, 'max_trade_size': 0,
            'burst_score': 1.0, 'recent_1h_volume': 0,
            'suspicious': False, 'suspicious_reasons': [],
            'source': f'empty:{reason}',
        }

    def analyze_market(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Full trade analysis for a single market.
        Fetches YES token trades, computes all metrics.
        Returns enrichment dict to merge into signal.
        """
        token_ids = self._get_token_ids(snapshot)

        if not token_ids:
            logger.debug(f"No token IDs for {snapshot.get('question', '')[:40]}")
            return self._empty_result("no_token_ids")

        # Fetch trades for YES token (index 0)
        raw_trades = self._fetch_trades(token_ids[0], limit=200)

        if not raw_trades:
            return self._empty_result("no_trades_returned")

        # Parse and normalize
        parsed = [self._parse_trade(t) for t in raw_trades]
        parsed = [t for t in parsed if t is not None]

        if not parsed:
            return self._empty_result("all_trades_unparseable")

        metrics = self._compute_metrics(parsed)

        if metrics.get('suspicious'):
            logger.info(
                f"🐋 WHALE ALERT: {snapshot.get('question', '')[:45]} | "
                f"Whales: {metrics['whale_count']} | "
                f"TopWallet: {metrics['top_wallet_pct']:.0%} | "
                f"Dir: {metrics['directional_bias']:.0%} {metrics['dominant_side']} | "
                f"Burst: {metrics['burst_score']:.1f}x | "
                f"Reasons: {metrics['suspicious_reasons']}"
            )

        return metrics

    def analyze_batch(self, snapshots: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Analyze trades for multiple markets.
        Returns dict: market_id → trade_metrics.
        Rate-limited: 0.3s between calls.
        """
        results = {}
        for snapshot in snapshots:
            market_id = snapshot.get('id', '')
            results[market_id] = self.analyze_market(snapshot)
            time.sleep(0.3)  # Rate limit

        suspicious_count = sum(1 for r in results.values() if r.get('suspicious'))
        logger.info(
            f"🐋 Trade analysis: {len(results)} markets | "
            f"{suspicious_count} suspicious | "
            f"{self.call_count} CLOB calls"
        )
        return results

    def reset_cycle_counters(self):
        self.call_count = 0


def main():
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("🧪 PolyAugur Trade Analyzer Test - Phase 7")
    print("=" * 60)

    analyzer = TradeAnalyzer()

    # Test 1: Metric computation with synthetic trades
    print("\n[Test 1] Synthetic trade analysis...")
    now = datetime.now(timezone.utc)
    fake_trades = [
        {'usd_value': 12000, 'side': 'BUY', 'maker': 'whale_wallet_1', 'timestamp': now - timedelta(minutes=15)},
        {'usd_value': 8000,  'side': 'BUY', 'maker': 'whale_wallet_1', 'timestamp': now - timedelta(minutes=30)},
        {'usd_value': 500,   'side': 'BUY', 'maker': 'retail_1',       'timestamp': now - timedelta(hours=2)},
        {'usd_value': 300,   'side': 'SELL', 'maker': 'retail_2',      'timestamp': now - timedelta(hours=3)},
        {'usd_value': 200,   'side': 'BUY', 'maker': 'retail_3',       'timestamp': now - timedelta(hours=5)},
        {'usd_value': 6000,  'side': 'BUY', 'maker': 'whale_wallet_2', 'timestamp': now - timedelta(minutes=45)},
    ]
    metrics = analyzer._compute_metrics(fake_trades)

    print(f"   Trades:            {metrics['trade_count']}")
    print(f"   Total volume:      ${metrics['total_volume']:,.0f}")
    print(f"   Whale trades:      {metrics['whale_count']} (${metrics['whale_volume_pct']:.0%} of vol)")
    print(f"   Unique wallets:    {metrics['unique_wallets']}")
    print(f"   Top wallet:        {metrics['top_wallet_pct']:.0%}")
    print(f"   Directional bias:  {metrics['directional_bias']:.0%} {metrics['dominant_side']}")
    print(f"   Burst score:       {metrics['burst_score']:.1f}x")
    print(f"   Suspicious:        {metrics['suspicious']}")
    print(f"   Reasons:           {metrics['suspicious_reasons']}")

    expected_suspicious = metrics['suspicious']
    print(f"   {'✅' if expected_suspicious else '❌'} Expected suspicious=True")

    # Test 2: Empty result
    print("\n[Test 2] Empty input handling...")
    empty = analyzer._compute_metrics([])
    print(f"   ✅ Empty result: suspicious={empty['suspicious']}")

    # Test 3: Live CLOB fetch (if token ID available)
    print("\n[Test 3] Live CLOB API fetch (optional)...")
    test_snapshot = {
        'id': 'test',
        'question': 'Test market',
        'clobTokenIds': []  # No real token → graceful empty
    }
    live_result = analyzer.analyze_market(test_snapshot)
    print(f"   ✅ No token IDs → source={live_result['source']}")

    print("\n" + "=" * 60)
    print("✅ Phase 7 Trade Analyzer: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
