import os
import re
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool


def normalize_tag(tag: str) -> str:
    """Canonicalize a tag for hot-tag stats so that variants differing only by
    case, underscores/dashes (ASCII '-' plus Unicode dashes ‐-―),
    or whitespace collapse into one.
    e.g. 'Machine_Learning', 'machine-learning', 'machine learning'
         -> 'machine learning'."""
    s = re.sub(r'[_\-‐-―]+', ' ', str(tag).lower())
    return re.sub(r'\s+', ' ', s).strip()


class UserDatabase:
    """PostgreSQL database (Supabase) for user management and feedback tracking."""
    
    _pool = None  # Class-level connection pool (shared across instances)
    
    def __init__(self, db_url: str = None):
        if db_url is None:
            from config_loader import ConfigLoader
            config = ConfigLoader.get_instance().get('supabase', {})
            db_url = config.get('db_url', '')
        self.db_url = db_url
        self._ensure_pool()
        self._init_database()
    
    def _ensure_pool(self):
        """Create connection pool if not exists."""
        if UserDatabase._pool is None or UserDatabase._pool.closed:
            UserDatabase._pool = pool.ThreadedConnectionPool(
                minconn=1, maxconn=10, dsn=self.db_url
            )
            logging.info("PostgreSQL connection pool created")
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections from pool with health check."""
        self._ensure_pool()
        conn = UserDatabase._pool.getconn()
        
        # Health check: detect stale/broken SSL connections
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.close()
            conn.autocommit = False
        except Exception:
            logging.warning("Stale connection detected, replacing...")
            try:
                UserDatabase._pool.putconn(conn, close=True)
            except Exception:
                pass
            # Try to get a fresh connection; if pool is exhausted, recreate it
            try:
                conn = UserDatabase._pool.getconn()
                conn.autocommit = False
            except Exception:
                logging.warning("Pool exhausted or broken, recreating...")
                try:
                    UserDatabase._pool.closeall()
                except Exception:
                    pass
                UserDatabase._pool = None
                self._ensure_pool()
                conn = UserDatabase._pool.getconn()
                conn.autocommit = False
        
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            UserDatabase._pool.putconn(conn)
    
    def _init_database(self):
        """Initialize database tables (PostgreSQL)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Enable pgvector extension (for vector_store)
            cursor.execute('CREATE EXTENSION IF NOT EXISTS vector')
            
            # NOTE: user accounts + profiles are Supabase-managed
            # (auth.users UUID + a trigger-populated public.user_profiles).
            # The legacy integer-keyed users / user_feedback / user_blocks / api_keys
            # tables were dropped; their DDL must NOT be recreated here.

            # RSS Sources table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rss_sources (
                    id SERIAL PRIMARY KEY,
                    url TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    importance_score INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            # Migration: add last_fetch_at column if missing
            cursor.execute('''
                ALTER TABLE rss_sources ADD COLUMN IF NOT EXISTS last_fetch_at TIMESTAMPTZ
            ''')
            
            # Articles table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    rss_source_id INTEGER REFERENCES rss_sources(id),
                    title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    summary TEXT,
                    long_summary TEXT,
                    importance_score INTEGER DEFAULT 0,
                    tags TEXT,
                    category TEXT DEFAULT 'news',
                    content_hash BIGINT,
                    published_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    summary_en TEXT,
                    summary_zh TEXT,
                    long_summary_en TEXT,
                    long_summary_zh TEXT,
                    title_en TEXT,
                    title_zh TEXT
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_articles_source 
                ON articles(rss_source_id, published_at DESC)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_articles_url 
                ON articles(url)
            ''')
            

            # Daily archives table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_archives (
                    id SERIAL PRIMARY KEY,
                    archive_date TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    title TEXT,
                    title_en TEXT,
                    title_zh TEXT,
                    summary_en TEXT,
                    summary_zh TEXT,
                    long_summary_en TEXT,
                    long_summary_zh TEXT,
                    importance_score INTEGER DEFAULT 0,
                    relevance_score REAL DEFAULT 0,
                    source TEXT,
                    category TEXT,
                    published_at TEXT,
                    url TEXT,
                    labels TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(archive_date, article_id)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_daily_archives_date 
                ON daily_archives(archive_date)
            ''')
            
            # Fetched URLs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fetched_urls (
                    id SERIAL PRIMARY KEY,
                    url TEXT NOT NULL,
                    rss_source_id INTEGER,
                    next_fetch_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_fetched_urls_url 
                ON fetched_urls(url)
            ''')
            # Migration: add next_fetch_at column if missing
            cursor.execute('''
                ALTER TABLE fetched_urls ADD COLUMN IF NOT EXISTS next_fetch_at TIMESTAMPTZ
            ''')
            
            # Article vectors table (for pgvector, used by vector_store.py)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_vectors (
                    article_id TEXT PRIMARY KEY,
                    embedding vector(384),
                    document TEXT,
                    source TEXT,
                    category TEXT,
                    tags JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_article_vectors_category
                ON article_vectors(category)
            ''')
            
            # ===== Knowledge Base tables (1-year retention, separate from articles) =====
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_articles (
                    article_id        TEXT PRIMARY KEY,
                    title             TEXT NOT NULL,
                    url               TEXT NOT NULL,
                    source            TEXT NOT NULL,
                    category          TEXT NOT NULL,
                    tags              JSONB DEFAULT '[]',
                    short_summary     TEXT,
                    long_summary      TEXT,
                    full_content      TEXT,
                    chunk_strategy    TEXT NOT NULL,
                    chunk_source      TEXT NOT NULL,
                    importance_score  INT,
                    published_at      TIMESTAMPTZ NOT NULL,
                    archived_at       TIMESTAMPTZ DEFAULT NOW(),
                    expires_at        TIMESTAMPTZ NOT NULL,
                    alias_of          TEXT REFERENCES knowledge_articles(article_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ka_published ON knowledge_articles(published_at DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ka_category  ON knowledge_articles(category)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ka_source    ON knowledge_articles(source)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ka_expires   ON knowledge_articles(expires_at)')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id          BIGSERIAL PRIMARY KEY,
                    article_id        TEXT NOT NULL REFERENCES knowledge_articles(article_id) ON DELETE CASCADE,
                    chunk_index       INT NOT NULL,
                    chunk_text        TEXT NOT NULL,
                    embedding         VECTOR(1024) NOT NULL,
                    token_count       INT,
                    content_hash      BIGINT,
                    created_at        TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(article_id, chunk_index)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_kc_article ON knowledge_chunks(article_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_kc_hash    ON knowledge_chunks(content_hash)')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_kc_embedding ON knowledge_chunks
                    USING hnsw (embedding vector_cosine_ops)
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS knowledge_queries (
                    query_id          BIGSERIAL PRIMARY KEY,
                    user_id           TEXT,
                    query             TEXT NOT NULL,
                    decomposed_subqs  JSONB,
                    retrieved_chunks  JSONB,
                    answer            TEXT,
                    citations         JSONB,
                    latency_ms        INT,
                    llm_tokens_in     INT,
                    llm_tokens_out    INT,
                    iteration_count   INT,
                    created_at        TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_kq_user    ON knowledge_queries(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_kq_created ON knowledge_queries(created_at DESC)')

            # Email verification codes (for signup OTP / future password reset)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS email_verify_codes (
                    id            BIGSERIAL PRIMARY KEY,
                    email         TEXT NOT NULL,
                    code_hash     TEXT NOT NULL,
                    purpose       TEXT NOT NULL DEFAULT 'signup',
                    expires_at    TIMESTAMPTZ NOT NULL,
                    attempts      INT NOT NULL DEFAULT 0,
                    consumed_at   TIMESTAMPTZ,
                    created_at    TIMESTAMPTZ DEFAULT NOW(),
                    ip            TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_evc_email ON email_verify_codes(email, purpose, created_at DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_evc_ip    ON email_verify_codes(ip, created_at DESC)')

            # Tag daily stats (hot tags feature)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tag_daily_stats (
                    stat_date DATE NOT NULL,
                    tag TEXT NOT NULL,
                    count INT NOT NULL DEFAULT 0,
                    PRIMARY KEY (stat_date, tag)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tag_daily_date ON tag_daily_stats(stat_date)')

            logging.info("PostgreSQL database initialized (Supabase)")
    
    # ==================== Profile CRUD ====================
    
    def get_user_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user profile with parsed JSON fields."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            row = cursor.fetchone()
            if not row:
                return None
            
            profile = dict(row)
            if profile.get('categories'):
                try:
                    profile['categories'] = json.loads(profile['categories'])
                except json.JSONDecodeError:
                    profile['categories'] = []
            else:
                profile['categories'] = []
            
            return profile
    
    def update_interest_description(self, user_id: int, description: str):
        """Update user's interest description."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''UPDATE user_profiles 
                   SET interest_description = %s, updated_at = %s 
                   WHERE user_id = %s''',
                (description, datetime.now(timezone.utc).isoformat(), user_id)
            )
            logging.info(f"Updated interest description for user {user_id}")
    
    def update_ai_persona(self, user_id: int, persona: str, categories: List[str]):
        """Update AI-generated persona and categories."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''UPDATE user_profiles 
                   SET ai_persona = %s, categories = %s 
                   WHERE user_id = %s''',
                (persona, json.dumps(categories), user_id)
            )
            logging.info(f"Updated AI persona for user {user_id}")
    
    def update_display_count(self, user_id: int, count: int):
        """Update display count preference."""
        count = max(5, min(100, count))
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE user_profiles SET display_count = %s WHERE user_id = %s',
                (count, user_id)
            )
            logging.info(f"Updated display count for user {user_id}: {count}")

    def update_interface_language(self, user_id: int, language: str):
        """Update interface language preference."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE user_profiles SET interface_language = %s WHERE user_id = %s',
                (language, user_id)
            )
            logging.info(f"Updated interface language for user {user_id}: {language}")

    def update_push_email(self, user_id: int, email: str):
        """Update push email for a user. Empty string disables push."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE user_profiles SET push_email = %s WHERE user_id = %s',
                (email if email else None, user_id)
            )
            logging.info(f"Updated push email for user {user_id}: {email or '(disabled)'}")

    def get_push_email(self, user_id: int) -> Optional[str]:
        """Get push email for a user."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('SELECT push_email FROM user_profiles WHERE user_id = %s', (user_id,))
            row = cursor.fetchone()
            return row['push_email'] if row else None

    def get_users_for_push(self, today_str: str) -> List[Dict[str, Any]]:
        """Get users with push_email set, push_enabled=true, and not yet pushed today."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('''
                SELECT up.user_id, up.push_email, up.interface_language
                FROM user_profiles up
                WHERE up.push_email IS NOT NULL AND up.push_email != ''
                AND up.push_enabled = true
                AND (up.last_push_date IS NULL OR up.last_push_date != %s)
            ''', (today_str,))
            return [dict(row) for row in cursor.fetchall()]

    def update_last_push_date(self, user_id: int, date_str: str):
        """Mark user as pushed for a given date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE user_profiles SET last_push_date = %s WHERE user_id = %s',
                (date_str, user_id)
            )

    def update_last_fetch_at(self, user_id: int):
        """Update last fetch timestamp."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE user_profiles SET last_fetch_at = %s WHERE user_id = %s',
                (datetime.now(timezone.utc).isoformat(), user_id)
            )

    # ==================== RSS Sources CRUD ====================
    
    def add_rss_source(self, url: str, name: str, description: str = None, category: str = None, importance_score: int = 0) -> Optional[int]:
        """Add a new RSS source. Returns source_id or None if URL already exists."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT INTO rss_sources (url, name, description, category, importance_score) VALUES (%s, %s, %s, %s, %s) RETURNING id',
                    (url, name, description, category, importance_score)
                )
                source_id = cursor.fetchone()[0]
                logging.info(f"Added RSS source: {name} (ID: {source_id})")
                return source_id
        except psycopg2.IntegrityError:
            logging.debug(f"RSS source URL already exists: {url}")
            return None
    
    def get_or_create_rss_source(self, url: str, name: str, description: str = None, category: str = None) -> int:
        """Get existing RSS source ID or create new one."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM rss_sources WHERE url = %s', (url,))
            row = cursor.fetchone()
            if row:
                return row[0]
        
        source_id = self.add_rss_source(url, name, description, category)
        if source_id:
            return source_id
        
        # Handle race condition
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM rss_sources WHERE url = %s', (url,))
            row = cursor.fetchone()
            return row[0] if row else None
    
    def get_rss_source_by_id(self, source_id: int) -> Optional[Dict[str, Any]]:
        """Get RSS source by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('SELECT * FROM rss_sources WHERE id = %s', (source_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_all_rss_sources(self) -> List[Dict[str, Any]]:
        """Get all RSS sources."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('SELECT * FROM rss_sources ORDER BY last_fetch_at ASC NULLS FIRST, name ASC')
            return [dict(row) for row in cursor.fetchall()]
    
    def update_rss_source_fetch_time(self, source_id: int):
        """Update the last fetch time for an RSS source."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE rss_sources SET last_fetch_at = %s WHERE id = %s',
                (datetime.now(timezone.utc).isoformat(), source_id)
            )
            logging.debug(f"Updated fetch time for RSS source ID {source_id}")
    
    def delete_rss_sources(self, source_ids: List[int]):
        """Delete RSS sources and their associated articles."""
        if not source_ids:
            return
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                'DELETE FROM articles WHERE rss_source_id = ANY(%s)',
                (source_ids,)
            )
            deleted_articles = cursor.rowcount
            
            cursor.execute(
                'DELETE FROM rss_sources WHERE id = ANY(%s)',
                (source_ids,)
            )
            
            logging.info(f"Deleted {len(source_ids)} RSS sources and {deleted_articles} articles")

    # ==================== Articles CRUD ====================
    
    def add_article(self, article_data: Dict[str, Any]) -> Optional[str]:
        """Add a new article. Returns article_id or None if URL already exists."""
        import uuid
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                article_id = str(uuid.uuid4())
                
                tags = article_data.get('tags', [])
                if isinstance(tags, list):
                    tags = json.dumps(tags)
                
                content_hash = article_data.get('content_hash')

                cursor.execute('''
                    INSERT INTO articles 
                    (id, rss_source_id, title, url, 
                     summary_en, summary_zh, long_summary_en, long_summary_zh,
                     title_en, title_zh,
                     importance_score, tags, category, content_hash, published_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    article_id,
                    article_data['rss_source_id'],
                    article_data['title'],
                    article_data['url'],
                    article_data.get('summary_en', ''),
                    article_data.get('summary_zh', ''),
                    article_data.get('long_summary_en', ''),
                    article_data.get('long_summary_zh', ''),
                    article_data.get('title_en', ''),
                    article_data.get('title_zh', ''),
                    article_data.get('importance_score', 0),
                    tags,
                    article_data.get('category', 'news'),
                    content_hash,
                    article_data.get('published_at')
                ))
                
                # Increment tag daily stats (hot tags)
                raw_tags = article_data.get('tags', [])
                if isinstance(raw_tags, str):
                    try:
                        raw_tags = json.loads(raw_tags)
                    except Exception:
                        raw_tags = []
                # Normalize + dedupe within this article so case/underscore
                # variants of the same tag count once.
                tag_list = list(dict.fromkeys(
                    normalize_tag(t) for t in (raw_tags or []) if normalize_tag(t)
                ))
                if tag_list:
                    pub = article_data.get('published_at')
                    stat_date_sql = "COALESCE(%s::date, CURRENT_DATE)"
                    for t in tag_list:
                        cursor.execute(
                            f'''INSERT INTO tag_daily_stats (stat_date, tag, count)
                                VALUES ({stat_date_sql}, %s, 1)
                                ON CONFLICT (stat_date, tag)
                                DO UPDATE SET count = tag_daily_stats.count + 1''',
                            (pub, t)
                        )

                logging.info(f"Added article: {article_data['title'][:50]}... (ID: {article_id})")
                return article_id
        except psycopg2.IntegrityError:
            logging.debug(f"Article URL already exists: {article_data['url']}")
            return None

    # ==================== Hot Tags ====================

    def get_hot_tags(self, limit: int = 60) -> Dict[str, List[Dict[str, Any]]]:
        """Return hot tags over the past 30 days (rolling window)."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                '''SELECT tag, SUM(count)::int AS count FROM tag_daily_stats
                   WHERE stat_date >= CURRENT_DATE - INTERVAL '30 days'
                   GROUP BY tag
                   ORDER BY count DESC, tag ASC
                   LIMIT %s''',
                (limit,)
            )
            month = [dict(r) for r in cursor.fetchall()]
            return {"month": month}

    def backfill_tag_daily_stats(self) -> int:
        """Recompute tag_daily_stats from articles. Returns rows inserted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('TRUNCATE tag_daily_stats')
            # Normalize the tag the same way normalize_tag() does (lowercase,
            # _/- -> space, collapse whitespace) so variants merge into one row.
            cursor.execute(r'''
                INSERT INTO tag_daily_stats (stat_date, tag, count)
                SELECT d, tag, SUM(c)::int AS c FROM (
                    SELECT COALESCE(published_at, created_at)::date AS d,
                           btrim(regexp_replace(
                               regexp_replace(lower(trim(both '"' from t.value::text)),
                                              '[_‐-―-]+', ' ', 'g'),
                               '\s+', ' ', 'g')) AS tag,
                           1 AS c
                    FROM articles a,
                         LATERAL jsonb_array_elements(
                            CASE
                                WHEN a.tags IS NULL OR a.tags = '' THEN '[]'::jsonb
                                ELSE a.tags::jsonb
                            END
                         ) AS t(value)
                ) s
                WHERE tag <> ''
                GROUP BY d, tag
            ''')
            rows = cursor.rowcount
            logging.info(f"Backfilled tag_daily_stats: {rows} rows")
            return rows

    def normalize_existing_tag_stats(self) -> int:
        """One-time migration: collapse already-stored tag variants (case,
        underscores/hyphens, whitespace) into a single normalized row per day,
        summing their counts. Rebuilds from the table itself, so it does NOT
        depend on article retention. Returns the row count after merging."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(r'''
                CREATE TEMP TABLE _tag_stats_merged ON COMMIT DROP AS
                SELECT stat_date,
                       btrim(regexp_replace(
                           regexp_replace(lower(tag), '[_‐-―-]+', ' ', 'g'),
                           '\s+', ' ', 'g')) AS tag,
                       SUM(count)::int AS count
                FROM tag_daily_stats
                GROUP BY 1, 2
                HAVING btrim(regexp_replace(
                           regexp_replace(lower(tag), '[_-]+', ' ', 'g'),
                           '\s+', ' ', 'g')) <> ''
            ''')
            cursor.execute('TRUNCATE tag_daily_stats')
            cursor.execute(
                'INSERT INTO tag_daily_stats (stat_date, tag, count) '
                'SELECT stat_date, tag, count FROM _tag_stats_merged'
            )
            rows = cursor.rowcount
            logging.info(f"Normalized tag_daily_stats: {rows} rows after merge")
            return rows

    def cleanup_old_tag_stats(self, retention_days: int) -> int:
        """Delete tag_daily_stats rows older than the retention period."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tag_daily_stats "
                "WHERE stat_date < CURRENT_DATE - make_interval(days => %s)",
                (retention_days,)
            )
            deleted = cursor.rowcount
            logging.info(f"Cleaned up {deleted} old tag_daily_stats rows (retention: {retention_days} days)")
            return deleted

    def refresh_homepage_cache(self) -> None:
        """Recompute the homepage article cache. Run after each pipeline so the
        read RPC (get_homepage_articles_rpc) serves a cheap indexed slice instead
        of recomputing the full ranking on every page load."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT refresh_homepage_cache()')
            logging.info("Homepage article cache refreshed")

    def article_url_exists(self, url: str) -> bool:
        """Check if an article with this URL already exists (articles or fetched_urls).
        
        For fetched_urls with next_fetch_at:
        - NULL: permanently processed, skip (return True)
        - > NOW(): not yet time to re-fetch, skip (return True)
        - <= NOW(): delay expired, allow re-fetch (don't count as exists)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT 1 FROM articles WHERE url = %s
                   UNION
                   SELECT 1 FROM fetched_urls WHERE url = %s
                     AND (next_fetch_at IS NULL OR next_fetch_at > NOW())
                   LIMIT 1''',
                (url, url)
            )
            return cursor.fetchone() is not None
    
    def record_fetched_url(self, url: str, rss_source_id: int = None, next_fetch_at: datetime = None):
        """Record a fetched URL.
        
        Args:
            next_fetch_at: If set, the URL can be re-fetched after this time.
                           If None, the URL is permanently marked as fetched.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO fetched_urls (url, rss_source_id, next_fetch_at) 
                   VALUES (%s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET 
                     next_fetch_at = EXCLUDED.next_fetch_at,
                     created_at = NOW()''',
                (url, rss_source_id, next_fetch_at)
            )
            
    def get_similar_article(self, content_hash: int, threshold: int = 3) -> Optional[Dict[str, Any]]:
        """Check if a similar article exists using SimHash Hamming distance."""
        if content_hash == 0:
            return None
            
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('''
                SELECT id, title, url, content_hash 
                FROM articles 
                WHERE content_hash IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1000
            ''')
            
            rows = cursor.fetchall()
            
            from lsh_dedup import is_similar
            
            for row in rows:
                db_hash = row['content_hash']
                if is_similar(content_hash, db_hash, threshold):
                    return dict(row)
            
            return None
    
    def get_articles_by_sources(self, source_ids: List[int], since_time: str = None, 
                                 limit: int = 100) -> List[Dict[str, Any]]:
        """Get articles from specified sources, optionally filtered by time."""
        if not source_ids:
            return []
        
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            if since_time:
                cursor.execute('''
                    SELECT a.*, rs.name as source_name FROM articles a
                    JOIN rss_sources rs ON a.rss_source_id = rs.id
                    WHERE a.rss_source_id = ANY(%s)
                    AND a.published_at >= %s
                    ORDER BY a.importance_score DESC, a.published_at DESC
                    LIMIT %s
                ''', (source_ids, since_time, limit))
            else:
                cursor.execute('''
                    SELECT a.*, rs.name as source_name FROM articles a
                    JOIN rss_sources rs ON a.rss_source_id = rs.id
                    WHERE a.rss_source_id = ANY(%s)
                    ORDER BY a.importance_score DESC, a.published_at DESC
                    LIMIT %s
                ''', (source_ids, limit))
            
            articles = []
            for row in cursor.fetchall():
                article = dict(row)
                if article.get('tags'):
                    try:
                        article['tags'] = json.loads(article['tags'])
                    except json.JSONDecodeError:
                        article['tags'] = []
                else:
                    article['tags'] = []
                articles.append(article)
            
            return articles
    
    def get_article_by_id(self, article_id: str) -> Optional[Dict[str, Any]]:
        """Get a single article by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('''
                SELECT a.*, rs.name as source_name FROM articles a
                JOIN rss_sources rs ON a.rss_source_id = rs.id
                WHERE a.id = %s
            ''', (article_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            article = dict(row)
            if article.get('tags'):
                try:
                    article['tags'] = json.loads(article['tags'])
                except json.JSONDecodeError:
                    article['tags'] = []
            else:
                article['tags'] = []
            
            return article
    
    def get_article_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Get a single article by URL (used for dedup/refresh)."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                'SELECT id, summary_en, summary_zh, category FROM articles WHERE url = %s',
                (url,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_article_refresh(self, article_id: str, published_at: str,
                               importance_score: int, summary_en: str, summary_zh: str) -> bool:
        """Refresh an existing article's timestamp / score / summary (for trending re-fetch)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE articles SET
                    published_at = %s,
                    importance_score = %s,
                    summary_en = %s,
                    summary_zh = %s
                WHERE id = %s
            ''', (published_at, importance_score, summary_en, summary_zh, article_id))
            return cursor.rowcount > 0

    def get_articles_count_by_sources(self, source_ids: List[int], since_time: str = None) -> int:
        """Get count of articles from specified sources."""
        if not source_ids:
            return 0
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if since_time:
                cursor.execute('''
                    SELECT COUNT(*) as cnt FROM articles
                    WHERE rss_source_id = ANY(%s)
                    AND published_at >= %s
                ''', (source_ids, since_time))
            else:
                cursor.execute('''
                    SELECT COUNT(*) as cnt FROM articles
                    WHERE rss_source_id = ANY(%s)
                ''', (source_ids,))
            
            return cursor.fetchone()[0]



    def get_all_articles_in_date_range(self, start_date: str, end_date: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get all articles in a date range."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('''
                SELECT a.*, rs.name as source_name FROM articles a
                JOIN rss_sources rs ON a.rss_source_id = rs.id
                WHERE a.published_at >= %s AND a.published_at <= %s
                ORDER BY a.importance_score DESC, a.published_at DESC
                LIMIT %s
            ''', (start_date, end_date, limit))
            
            articles = []
            for row in cursor.fetchall():
                article = dict(row)
                if article.get('tags'):
                    try:
                        article['tags'] = json.loads(article['tags'])
                    except json.JSONDecodeError:
                        article['tags'] = []
                else:
                    article['tags'] = []
                articles.append(article)
            
            return articles

    def get_articles_by_category_in_date_range(self, category: str, start_date: str, end_date: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get articles for a specific category in a date range."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute('''
                SELECT a.*, rs.name as source_name FROM articles a
                JOIN rss_sources rs ON a.rss_source_id = rs.id
                WHERE a.category = %s AND a.published_at >= %s AND a.published_at <= %s
                ORDER BY a.importance_score DESC, a.published_at DESC
                LIMIT %s
            ''', (category, start_date, end_date, limit))
            
            articles = []
            for row in cursor.fetchall():
                article = dict(row)
                if article.get('tags'):
                    try:
                        article['tags'] = json.loads(article['tags'])
                    except json.JSONDecodeError:
                        article['tags'] = []
                else:
                    article['tags'] = []
                articles.append(article)
            
            return articles

    def get_articles_count(self) -> int:
        """Get total article count."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM articles')
            return cursor.fetchone()[0]

    def cleanup_old_articles(self, retention_days: int) -> int:
        """Clean up articles older than retention period."""
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM articles WHERE published_at < %s',
                (cutoff_date,)
            )
            deleted_count = cursor.rowcount
            
            cursor.execute(
                'DELETE FROM fetched_urls WHERE created_at < %s',
                (cutoff_date,)
            )
            deleted_urls = cursor.rowcount
            
            logging.info(f"Cleaned up {deleted_count} old articles, {deleted_urls} old fetched_urls (older than {retention_days} days)")
            return deleted_count

    def delete_articles_by_category(self, category: str) -> list:
        """Delete all articles of a given category. Returns list of deleted article IDs (for vector store sync)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Get IDs first for vector store cleanup
            cursor.execute('SELECT id FROM articles WHERE category = %s', (category,))
            deleted_ids = [row[0] for row in cursor.fetchall()]
            
            if deleted_ids:
                cursor.execute('DELETE FROM articles WHERE category = %s', (category,))
                logging.info(f"Deleted {len(deleted_ids)} articles with category='{category}'")
            
            return deleted_ids

    def get_articles_count_by_category_today(self, category: str) -> int:
        """Get count of articles created today for a given category."""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) FROM articles WHERE category = %s AND created_at >= %s',
                (category, today_start.isoformat())
            )
            return cursor.fetchone()[0]

    def get_articles_count_by_category_published_today(self, category: str) -> int:
        """Count articles in category whose published_at is today (UTC).

        Used by GitHub trending pipeline: even if an article is reused (no new row),
        we refresh published_at to NOW(), so this signals 'pipeline ran today'.
        """
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) FROM articles WHERE category = %s AND published_at >= %s',
                (category, today_start.isoformat())
            )
            return cursor.fetchone()[0]

    def delete_articles_by_category_excluding_urls(self, category: str, keep_urls: list) -> list:
        """Delete articles in category whose URL is NOT in keep_urls. Returns deleted IDs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if keep_urls:
                cursor.execute(
                    'SELECT id FROM articles WHERE category = %s AND url <> ALL(%s)',
                    (category, list(keep_urls))
                )
            else:
                cursor.execute('SELECT id FROM articles WHERE category = %s', (category,))
            deleted_ids = [row[0] for row in cursor.fetchall()]
            if deleted_ids:
                cursor.execute('DELETE FROM articles WHERE id = ANY(%s)', (deleted_ids,))
                logging.info(f"Deleted {len(deleted_ids)} stale '{category}' articles not in current list")
            return deleted_ids

    # ==================== Article ID Helpers ====================

    def get_all_article_ids(self) -> List[str]:
        """Get all article IDs in the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM articles')
            return [row[0] for row in cursor.fetchall()]
    
    def get_expired_article_ids(self, retention_days: int) -> List[str]:
        """Get article IDs that are older than retention period."""
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM articles WHERE published_at < %s',
                (cutoff_date,)
            )
            return [row[0] for row in cursor.fetchall()]

    # ==================== Email Verification Codes ====================

    def create_verify_code(self, email: str, code_hash: str, purpose: str,
                           ttl_seconds: int, ip: Optional[str]) -> int:
        """Insert a new OTP record. Returns id."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO email_verify_codes
                   (email, code_hash, purpose, expires_at, ip)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id''',
                (email.lower(), code_hash, purpose, expires_at, ip)
            )
            return cursor.fetchone()[0]

    def get_latest_verify_code(self, email: str, purpose: str) -> Optional[Dict[str, Any]]:
        """Most recent (any state) OTP row for email+purpose."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                '''SELECT * FROM email_verify_codes
                   WHERE email = %s AND purpose = %s
                   ORDER BY created_at DESC LIMIT 1''',
                (email.lower(), purpose)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def count_recent_codes_for_email(self, email: str, purpose: str, window_seconds: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT COUNT(*) FROM email_verify_codes
                   WHERE email = %s AND purpose = %s AND created_at >= %s''',
                (email.lower(), purpose, cutoff)
            )
            return cursor.fetchone()[0]

    def count_recent_codes_for_ip(self, ip: str, window_seconds: int) -> int:
        if not ip:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT COUNT(*) FROM email_verify_codes
                   WHERE ip = %s AND created_at >= %s''',
                (ip, cutoff)
            )
            return cursor.fetchone()[0]

    def increment_verify_attempts(self, code_id: int) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE email_verify_codes SET attempts = attempts + 1 WHERE id = %s RETURNING attempts',
                (code_id,)
            )
            row = cursor.fetchone()
            return row[0] if row else 0

    def mark_verify_code_consumed(self, code_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE email_verify_codes SET consumed_at = NOW() WHERE id = %s',
                (code_id,)
            )

    # ==================== Membership (Supabase user_profiles) ====================

    def get_membership_tier(self, user_id_uuid: str) -> Dict[str, Any]:
        """Look up membership tier + expiry from Supabase user_profiles by uuid.

        Returns {'tier': 'member'|'free', 'expires_at': datetime|None}.
        Falls back to 'free' if profile not found or schema mismatch.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cursor.execute(
                    '''SELECT membership_tier, membership_expires_at
                       FROM user_profiles WHERE user_id::text = %s LIMIT 1''',
                    (str(user_id_uuid),)
                )
                row = cursor.fetchone()
        except Exception as e:
            logging.warning(f"get_membership_tier: query failed for {user_id_uuid}: {e}")
            return {'tier': 'free', 'expires_at': None}

        if not row:
            return {'tier': 'free', 'expires_at': None}
        tier = (row.get('membership_tier') or 'free').strip().lower()
        expires = row.get('membership_expires_at')
        # If 'member' but expired, downgrade to free.
        if tier == 'member' and expires is not None:
            now = datetime.now(timezone.utc)
            try:
                if expires < now:
                    tier = 'free'
            except TypeError:
                pass
        return {'tier': tier if tier == 'member' else 'free', 'expires_at': expires}

    # ==================== Billing (Creem subscriptions) ====================

    def claim_billing_event(self, event_id: str, event_type: str,
                            user_id: Optional[str], payload: Dict[str, Any]) -> bool:
        """Record a webhook event for idempotency.

        Returns True if this is the first time we see the event (caller should
        process it), False if it was already recorded (caller should skip).
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''INSERT INTO billing_events (event_id, event_type, user_id, payload)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (event_id) DO NOTHING''',
                    (event_id, event_type, user_id, json.dumps(payload, default=str))
                )
                return cursor.rowcount > 0
        except Exception as e:
            logging.error(f"claim_billing_event failed for {event_id}: {e}")
            # On error, allow processing rather than silently dropping the event.
            return True

    def find_user_by_subscription(self, subscription_id: str) -> Optional[str]:
        """Resolve our user_id (UUID str) from a Creem subscription id."""
        if not subscription_id:
            return None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT user_id::text FROM user_profiles WHERE subscription_id = %s LIMIT 1',
                (subscription_id,)
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def activate_membership(self, user_id: str, expires_at: datetime,
                            subscription_id: Optional[str] = None,
                            customer_id: Optional[str] = None) -> None:
        """Grant/extend membership and store the Creem subscription mapping.

        Expiry is monotonic: we keep the later of the existing expiry and the
        new candidate, so a renewal never moves the date backwards (it extends
        from whatever the user already has, not from the current date). Using an
        absolute GREATEST keeps this idempotent against duplicate webhook
        deliveries (e.g. checkout.completed + subscription.paid for the same
        first payment), which would double-count if we added a fixed period.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''UPDATE user_profiles
                   SET membership_tier = 'member',
                       membership_expires_at = GREATEST(
                           %s::timestamptz,
                           COALESCE(membership_expires_at, NOW())
                       ),
                       subscription_id = COALESCE(%s, subscription_id),
                       customer_id = COALESCE(%s, customer_id)
                   WHERE user_id::text = %s''',
                (expires_at, subscription_id, customer_id, str(user_id))
            )
        logging.info(f"Membership activated for {user_id} until >= {expires_at} (sub={subscription_id})")

    def expire_membership(self, user_id: str) -> None:
        """Immediately downgrade a user to free (refund / hard expiry)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''UPDATE user_profiles
                   SET membership_tier = 'free',
                       membership_expires_at = NOW()
                   WHERE user_id::text = %s''',
                (str(user_id),)
            )
        logging.info(f"Membership expired/downgraded for {user_id}")

    def get_customer_id(self, user_id: str) -> Optional[str]:
        """Return the stored Creem customer_id for a user, if any."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT customer_id FROM user_profiles WHERE user_id::text = %s LIMIT 1',
                (str(user_id),)
            )
            row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def attach_user_to_event(self, event_id: str, user_id: str) -> None:
        """Backfill the resolved user_id onto a billing_events row.

        The webhook claims the event for idempotency before it knows which user
        it belongs to, so the row is inserted with user_id = NULL. Once resolved
        we attach it so the payment history can be queried by user.
        """
        if not (event_id and user_id):
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE billing_events SET user_id = %s WHERE event_id = %s AND user_id IS NULL',
                (str(user_id), event_id)
            )

    def get_billing_history(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch raw billing_events rows for a user (newest first).

        Matches both the resolved user_id column and the user_id carried in the
        webhook payload metadata, so legacy rows recorded before user_id
        backfill are still returned.
        """
        if not user_id:
            return []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''SELECT event_type, payload, created_at
                   FROM billing_events
                   WHERE user_id::text = %s
                      OR payload->'object'->'metadata'->>'user_id' = %s
                   ORDER BY created_at DESC
                   LIMIT %s''',
                (str(user_id), str(user_id), limit)
            )
            rows = cursor.fetchall()
        history: List[Dict[str, Any]] = []
        for event_type, payload, created_at in rows:
            history.append({
                "event_type": event_type,
                "payload": payload if isinstance(payload, dict) else {},
                "created_at": created_at.isoformat() if created_at else None,
            })
        return history

    # ==================== Daily Archives CRUD ====================

    def delete_daily_archive(self, date_str: str):
        """Delete archive for a specific date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM daily_archives WHERE archive_date = %s', (date_str,))
            deleted = cursor.rowcount
            if deleted > 0:
                logging.info(f"Deleted existing daily archive for {date_str}: {deleted} articles")

    def save_daily_archive(self, date_str: str, articles: List[Dict[str, Any]]):
        """Save daily archive."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            for rank, article in enumerate(articles):
                labels = article.get('labels', [])
                if isinstance(labels, list):
                    labels = json.dumps(labels)
                
                cursor.execute('''
                    INSERT INTO daily_archives 
                    (archive_date, article_id, rank, title, title_en, title_zh,
                     summary_en, summary_zh, long_summary_en, long_summary_zh,
                     importance_score, relevance_score, source, category,
                     published_at, url, labels)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    date_str,
                    article.get('id', ''),
                    rank,
                    article.get('title', ''),
                    article.get('title_en', ''),
                    article.get('title_zh', ''),
                    article.get('summary_en', ''),
                    article.get('summary_zh', ''),
                    article.get('long_summary_en', ''),
                    article.get('long_summary_zh', ''),
                    article.get('importance_score', 0),
                    article.get('relevance_score', 0),
                    article.get('source', ''),
                    article.get('category', ''),
                    article.get('published_at', ''),
                    article.get('url', ''),
                    labels
                ))
            
            logging.info(f"Saved daily archive for {date_str}: {len(articles)} articles")
            return True

    def get_daily_archive(self, date_str: str) -> List[Dict[str, Any]]:
        """Get archive articles for a specific date."""
        with self._get_connection() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                'SELECT * FROM daily_archives WHERE archive_date = %s ORDER BY rank',
                (date_str,)
            )
            articles = []
            for row in cursor.fetchall():
                article = dict(row)
                if article.get('labels'):
                    try:
                        article['labels'] = json.loads(article['labels'])
                    except json.JSONDecodeError:
                        article['labels'] = []
                else:
                    article['labels'] = []
                articles.append(article)
            return articles

    def get_archive_dates(self) -> List[str]:
        """Get all dates with archives (descending)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT DISTINCT archive_date FROM daily_archives ORDER BY archive_date DESC'
            )
            return [row[0] for row in cursor.fetchall()]

    def get_latest_archive_date(self) -> Optional[str]:
        """Get the latest archive date, or None if no archives exist."""
        dates = self.get_archive_dates()
        return dates[0] if dates else None
