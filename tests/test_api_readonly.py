import pytest

from api import deps
from api.deps import ALLOWED_METHODS, ReadOnlyDB


class FakeDB:
    def __init__(self):
        self._pool = object()

    def get_archive_dates(self):
        return ["2026-10-03"]

    def save_daily_archive(self, *a):
        raise AssertionError("write reached the database")

    def add_article(self, *a):
        raise AssertionError("write reached the database")

    def _get_connection(self):
        raise AssertionError("connection handle leaked")


def test_allowlisted_read_is_forwarded():
    db = ReadOnlyDB(FakeDB())
    assert db.get_archive_dates() == ["2026-10-03"]


@pytest.mark.parametrize(
    "name",
    ["save_daily_archive", "add_article", "_get_connection", "_pool", "_db", "_read_db", "db_url", "delete_daily_archive"],
)
def test_everything_else_is_rejected(name):
    db = ReadOnlyDB(FakeDB())
    with pytest.raises(AttributeError):
        getattr(db, name)


def test_allowlist_contains_only_read_methods():
    assert ALLOWED_METHODS
    for name in ALLOWED_METHODS:
        assert name.startswith("get_")


def test_dependency_is_lazy(monkeypatch):
    created = []
    monkeypatch.setattr(deps, "_make_user_db", lambda: created.append(1) or FakeDB())
    deps.get_db.cache_clear()
    assert created == []
    deps.get_db()
    deps.get_db()
    assert created == [1]
    deps.get_db.cache_clear()


def test_default_bind_host_is_loopback():
    from api import config

    assert config.HOST == "127.0.0.1"
    assert config.PORT == 8000
