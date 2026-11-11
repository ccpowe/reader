import json

import pytest

from reader import cli


def test_reset_password_reads_the_password_interactively(monkeypatch, capsys) -> None:
    prompts = iter(["new password 1", "new password 1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(prompts))
    seen = {}

    async def fake_reset(email: str, password: str) -> dict:
        seen.update(email=email, password=password)
        return {"user_id": "u", "status": "password_reset", "sessions_revoked": True}

    monkeypatch.setattr(cli, "_reset_password", fake_reset)

    assert cli.main(["users", "reset-password", " Me@Example.COM "]) == 0
    assert seen == {"email": "me@example.com", "password": "new password 1"}
    assert json.loads(capsys.readouterr().out)["status"] == "password_reset"


@pytest.mark.parametrize(
    ("answers", "message"),
    [(["one password", "another one"], "do not match"), (["short", "short"], "8–128")],
)
def test_reset_password_rejects_bad_input(monkeypatch, capsys, answers, message) -> None:
    prompts = iter(answers)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(prompts))

    assert cli.main(["users", "reset-password", "me@example.com"]) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("command", ["codex-auth", "translation-quota"])
def test_unported_commands_go_to_the_old_cli(monkeypatch, command) -> None:
    import app.admin.cli as old_cli

    calls = []
    monkeypatch.setattr(old_cli, "main", lambda argv: calls.append(argv) or 0)

    assert cli.main([command, "status"]) == 0
    assert calls == [[command, "status"]]
