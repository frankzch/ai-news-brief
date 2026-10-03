import logging
from datetime import datetime, timezone

DEFAULT_ARCHIVE_LIMIT = 50


def _to_text(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value or ''


def build_archive_rows(articles, limit=DEFAULT_ARCHIVE_LIMIT):
    """Map article rows (with source_name and parsed tags) to daily_archives rows, keeping order."""
    rows = []
    for article in articles[:limit]:
        rows.append({
            'id': article['id'],
            'title': article.get('title'),
            'title_en': article.get('title_en'),
            'title_zh': article.get('title_zh'),
            'summary_en': article.get('summary_en'),
            'summary_zh': article.get('summary_zh'),
            'long_summary_en': article.get('long_summary_en'),
            'long_summary_zh': article.get('long_summary_zh'),
            'importance_score': article.get('importance_score') or 0,
            'relevance_score': 0,
            'source': article.get('source_name'),
            'category': article.get('category'),
            'published_at': _to_text(article.get('published_at')),
            'url': article.get('url'),
            'labels': list(article.get('tags') or []),
        })
    return rows


def build_daily_archive(user_db, date_str, limit=DEFAULT_ARCHIVE_LIMIT):
    """Rebuild the archive for a UTC date (YYYY-MM-DD) from articles published that day.

    Returns the number of archived articles. Leaves any existing archive untouched when
    the day has no articles.
    """
    day = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = day.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

    articles = user_db.get_all_articles_in_date_range(start, end, limit=limit)
    if not articles:
        logging.info(f"No articles published on {date_str}; skipping daily archive")
        return 0

    rows = build_archive_rows(articles, limit=limit)
    user_db.delete_daily_archive(date_str)
    user_db.save_daily_archive(date_str, rows)
    return len(rows)
