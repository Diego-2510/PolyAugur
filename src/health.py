"""
PolyAugur Health Monitor
Sends periodic health pings to Telegram + tracks system metrics.
Alerts on failures, stale cycles, or API degradation.

Author: Diego Ringleb | Phase 10 | 2026-02-28
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import requests
import config

logger = logging.getLogger(__name__)


class HealthMonitor:
    """
    Tracks system health across cycles.
    Sends Telegram alerts on degradation.
    """

    def __init__(self):
        self.start_time = datetime.now(timezone.utc)
        self.total_cycles = 0
        self.total_signals = 0
        self.total_errors = 0
        self.consecutive_errors = 0
        self.last_successful_cycle: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.api_health: Dict[str, bool] = {
            'gamma': True,
            'mistral': True,
            'clob': True,
            'telegram': True,
        }

        # Alert thresholds
        self.MAX_CONSECUTIVE_ERRORS = 5
        self.STALE_CYCLE_MINUTES = 10
        self.HEALTH_PING_EVERY_N_CYCLES = 100  # ~50 min at 30s interval

    def record_cycle(self, summary: Dict[str, Any]):
        """Record a successful cycle."""
        self.total_cycles += 1
        self.total_signals += summary.get('signal_count', 0)
        self.consecutive_errors = 0
        self.last_successful_cycle = datetime.now(timezone.utc)

        # Check for API issues within the cycle
        if summary.get('markets_fetched', 0) == 0:
            self.api_health['gamma'] = False
        else:
            self.api_health['gamma'] = True

        if summary.get('mistral_calls', 0) == 0 and summary.get('anomalies_detected', 0) > 0:
            self.api_health['mistral'] = False
        else:
            self.api_health['mistral'] = True

    def record_error(self, error: str):
        """Record a cycle error."""
        self.total_errors += 1
        self.consecutive_errors += 1
        self.last_error = error

        if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
            self._send_alert(
                f"🚨 *PolyAugur ALERT*\n\n"
                f"❌ {self.consecutive_errors} consecutive errors\\!\n"
                f"Last: `{error[:100]}`\n\n"
                f"Bot may need restart\\."
            )

    def should_send_ping(self) -> bool:
        """Check if it's time for a health ping."""
        return self.total_cycles > 0 and self.total_cycles % self.HEALTH_PING_EVERY_N_CYCLES == 0

    def get_status(self) -> Dict[str, Any]:
        """Get current health status."""
        uptime = datetime.now(timezone.utc) - self.start_time
        hours = uptime.total_seconds() / 3600

        return {
            'status': 'healthy' if self.consecutive_errors == 0 else 'degraded',
            'uptime_hours': round(hours, 1),
            'total_cycles': self.total_cycles,
            'total_signals': self.total_signals,
            'total_errors': self.total_errors,
            'consecutive_errors': self.consecutive_errors,
            'last_error': self.last_error,
            'api_health': self.api_health,
            'signals_per_hour': round(self.total_signals / max(hours, 0.01), 1),
        }

    def send_health_ping(self):
        """Send health status to Telegram."""
        status = self.get_status()
        uptime_h = status['uptime_hours']
        emoji = '✅' if status['status'] == 'healthy' else '⚠️'

        api_lines = []
        for api, healthy in status['api_health'].items():
            api_lines.append(f"   {api}: {'✅' if healthy else '❌'}")
        api_str = "\n".join(api_lines)

        msg = (
            f"{emoji} *PolyAugur Health Ping*\n\n"
            f"⏱️ Uptime: `{uptime_h:.1f}h`\n"
            f"🔄 Cycles: `{status['total_cycles']}`\n"
            f"🚨 Signals: `{status['total_signals']}` "
            f"\\(`{status['signals_per_hour']:.1f}`/h\\)\n"
            f"❌ Errors: `{status['total_errors']}`\n\n"
            f"📡 API Status:\n{api_str}"
        )

        self._send_alert(msg)

    def _send_alert(self, message: str):
        """Send alert message to Telegram."""
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            logger.warning(f"Health alert (no Telegram): {message[:80]}")
            return

        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                # Fallback: plain text
                requests.post(
                    url,
                    json={
                        "chat_id": config.TELEGRAM_CHAT_ID,
                        "text": message.replace('*', '').replace('\\', '').replace('`', ''),
                    },
                    timeout=10,
                )
        except Exception as e:
            logger.error(f"Health alert send failed: {e}")

    def preflight_check(self) -> Dict[str, Any]:
        """
        Pre-flight validation before starting the bot.
        Checks API keys, DB access, connectivity.
        Returns dict with results.
        """
        results = {}

        # 1. Mistral API key
        results['mistral_key'] = bool(config.MISTRAL_API_KEY)

        # 2. Telegram config
        results['telegram_config'] = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)

        # 3. Gamma API reachable
        try:
            resp = requests.get(
                f"{config.GAMMA_API_BASE}/markets?limit=1",
                timeout=10,
            )
            results['gamma_api'] = resp.status_code == 200
        except Exception:
            results['gamma_api'] = False

        # 4. CLOB API reachable
        try:
            resp = requests.get(
                f"{config.CLOB_API_BASE}/time",
                timeout=10,
            )
            results['clob_api'] = resp.status_code == 200
        except Exception:
            results['clob_api'] = False

        # 5. DB writable
        try:
            import os
            db_dir = os.path.dirname(config.SIGNAL_DB_PATH)
            os.makedirs(db_dir, exist_ok=True)
            results['db_writable'] = True
        except Exception:
            results['db_writable'] = False

        # 6. Mistral API reachable (quick test)
        if config.MISTRAL_API_KEY:
            try:
                from mistralai import Mistral
                client = Mistral(api_key=config.MISTRAL_API_KEY)
                resp = client.chat.complete(
                    model=config.MISTRAL_MODEL,
                    messages=[{"role": "user", "content": "reply OK"}],
                    max_tokens=5,
                )
                results['mistral_api'] = bool(resp.choices)
            except Exception:
                results['mistral_api'] = False
        else:
            results['mistral_api'] = False

        return results


def main():
    print("=" * 60)
    print("🏥 PolyAugur Pre-Flight Check — Phase 10")
    print("=" * 60)

    monitor = HealthMonitor()
    results = monitor.preflight_check()

    all_ok = True
    for check, passed in results.items():
        emoji = "✅" if passed else "❌"
        print(f"  {emoji} {check:20s}: {'OK' if passed else 'FAIL'}")
        if not passed and check in ('mistral_key', 'gamma_api', 'db_writable'):
            all_ok = False

    print()
    if all_ok:
        print("✅ All critical checks passed — ready to run!")
    else:
        print("⚠️  Some critical checks failed — fix before running.")

    print("=" * 60)


if __name__ == "__main__":
    main()
