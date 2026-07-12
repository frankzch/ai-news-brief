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


class LoginRequiredError(Exception):
    """Raised when a browser task triggers a login wall in headless mode."""
    pass

def _get_chrome_version() -> int:
    """
    Auto-detect installed Chrome major version.
    Returns major version number (e.g., 144) or None if detection fails.
    """
    import subprocess
    import winreg
    
    # Method 1: Try Windows Registry
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
        version, _ = winreg.QueryValueEx(key, "version")
        winreg.CloseKey(key)
        major = int(version.split('.')[0])
        logging.debug(f"Detected Chrome version from registry: {major}")
        return major
    except Exception:
        pass
    
    # Method 2: Try running chrome --version
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in chrome_paths:
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # Output: "Google Chrome 144.0.7559.133"
                version_str = result.stdout.strip()
                match = re.search(r'(\d+)\.', version_str)
                if match:
                    major = int(match.group(1))
                    logging.debug(f"Detected Chrome version from executable: {major}")
                    return major
        except Exception:
            continue
    
    logging.warning("Could not detect Chrome version, will let undetected-chromedriver auto-detect")
    return None

def _create_browser(target_channel: str = None, force_visible: bool = False, profile_name: str = None):
    """
    Create a Playwright browser context and page for browser-based scraping (Reddit, YouTube, etc.).
    Returns (playwright_instance, context, page) tuple.
    Caller is responsible for closing context and stopping playwright.

    Args:
        target_channel: Browser channel to use ('chrome', 'msedge', etc.)
        force_visible: If True, override headless config and show browser window
        profile_name: If provided, use `data/playwright_profile/<profile_name>` as user_data_dir so
            multiple browser instances can run in parallel without conflicting on Chrome's
            profile-dir lock. If None, use the shared `data/playwright_profile` directory (legacy).
    """
    from playwright.sync_api import sync_playwright

    base_profile_dir = os.path.join(os.path.dirname(__file__), 'data', 'playwright_profile')
    if profile_name:
        playwright_profile_dir = os.path.join(base_profile_dir, profile_name)
    else:
        playwright_profile_dir = base_profile_dir
    os.makedirs(playwright_profile_dir, exist_ok=True)
    
    p = sync_playwright().start()
    
    # Determine channel
    channel = target_channel
    if not channel:
        # Auto-detect: prefer chrome, fallback to msedge
        channel = 'chrome'
        # Simple check if we are on a system that might only have edge (e.g. cloud windows)
        # But for persistent context we usually just try one.
        # Let's try to launch. If it fails, we might need to catch exception in caller or here.
        # Since launch_persistent_context doesn't easily allow "try-catch-launch-another" 
        # without potentially messing up the data dir lock, we'll iterate locally if needed,
        # but simpler is to try the requested one or default to chrome.
        # However, to be robust for the user's "cloud has only edge" case:
        
    # We will try a robust approach: try Chrome, if missing executable, try Edge.
    # But launch_persistent_context locks the dir. 
    # So we define a helper to launch.
    
    context = None
    channels_to_try = [target_channel] if target_channel else ['chrome', 'msedge']
    
    launch_error = None
    display_browser = False
    
    # Always show browser window for easier debugging
    is_headless = False
    logging.info(f"Browser Headless Mode: {is_headless}")

    for ch in channels_to_try:
        try:
            logging.info(f"Attempting to launch browser with channel: {ch}")
            context = p.chromium.launch_persistent_context(
                user_data_dir=playwright_profile_dir,
                channel=ch,
                headless=is_headless, # Use config setting

                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--lang=en-US',
                ]
            )
            logging.info(f"Successfully launched browser (channel: {ch})")
            break
        except Exception as e:
            logging.warning(f"Failed to launch browser with channel '{ch}': {e}")
            launch_error = e
    
    if not context:
        logging.error("Could not launch any browser.")
        p.stop()
        raise launch_error or Exception("Browser launch failed")

    # Inject Cookies if available
    cookies_file = os.path.join(os.path.dirname(__file__), 'data', 'cookies.json')
    cookies_env = os.environ.get('REDDIT_COOKIES')
    
    cookies_to_add = []
    
    if os.path.exists(cookies_file):
        try:
            with open(cookies_file, 'r') as f:
                file_cookies = json.load(f)
                cookies_to_add.extend(file_cookies)
            logging.info(f"Loaded {len(file_cookies)} cookies from {cookies_file}")
        except Exception as e:
            logging.warning(f"Failed to load cookies from file: {e}")
            
    if cookies_env:
        try:
            env_cookies = json.loads(cookies_env)
            cookies_to_add.extend(env_cookies)
            logging.info(f"Loaded {len(env_cookies)} cookies from environment variable")
        except Exception as e:
            logging.warning(f"Failed to parse cookies from environment variable: {e}")

    if cookies_to_add:
        try:
            context.add_cookies(cookies_to_add)
            logging.info(f"Injected {len(cookies_to_add)} cookies into browser context")
        except Exception as e:
            logging.warning(f"Failed to inject cookies: {e}")

    # Rezuse valid page
    if context.pages:
        page = context.pages[0]
    else:
        page = context.new_page()
    page.set_default_timeout(60000)
    
    return p, context, page

