import logging
import sys
import os
import re
import json
import time
import concurrent.futures
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), '.'))
from config_loader import ConfigLoader
from user_db import UserDatabase
from vector_store import VectorStore
from content_processor import ContentProcessor
from ai_engine import AIEngine
from fetch_stats import FetchStats
from rss_fetcher import RSSFetcher

from browser_utils import _create_browser, _close_browser, _is_playwright_domain, _fetch_article_via_playwright, _check_logins_needed, _ensure_browser_logins, LoginRequiredError
from reddit_fetcher import _fetch_reddit_content, _scrape_reddit_search, _scrape_reddit_subreddit_hot, _is_reddit_keyword_source, _is_reddit_subreddit_source, _extract_subreddit_name
from twitter_fetcher import fetch_twitter_posts
from github_fetcher import fetch_github_trending
from hackernews_fetcher import _fetch_hackernews_content
from youtube_fetcher import _fetch_youtube_content


def cleanup_old_articles():
    """Clean up articles older than retention period and sync vector store."""
    logging.info("Starting Old Articles Cleanup...")
    
    config = ConfigLoader.get_instance()
    cleanup_config = config.get('cleanup', {})
    retention_days = cleanup_config.get('article_retention_days', 90)
    tag_stats_retention_days = cleanup_config.get('tag_stats_retention_days', 90)

    user_db = UserDatabase()
    vector_store = VectorStore()
    
    # 先获取要删除的文章 ID
    expired_ids = user_db.get_expired_article_ids(retention_days)
    
    # 删除数据库中的过期文章
    deleted_count = user_db.cleanup_old_articles(retention_days)
    
    # 同步删除向量库中的向量
    if expired_ids:
        vector_store.delete_articles(expired_ids)
        logging.info(f"Synced vector store: deleted {len(expired_ids)} vectors")
    
    logging.info(f"Cleaned up {deleted_count} old articles (retention: {retention_days} days)")

    # 清理过期的热点标签统计
    user_db.cleanup_old_tag_stats(tag_stats_retention_days)

