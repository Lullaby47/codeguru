import os
from pathlib import Path


TEMPLATE_DIR = Path("templates")


FORBIDDEN_PATTERNS = [
    "/auth/login",
    "/auth/signup",
]


def _scan_file(path: Path) -> list[str]:
    hits: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return hits
    for pat in FORBIDDEN_PATTERNS:
        if pat in text:
            hits.append(pat)
    return hits


def test_no_direct_auth_links_in_templates():
    """
    Guardrail: templates must not contain direct browser links/forms
    to /auth/login or /auth/signup.
    """
    assert TEMPLATE_DIR.exists(), "templates directory not found"

    offenders: list[str] = []

    for root, _dirs, files in os.walk(TEMPLATE_DIR):
        for name in files:
            if not name.endswith(".html"):
                continue
            path = Path(root) / name
            hits = _scan_file(path)
            if hits:
                offenders.append(f"{path}: {', '.join(hits)}")

    assert not offenders, (
        "Found forbidden /auth/... references in templates "
        "(use /login or /signup web routes instead):\n" + "\n".join(offenders)
    )


