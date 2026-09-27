"""Build-time canonical lineage gate for index build/rebuild scripts.

No real artifacts, DB, Chroma, embedding model, or network: SourceDocument rows
live in an in-memory SQLite session, chunk artifacts are tmp parquet files, and
Chroma is replaced by an injected collection snapshot loader.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, SourceDocument
from src.services import canonical_lineage
from scripts import _lineage_gate

SECRET_TITLE = "출력되면 안 되는 제목"
SECRET_TEXT = "출력되면 안 되는 본문"


@pytest.fixture
def lineage_world(tmp_path, monkeypatch):
    """Two canonical datasets (notices, rules) with consistent derived layers."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    artifacts = {}
    chroma: dict[str, tuple[list[str], list[dict]]] = {}
    for dataset in ("notices", "rules"):
        session.add(
            SourceDocument(
                dataset=dataset,
                source_type="fixture",
                source_id="1",
                source_url=f"fixture://{dataset}/1",
                document_key=f"{dataset}:1",
                title=SECRET_TITLE,
                status="active",
            )
        )
        path = tmp_path / f"{dataset}.parquet"
        pd.DataFrame(
            {
                "chunk_id": [f"{dataset}-c1", f"{dataset}-c2"],
                "doc_id": [f"{dataset}:1", f"{dataset}:1"],
                "chunk_text": [SECRET_TEXT, SECRET_TEXT],
            }
        ).to_parquet(path, index=False)
        collection = f"fixture_{dataset}"
        artifacts[dataset] = SimpleNamespace(chunk_path=path, collection=collection)
        chroma[collection] = (
            [f"{dataset}-c1", f"{dataset}-c2"],
            [{"doc_id": f"{dataset}:1"}, {"doc_id": f"{dataset}:1"}],
        )
    session.commit()

    calls: list[list[str]] = []
    real_report = canonical_lineage.build_canonical_lineage_report

    def live_loader(name):
        return chroma[name]

    def fake_report(session_arg=None, *, artifacts=None, collection_snapshot_loader=None):
        calls.append(sorted(artifacts))
        return real_report(
            session,
            artifacts=artifacts,
            collection_snapshot_loader=collection_snapshot_loader or live_loader,
        )

    monkeypatch.setattr(_lineage_gate, "build_canonical_lineage_report", fake_report)
    monkeypatch.setattr(_lineage_gate, "DATASET_ARTIFACTS", artifacts)
    world = SimpleNamespace(
        session=session,
        artifacts=artifacts,
        chroma=chroma,
        calls=calls,
        loader=live_loader,
        tmp_path=tmp_path,
    )

    def break_dataset(dataset: str, collection: str | None = None) -> None:
        name = collection or f"fixture_{dataset}"
        chroma[name] = (["stray-chunk"], [{"doc_id": "wrong"}])

    world.break_dataset = break_dataset
    yield world
    session.close()


# --------------------------------------------------------------------------- helper


def test_gate_passes_when_lineage_is_consistent(lineage_world):
    stream = io.StringIO()
    gate = _lineage_gate.run_lineage_gate(
        ["rules"], skip=False, phase=_lineage_gate.POST_PUBLISH, stage="t", stream=stream
    )
    assert gate["status"] == "passed"
    assert gate["exit_code"] == 0
    assert lineage_world.calls == [["rules"]]


def test_gate_fails_on_mismatch_with_aggregate_only_output(lineage_world):
    lineage_world.break_dataset("rules")
    stream = io.StringIO()
    gate = _lineage_gate.run_lineage_gate(
        ["rules"], skip=False, phase=_lineage_gate.POST_PUBLISH, stage="t", stream=stream
    )
    text = stream.getvalue()
    assert gate["status"] == "failed"
    assert gate["exit_code"] == 1
    assert "artifact_missing_chroma" in text
    assert "PUBLISHED but lineage FAILED" in text
    rendered = text + json.dumps(gate, ensure_ascii=False)
    assert SECRET_TITLE not in rendered
    assert SECRET_TEXT not in rendered
    assert "stray-chunk" not in rendered


