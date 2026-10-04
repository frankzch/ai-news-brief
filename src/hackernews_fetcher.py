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
from browser_utils import _expand_nested_replies


def _hn_stats_from_api(comments_url: str):
    """Points and comment count from HN's official Firebase API: (points, comments) or None.

    The HTML item page rate-limits fast crawlers (HTTP 419 "Sorry"), so the API is the primary source.
    """
    import requests
    m = re.search(r'[?&]id=(\d+)', comments_url or '')
    if not m:
        return None
    try:
        resp = requests.get(f'https://hacker-news.firebaseio.com/v0/item/{m.group(1)}.json', timeout=10)
        resp.raise_for_status()
        item = resp.json()
        if not item or 'score' not in item:
            return None
        return int(item.get('score', 0)), int(item.get('descendants', 0))
    except Exception as e:
        logging.warning(f"HN API stats lookup failed for {comments_url}: {e}")
        return None


def _hn_text_from_api(comments_url: str, max_comments: int = 40):
    """Post body and top comments from HN's Firebase API: (self_text, comments_text).

    Fallback for when the HTML item page is rate-limited (HTTP 419). Takes the first `max_comments`
    comments in HN ranking order (top-level first, then one reply level); dead/deleted ones are dropped.
    """
    import requests
    from bs4 import BeautifulSoup

    m = re.search(r'[?&]id=(\d+)', comments_url or '')
    if not m:
        return '', ''
    base = 'https://hacker-news.firebaseio.com/v0/item/{}.json'

    def get_item(item_id):
        try:
            resp = requests.get(base.format(item_id), timeout=10)
            resp.raise_for_status()
            return resp.json() or {}
        except Exception as e:
            logging.debug(f"HN API item {item_id} failed: {e}")
            return {}

    def clean(html):
        return BeautifulSoup(html or '', 'html.parser').get_text('\n').strip()

    try:
        story = get_item(m.group(1))
        self_text = clean(story.get('text'))
        kids = (story.get('kids') or [])[:max_comments]
        if not kids:
            return self_text, ''
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            top = list(pool.map(get_item, kids))
            replies = []
            for c in top:
                replies.extend((c.get('kids') or [])[:2])
            replies = replies[:max(0, max_comments - len(top))]
            reply_items = list(pool.map(get_item, replies)) if replies else []
        by_id = {c.get('id'): c for c in reply_items}
        lines = []
        for c in top:
            if c.get('deleted') or c.get('dead') or not c.get('text'):
                continue
            lines.append(f"{c.get('by', '?')}: {clean(c['text'])}")
            for rid in (c.get('kids') or [])[:2]:
                r = by_id.get(rid)
                if r and not (r.get('deleted') or r.get('dead')) and r.get('text'):
                    lines.append(f"  ↳ {r.get('by', '?')}: {clean(r['text'])}")
        return self_text, '\n\n'.join(lines)
    except Exception as e:
        logging.warning(f"HN API text lookup failed for {comments_url}: {e}")
        return '', ''


