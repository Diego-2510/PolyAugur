"""Offline evaluation utilities for PolyAugur benchmark predictions.

This module intentionally performs no external API calls. It evaluates stored
prediction records against a manually labelled benchmark and reports
classification, calibration, and optional token-cost metrics.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable


class EvaluationError(ValueError):
    """Raised when evaluation inputs are incomplete or invalid."""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationError(f"unable to read {path}: {exc}") from exc

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc

        if not isinstance(value, dict):
            raise EvaluationError(f"{path}:{line_number}: each JSONL record must be an object")

        records.append(value)

    if not records:
        raise EvaluationError(f"{path}: no JSONL records found")

    return records


def _index_unique(
    records: Iterable[dict[str, Any]],
    *,
    kind: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}

    for index, record in enumerate(records, 1):
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise EvaluationError(f"{kind} record {index}: case_id must be a non-empty string")

        if case_id in indexed:
            raise EvaluationError(f"duplicate {kind} case_id: {case_id}")

        indexed[case_id] = record

    return indexed


def _validate_label(record: dict[str, Any], case_id: str) -> bool:
    label = record.get("label")

    if type(label) is not bool:
        raise EvaluationError(f"label {case_id}: label must be boolean")

    return label


def _validate_prediction(
    record: dict[str, Any],
    case_id: str,
) -> tuple[bool, float]:
    anomaly_detected = record.get("anomaly_detected")
    confidence_score = record.get("confidence_score")

    if type(anomaly_detected) is not bool:
        raise EvaluationError(f"prediction {case_id}: anomaly_detected must be boolean")

    if isinstance(confidence_score, bool) or not isinstance(
        confidence_score,
        (int, float),
    ):
        raise EvaluationError(f"prediction {case_id}: confidence_score must be numeric")

    confidence = float(confidence_score)

    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise EvaluationError(
            f"prediction {case_id}: confidence_score must be finite and in [0, 1]"
        )

    for field in ("input_tokens", "output_tokens"):
        value = record.get(field)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise EvaluationError(f"prediction {case_id}: {field} must be a non-negative integer")

    return anomaly_detected, confidence


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _calibration_bins(
    confidences: list[float],
    labels: list[bool],
    *,
    bins: int,
) -> tuple[list[dict[str, Any]], float]:
    if bins < 2:
        raise EvaluationError("calibration bins must be at least 2")

    buckets: list[list[int]] = [[] for _ in range(bins)]

    for index, confidence in enumerate(confidences):
        bucket = min(int(confidence * bins), bins - 1)
        buckets[bucket].append(index)

    table: list[dict[str, Any]] = []
    total = len(confidences)
    ece = 0.0

    for bucket_index, indices in enumerate(buckets):
        lower = bucket_index / bins
        upper = (bucket_index + 1) / bins

        if not indices:
            table.append(
                {
                    "lower": lower,
                    "upper": upper,
                    "count": 0,
                    "avg_confidence": None,
                    "positive_rate": None,
                    "absolute_gap": None,
                }
            )
            continue

        avg_confidence = sum(confidences[i] for i in indices) / len(indices)
        positive_rate = sum(1 for i in indices if labels[i]) / len(indices)
        gap = abs(avg_confidence - positive_rate)

        ece += (len(indices) / total) * gap

        table.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(indices),
                "avg_confidence": avg_confidence,
                "positive_rate": positive_rate,
                "absolute_gap": gap,
            }
        )

    return table, ece


def _cost_report(
    prediction_records: list[dict[str, Any]],
    *,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> dict[str, Any]:
    input_tokens = [record.get("input_tokens") for record in prediction_records]
    output_tokens = [record.get("output_tokens") for record in prediction_records]

    token_counts_complete = all(type(value) is int for value in [*input_tokens, *output_tokens])

    total_input_tokens = sum(input_tokens) if token_counts_complete else None
    total_output_tokens = sum(output_tokens) if token_counts_complete else None

    report: dict[str, Any] = {
        "status": "not_measured",
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "estimated_cost_usd": None,
        "pricing": None,
    }

    if not token_counts_complete:
        report["reason"] = "prediction records do not contain complete token counts"
        return report

    if input_cost_per_million is None or output_cost_per_million is None:
        report["reason"] = "provider pricing was not supplied"
        return report

    if input_cost_per_million < 0 or output_cost_per_million < 0:
        raise EvaluationError("token pricing must be non-negative")

    estimated = (
        total_input_tokens / 1_000_000 * input_cost_per_million
        + total_output_tokens / 1_000_000 * output_cost_per_million
    )

    report.update(
        {
            "status": "estimated_from_supplied_pricing",
            "estimated_cost_usd": estimated,
            "pricing": {
                "input_cost_per_million_usd": input_cost_per_million,
                "output_cost_per_million_usd": output_cost_per_million,
            },
        }
    )
    report.pop("reason", None)
    return report


def evaluate(
    labels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    threshold: float = 0.80,
    calibration_bins: int = 10,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> dict[str, Any]:
    """Evaluate stored model predictions against manual benchmark labels."""
    if not 0.0 <= threshold <= 1.0:
        raise EvaluationError("threshold must be in [0, 1]")

    label_index = _index_unique(labels, kind="label")
    prediction_index = _index_unique(predictions, kind="prediction")

    missing = sorted(set(label_index) - set(prediction_index))
    extra = sorted(set(prediction_index) - set(label_index))

    if missing or extra:
        details = []
        if missing:
            details.append(f"missing predictions: {', '.join(missing)}")
        if extra:
            details.append(f"unknown predictions: {', '.join(extra)}")
        raise EvaluationError("; ".join(details))

    tp = fp = tn = fn = 0
    labels_binary: list[bool] = []
    confidences: list[float] = []
    ordered_predictions: list[dict[str, Any]] = []

    for case_id in label_index:
        label = _validate_label(label_index[case_id], case_id)
        prediction_record = prediction_index[case_id]
        anomaly_detected, confidence = _validate_prediction(
            prediction_record,
            case_id,
        )

        predicted_positive = anomaly_detected and confidence >= threshold

        if label and predicted_positive:
            tp += 1
        elif not label and predicted_positive:
            fp += 1
        elif label and not predicted_positive:
            fn += 1
        else:
            tn += 1

        labels_binary.append(label)
        confidences.append(confidence)
        ordered_predictions.append(prediction_record)

    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)

    if precision is None or recall is None or precision + recall == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)

    accuracy = (tp + tn) / len(labels_binary)
    brier_score = sum(
        (confidence - float(label)) ** 2 for confidence, label in zip(confidences, labels_binary)
    ) / len(labels_binary)

    calibration_table, ece = _calibration_bins(
        confidences,
        labels_binary,
        bins=calibration_bins,
    )

    return {
        "benchmark": {
            "cases": len(labels_binary),
            "positive_labels": sum(labels_binary),
            "negative_labels": len(labels_binary) - sum(labels_binary),
            "decision_rule": (
                f"positive iff anomaly_detected=true and confidence_score>={threshold:.2f}"
            ),
            "target": "manual review escalation according to benchmark rubric",
        },
        "classification": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "accuracy": accuracy,
        },
        "calibration": {
            "brier_score": brier_score,
            "expected_calibration_error": ece,
            "bins": calibration_table,
            "interpretation": (
                "Calibration is measured against benchmark labels, not verified "
                "ground truth of actual insider trading."
            ),
        },
        "cost": _cost_report(
            ordered_predictions,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
        ),
    }


def evaluate_files(
    labels_path: Path,
    predictions_path: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    return evaluate(
        _load_jsonl(labels_path),
        _load_jsonl(predictions_path),
        **kwargs,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate stored PolyAugur predictions without external API calls."
    )
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threshold", type=float, default=0.80)
    parser.add_argument("--calibration-bins", type=int, default=10)
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    try:
        report = evaluate_files(
            args.labels,
            args.predictions,
            threshold=args.threshold,
            calibration_bins=args.calibration_bins,
            input_cost_per_million=args.input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
        )
    except EvaluationError as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, sort_keys=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
