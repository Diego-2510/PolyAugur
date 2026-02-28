"""
PolyAugur Dashboard - Signal Explorer & Export
CLI tool to query, filter, and export signals from the SQLite database.

Usage:
    python -m src.dashboard                    # Last 24h signals
    python -m src.dashboard --hours 72         # Last 72h
    python -m src.dashboard --whales           # Only whale signals
    python -m src.dashboard --export csv       # Export to CSV
    python -m src.dashboard --export html      # Export to HTML report
    python -m src.dashboard --performance      # Win/loss breakdown

Author: Diego Ringleb | Phase 9 | 2026-02-28
"""

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
import config
from src.signal_store import SignalStore

logger = logging.getLogger(__name__)


class Dashboard:
    """Query and visualize signal history from SQLite."""

    def __init__(self):
        self.store = SignalStore(config.SIGNAL_DB_PATH)

    def get_signals(self, hours: int = 24, whales_only: bool = False) -> List[Dict[str, Any]]:
        signals = self.store.get_recent(hours=hours)
        if whales_only:
            signals = [s for s in signals if s.get('trade_suspicious')]
        return signals

    def print_signals(self, signals: List[Dict[str, Any]]):
        if not signals:
            print("\n   No signals found for the given filters.")
            return

        print(f"\n{'='*90}")
        print(f"  {'#':>3}  {'Whale':5}  {'Trade':8}  {'Conf':5}  {'Boost':5}  "
              f"{'Risk':6}  {'Score':5}  {'Outcome':8}  Question")
        print(f"{'─'*90}")

        for s in signals:
            whale = "🐋" if s.get('trade_suspicious') else "  "
            conf = s.get('confidence', 0)
            boost = s.get('confidence_boost', 0)
            boost_str = f"+{boost:.0%}" if boost and boost > 0 else "    "
            outcome = s.get('outcome', 'pending')
            outcome_map = {'win': '✅ win', 'loss': '❌ loss', 'pending': '⏳ pend', 'neutral': '⚪ neut'}
            outcome_str = outcome_map.get(outcome, outcome)

            print(
                f"  {s.get('id', 0):>3}  {whale:5}  {s.get('trade', 'HOLD'):8}  "
                f"{conf:.0%}   {boost_str:5}  {s.get('risk_level', '?'):6}  "
                f"{s.get('anomaly_score', 0):.2f}   {outcome_str:8}  "
                f"{s.get('question', '')[:40]}"
            )

        print(f"{'='*90}")
        print(f"  Total: {len(signals)} signals")

    def print_performance(self):
        stats = self.store.get_stats()
        print(f"\n{'='*50}")
        print(f"  📊 PolyAugur Performance Dashboard")
        print(f"{'='*50}")
        print(f"  Total signals:      {stats['total_signals']}")
        print(f"  Signals (24h):      {stats['signals_24h']}")
        print(f"  Whale signals:      {stats.get('whale_signals', 0)}")
        print(f"  Telegram unsent:    {stats['telegram_unsent']}")
        print(f"{'─'*50}")
        print(f"  ✅ Wins:            {stats.get('wins', 0)}")
        print(f"  ❌ Losses:          {stats.get('losses', 0)}")

        wr = stats.get('win_rate')
        if wr is not None:
            print(f"  🎯 Win Rate:        {wr:.1%}")
        else:
            print(f"  🎯 Win Rate:        N/A (no resolved signals)")

        # Whale vs non-whale performance
        with self.store._get_conn() as conn:
            whale_wins = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE trade_suspicious=1 AND outcome='win'"
            ).fetchone()['n']
            whale_losses = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE trade_suspicious=1 AND outcome='loss'"
            ).fetchone()['n']
            normal_wins = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE trade_suspicious=0 AND outcome='win'"
            ).fetchone()['n']
            normal_losses = conn.execute(
                "SELECT COUNT(*) as n FROM signals WHERE trade_suspicious=0 AND outcome='loss'"
            ).fetchone()['n']

        whale_total = whale_wins + whale_losses
        normal_total = normal_wins + normal_losses

        print(f"{'─'*50}")
        print(f"  🐋 Whale signal WR:  ", end="")
        if whale_total > 0:
            print(f"{whale_wins}/{whale_total} = {whale_wins/whale_total:.1%}")
        else:
            print("N/A")

        print(f"  📊 Normal signal WR: ", end="")
        if normal_total > 0:
            print(f"{normal_wins}/{normal_total} = {normal_wins/normal_total:.1%}")
        else:
            print("N/A")

        # Average P&L
        with self.store._get_conn() as conn:
            avg_pnl = conn.execute(
                "SELECT AVG(profit_loss_pct) as avg FROM signals WHERE outcome IN ('win', 'loss')"
            ).fetchone()['avg']

        if avg_pnl is not None:
            print(f"  💰 Avg P&L:         {avg_pnl:+.1%}")

        print(f"{'='*50}")

    def export_csv(self, signals: List[Dict[str, Any]], filename: str = None) -> str:
        os.makedirs("exports", exist_ok=True)
        if not filename:
            filename = f"exports/signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        if not signals:
            print("No signals to export.")
            return ""

        fields = [
            'id', 'detected_at', 'question', 'trade', 'confidence',
            'confidence_boost', 'anomaly_score', 'anomaly_type', 'risk_level',
            'yes_price', 'volume_24hr', 'spike_ratio', 'days_to_close',
            'holding_hours', 'position_size_pct',
            'whale_count', 'whale_volume_pct', 'top_wallet_pct',
            'unique_wallets', 'directional_bias', 'dominant_side',
            'burst_score', 'trade_suspicious', 'suspicious_reasons',
            'outcome', 'profit_loss_pct', 'reasoning'
        ]

        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for s in signals:
                writer.writerow(s)

        print(f"📁 Exported {len(signals)} signals → {filename}")
        return filename

    def export_html(self, signals: List[Dict[str, Any]], filename: str = None) -> str:
        os.makedirs("exports", exist_ok=True)
        if not filename:
            filename = f"exports/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

        stats = self.store.get_stats()

        rows_html = ""
        for s in signals:
            whale = "🐋" if s.get('trade_suspicious') else ""
            trade = s.get('trade', 'HOLD')
            trade_color = '#22c55e' if trade == 'BUY_YES' else '#ef4444' if trade == 'BUY_NO' else '#eab308'
            conf = s.get('confidence', 0)
            boost = s.get('confidence_boost', 0)
            boost_html = f" <small>(+{boost:.0%})</small>" if boost and boost > 0 else ""
            outcome = s.get('outcome', 'pending')
            outcome_color = '#22c55e' if outcome == 'win' else '#ef4444' if outcome == 'loss' else '#6b7280'

            rows_html += f"""
            <tr>
                <td>{s.get('id', '')}</td>
                <td>{s.get('detected_at', '')[:16]}</td>
                <td>{whale} {s.get('question', '')[:60]}</td>
                <td style="color:{trade_color};font-weight:bold">{trade}</td>
                <td>{conf:.0%}{boost_html}</td>
                <td>{s.get('risk_level', '')}</td>
                <td>{s.get('whale_count', 0)}</td>
                <td>{s.get('top_wallet_pct', 0):.0%}</td>
                <td>{s.get('burst_score', 1.0):.1f}x</td>
                <td style="color:{outcome_color};font-weight:bold">{outcome}</td>
                <td>{s.get('profit_loss_pct', 0) or 0:+.1%}</td>
            </tr>"""

        wr = stats.get('win_rate')
        wr_str = f"{wr:.1%}" if wr is not None else "N/A"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PolyAugur Signal Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0f172a; color: #e2e8f0; padding: 2rem; }}
        h1 {{ color: #38bdf8; }}
        .stats {{ display: flex; gap: 2rem; margin: 1rem 0 2rem; flex-wrap: wrap; }}
        .stat {{ background: #1e293b; padding: 1rem 1.5rem; border-radius: 8px; }}
        .stat-value {{ font-size: 1.8rem; font-weight: bold; color: #38bdf8; }}
        .stat-label {{ font-size: 0.85rem; color: #94a3b8; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
        th {{ background: #1e293b; padding: 0.6rem; text-align: left; font-size: 0.85rem;
              color: #94a3b8; border-bottom: 2px solid #334155; }}
        td {{ padding: 0.5rem 0.6rem; border-bottom: 1px solid #1e293b; font-size: 0.85rem; }}
        tr:hover {{ background: #1e293b; }}
        small {{ color: #22c55e; }}
    </style>
</head>
<body>
    <h1>🔮 PolyAugur Signal Report</h1>
    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>

    <div class="stats">
        <div class="stat"><div class="stat-value">{stats['total_signals']}</div><div class="stat-label">Total Signals</div></div>
        <div class="stat"><div class="stat-value">{stats['signals_24h']}</div><div class="stat-label">Last 24h</div></div>
        <div class="stat"><div class="stat-value">{stats.get('whale_signals', 0)}</div><div class="stat-label">🐋 Whale Signals</div></div>
        <div class="stat"><div class="stat-value">{wr_str}</div><div class="stat-label">Win Rate</div></div>
        <div class="stat"><div class="stat-value">{stats.get('wins', 0)}W / {stats.get('losses', 0)}L</div><div class="stat-label">Record</div></div>
    </div>

    <table>
        <thead>
            <tr>
                <th>#</th><th>Time</th><th>Market</th><th>Trade</th><th>Conf</th>
                <th>Risk</th><th>Whales</th><th>Top Wallet</th><th>Burst</th>
                <th>Outcome</th><th>P&L</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
</body>
</html>"""

        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"📁 HTML report → {filename}")
        return filename


def main():
    parser = argparse.ArgumentParser(description="PolyAugur Signal Dashboard")
    parser.add_argument('--hours', type=int, default=24, help='Show signals from last N hours')
    parser.add_argument('--whales', action='store_true', help='Only show whale signals')
    parser.add_argument('--export', choices=['csv', 'html'], help='Export format')
    parser.add_argument('--performance', action='store_true', help='Show performance stats')
    parser.add_argument('--all', action='store_true', help='Show all signals (no time limit)')
    args = parser.parse_args()

    dash = Dashboard()

    if args.performance:
        dash.print_performance()
        return

    hours = 8760 if args.all else args.hours  # 8760h = 1 year
    signals = dash.get_signals(hours=hours, whales_only=args.whales)

    if args.export == 'csv':
        dash.export_csv(signals)
    elif args.export == 'html':
        dash.export_html(signals)
    else:
        label = "whale " if args.whales else ""
        print(f"\n📊 PolyAugur Dashboard — Last {args.hours}h {label}signals")
        dash.print_signals(signals)

        if signals:
            print(f"\n💡 Export: python -m src.dashboard --hours {args.hours} --export html")


if __name__ == "__main__":
    main()
