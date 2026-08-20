from __future__ import annotations

import os

from src import config


def test_project_env_loader_uses_explicit_directory(monkeypatch, tmp_path):
    key = "DONGTTOK_TEST_ENV_FROM_FILE"
    monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(f"{key}=loaded\n", encoding="utf-8")

    assert config._load_project_env(tmp_path) is True
    assert os.environ[key] == "loaded"


def test_project_env_loader_never_overrides_process_environment(monkeypatch, tmp_path):
    key = "DONGTTOK_TEST_ENV_PRECEDENCE"
    monkeypatch.setenv(key, "deployed-value")
    (tmp_path / ".env").write_text(f"{key}=local-value\n", encoding="utf-8")

    assert config._load_project_env(tmp_path) is True
    assert os.environ[key] == "deployed-value"
