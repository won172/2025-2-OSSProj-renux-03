from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from scripts.select_collection_pointer import POINTER_ENV, select_pointer


RAG_ROOT = Path(__file__).resolve().parents[1]


def _pointer(path: Path, targets: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "collections": {key: {"active": value} for key, value in targets.items()},
    }), encoding="utf-8")
    return path


def _catalog(artifacts: Path, names: list[str], *, tenant: str = "default_tenant") -> Path:
    chroma = artifacts / "db_chroma" / "chroma.sqlite3"
    chroma.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(chroma) as connection:
        connection.executescript(
            "CREATE TABLE databases (id TEXT PRIMARY KEY, name TEXT, tenant_id TEXT);"
            "CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT, database_id TEXT);"
        )
        connection.execute("INSERT INTO databases VALUES ('db', 'default_database', ?)", (tenant,))
        connection.executemany("INSERT INTO collections VALUES (?, ?, 'db')", enumerate(names))
    return chroma


def _recovery_layout(artifacts: Path, *, names: list[str] | None = None):
    normal = _pointer(artifacts / "collection_pointers.json", {"notices": "stale"})
    recovered = _pointer(artifacts / "db_chroma" / "collection_pointers_rebuilt.json", {"notices": "rebuilt"})
    _catalog(artifacts, names if names is not None else ["rebuilt"])
    return normal, recovered


def test_stale_default_uses_persisted_recovery_without_mutating_runtime_files(tmp_path, capsys):
    # URI escaping matters: '?' and '#' must be filename characters, not options.
    artifacts = tmp_path / "data ? # 한글"
    _, recovered = _recovery_layout(artifacts)
    before = {path.relative_to(artifacts): path.read_bytes() for path in artifacts.rglob("*") if path.is_file()}

    assert select_pointer(artifacts, None) == recovered

    after = {path.relative_to(artifacts): path.read_bytes() for path in artifacts.rglob("*") if path.is_file()}
    assert after == before
    output = capsys.readouterr()
    assert output.out == ""
    assert "using recovered" in output.err


def test_valid_default_wins_even_if_recovery_is_corrupt(tmp_path):
    normal, recovered = _recovery_layout(tmp_path, names=["stale", "rebuilt"])
    recovered.write_text("not JSON", encoding="utf-8")
    assert select_pointer(tmp_path, None) == normal


def test_valid_explicit_override_is_authoritative(tmp_path):
    _recovery_layout(tmp_path)
    explicit = _pointer(tmp_path / "chosen.json", {"notices": "rebuilt"})
    assert select_pointer(tmp_path, str(explicit)) == explicit


@pytest.mark.parametrize("failure", ["missing", "json", "schema", "map", "record", "target"])
def test_invalid_explicit_override_never_falls_back(tmp_path, failure):
    _recovery_layout(tmp_path)
    explicit = tmp_path / "chosen.json"
    if failure == "json":
        explicit.write_text("{broken", encoding="utf-8")
    elif failure in {"schema", "map", "record"}:
        state = {"schema_version": 2 if failure == "schema" else 1, "collections": [] if failure == "map" else {"notices": {}}}
        explicit.write_text(json.dumps(state), encoding="utf-8")
    elif failure == "target":
        _pointer(explicit, {"notices": "does-not-exist"})
    with pytest.raises(ValueError):
        select_pointer(tmp_path, str(explicit))


@pytest.mark.parametrize("contents", ["{broken", '{"schema_version": 2, "collections": {}}', '{"schema_version": 1, "collections": {"notices": null}}'])
def test_corrupt_default_does_not_trigger_recovery(tmp_path, contents):
    normal, _ = _recovery_layout(tmp_path)
    normal.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        select_pointer(tmp_path, None)


@pytest.mark.parametrize("failure", ["target", "coverage", "empty", "json"])
def test_invalid_recovery_is_rejected(tmp_path, failure):
    _, recovered = _recovery_layout(tmp_path)
    if failure == "json":
        recovered.write_text("{broken", encoding="utf-8")
    else:
        _pointer(recovered, {"notices": "missing"} if failure == "target" else {"rules": "rebuilt"} if failure == "coverage" else {})
    with pytest.raises(ValueError):
        select_pointer(tmp_path, None)


