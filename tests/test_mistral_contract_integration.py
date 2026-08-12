import json
from pathlib import Path
from types import SimpleNamespace

from src.mistral_analyzer import (
    MistralAnalyzer,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mistral"


def load_text(
    name: str,
) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def load_json(
    name: str,
) -> dict:
    return json.loads(load_text(name))


class FakeChat:
    def __init__(
        self,
        raw: str,
    ):
        self.raw = raw

    def complete(
        self,
        **kwargs,
    ):
        del kwargs

        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.raw))])


class FakeClient:
    def __init__(
        self,
        raw: str,
    ):
        self.chat = FakeChat(raw)


def make_analyzer(
    raw: str,
) -> MistralAnalyzer:
    analyzer = MistralAnalyzer.__new__(MistralAnalyzer)

    analyzer.client = FakeClient(raw)

    analyzer.call_count = 0
    analyzer.error_count = 0

    return analyzer


def snapshot(
    market_id: str = "market-1",
) -> dict:
    return {
        "id": market_id,
        "yes_price": 0.4,
    }


def anomaly_result() -> dict:
    return {
        "score": 0.1,
        "breakdown": {},
    }


def test_invalid_single_llm_output_falls_back() -> None:
    analyzer = make_analyzer(load_text("invalid_missing_field.json"))

    result = analyzer.analyze_single(
        snapshot(),
        anomaly_result(),
    )

    assert result["source"] == "rule_based_fallback"

    assert result["recommended_trade"] == "HOLD"

    assert analyzer.error_count == 1


def test_batch_count_mismatch_falls_back_for_entire_batch() -> None:
    raw = json.dumps({"results": [load_json("valid_signal.json")]})

    analyzer = make_analyzer(raw)

    results = analyzer.analyze_batch(
        [
            (
                snapshot("market-1"),
                anomaly_result(),
            ),
            (
                snapshot("market-2"),
                anomaly_result(),
            ),
        ]
    )

    assert len(results) == 2

    assert all(result["source"] == "rule_based_fallback" for result in results)

    assert analyzer.error_count == 1


def test_valid_output_is_accepted_and_confidence_is_safety_capped() -> None:
    payload = load_json("valid_signal.json")

    payload["confidence_score"] = 1.0

    analyzer = make_analyzer(json.dumps(payload))

    result = analyzer.analyze_single(
        snapshot(),
        anomaly_result(),
    )

    assert result["source"] == "mistral"

    assert result["confidence_score"] == 0.95

    assert analyzer.error_count == 0


def test_extreme_price_override_runs_after_schema_validation() -> None:
    payload = load_json("valid_signal.json")

    payload["recommended_trade"] = "BUY_YES"

    analyzer = make_analyzer(json.dumps(payload))

    extreme_snapshot = snapshot()
    extreme_snapshot["yes_price"] = 0.995

    result = analyzer.analyze_single(
        extreme_snapshot,
        anomaly_result(),
    )

    assert result["recommended_trade"] == "HOLD"

    assert result["recommended_position_size_pct"] == 0.0

    assert result["holding_period_hours"] == 0

    assert any("Price override" in evidence for evidence in result["counter_evidence"])
