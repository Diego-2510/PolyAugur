"""Strict parsing and JSON-Schema validation for untrusted LLM output."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "mistral_signal.schema.json"

_WRAPPER_KEYS = (
    "results",
    "markets",
    "analyses",
    "analysis",
)


class LLMOutputContractError(ValueError):
    """Raised when an LLM response violates the PolyAugur output contract."""


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    try:
        with _SCHEMA_PATH.open(
            "r",
            encoding="utf-8",
        ) as handle:
            schema = json.load(handle)

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(f"Unable to load LLM output schema at {_SCHEMA_PATH}: {exc}") from exc

    try:
        Draft202012Validator.check_schema(schema)

    except SchemaError as exc:
        raise RuntimeError(f"Invalid LLM output schema: {exc.message}") from exc

    return Draft202012Validator(schema)


def _format_path(
    parts: list[Any],
) -> str:
    path = "$"

    for part in parts:
        if isinstance(
            part,
            int,
        ):
            path += f"[{part}]"

        else:
            path += f".{part}"

    return path


def _error_sort_key(
    error: Any,
) -> tuple[str, str]:
    return (
        _format_path(list(error.absolute_path)),
        error.message,
    )


def validate_signal_payload(
    payload: Mapping[
        str,
        Any,
    ],
) -> None:
    """Validate one raw signal object without coercing model-produced values."""
    errors = sorted(
        _validator().iter_errors(payload),
        key=_error_sort_key,
    )

    if not errors:
        return

    details = [
        (f"{_format_path(list(error.absolute_path))}: {error.message}") for error in errors[:5]
    ]

    if len(errors) > 5:
        details.append(f"... and {len(errors) - 5} more error(s)")

    raise LLMOutputContractError("; ".join(details))


def parse_signal_response(
    raw: str,
    *,
    expected_count: int,
) -> list[
    dict[
        str,
        Any,
    ]
]:
    """
    Parse JSON, enforce exact cardinality,
    and validate every signal object.
    """
    if expected_count < 1:
        raise ValueError("expected_count must be at least 1")

    try:
        parsed = json.loads(raw)

    except json.JSONDecodeError as exc:
        raise LLMOutputContractError(f"invalid JSON: {exc.msg}") from exc

    if isinstance(
        parsed,
        dict,
    ):
        for key in _WRAPPER_KEYS:
            candidate = parsed.get(key)

            if isinstance(
                candidate,
                list,
            ):
                parsed = candidate
                break

        else:
            parsed = [parsed]

    elif not isinstance(
        parsed,
        list,
    ):
        raise LLMOutputContractError(
            f"top-level JSON must be an object or array, got {type(parsed).__name__}"
        )

    if len(parsed) != expected_count:
        raise LLMOutputContractError(
            f"expected {expected_count} signal object(s), received {len(parsed)}"
        )

    validated: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for index, item in enumerate(parsed):
        if not isinstance(
            item,
            dict,
        ):
            raise LLMOutputContractError(
                f"signal[{index}] must be an object, got {type(item).__name__}"
            )

        try:
            validate_signal_payload(item)

        except LLMOutputContractError as exc:
            raise LLMOutputContractError(f"signal[{index}]: {exc}") from exc

        validated.append(dict(item))

    return validated
