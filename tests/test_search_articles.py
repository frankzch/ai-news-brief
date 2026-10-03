from contextlib import contextmanager

from user_db import UserDatabase


class FakeCursor:
    def __init__(self, log, total=7):
        self.log = log
        self.total = total
        self.rows = []

    def execute(self, sql, params=()):
        self.log.append((sql, tuple(params)))
        self.rows = [(self.total,)] if "COUNT(*)" in sql else [{"id": "a1", "tags": '["gpt"]'}]

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self, **kwargs):
        return FakeCursor(self.log)


def make_db(log):
    db = UserDatabase.__new__(UserDatabase)

    @contextmanager
    def conn():
        yield FakeConn(log)

    db._get_connection = conn
    return db


def test_search_returns_rows_with_parsed_tags_and_total():
    log = []
    rows, total = make_db(log).search_articles(limit=5, offset=10)
    assert total == 7
    assert rows[0]["tags"] == ["gpt"]
    assert log[-1][1][-2:] == (5, 10)


def test_user_input_is_bound_never_interpolated():
    log = []
    make_db(log).search_articles(category="news'; DROP TABLE x;--", tag="t'ag", q="q'uery")
    for sql, _ in log:
        assert "DROP" not in sql and "t'ag" not in sql and "q'uery" not in sql
    params = log[-1][1]
    assert "news'; DROP TABLE x;--" in params


def test_like_wildcards_are_escaped():
    log = []
    make_db(log).search_articles(q="50%_off")
    like_params = [p for p in log[-1][1] if isinstance(p, str) and "off" in p]
    assert like_params and all(r"50\%\_off" in p for p in like_params)


def test_tag_matches_whole_json_element():
    log = []
    make_db(log).search_articles(tag="gpt")
    assert '%"gpt"%' in log[-1][1]


def test_no_filters_has_no_where_clause():
    log = []
    make_db(log).search_articles()
    assert "WHERE" not in log[-1][0]
