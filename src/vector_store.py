"""
Vector store using Supabase pgvector for semantic article operations.
Replaces ChromaDB with PostgreSQL + pgvector extension.
"""

import logging
import json
from typing import List, Dict, Optional, Any
import numpy as np


def _get_embedding_model():
    """Get the embedding model from embedding_filter module."""
    from embedding_filter import _get_model
    return _get_model()


class VectorStore:
    """
    pgvector-based vector store for article semantic operations.
    Supports semantic deduplication, interest-based search, and filtering.
    Uses the article_vectors table created by UserDatabase._init_database().
    """
    
    TABLE_NAME = "article_vectors"
    
    def __init__(self, db_url: str = None):
        """
        Initialize the vector store.
        
        Args:
            db_url: PostgreSQL connection string (Supabase)
        """
        if db_url is None:
            from config_loader import ConfigLoader
            config = ConfigLoader.get_instance().get('supabase', {})
            db_url = config.get('db_url', '')
        self.db_url = db_url
        self._model = None
        self._pool = None
    
    def _get_pool(self):
        """Get connection pool (reuse UserDatabase pool if available)."""
        if self._pool is None:
            from user_db import UserDatabase
            if UserDatabase._pool and not UserDatabase._pool.closed:
                self._pool = UserDatabase._pool
            else:
                import psycopg2.pool
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1, maxconn=5, dsn=self.db_url
                )
        return self._pool
    
    def _get_conn(self):
        """Get a connection from pool."""
        return self._get_pool().getconn()
    
    def _put_conn(self, conn):
        """Return connection to pool."""
        self._get_pool().putconn(conn)
    
    def _get_model(self):
        """Get embedding model (lazy load)."""
        if self._model is None:
            self._model = _get_embedding_model()
        return self._model
    
    def _embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts using the sentence transformer model."""
        if not texts:
            return []
        model = self._get_model()
        embeddings = model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()
    
    def _build_document(self, summary: str, tags: List[str]) -> str:
        """Build document string for embedding."""
        tags_str = ", ".join(tags) if tags else ""
        if tags_str:
            return f"{summary} | Tags: {tags_str}"
        return summary
    
    def _vec_to_str(self, vec: List[float]) -> str:
        """Convert vector to PostgreSQL vector string format."""
        return '[' + ','.join(str(f) for f in vec) + ']'
    
    # ==================== Article CRUD ====================
    
    def add_article(self, article_id: str, summary: str, tags: List[str], source: str, category: str = "news") -> bool:
        """Add an article to the vector store."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                # Check existence
                cursor.execute(f'SELECT 1 FROM {self.TABLE_NAME} WHERE article_id = %s', (article_id,))
                if cursor.fetchone():
                    conn.commit()
                    return True
                
                document = self._build_document(summary, tags)
                embeddings = self._embed([document])
                
                if not embeddings:
                    conn.commit()
                    return False
                
                vec_str = self._vec_to_str(embeddings[0])
                tags_json = json.dumps(tags) if tags else '[]'
                
                cursor.execute(f'''
                    INSERT INTO {self.TABLE_NAME} (article_id, embedding, document, source, category, tags)
                    VALUES (%s, %s::vector, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (article_id) DO NOTHING
                ''', (article_id, vec_str, document, source, category or "news", tags_json))
                
                conn.commit()
                logging.debug(f"Added article {article_id} to vector store")
                return True
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                self._put_conn(conn)
                
        except Exception as e:
            logging.error(f"Error adding article {article_id} to vector store: {e}")
            return False
    
    def delete_article(self, article_id: str) -> bool:
        """Delete an article from the vector store."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(f'DELETE FROM {self.TABLE_NAME} WHERE article_id = %s', (article_id,))
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                self._put_conn(conn)
        except Exception as e:
            logging.error(f"Error deleting article {article_id}: {e}")
            return False
    
    def delete_articles(self, article_ids: List[str]) -> int:
        """Delete multiple articles from the vector store."""
        if not article_ids:
            return 0
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(f'DELETE FROM {self.TABLE_NAME} WHERE article_id = ANY(%s)', (article_ids,))
                deleted = cursor.rowcount
                conn.commit()
                logging.info(f"Deleted {deleted} articles from vector store")
                return deleted
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                self._put_conn(conn)
        except Exception as e:
            logging.error(f"Error deleting articles: {e}")
            return 0
    
    def article_exists(self, article_id: str) -> bool:
        """Check if an article exists in the vector store."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(f'SELECT 1 FROM {self.TABLE_NAME} WHERE article_id = %s', (article_id,))
                result = cursor.fetchone() is not None
                conn.commit()
                return result
            finally:
                self._put_conn(conn)
        except Exception:
            return False
    
    def get_count(self) -> int:
        """Get the total number of articles in the vector store."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(f'SELECT COUNT(*) FROM {self.TABLE_NAME}')
                count = cursor.fetchone()[0]
                conn.commit()
                return count
            finally:
                self._put_conn(conn)
        except Exception:
            return 0
    
    # ==================== Semantic Deduplication ====================
    
    def find_similar_articles(self, summary: str, tags: List[str] = None, 
                               threshold: float = 0.85, recent_threshold: float = 0.80, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Find similar articles based on semantic similarity.
        Uses pgvector cosine distance operator <=>.
        For articles published within the last 24 hours, uses recent_threshold.
        For older articles, uses threshold.
        """
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                cursor.execute(f'SELECT COUNT(*) FROM {self.TABLE_NAME}')
                if cursor.fetchone()[0] == 0:
                    conn.commit()
                    return []
                
                document = self._build_document(summary, tags or [])
                embeddings = self._embed([document])
                
                if not embeddings:
                    conn.commit()
                    return []
                
                vec_str = self._vec_to_str(embeddings[0])
                
                # cosine distance: 0 = identical, 2 = opposite
                # similarity = 1 - cosine_distance
                cursor.execute(f'''
                    SELECT v.article_id, 1 - (v.embedding <=> %s::vector) AS similarity,
                           v.source, v.category, v.tags
                    FROM {self.TABLE_NAME} v
                    LEFT JOIN articles a ON v.article_id = a.id
                    WHERE (1 - (v.embedding <=> %s::vector)) >= %s
                       OR (a.published_at IS NOT NULL 
                           AND a.published_at >= NOW() - INTERVAL '24 hours' 
                           AND (1 - (v.embedding <=> %s::vector)) >= %s)
                    ORDER BY v.embedding <=> %s::vector
                    LIMIT %s
                ''', (vec_str, vec_str, threshold, vec_str, recent_threshold, vec_str, limit))
                
                similar = []
                for row in cursor.fetchall():
                    similar.append({
                        'id': row[0],
                        'similarity': float(row[1]),
                        'metadata': {
                            'source': row[2] or '',
                            'category': row[3] or '',
                            'tags': json.dumps(row[4]) if row[4] else '[]'
                        }
                    })
                
                conn.commit()
                return similar
            finally:
                self._put_conn(conn)
                
        except Exception as e:
            logging.error(f"Error finding similar articles: {e}")
            return []
    
    def is_duplicate(self, summary: str, tags: List[str] = None, 
                     threshold: float = 0.85, recent_threshold: float = 0.80) -> Optional[str]:
        """Check if an article is a duplicate."""
        similar = self.find_similar_articles(summary, tags, threshold, recent_threshold, limit=1)
        if similar:
            return similar[0]['id']
        return None
    
    # ==================== Interest-based Search ====================
    
    def search_by_interest(self, interest_desc: str, limit: int = 50) -> List[tuple]:
        """
        Search articles matching user's interest description.
        Returns list of (article_id, relevance_score) tuples, score 0-100.
        """
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                cursor.execute(f'SELECT COUNT(*) FROM {self.TABLE_NAME}')
                if cursor.fetchone()[0] == 0 or not interest_desc:
                    conn.commit()
                    return []
                
                embeddings = self._embed([interest_desc])
                if not embeddings:
                    conn.commit()
                    return []
                
                vec_str = self._vec_to_str(embeddings[0])
                
                cursor.execute(f'''
                    SELECT article_id, 1 - (embedding <=> %s::vector) AS similarity
                    FROM {self.TABLE_NAME}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                ''', (vec_str, vec_str, limit))
                
                scored = []
                for row in cursor.fetchall():
                    similarity = float(row[1])
                    # cosine similarity → score 0-100
                    score = max(0, min(100, int(similarity * 100)))
                    scored.append((row[0], score))
                
                conn.commit()
                return scored
            finally:
                self._put_conn(conn)
                
        except Exception as e:
            logging.error(f"Error searching by interest: {e}")
            return []
    
    def search_by_interest_per_category(self, interest_desc: str,
                                         categories: List[str],
                                         per_category_limit: int = 10) -> Dict[str, List[tuple]]:
        """按 category 分别搜索与用户兴趣最匹配的文章。"""
        results = {}
        if not interest_desc or not categories:
            return results
        
        try:
            embeddings = self._embed([interest_desc])
            if not embeddings:
                return results
            
            vec_str = self._vec_to_str(embeddings[0])
            
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                for cat in categories:
                    try:
                        cursor.execute(f'''
                            SELECT article_id, 1 - (embedding <=> %s::vector) AS similarity
                            FROM {self.TABLE_NAME}
                            WHERE category = %s
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                        ''', (vec_str, cat, vec_str, per_category_limit))
                        
                        scored = []
                        for row in cursor.fetchall():
                            similarity = float(row[1])
                            score = max(0, min(100, int(similarity * 100)))
                            scored.append((row[0], score))
                        
                        results[cat] = scored
                        logging.debug(f"Interest search [{cat}]: {len(scored)} articles found")
                    except Exception as e:
                        logging.warning(f"Interest search [{cat}] failed: {e}")
                        results[cat] = []
                
                conn.commit()
            finally:
                self._put_conn(conn)
            
            return results
            
        except Exception as e:
            logging.error(f"Error in search_by_interest_per_category: {e}")
            return results
    
    def filter_by_negative_tags(self, article_ids: List[str], 
                                 negative_tags: List[str],
                                 threshold: float = 0.6) -> List[str]:
        """Filter out articles that match user's negative interest tags."""
        if not article_ids or not negative_tags:
            return article_ids
        
        try:
            # Embed negative tags
            negative_embedding = self._embed([", ".join(negative_tags)])
            if not negative_embedding:
                return article_ids
            
            negative_vec = np.array(negative_embedding[0])
            vec_str = self._vec_to_str(negative_embedding[0])
            
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                # Get cosine similarity for each article against negative tags
                cursor.execute(f'''
                    SELECT article_id, 1 - (embedding <=> %s::vector) AS similarity
                    FROM {self.TABLE_NAME}
                    WHERE article_id = ANY(%s)
                ''', (vec_str, article_ids))
                
                filtered_ids = []
                found_ids = set()
                for row in cursor.fetchall():
                    found_ids.add(row[0])
                    similarity = float(row[1])
                    if similarity < threshold:
                        filtered_ids.append(row[0])
                
                # Keep articles not in vector store
                for aid in article_ids:
                    if aid not in found_ids:
                        filtered_ids.append(aid)
                
                conn.commit()
                logging.debug(f"Negative tag filter: {len(filtered_ids)}/{len(article_ids)} passed")
                return filtered_ids
            finally:
                self._put_conn(conn)
                
        except Exception as e:
            logging.error(f"Error filtering by negative tags: {e}")
            return article_ids
    
    def filter_by_blocked_sources(self, article_ids: List[str],
                                   blocked_sources: List[str],
                                   threshold: float = 0.7) -> List[str]:
        """Filter out articles from blocked sources using metadata exact match."""
        if not article_ids or not blocked_sources:
            return article_ids
        
        try:
            blocked_set = {s.strip().lower() for s in blocked_sources}
            
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                cursor.execute(f'''
                    SELECT article_id, source
                    FROM {self.TABLE_NAME}
                    WHERE article_id = ANY(%s)
                ''', (article_ids,))
                
                blocked_ids = set()
                found_ids = set()
                for row in cursor.fetchall():
                    found_ids.add(row[0])
                    source = (row[1] or '').strip().lower()
                    if source in blocked_set:
                        blocked_ids.add(row[0])
                
                # Keep articles not blocked and not found (keep by default)
                filtered_ids = [aid for aid in article_ids if aid not in blocked_ids]
                
                conn.commit()
                logging.debug(f"Blocked source filter: {len(filtered_ids)}/{len(article_ids)} passed")
                return filtered_ids
            finally:
                self._put_conn(conn)
                
        except Exception as e:
            logging.error(f"Error filtering by blocked sources: {e}")
            return article_ids
    
    # ==================== Sync Operations ====================
    
    def sync_with_articles(self, article_ids_in_db: List[str]) -> Dict[str, int]:
        """Remove vectors for articles that no longer exist in main DB."""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                cursor.execute(f'SELECT article_id FROM {self.TABLE_NAME}')
                vector_ids = {row[0] for row in cursor.fetchall()}
                
                db_ids = set(article_ids_in_db)
                ids_to_remove = list(vector_ids - db_ids)
                
                if ids_to_remove:
                    cursor.execute(f'DELETE FROM {self.TABLE_NAME} WHERE article_id = ANY(%s)', (ids_to_remove,))
                    logging.info(f"Sync: removed {len(ids_to_remove)} orphaned vectors")
                
                conn.commit()
                return {'removed': len(ids_to_remove)}
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                self._put_conn(conn)
                
        except Exception as e:
            logging.error(f"Error syncing vector store: {e}")
            return {'removed': 0}
