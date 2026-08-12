from __future__ import annotations

import pytest

from src.evaluation import EvaluationError, evaluate


def label(case_id: str, value: bool) -> dict:
    return {"case_id": case_id, "label": value}


def prediction(
    case_id: str,
    anomaly_detected: bool,
    confidence_score: float,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict:
    record = {
        "case_id": case_id,
        "anomaly_detected": anomaly_detected,
        "confidence_score": confidence_score,
    }
    if input_tokens is not None:
        record["input_tokens"] = input_tokens
    if output_tokens is not None:
        record["output_tokens"] = output_tokens
    return record


def test_perfect_classification() -> None:
    report = evaluate(
        [
            label("a", True),
            label("b", False),
            label("c", True),
            label("d", False),
        ],
        [
            prediction("a", True, 0.95),
            prediction("b", False, 0.10),
            prediction("c", True, 0.80),
            prediction("d", False, 0.20),
        ],
        threshold=0.80,
        calibration_bins=5,
    )

    classification = report["classification"]
    assert classification["true_positive"] == 2
    assert classification["false_positive"] == 0
    assert classification["true_negative"] == 2
    assert classification["false_negative"] == 0
    assert classification["precision"] == pytest.approx(1.0)
    assert classification["recall"] == pytest.approx(1.0)
    assert classification["f1"] == pytest.approx(1.0)
    assert classification["accuracy"] == pytest.approx(1.0)


def test_threshold_uses_anomaly_flag_and_confidence() -> None:
    report = evaluate(
        [label("a", True), label("b", False), label("c", True)],
        [
            prediction("a", True, 0.79),
            prediction("b", True, 0.90),
            prediction("c", False, 0.99),
        ],
        threshold=0.80,
    )

    classification = report["classification"]
    assert classification["true_positive"] == 0
    assert classification["false_positive"] == 1
    assert classification["false_negative"] == 2
    assert classification["true_negative"] == 0
    assert classification["precision"] == pytest.approx(0.0)
    assert classification["recall"] == pytest.approx(0.0)
    assert classification["f1"] is None


def test_brier_and_ece_are_reported() -> None:
    report = evaluate(
        [label("a", True), label("b", False)],
        [
            prediction("a", True, 0.8),
            prediction("b", False, 0.2),
        ],
        calibration_bins=2,
    )

    calibration = report["calibration"]
    assert calibration["brier_score"] == pytest.approx(0.04)
    assert calibration["expected_calibration_error"] == pytest.approx(0.2)


def test_cost_requires_explicit_pricing() -> None:
    report = evaluate(
        [label("a", True)],
        [
            prediction(
                "a",
                True,
                0.9,
                input_tokens=1_000,
                output_tokens=200,
            )
        ],
    )

    assert report["cost"]["status"] == "not_measured"
    assert report["cost"]["estimated_cost_usd"] is None


def test_cost_uses_supplied_token_pricing() -> None:
    report = evaluate(
        [label("a", True)],
        [
            prediction(
                "a",
                True,
                0.9,
                input_tokens=1_000_000,
                output_tokens=500_000,
            )
        ],
        input_cost_per_million=2.0,
        output_cost_per_million=6.0,
    )

    assert report["cost"]["status"] == "estimated_from_supplied_pricing"
    assert report["cost"]["estimated_cost_usd"] == pytest.approx(5.0)


def test_missing_predictions_fail_closed() -> None:
    with pytest.raises(EvaluationError, match="missing predictions"):
        evaluate(
            [label("a", True), label("b", False)],
            [prediction("a", True, 0.9)],
        )


def test_unknown_predictions_fail_closed() -> None:
    with pytest.raises(EvaluationError, match="unknown predictions"):
        evaluate(
            [label("a", True)],
            [
                prediction("a", True, 0.9),
                prediction("extra", False, 0.1),
            ],
        )


def test_duplicate_case_ids_are_rejected() -> None:
    with pytest.raises(EvaluationError, match="duplicate label case_id"):
        evaluate(
            [label("a", True), label("a", False)],
            [prediction("a", True, 0.9)],
        )


def test_invalid_confidence_is_rejected() -> None:
    with pytest.raises(EvaluationError, match=r"confidence_score must be finite and in \[0, 1\]"):
        evaluate(
            [label("a", True)],
            [prediction("a", True, 1.5)],
        )
