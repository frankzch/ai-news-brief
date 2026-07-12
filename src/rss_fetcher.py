import feedparser
import logging
import httpx
import concurrent.futures
import threading
import os
import re
from datetime import datetime, timezone
from calendar import timegm

from config_loader import ConfigLoader


class RSSFetcher:
    def __init__(self, feeds_config):
        self.feeds = feeds_config
        # Load timeout from config
        config = ConfigLoader.get_instance().get('fetching', {})
        self.timeout = config.get('network_timeout', 60)
        # Lazy-initialized browser instance for YouTube
        self._browser = None
        self._browser_lock = threading.Lock()
    
    def _get_browser_unlocked(self):
        """Get or create the shared browser instance (must be called with lock held)"""
        if self._browser is None:
            from DrissionPage import ChromiumPage, ChromiumOptions
            co = ChromiumOptions()
            co.headless()
            co.set_argument('--disable-gpu')
            self._browser = ChromiumPage(co)
        return self._browser

    
    def close(self):
        """Close the browser instance if it exists"""
        if self._browser is not None:
            try:
                self._browser.quit()
            except:
                pass
            self._browser = None

    def _fetch_url(self, url, headers):
        """Fetch URL with httpx client"""
        with httpx.Client(timeout=self.timeout, trust_env=True, verify=False) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            return response.content

    def _get_youtube_channel_id(self, url):
        """Extract channel ID from YouTube RSS URL"""
        # Pattern: https://www.youtube.com/feeds/videos.xml?channel_id=UC...
        match = re.search(r'channel_id=([A-Za-z0-9_-]+)', url)
        return match.group(1) if match else None

    def _fetch_youtube_via_api(self, feed_config):
        """
        Fetch YouTube videos using YouTube Data API (PlaylistItems).
        More reliable than RSS and uses less quota than search.
        
        Returns:
            dict with 'entries' (list) and 'error' (str or None)
        """
        url = feed_config['url']
        name = feed_config['name']
        source_id = feed_config.get('id')
        
        api_key = os.environ.get('YOUTUBE_API_KEY')
        if not api_key:
            return None  # Fall back to RSS
        
        channel_id = self._get_youtube_channel_id(url)
        if not channel_id:
            logging.warning(f"Cannot extract channel ID from {url}")
            return None
        
        # Convert channel ID (UC...) to uploads playlist ID (UU...)
        # YouTube stores all uploads in a playlist with ID starting with UU
        if channel_id.startswith('UC'):
            playlist_id = 'UU' + channel_id[2:]
        else:
            playlist_id = channel_id
        
        try:
            api_url = 'https://www.googleapis.com/youtube/v3/playlistItems'
            params = {
                'part': 'snippet',
                'playlistId': playlist_id,
                'maxResults': 10,  # Fetch latest 10 videos
                'key': api_key
            }
            
            with httpx.Client(timeout=self.timeout, trust_env=True) as client:
                response = client.get(api_url, params=params)
                response.raise_for_status()
                data = response.json()
            
            entries = []
            for item in data.get('items', []):
                snippet = item.get('snippet', {})
                video_id = snippet.get('resourceId', {}).get('videoId', '')
                
                # Parse published date
                published_at_str = snippet.get('publishedAt', '')
                if published_at_str:
                    # ISO 8601 format: 2026-02-09T12:00:00Z
                    try:
                        dt = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
                        published_at = dt.isoformat()
                    except:
                        published_at = datetime.now(timezone.utc).isoformat()
                else:
                    published_at = datetime.now(timezone.utc).isoformat()
                
                entry_data = {
                    'title': snippet.get('title', ''),
                    'link': f'https://www.youtube.com/watch?v={video_id}' if video_id else '',
                    'summary': snippet.get('description', '')[:500],  # Truncate long descriptions
                    'source': name,
                    'source_id': source_id,
                    'published_at': published_at,
                    'is_youtube': True,
                    'orig_entry': item
                }
                entries.append(entry_data)
            
            logging.info(f"Fetched {len(entries)} entries from {name} via YouTube API")
            return {'entries': entries, 'error': None, 'feed_config': feed_config}
            
        except Exception as e:
            logging.warning(f"YouTube API failed for {name}: {e}")
            return None  # Fall back to RSS

    def _fetch_hf_daily_papers(self, feed_config):
        """Fetch Hugging Face curated Papers (JSON API), the *weekly* trending
        batch rather than the daily one. The base source URL points at the daily
        endpoint; we append ?week=YYYY-Www (current ISO week) so the API returns
        the whole week's trending list. Using the weekly list instead of the daily
        one keeps the collected set stable within a week (each run returns roughly
        the same papers, so dedup absorbs them) instead of piling on a fresh daily
        batch every run. Entries are flagged is_hf_papers so the pipeline skips the
        age cutoff.

        To resist HF upvote gaming, papers are re-ranked by combining their rank
        position in the upvotes list with their rank position in the GitHub-stars
        list (ranks, not raw values, so a large star count can't drown out votes) —
        both fields are supplied by the API — and only the top N are kept. Papers
        without an associated GitHub repo are dropped. Endpoint sits behind
        Cloudflare, so use curl_cffi TLS impersonation."""
        url = feed_config['url']
        name = feed_config['name']
        source_id = feed_config.get('id')
        top_n = ConfigLoader.get_instance().get('fetching', {}).get('hf_daily_top_n', 5)
        # Request the current ISO week's trending list (HF uses ISO week tags,
        # e.g. 2026-W24). The daily endpoint accepts ?week=... and returns the
        # weekly batch.
        iso = datetime.now(timezone.utc).isocalendar()
        week_tag = f"{iso[0]}-W{iso[1]:02d}"
        sep = '&' if '?' in url else '?'
        fetch_url = f"{url}{sep}week={week_tag}"
        try:
            from curl_cffi import requests as cffi_requests
            resp = cffi_requests.get(fetch_url, impersonate="chrome", timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.warning(f"HF Daily Papers fetch failed for {name}: {e}")
            return {'entries': [], 'error': str(e), 'feed_config': feed_config}

        candidates = []
        for item in data:
            paper = item.get('paper', {}) or {}
            arxiv_id = paper.get('id', '')
            github_repo = paper.get('githubRepo')
            # Ignore papers without an arxiv id or a backing GitHub project.
            if not arxiv_id or not github_repo:
                continue
            candidates.append({
                'item': item,
                'paper': paper,
                'arxiv_id': arxiv_id,
                'upvotes': paper.get('upvotes') or 0,
                'stars': paper.get('githubStars') or 0,
            })

        # Rank by position, not raw value: each paper's rank in the upvotes list
        # plus its rank in the stars list (rank = number of papers strictly above,
        # so ties share a rank). Smaller combined rank = better.
        for c in candidates:
            c['rank'] = (sum(1 for o in candidates if o['upvotes'] > c['upvotes'])
                         + sum(1 for o in candidates if o['stars'] > c['stars']))
        candidates.sort(key=lambda c: c['rank'])
        candidates = candidates[:top_n]

        entries = []
        for c in candidates:
            item, paper, arxiv_id = c['item'], c['paper'], c['arxiv_id']
            published_at_str = (item.get('publishedAt')
                                or paper.get('submittedOnDailyAt')
                                or paper.get('publishedAt') or '')
            try:
                dt = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
                published_at = dt.isoformat()
            except Exception:
                published_at = datetime.now(timezone.utc).isoformat()

            entries.append({
                'title': paper.get('title') or item.get('title') or '',
                'link': f'https://huggingface.co/papers/{arxiv_id}',
                'summary': paper.get('summary') or item.get('summary') or '',
                'source': name,
                'source_id': source_id,
                'published_at': published_at,
                'is_hf_papers': True,
                'orig_entry': item,
            })

        logging.info(f"Fetched {len(entries)} entries from {name} via HF Papers API "
                     f"(week {week_tag}, top {top_n} by upvotes+stars, github-backed only)")
        return {'entries': entries, 'error': None, 'feed_config': feed_config}

    # Domains that require TLS fingerprint impersonation to bypass WAF
    DIFFICULT_RSS_DOMAINS = [
        'marktechpost.com',
        'artificialintelligence-news.com',
        'awesomeagents.ai',
    ]

    def parse_feed(self, feed_config):
        """
        Parse a single RSS feed.
        
        Returns:
            dict with 'entries' (list) and 'error' (str or None)
        """
        url = feed_config['url']
        name = feed_config['name']
        source_id = feed_config.get('id')  # Optional source ID from database
        description = feed_config.get('description', name)
        
        # Check if this is a Hacker News feed
        is_hackernews = 'ycombinator.com' in url
        # Check if this is a Reddit feed
        is_reddit = 'reddit.com' in url
        # Check if this is the Hugging Face Daily Papers JSON API
        is_hf_papers = 'huggingface.co/api/daily_papers' in url
        # Check if this is a YouTube feed
        is_youtube = 'youtube.com' in url or 'youtu.be' in url
        # Check if this is a The Verge feed
        is_theverge = 'theverge.com' in url
        # Check if this is a Twitter/X feed
        is_twitter = 'twitter.com' in url or 'x.com' in url or 'nitter' in url or '/twitter/' in url
        # Check if this is a difficult RSS domain
        is_difficult_rss = any(domain in url for domain in self.DIFFICULT_RSS_DOMAINS)
        
        # Hugging Face Daily Papers come from a JSON API, not RSS
        if is_hf_papers:
            return self._fetch_hf_daily_papers(feed_config)

        # For YouTube, try API first (more stable than RSS)
        if is_youtube:
            api_result = self._fetch_youtube_via_api(feed_config)
            if api_result is not None:
                return api_result
            logging.info(f"Falling back to RSS for YouTube: {name}")
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': url,
            }
            
            content = None
            
            # For specific difficult domains, prioritize curl_cffi
            if is_difficult_rss:
                try:
                    from curl_cffi import requests as cffi_requests
                    logging.info(f"Using curl_cffi for difficult domain: {name}")
                    resp = cffi_requests.get(url, impersonate="chrome", timeout=self.timeout)
                    resp.raise_for_status()
                    # Relaxed XML check for these domains
                    # Check first 1000 bytes
                    prefix = resp.content[:1000]
                    if b'<?' in prefix or b'<rss' in prefix or b'<feed' in prefix or b'<html' not in prefix:
                        content = resp.content
                        logging.info(f"curl_cffi succeeded for {name}")
                    else:
                        logging.warning(f"curl_cffi returned likely non-XML for {name}: {prefix[:100]}")
                except Exception as e:
                    logging.warning(f"curl_cffi failed for difficult domain {name}: {e}")
            
            # For YouTube fallback, use browser engine to bypass TLS fingerprint detection
            if content is None and is_youtube:
                try:
                    with self._browser_lock:
                        browser = self._get_browser_unlocked()
                        browser.get(url)
                        content = browser.html.encode('utf-8')
                    logging.info(f"Using browser engine for YouTube: {name}")
                except Exception as e:
                    logging.warning(f"Browser engine failed for {name}: {e}")
            
            # Fallback to httpx/autodetect logic if content is still None
            if content is None:
                # Strategy: curl_cffi (TLS impersonation) → DrissionPage (real browser) → httpx
                # NOTE: Do NOT pass custom headers to curl_cffi - impersonate mode auto-sets
                # browser-matching headers, and custom headers would break fingerprint consistency
                try:
                    from curl_cffi import requests as cffi_requests
                    resp = cffi_requests.get(url, impersonate="chrome", timeout=self.timeout)
                    resp.raise_for_status()
                    # Verify we got actual XML, not a WAF challenge page (e.g. SiteGround captcha)
                    if b'<?' in resp.content[:100] or b'<rss' in resp.content[:500] or b'<feed' in resp.content[:500]:
                        content = resp.content
                    else:
                        logging.info(f"curl_cffi got non-XML response for {name}, trying browser engine...")
                        raise ValueError("Non-XML response from curl_cffi")
                except Exception as ce:
                    # curl_cffi failed or got non-XML, try DrissionPage browser engine
                    logging.debug(f"curl_cffi not sufficient for {name}: {ce}")
                    try:
                        with self._browser_lock:
                            browser = self._get_browser_unlocked()
                            browser.get(url)
                            # Browser renders XML in a <pre> tag; extract raw text via JS
                            raw_xml = browser.run_js(
                                'return document.querySelector("pre")?.textContent || ""'
                            )
                            if raw_xml and ('<rss' in raw_xml or '<?xml' in raw_xml or '<feed' in raw_xml):
                                content = raw_xml.encode('utf-8')
                                logging.info(f"Browser engine succeeded for {name}")
                            else:
                                raise ValueError("Browser did not return valid RSS XML")
                    except Exception as be:
                        logging.debug(f"Browser engine failed for {name}: {be}")
                        # Final fallback: plain httpx
                        content = self._fetch_url(url, headers)
            
            parsed = feedparser.parse(content)
            entries = []
            for entry in parsed.entries:
                # The Verge: only keep entries with "AI" category
                if is_theverge:
                    tags = entry.get('tags', [])
                    has_ai = any(
                        (tag.get('term', '') or '').strip().upper() == 'AI'
                        for tag in tags
                    )
                    if not has_ai:
                        continue
                # Harmonize date
                published_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
                if published_parsed:
                    dt = datetime.fromtimestamp(timegm(published_parsed), tz=timezone.utc)
                    published_at = dt.isoformat()
                else:
                    published_at = datetime.now(timezone.utc).isoformat()

                # Try to get the most detailed content from RSS
                content_val = ''
                if 'content' in entry and len(entry.content) > 0:
                    content_val = entry.content[0].value
                
                summary = content_val or entry.get('summary', '') or entry.get('description', '')

                entry_data = {
                    'title': entry.get('title', ''),
                    'link': entry.get('link', ''),
                    'summary': summary,
                    'source': name,
                    'source_id': source_id,  # Include source ID
                    'published_at': published_at,
                    'orig_entry': entry # Keep raw just in case
                }
                
                # For Hacker News, extract comments URL from the comments tag
                if is_hackernews:
                    entry_data['is_hackernews'] = True
                    # feedparser stores <comments> tag in entry.comments
                    comments_url = entry.get('comments', '')
                    entry_data['comments_url'] = comments_url
                
                # For Reddit, mark as Reddit source and extract updated time
                if is_reddit:
                    entry_data['is_reddit'] = True
                    updated_parsed = entry.get('updated_parsed')
                    if updated_parsed:
                        entry_data['updated_at'] = datetime.fromtimestamp(timegm(updated_parsed), tz=timezone.utc).isoformat()
                
                # For Twitter, mark as Twitter source if feed matches or link matches
                is_twitter_entry = is_twitter or 'twitter.com' in entry_data['link'] or 'x.com' in entry_data['link']
                if is_twitter_entry:
                    entry_data['is_twitter'] = True
                
                # For YouTube, mark as YouTube source
                if is_youtube:
                    entry_data['is_youtube'] = True
                
                entries.append(entry_data)
            logging.info(f"Fetched {len(entries)} entries from {name}")
            return {'entries': entries, 'error': None, 'feed_config': feed_config}
        except Exception as e:
            # Extract error type for clearer reporting
            error_str = str(e)
            if '404' in error_str:
                error_type = 'rss_404_not_found'
            elif '403' in error_str:
                error_type = 'rss_403_forbidden'
            elif '401' in error_str:
                error_type = 'rss_401_unauthorized'
            elif 'timeout' in error_str.lower():
                error_type = 'rss_timeout'
            elif 'ssl' in error_str.lower() or 'certificate' in error_str.lower():
                error_type = 'rss_ssl_error'
            else:
                error_type = 'rss_fetch_error'
            
            logging.error(f"Error fetching feed {name} ({url}): {e}")
            return {
                'entries': [], 
                'error': error_type, 
                'error_detail': error_str,
                'feed_config': feed_config
            }

    def fetch_all(self):
        """
        Fetch all configured feeds.
        
        Returns:
            tuple: (all_entries, failed_feeds)
            - all_entries: list of entry dicts
            - failed_feeds: list of dicts with 'url', 'name', 'description', 'error', 'error_detail'
        """
        all_entries = []
        failed_feeds = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                future_to_feed = {executor.submit(self.parse_feed, feed): feed for feed in self.feeds}
                for future in concurrent.futures.as_completed(future_to_feed):
                    feed = future_to_feed[future]
                    try:
                        result = future.result()
                        if result.get('error'):
                            # Feed fetch failed, record for statistics
                            failed_feeds.append({
                                'url': feed['url'],
                                'name': feed['name'],
                                'description': feed.get('description', feed['name']),
                                'error': result['error'],
                                'error_detail': result.get('error_detail', '')
                            })
                        else:
                            all_entries.extend(result['entries'])
                    except Exception as exc:
                        logging.error(f"Feed fetch generated an exception: {exc}")
                        failed_feeds.append({
                            'url': feed['url'],
                            'name': feed['name'],
                            'description': feed.get('description', feed['name']),
                            'error': 'rss_exception',
                            'error_detail': str(exc)
                        })
        finally:
            self.close()  # Clean up browser after all fetches
        return all_entries, failed_feeds
