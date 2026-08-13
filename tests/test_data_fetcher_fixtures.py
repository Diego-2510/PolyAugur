import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import src.data_fetcher as data_fetcher_module
from src.data_fetcher import PolymarketFetcher

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_keyset_pagination_deduplicates_and_uses_cursor(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    page_1 = load_fixture("gamma_markets_page_1.json")
    page_2 = load_fixture("gamma_markets_page_2.json")
    monkeypatch.setattr(config, "MARKETS_PER_PAGE", 2)
    monkeypatch.setattr(data_fetcher_module.time, "sleep", lambda _: None)

    responses = {
        None: {"markets": page_1, "next_cursor": "cursor-2"},
        "cursor-2": {"markets": page_2, "next_cursor": "cursor-3"},
        "cursor-3": {"markets": []},
    }
    requested = []

    def fake_api_get(base, endpoint, params=None, max_retries=3):
        del base, max_retries
        assert endpoint == "markets/keyset"
        assert "offset" not in params
        cursor = params.get("after_cursor")
        requested.append(cursor)
        return copy.deepcopy(responses[cursor])

    monkeypatch.setattr(fetcher, "_api_get", fake_api_get)
    markets = fetcher.fetch_all_markets_paginated(max_pages=10)

    assert [market["id"] for market in markets] == [
        "market-001",
        "market-002",
        "market-003",
    ]
    assert requested == [None, "cursor-2", "cursor-3"]
    assert fetcher.fetch_stats["pagination"] == "keyset"
    assert fetcher.fetch_stats["duplicates_removed"] == 1
    assert fetcher.fetch_stats["incomplete"] is False
    assert fetcher.fetch_stats["truncated"] is False


def test_failed_keyset_page_fails_closed(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    page_1 = load_fixture("gamma_markets_page_1.json")
    monkeypatch.setattr(config, "MARKETS_PER_PAGE", 2)
    monkeypatch.setattr(data_fetcher_module.time, "sleep", lambda _: None)

    def fake_api_get(base, endpoint, params=None, max_retries=3):
        del base, endpoint, max_retries
        if params.get("after_cursor") is None:
            return {"markets": copy.deepcopy(page_1), "next_cursor": "cursor-2"}
        return None

    monkeypatch.setattr(fetcher, "_api_get", fake_api_get)
    assert fetcher.fetch_all_markets_paginated(max_pages=10) == []
    assert fetcher.fetch_stats["incomplete"] is True
    assert fetcher.fetch_stats["stopped_at_cursor"] == "cursor-2"
    assert fetcher.fetch_stats["stopped_reason"] == "request_failed"


def test_invalid_keyset_response_fails_closed(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    monkeypatch.setattr(fetcher, "_api_get", lambda *args, **kwargs: ["wrong"])
    assert fetcher.fetch_all_markets_paginated(max_pages=1) == []
    assert fetcher.fetch_stats["stopped_reason"] == "unexpected_response_type"


def test_missing_markets_field_fails_closed(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    monkeypatch.setattr(fetcher, "_api_get", lambda *args, **kwargs: {"next_cursor": "x"})
    assert fetcher.fetch_all_markets_paginated(max_pages=1) == []
    assert fetcher.fetch_stats["stopped_reason"] == "invalid_markets_field"


def test_cursor_loop_fails_closed(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    monkeypatch.setattr(config, "MARKETS_PER_PAGE", 1)
    monkeypatch.setattr(data_fetcher_module.time, "sleep", lambda _: None)

    def fake_api_get(base, endpoint, params=None, max_retries=3):
        del base, endpoint, max_retries
        cursor = params.get("after_cursor")
        if cursor is None:
            return {"markets": [{"id": "1"}], "next_cursor": "repeat"}
        return {"markets": [{"id": "2"}], "next_cursor": "repeat"}

    monkeypatch.setattr(fetcher, "_api_get", fake_api_get)
    assert fetcher.fetch_all_markets_paginated(max_pages=10) == []
    assert fetcher.fetch_stats["stopped_reason"] == "cursor_loop"


def test_configured_page_cap_is_reported_as_truncation(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    monkeypatch.setattr(config, "MARKETS_PER_PAGE", 1)
    monkeypatch.setattr(
        fetcher,
        "_api_get",
        lambda *args, **kwargs: {"markets": [{"id": "1"}], "next_cursor": "more"},
    )
    markets = fetcher.fetch_all_markets_paginated(max_pages=1)
    assert [market["id"] for market in markets] == ["1"]
    assert fetcher.fetch_stats["incomplete"] is False
    assert fetcher.fetch_stats["truncated"] is True


def test_exact_page_cap_without_next_cursor_is_not_truncated(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    monkeypatch.setattr(config, "MARKETS_PER_PAGE", 1)
    monkeypatch.setattr(data_fetcher_module.time, "sleep", lambda _: None)

    responses = {
        None: {"markets": [{"id": "1"}], "next_cursor": "page-2"},
        "page-2": {"markets": [{"id": "2"}]},
    }

    def fake_api_get(base, endpoint, params=None, max_retries=3):
        del base, endpoint, max_retries
        return copy.deepcopy(responses[params.get("after_cursor")])

    monkeypatch.setattr(fetcher, "_api_get", fake_api_get)
    markets = fetcher.fetch_all_markets_paginated(max_pages=2)

    assert [market["id"] for market in markets] == ["1", "2"]
    assert fetcher.fetch_stats["incomplete"] is False
    assert fetcher.fetch_stats["truncated"] is False


def test_get_active_markets_raises_for_incomplete_scan(monkeypatch) -> None:
    fetcher = PolymarketFetcher()

    def fake_fetch(max_pages=None):
        del max_pages
        fetcher.fetch_stats = {
            "incomplete": True,
            "stopped_reason": "request_failed",
            "stopped_at_cursor": "cursor-2",
        }
        return []

    monkeypatch.setattr(fetcher, "fetch_all_markets_paginated", fake_fetch)
    with pytest.raises(RuntimeError, match="Incomplete Gamma market scan"):
        fetcher.get_active_markets()


def test_api_get_retries_429_without_real_sleep(monkeypatch) -> None:
    fetcher = PolymarketFetcher()
    sleeps = []
    responses = iter(
        [
            SimpleNamespace(
                status_code=429,
                text="rate limited",
                raise_for_status=lambda: None,
                json=lambda: None,
            ),
            SimpleNamespace(
                status_code=200,
                text="ok",
                raise_for_status=lambda: None,
                json=lambda: {"markets": [{"id": "market-001"}]},
            ),
        ]
    )
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(data_fetcher_module.time, "sleep", sleeps.append)

    result = fetcher._api_get("https://example.test", "markets/keyset")
    assert result == {"markets": [{"id": "market-001"}]}
    assert sleeps == [config.BACKOFF_DELAYS[0]]
