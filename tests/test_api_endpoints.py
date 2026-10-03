from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app


def _article(i, category, tags, score, title=None):
    return {
        "id": f"art-{i}",
        "rss_source_id": 1,
        "content_hash": 123,
        "title": title or f"Title {i}",
        "title_en": title or f"Title {i}",
        "title_zh": f"标题 {i}",
        "summary_en": f"Summary {i}",
        "summary_zh": f"摘要 {i}",
        "long_summary_en": f"Long {i}",
        "long_summary_zh": f"长摘要 {i}",
        "importance_score": score,
        "tags": tags,
        "category": category,
        "url": f"https://example.com/{i}",
        "source_name": "Example",
        "published_at": datetime(2026, 10, 3, 12, i, tzinfo=timezone.utc),
        "created_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
    }


ARTICLES = [
    _article(1, "news", ["gpt", "llm"], 90, "GPT launch"),
    _article(2, "news", ["llm"], 70, "100%_real benchmark"),
    _article(3, "video", ["gpt"], 80),
    _article(4, "opensource", [], 60),
]
ARCHIVE = {
    "2026-10-03": [
        {"archive_date": "2026-10-03", "article_id": "art-1", "rank": 0, "title": "GPT launch",
         "title_en": "GPT launch", "title_zh": "标题 1", "summary_en": "S", "summary_zh": "摘",
         "long_summary_en": "L", "long_summary_zh": "长", "importance_score": 90,
         "relevance_score": 0.5, "source": "Example", "category": "news",
         "published_at": "2026-10-03T12:01:00+00:00", "url": "https://example.com/1",
         "labels": ["gpt"], "id": 99, "created_at": datetime(2026, 10, 3)},
    ],
    "2026-10-02": [],
}


class SeededDB:
    def __init__(self, articles=ARTICLES, archive=ARCHIVE):
        self.articles, self.archive = articles, archive

    def get_archive_dates(self):
        return sorted((d for d, rows in self.archive.items() if rows), reverse=True)

    def get_latest_archive_date(self):
        dates = self.get_archive_dates()
        return dates[0] if dates else None

    def get_daily_archive(self, date):
        return list(self.archive.get(date, []))

    def get_article_by_id(self, article_id):
        return next((a for a in self.articles if a["id"] == article_id), None)

    def get_hot_tags(self, limit=60):
        if not self.articles:
            return {"month": []}
        return {"month": [{"tag": "llm", "count": 5}, {"tag": "gpt", "count": 3}][:limit]}

    def search_articles(self, category=None, tag=None, q=None, limit=30, offset=0):
        rows = [a for a in self.articles
                if (not category or a["category"] == category)
                and (not tag or tag in a["tags"])
                and (not q or q.lower() in a["title"].lower())]
        return rows[offset:offset + limit], len(rows)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = lambda: SeededDB()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def empty_client():
    app.dependency_overrides[get_db] = lambda: SeededDB([], {})
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_dates_lists_newest_first_with_latest(client):
    assert client.get("/api/dates").json() == {"dates": ["2026-10-03"], "latest": "2026-10-03"}


def test_dates_empty_database(empty_client):
    assert empty_client.get("/api/dates").json() == {"dates": [], "latest": None}


def test_tags_returns_month_window(client):
    assert client.get("/api/tags").json() == {"tags": [{"tag": "llm", "count": 5}, {"tag": "gpt", "count": 3}]}


def test_tags_empty(empty_client):
    assert empty_client.get("/api/tags").json() == {"tags": []}


def test_digest_returns_ranked_articles_without_internal_columns(client):
    body = client.get("/api/digest/2026-10-03").json()
    assert body["date"] == "2026-10-03"
    a = body["articles"][0]
    assert a["article_id"] == "art-1" and a["rank"] == 0 and a["labels"] == ["gpt"]
    assert a["title_zh"] == "标题 1"
    assert "created_at" not in a and "id" not in a


def test_digest_missing_date_is_404(client):
    assert client.get("/api/digest/2026-01-01").status_code == 404


def test_digest_date_with_no_rows_is_404(client):
    assert client.get("/api/digest/2026-10-02").status_code == 404


@pytest.mark.parametrize("bad", ["yesterday", "2026-13-45", "20261003", "2026-1-3"])
def test_digest_rejects_malformed_date(client, bad):
    assert client.get(f"/api/digest/{bad}").status_code == 422


def test_article_detail(client):
    body = client.get("/api/articles/art-1").json()
    assert body["id"] == "art-1" and body["source_name"] == "Example"
    assert body["tags"] == ["gpt", "llm"]
    assert body["published_at"].startswith("2026-10-03T12:01")
    assert "content_hash" not in body and "rss_source_id" not in body and "created_at" not in body


def test_article_detail_missing_is_404(client):
    assert client.get("/api/articles/nope").status_code == 404


def test_articles_unfiltered_has_total(client):
    body = client.get("/api/articles").json()
    assert body["total"] == 4 and len(body["articles"]) == 4


def test_articles_filters_combine(client):
    body = client.get("/api/articles", params={"category": "news", "tag": "llm", "q": "gpt"}).json()
    assert [a["id"] for a in body["articles"]] == ["art-1"] and body["total"] == 1


def test_articles_no_match_is_empty_not_error(client):
    assert client.get("/api/articles", params={"tag": "zzz"}).json() == {"articles": [], "total": 0}


def test_articles_pagination(client):
    body = client.get("/api/articles", params={"limit": 2, "offset": 2}).json()
    assert [a["id"] for a in body["articles"]] == ["art-3", "art-4"] and body["total"] == 4


@pytest.mark.parametrize("params", [
    {"category": "bogus"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 201},
])
def test_articles_rejects_invalid_params(client, params):
    assert client.get("/api/articles", params=params).status_code == 422


def test_write_methods_not_routed(client):
    assert client.post("/api/articles").status_code in (405, 404)
    assert client.delete("/api/articles/art-1").status_code in (405, 404)