def _close_browser(playwright_instance, context):
    """Close browser context and stop Playwright."""
    try:
        context.close()
    except Exception:
        pass
    try:
        playwright_instance.stop()
    except Exception:
        pass
    logging.info("Playwright Chrome browser closed")

def _check_logins_needed(context, needs_reddit: bool, needs_youtube: bool, needs_twitter: bool = False) -> bool:
    """Quick check if any required logins are missing. Returns True if login is needed."""
    if needs_reddit:
        reddit_cookies = [
            c for c in context.cookies()
            if '.reddit.com' in c.get('domain', '')
            and c['name'] in ('reddit_session', 'token_v2')
        ]
        if not reddit_cookies:
            return True
    
    if needs_youtube:
        google_cookies = [
            c for c in context.cookies()
            if '.google.com' in c.get('domain', '')
            and c['name'] in ('SID', 'SSID', 'HSID', '__Secure-1PSID')
        ]
        if not google_cookies:
            return True
    
    if needs_twitter:
        twitter_cookies = [
            c for c in context.cookies()
            if ('.x.com' in c.get('domain', '') or '.twitter.com' in c.get('domain', ''))
            and c['name'] in ('auth_token', 'ct0')
        ]
        if not twitter_cookies:
            return True
    
    return False

def _ensure_browser_logins(context, page, needs_reddit: bool, needs_youtube: bool, needs_twitter: bool = False):
    """
    Check if browser has valid login cookies for Reddit/YouTube/X.com.
    If not, navigate to the site and wait for manual login.
    
    Args:
        context: Playwright browser context
        page: Playwright page object
        needs_reddit: Whether Reddit login is needed
        needs_youtube: Whether YouTube/Google login is needed
        needs_twitter: Whether X.com login is needed
    """
    LOGIN_TIMEOUT = 300000  # 5 minutes
    
    # --- Reddit Login Check ---
    if needs_reddit:
        reddit_cookies = [
            c for c in context.cookies()
            if '.reddit.com' in c.get('domain', '')
            and c['name'] in ('reddit_session', 'token_v2')
        ]
        if reddit_cookies:
            logging.info(f"Reddit login detected ({len(reddit_cookies)} session cookies found)")
        else:
            logging.warning("No Reddit login cookies found! Navigating to Reddit for manual login...")
            try:
                page.goto("https://www.reddit.com/login", wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                
                # Check if already logged in (redirected away from login page)
                if '/login' not in page.url:
                    logging.info("Reddit already logged in (redirected from login page)")
                else:
                    logging.warning(f"Please login to Reddit in the browser window. Waiting up to {LOGIN_TIMEOUT // 1000}s...")
                    # Wait until URL changes away from login page (successful login)
                    try:
                        page.wait_for_url(lambda url: '/login' not in url, timeout=LOGIN_TIMEOUT)
                        page.wait_for_timeout(3000)  # Let cookies settle
                        logging.info("Reddit login successful!")
                    except Exception:
                        logging.error("Reddit login timeout! Reddit content may fail to fetch.")
            except Exception as e:
                logging.error(f"Reddit login check failed: {e}")
    
    # --- YouTube/Google Login Check ---
    if needs_youtube:
        google_cookies = [
            c for c in context.cookies()
            if '.google.com' in c.get('domain', '')
            and c['name'] in ('SID', 'SSID', 'HSID', '__Secure-1PSID')
        ]
        if google_cookies:
            logging.info(f"Google/YouTube login detected ({len(google_cookies)} session cookies found)")
        else:
            logging.warning("No Google/YouTube login cookies found! Navigating to YouTube for manual login...")
            try:
                page.goto("https://accounts.google.com/ServiceLogin?continue=https://www.youtube.com/", wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                
                # Check if already logged in (redirected to YouTube)
                if 'youtube.com' in page.url:
                    logging.info("Google/YouTube already logged in (redirected to YouTube)")
                else:
                    logging.warning(f"Please login to Google in the browser window. Waiting up to {LOGIN_TIMEOUT // 1000}s...")
                    try:
                        # Wait until redirected to YouTube after login
                        page.wait_for_url(lambda url: 'youtube.com' in url, timeout=LOGIN_TIMEOUT)
                        page.wait_for_timeout(3000)  # Let cookies settle
                        logging.info("Google/YouTube login successful!")
                    except Exception:
                        logging.error("Google/YouTube login timeout! YouTube transcripts may fail to fetch.")
            except Exception as e:
                logging.error(f"YouTube login check failed: {e}")
    
    # --- Twitter/X.com Login Check ---
    if needs_twitter:
        twitter_cookies = [
            c for c in context.cookies()
            if ('.x.com' in c.get('domain', '') or '.twitter.com' in c.get('domain', ''))
            and c['name'] in ('auth_token', 'ct0')
        ]
        if twitter_cookies:
            logging.info(f"X.com login detected ({len(twitter_cookies)} session cookies)")
        else:
            logging.warning("No X.com login cookies found! Navigating to X.com for manual login...")
            try:
                page.goto("https://x.com/login", wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                
                # Check if already logged in (redirected away from login page)
                if '/login' not in page.url and '/i/flow/login' not in page.url:
                    logging.info("X.com already logged in (redirected from login page)")
                else:
                    logging.warning(f"Please login to X.com in the browser window. Waiting up to {LOGIN_TIMEOUT // 1000}s...")
                    try:
                        page.wait_for_url(
                            lambda url: '/login' not in url and '/i/flow/login' not in url,
                            timeout=LOGIN_TIMEOUT
                        )
                        page.wait_for_timeout(3000)  # Let cookies settle
                        logging.info("X.com login successful!")
                    except Exception:
                        logging.error("X.com login timeout! Twitter content may fail to fetch.")
            except Exception as e:
                logging.error(f"X.com login check failed: {e}")

def _expand_nested_replies(page, url, max_clicks=30):
    """Expand collapsed/nested reply threads on social media platforms.
    Clicks 'show more replies' buttons to reveal hidden comment content.
    Returns total number of expand actions performed."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    total_clicked = 0
    max_passes = 3

    for pass_num in range(max_passes):
        clicked = 0
        try:
            if 'reddit.com' in domain:
                # Reddit: "X more replies" / "另外 X 条回复" / "more comments"
                btns = page.locator('button').filter(
                    has_text=re.compile(r'more repl|条回复|more comment|更多评论', re.IGNORECASE)
                ).all()
                for btn in btns:
                    if total_clicked >= max_clicks:
                        break
                    try:
                        if btn.is_visible():
                            btn.scroll_into_view_if_needed(timeout=2000)
                            btn.click(timeout=3000)
                            clicked += 1
                            total_clicked += 1
                            page.wait_for_timeout(800)
                    except Exception:
                        pass

            elif 'x.com' in domain or 'twitter.com' in domain:
                # X/Twitter: "Show replies" / "显示回复" / "Show this thread"
                for pattern in [r'Show\s*replies', r'显示回复',
                                r'Show\s*more\s*replies', r'显示更多回复',
                                r'Show\s*this\s*thread', r'显示此对话']:
                    if total_clicked >= max_clicks:
                        break
                    try:
                        elements = page.get_by_text(re.compile(pattern, re.IGNORECASE)).all()
                    except Exception:
                        continue
                    for el in elements:
                        if total_clicked >= max_clicks:
                            break
                        try:
                            if el.is_visible():
                                el.scroll_into_view_if_needed(timeout=2000)
                                el.click(timeout=3000)
                                clicked += 1
                                total_clicked += 1
                                page.wait_for_timeout(1000)
                        except Exception:
                            pass

            elif 'news.ycombinator.com' in domain:
                # HN: collapsed comments show [+N] in toggle link
                toggles = page.locator('a.togg').all()
                for toggle in toggles:
                    if total_clicked >= max_clicks:
                        break
                    try:
                        text = toggle.inner_text(timeout=500).strip()
                        if toggle.is_visible() and text.startswith('[+'):
                            toggle.click(timeout=2000)
                            clicked += 1
                            total_clicked += 1
                            page.wait_for_timeout(300)
                    except Exception:
                        pass
                # "More" pagination link
                more_links = page.locator('a.morelink').all()
                for link in more_links:
                    if total_clicked >= max_clicks:
                        break
                    try:
                        if link.is_visible():
                            link.click(timeout=3000)
                            clicked += 1
                            total_clicked += 1
                            page.wait_for_timeout(2000)
                    except Exception:
                        pass

        except Exception as e:
            logging.debug(f"[SocialMedia] expand pass {pass_num+1} error: {e}")

        if clicked == 0:
            break
        page.wait_for_timeout(1500)
        logging.info(f"[SocialMedia] Expand pass {pass_num+1}: clicked {clicked} buttons")

    if total_clicked > 0:
        logging.info(f"[SocialMedia] Expanded {total_clicked} nested reply sections total")
    return total_clicked

def _is_playwright_domain(url: str, fetching_config: dict) -> bool:
    """Check if the URL belongs to a domain configured for Playwright fetching."""
    playwright_domains = fetching_config.get('playwright_domains', [])
    if not playwright_domains:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in playwright_domains)

def _fetch_article_via_playwright(url: str, page, log_prefix: str = '') -> str:
    """
    Fetch an article using a Playwright browser page and extract its content via trafilatura.
    This is useful for sites that block standard HTTP requests (403 errors).
    """
    import trafilatura
    logging.info(f"{log_prefix}Fetching article via Playwright: {url}")
    try:
        # Navigate to the URL and wait for DOM to be ready
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        # Give some time for anti-bot scripts and dynamic content to execute
        page.wait_for_timeout(3000)
        
        # Get the full rendered HTML
        html_content = page.content()
        
        # Extract main text using trafilatura
        text = trafilatura.extract(html_content)
        if text:
            return text
        
        # Fallback to basic innerText if trafilatura fails
        logging.warning(f"{log_prefix}trafilatura failed to extract from Playwright HTML for {url}, falling back to innerText.")
        fallback_text = page.evaluate('() => document.body ? document.body.innerText : ""')
        return fallback_text.strip() if fallback_text else None

    except Exception as e:
        logging.error(f"{log_prefix}Failed to fetch article via Playwright: {url}, error: {e}")
        return None

