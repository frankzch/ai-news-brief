from datetime import datetime, timezone

from daily_archive import build_archive_rows, build_daily_archive


def _article(n, **overrides):
    article = {
        'id': f'a{n}',
        'title': f'Title {n}',
        'title_en': f'Title EN {n}',
        'title_zh': f'标题 {n}',
        'summary_en': 'short en',
        'summary_zh': '简短',
        'long_summary_en': 'long en',
        'long_summary_zh': '详细',
        'importance_score': 100 - n,
        'source_name': 'Example Feed',
        'category': 'news',
        'published_at': datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        'url': f'https://example.com/{n}',
        'tags': ['llm', 'agents'],
    }
    article.update(overrides)
    return article


class FakeDB:
    def __init__(self, articles):
        self.articles = articles
        self.archives = {}
        self.calls = []

    def get_all_articles_in_date_range(self, start_date, end_date, limit=1000):
        self.calls.append(('read', start_date, end_date))
        return self.articles

    def delete_daily_archive(self, date_str):
        self.calls.append(('delete', date_str))
        self.archives.pop(date_str, None)

    def save_daily_archive(self, date_str, articles):
        self.calls.append(('save', date_str))
        self.archives[date_str] = list(articles)
        return True


def test_maps_source_name_and_tags_to_archive_fields():
    rows = build_archive_rows([_article(1)])

    assert rows[0]['source'] == 'Example Feed'
    assert rows[0]['labels'] == ['llm', 'agents']
    assert rows[0]['relevance_score'] == 0
    assert rows[0]['id'] == 'a1'


def test_published_at_is_iso_string():
    rows = build_archive_rows([_article(1)])

    assert rows[0]['published_at'] == '2026-10-03T12:00:00+00:00'


def test_preserves_input_order_and_caps_at_limit():
    articles = [_article(n) for n in range(1, 6)]

    rows = build_archive_rows(articles, limit=3)

    assert [r['id'] for r in rows] == ['a1', 'a2', 'a3']


def test_missing_tags_become_empty_labels():
    rows = build_archive_rows([_article(1, tags=None)])

    assert rows[0]['labels'] == []


def test_build_daily_archive_replaces_then_saves_for_the_date():
    db = FakeDB([_article(1), _article(2)])

    saved = build_daily_archive(db, '2026-10-03')

    assert saved == 2
    assert [c[0] for c in db.calls] == ['read', 'delete', 'save']
    assert [r['id'] for r in db.archives['2026-10-03']] == ['a1', 'a2']


def test_build_daily_archive_reads_the_utc_day_window():
    db = FakeDB([_article(1)])

    build_daily_archive(db, '2026-10-03')

    assert db.calls[0] == ('read', '2026-10-03T00:00:00+00:00', '2026-10-03T23:59:59.999999+00:00')


def test_build_daily_archive_is_idempotent():
    db = FakeDB([_article(1), _article(2)])

    build_daily_archive(db, '2026-10-03')
    build_daily_archive(db, '2026-10-03')

    assert len(db.archives['2026-10-03']) == 2


def test_build_daily_archive_does_nothing_when_no_articles():
    db = FakeDB([])

    saved = build_daily_archive(db, '2026-10-03')

    assert saved == 0
    assert [c[0] for c in db.calls] == ['read']
    assert db.archives == {}
