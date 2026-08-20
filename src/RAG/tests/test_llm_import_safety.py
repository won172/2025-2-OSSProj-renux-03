from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


RAG_ROOT = Path(__file__).resolve().parents[1]


def test_rag_service_import_does_not_require_openai_credentials() -> None:
    """CI and readiness checks may import the app before secrets are mounted."""
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = ""
    env["OPENAI_ADMIN_KEY"] = ""
    result = subprocess.run(
        [sys.executable, "-c", "import api.rag_service"],
        cwd=RAG_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