def test_gate_pre_publish_failure_says_nothing_published(lineage_world):
    lineage_world.break_dataset("rules")
    stream = io.StringIO()
    gate = _lineage_gate.run_lineage_gate(
        ["rules"], skip=False, phase=_lineage_gate.PRE_PUBLISH, stage="t", stream=stream
    )
    assert gate["exit_code"] == 1
    assert "Nothing was published" in stream.getvalue()


def test_gate_is_scoped_to_touched_datasets(lineage_world):
    lineage_world.break_dataset("notices")  # untouched dataset is broken
    gate = _lineage_gate.run_lineage_gate(
        ["rules"], skip=False, phase=_lineage_gate.POST_PUBLISH, stage="t", stream=io.StringIO()
    )
    assert gate["exit_code"] == 0
    assert lineage_world.calls == [["rules"]]


def test_gate_skip_warns_loudly_and_records_skip(lineage_world):
    lineage_world.break_dataset("rules")
    stream = io.StringIO()
    gate = _lineage_gate.run_lineage_gate(
        ["rules"], skip=True, phase=_lineage_gate.POST_PUBLISH, stage="t", stream=stream
    )
    assert gate["status"] == "skipped"
    assert gate["exit_code"] == 0
    assert "WARNING" in stream.getvalue()
    assert "SKIPPED" in stream.getvalue()
    assert lineage_world.calls == []


def test_gate_without_touched_datasets_is_not_applicable(lineage_world):
    gate = _lineage_gate.run_lineage_gate(
        [], skip=False, phase=_lineage_gate.POST_PUBLISH, stage="t", stream=io.StringIO()
    )
    assert gate["status"] == "not_applicable"
    assert gate["exit_code"] == 0
    assert lineage_world.calls == []


def test_client_snapshot_loader_reads_physical_collection_lazily():
    created = []

    class _Collection:
        def get(self, include, limit):
            assert include == ["metadatas"]
            return {"ids": [" a "], "metadatas": [{"doc_id": "d"}]}

    class _Client:
        def get_collection(self, name):
            assert name == "physical__build__x"
            return _Collection()

    loader = _lineage_gate.client_snapshot_loader(lambda: created.append(1) or _Client())
    assert created == []
    assert loader("physical__build__x") == (["a"], [{"doc_id": "d"}])
    loader("physical__build__x")
    assert created == [1]


# --------------------------------------------------------------------------- build_indices


@pytest.fixture
def build_indices(monkeypatch):
    from scripts import build_indices as module

    reindexed: list[str] = []
    monkeypatch.setattr(module, "init_db", lambda: None)
    monkeypatch.setattr(module, "backfill_static_source_documents", lambda targets: {})

    def fake_reindex(key):
        reindexed.append(key)
        return {key: (pd.DataFrame({"chunk_id": ["x"]}), None, None)}

    monkeypatch.setattr(module, "reindex_from_db", fake_reindex)
    module.reindexed = reindexed
    return module


def test_build_indices_exits_zero_when_lineage_passes(lineage_world, build_indices):
    assert build_indices.main(["--datasets", "rules"]) == 0
    assert build_indices.reindexed == ["rules"]
    assert lineage_world.calls == [["rules"]]


def test_build_indices_exits_nonzero_when_lineage_fails(lineage_world, build_indices, capsys):
    lineage_world.break_dataset("rules")
    assert build_indices.main(["--datasets", "rules"]) == 1
    assert "PUBLISHED but lineage FAILED" in capsys.readouterr().err


def test_build_indices_skip_flag_exits_zero_with_warning(lineage_world, build_indices, capsys):
    lineage_world.break_dataset("rules")
    assert build_indices.main(["--datasets", "rules", "--skip-lineage-gate"]) == 0
    assert "WARNING" in capsys.readouterr().err
    assert lineage_world.calls == []


