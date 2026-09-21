from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_entrypoint_loads_youtube_key_from_file_without_forwarding_file_path(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "youtube-data-api-key"
    secret.write_text("synthetic-youtube-key\n")
    secret.chmod(0o440)
    entrypoint = Path(__file__).parents[1] / "docker-entrypoint.py"
    environment = os.environ.copy()
    environment["APP_YOUTUBE_DATA_API_KEY_FILE"] = str(secret)

    completed = subprocess.run(
        [
            sys.executable,
            str(entrypoint),
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.environ.get('APP_YOUTUBE_DATA_API_KEY') == "
                "'synthetic-youtube-key'); "
                "print('APP_YOUTUBE_DATA_API_KEY_FILE' in os.environ)"
            ),
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.stdout.splitlines() == ["True", "False"]
