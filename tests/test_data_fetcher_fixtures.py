import copy
import json
from pathlib import Path
from types import SimpleNamespace

import config
import src.data_fetcher as data_fetcher_module
from src.data_fetcher import PolymarketFetcher

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(
    name: str,
):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_pagination_uses_fixtures_and_deduplicates(
    monkeypatch,
) -> None:
    fetcher = PolymarketFetcher()

    page_1 = load_fixture("gamma_markets_page_1.json")

    page_2 = load_fixture("gamma_markets_page_2.json")

    monkeypatch.setattr(
        config,
        "MARKETS_PER_PAGE",
        2,
    )

    monkeypatch.setattr(
        data_fetcher_module.time,
        "sleep",
        lambda _: None,
    )

    pages = {
        0: page_1,
        2: page_2,
        4: [],
    }

    requested_offsets = []

    def fake_api_get(
        base,
        endpoint,
        params=None,
        max_retries=3,
    ):
        del (
            base,
            endpoint,
            max_retries,
        )

        offset = int(params["offset"])

        requested_offsets.append(offset)

        return copy.deepcopy(pages[offset])

    monkeypatch.setattr(
        fetcher,
        "_api_get",
        fake_api_get,
    )

    markets = fetcher.fetch_all_markets_paginated(max_pages=10)

    assert [market["id"] for market in markets] == [
        "market-001",
        "market-002",
        "market-003",
    ]

    assert requested_offsets == [
        0,
        2,
        4,
    ]

    assert fetcher.fetch_stats["duplicates_removed"] == 1

    assert markets[0]["volume_24hr"] == 40000.0

    assert markets[0]["end_date_iso"] == "2027-06-30T12:00:00Z"

    assert markets[2]["volume_24hr"] == 62000.0

    assert markets[2]["end_date_iso"] == "2027-08-01T12:00:00Z"

    assert fetcher.fetch_stats["incomplete"] is False


def test_failed_page_does_not_silently_skip_to_later_offset(
    monkeypatch,
) -> None:
    fetcher = PolymarketFetcher()

    page_1 = load_fixture("gamma_markets_page_1.json")

    monkeypatch.setattr(
        config,
        "MARKETS_PER_PAGE",
        2,
    )

    monkeypatch.setattr(
        data_fetcher_module.time,
        "sleep",
        lambda _: None,
    )

    requested_offsets = []

    def fake_api_get(
        base,
        endpoint,
        params=None,
        max_retries=3,
    ):
        del (
            base,
            endpoint,
            max_retries,
        )

        offset = int(params["offset"])

        requested_offsets.append(offset)

        if offset == 0:
            return copy.deepcopy(page_1)

        if offset == 2:
            return None

        raise AssertionError("pagination must stop instead of skipping a failed page")

    monkeypatch.setattr(
        fetcher,
        "_api_get",
        fake_api_get,
    )

    markets = fetcher.fetch_all_markets_paginated(max_pages=10)

    assert markets == []

    assert requested_offsets == [
        0,
        2,
    ]

    assert fetcher.fetch_stats["markets_raw"] == 2

    assert fetcher.fetch_stats["incomplete"] is True

    assert fetcher.fetch_stats["stopped_at_offset"] == 2

    assert fetcher.fetch_stats["stopped_reason"] == "request_failed"


def test_unexpected_page_type_fails_closed(
    monkeypatch,
) -> None:
    fetcher = PolymarketFetcher()

    monkeypatch.setattr(
        data_fetcher_module.time,
        "sleep",
        lambda _: None,
    )

    monkeypatch.setattr(
        fetcher,
        "_api_get",
        lambda *args, **kwargs: {"markets": []},
    )

    assert fetcher.fetch_all_markets_paginated(max_pages=1) == []

    assert fetcher.fetch_stats["incomplete"] is True

    assert fetcher.fetch_stats["stopped_reason"] == "unexpected_response_type"


def test_api_get_retries_429_without_real_sleep(
    monkeypatch,
) -> None:
    fetcher = PolymarketFetcher()

    sleeps = []
    calls = []

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
                json=lambda: [{"id": "market-001"}],
            ),
        ]
    )

    def fake_get(
        url,
        params=None,
        timeout=None,
    ):
        calls.append(
            (
                url,
                params,
                timeout,
            )
        )

        return next(responses)

    monkeypatch.setattr(
        fetcher.session,
        "get",
        fake_get,
    )

    monkeypatch.setattr(
        data_fetcher_module.time,
        "sleep",
        sleeps.append,
    )

    result = fetcher._api_get(
        "https://example.test",
        "markets",
    )

    assert result == [{"id": "market-001"}]

    assert len(calls) == 2

    assert sleeps == [config.BACKOFF_DELAYS[0]]
