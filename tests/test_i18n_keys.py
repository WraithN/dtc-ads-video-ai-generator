"""Frontend i18n key regression guard.

The frontend's translation keys are plain strings passed to ``t('...')``, so a
key that is missing from the locale files only surfaces at runtime as the raw
key text. This test runs the Node check in ``web/frontend/scripts/i18n-check.mjs``
which extracts every key from ``zh.ts``/``en.ts`` and asserts that every
``t('...')`` / ``i18n.t('...')`` call site references a real key.

It is intentionally runnable from the Python suite so the whole project has one
``pytest tests`` entry point. It skips (rather than fails) when Node is not
installed, so a Python-only CI still passes.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "web" / "frontend"
CHECK_SCRIPT = FRONTEND / "scripts" / "i18n-check.mjs"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not on PATH; install Node to run the i18n key check",
)


def test_i18n_keys_are_defined():
    assert CHECK_SCRIPT.exists(), f"missing check script: {CHECK_SCRIPT}"
    proc = subprocess.run(
        [NODE, str(CHECK_SCRIPT)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            "i18n key check failed:\n"
            f"exit={proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    assert "i18n check passed" in proc.stdout, proc.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