def _process_single_entry(entry, sources, source_map, user_db, ai_engine, vector_store,
                           fetch_stats, min_upvotes, min_comments, cutoff_date,
                           fetching_config, browser_page=None, browser_context=None,
                           reddit_fetched_counter=None, youtube_fetched_counter=None,
                           twitter_fetched_counter=None, counter_lock=None, processed_lock=None,
                           is_interactive=False,
                           discussion_cutoff_date=None, discussion_update_cutoff=None,
                           discussion_too_new_cutoff=None,
                           log_prefix: str = ''):
    """
    处理单条 entry：内容抓取 → 去重 → LLM 摘要 → 入库。
    
    Returns:
        1 if article was successfully processed and stored, 0 otherwise.
    """
    import time
    import random
    from lsh_dedup import compute_simhash
    
    title = entry['title']
    url = entry['link']
    
    # 获取当前 entry 的 RSS 源信息
    entry_source_id = entry.get('source_id')
    if not entry_source_id:
        for s in sources:
            if s['name'] == entry.get('source'):
                entry_source_id = s['id']
                break
    
    rss_info = source_map.get(entry_source_id, {})
    rss_url = rss_info.get('url', 'unknown')
    _rss_desc = rss_info.get('description') or ''
    _rss_name = rss_info.get('name') or ''
    if _rss_desc and _rss_name:
        rss_desc = f"{_rss_desc} [{_rss_name}]"
    else:
        rss_desc = _rss_desc or _rss_name or 'Unknown Source'

    # 统一日志前缀：调用方未传则用 rss_desc 自动构造
    if not log_prefix:
        log_prefix = f"[desc={rss_desc}] "

    logging.debug(f"{log_prefix}Processing entry: {title[:80]}  ->  {url}")

    # URL Deduplication
    # HN posts: use comments_url as canonical URL for dedup (topic_url is only for content fetching)
    dedup_url = entry['comments_url'] if entry.get('is_hackernews') and entry.get('comments_url') else url
    if user_db.article_url_exists(dedup_url):
        logging.debug(f"{log_prefix}Skipping processed URL: {dedup_url}")
        fetch_stats.record_skip(rss_url, rss_desc, 'url_exists')
        return 0
    
    # Filter by published date (with source-specific logic)
    published_at = entry.get('published_at')
    if published_at:
        try:
            if isinstance(published_at, str):
                pub_date = None
                for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                    try:
                        pub_date = datetime.strptime(published_at[:19], fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
            elif isinstance(published_at, datetime):
                pub_date = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
            else:
                pub_date = None
            
            if pub_date:
                is_hn = entry.get('is_hackernews')
                is_reddit = entry.get('is_reddit')
                is_hf_papers = entry.get('is_hf_papers')

                if is_hf_papers:
                    # HF Daily Papers API already returns only the latest curated
                    # batch, so accept whatever it returns regardless of pub date.
                    pass
                elif is_hn:
                    # HackerNews: RSS 返回的就是最近更新的帖子，只需判断 pubDate 在 N 天内
                    effective_cutoff = discussion_cutoff_date or cutoff_date
                    if pub_date < effective_cutoff:
                        logging.debug(f"{log_prefix}Skipping old HN article (published: {published_at}): {title}")
                        fetch_stats.record_skip(rss_url, rss_desc, 'old_article')
                        return 0
                elif is_reddit:
                    # Reddit: pubDate 在 N 天内 + updated 在 24h 内 + 至少发布 6h
                    effective_cutoff = discussion_cutoff_date or cutoff_date
                    if pub_date < effective_cutoff:
                        logging.debug(f"{log_prefix}Skipping old Reddit article (published: {published_at}): {title}")
                        fetch_stats.record_skip(rss_url, rss_desc, 'old_article')
                        return 0
                    # 检查 updated_at 是否在指定时间内
                    if discussion_update_cutoff:
                        updated_at = entry.get('updated_at')
                        if updated_at:
                            upd_date = None
                            for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']:
                                try:
                                    upd_date = datetime.strptime(updated_at[:19], fmt).replace(tzinfo=timezone.utc)
                                    break
                                except ValueError:
                                    continue
                            if upd_date and upd_date < discussion_update_cutoff:
                                logging.debug(f"{log_prefix}Skipping Reddit article (updated too old: {updated_at}): {title}")
                                fetch_stats.record_skip(rss_url, rss_desc, 'reddit_update_old')
                                return 0
                    # 过滤太新的帖子（评论太少）
                    if discussion_too_new_cutoff and pub_date > discussion_too_new_cutoff:
                        logging.debug(f"{log_prefix}Skipping too-new Reddit article (published: {published_at}): {title}")
                        fetch_stats.record_skip(rss_url, rss_desc, 'reddit_too_new')
                        return 0
                else:
                    # 其他源：保持原有逻辑
                    if pub_date < cutoff_date:
                        logging.debug(f"{log_prefix}Skipping old article (published: {published_at}): {title}")
                        fetch_stats.record_skip(rss_url, rss_desc, 'old_article')
                        return 0
        except Exception as e:
            logging.warning(f"{log_prefix}Failed to parse published_at '{published_at}': {e}")
    
    source_id = entry_source_id
    if not source_id:
        logging.warning(f"{log_prefix}Could not find source_id for entry: {title}")
        fetch_stats.record_skip(rss_url, rss_desc, 'no_source_id')
        return 0

    # Content Extraction
    logging.info(f"{log_prefix}Fetching content from: {url}")
    
    discussion_importance_score = None
    
    # Rate limiting configs (per source type)
    discussion_cfg = fetching_config.get('discussion', {})
    reddit_cfg = fetching_config.get('reddit', {})
    twitter_cfg = fetching_config.get('twitter', {})
    youtube_cfg = fetching_config.get('youtube', {})
    reddit_max_per_run = reddit_cfg.get('max_per_run', 10)
    reddit_delay_min = discussion_cfg.get('post_delay_min', 3)
    reddit_delay_max = discussion_cfg.get('post_delay_max', 8)
    youtube_max_per_run = youtube_cfg.get('max_per_run', 10)
    youtube_delay_min = youtube_cfg.get('delay_min', 30)
    youtube_delay_max = youtube_cfg.get('delay_max', 60)
    twitter_max_per_run = twitter_cfg.get('max_per_run', 50)
    
    if entry.get('is_hackernews') and entry.get('comments_url'):
        full_text, discussion_importance_score, points, hn_comments = _fetch_hackernews_content(url, entry['comments_url'], page=browser_page, log_prefix=log_prefix)
        # Switch to comments_url as canonical URL from here on
        url = entry['comments_url']
        if not full_text:
            logging.warning(f"{log_prefix}Failed to extract HN content. Skipping. URL: {url}")
            fetch_stats.record_failure(rss_url, rss_desc, 'hn_extract_failed')
            return 0
        if points < min_upvotes or hn_comments < min_comments:
            # Record with delayed re-fetch based on engagement gap
            gap_ratio = 1 - max(
                points / max(min_upvotes, 1),
                hn_comments / max(min_comments, 1)
            )
            gap_ratio = max(0.0, min(1.0, gap_ratio))
            discussion_min_age_hours = discussion_cfg.get('min_age_hours', 2)
            delay_hours = discussion_min_age_hours + gap_ratio * 6
            next_fetch_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
            user_db.record_fetched_url(url, source_id, next_fetch_at=next_fetch_at)
            logging.info(f"{log_prefix}Skipping HN post (points={points}, comments={hn_comments}) below threshold, next_fetch_at={next_fetch_at.strftime('%m-%d %H:%M')}: {title}")
            fetch_stats.record_skip(rss_url, rss_desc, 'hn_low_engagement')
            return 0
    elif entry.get('is_reddit'):
        with counter_lock:
            current_count = reddit_fetched_counter[0]
            if current_count >= reddit_max_per_run:
                logging.info(f"{log_prefix}Reached Reddit limit ({reddit_max_per_run}), skipping. URL: {url}")
                fetch_stats.record_skip(rss_url, rss_desc, 'reddit_limit')
                return 0
            if current_count > 0:
                delay = random.uniform(reddit_delay_min, reddit_delay_max)
                logging.info(f"{log_prefix}Waiting {delay:.1f}s before next Reddit request...")
                # 释放锁后再 sleep，避免阻塞其他线程
            else:
                delay = 0
            reddit_fetched_counter[0] += 1

        if delay > 0:
            time.sleep(delay)

        full_text, discussion_importance_score, reddit_upvotes, reddit_comments = _fetch_reddit_content(url, page=browser_page, is_interactive=is_interactive, log_prefix=log_prefix)

        if not full_text:
            logging.warning(f"{log_prefix}Failed to extract Reddit content. Skipping. URL: {url}")
            fetch_stats.record_failure(rss_url, rss_desc, 'reddit_extract_failed')
            return 0
        # 来自搜索页直抓的 entry 已在搜索页过过阈值，跳过详情页二次比较
        if entry.get('_pre_filtered'):
            pass
        elif reddit_upvotes < min_upvotes or reddit_comments < min_comments:
            # Record with delayed re-fetch based on engagement gap
            gap_ratio = 1 - max(
                reddit_upvotes / max(min_upvotes, 1),
                reddit_comments / max(min_comments, 1)
            )
            gap_ratio = max(0.0, min(1.0, gap_ratio))
            discussion_min_age_hours = discussion_cfg.get('min_age_hours', 2)
            delay_hours = discussion_min_age_hours + gap_ratio * 6
            next_fetch_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
            user_db.record_fetched_url(url, source_id, next_fetch_at=next_fetch_at)
            logging.info(f"{log_prefix}Skipping Reddit post (upvotes={reddit_upvotes}, comments={reddit_comments}) below threshold, next_fetch_at={next_fetch_at.strftime('%m-%d %H:%M')}: {title}")
            fetch_stats.record_skip(rss_url, rss_desc, 'reddit_low_engagement')
            return 0
    elif entry.get('is_youtube'):
        with counter_lock:
            current_count = youtube_fetched_counter[0]
            if current_count >= youtube_max_per_run:
                logging.info(f"{log_prefix}Reached YouTube limit ({youtube_max_per_run}), skipping. URL: {url}")
                fetch_stats.record_skip(rss_url, rss_desc, 'youtube_limit')
                return 0
            if current_count > 0:
                delay = random.uniform(youtube_delay_min, youtube_delay_max)
                logging.info(f"{log_prefix}Waiting {delay:.1f}s before next YouTube request...")
            else:
                delay = 0
            youtube_fetched_counter[0] += 1

        if delay > 0:
            time.sleep(delay)

        full_text, video_id = _fetch_youtube_content(url, page=browser_page, browser_context=browser_context, log_prefix=log_prefix)

        if not full_text:
            logging.info(f"{log_prefix}Skipping YouTube video without English subtitles: {title}")
            fetch_stats.record_skip(rss_url, rss_desc, 'youtube_no_transcript')
    elif entry.get('is_twitter'):
        with counter_lock:
            if twitter_fetched_counter:
                current_count = twitter_fetched_counter[0]
                if current_count >= twitter_max_per_run:
                    logging.info(f"{log_prefix}Reached Twitter limit ({twitter_max_per_run}), skipping. URL: {url}")
                    fetch_stats.record_skip(rss_url, rss_desc, 'twitter_limit')
                    return 0
                twitter_fetched_counter[0] += 1

        # 优先用浏览器获取完整的主贴+回复内容
        if browser_page is not None:
            try:
                tweet_text, tweet_title, tweet_pub, tweet_last_reply = _fetch_tweet_content(browser_page, url, log_prefix=log_prefix)
                if tweet_text:
                    full_text = tweet_text
                    logging.info(f"{log_prefix}Extracted Twitter content via Playwright ({len(full_text)} chars).")
                else:
                    full_text = None
            except Exception as e:
                logging.warning(f"{log_prefix}Playwright fetch failed for RSS entry, falling back to RSS: {e}")
                full_text = None
        else:
            full_text = None

        # Fallback: 从 RSS summary 提取纯文本
        if not full_text:
            from bs4 import BeautifulSoup
            raw_html = entry.get('summary', '')
            if '<' in raw_html and '>' in raw_html:
                soup = BeautifulSoup(raw_html, 'html.parser')
                for script in soup(["script", "style"]):
                    script.decompose()
                full_text = soup.get_text(separator='\n\n', strip=True)
            else:
                full_text = raw_html

        if not full_text:
            logging.warning(f"{log_prefix}Failed to extract Twitter content. Skipping. URL: {url}")
            fetch_stats.record_failure(rss_url, rss_desc, 'twitter_extract_failed')
            return 0
        if browser_page is None:
            logging.info(f"{log_prefix}Extracted Twitter content from RSS feed (no browser available).")
    elif _is_playwright_domain(url, fetching_config):
        full_text = _fetch_article_via_playwright(url, page=browser_page, log_prefix=log_prefix)
        if not full_text:
            logging.warning(f"{log_prefix}Failed to extract Playwright content. Skipping. URL: {url}")
            fetch_stats.record_failure(rss_url, rss_desc, 'playwright_extract_failed')
            return 0
    else:
        full_text = ContentProcessor.fetch_and_extract(url)
        if not full_text:
            logging.warning(f"{log_prefix}Failed to extract content. Skipping. URL: {url}")
            fetch_stats.record_failure(rss_url, rss_desc, 'content_extract_failed')
            return 0
    
    # 记录已成功获取内容的URL
    user_db.record_fetched_url(url, source_id)
    
    # LSH Deduplication
    content_hash = compute_simhash(full_text)
    similar_article = user_db.get_similar_article(content_hash)
    if similar_article:
        logging.warning(f"{log_prefix}Skipping duplicate content (LSH): {title}")
        logging.warning(f"{log_prefix}  [Current] {title} - {url}")
        logging.warning(f"{log_prefix}  [Duplicate] {similar_article['title']} - {similar_article['url']}")
        fetch_stats.record_skip(rss_url, rss_desc, 'lsh_duplicate')
        return 0
    
    # Get category from RSS source
    source_category = None
    source_importance_score = 0
    for s in sources:
        if s['id'] == source_id:
            source_category = s.get('category')
            source_importance_score = s.get('importance_score', 0)
            break
    
    # Summarization
    logging.info(f"{log_prefix}Summarizing content: {title[:50]}... (category: {source_category})")
    summary_en, summary_zh, long_summary_en, long_summary_zh, ai_importance_score, tags, title_en, title_zh, china_blocked = ai_engine.summarize_content(full_text, title=title, category=source_category)

    if china_blocked:
        logging.warning(f"{log_prefix}China compliance blocked. Skipping. URL: {url}")
        fetch_stats.record_skip(rss_url, rss_desc, 'china_blocked')
        return 0

    # AI 相关性硬过滤：提示词约定"与 AI 无关 = 0 分"，此处直接丢弃，不入库
    if ai_importance_score == 0:
        logging.info(f"{log_prefix}Not AI-related (score=0). Skipping. URL: {url}")
        fetch_stats.record_skip(rss_url, rss_desc, 'not_ai_related')
        return 0

    # 阿里云内容安全二次过滤（与邮件审核同源规则，防止 SMTP 整封拒收）
    from compliance_filter import check_summary
    _safe, _labels = check_summary(title_en, title_zh, summary_en, summary_zh,
                                   long_summary_en, long_summary_zh)
    if not _safe:
        logging.warning(f"{log_prefix}Aliyun Green blocked [{_labels}]. Skipping. URL: {url}")
        fetch_stats.record_skip(rss_url, rss_desc, 'aliyun_green_blocked')
        return 0

    # 摘要完整性门槛（已通过重试补全仍缺失则丢弃，计入报告的 incomplete_summary）：
    # 双语短摘要必须都有；长摘要仅在原文够长（≥1000 字，与提示词阈值一致）时才要求。
    long_required = len(full_text) >= 1000
    missing = []
    if not (summary_en or '').strip():
        missing.append('summary_en')
    if not (summary_zh or '').strip():
        missing.append('summary_zh')
    if long_required:
        if not (long_summary_en or '').strip():
            missing.append('long_summary_en')
        if not (long_summary_zh or '').strip():
            missing.append('long_summary_zh')
    if missing:
        logging.warning(f"{log_prefix}Incomplete summary, missing {missing}. Skipping. URL: {url}")
        fetch_stats.record_failure(rss_url, rss_desc, 'incomplete_summary')
        return 0

    # Vector DB semantic deduplication
    similar_articles = vector_store.find_similar_articles(
        summary=summary_en or summary_zh,
        tags=tags,
        threshold=0.85,
        limit=1
    )
    if similar_articles:
        dup = similar_articles[0]
        logging.warning(f"{log_prefix}Skipping semantic duplicate (similarity={dup['similarity']:.3f}): {title}")
        logging.warning(f"{log_prefix}  [Duplicate of] article_id={dup['id']}")
        fetch_stats.record_skip(rss_url, rss_desc, 'vector_duplicate')
        return 0
    
    # 计算 LLM 输入输出字数
    llm_input_chars = min(len(full_text), 8000)
    llm_output_chars = len(summary_en) + len(summary_zh) + len(long_summary_en) + len(long_summary_zh) + len(title_en) + len(title_zh)
    
    # Calculate final importance score
    importance_scores = [ai_importance_score, source_importance_score]
    if discussion_importance_score is not None:
        importance_scores.append(discussion_importance_score)
    importance_score = sum(importance_scores) / len(importance_scores)
    
    logging.info(f"{log_prefix} -> Tags: {tags}, AI: {ai_importance_score}, Source: {source_importance_score}, Discussion: {discussion_importance_score}, Final: {importance_score:.1f}")
    
    # Configure prefix for discussion articles
    discussion_prefix_en = ""
    discussion_prefix_zh = ""
    if entry.get('is_hackernews'):
        discussion_prefix_en = f"[{points} upvotes/{hn_comments} comments] "
        discussion_prefix_zh = f"[{points}赞/{hn_comments}评] "
    elif entry.get('is_reddit'):
        discussion_prefix_en = f"[{reddit_upvotes} upvotes/{reddit_comments} comments] "
        discussion_prefix_zh = f"[{reddit_upvotes}赞/{reddit_comments}评] "
        
    final_summary_en = f"{discussion_prefix_en}{summary_en}" if summary_en else summary_en
    final_summary_zh = f"{discussion_prefix_zh}{summary_zh}" if summary_zh else summary_zh
    
    # Storage
    article_data = {
        'rss_source_id': source_id,
        'title': title,
        'title_en': title_en,
        'title_zh': title_zh,
        'url': url,
        'summary_en': final_summary_en,
        'summary_zh': final_summary_zh,
        'long_summary_en': long_summary_en,
        'long_summary_zh': long_summary_zh,
        'category': source_category or 'news',
        'importance_score': importance_score,
        'tags': tags,
        'content_hash': content_hash,
        'published_at': entry['published_at']
    }
    article_id = user_db.add_article(article_data)

    # 写入向量库
    if article_id:
        source_name = next((s['name'] for s in sources if s['id'] == source_id), '')
        vector_store.add_article(
            article_id=article_id,
            summary=summary_en or summary_zh,
            tags=tags,
            source=source_name,
            category=source_category or 'news'
        )

    # 记录成功统计
    fetch_stats.record_success(rss_url, rss_desc, llm_input_chars, llm_output_chars)
    return 1

def _fetch_and_process_source(source: dict, rss_fetcher, sources, source_map,
                               user_db, ai_engine, vector_store, fetch_stats,
                               min_upvotes, min_comments, cutoff_date, fetching_config,
                               browser_page, browser_context,
                               reddit_fetched_counter, youtube_fetched_counter,
                               twitter_fetched_counter, counter_lock, is_interactive=False,
                               discussion_cutoff_date=None, discussion_update_cutoff=None,
                               discussion_too_new_cutoff=None):
    """
    针对单个 RSS 源：临到处理时才拉取列表（list fetch），然后串行处理其 entries。
    Reddit 关键词搜索源不走 RSS（search.rss 没有 score/comments），改为搜索页直抓。
    """
    sid = source['id']
    desc = source.get('description') or source.get('name', '')
    name = source.get('name', f'source_{sid}')
    url = source.get('url', '')
    _raw_desc = source.get('description') or ''
    if _raw_desc and name:
        stats_desc = f"{_raw_desc} [{name}]"
    else:
        stats_desc = _raw_desc or name or 'Unknown Source'
    log_prefix = f"[desc={desc}] "

    # 1) 拉取该源的 entries
    if _is_reddit_keyword_source(source):
        if browser_page is None:
            logging.warning(f"{log_prefix}Reddit keyword search needs browser, but none available; skipping")
            return 0
        keyword = (source.get('description') or '')[len('keyword '):].strip()
        if not keyword:
            logging.warning(f"{log_prefix}Empty keyword in description; skipping")
            return 0
        max_posts = fetching_config.get('reddit', {}).get('max_posts_per_keyword', 10)
        try:
            entries = _scrape_reddit_search(
                browser_page, keyword, log_prefix,
                min_upvotes, min_comments, max_posts,
                source_id=sid, source_name=name,
                user_db=user_db, is_interactive=is_interactive
            )
        except LoginRequiredError:
            raise
        except Exception as exc:
            logging.error(f"{log_prefix}Reddit search scrape failed: {exc}", exc_info=True)
            fetch_stats.record_failure(url, stats_desc, 'reddit_search_failed')
            return 0
        logging.info(f"{log_prefix}Reddit search returned {len(entries)} qualifying posts; will fetch each post detail page sequentially")
    elif _is_reddit_subreddit_source(source):
        if browser_page is None:
            logging.warning(f"{log_prefix}Reddit subreddit hot needs browser, but none available; skipping")
            return 0
        subreddit = _extract_subreddit_name(url)
        if not subreddit:
            logging.warning(f"{log_prefix}Could not extract subreddit name from URL: {url}")
            return 0
        max_posts = fetching_config.get('reddit', {}).get('max_posts_per_keyword', 10)
        try:
            entries = _scrape_reddit_subreddit_hot(
                browser_page, subreddit, log_prefix,
                min_upvotes, min_comments, max_posts,
                source_id=sid, source_name=name,
                user_db=user_db, is_interactive=is_interactive
            )
        except LoginRequiredError:
            raise
        except Exception as exc:
            logging.error(f"{log_prefix}Reddit subreddit hot scrape failed: {exc}", exc_info=True)
            fetch_stats.record_failure(url, stats_desc, 'reddit_subreddit_failed')
            return 0
        logging.info(f"{log_prefix}Reddit subreddit hot returned {len(entries)} qualifying posts; will fetch each post detail page sequentially")
    else:
        feed_config = {'url': url, 'name': name, 'id': sid, 'description': desc}
        logging.info(f"{log_prefix}Fetching RSS list...")
        result = rss_fetcher.parse_feed(feed_config)
        if result.get('error'):
            fetch_stats.record_failure(url, stats_desc, result['error'])
            logging.warning(f"{log_prefix}RSS source failed: [{result['error']}] {url}")
            return 0
        entries = result.get('entries', [])
        # 兼容用户手动配置 reddit.com/search.rss 的旧场景
        if 'reddit.com/search.rss' in url:
            max_posts = fetching_config.get('reddit', {}).get('max_posts_per_keyword', 10)
            entries = entries[:max_posts]
        logging.info(f"{log_prefix}Fetched {len(entries)} entries from RSS")

    # 2) 串行处理 entries
    count = 0
    for entry in entries:
        try:
            count += _process_single_entry(
                entry, sources, source_map, user_db, ai_engine, vector_store,
                fetch_stats, min_upvotes, min_comments, cutoff_date, fetching_config,
                browser_page=browser_page,
                browser_context=browser_context,
                reddit_fetched_counter=reddit_fetched_counter,
                youtube_fetched_counter=youtube_fetched_counter,
                twitter_fetched_counter=twitter_fetched_counter,
                counter_lock=counter_lock,
                is_interactive=is_interactive,
                discussion_cutoff_date=discussion_cutoff_date,
                discussion_update_cutoff=discussion_update_cutoff,
                discussion_too_new_cutoff=discussion_too_new_cutoff,
                log_prefix=log_prefix,
            )
        except LoginRequiredError:
            raise
        except Exception as exc:
            logging.error(f"{log_prefix}Error processing entry '{entry.get('title', 'unknown')}': {exc}", exc_info=True)

    user_db.update_rss_source_fetch_time(sid)
    logging.info(f"{log_prefix}Finished source: {count} articles processed")
    return count

def _process_browser_group(group_name, group_sources, rss_fetcher, sources, source_map,
                            user_db, ai_engine, vector_store, fetch_stats,
                            min_upvotes, min_comments, cutoff_date, fetching_config,
                            reddit_fetched_counter, youtube_fetched_counter,
                            twitter_fetched_counter, counter_lock,
                            discussion_cutoff_date=None, discussion_update_cutoff=None,
                            discussion_too_new_cutoff=None):
    """
    在独立线程中处理某一个平台组（reddit / youtube / other）的所有浏览器源。
    - 启动独立的 Playwright 实例和独立的 user_data_dir，避免跨线程共用 greenlet / Chrome 锁。
    - 组内严格串行：每条源 = "拉列表 + 串行处理 entries"，配合 reddit/youtube delay 节流。
    """
    if not group_sources:
        return 0

    logging.info(f"[BrowserGroup:{group_name}] Starting with {len(group_sources)} source(s)")

    has_reddit = (group_name == 'reddit')
    has_youtube = (group_name == 'youtube')

    def _launch_browser_safe(visible=False):
        try:
            return _create_browser(force_visible=visible, profile_name=group_name)
        except Exception as e:
            logging.warning(f"[BrowserGroup:{group_name}] Failed to launch browser (visible={visible}): {e}")
            return None, None, None

    browser_playwright, browser_context, browser_page = _launch_browser_safe(visible=False)
    is_interactive_mode = False

    if browser_context and browser_page:
        needs_login = _check_logins_needed(browser_context, has_reddit, has_youtube)
        if needs_login:
            logging.info(f"[BrowserGroup:{group_name}] Login cookies missing — relaunching visible...")
            _close_browser(browser_playwright, browser_context)
            browser_playwright, browser_context, browser_page = _launch_browser_safe(visible=True)
            is_interactive_mode = True
            if browser_context and browser_page:
                _ensure_browser_logins(browser_context, browser_page, has_reddit, has_youtube)

    processed = 0
    try:
        for source in group_sources:
            sname = source.get('name', f"source_{source.get('id')}")
            try:
                if not browser_page:
                    browser_playwright, browser_context, browser_page = _launch_browser_safe(visible=is_interactive_mode)
                if not browser_page:
                    logging.error(f"[BrowserGroup:{group_name}] Browser unavailable, skipping source '{sname}'")
                    continue

                count = _fetch_and_process_source(
                    source, rss_fetcher, sources, source_map,
                    user_db, ai_engine, vector_store, fetch_stats,
                    min_upvotes, min_comments, cutoff_date, fetching_config,
                    browser_page, browser_context,
                    reddit_fetched_counter, youtube_fetched_counter,
                    twitter_fetched_counter, counter_lock,
                    is_interactive=is_interactive_mode,
                    discussion_cutoff_date=discussion_cutoff_date,
                    discussion_update_cutoff=discussion_update_cutoff,
                    discussion_too_new_cutoff=discussion_too_new_cutoff
                )
                processed += count
            except LoginRequiredError:
                logging.warning(f"[BrowserGroup:{group_name}] Login required during source '{sname}', relaunching visible...")
                if browser_playwright:
                    _close_browser(browser_playwright, browser_context)
                browser_playwright, browser_context, browser_page = _launch_browser_safe(visible=True)
                is_interactive_mode = True
                if browser_page:
                    try:
                        count = _fetch_and_process_source(
                            source, rss_fetcher, sources, source_map,
                            user_db, ai_engine, vector_store, fetch_stats,
                            min_upvotes, min_comments, cutoff_date, fetching_config,
                            browser_page, browser_context,
                            reddit_fetched_counter, youtube_fetched_counter,
                            twitter_fetched_counter, counter_lock,
                            is_interactive=True,
                            discussion_cutoff_date=discussion_cutoff_date,
                            discussion_update_cutoff=discussion_update_cutoff,
                            discussion_too_new_cutoff=discussion_too_new_cutoff
                        )
                        processed += count
                    except Exception as retry_exc:
                        logging.error(f"[BrowserGroup:{group_name}] Retry failed for source '{sname}': {retry_exc}")
                else:
                    logging.error(f"[BrowserGroup:{group_name}] Failed to relaunch browser in visible mode.")
            except Exception as exc:
                logging.error(f"[BrowserGroup:{group_name}] Source '{sname}' processing failed: {exc}", exc_info=True)
    finally:
        if browser_playwright:
            _close_browser(browser_playwright, browser_context)

    logging.info(f"[BrowserGroup:{group_name}] Finished: {processed} articles processed")
    return processed

def fetch_articles_for_sources(sources: list, user_db: UserDatabase, ai_engine: AIEngine, vector_store: VectorStore, fetch_stats: 'FetchStats' = None) -> int:
    """
    Fetch and process articles for the given RSS sources.
    分组方式（按源 URL，不再上来全拉一遍 RSS 列表）：
      - reddit.com → reddit 浏览器组
      - youtube.com / youtu.be → youtube 浏览器组
      - URL 命中 fetching.playwright_domains → other 浏览器组
      - 其它 → 非浏览器线程池
    每个浏览器组独立线程 + 独立 profile，组内每条源临到处理时才 parse_feed (或对 reddit
    keyword 走搜索页直抓)，然后串行处理 entries，避免 list 阶段 burst。
    """
    if not sources:
        return 0

    feeds_config = [{'url': s['url'], 'name': s['name'], 'id': s['id'], 'description': s.get('description', s['name'])} for s in sources]
    rss_fetcher = RSSFetcher(feeds_config)

    if fetch_stats is None:
        fetch_stats = FetchStats()

    source_map = {s['id']: s for s in sources}

    config = ConfigLoader.get_instance()
    fetching_config = config.get('fetching', {})
    discussion_config = fetching_config.get('discussion', {})
    min_upvotes = discussion_config.get('min_likes', 0)
    min_comments = discussion_config.get('min_replies', 0)

    article_max_age_days = fetching_config.get('article_max_age_days', 7)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=article_max_age_days)
    discussion_cutoff_date = datetime.now(timezone.utc) - timedelta(days=discussion_config.get('max_age_days', 3))
    discussion_update_cutoff = datetime.now(timezone.utc) - timedelta(hours=discussion_config.get('max_update_hours', 24))
    discussion_too_new_cutoff = datetime.now(timezone.utc) - timedelta(hours=discussion_config.get('min_age_hours', 6))

    playwright_domains = [d.lower() for d in fetching_config.get('playwright_domains', [])]

    # ===== 按源 URL 分组 =====
    reddit_sources = []
    youtube_sources = []
    other_browser_sources = []
    non_browser_sources = []
    for s in sources:
        url_lower = (s.get('url') or '').lower()
        if 'reddit.com' in url_lower:
            reddit_sources.append(s)
        elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            youtube_sources.append(s)
        elif playwright_domains and any(d in url_lower for d in playwright_domains):
            other_browser_sources.append(s)
        else:
            non_browser_sources.append(s)

    def _sort_lru(srcs):
        return sorted(
            srcs,
            key=lambda s: (s.get('last_fetch_at') is not None, s.get('last_fetch_at', ''))
        )
    reddit_sources = _sort_lru(reddit_sources)
    youtube_sources = _sort_lru(youtube_sources)
    other_browser_sources = _sort_lru(other_browser_sources)
    non_browser_sources = _sort_lru(non_browser_sources)

    logging.info(
        f"Source classification: reddit={len(reddit_sources)}, "
        f"youtube={len(youtube_sources)}, other_browser={len(other_browser_sources)}, "
        f"non_browser={len(non_browser_sources)}"
    )

    import threading
    counter_lock = threading.Lock()
    reddit_fetched_counter = [0]
    youtube_fetched_counter = [0]
    twitter_fetched_counter = [0]

    processed_count = 0

    # ===== 非浏览器源：线程池并行（每个 worker 自己 parse_feed 后串行处理 entries）=====
    if non_browser_sources:
        concurrency = fetching_config.get('concurrency', 5)
        max_workers = max(1, min(concurrency, len(non_browser_sources)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_source = {
                executor.submit(
                    _fetch_and_process_source,
                    s, rss_fetcher, sources, source_map,
                    user_db, ai_engine, vector_store, fetch_stats,
                    min_upvotes, min_comments, cutoff_date, fetching_config,
                    None, None,
                    reddit_fetched_counter, youtube_fetched_counter, twitter_fetched_counter,
                    counter_lock, False,
                    discussion_cutoff_date, discussion_update_cutoff, discussion_too_new_cutoff
                ): s for s in non_browser_sources
            }
            for future in concurrent.futures.as_completed(future_to_source):
                s = future_to_source[future]
                try:
                    processed_count += future.result()
                except Exception as exc:
                    logging.error(f"Source '{s.get('name')}' processing failed: {exc}", exc_info=True)

    # ===== 浏览器组：3 组并行，组内串行（含 list fetch + 内容 fetch）=====
    groups = []
    if reddit_sources:
        groups.append(('reddit', reddit_sources))
    if youtube_sources:
        groups.append(('youtube', youtube_sources))
    if other_browser_sources:
        groups.append(('other', other_browser_sources))

    if groups:
        logging.info(f"Parallel browser groups: {[(n, len(g)) for n, g in groups]}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(groups)) as executor:
            future_to_group = {
                executor.submit(
                    _process_browser_group,
                    name, group_sources, rss_fetcher, sources, source_map,
                    user_db, ai_engine, vector_store, fetch_stats,
                    min_upvotes, min_comments, cutoff_date, fetching_config,
                    reddit_fetched_counter, youtube_fetched_counter,
                    twitter_fetched_counter, counter_lock,
                    discussion_cutoff_date=discussion_cutoff_date,
                    discussion_update_cutoff=discussion_update_cutoff,
                    discussion_too_new_cutoff=discussion_too_new_cutoff
                ): name for name, group_sources in groups
            }
            for future in concurrent.futures.as_completed(future_to_group):
                name = future_to_group[future]
                try:
                    processed_count += future.result()
                except Exception as exc:
                    logging.error(f"Browser group '{name}' failed: {exc}", exc_info=True)

    rss_fetcher.close()
    return processed_count

def run_pipeline():
    """Main pipeline: fetch articles for all active RSS sources."""
    logging.info("Starting InBrief Pipeline...")
    
    # 1. Init modules
    user_db = UserDatabase()
    config = ConfigLoader.get_instance()
    llm_config = config.get('llm')
    
    ai_engine = AIEngine(llm_config)
    vector_store = VectorStore()
    
    # 共享统计实例，所有采集管道共用一个报告
    fetch_stats = FetchStats()
    
    # 2. Get all RSS sources (no longer filtered by user association)
    all_sources = user_db.get_all_rss_sources()
    
    rss_sources = []
    twitter_sources = []
    for s in all_sources:
        url = s.get('url', '')
        desc_original = s.get('description', '').strip()
        desc = desc_original.lower()
        # Reddit 关键词搜索源：保留原始 URL，由 RSS pipeline 内部识别后走搜索页直抓
        if 'reddit.com' in url and desc.startswith('keyword '):
            rss_sources.append(s)
            continue
        if desc.startswith('keyword ') or desc.startswith('kol '):
            twitter_sources.append(s)
        else:
            rss_sources.append(s)
    
    # 3-5. RSS / Twitter / GitHub 三大采集管道并行。
    # 彼此独立：写库靠 UserDatabase 的线程安全连接池，fetch_stats 自带锁，
    # 各 pipeline 的浏览器使用不同的 playwright profile 子目录（reddit/youtube/other/twitter），
    # 不会出现 Chrome user_data_dir 锁冲突。
    pipeline_jobs = []
    if not rss_sources:
        logging.info("No regular RSS sources found. Skipping RSS fetch.")
    else:
        logging.info(f"Found {len(rss_sources)} regular RSS sources")
        pipeline_jobs.append(('RSS', lambda: fetch_articles_for_sources(
            rss_sources, user_db, ai_engine, vector_store, fetch_stats=fetch_stats)))

    pipeline_jobs.append(('Twitter', lambda: fetch_twitter_posts(
        twitter_sources, user_db, ai_engine, vector_store, fetch_stats=fetch_stats)))
    pipeline_jobs.append(('GitHub', lambda: fetch_github_trending(
        user_db, ai_engine, vector_store, fetch_stats=fetch_stats)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(pipeline_jobs)) as executor:
        future_to_name = {executor.submit(job): name for name, job in pipeline_jobs}
        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            try:
                processed = future.result()
                logging.info(f"{name} Pipeline Finished. Processed: {processed}")
            except Exception as e:
                logging.error(f"{name} pipeline failed: {e}", exc_info=True)

    # 6. 统一输出所有采集管道的统计报告
    fetch_stats.log_report()
    
    logging.info("All pipelines finished.")