def test_build_indices_does_not_gate_datasets_without_data(lineage_world, build_indices, monkeypatch):
    monkeypatch.setattr(build_indices, "reindex_from_db", lambda key: {})
    lineage_world.break_dataset("rules")
    assert build_indices.main(["--datasets", "rules"]) == 0
    assert lineage_world.calls == []


# --------------------------------------------------------------------------- lexical


@pytest.fixture
def lexical(monkeypatch):
    from scripts import rebuild_lexical_indices as module

    rebuilt: list[list[str]] = []
    monkeypatch.setattr(module, "rebuild", lambda datasets: rebuilt.append(list(datasets)) or 0)
    monkeypatch.setattr(module, "show_state", lambda: None)
    monkeypatch.setattr(module, "_load_kiwi", lambda: None)
    module.rebuilt = rebuilt
    return module


def test_lexical_rebuild_runs_after_lineage_passes(lineage_world, lexical):
    assert lexical.main(["--datasets", "notices"]) == 0
    assert lexical.rebuilt == [["notices"]]
    assert lineage_world.calls == [["notices"]]


def test_lexical_rebuild_refuses_to_publish_on_lineage_failure(lineage_world, lexical, capsys):
    lineage_world.break_dataset("notices")
    assert lexical.main(["--datasets", "notices"]) == 1
    assert lexical.rebuilt == []
    assert "Nothing was published" in capsys.readouterr().err


def test_lexical_rebuild_skip_flag_proceeds_with_warning(lineage_world, lexical, capsys):
    lineage_world.break_dataset("notices")
    assert lexical.main(["--datasets", "notices", "--skip-lineage-gate"]) == 0
    assert lexical.rebuilt == [["notices"]]
    assert "WARNING" in capsys.readouterr().err


# --------------------------------------------------------------------------- isolated dense


@pytest.fixture
def isolated(lineage_world, monkeypatch):
    from scripts import rebuild_dense_indices_isolated as module

    staged_calls: list[list[str]] = []
    staged_chroma = {
        "fixture_rules": lineage_world.chroma["fixture_rules"],
        "fixture_notices": lineage_world.chroma["fixture_notices"],
    }

    def fake_inputs(chroma_dir, datasets):
        staged_calls.append(list(datasets))
        return (
            {key: lineage_world.artifacts[key] for key in datasets},
            lambda name: staged_chroma[name],
        )

    monkeypatch.setattr(module, "_staged_lineage_inputs", fake_inputs)
    monkeypatch.setattr(
        module, "build_staged_datasets", lambda **kwargs: {"status": "verified", "datasets": {}}
    )
    module.staged_calls = staged_calls
    module.staged_chroma = staged_chroma
    return module


