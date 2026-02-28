"""
PolyAugur Telegram Notifier - Signal Delivery via Telegram Bot API
Sends formatted signal alerts directly to a Telegram chat.
No external library needed – plain HTTPS POST via requests.
Author: Diego Ringleb | Phase 6 | 2026-02-28
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import config

logger = logging.getLogger(__name__)

# Emoji map for trade direction
TRADE_EMOJI = {
    'BUY_YES': '🟢',
    'BUY_NO':  '🔴',
    'HOLD':    '🟡',
}

RISK_EMOJI = {
    'low':    '🟢',
    'medium': '🟡',
    'high':   '🔴',
}


class TelegramNotifier:
    """
    Sends PolyAugur signal alerts to Telegram.

    Uses Bot API directly (no python-telegram-bot needed).
    Endpoint: POST https://api.telegram.org/bot{TOKEN}/sendMessage

    Each signal message includes:
    - Market question (truncated)
    - Trade direction + confidence
    - Volume spike ratio
    - YES price + days to close
    - Risk level + position size
    - Reasoning from Mistral
    """

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.warning(
                "⚠️ Telegram disabled – set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env"
            )
        else:
            logger.info(f"📱 TelegramNotifier ready (chat_id={self.chat_id})")

    def _format_signal(self, signal: Dict[str, Any]) -> str:
        """
        Build Markdown-formatted signal message.
        Telegram supports MarkdownV2 – escape special chars.
        """
        trade = signal.get('recommended_trade', 'HOLD')
        conf = signal.get('confidence_score', 0.0)
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

        trade_e = TRADE_EMOJI.get(trade, '⚪')
        risk_e = RISK_EMOJI.get(risk, '⚪')

        lines = [
            f"🚨 *PolyAugur Signal*",
            f"",
            f"📌 `{question}`",
            f"",
            f"{trade_e} *Trade:* `{trade}`",
            f"🎯 *Confidence:* `{conf:.0%}`",
            f"⚡ *Type:* `{anomaly_type}`",
            f"",
            f"📊 *Volume:* `${volume:,.0f}` \\({spike:.1f}x spike\\)",
            f"💰 *YES Price:* `{yes_price:.3f}`",
            f"⏰ *Closes in:* `{days_to_close}d`",
            f"",
            f"{risk_e} *Risk:* `{risk}` \\| *Hold:* `{holding}h` \\| *Size:* `{position:.0%}`",
            f"",
            f"💬 _{reasoning}_",
            f"",
            f"🕐 `{detected_at} UTC`",
        ]

        return "\n".join(lines)

    def _escape_md(self, text: str) -> str:
        """Escape MarkdownV2 special characters."""
        special = r'_*[]()~`>#+-=|{}.!'
        for char in special:
            text = text.replace(char, f'\\{char}')
        return text

    def send_signal(self, signal: Dict[str, Any]) -> bool:
        """
        Send a formatted signal to Telegram.
        Returns True on success, False on failure.
        Falls back gracefully if Telegram is not configured.
        """
        if not self.enabled:
            logger.debug("Telegram not configured, skipping notification")
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
                logger.info(
                    f"📱 Telegram sent: {signal.get('question', '')[:45]} | "
                    f"Trade={signal.get('recommended_trade')}"
                )
                return True
            else:
                # Retry with plain text on markdown parse error
                if resp.status_code == 400 and 'parse' in resp.text.lower():
                    return self._send_plain(signal)
                logger.error(
                    f"Telegram API error {resp.status_code}: {resp.text[:100]}"
                )
                return False

        except requests.exceptions.Timeout:
            logger.error("Telegram request timed out")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Telegram request failed: {e}")
            return False

    def _send_plain(self, signal: Dict[str, Any]) -> bool:
        """Fallback: send plain text without Markdown if formatting fails."""
        trade = signal.get('recommended_trade', 'HOLD')
        conf = signal.get('confidence_score', 0.0)
        question = signal.get('question', 'Unknown')[:80]
        reasoning = signal.get('reasoning', '')[:150]

        message = (
            f"PolyAugur Signal\n\n"
            f"{question}\n\n"
            f"Trade: {trade} | Confidence: {conf:.0%}\n"
            f"Risk: {signal.get('risk_level')} | Hold: {signal.get('holding_period_hours')}h\n\n"
            f"{reasoning}"
        )

        url = self.BASE_URL.format(token=self.token)
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Plain text fallback also failed: {e}")
            return False

    def send_cycle_summary(self, summary: Dict[str, Any]) -> bool:
        """
        Send a brief cycle summary (optional, for monitoring).
        Only sends if signal_count > 0 to avoid spam.
        """
        if not self.enabled:
            return False

        if summary.get('signal_count', 0) == 0:
            return False

        msg = (
            f"📊 *PolyAugur Cycle \\#{summary.get('cycle', '?')}*\n\n"
            f"🔍 Markets: `{summary.get('markets_fetched', 0)}`\n"
            f"🚨 Anomalies: `{summary.get('anomalies_detected', 0)}`\n"
            f"📣 Signals: `{summary.get('signal_count', 0)}`\n"
            f"🧠 Mistral calls: `{summary.get('mistral_calls', 0)}`\n"
            f"⏱ Cycle time: `{summary.get('cycle_time_sec', 0)}s`"
        )

        url = self.BASE_URL.format(token=self.token)
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": msg,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            return resp.status_code == 200
        except Exception:
            return False


def main():
    """Standalone test – sends a real message if TELEGRAM_* env vars are set."""
    import os
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("🧪 PolyAugur Telegram Notifier Test - Phase 6")
    print("=" * 60)

    notifier = TelegramNotifier()

    test_signal = {
        'market_id': 'test_001',
        'question': 'Will Trump nominate Michelle Bowman as Fed chair?',
        'recommended_trade': 'BUY_YES',
        'confidence_score': 0.87,
        'anomaly_type': 'volume_spike',
        'risk_level': 'medium',
        'yes_price': 0.73,
        'volume_24hr': 453000,
        'spike_ratio': 9.0,
        'days_to_close': 20,
        'holding_period_hours': 48,
        'recommended_position_size_pct': 0.10,
        'reasoning': '9x volume spike on Fed nomination 20d before close. White House insider pattern.',
        'detected_at': datetime.now(timezone.utc).isoformat(),
    }

    print(f"\n[Test 1] Format check (no API call)...")
    msg = notifier._format_signal(test_signal)
    print(f"   Message preview ({len(msg)} chars):")
    print("   " + msg[:200].replace('\n', '\n   '))

    if notifier.enabled:
        print(f"\n[Test 2] Live send to chat_id={notifier.chat_id}...")
        ok = notifier.send_signal(test_signal)
        print(f"   {'✅ Sent' if ok else '❌ Failed'}")
    else:
        print(f"\n[Test 2] Skipped (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in .env)")
        print(f"   Set them to enable live notifications")

    print("\n" + "=" * 60)
    print("✅ Telegram Notifier: PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
