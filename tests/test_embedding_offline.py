"""Offline loading of the embedding model, per-run failure reporting and vector backfill.

No network, torch or Postgres: huggingface_hub / sentence_transformers are faked.
"""
import logging
import sys
import types

import pytest

import embedding_filter
from embedding_filter import EmbeddingModelUnavailable, MODEL_ALLOW_PATTERNS
from vector_store import VectorStore


class IncompleteSnapshotError(Exception):
    pass


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(embedding_filter, "_model", None)
    embedding_filter.reset_model_failure()
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    yield
    embedding_filter.reset_model_failure()


def _install_fakes(monkeypatch, snapshot_download):
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = snapshot_download
    st = types.ModuleType("sentence_transformers")
    loaded = []

    class FakeST:
        def __init__(self, path):
            loaded.append(path)

    st.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    return loaded


def _fake_cache_download(calls):
    """Like huggingface_hub: with no allow_patterns the unused TF/Rust files are 'required'."""
    cache_files = {"config.json", "modules.json", "model.safetensors", "vocab.txt"}  # no TF/Rust

    def download(repo, local_files_only=False, allow_patterns=None):
        calls.append({"local_files_only": local_files_only, "allow_patterns": allow_patterns})
        if allow_patterns is None:
            raise IncompleteSnapshotError("2 file(s) are missing (rust_model.ot, tf_model.h5)")
        assert cache_files  # PyTorch files present
        return "/cache/snapshot"

    return download


def test_offline_with_pytorch_complete_cache_loads(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    calls = []
    loaded = _install_fakes(monkeypatch, _fake_cache_download(calls))

    embedding_filter._get_model()

    assert loaded == ["/cache/snapshot"]
    assert len(calls) == 1  # no network fallback needed
    assert calls[0]["local_files_only"] is True
    assert calls[0]["allow_patterns"] == MODEL_ALLOW_PATTERNS
    assert not any("tf_model" in p or "rust_model" in p for p in MODEL_ALLOW_PATTERNS)


def test_online_falls_back_to_network_when_cache_missing(monkeypatch):
    calls = []

    def download(repo, local_files_only=False, allow_patterns=None):
        calls.append(local_files_only)
        if local_files_only:
            raise IncompleteSnapshotError("not cached")
        return "/downloaded"

    loaded = _install_fakes(monkeypatch, download)
    embedding_filter._get_model()
    assert calls == [True, False]
    assert loaded == ["/downloaded"]


def test_offline_and_missing_model_raises_clear_error_once(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    calls = []

    def download(repo, local_files_only=False, allow_patterns=None):
        calls.append(local_files_only)
        raise IncompleteSnapshotError("not cached")

    _install_fakes(monkeypatch, download)

    with pytest.raises(EmbeddingModelUnavailable, match="HF_HUB_OFFLINE"):
        embedding_filter._get_model()
    with pytest.raises(EmbeddingModelUnavailable):
        embedding_filter._get_model()
    assert calls == [True]  # second call reuses the remembered failure

    embedding_filter.reset_model_failure()
    with pytest.raises(EmbeddingModelUnavailable):
        embedding_filter._get_model()
    assert calls == [True, True]


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("0", False), ("", False)])
def test_hf_offline_enabled(monkeypatch, value, expected):
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    assert embedding_filter.hf_offline_enabled() is expected


# ---- VectorStore ----

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if "WHERE article_id = %s" in sql and sql.lstrip().startswith("SELECT 1"):
            self.rows = []
        elif "COUNT(*)" in sql:
            self.rows = [(1,)]
        elif "LEFT JOIN" in sql and "v.article_id IS NULL" in sql:
            self.rows = list(self.conn.missing)
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, missing=()):
        self.missing = missing
        self.executed = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def getconn(self):
        return self.conn

    def putconn(self, conn):
        pass


def _store(conn):
    vs = VectorStore(db_url="x")
    vs._pool = FakePool(conn)
    return vs


def _unavailable(*a, **k):
    raise EmbeddingModelUnavailable("model gone")


def test_failures_reported_once_with_count(caplog):
    vs = _store(FakeConn())
    vs._embed = _unavailable
    with caplog.at_level(logging.DEBUG):
        results = [vs.add_article(f"a{i}", "sum", ["t"], "src") for i in range(9)]
        assert vs.find_similar_articles("sum") == []  # dedup check also counts as skipped
        count = vs.report_embedding_failures()

    assert results == [False] * 9
    assert count == 10
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "10 article(s)" in errors[0].getMessage()
    assert "model gone" in errors[0].getMessage()


def test_report_is_silent_when_no_failures(caplog):
    vs = _store(FakeConn())
    with caplog.at_level(logging.DEBUG):
        assert vs.report_embedding_failures() == 0
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_reset_clears_failures(monkeypatch):
    vs = _store(FakeConn())
    vs._embed = _unavailable
    vs.add_article("a", "sum", [], "src")
    vs.reset_embedding_failures()
    assert vs.report_embedding_failures() == 0


def test_backfill_embeds_articles_without_vectors():
    rows = [
        ("id1", "english summary", '["ai", "llm"]', "news", "Src A"),
        ("id2", "", None, "news", "Src B"),  # no summary: skipped
        ("id3", "zh fallback", "not json", None, ""),
    ]
    conn = FakeConn(missing=rows)
    vs = _store(conn)
    vs._embed = lambda texts: [[0.1] * 384 for _ in texts]

    result = vs.backfill_missing_vectors(limit=10, days=7)

    assert result == {"missing": 3, "added": 2}
    inserts = [p for sql, p in conn.executed if "INSERT INTO article_vectors" in sql]
    assert [p[0] for p in inserts] == ["id1", "id3"]
    assert inserts[0][2] == "english summary | Tags: ai, llm"
    assert inserts[1][4] == "news"  # category default
    select_params = [p for sql, p in conn.executed if "v.article_id IS NULL" in sql][0]
    assert select_params == (7, 10)


def test_backfill_noop_when_nothing_missing():
    vs = _store(FakeConn(missing=[]))
    vs._embed = _unavailable
    assert vs.backfill_missing_vectors() == {"missing": 0, "added": 0}
    assert vs.report_embedding_failures() == 0


def test_backfill_stops_early_when_model_unavailable(caplog):
    rows = [(f"id{i}", "s", "[]", "news", "x") for i in range(50)]
    conn = FakeConn(missing=rows)
    vs = _store(conn)
    embeds = []

    def unavailable(texts):
        embeds.append(1)
        raise EmbeddingModelUnavailable("down")

    vs._embed = unavailable
    result = vs.backfill_missing_vectors()
    assert result["added"] == 0
    assert len(embeds) == 1  # gave up after the first failure
