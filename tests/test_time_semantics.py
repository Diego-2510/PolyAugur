from datetime import datetime, timedelta, timezone

import pytest

from src.data_fetcher import PolymarketFetcher
from src.mistral_analyzer import MistralAnalyzer
from src.orchestrator import Orchestrator


def make_orchestrator() -> Orchestrator:
    """
    Construct only the part of Orchestrator needed for this unit test.

    This deliberately bypasses __init__ so tests do not create a database,
    notifier, API clients, or other infrastructure dependencies.
    """
    orchestrator = Orchestrator.__new__(Orchestrator)

    orchestrator.snapshot_history = {}

    return orchestrator


def make_snapshot(
    *,
    market_id: str = "market-1",
    yes_price: float = 0.40,
    volume_24hr: float = 1_000.0,
) -> dict:
    return {
        "id": market_id,
        "yes_price": yes_price,
        "volume_24hr": volume_24hr,
    }


def test_first_observation_has_no_invented_change_metrics() -> None:
    orchestrator = make_orchestrator()

    observed_at = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    result = orchestrator.enrich_with_price_velocity(
        [make_snapshot()],
        observed_at=observed_at,
    )[0]

    assert result["price_change_since_last_observation"] is None

    assert result["volume_24h_change_since_last_observation"] is None

    assert result["seconds_since_last_observation"] is None

    assert result["price_change_per_hour_linearized"] is None


def test_actual_30_second_interval_is_used() -> None:
    orchestrator = make_orchestrator()

    start = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    orchestrator.enrich_with_price_velocity(
        [
            make_snapshot(
                yes_price=0.40,
                volume_24hr=1_000.0,
            )
        ],
        observed_at=start,
    )

    result = orchestrator.enrich_with_price_velocity(
        [
            make_snapshot(
                yes_price=0.41,
                volume_24hr=1_100.0,
            )
        ],
        observed_at=(start + timedelta(seconds=30)),
    )[0]

    assert result["price_change_since_last_observation"] == pytest.approx(0.01)

    assert result["volume_24h_change_since_last_observation"] == pytest.approx(100.0)

    assert result["seconds_since_last_observation"] == pytest.approx(30.0)

    # 0.01 observed in 30 seconds:
    # 0.01 * 3600 / 30 = 1.20 price units/hour.
    assert result["price_change_per_hour_linearized"] == pytest.approx(1.20)


def test_actual_30_minute_interval_is_used() -> None:
    orchestrator = make_orchestrator()

    start = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    orchestrator.enrich_with_price_velocity(
        [
            make_snapshot(
                yes_price=0.40,
            )
        ],
        observed_at=start,
    )

    result = orchestrator.enrich_with_price_velocity(
        [
            make_snapshot(
                yes_price=0.41,
            )
        ],
        observed_at=(start + timedelta(minutes=30)),
    )[0]

    assert result["price_change_since_last_observation"] == pytest.approx(0.01)

    assert result["seconds_since_last_observation"] == pytest.approx(1_800.0)

    # 0.01 observed in half an hour:
    # 0.01 * 3600 / 1800 = 0.02 per hour.
    assert result["price_change_per_hour_linearized"] == pytest.approx(0.02)


def test_non_positive_interval_does_not_invent_velocity() -> None:
    orchestrator = make_orchestrator()

    observed_at = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc,
    )

    orchestrator.enrich_with_price_velocity(
        [
            make_snapshot(
                yes_price=0.40,
            )
        ],
        observed_at=observed_at,
    )

    result = orchestrator.enrich_with_price_velocity(
        [
            make_snapshot(
                yes_price=0.41,
            )
        ],
        observed_at=observed_at,
    )[0]

    assert result["price_change_since_last_observation"] is None

    assert result["seconds_since_last_observation"] is None

    assert result["price_change_per_hour_linearized"] is None


def test_naive_observed_at_is_rejected() -> None:
    orchestrator = make_orchestrator()

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        orchestrator.enrich_with_price_velocity(
            [make_snapshot()],
            observed_at=datetime(
                2026,
                1,
                1,
                12,
                0,
            ),
        )


def test_snapshot_builder_does_not_claim_unobserved_time_window() -> None:
    fetcher = PolymarketFetcher()

    market = {
        "id": "market-1",
        "question": "Example market?",
        "outcomePrices": [
            "0.40",
            "0.60",
        ],
        "volume_24hr": 1_000.0,
        "volume": 10_000.0,
        "liquidity": 5_000.0,
    }

    result = fetcher.get_market_snapshot(market)

    assert result is not None

    assert result["price_change_since_last_observation"] is None

    assert result["volume_24h_change_since_last_observation"] is None

    assert result["seconds_since_last_observation"] is None

    assert result["price_change_per_hour_linearized"] is None

    assert "price_delta_30m" not in result

    assert "volume_delta_30m" not in result

    assert "price_velocity" not in result


def test_mistral_prompt_uses_actual_observation_semantics() -> None:
    analyzer = MistralAnalyzer.__new__(MistralAnalyzer)

    snapshot = {
        "id": "market-1",
        "question": "Will an example event occur?",
        "description": "Test fixture",
        "end_date_iso": "2026-12-31T00:00:00+00:00",
        "yes_price": 0.41,
        "no_price": 0.59,
        "spread": 0.18,
        "volume_24hr": 1_100.0,
        "volume": 10_000.0,
        "liquidity": 5_000.0,
        "price_change_since_last_observation": 0.01,
        "volume_24h_change_since_last_observation": 100.0,
        "seconds_since_last_observation": 30.0,
        "price_change_per_hour_linearized": 1.2,
    }

    anomaly_result = {
        "score": 0.55,
        "breakdown": {
            "volume_spike": {
                "spike_ratio": 4.0,
                "severity": "moderate_high",
            },
            "price_anomaly": {
                "indicators": [],
                "vol_liq_ratio": 1.0,
            },
            "topic_sensitivity": {
                "reasons": [],
                "multiplier": 1.0,
            },
        },
    }

    prompt = analyzer._build_user_prompt(
        snapshot,
        anomaly_result,
    )

    assert "Price delta (30m)" not in prompt

    assert "Price velocity (1h)" not in prompt

    assert "Price change since previous observation: +0.0100" in prompt

    assert "Elapsed time since previous observation: 30.0 seconds" in prompt

    assert "Price change linearly normalized to one hour: +1.2000" in prompt

    assert "not a forecast" in prompt


def test_mistral_prompt_represents_missing_observation_as_na() -> None:
    analyzer = MistralAnalyzer.__new__(MistralAnalyzer)

    snapshot = {
        "id": "market-1",
        "question": "Example market?",
        "description": "",
        "end_date_iso": None,
        "yes_price": 0.50,
        "no_price": 0.50,
        "spread": 0.0,
        "volume_24hr": 1_000.0,
        "volume": 5_000.0,
        "liquidity": 2_000.0,
        "price_change_since_last_observation": None,
        "volume_24h_change_since_last_observation": None,
        "seconds_since_last_observation": None,
        "price_change_per_hour_linearized": None,
    }

    anomaly_result = {
        "score": 0.0,
        "breakdown": {},
    }

    prompt = analyzer._build_user_prompt(
        snapshot,
        anomaly_result,
    )

    assert "Price change since previous observation: N/A" in prompt

    assert "Elapsed time since previous observation: N/A seconds" in prompt

    assert "Price change linearly normalized to one hour: N/A" in prompt
