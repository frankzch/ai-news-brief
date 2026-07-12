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
from browser_utils import _create_browser, _close_browser


def _extract_youtube_video_id(url: str) -> str:
    """
    Extract YouTube video ID from various URL formats.
    
    Supported formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://www.youtube.com/v/VIDEO_ID
    
    Returns:
        Video ID string, or None if not found
    """
    import re
    
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def _fetch_youtube_content(url: str, page=None, browser_context=None, log_prefix: str = '') -> tuple:
    """
    Fetch YouTube video transcript by intercepting the player's own timedtext
    API requests via Playwright. This approach reuses the real browser session,
    making it resilient to YouTube's anti-bot measures.
    
    Args:
        url: URL of the YouTube video
        page: Optional Playwright page object to reuse
        browser_context: Optional Playwright browser context (unused, kept for signature compat)
        
    Returns:
        Tuple of (transcript_text, video_id)
    """
    # Extract video ID
    video_id = _extract_youtube_video_id(url)
    if not video_id:
        logging.warning(f"{log_prefix}Could not extract video ID from URL: {url}")
        return None, None

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    transcript_text = _fetch_youtube_via_playwright(video_id, canonical_url, page, log_prefix=log_prefix)
    if transcript_text:
        return transcript_text, video_id
    
    return None, video_id

def _skip_youtube_ads(page, video_id: str, max_wait: int = 30):
    """
    Detect and skip YouTube pre-roll ads before capturing captions.
    Tries clicking the 'Skip Ad' button; otherwise waits for the ad to finish.
    
    Args:
        page: Playwright page object
        video_id: Video ID for logging
        max_wait: Maximum seconds to wait for ad to end
    """
    import time
    
    start = time.time()
    while time.time() - start < max_wait:
        try:
            is_ad = page.evaluate('''() => {
                const player = document.querySelector('#movie_player');
                return player ? player.classList.contains('ad-showing') : false;
            }''')
        except Exception:
            break
        
        if not is_ad:
            break
        
        logging.info(f"YouTube {video_id}: pre-roll ad detected, attempting to skip...")
        
        # Try clicking skip button (multiple selectors for different YouTube versions)
        skipped = False
        skip_selectors = [
            'button.ytp-skip-ad-button',
            'button.ytp-ad-skip-button',
            'button.ytp-ad-skip-button-modern',
            '.ytp-ad-skip-button-container button',
            'button[id^="skip-button"]',
        ]
        for sel in skip_selectors:
            try:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(1000)
                    skipped = True
                    logging.info(f"YouTube {video_id}: skip-ad button clicked ({sel})")
                    break
            except Exception:
                continue
        
        if skipped:
            break
        
        # No skip button yet — wait a bit and retry
        page.wait_for_timeout(1000)
    
    # Final check — if still showing ad after max_wait, log warning
    try:
        still_ad = page.evaluate('''() => {
            const player = document.querySelector('#movie_player');
            return player ? player.classList.contains('ad-showing') : false;
        }''')
        if still_ad:
            logging.warning(f"YouTube {video_id}: ad still showing after {max_wait}s, proceeding anyway")
        else:
            logging.debug(f"YouTube {video_id}: no ad (or ad skipped), proceeding to capture captions")
    except Exception:
        pass

