"""Read-only database access for the API.

The wrapper holds the UserDatabase privately and forwards only an explicit
allowlist of read methods, so write methods and connection handles are not
reachable from request handlers.
"""
import os
import sys
from functools import lru_cache

ALLOWED_METHODS = frozenset({
    "get_archive_dates",
    "get_latest_archive_date",
    "get_daily_archive",
    "get_article_by_id",
    "get_hot_tags",
    "get_all_articles_in_date_range",
    "get_articles_by_category_in_date_range",
    "search_articles",
})


class ReadOnlyDB:
    """Forwards allowlisted reads only. The wrapped db lives in a closure, not an attribute."""

    __slots__ = ("_read",)

    def __init__(self, db):
        def read(name):
            if name not in ALLOWED_METHODS:
                raise AttributeError(f"{name!r} is not available through the read-only API")
            return getattr(db, name)

        object.__setattr__(self, "_read", read)

    def __getattr__(self, name):
        # Only reached when normal lookup fails, so "_read" never gets here.
        return object.__getattribute__(self, "_read")(name)

    def __setattr__(self, name, value):
        raise AttributeError("ReadOnlyDB is immutable")


def _make_user_db():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from user_db import UserDatabase

    return UserDatabase()


@lru_cache(maxsize=1)
def get_db() -> ReadOnlyDB:
    """FastAPI dependency; the database is only opened on first use."""
    return ReadOnlyDB(_make_user_db())
