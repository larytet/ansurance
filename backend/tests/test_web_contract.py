"""Every API path the web app calls exists in the back end (found: the Users page called
/api/auth/users while the route is /api/users, and its unit test mocked the wrong path too)."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"
LITERAL = re.compile(r"""["'`](/api/[^"'`\s]*)["'`]""")


def _norm(path: str) -> str:
    path = path.split("?")[0].rstrip("/")
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    return re.sub(r"\{[^}/]*\}", "{}", path)


def _web_paths() -> dict[str, str]:
    found: dict[str, str] = {}
    for f in WEB_SRC.rglob("*.ts*"):
        if ".test." in f.name or f.name == "testutil.tsx":
            continue
        for m in LITERAL.finditer(f.read_text()):
            found.setdefault(_norm(m.group(1)), str(f.relative_to(WEB_SRC)))
    return found


def test_the_web_app_only_calls_paths_the_back_end_serves(api: TestClient) -> None:
    served = {_norm(p) for p in api.app.openapi()["paths"]}  # type: ignore[attr-defined]
    called = _web_paths()
    assert len(called) > 40, "the scan found too few paths; the pattern is broken"
    missing = {p: f for p, f in called.items() if p not in served}
    assert not missing, f"called by the web app but not served: {missing}"
