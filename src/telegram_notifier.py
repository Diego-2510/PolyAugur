"""
PolyAugur Telegram Notifier
Phase 8: Enhanced messages with whale intelligence + confidence boost indicator.
Author: Diego Ringleb | Phase 8 | 2026-02-28
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Dict, Any
import config

logger = logging.getLogger(__name__)

TRADE_EMOJI = {'BUY_YES': '🟢', 'BUY_NO': '🔴', 'HOLD': '🟡'}
RISK_EMOJI  = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}


class TelegramNotifier:

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.warning("⚠️ Telegram disabled – set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")
        else:
            logger.info(f"📱 TelegramNotifier ready (chat_id={self.chat_id})")

    def _format_signal(self, signal: Dict[str, Any]) -> str:
        trade = signal.get('recommended_trade', 'HOLD')
        conf = signal.get('confidence_score', 0.0)
        conf_raw = signal.get('confidence_raw', conf)
        conf_boost = signal.get('confidence_boost', 0.0)
        risk = signal.get('risk_level', 'medium')
        yes_price = signal.get('yes_price', 0.5)
        volume = signal.get('volume_24hr', 0)
        spike = signal.get('spike_ratio', 1.0)
        question = signal.get('question', 'Unknown')[:80]
        reasoning = signal.get('reasoning', '')[:180]
        holding = signal.get('holding_period_hours', 0)
        position = signal.get('recommended_position_size_pct', 0.0)
        days_to_close = signal.get('days_to_close', '?')
        anomaly_type = signal.get('anomaly_type', 'unknown')
        detected_at = signal.get('detected_at', '')[:16].replace('T', ' ')

        # Whale data
        whale_count = signal.get('whale_count', 0)
        top_wallet = signal.get('top_wallet_pct', 0)
        dir_bias = signal.get('directional_bias', 0.5)
        dom_side = signal.get('dominant_side', 'NONE')
        burst = signal.get('burst_score', 1.0)
        suspicious = signal.get('trade_suspicious', False)
        unique_wallets = signal.get('unique_wallets', 0)

        trade_e = TRADE_EMOJI.get(trade, '⚪')
        risk_e = RISK_EMOJI.get(risk, '⚪')

        # Header: whale alert vs normal
        if suspicious:
            header = "🐋🚨 *PolyAugur WHALE Signal*"
        else:
            header = "🚨 *PolyAugur Signal*"

        # Confidence display with boost
        if conf_boost > 0:
            conf_line = f"🎯 *Confidence:* `{conf:.0%}` \\(↑{conf_boost:.0%} whale boost\\)"
        else:
            conf_line = f"🎯 *Confidence:* `{conf:.0%}`"

        lines = [
            header,
            "",
            f"📌 `{question}`",
            "",
            f"{trade_e} *Trade:* `{trade}`",
            conf_line,
            f"⚡ *Type:* `{anomaly_type}`",
            "",
            f"📊 *Volume:* `${volume:,.0f}` \\({spike:.1f}x spike\\)",
            f"💰 *YES Price:* `{yes_price:.3f}`",
            f"⏰ *Closes in:* `{days_to_close}d`",
            "",
            f"{risk_e} *Risk:* `{risk}` \\| *Hold:* `{holding}h` \\| *Size:* `{position:.0%}`",
        ]

        # Whale section (only if data exists)
        if whale_count > 0 or suspicious:
            lines.extend([
                "",
                "🐋 *On\\-Chain Intelligence:*",
                f"   Whales: `{whale_count}` \\| Wallets: `{unique_wallets}`",
                f"   Top wallet: `{top_wallet:.0%}` of volume",
                f"   Direction: `{dir_bias:.0%} {dom_side}`",
                f"   Burst: `{burst:.1f}x` last hour",
            ])
            if suspicious:
                reasons = signal.get('suspicious_reasons', [])
                if isinstance(reasons, str):
                    import json
                    try:
                        reasons = json.loads(reasons)
                    except Exception:
                        reasons = [reasons]
                if reasons:
                    flags = ", ".join(r.replace('_', ' ') for r in reasons[:3])
                    lines.append(f"   ⚠️ `{flags}`")

        lines.extend([
            "",
            f"💬 _{reasoning}_",
            "",
            f"🕐 `{detected_at} UTC`",
        ])

        return "\n".join(lines)

    def send_signal(self, signal: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False

        message = self._format_signal(signal)
        url = self.BASE_URL.format(token=self.token)

        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            if resp.status_code == 200:
                logger.info(f"📱 Telegram sent: {signal.get('question', '')[:45]}")
                return True
            elif resp.status_code == 400 and 'parse' in resp.text.lower():
                return self._send_plain(signal)
            else:
                logger.error(f"Telegram API error {resp.status_code}: {resp.text[:100]}")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram request failed: {e}")
            return False

    def _send_plain(self, signal: Dict[str, Any]) -> bool:
        trade = signal.get('recommended_trade', 'HOLD')
        conf = signal.get('confidence_score', 0.0)
        question = signal.get('question', 'Unknown')[:80]
        reasoning = signal.get('reasoning', '')[:150]
        whale_count = signal.get('whale_count', 0)
        suspicious = signal.get('trade_suspicious', False)

        whale_tag = "🐋 WHALE " if suspicious else ""

        message = (
            f"{whale_tag}PolyAugur Signal\n\n"
            f"{question}\n\n"
            f"Trade: {trade} | Confidence: {conf:.0%}\n"
            f"Risk: {signal.get('risk_level')} | Hold: {signal.get('holding_period_hours')}h\n"
            f"Whales: {whale_count}\n\n"
            f"{reasoning}"
        )

        url = self.BASE_URL.format(token=self.token)
        try:
            resp = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": message, "disable_web_page_preview": True},
                timeout=10
            )
            return resp.status_code == 200
        except Exception:
            return False

    def send_daily_report(self, stats: Dict[str, Any]) -> bool:
        """Send daily performance summary."""
        if not self.enabled:
            return False

        win_rate = stats.get('win_rate')
        wr_str = f"{win_rate:.0%}" if win_rate is not None else "N/A"

        msg = (
            f"📊 *PolyAugur Daily Report*\n\n"
            f"📈 Signals \\(24h\\): `{stats.get('signals_24h', 0)}`\n"
            f"🐋 Whale signals: `{stats.get('whale_signals', 0)}`\n"
            f"✅ Wins: `{stats.get('wins', 0)}`\n"
            f"❌ Losses: `{stats.get('losses', 0)}`\n"
            f"🎯 Win rate: `{wr_str}`\n"
            f"📦 Total signals: `{stats.get('total_signals', 0)}`"
        )

        url = self.BASE_URL.format(token=self.token)
        try:
            resp = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": msg, "parse_mode": "MarkdownV2",
                      "disable_web_page_preview": True},
                timeout=10
            )
            return resp.status_code == 200
        except Exception:
            return False