@pytest.mark.parametrize("failure", ["missing", "corrupt", "schema", "wrong-tenant"])
def test_missing_or_unreadable_catalog_never_selects_recovery(tmp_path, failure):
    _, recovered = _recovery_layout(tmp_path)
    catalog = recovered.parent / "chroma.sqlite3"
    catalog.unlink()
    if failure == "corrupt":
        catalog.write_bytes(b"not sqlite")
    elif failure == "schema":
        with sqlite3.connect(catalog) as connection:
            connection.execute("CREATE TABLE unrelated (name TEXT)")
    elif failure == "wrong-tenant":
        _catalog(tmp_path, ["rebuilt"], tenant="other_tenant")
    before = {path.name for path in recovered.parent.iterdir()}
    with pytest.raises(ValueError):
        select_pointer(tmp_path, None)
    assert {path.name for path in recovered.parent.iterdir()} == before


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_nonempty_journal_refuses_immutable_catalog_inspection(tmp_path, suffix):
    _recovery_layout(tmp_path)
    journal = tmp_path / "db_chroma" / f"chroma.sqlite3{suffix}"
    journal.write_bytes(b"uncheckpointed content")
    with pytest.raises(ValueError, match="journaled Chroma catalog"):
        select_pointer(tmp_path, None)
    assert journal.read_bytes() == b"uncheckpointed content"


def test_fresh_setup_preserves_existing_behavior_and_creates_no_database(tmp_path):
    assert select_pointer(tmp_path, None) is None
    normal = _pointer(tmp_path / "collection_pointers.json", {"notices": "not-built-yet"})
    assert select_pointer(tmp_path, "") is None
    assert list(tmp_path.iterdir()) == [normal]


def test_recovery_can_supply_missing_default_file(tmp_path):
    normal, recovered = _recovery_layout(tmp_path)
    normal.unlink()
    assert select_pointer(tmp_path, None) == recovered


def test_empty_default_keeps_logical_collection_behavior(tmp_path):
    normal, _ = _recovery_layout(tmp_path)
    _pointer(normal, {})
    assert select_pointer(tmp_path, None) == normal


def test_cli_outputs_only_selected_path_and_reports_invalid_override(tmp_path):
    _, recovered = _recovery_layout(tmp_path)
    environment = dict(os.environ)
    environment.pop(POINTER_ENV, None)
    command = [sys.executable, str(RAG_ROOT / "scripts/select_collection_pointer.py"), "--artifact-dir", str(tmp_path)]
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == f"{recovered}\n"
    assert "using recovered" in result.stderr

    environment[POINTER_ENV] = str(tmp_path / "missing.json")
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "collection pointer is unreadable" in result.stderr


def test_entrypoint_exports_selection_and_preserves_command_exit_code(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    entrypoint = app / "docker-entrypoint.sh"
    shutil.copyfile(RAG_ROOT / "docker-entrypoint.sh", entrypoint)
    (app / "scripts").mkdir()
    shutil.copyfile(RAG_ROOT / "scripts/select_collection_pointer.py", app / "scripts/select_collection_pointer.py")
    _, recovered = _recovery_layout(app / "artifacts")
    executables = tmp_path / "bin"
    executables.mkdir()
    # The test has no root/user database requirement; exercise the actual shell
    # entrypoint while replacing only its privilege/ownership boundary.
    for name, contents in {
        "gosu": '#!/bin/sh\nshift\nexec "$@"\n',
        "chown": "#!/bin/sh\nexit 0\n",
    }.items():
        command = executables / name
        command.write_text(contents, encoding="utf-8")
        command.chmod(0o755)
    (executables / "python").symlink_to(sys.executable)
    environment = dict(os.environ, PATH=f"{executables}{os.pathsep}{os.environ['PATH']}")
    environment.pop(POINTER_ENV, None)
    result = subprocess.run(
        ["sh", str(entrypoint), sys.executable, "-c", f"import os,sys; print(os.environ[{POINTER_ENV!r}]); sys.exit(23)"],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 23
    assert result.stdout == f"{recovered}\n"

    environment[POINTER_ENV] = str(app / "missing.json")
    result = subprocess.run(["sh", str(entrypoint), "echo", "must-not-start"], env=environment, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
