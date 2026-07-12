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
    
    # Extract points and comments count from comments page HTML
    discussion_importance_score = 0
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