def _fetch_youtube_via_playwright(video_id: str, url: str, page=None, log_prefix: str = '') -> str:
    """
    Fetch YouTube transcript via Playwright by intercepting the player's own
    timedtext API requests. This works even when direct fetch/XHR returns empty,
    because YouTube's own player successfully loads captions through the same API.
    """
    import xml.etree.ElementTree as ET
    import threading
    
    temp_playwright = None
    temp_context = None
    if page is None:
        temp_playwright, temp_context, page = _create_browser()
    
    captured_captions = []
    capture_lock = threading.Lock()
    
    def on_response(response):
        """Intercept timedtext API responses from YouTube's player."""
        resp_url = response.url
        if 'api/timedtext' not in resp_url or response.status != 200:
            return
        try:
            body = response.text()
            if body and len(body) > 10:
                lang = 'unknown'
                if 'lang=en' in resp_url:
                    lang = 'en'
                elif 'tlang=en' in resp_url:
                    lang = 'en-translated'
                with capture_lock:
                    captured_captions.append({'lang': lang, 'body': body})
                logging.debug(f"YouTube {video_id}: intercepted timedtext response, lang={lang}, len={len(body)}")
        except Exception:
            pass
    
    try:
        logging.info(f"{log_prefix}Fetching YouTube via Playwright (intercept): {url}")
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        
        # Skip pre-roll ads BEFORE registering caption interceptor
        _skip_youtube_ads(page, video_id)
        
        # Now register the interceptor — only captures real video captions
        page.on('response', on_response)
        
        # Reload or seek to trigger fresh caption load for the actual video
        # Use player API to seek to start, which forces caption re-fetch
        try:
            page.evaluate('''() => {
                const player = document.querySelector('#movie_player');
                if (player && player.seekTo) {
                    player.seekTo(0, true);
                }
            }''')
        except Exception:
            pass
        page.wait_for_timeout(3000)
        
        # If no captions captured, try clicking CC button
        if not captured_captions:
            try:
                cc_button = page.locator('button.ytp-subtitles-button')
                if cc_button.count() > 0:
                    cc_button.click()
                    page.wait_for_timeout(3000)
                    logging.debug(f"YouTube {video_id}: CC button clicked, captured={len(captured_captions)}")
            except Exception:
                pass
        
        # If still no captions, try triggering via player JS API
        if not captured_captions:
            try:
                page.evaluate('''() => {
                    const player = document.querySelector('#movie_player');
                    if (player && player.setOption) {
                        player.setOption('captions', 'track', {'languageCode': 'en'});
                    }
                }''')
                page.wait_for_timeout(3000)
            except Exception:
                pass
        
        if not captured_captions:
            logging.warning(f"{log_prefix}YouTube video {video_id}: no timedtext responses intercepted")
            return None
        
        # Pick the best caption (prefer English)
        best = None
        for cap in captured_captions:
            if cap['lang'] == 'en':
                best = cap
                break
        if not best:
            best = captured_captions[0]
        
        # Parse caption content (supports both XML/srv1 and json3 formats)
        transcript_text = _parse_caption_content(best['body'])
        
        if transcript_text:
            logging.info(f"{log_prefix} -> YouTube transcript via Playwright (intercept): {len(transcript_text)} chars, video_id: {video_id}")
            return transcript_text

        logging.info(f"{log_prefix}YouTube video {video_id}: intercepted caption parsed but empty")
        return None

    except Exception as e:
        logging.warning(f"{log_prefix}YouTube Playwright intercept failed: {e}")
        return None
    finally:
        try:
            page.remove_listener('response', on_response)
        except Exception:
            pass
        if temp_playwright:
            _close_browser(temp_playwright, temp_context)

def _parse_caption_content(body: str) -> str:
    """Parse YouTube caption content in either XML (srv1) or json3 format."""
    import xml.etree.ElementTree as ET
    
    body_stripped = body.strip()
    
    # Try XML format first (srv1)
    if body_stripped.startswith('<?xml') or body_stripped.startswith('<timedtext') or body_stripped.startswith('<transcript'):
        try:
            root = ET.fromstring(body_stripped)
            texts = []
            for elem in root.iter('text'):
                t = elem.text
                if t:
                    t = t.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'").replace('&quot;', '"')
                    texts.append(t.strip())
            return ' '.join(texts)
        except ET.ParseError:
            pass
    
    # Try json3 format
    if body_stripped.startswith('{'):
        try:
            data = json.loads(body_stripped)
            events = data.get('events', [])
            texts = []
            for event in events:
                segs = event.get('segs', [])
                for seg in segs:
                    t = seg.get('utf8', '')
                    if t and t.strip() and t != '\n':
                        texts.append(t.strip())
            return ' '.join(texts)
        except (json.JSONDecodeError, KeyError):
            pass
    
    return None

