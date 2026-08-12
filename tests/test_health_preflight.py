from types import SimpleNamespace

import config
import src.health as health_module
from src.health import HealthMonitor


def test_preflight_skips_mistral_when_not_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "MISTRAL_API_KEY", None)
    monkeypatch.setattr(config, "SIGNAL_DB_PATH", str(tmp_path / "signals.db"))

    def fake_get(url, params=None, timeout=None):
        del params, timeout
        if url.endswith("/markets/keyset"):
            return SimpleNamespace(status_code=200)
        if url.endswith("/time"):
            return SimpleNamespace(status_code=200)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(health_module.requests, "get", fake_get)
    results = HealthMonitor().preflight_check()

    assert results["mistral_configured"] is False
    assert results["mistral_api"] is None
    assert results["gamma_api"] is True
    assert results["clob_api"] is True
    assert results["db_writable"] is True
