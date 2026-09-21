"""Generate the two private browser-runtime contracts from their real FastAPI apps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.browser_tasks.adapter_app import app as adapter_app
from app.browser_tasks.manager_app import app as manager_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = {
    REPOSITORY_ROOT / "contracts/browser-controller-openapi.json": manager_app,
    REPOSITORY_ROOT / "contracts/browser-adapter-openapi.json": adapter_app,
}


def rendered_contract(app) -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail when committed contracts are stale"
    )
    args = parser.parse_args()
    stale: list[str] = []
    for path, app in CONTRACTS.items():
        rendered = rendered_contract(app)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                stale.append(str(path.relative_to(REPOSITORY_ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
    if stale:
        raise SystemExit("stale private browser contract(s): " + ", ".join(stale))


if __name__ == "__main__":
    main()
