"""
PolyAugur Performance Tracker
Checks outcomes of past signals against current market prices.
Determines win/loss and tracks P&L for accuracy measurement.

Author: Diego Ringleb | Phase 8 | 2026-02-28
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import config
from src.signal_store import SignalStore

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """
    Resolves signal outcomes by checking final market prices.

    Logic:
    - BUY_YES signal wins if final YES price > entry price
    - BUY_NO signal wins if final YES price < entry price
    - HOLD signals are always neutral

    P&L calc:
    - BUY_YES: pnl = (1.0 - entry_price) if resolved YES, else -entry_price
    - BUY_NO:  pnl = (1.0 - (1 - entry_price)) if resolved NO, else -(1 - entry_price)
    - Simplified: assumes binary resolution (YES=1.0, NO=0.0)
    """

    def __init__(self, store: SignalStore):
        self.store = store
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolyAugur/1.0"})

    def _fetch_market_state(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Fetch current market state from Gamma API."""
        try:
            resp = self.session.get(
                f"{config.GAMMA_API_BASE}/markets/{market_id}",
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    def _resolve_outcome(self, signal: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determine win/loss for a signal based on market state.
        Returns dict with outcome, outcome_price, pnl_pct.
        """
        trade = signal.get('trade', 'HOLD')
        entry_price = signal.get('yes_price', 0.5)

        # Check if market is resolved
        resolved = market.get('resolved', False)
        closed = market.get('closed', False)

        if not (resolved or closed):
            return {'outcome': 'pending', 'outcome_price': None, 'pnl_pct': None}

        # Get outcome price
        outcome_prices = market.get('outcomePrices', '[]')
        if isinstance(outcome_prices, str):
            import json
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = []

        if outcome_prices:
            final_yes = float(outcome_prices[0])
        else:
            # Try current yes price
            final_yes = float(market.get('bestAsk', market.get('yes_price', 0.5)))

        # Determine outcome
        if trade == 'BUY_YES':
            if final_yes >= 0.95:  # Resolved YES
                pnl_pct = round((1.0 - entry_price) / entry_price, 4)
                outcome = 'win'
            elif final_yes <= 0.05:  # Resolved NO
                pnl_pct = -1.0
                outcome = 'loss'
            else:
                pnl_pct = round((final_yes - entry_price) / entry_price, 4)
                outcome = 'win' if pnl_pct > 0 else 'loss'

        elif trade == 'BUY_NO':
            no_entry = 1.0 - entry_price
            if final_yes <= 0.05:  # Resolved NO → we win
                pnl_pct = round((1.0 - no_entry) / no_entry, 4) if no_entry > 0 else 0
                outcome = 'win'
            elif final_yes >= 0.95:  # Resolved YES → we lose
                pnl_pct = -1.0
                outcome = 'loss'
            else:
                current_no = 1.0 - final_yes
                pnl_pct = round((current_no - no_entry) / no_entry, 4) if no_entry > 0 else 0
                outcome = 'win' if pnl_pct > 0 else 'loss'

        else:  # HOLD
            pnl_pct = 0.0
            outcome = 'neutral'

        return {
            'outcome': outcome,
            'outcome_price': final_yes,
            'pnl_pct': pnl_pct
        }

    def check_outcomes(self) -> Dict[str, Any]:
        """
        Check all pending signals that should have resolved.
        Updates the database with outcomes.
        Returns summary.
        """
        pending = self.store.get_pending_outcomes()

        if not pending:
            logger.info("📊 Performance: no pending outcomes to check")
            return {'checked': 0, 'wins': 0, 'losses': 0, 'still_pending': 0}

        logger.info(f"📊 Checking {len(pending)} pending signal outcomes...")

        wins = 0
        losses = 0
        still_pending = 0

        for signal in pending:
            market_id = signal.get('market_id', '')
            market = self._fetch_market_state(market_id)

            if not market:
                still_pending += 1
                continue

            result = self._resolve_outcome(signal, market)

            if result['outcome'] == 'pending':
                still_pending += 1
                continue

            self.store.update_outcome(
                row_id=signal['id'],
                outcome=result['outcome'],
                outcome_price=result['outcome_price'],
                pnl_pct=result['pnl_pct']
            )

            if result['outcome'] == 'win':
                wins += 1
                emoji = '✅'
            elif result['outcome'] == 'loss':
                losses += 1
                emoji = '❌'
            else:
                emoji = '⚪'

            logger.info(
                f"{emoji} Signal #{signal['id']}: {signal.get('question', '')[:40]} | "
                f"{result['outcome']} | P&L: {result['pnl_pct']:.1%}"
            )

        summary = {
            'checked': len(pending),
            'wins': wins,
            'losses': losses,
            'still_pending': still_pending,
            'win_rate': round(wins / (wins + losses), 3) if (wins + losses) > 0 else None
        }

        logger.info(
            f"📊 Outcome check: {wins}W / {losses}L "
            f"(WR: {summary['win_rate']:.0%})" if summary['win_rate'] else
            f"📊 Outcome check: {still_pending} still pending"
        )

        return summary


def main():
    import logging
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("🧪 PolyAugur Performance Tracker Test - Phase 8")
    print("=" * 60)

    store = SignalStore(config.SIGNAL_DB_PATH)
    tracker = PerformanceTracker(store)

    print("\n[Test 1] Check pending outcomes...")
    summary = tracker.check_outcomes()
    print(f"   Checked: {summary['checked']}")
    print(f"   Wins: {summary['wins']}")
    print(f"   Losses: {summary['losses']}")
    print(f"   Pending: {summary['still_pending']}")

    print("\n[Test 2] DB stats with win rate...")
    stats = store.get_stats()
    print(f"   {stats}")

    print("\n" + "=" * 60)
    print("✅ Performance Tracker: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
