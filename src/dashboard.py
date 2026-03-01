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

Author: Diego Ringleb | Phase 12.2 | 2026-03-01
"""

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
from urllib.parse import quote
import config
from src.signal_store import SignalStore

logger = logging.getLogger(__name__)


def _polymarket_url(signal: Dict[str, Any]) -> str:
    """
    Build a Polymarket link for a signal.
    DB has no slug, so we use a search URL with the question text.
    """
    question = signal.get('question', '')
    if question:
        return f"https://polymarket.com/browse?_q={quote(question[:80])}"
    return "https://polymarket.com"


def _fmt_vol(vol: float) -> str:
    """Format volume for display — abbreviated for large numbers."""
    if vol >= 1_000_000:
        return f"${vol / 1_000_000:.1f}M"
    elif vol >= 1_000:
        return f"${vol / 1_000:.0f}K"
    else:
        return f"${vol:,.0f}"


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
        print(f"  {'#':>3}  {'Trade':8}  {'Conf':5}  {'Boost':5}  "
              f"{'Risk':6}  {'Score':5}  {'Outcome':8}  Question")
        print(f"{'─'*90}")

        for s in signals:
            conf = s.get('confidence', 0)
            boost = s.get('confidence_boost', 0)
            boost_str = f"+{boost:.0%}" if boost and boost > 0 else "    "
            outcome = s.get('outcome', 'pending')
            outcome_map = {
                'win': '✅ win', 'loss': '❌ loss',
                'pending': '⏳ pend', 'neutral': '⚪ neut',
            }
            outcome_str = outcome_map.get(outcome, outcome)

            print(
                f"  {s.get('id', 0):>3}  {s.get('trade', 'HOLD'):8}  "
                f"{conf:.0%}   {boost_str:5}  {s.get('risk_level', '?'):6}  "
                f"{s.get('anomaly_score', 0):.2f}   {outcome_str:8}  "
                f"{s.get('question', '')[:45]}"
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
            'outcome', 'profit_loss_pct', 'reasoning',
        ]

        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for s in signals:
                writer.writerow(s)

        print(f"📁 Exported {len(signals)} signals → {filename}")
        return filename

    def export_html(self, signals: List[Dict[str, Any]], filename: str = None) -> str:
        """Generate a hackathon-ready, visually stunning HTML report."""
        os.makedirs("exports", exist_ok=True)
        if not filename:
            filename = f"exports/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

        stats = self.store.get_stats()
        wr = stats.get('win_rate')
        wr_str = f"{wr:.1%}" if wr is not None else "N/A"
        wr_color = "#22c55e" if wr and wr >= 0.5 else "#ef4444" if wr else "#94a3b8"

        # ── Compute extra stats ──────────────────────────────────────
        total_signals = len(signals)
        total_volume = sum(s.get('volume_24hr', 0) for s in signals)
        whale_signals = sum(1 for s in signals if s.get('trade_suspicious'))
        avg_conf = (
            sum(s.get('confidence', 0) for s in signals) / total_signals
            if total_signals else 0
        )

        # Trade distribution
        buy_yes = sum(1 for s in signals if s.get('trade') == 'BUY_YES')
        buy_no = sum(1 for s in signals if s.get('trade') == 'BUY_NO')
        hold = total_signals - buy_yes - buy_no

        buy_yes_pct = (buy_yes / total_signals * 100) if total_signals > 0 else 0
        buy_no_pct = (buy_no / total_signals * 100) if total_signals > 0 else 0
        hold_pct = (hold / total_signals * 100) if total_signals > 0 else 0

        # ── Distribution segments ────────────────────────────────────
        dist_segments = ""
        if buy_yes > 0:
            dist_segments += (
                f'<div class="dist-segment dist-green" style="width:{buy_yes_pct}%">'
                f'<span class="dist-count">{buy_yes}</span></div>'
            )
        if buy_no > 0:
            dist_segments += (
                f'<div class="dist-segment dist-red" style="width:{buy_no_pct}%">'
                f'<span class="dist-count">{buy_no}</span></div>'
            )
        if hold > 0:
            dist_segments += (
                f'<div class="dist-segment dist-gray" style="width:{hold_pct}%">'
                f'<span class="dist-count">{hold}</span></div>'
            )
        if not dist_segments:
            dist_segments = (
                '<div class="dist-segment dist-gray" style="width:100%">'
                '<span class="dist-count">—</span></div>'
            )

        # ── Volume display (abbreviated) ─────────────────────────────
        vol_display = _fmt_vol(total_volume)

        # ── Signal rows ──────────────────────────────────────────────
        rows_html = ""
        for s in signals:
            trade = s.get('trade', 'HOLD')
            conf = s.get('confidence', 0)
            boost = s.get('confidence_boost', 0)
            outcome = s.get('outcome', 'pending')
            pnl = s.get('profit_loss_pct', 0) or 0
            pm_url = _polymarket_url(s)

            # Trade badge
            if trade == 'BUY_YES':
                trade_badge = '<span class="badge badge-green">BUY YES</span>'
            elif trade == 'BUY_NO':
                trade_badge = '<span class="badge badge-red">BUY NO</span>'
            else:
                trade_badge = '<span class="badge badge-gray">HOLD</span>'

            # Outcome badge
            if outcome == 'win':
                outcome_badge = '<span class="badge badge-green">✅ Win</span>'
            elif outcome == 'loss':
                outcome_badge = '<span class="badge badge-red">❌ Loss</span>'
            elif outcome == 'pending':
                outcome_badge = '<span class="badge badge-blue">⏳ Pending</span>'
            else:
                outcome_badge = '<span class="badge badge-gray">⚪ Neutral</span>'

            boost_html = (
                f'<span class="boost">+{boost:.0%}</span>'
                if boost and boost > 0 else ""
            )

            # Confidence bar
            conf_bar_w = max(conf * 100, 5)
            conf_color = (
                "#22c55e" if conf >= 0.65
                else "#eab308" if conf >= 0.50
                else "#ef4444"
            )

            # P&L color
            pnl_color = (
                "#22c55e" if pnl > 0
                else "#ef4444" if pnl < 0
                else "#64748b"
            )

            # Entry price
            yes_price = s.get('yes_price', 0)
            entry_str = f"${yes_price:.2f}" if yes_price else "—"

            # Row volume (abbreviated)
            row_vol = _fmt_vol(s.get('volume_24hr', 0))

            # Anomaly type label
            anomaly_type = s.get('anomaly_type', 'mixed')

            rows_html += f"""
            <tr>
                <td class="td-id">{s.get('id', '')}</td>
                <td class="td-time">{s.get('detected_at', '')[:16]}</td>
                <td class="td-market">
                    <div class="market-name">{s.get('question', '')[:65]}</div>
                    <div class="market-meta">
                        Spike {s.get('spike_ratio', 0):.1f}x · Vol {row_vol} ·
                        Entry {entry_str} · {anomaly_type}
                    </div>
                </td>
                <td>{trade_badge}</td>
                <td class="td-conf">
                    <div class="conf-wrapper">
                        <div class="conf-bar" style="width:{conf_bar_w}%;background:{conf_color}"></div>
                        <span class="conf-text">{conf:.0%}{boost_html}</span>
                    </div>
                </td>
                <td class="td-risk">{s.get('risk_level', '—')}</td>
                <td>{outcome_badge}</td>
                <td style="color:{pnl_color};font-weight:600;font-family:'JetBrains Mono',monospace;font-size:0.8rem">{pnl:+.1%}</td>
                <td class="td-action">
                    <a href="{pm_url}" target="_blank" rel="noopener" class="btn-market">
                        Trade ↗
                    </a>
                </td>
            </tr>"""

        # ── Full HTML ────────────────────────────────────────────────
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PolyAugur — Insider Signal Intelligence</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

        :root {{
            --bg-primary: #0a0e1a;
            --bg-secondary: #111827;
            --bg-card: #1a1f35;
            --bg-card-hover: #1f2847;
            --border: #2a3050;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-blue: #3b82f6;
            --accent-cyan: #22d3ee;
            --accent-purple: #a855f7;
            --accent-green: #22c55e;
            --accent-red: #ef4444;
            --accent-yellow: #eab308;
            --glow-blue: rgba(59, 130, 246, 0.15);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }}

        body::before {{
            content: '';
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background:
                radial-gradient(ellipse 80% 50% at 20% 40%, rgba(59,130,246,0.06) 0%, transparent 50%),
                radial-gradient(ellipse 60% 40% at 80% 60%, rgba(168,85,247,0.05) 0%, transparent 50%),
                radial-gradient(ellipse 50% 30% at 50% 10%, rgba(34,211,238,0.04) 0%, transparent 50%);
            pointer-events: none;
            z-index: 0;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
            position: relative;
            z-index: 1;
        }}

        .header {{
            text-align: center;
            margin-bottom: 2.5rem;
            padding: 2.5rem 0;
        }}

        .header-logo {{
            font-size: 3rem;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.03em;
        }}

        .header-sub {{
            font-size: 1.1rem;
            color: var(--text-secondary);
            margin-top: 0.5rem;
            font-weight: 300;
            letter-spacing: 0.15em;
            text-transform: uppercase;
        }}

        .header-meta {{
            margin-top: 1rem;
            font-size: 0.8rem;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
        }}

        .live-dot {{
            display: inline-block;
            width: 8px; height: 8px;
            background: var(--accent-green);
            border-radius: 50%;
            margin-right: 6px;
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; box-shadow: 0 0 0 0 rgba(34,197,94,0.4); }}
            50% {{ opacity: 0.8; box-shadow: 0 0 0 6px rgba(34,197,94,0); }}
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}

        .stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.25rem 1.5rem;
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
        }}

        .stat-card:hover {{
            border-color: var(--accent-blue);
            box-shadow: 0 0 20px var(--glow-blue);
            transform: translateY(-2px);
        }}

        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--accent-cyan), var(--accent-blue));
            opacity: 0;
            transition: opacity 0.3s;
        }}

        .stat-card:hover::before {{ opacity: 1; }}

        .stat-value {{
            font-size: 1.8rem;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            color: var(--accent-cyan);
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-top: 0.25rem;
        }}

        .stat-icon {{
            position: absolute;
            top: 1rem; right: 1.25rem;
            font-size: 1.5rem;
            opacity: 0.3;
        }}

        .stat-green .stat-value {{ color: var(--accent-green); }}
        .stat-red .stat-value {{ color: var(--accent-red); }}
        .stat-purple .stat-value {{ color: var(--accent-purple); }}
        .stat-yellow .stat-value {{ color: var(--accent-yellow); }}

        .dist-section {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.25rem 1.5rem;
            margin-bottom: 2rem;
        }}

        .dist-title {{
            font-size: 0.8rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-bottom: 0.75rem;
        }}

        .dist-bar {{
            display: flex;
            height: 36px;
            border-radius: 8px;
            overflow: hidden;
            gap: 0;
        }}

        .dist-segment {{
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            min-width: 0;
            transition: all 0.5s ease;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            font-weight: 600;
            color: rgba(255,255,255,0.9);
        }}

        .dist-segment:first-child {{ border-radius: 8px 0 0 8px; }}
        .dist-segment:last-child  {{ border-radius: 0 8px 8px 0; }}
        .dist-segment:only-child  {{ border-radius: 8px; }}

        .dist-green {{ background: var(--accent-green); }}
        .dist-red   {{ background: var(--accent-red); }}
        .dist-gray  {{ background: var(--text-muted); }}

        .dist-segment:hover {{ filter: brightness(1.2); }}

        .dist-count {{ text-shadow: 0 1px 2px rgba(0,0,0,0.3); }}

        .dist-legend {{
            display: flex;
            gap: 1.5rem;
            margin-top: 0.75rem;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }}

        .legend-dot {{
            display: inline-block;
            width: 10px; height: 10px;
            border-radius: 3px;
            margin-right: 6px;
            vertical-align: middle;
        }}

        .table-wrapper {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
        }}

        .table-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1.25rem 1.5rem;
            border-bottom: 1px solid var(--border);
        }}

        .table-title {{ font-size: 1.1rem; font-weight: 600; }}

        .table-count {{
            font-size: 0.8rem;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
            background: var(--bg-secondary);
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
        }}

        table {{ width: 100%; border-collapse: collapse; }}

        thead th {{
            background: var(--bg-secondary);
            padding: 0.75rem 0.75rem;
            text-align: left;
            font-size: 0.7rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            border-bottom: 1px solid var(--border);
            position: sticky;
            top: 0;
            z-index: 2;
        }}

        tbody td {{
            padding: 0.7rem 0.75rem;
            font-size: 0.85rem;
            border-bottom: 1px solid rgba(42,48,80,0.5);
            vertical-align: middle;
        }}

        tbody tr {{ transition: background 0.15s; }}
        tbody tr:hover {{ background: var(--bg-card-hover); }}

        .td-id {{
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-muted);
            font-size: 0.8rem;
        }}

        .td-time {{
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-secondary);
            font-size: 0.75rem;
            white-space: nowrap;
        }}

        .td-market {{ max-width: 360px; }}

        .market-name {{
            font-weight: 500;
            color: var(--text-primary);
            font-size: 0.85rem;
            line-height: 1.4;
        }}

        .market-meta {{
            font-size: 0.7rem;
            color: var(--text-muted);
            margin-top: 2px;
            font-family: 'JetBrains Mono', monospace;
        }}

        .badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 6px;
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
        }}

        .badge-green {{
            background: rgba(34, 197, 94, 0.15);
            color: var(--accent-green);
            border: 1px solid rgba(34, 197, 94, 0.3);
        }}

        .badge-red {{
            background: rgba(239, 68, 68, 0.15);
            color: var(--accent-red);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}

        .badge-blue {{
            background: rgba(59, 130, 246, 0.15);
            color: var(--accent-blue);
            border: 1px solid rgba(59, 130, 246, 0.3);
        }}

        .badge-gray {{
            background: rgba(100, 116, 139, 0.15);
            color: var(--text-secondary);
            border: 1px solid rgba(100, 116, 139, 0.3);
        }}

        .td-conf {{ min-width: 100px; }}

        .conf-wrapper {{
            position: relative;
            background: rgba(255,255,255,0.05);
            border-radius: 6px;
            height: 24px;
            overflow: hidden;
        }}

        .conf-bar {{
            position: absolute;
            top: 0; left: 0; bottom: 0;
            border-radius: 6px;
            opacity: 0.25;
            transition: width 0.5s ease;
        }}

        .conf-text {{
            position: relative;
            z-index: 1;
            display: flex;
            align-items: center;
            height: 100%;
            padding: 0 0.5rem;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            font-weight: 600;
        }}

        .boost {{
            color: var(--accent-green);
            font-size: 0.65rem;
            margin-left: 4px;
        }}

        .td-risk {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}

        .td-action {{ text-align: center; }}

        .btn-market {{
            display: inline-block;
            padding: 0.3rem 0.75rem;
            border-radius: 6px;
            font-size: 0.7rem;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
            text-decoration: none;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            color: #fff;
            border: none;
            transition: all 0.2s ease;
            cursor: pointer;
        }}

        .btn-market:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(59,130,246,0.4);
            filter: brightness(1.15);
        }}

        .footer {{
            text-align: center;
            margin-top: 2.5rem;
            padding: 1.5rem;
            color: var(--text-muted);
            font-size: 0.75rem;
            font-family: 'JetBrains Mono', monospace;
        }}

        .footer a {{
            color: var(--accent-blue);
            text-decoration: none;
        }}
        .footer a:hover {{ text-decoration: underline; }}

        @media (max-width: 1024px) {{
            .container {{ padding: 1rem; }}
            .stats-grid {{ grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; }}
            .stat-value {{ font-size: 1.5rem; }}
            .header-logo {{ font-size: 2rem; }}
            .td-market {{ max-width: 200px; }}
            table {{ font-size: 0.8rem; }}
        }}

        @media (max-width: 640px) {{
            .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}
    </style>
</head>
<body>
    <div class="container">

        <div class="header">
            <div class="header-logo">🔮 PolyAugur</div>
            <div class="header-sub">Polymarket Insider Signal Intelligence</div>
            <div class="header-meta">
                <span class="live-dot"></span>
                Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} ·
                Scanning 10,000+ markets · Multi-layer anomaly detection
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon">📡</div>
                <div class="stat-value">{stats['total_signals']}</div>
                <div class="stat-label">Total Signals</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">⏰</div>
                <div class="stat-value">{stats['signals_24h']}</div>
                <div class="stat-label">Last 24 Hours</div>
            </div>
            <div class="stat-card stat-yellow">
                <div class="stat-icon">🐋</div>
                <div class="stat-value">{whale_signals}</div>
                <div class="stat-label">Whale Signals</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">🎯</div>
                <div class="stat-value" style="color:{wr_color}">{wr_str}</div>
                <div class="stat-label">Win Rate</div>
            </div>
            <div class="stat-card stat-green">
                <div class="stat-icon">✅</div>
                <div class="stat-value">{stats.get('wins', 0)}</div>
                <div class="stat-label">Wins</div>
            </div>
            <div class="stat-card stat-red">
                <div class="stat-icon">❌</div>
                <div class="stat-value">{stats.get('losses', 0)}</div>
                <div class="stat-label">Losses</div>
            </div>
            <div class="stat-card stat-purple">
                <div class="stat-icon">📊</div>
                <div class="stat-value">{avg_conf:.0%}</div>
                <div class="stat-label">Avg Confidence</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">💰</div>
                <div class="stat-value">{vol_display}</div>
                <div class="stat-label">Signal Volume</div>
            </div>
        </div>

        <div class="dist-section">
            <div class="dist-title">Signal Distribution</div>
            <div class="dist-bar">{dist_segments}</div>
            <div class="dist-legend">
                <span><span class="legend-dot" style="background:var(--accent-green)"></span>BUY YES ({buy_yes} · {buy_yes_pct:.0f}%)</span>
                <span><span class="legend-dot" style="background:var(--accent-red)"></span>BUY NO ({buy_no} · {buy_no_pct:.0f}%)</span>
                <span><span class="legend-dot" style="background:var(--text-muted)"></span>HOLD ({hold} · {hold_pct:.0f}%)</span>
            </div>
        </div>

        <div class="table-wrapper">
            <div class="table-header">
                <div class="table-title">🚨 Detected Signals</div>
                <div class="table-count">{total_signals} signals</div>
            </div>
            <div style="overflow-x:auto">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Time</th>
                            <th>Market</th>
                            <th>Trade</th>
                            <th>Confidence</th>
                            <th>Risk</th>
                            <th>Outcome</th>
                            <th>P&L</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            PolyAugur v1.0 · Built by Diego Ringleb ·
            <a href="https://github.com/Diego-2510/PolyAugur">github.com/Diego-2510/PolyAugur</a>
            <br>Powered by Polymarket Gamma API · Mistral AI · On-Chain CLOB Analysis
        </div>
    </div>
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

    hours = 8760 if args.all else args.hours
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