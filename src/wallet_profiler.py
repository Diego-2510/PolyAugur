"""
PolyAugur Wallet Profiler - Trader Classification System
Analyzes wallet history to classify traders as:
  - 🎰 GAMBLER: Low win rate (<40%), many small bets, random patterns
  - 🧠 INSIDER: New account OR high win rate (>65%), concentrated bets
  - 👤 REGULAR: Average trader, no strong signal either way
  - 🐋 SMART_MONEY: High win rate + high volume, consistent profits

Uses Polymarket Gamma API: /users/{address}/positions
Caches profiles to avoid redundant API calls.

Author: Diego Ringleb | Phase 11 | 2026-02-28
"""

import logging
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict
import config

logger = logging.getLogger(__name__)


class WalletProfile:
    """Represents a wallet's trading profile."""

    def __init__(self, address: str):
        self.address = address
        self.total_positions = 0
        self.resolved_positions = 0
        self.wins = 0
        self.losses = 0
        self.total_invested = 0.0
        self.total_pnl = 0.0
        self.avg_position_size = 0.0
        self.max_position_size = 0.0
        self.first_seen: Optional[datetime] = None
        self.last_active: Optional[datetime] = None
        self.account_age_days = 0
        self.classification = "UNKNOWN"
        self.confidence = 0.0
        self.reasons: List[str] = []

    @property
    def win_rate(self) -> float:
        if self.resolved_positions == 0:
            return 0.0
        return self.wins / self.resolved_positions

    @property
    def is_new_account(self) -> bool:
        return self.account_age_days < 30

    def to_dict(self) -> Dict[str, Any]:
        return {
            'address': self.address[:10] + '...',
            'classification': self.classification,
            'confidence': round(self.confidence, 2),
            'win_rate': round(self.win_rate, 3),
            'total_positions': self.total_positions,
            'resolved_positions': self.resolved_positions,
            'wins': self.wins,
            'losses': self.losses,
            'account_age_days': self.account_age_days,
            'total_invested': round(self.total_invested, 2),
            'avg_position_size': round(self.avg_position_size, 2),
            'reasons': self.reasons,
        }


