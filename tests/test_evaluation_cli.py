import sys

from src import evaluation


def test_main_returns_two_for_missing_input_files(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "--labels",
            str(tmp_path / "missing-labels.jsonl"),
            "--predictions",
            str(tmp_path / "missing-predictions.jsonl"),
        ],
    )
    assert evaluation.main() == 2
    assert "evaluation error:" in capsys.readouterr().err
