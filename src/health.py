"""Health monitoring and pre-flight checks for PolyAugur."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Track cycle health and validate required runtime dependencies."""

    def __init__(self) -> None:
        self.start_time = datetime.now(timezone.utc)
        self.total_cycles = 0
        self.total_signals = 0
        self.total_errors = 0
        self.consecutive_errors = 0
        self.last_successful_cycle: datetime | None = None
        self.last_error: str | None = None
        self.api_health: dict[str, bool] = {
            "gamma": True,
            "mistral": True,
            "clob": True,
            "telegram": True,
        }
        self.MAX_CONSECUTIVE_ERRORS = config.MAX_CONSECUTIVE_ERRORS
        self.STALE_CYCLE_MINUTES = 10
        self.HEALTH_PING_EVERY_N_CYCLES = config.HEALTH_PING_EVERY_N_CYCLES

    def record_cycle(self, summary: dict[str, Any]) -> None:
        self.total_cycles += 1
        self.total_signals += summary.get("signal_count", 0)
        self.consecutive_errors = 0
        self.last_successful_cycle = datetime.now(timezone.utc)
        self.api_health["gamma"] = summary.get("markets_fetched", 0) > 0

        if config.MISTRAL_API_KEY:
            self.api_health["mistral"] = not (
                summary.get("mistral_calls", 0) == 0 and summary.get("anomalies_detected", 0) > 0
            )
        else:
            # Mistral is optional; rule-based fallback is expected when unconfigured.
            self.api_health["mistral"] = True

    def record_error(self, error: str) -> None:
        self.total_errors += 1
        self.consecutive_errors += 1
        self.last_error = error
        if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
            self._send_alert(
                "PolyAugur alert: "
                f"{self.consecutive_errors} consecutive errors. "
                f"Last error: {error[:100]}"
            )

    def should_send_ping(self) -> bool:
        return self.total_cycles > 0 and self.total_cycles % self.HEALTH_PING_EVERY_N_CYCLES == 0

    def get_status(self) -> dict[str, Any]:
        uptime = datetime.now(timezone.utc) - self.start_time
        hours = uptime.total_seconds() / 3600
        return {
            "status": "healthy" if self.consecutive_errors == 0 else "degraded",
            "uptime_hours": round(hours, 1),
            "total_cycles": self.total_cycles,
            "total_signals": self.total_signals,
            "total_errors": self.total_errors,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "api_health": self.api_health,
            "signals_per_hour": round(self.total_signals / max(hours, 0.01), 1),
        }

    def send_health_ping(self) -> None:
        status = self.get_status()
        api_lines = [
            f"{api}: {'OK' if healthy else 'FAIL'}" for api, healthy in status["api_health"].items()
        ]
        message = (
            "PolyAugur health ping\n"
            f"Uptime: {status['uptime_hours']:.1f}h\n"
            f"Cycles: {status['total_cycles']}\n"
            f"Signals: {status['total_signals']}\n"
            f"Errors: {status['total_errors']}\n" + "\n".join(api_lines)
        )
        self._send_alert(message)

    def _send_alert(self, message: str) -> None:
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            logger.warning("Health alert (Telegram not configured): %s", message[:120])
            return

        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            response = requests.post(
                url,
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message},
                timeout=10,
            )
            if response.status_code != 200:
                logger.error("Telegram health alert returned HTTP %s", response.status_code)
        except requests.RequestException as exc:
            logger.error("Health alert send failed: %s", exc)

    def preflight_check(self) -> dict[str, bool | None]:
        """Check required services while treating Mistral/Telegram as optional."""
        results: dict[str, bool | None] = {
            "mistral_configured": bool(config.MISTRAL_API_KEY),
            "telegram_configured": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID),
        }

        try:
            response = requests.get(
                f"{config.GAMMA_API_BASE}/markets/keyset",
                params={"limit": 1, "closed": "false"},
                timeout=10,
            )
            results["gamma_api"] = response.status_code == 200
        except requests.RequestException:
            results["gamma_api"] = False

        try:
            response = requests.get(f"{config.CLOB_API_BASE}/time", timeout=10)
            results["clob_api"] = response.status_code == 200
        except requests.RequestException:
            results["clob_api"] = False

        try:
            db_parent = Path(config.SIGNAL_DB_PATH).expanduser().resolve().parent
            db_parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=db_parent):
                pass
            results["db_writable"] = True
        except OSError:
            results["db_writable"] = False

        if config.MISTRAL_API_KEY:
            try:
                from mistralai import Mistral

                client = Mistral(api_key=config.MISTRAL_API_KEY)
                response = client.chat.complete(
                    model=config.MISTRAL_MODEL,
                    messages=[{"role": "user", "content": "reply OK"}],
                    max_tokens=5,
                )
                results["mistral_api"] = bool(response.choices)
            except Exception:
                results["mistral_api"] = False
        else:
            results["mistral_api"] = None

        return results


def _format_result(name: str, value: bool | None) -> str:
    if name in {"mistral_configured", "telegram_configured"}:
        return "CONFIGURED" if value else "NOT CONFIGURED (optional)"
    if value is None:
        return "SKIPPED (optional)"
    return "OK" if value else "FAIL"


def main() -> int:
    print("=" * 60)
    print("PolyAugur pre-flight check")
    print("=" * 60)

    monitor = HealthMonitor()
    results = monitor.preflight_check()
    for name, value in results.items():
        print(f"  {name:22s}: {_format_result(name, value)}")

    critical = {"gamma_api", "db_writable"}
    if config.TRADE_ANALYSIS_ENABLED:
        critical.add("clob_api")

    failed = [name for name in critical if results.get(name) is not True]
    print()
    if failed:
        print("Critical checks failed: " + ", ".join(sorted(failed)))
        return 1

    print("All required checks passed. Optional integrations may be unconfigured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
