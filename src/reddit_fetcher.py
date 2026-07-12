import logging
import sys
import os
import re
import json
import time
import random
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
from browser_utils import _create_browser, _close_browser, LoginRequiredError, _expand_nested_replies


def _fetch_reddit_content(url: str, page=None, is_interactive=False, log_prefix: str = '') -> tuple:
    """
    Fetch and process Reddit content using Playwright with real Chrome browser.
    
    If a page object is provided, it will be reused (navigated to the new URL).
    Otherwise, a temporary browser session is created and closed after use.
    
    Args:
        url: URL of the Reddit post
        page: Optional Playwright page object to reuse
        
    Returns:
        Tuple of (full_text, discussion_importance_score, upvotes, comments_count)
    """
    from bs4 import BeautifulSoup
    import trafilatura
    
    upvotes = 0
    comments_count = 0
    discussion_importance_score = 0
    full_text = None
    
    # 如果没有传入 page，临时创建一个
    temp_playwright = None
    temp_context = None
    if page is None:
        temp_playwright, temp_context, page = _create_browser()
    
    try:
        logging.info(f"{log_prefix}Fetching Reddit via Playwright: {url}")
        
        # 访问 URL（复用已有页面）
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        
        # 检测是否被拦截（未登录）— 帖子元素未出现即视为 blocked，
        # 不再依赖脆弱的关键词 grep（Reddit 登录墙文案多变）。
        post_loaded = page.locator('shreddit-post, [data-testid="post-container"]').count() > 0
        is_blocked = not post_loaded
        if post_loaded:
            logging.debug("Reddit post element found, page loaded successfully")
        
        if is_blocked:
            if not is_interactive:
                logging.warning(f"{log_prefix}Reddit requires login (blocked). requesting interactive mode...")
                raise LoginRequiredError("Reddit login check required")

            logging.warning(f"{log_prefix}Reddit requires login! Please login manually in the browser window...")
            logging.warning(f"{log_prefix}After login, the page will automatically continue. Waiting up to 120 seconds...")
            try:
                page.wait_for_selector('shreddit-post, [data-testid="post-container"]', timeout=120000)
                logging.info(f"{log_prefix}Login detected! Continuing...")
                page.wait_for_timeout(2000)
            except Exception:
                logging.error(f"{log_prefix}Login timeout. Please try again.")
                return None, 0, 0, 0
        
        # 展开嵌套回复（"more replies" 等折叠按钮）
        _expand_nested_replies(page, url)
        
        # 获取页面 HTML
        page_source = page.content()
        full_text = trafilatura.extract(page_source)
        
        # 解析统计数据
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # Method 1: shreddit-post element (new Reddit)
        post_elem = soup.find('shreddit-post')
        if post_elem:
            score_str = post_elem.get('score', '0')
            try:
                upvotes = int(score_str)
            except ValueError:
                pass
            comments_str = post_elem.get('comment-count', '0')
            try:
                comments_count = int(comments_str)
            except ValueError:
                pass
        
        # Method 2: Look for score in other elements
        if upvotes == 0:
            vote_elems = soup.find_all(attrs={'data-click-id': 'upvote'})
            for elem in vote_elems:
                parent = elem.find_parent()
                if parent:
                    score_text = parent.get_text()
                    score_match = re.search(r'(\d+(?:\.\d+)?[kKmM]?)\s*(?:points?|upvotes?)?', score_text)
                    if score_match:
                        score_str = score_match.group(1).lower()
                        if 'k' in score_str:
                            upvotes = int(float(score_str.replace('k', '')) * 1000)
                        elif 'm' in score_str:
                            upvotes = int(float(score_str.replace('m', '')) * 1000000)
                        else:
                            upvotes = int(float(score_str))
                        break
        
        # Method 3: Find comments count from comments link
        if comments_count == 0:
            comments_link = soup.find('a', string=re.compile(r'\d+\s*comments?', re.I))
            if comments_link:
                match = re.search(r'(\d+)', comments_link.get_text())
                if match:
                    comments_count = int(match.group(1))
        
        # Calculate discussion score
        raw_discussion_score = upvotes + comments_count
        discussion_importance_score = min(100, raw_discussion_score // 10)
        
        logging.info(f"{log_prefix} -> Reddit Stats (via Playwright): {upvotes} upvotes, {comments_count} comments, discussion_score: {discussion_importance_score}")

    except LoginRequiredError:
        # 必须向上抛，由 pipeline_runner 切换可见浏览器并等待用户登录；
        # 之前被下面的通用 Exception 吞掉，导致后台继续撞墙。
        if temp_playwright:
            _close_browser(temp_playwright, temp_context)
            temp_playwright = None
        raise
    except Exception as e:
        logging.warning(f"{log_prefix}Failed to fetch Reddit content via Playwright: {e}")
    finally:
        # 仅在临时创建的情况下关闭浏览器
        if temp_playwright:
            _close_browser(temp_playwright, temp_context)
    
    if not full_text:
        return None, discussion_importance_score, upvotes, comments_count
    
    return full_text, discussion_importance_score, upvotes, comments_count

def _human_scroll_feed(page):
    """像人一样分段慢滚约 0.6-0.85 屏（段间停顿 + 偶尔多看两眼），而不是一次跳到底部——
    跳底部既不像真人、也更容易触发风控。仅用于 promo 拟人浏览。"""
    try:
        viewport_h = page.evaluate("window.innerHeight") or 800
    except Exception:
        page.wait_for_timeout(2000)
        return
    total = int(viewport_h * random.uniform(0.6, 0.85))
    steps = random.randint(7, 12)
    scrolled = 0
    for i in range(steps):
        remaining = steps - i
        step = int((total - scrolled) / remaining * random.uniform(0.7, 1.3))
        step = max(40, min(step, total - scrolled))
        try:
            page.mouse.wheel(0, step)
        except Exception:
            try:
                page.evaluate(f"window.scrollBy(0, {step})")
            except Exception:
                return
        scrolled += step
        page.wait_for_timeout(random.randint(350, 800))
        if random.random() < 0.15:
            page.wait_for_timeout(random.randint(700, 1600))
    page.wait_for_timeout(random.randint(1200, 2800))


def _scrape_reddit_search(page, keyword: str, log_prefix: str,
                            min_upvotes: int, min_comments: int,
                            max_posts: int = 10, source_id=None,
                            source_name: str = 'Reddit Search',
                            user_db=None, is_interactive: bool = False,
                            human_scroll: bool = False) -> list:
    """
    在 Reddit 搜索结果页直接滚动抓取，从卡片上读 score / comment-count，
    避免对每条结果都开详情页。模仿 _scrape_twitter_feed 的滚动停止策略：
    通过 URL 去重 + 阈值过滤后的有效帖子数 >= max_posts 即停。

    Returns:
        List of entry dicts with: title, link, summary='', source, source_id,
        published_at, is_reddit=True, reddit_upvotes, reddit_comments, _pre_filtered=True
    """
    import urllib.parse as _up

    target_url = f'https://www.reddit.com/search/?q={_up.quote(keyword)}&type=posts&sort=top&t=year'
    logging.info(f"{log_prefix}Reddit search target: {target_url}")

    try:
        page.goto(target_url, wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
    except Exception as e:
        logging.error(f"{log_prefix}Failed to load Reddit search page: {e}")
        return []

    # 登录拦截检测：新搜索页用 [data-testid="search-post-unit"] 卡片，不再是 shreddit-post
    try:
        post_loaded = page.locator('[data-testid="search-post-unit"]').count() > 0
    except Exception:
        post_loaded = False
    if not post_loaded:
        # 卡片未出现即视为登录墙/拦截，不再依赖关键词 grep。
        if not is_interactive:
            logging.warning(f"{log_prefix}Reddit search blocked (no result cards), requesting interactive mode...")
            raise LoginRequiredError("Reddit search login required")
        logging.warning(f"{log_prefix}Reddit search requires login, waiting up to 120s for manual login...")
        try:
            page.wait_for_selector('[data-testid="search-post-unit"]', timeout=120000)
        except Exception:
            logging.error(f"{log_prefix}Login timeout on Reddit search.")
            return []

    collected = {}  # url -> {upvotes, comments, title, created}
    max_scrolls = 20
    no_new_count = 0

    for scroll_i in range(max_scrolls):
        try:
            # 新版搜索结果 DOM:
            #   [data-testid="search-post-unit"] 容器内
            #     a[data-testid="post-title"] href + aria-label
            #     2 个 <faceplate-number number="..."> 顺序为 [score, comments]
            #     <faceplate-timeago ts="..."> 帖子创建时间
            posts = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('[data-testid="search-post-unit"]').forEach(unit => {
                    const a = unit.querySelector('a[data-testid="post-title"]');
                    const permalink = a ? (a.getAttribute('href') || '') : '';
                    if (!permalink) return;
                    let title = a.getAttribute('aria-label') || '';
                    if (!title) {
                        const t = unit.querySelector('[data-testid="post-title-text"]');
                        title = t ? (t.innerText || '').trim() : '';
                    }
                    const nums = unit.querySelectorAll('faceplate-number');
                    let score = 0, comments = 0;
                    if (nums.length >= 1) score = parseInt(nums[0].getAttribute('number') || '0', 10) || 0;
                    if (nums.length >= 2) comments = parseInt(nums[1].getAttribute('number') || '0', 10) || 0;
                    const ago = unit.querySelector('faceplate-timeago');
                    const created = ago ? (ago.getAttribute('ts') || '') : '';
                    results.push({permalink, score, comments, title, created});
                });
                return results;
            }''')
        except Exception as e:
            logging.warning(f"{log_prefix}Reddit search extract error on scroll {scroll_i}: {e}")
            break

        prev_count = len(collected)
        for p in posts:
            permalink = p.get('permalink') or ''
            if not permalink:
                continue
            full_url = f'https://www.reddit.com{permalink}' if permalink.startswith('/') else permalink
            if full_url not in collected:
                collected[full_url] = {
                    'upvotes': p.get('score', 0),
                    'comments': p.get('comments', 0),
                    'title': p.get('title', ''),
                    'created': p.get('created', '')
                }
        new_count = len(collected) - prev_count

        if new_count == 0:
            no_new_count += 1
            if no_new_count >= 2:
                logging.info(f"{log_prefix}Reddit search: no new results for 2 scrolls, stopping (total scanned={len(collected)})")
                break
        else:
            no_new_count = 0

        qualified = sum(
            1 for url, d in collected.items()
            if d['upvotes'] >= min_upvotes and d['comments'] >= min_comments
            and (user_db is None or not user_db.article_url_exists(url))
        )
        if qualified >= max_posts:
            logging.info(f"{log_prefix}Reddit search: qualified ({qualified}) >= max_posts ({max_posts}), stopping (total scanned={len(collected)})")
            break

        try:
            if human_scroll:
                _human_scroll_feed(page)  # promo 场景：像人一样分段慢滚
            else:
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(2000)
        except Exception:
            break

    if not collected:
        logging.warning(f"{log_prefix}Reddit search returned no posts")
        return []

    # 按互动数倒序，挑前 max_posts 个满足阈值的
    items = sorted(collected.items(), key=lambda kv: (kv[1]['upvotes'] + kv[1]['comments']), reverse=True)
    entries = []
    for url, d in items:
        if d['upvotes'] < min_upvotes or d['comments'] < min_comments:
            continue
        if len(entries) >= max_posts:
            break
        published_at = d.get('created') or datetime.now(timezone.utc).isoformat()
        entries.append({
            'title': d.get('title') or url,
            'link': url,
            'summary': '',
            'source': source_name,
            'source_id': source_id,
            'published_at': published_at,
            'is_reddit': True,
            'reddit_upvotes': d['upvotes'],
            'reddit_comments': d['comments'],
            '_pre_filtered': True,
        })

    logging.info(f"{log_prefix}Reddit search: scanned={len(collected)}, kept={len(entries)} (>= {min_upvotes} upvotes & {min_comments} comments)")
    return entries

def _is_reddit_keyword_source(source: dict) -> bool:
    """Reddit 关键词搜索源识别：URL 含 reddit.com 且 description 以 'keyword ' 开头。"""
    url = (source.get('url') or '').lower()
    desc = (source.get('description') or '').strip().lower()
    return 'reddit.com' in url and desc.startswith('keyword ')

def _extract_subreddit_name(url: str) -> str:
    """从 reddit URL 中提取 subreddit 名（/r/<name>），失败返回空串。"""
    m = re.search(r'reddit\.com/r/([^/?#]+)', url or '', re.IGNORECASE)
    return m.group(1) if m else ''

def _is_reddit_subreddit_source(source: dict) -> bool:
    """Reddit subreddit 源识别：URL 含 reddit.com/r/<name> 且 description 不以 'keyword ' 开头。"""
    url = (source.get('url') or '').lower()
    desc = (source.get('description') or '').strip().lower()
    if desc.startswith('keyword '):
        return False
    return bool(_extract_subreddit_name(url))

def _scrape_reddit_subreddit_hot(page, subreddit: str, log_prefix: str,
                                 min_upvotes: int, min_comments: int,
                                 max_posts: int = 10, source_id=None,
                                 source_name: str = 'Reddit Subreddit',
                                 user_db=None, is_interactive: bool = False) -> list:
    """
    抓取 subreddit 的 hot 紧凑视图（compactView），从 shreddit-post 卡片直接读
    score / comment-count，避免对每条 RSS entry 都开详情页。

    Returns:
        List of entry dicts (same shape as _scrape_reddit_search), with _pre_filtered=True
    """
    target_url = f'https://www.reddit.com/r/{subreddit}/hot/?feedViewType=compactView'
    logging.info(f"{log_prefix}Reddit subreddit hot target: {target_url}")

    try:
        page.goto(target_url, wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
    except Exception as e:
        logging.error(f"{log_prefix}Failed to load subreddit hot page: {e}")
        return []

    # 登录拦截检测
    try:
        post_loaded = page.locator('shreddit-post').count() > 0
    except Exception:
        post_loaded = False
    if not post_loaded:
        # shreddit-post 未出现即视为登录墙/拦截，不再依赖关键词 grep。
        if not is_interactive:
            logging.warning(f"{log_prefix}Subreddit hot blocked (no post cards), requesting interactive mode...")
            raise LoginRequiredError("Reddit subreddit login required")
        logging.warning(f"{log_prefix}Subreddit hot requires login, waiting up to 120s for manual login...")
        try:
            page.wait_for_selector('shreddit-post', timeout=120000)
        except Exception:
            logging.error(f"{log_prefix}Login timeout on subreddit hot.")
            return []

    collected = {}  # url -> {upvotes, comments, title, created}
    max_scrolls = 20
    no_new_count = 0

    for scroll_i in range(max_scrolls):
        try:
            # subreddit 列表页 shreddit-post 元素 attribute 齐全：
            #   permalink, score, comment-count, post-title, created-timestamp
            posts = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('shreddit-post').forEach(p => {
                    const permalink = p.getAttribute('permalink') || '';
                    const score = parseInt(p.getAttribute('score') || '0', 10) || 0;
                    const comments = parseInt(p.getAttribute('comment-count') || '0', 10) || 0;
                    const title = p.getAttribute('post-title') || '';
                    const created = p.getAttribute('created-timestamp') || '';
                    if (permalink) results.push({permalink, score, comments, title, created});
                });
                return results;
            }''')
        except Exception as e:
            logging.warning(f"{log_prefix}Subreddit hot extract error on scroll {scroll_i}: {e}")
            break

        prev_count = len(collected)
        for p in posts:
            permalink = p.get('permalink') or ''
            if not permalink:
                continue
            full_url = f'https://www.reddit.com{permalink}' if permalink.startswith('/') else permalink
            if full_url not in collected:
                collected[full_url] = {
                    'upvotes': p.get('score', 0),
                    'comments': p.get('comments', 0),
                    'title': p.get('title', ''),
                    'created': p.get('created', '')
                }
        new_count = len(collected) - prev_count

        if new_count == 0:
            no_new_count += 1
            if no_new_count >= 2:
                logging.info(f"{log_prefix}Subreddit hot: no new results for 2 scrolls, stopping (total scanned={len(collected)})")
                break
        else:
            no_new_count = 0

        qualified = sum(
            1 for url, d in collected.items()
            if d['upvotes'] >= min_upvotes and d['comments'] >= min_comments
            and (user_db is None or not user_db.article_url_exists(url))
        )
        if qualified >= max_posts:
            logging.info(f"{log_prefix}Subreddit hot: qualified ({qualified}) >= max_posts ({max_posts}), stopping (total scanned={len(collected)})")
            break

        try:
            page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            page.wait_for_timeout(2000)
        except Exception:
            break

    if not collected:
        logging.warning(f"{log_prefix}Subreddit hot returned no posts")
        return []

    items = sorted(collected.items(), key=lambda kv: (kv[1]['upvotes'] + kv[1]['comments']), reverse=True)
    entries = []
    for url, d in items:
        if d['upvotes'] < min_upvotes or d['comments'] < min_comments:
            continue
        if len(entries) >= max_posts:
            break
        published_at = d.get('created') or datetime.now(timezone.utc).isoformat()
        entries.append({
            'title': d.get('title') or url,
            'link': url,
            'summary': '',
            'source': source_name,
            'source_id': source_id,
            'published_at': published_at,
            'is_reddit': True,
            'reddit_upvotes': d['upvotes'],
            'reddit_comments': d['comments'],
            '_pre_filtered': True,
        })

    logging.info(f"{log_prefix}Subreddit hot: scanned={len(collected)}, kept={len(entries)} (>= {min_upvotes} upvotes & {min_comments} comments)")
    return entries

