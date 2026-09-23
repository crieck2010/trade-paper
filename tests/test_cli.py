import json
import pytest

from trade_paper.cli import build_parser, main


def test_parser_commands():
    p = build_parser()
    cases = {"init": ["init"], "discover": ["discover"], "run": ["run"],
             "schedule": ["schedule"], "approvals": ["approvals"],
             "approve": ["approve", "1"], "reject": ["reject", "2"],
             "status": ["status"], "reconcile": ["reconcile"],
             "fidelity": ["fidelity"], "license": ["license"],
             "update-check": ["update-check"]}
    for cmd, argv in cases.items():
        assert p.parse_args(argv).cmd == cmd


def test_init_writes_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--config", "c.json", "init"]) == 0
    data = json.loads((tmp_path / "c.json").read_text())
    assert data["schedule"]["slots"] == ["10:00", "13:00", "15:30"]


def test_schedule_prints(capsys):
    assert main(["schedule", "--print-cron"]) == 0
    out = capsys.readouterr().out
    assert "trade-paper run" in out


def test_approvals_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--config", "c.json", "init"]) == 0
    assert main(["--config", "c.json", "approvals"]) == 0