def _fetch_hackernews_content(topic_url: str, comments_url: str, page=None, log_prefix: str = '') -> tuple:
    """
    Fetch and process Hacker News content from both topic and comments pages.
    
    Args:
        topic_url: URL of the external topic/article
        comments_url: URL of the HN discussion page
        page: Optional Playwright page object for expanding nested replies
        
    Returns:
        Tuple of (full_text, discussion_importance_score, points, comments_count)
        - full_text: Combined content from topic and comments, or None if both fail
        - discussion_importance_score: Score based on points + comments (0-100)
        - points: Number of points/upvotes
        - comments_count: Number of comments
    """
    import requests
    from bs4 import BeautifulSoup
    
    # Fetch topic content (external link)
    topic_text = ContentProcessor.fetch_and_extract(topic_url) or ""
    
    # Fetch comments page content — prefer Playwright with nested reply expansion
    comments_text = ""
    if page is not None:
        try:
            import trafilatura
            logging.info(f"{log_prefix}Fetching HN comments via Playwright: {comments_url}")
            page.goto(comments_url, wait_until='domcontentloaded')
            page.wait_for_timeout(2000)
            # Expand collapsed comments and load more pages
            _expand_nested_replies(page, comments_url)
            comments_text = trafilatura.extract(page.content()) or ""
            if comments_text:
                logging.info(f"{log_prefix}Extracted {len(comments_text)} chars of HN comments via Playwright")
        except Exception as e:
            logging.warning(f"{log_prefix}Playwright HN comments fetch failed, falling back to ContentProcessor: {e}")
            comments_text = ContentProcessor.fetch_and_extract(comments_url) or ""
    else:
        comments_text = ContentProcessor.fetch_and_extract(comments_url) or ""
    
    # HN throttles the HTML pages (419); fall back to the API for the post body (Ask/Show HN) and comments
    if not comments_text or not topic_text:
        api_self_text, api_comments = _hn_text_from_api(comments_url)
        if not comments_text and api_comments:
            comments_text = api_comments
            logging.info(f"{log_prefix}Using {len(api_comments)} chars of HN comments from the API")
        if not topic_text and api_self_text:
            topic_text = api_self_text
            logging.info(f"{log_prefix}Using HN post body from the API ({len(api_self_text)} chars)")

    # Extract points and comments count from comments page HTML
    discussion_importance_score = 0
    points = 0
    comments_count = 0
    api_stats = _hn_stats_from_api(comments_url)
    if api_stats is not None:
        points, comments_count = api_stats
        discussion_importance_score = min(100, (points + comments_count) // 10)
        logging.info(f"{log_prefix} -> HN Stats (API): {points} points, {comments_count} comments, discussion_score: {discussion_importance_score}")
    else:
        try:
            response = requests.get(comments_url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
        
            points = 0
            comments_count = 0
            parse_error = False
        
            # Step 1: Find span.score for points (e.g., "123 points")
            score_span = soup.find('span', class_='score')
            if score_span:
                score_text = score_span.get_text()
                points_match = re.search(r'(\d+)', score_text)
                if points_match:
                    points = int(points_match.group(1))
                else:
                    logging.error(f"HN parse error: Found score span but no number in '{score_text}' - {comments_url}")
                    parse_error = True
            else:
                logging.error(f"HN parse error: Could not find span.score - {comments_url}")
                parse_error = True
        
            # Step 2: From score position, find comments count in subline links
            if score_span and not parse_error:
                subline = score_span.find_parent('span', class_='subline') or score_span.find_parent('td', class_='subtext')
                if subline:
                    links = subline.find_all('a')
                    for link in links:
                        link_text = link.get_text()
                        comments_match = re.search(r'(\d+)[\s\xa0]+comments?', link_text)
                        if comments_match:
                            comments_count = int(comments_match.group(1))
                            break
                
                    if comments_count == 0:
                        # Check if it's "discuss" (0 comments)
                        for link in links:
                            if 'discuss' in link.get_text().lower():
                                comments_count = 0
                                break
                        else:
                            logging.error(f"HN parse error: Could not find comments count in subline - {comments_url}")
                            parse_error = True
                else:
                    logging.error(f"HN parse error: Could not find subline parent of score - {comments_url}")
                    parse_error = True
        
            # Calculate discussion_importance_score: (points + comments) scaled to 0-100
            raw_discussion_score = points + comments_count
            discussion_importance_score = min(100, raw_discussion_score // 10)
        
            logging.info(f"{log_prefix} -> HN Stats: {points} points, {comments_count} comments, discussion_score: {discussion_importance_score}")
        except Exception as e:
            logging.warning(f"{log_prefix}Failed to extract HN stats from {comments_url}: {e}")
            discussion_importance_score = 0
    
    # Combine topic and comments content
    if not topic_text and not comments_text:
        return None, discussion_importance_score, points, comments_count
    
    full_text = f"=== TOPIC CONTENT ===\n{topic_text}\n\n=== HACKER NEWS DISCUSSION ===\n{comments_text}"
    return full_text, discussion_importance_score, points, comments_count

