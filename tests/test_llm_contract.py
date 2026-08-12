import json
from pathlib import Path

import pytest

from src.llm_contract import (
    LLMOutputContractError,
    parse_signal_response,
    validate_signal_payload,
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


def test_valid_signal_fixture_matches_schema() -> None:
    validate_signal_payload(load_json("valid_signal.json"))


@pytest.mark.parametrize(
    (
        "fixture_name",
        "expected_fragment",
    ),
    [
        (
            "invalid_missing_field.json",
            "risk_level",
        ),
        (
            "invalid_enum.json",
            "BUY",
        ),
        (
            "invalid_type.json",
            "not of type 'number'",
        ),
    ],
)
def test_invalid_signal_fixtures_are_rejected(
    fixture_name: str,
    expected_fragment: str,
) -> None:
    with pytest.raises(LLMOutputContractError) as exc_info:
        validate_signal_payload(load_json(fixture_name))

    assert expected_fragment in str(exc_info.value)


def test_additional_fields_are_rejected() -> None:
    payload = load_json("valid_signal.json")

    payload["unexpected"] = "not part of the contract"

    with pytest.raises(
        LLMOutputContractError,
        match="unexpected",
    ):
        validate_signal_payload(payload)


def test_position_size_above_configured_contract_limit_is_rejected() -> None:
    payload = load_json("valid_signal.json")

    payload["recommended_position_size_pct"] = 0.11

    with pytest.raises(
        LLMOutputContractError,
        match="maximum of 0.1",
    ):
        validate_signal_payload(payload)


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(
        LLMOutputContractError,
        match="invalid JSON",
    ):
        parse_signal_response(
            load_text("malformed.txt"),
            expected_count=1,
        )


def test_batch_cardinality_must_match_exactly() -> None:
    raw = json.dumps({"results": [load_json("valid_signal.json")]})

    with pytest.raises(
        LLMOutputContractError,
        match="expected 2",
    ):
        parse_signal_response(
            raw,
            expected_count=2,
        )


def test_supported_wrapper_is_unwrapped_and_validated() -> None:
    signal = load_json("valid_signal.json")

    raw = json.dumps({"results": [signal]})

    assert parse_signal_response(
        raw,
        expected_count=1,
    ) == [signal]


def test_non_object_top_level_value_is_rejected() -> None:
    with pytest.raises(
        LLMOutputContractError,
        match="top-level JSON",
    ):
        parse_signal_response(
            json.dumps("invalid"),
            expected_count=1,
        )
