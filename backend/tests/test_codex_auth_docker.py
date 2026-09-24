"""Validate deployment wiring without starting services or reading real secrets."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI unavailable")
def test_compose_shares_private_writable_auth_volume_only_with_credential_owners():
    environment = {"PATH": os.environ["PATH"]}
    for name in re.findall(r"\$\{([A-Z_]+)(?::\?[^}]+)?\}", (ROOT / "compose.yaml").read_text()):
        environment[name] = "reader-test-" + name.lower().replace("_", "-")
    environment.update(
        DOCKER_GID="999",
        READER_SECRET_GID="1000",
        READER_DEPLOYMENT_ID="test",
        APP_TRANSLATION_DEFAULT_ENGINE_ID="codex-subscription",
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--profile",
            "tools",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    owners = []
    for name, service in services.items():
        for volume in service.get("volumes", []):
            if volume["source"] == "codex-auth":
                owners.append(name)
                assert volume["target"] == "/var/lib/reader-codex"
                assert not volume.get("read_only", False)
    assert set(owners) == {"api", "worker", "codex-auth-init"}
    for name in ("api", "worker"):
        service = services[name]
        assert service["user"] == "10001:10001"
        assert service["read_only"]
        assert (
            service["environment"]["APP_CODEX_SUBSCRIPTION_AUTH_FILE"]
            == "/var/lib/reader-codex/auth.json"
        )
        assert service["environment"]["APP_CODEX_SUBSCRIPTION_MODEL"] == "gpt-6-luna"
    assert services["codex-auth-init"]["network_mode"] == "none"
    assert "CHOWN" in services["codex-auth-init"]["cap_add"]


@pytest.mark.parametrize("failure", [False, True])
def test_docker_import_uses_stdin_and_restarts_only_after_success(tmp_path, failure):
    shutil.copy(ROOT / "docker-init.sh", tmp_path / "docker-init.sh")
    (tmp_path / ".env.docker").write_text("APP_TRANSLATION_DEFAULT_ENGINE_ID=codex-subscription\n")
    for directory in (".docker", ".docker/inputs", ".docker/secrets", ".docker/runtime", "bin"):
        (tmp_path / directory).mkdir(mode=0o700)
    (tmp_path / ".docker/runtime/active.env").write_text("READER_IMAGE_ID=synthetic\n")
    (tmp_path / ".docker/runtime/base.env").write_text("COMPOSE_PROJECT_NAME=synthetic\n")
    source = tmp_path / "bootstrap.json"
    source.write_text("synthetic-credential-material")
    source.chmod(0o600)
    log = tmp_path / "calls.jsonl"
    fake = tmp_path / "bin/docker"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
args = sys.argv[1:]
is_import = "import" in args
matches = sys.stdin.read() == "synthetic-credential-material" if is_import else None
record = {"args": args, "stdin_matches": matches}
with open(os.environ["TEST_CALLS"], "a") as stream:
    stream.write(json.dumps(record) + "\\n")
if is_import and os.environ["TEST_IMPORT_FAILURE"] == "1":
    sys.exit(2)
"""
    )
    fake.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"],
        "TEST_CALLS": str(log),
        "TEST_IMPORT_FAILURE": "1" if failure else "0",
    }
    result = subprocess.run(
        [
            "bash",
            str(tmp_path / "docker-init.sh"),
            "codex-auth",
            "import",
            str(source),
            "--replace",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == (2 if failure else 0)
    assert "synthetic-credential-material" not in result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert (
        calls[0]["args"][-7:]
        == ["api", "reader-admin", "codex-auth", "import", "--source", "-", "--replace"]
    )
    assert calls[0]["stdin_matches"]
    assert len(calls) == (1 if failure else 2)
    if not failure:
        assert calls[1]["args"][-3:] == ["restart", "api", "worker"]