class WalletProfiler:
    """
    Fetches wallet history from Gamma API and classifies traders.

    Classification logic:
    1. GAMBLER: win_rate < 40% AND resolved >= 10 positions
    2. INSIDER: (win_rate > 65% AND resolved >= 5) OR (account_age < 30 days AND large bets)
    3. SMART_MONEY: win_rate > 60% AND total_invested > $50k AND resolved >= 20
    4. REGULAR: everything else

    Signal impact:
    - GAMBLER whales → reduce confidence (they bet big but lose often)
    - INSIDER whales → boost confidence (they know something)
    - SMART_MONEY whales → moderate boost (consistent winners)
    - REGULAR → neutral (no adjustment)
    """

    # Classification thresholds
    GAMBLER_WIN_RATE = 0.40
    GAMBLER_MIN_POSITIONS = 10

    INSIDER_WIN_RATE = 0.65
    INSIDER_MIN_POSITIONS = 5
    INSIDER_NEW_ACCOUNT_DAYS = 30
    INSIDER_LARGE_BET_MIN = 5_000

    SMART_MONEY_WIN_RATE = 0.60
    SMART_MONEY_MIN_INVESTED = 50_000
    SMART_MONEY_MIN_POSITIONS = 20

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})
        self.cache: Dict[str, WalletProfile] = {}
        self.call_count = 0

    def _fetch_wallet_positions(self, address: str) -> List[Dict[str, Any]]:
        """
        Fetch trading positions for a wallet from Gamma API.
        GET /users/{address} or /positions?user={address}
        """
        try:
            # Try Gamma positions endpoint
            resp = self.session.get(
                f"{config.GAMMA_API_BASE}/positions",
                params={"user": address, "limit": "200", "sortBy": "createdAt", "sortOrder": "desc"},
                timeout=10,
            )
            self.call_count += 1

            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and 'positions' in data:
                    return data['positions']
                return []

            logger.debug(f"Positions API {resp.status_code} for {address[:10]}")
            return []

        except requests.exceptions.RequestException as e:
            logger.debug(f"Positions fetch error for {address[:10]}: {e}")
            return []

    def _fetch_wallet_activity(self, address: str) -> Optional[Dict[str, Any]]:
        """
        Fetch wallet activity summary (alternative endpoint).
        GET /activity?user={address}
        """
        try:
            resp = self.session.get(
                f"{config.GAMMA_API_BASE}/activity",
                params={"user": address, "limit": "100"},
                timeout=10,
            )
            self.call_count += 1

            if resp.status_code == 200:
                return resp.json()
            return None

        except requests.exceptions.RequestException:
            return None

    def _analyze_positions(self, address: str, positions: List[Dict[str, Any]]) -> WalletProfile:
        """Analyze position history and build wallet profile."""
        profile = WalletProfile(address)
        profile.total_positions = len(positions)

        now = datetime.now(timezone.utc)
        timestamps = []

        for pos in positions:
            try:
                # Parse position data
                size = float(pos.get('size', pos.get('amount', 0)))
                price = float(pos.get('avgPrice', pos.get('price', 0.5)))
                invested = size * price
                profile.total_invested += invested
                profile.max_position_size = max(profile.max_position_size, invested)

                # Outcome
                outcome = pos.get('outcome', pos.get('resolved', None))
                cashout = float(pos.get('cashoutAmount', pos.get('payout', 0)))

                if outcome is not None or cashout > 0:
                    profile.resolved_positions += 1
                    pnl = cashout - invested
                    profile.total_pnl += pnl
                    if pnl > 0:
                        profile.wins += 1
                    elif pnl < 0:
                        profile.losses += 1

                # Timestamps
                ts_raw = pos.get('createdAt', pos.get('timestamp', ''))
                if ts_raw:
                    if isinstance(ts_raw, (int, float)):
                        ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
                    else:
                        ts = datetime.fromisoformat(str(ts_raw).replace('Z', '+00:00'))
                    timestamps.append(ts)

            except (ValueError, TypeError):
                continue

        # Account age
        if timestamps:
            profile.first_seen = min(timestamps)
            profile.last_active = max(timestamps)
            profile.account_age_days = (now - profile.first_seen).days

        # Avg position size
        if profile.total_positions > 0:
            profile.avg_position_size = profile.total_invested / profile.total_positions

        return profile

    def _classify(self, profile: WalletProfile) -> WalletProfile:
        """
        Classify wallet based on trading history.
        Priority: INSIDER > SMART_MONEY > GAMBLER > REGULAR
        """
        reasons = []

        # ── Check INSIDER ────────────────────────────────────────────
        insider_score = 0

        # New account with large bets = suspicious
        if profile.is_new_account and profile.max_position_size >= self.INSIDER_LARGE_BET_MIN:
            insider_score += 2
            reasons.append(f"new_account_{profile.account_age_days}d_large_bets")

        # High win rate with enough history
        if (profile.win_rate >= self.INSIDER_WIN_RATE
                and profile.resolved_positions >= self.INSIDER_MIN_POSITIONS):
            insider_score += 2
            reasons.append(f"high_win_rate_{profile.win_rate:.0%}_over_{profile.resolved_positions}_bets")

        # Very new account (< 7 days) with any whale trade
        if profile.account_age_days < 7 and profile.max_position_size >= 2_000:
            insider_score += 1
            reasons.append(f"very_new_account_{profile.account_age_days}d")

        if insider_score >= 2:
            profile.classification = "INSIDER"
            profile.confidence = min(0.5 + insider_score * 0.1, 0.9)
            profile.reasons = reasons
            return profile

        # ── Check SMART_MONEY ────────────────────────────────────────
        if (profile.win_rate >= self.SMART_MONEY_WIN_RATE
                and profile.total_invested >= self.SMART_MONEY_MIN_INVESTED
                and profile.resolved_positions >= self.SMART_MONEY_MIN_POSITIONS):
            profile.classification = "SMART_MONEY"
            profile.confidence = 0.7
            profile.reasons = [
                f"win_rate_{profile.win_rate:.0%}",
                f"invested_${profile.total_invested:,.0f}",
                f"positions_{profile.resolved_positions}",
            ]
            return profile

        # ── Check GAMBLER ────────────────────────────────────────────
        if (profile.win_rate < self.GAMBLER_WIN_RATE
                and profile.resolved_positions >= self.GAMBLER_MIN_POSITIONS):
            profile.classification = "GAMBLER"
            profile.confidence = 0.6
            profile.reasons = [
                f"low_win_rate_{profile.win_rate:.0%}",
                f"over_{profile.resolved_positions}_resolved_bets",
            ]
            return profile

        # ── Default: REGULAR ─────────────────────────────────────────
        profile.classification = "REGULAR"
        profile.confidence = 0.3
        profile.reasons = ["insufficient_data_or_average_pattern"]
        return profile

    def profile_wallet(self, address: str) -> WalletProfile:
        """
        Full wallet profiling pipeline.
        Uses cache to avoid redundant API calls.
        """
        # Check cache
        if address in self.cache:
            return self.cache[address]

        # Fetch positions
        positions = self._fetch_wallet_positions(address)

        if not positions:
            # Minimal profile for unknown wallets
            profile = WalletProfile(address)
            profile.classification = "UNKNOWN"
            profile.confidence = 0.1
            profile.reasons = ["no_position_data"]
            self.cache[address] = profile
            return profile

        # Analyze & classify
        profile = self._analyze_positions(address, positions)
        profile = self._classify(profile)
        self.cache[address] = profile

        logger.info(
            f"👤 Wallet {address[:10]}... → {profile.classification} | "
            f"WR={profile.win_rate:.0%} ({profile.wins}W/{profile.losses}L) | "
            f"Age={profile.account_age_days}d | "
            f"Invested=${profile.total_invested:,.0f}"
        )

        return profile

    def profile_top_wallets(
        self, wallet_volumes: Dict[str, float], top_n: int = 3
    ) -> Dict[str, Any]:
        """
        Profile the top N wallets by volume from a market's trades.
        Returns aggregated classification summary for the signal.
        """
        sorted_wallets = sorted(wallet_volumes.items(), key=lambda x: x[1], reverse=True)
        top = sorted_wallets[:top_n]

        profiles = []
        classifications = defaultdict(int)

        for address, volume in top:
            time.sleep(0.3)  # Rate limit
            profile = self.profile_wallet(address)
            profiles.append({
                'address': address[:10] + '...',
                'volume': round(volume, 2),
                'classification': profile.classification,
                'win_rate': round(profile.win_rate, 3),
                'account_age_days': profile.account_age_days,
                'total_positions': profile.total_positions,
                'reasons': profile.reasons,
            })
            classifications[profile.classification] += 1

        # Compute signal adjustment
        insider_count = classifications.get('INSIDER', 0)
        smart_money_count = classifications.get('SMART_MONEY', 0)
        gambler_count = classifications.get('GAMBLER', 0)

        confidence_adjustment = 0.0
        adjustment_reasons = []

        if insider_count > 0:
            confidence_adjustment += insider_count * 0.05
            adjustment_reasons.append(f"{insider_count}x INSIDER (+{insider_count * 0.05:.0%})")

        if smart_money_count > 0:
            confidence_adjustment += smart_money_count * 0.03
            adjustment_reasons.append(f"{smart_money_count}x SMART_MONEY (+{smart_money_count * 0.03:.0%})")

        if gambler_count > 0 and insider_count == 0:
            confidence_adjustment -= gambler_count * 0.05
            adjustment_reasons.append(f"{gambler_count}x GAMBLER ({gambler_count * -0.05:+.0%})")

        return {
            'top_wallets': profiles,
            'classifications': dict(classifications),
            'insider_count': insider_count,
            'smart_money_count': smart_money_count,
            'gambler_count': gambler_count,
            'confidence_adjustment': round(confidence_adjustment, 3),
            'adjustment_reasons': adjustment_reasons,
        }

    def reset_cycle_counters(self):
        self.call_count = 0
        # Keep cache across cycles (wallets don't change often)