def test_isolated_build_passes_lineage_against_staged_store(lineage_world, isolated, capsys, tmp_path):
    code = isolated.main(["--chroma-dir", str(tmp_path / "staged"), "build", "--dataset", "rules"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["lineage_gate"]["status"] == "passed"
    assert isolated.staged_calls == [["rules"]]
    assert lineage_world.calls == [["rules"]]


def test_isolated_build_fails_when_staged_store_mismatches(lineage_world, isolated, capsys, tmp_path):
    # Live store is fine, staged store is broken: the gate must inspect staged.
    isolated.staged_chroma["fixture_rules"] = (["stray-chunk"], [{"doc_id": "wrong"}])
    code = isolated.main(["--chroma-dir", str(tmp_path / "staged"), "build", "--dataset", "rules"])
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["lineage_gate"]["status"] == "failed"


def test_isolated_build_skip_flag_recorded_in_json(lineage_world, isolated, capsys, tmp_path):
    isolated.staged_chroma["fixture_rules"] = (["stray-chunk"], [{"doc_id": "wrong"}])
    code = isolated.main(
        ["--chroma-dir", str(tmp_path / "staged"), "build", "--dataset", "rules", "--skip-lineage-gate"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["lineage_gate"]["status"] == "skipped"
    assert "WARNING" in captured.err
    assert isolated.staged_calls == []


def test_isolated_failed_build_keeps_existing_exit_code_without_gate(lineage_world, isolated, monkeypatch, tmp_path):
    monkeypatch.setattr(isolated, "build_staged_datasets", lambda **kwargs: {"status": "failed"})
    code = isolated.main(["--chroma-dir", str(tmp_path / "staged"), "build", "--dataset", "rules"])
    assert code == 1
    assert isolated.staged_calls == []


# --------------------------------------------------------------------------- notices dense


@pytest.fixture
def notices_dense(lineage_world, monkeypatch):
    from scripts import rebuild_notices_dense as module

    build_collection = "notices__build__b1"
    lineage_world.chroma[build_collection] = lineage_world.chroma["fixture_notices"]
    checkpoint = {
        "source_artifact": str(lineage_world.artifacts["notices"].chunk_path),
        "build_collection": build_collection,
    }
    activations: list[str] = []
    monkeypatch.setattr(module, "load_checkpoint", lambda build_id, checkpoint_dir=None: checkpoint)
    monkeypatch.setattr(module, "_build_snapshot_loader", lambda: lineage_world.loader)
    monkeypatch.setattr(
        module,
        "build_notice_dense_index",
        lambda **kwargs: {"status": "verified", "build_id": "b1"},
    )

    def fake_activate(**kwargs):
        activations.append(kwargs["build_id"])
        return {"status": "active", "build_id": kwargs["build_id"]}

    monkeypatch.setattr(module, "activate_notice_dense_build", fake_activate)
    module.activations = activations
    module.build_collection = build_collection
    return module


def test_notices_build_passes_lineage_on_staged_collection(lineage_world, notices_dense, capsys):
    assert notices_dense.main(["build"]) == 0
    assert json.loads(capsys.readouterr().out)["lineage_gate"]["status"] == "passed"
    assert lineage_world.calls == [["notices"]]


def test_notices_build_fails_on_staged_mismatch(lineage_world, notices_dense, capsys):
    lineage_world.break_dataset("notices", notices_dense.build_collection)
    assert notices_dense.main(["build"]) == 1
    assert json.loads(capsys.readouterr().out)["lineage_gate"]["status"] == "failed"


def test_notices_activate_refuses_pointer_switch_on_lineage_failure(lineage_world, notices_dense, capsys):
    lineage_world.break_dataset("notices", notices_dense.build_collection)
    code = notices_dense.main(["activate", "--build-id", "b1", "--confirm-build-id", "b1"])
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["status"] == "activation_refused"
    assert notices_dense.activations == []


def test_notices_activate_switches_after_lineage_passes(lineage_world, notices_dense, capsys):
    code = notices_dense.main(["activate", "--build-id", "b1", "--confirm-build-id", "b1"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert notices_dense.activations == ["b1"]
    assert output["lineage_gate"]["status"] == "passed"


def test_notices_activate_skip_flag_warns_and_activates(lineage_world, notices_dense, capsys):
    lineage_world.break_dataset("notices", notices_dense.build_collection)
    code = notices_dense.main(
        ["activate", "--build-id", "b1", "--confirm-build-id", "b1", "--skip-lineage-gate"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert notices_dense.activations == ["b1"]
    assert json.loads(captured.out)["lineage_gate"]["status"] == "skipped"
    assert "WARNING" in captured.err


# --------------------------------------------------------------------------- real input wiring


class _FakeCollection:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get(self, include, limit):
        assert include == ["metadatas"]
        ids, metadatas = self._snapshot
        return {"ids": list(ids), "metadatas": list(metadatas)}


class _FakeClient:
    def __init__(self, collections):
        self.collections = collections
        self.requested: list[str] = []

    def get_collection(self, name):
        self.requested.append(name)
        return _FakeCollection(self.collections[name])


def test_isolated_gate_reads_configured_artifact_and_isolated_store_client(
    lineage_world, monkeypatch, capsys, tmp_path
):
    from scripts import rebuild_dense_indices_isolated as module
    from src.pipelines.ingest import DATASET_ARTIFACTS as REAL_ARTIFACTS

    collection = REAL_ARTIFACTS["rules"].collection
    client = _FakeClient({collection: lineage_world.chroma["fixture_rules"]})
    validated: list = []
    stores: list = []
    staged_root = tmp_path / "validated-staged"

    def fake_validate(path):
        validated.append(path)
        return staged_root

    class FakeStore:
        def __init__(self, path):
            stores.append(path)
            self.client = client

    monkeypatch.setattr(module, "validate_isolated_chroma_dir", fake_validate)
    monkeypatch.setattr(module, "PathChromaDenseStore", FakeStore)
    monkeypatch.setattr(
        module, "_configured_artifact_path", lambda ds: lineage_world.artifacts[ds].chunk_path
    )
    monkeypatch.setattr(module, "build_staged_datasets", lambda **kwargs: {"status": "verified"})

    requested_dir = tmp_path / "staged"
    code = module.main(["--chroma-dir", str(requested_dir), "build", "--dataset", "rules"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["lineage_gate"]["status"] == "passed"
    assert validated == [requested_dir]
    assert stores == [staged_root]
    assert client.requested == [collection]

    # A mismatch in the isolated store (live store untouched) fails the gate.
    client.collections[collection] = (["stray-chunk"], [{"doc_id": "wrong"}])
    code = module.main(["--chroma-dir", str(requested_dir), "build", "--dataset", "rules"])
    assert code == 1


@pytest.mark.parametrize("command", ["build", "activate"])
def test_notices_gate_reads_build_collection_via_raw_client(lineage_world, monkeypatch, capsys, command):
    from scripts import rebuild_notices_dense as module

    build_collection = "notices__build__b1"
    client = _FakeClient({build_collection: lineage_world.chroma["fixture_notices"]})
    checkpoint = {
        "source_artifact": str(lineage_world.artifacts["notices"].chunk_path),
        "build_collection": build_collection,
    }
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "load_checkpoint", lambda build_id, checkpoint_dir=None: checkpoint)
    monkeypatch.setattr(
        module, "build_notice_dense_index", lambda **kwargs: {"status": "verified", "build_id": "b1"}
    )
    activations: list[str] = []
    monkeypatch.setattr(
        module,
        "activate_notice_dense_build",
        lambda **kwargs: activations.append(kwargs["build_id"]) or {"status": "active"},
    )
    argv = ["build"] if command == "build" else ["activate", "--build-id", "b1", "--confirm-build-id", "b1"]

    assert module.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["lineage_gate"]["status"] == "passed"
    # Physical staged name requested directly; the logical (pointer) name is never used.
    assert client.requested == [build_collection]

    client.collections[build_collection] = (["stray-chunk"], [{"doc_id": "wrong"}])
    activations.clear()
    assert module.main(argv) == 1
    assert activations == []


# --------------------------------------------------------------------------- paused builds


def test_isolated_paused_build_exits_75_without_gate(lineage_world, isolated, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(isolated, "build_staged_datasets", lambda **kwargs: {"status": "paused"})
    code = isolated.main(["--chroma-dir", str(tmp_path / "staged"), "build", "--dataset", "rules"])
    assert code == 75
    assert "lineage_gate" not in json.loads(capsys.readouterr().out)
    assert isolated.staged_calls == []
    assert lineage_world.calls == []


def test_notices_paused_build_exits_75_without_gate(lineage_world, notices_dense, monkeypatch, capsys):
    loaded: list[str] = []
    monkeypatch.setattr(
        notices_dense,
        "build_notice_dense_index",
        lambda **kwargs: {"status": "paused", "build_id": "b1"},
    )
    monkeypatch.setattr(
        notices_dense, "load_checkpoint", lambda build_id, checkpoint_dir=None: loaded.append(build_id)
    )
    assert notices_dense.main(["build"]) == 75
    assert "lineage_gate" not in json.loads(capsys.readouterr().out)
    assert loaded == []
    assert lineage_world.calls == []