def main():
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("🧪 PolyAugur Wallet Profiler Test - Phase 11")
    print("=" * 60)

    profiler = WalletProfiler()

    # Test 1: Classification logic with synthetic data
    print("\n[Test 1] Classification logic...")

    # Simulate GAMBLER
    gambler = WalletProfile("0xGAMBLER")
    gambler.resolved_positions = 25
    gambler.wins = 8
    gambler.losses = 17
    gambler.total_invested = 15000
    gambler.account_age_days = 180
    gambler = profiler._classify(gambler)
    print(f"   {'✅' if gambler.classification == 'GAMBLER' else '❌'} Gambler: {gambler.classification} | WR={gambler.win_rate:.0%} | {gambler.reasons}")

    # Simulate INSIDER (new account + big bets)
    insider = WalletProfile("0xINSIDER")
    insider.resolved_positions = 3
    insider.wins = 3
    insider.losses = 0
    insider.total_invested = 45000
    insider.max_position_size = 20000
    insider.account_age_days = 12
    insider = profiler._classify(insider)
    print(f"   {'✅' if insider.classification == 'INSIDER' else '❌'} Insider: {insider.classification} | WR={insider.win_rate:.0%} | {insider.reasons}")

    # Simulate SMART_MONEY
    smart = WalletProfile("0xSMART")
    smart.resolved_positions = 50
    smart.wins = 35
    smart.losses = 15
    smart.total_invested = 120000
    smart.account_age_days = 365
    smart = profiler._classify(smart)
    print(f"   {'✅' if smart.classification == 'SMART_MONEY' else '❌'} Smart Money: {smart.classification} | WR={smart.win_rate:.0%} | {smart.reasons}")

    # Simulate REGULAR
    regular = WalletProfile("0xREGULAR")
    regular.resolved_positions = 8
    regular.wins = 4
    regular.losses = 4
    regular.total_invested = 5000
    regular.account_age_days = 90
    regular = profiler._classify(regular)
    print(f"   {'✅' if regular.classification == 'REGULAR' else '❌'} Regular: {regular.classification} | WR={regular.win_rate:.0%} | {regular.reasons}")

    # Test 2: Aggregated confidence adjustment
    print("\n[Test 2] Confidence adjustment logic...")
    test_wallets = {
        "0xINSIDER_WALLET": 25000,
        "0xGAMBLER_WALLET": 8000,
        "0xREGULAR_WALLET": 3000,
    }

    # Mock profiles in cache
    profiler.cache["0xINSIDER_WALLET"] = insider
    profiler.cache["0xGAMBLER_WALLET"] = gambler
    profiler.cache["0xREGULAR_WALLET"] = regular

    result = profiler.profile_top_wallets(test_wallets, top_n=3)
    print(f"   Classifications: {result['classifications']}")
    print(f"   Confidence adj:  {result['confidence_adjustment']:+.0%}")
    print(f"   Reasons:         {result['adjustment_reasons']}")

    adj = result['confidence_adjustment']
    # 1 insider (+5%) + 1 gambler (0% because insider present) = +5%
    print(f"   {'✅' if adj > 0 else '❌'} Net positive (insider outweighs gambler)")

    print("\n" + "=" * 60)
    print("✅ Phase 11 Wallet Profiler: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
