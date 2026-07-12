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
from browser_utils import LoginRequiredError, _create_browser, _close_browser, _check_logins_needed, _ensure_browser_logins


def _scrape_twitter_feed(page, target_url: str, log_identifier: str, min_faves: int, min_replies: int,
                          max_posts: int = 10, user_db=None, max_age_cutoff=None,
                          human_scroll: bool = False) -> list:
    """
    在 X.com 执行页面滚动发现帖子并返回带互动数据的列表。适用于搜索页或主页。
    滚动停止条件基于通过 URL 去重 + 热度过滤后的有效帖子数。
    
    Args:
        page: Playwright page
        target_url: 目标 URL
        log_identifier: 日志标识符（如 'keyword: AI' 或 'kol: elonmusk'）
        min_faves: 最小点赞数（用于有效帖子计数）
        min_replies: 最小回复数（用于有效帖子计数）
        max_posts: 有效帖子数上限（通过去重+热度过滤后）
        user_db: UserDatabase 实例，用于 URL 去重判断
    
    Returns:
        List of dicts: [{url, faves, replies}, ...]
    """
    
    logging.info(f"[Twitter] Target URL ({log_identifier}): {target_url}")
    
    # 提取 KOL 用户名，用于通过 URL 对比过滤转推（比 socialContext DOM 检测更可靠）
    import re as _re
    _kol_match = _re.search(r'x\.com/([^/]+)', target_url)
    kol_username = _kol_match.group(1).lower() if _kol_match else None
    is_kol = log_identifier.startswith('kol')

    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(20000)
    try:
        page.goto(target_url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(5000)  # 等待加载
    except Exception as e:
        logging.error(f"[Twitter] Failed to load page for '{log_identifier}': {e}")
        return []
    
    try:
        page_text = page.evaluate('() => document.body?.innerText || ""')
        if any(msg in page_text for msg in ['Something went wrong', 'Try again', 'Rate limit']):
            logging.error(f"[Twitter] Blocked or rate-limited for '{log_identifier}'. Page text: {page_text[:200]}")
            return []
    except Exception:
        pass
    
    # 持续滚动加载并收集帖子 URL + 互动数据
    collected_posts = {}  # url -> {faves, replies}
    max_scrolls = 20  # 安全上限
    no_new_count = 0  # 连续无新帖子计数
    
    for scroll_i in range(max_scrolls):
        try:
            posts = page.evaluate('''() => {
                const articles = document.querySelectorAll('article[data-testid="tweet"]');
                const results = [];
                articles.forEach(article => {
                    const contextEl = article.querySelector('[data-testid="socialContext"]');
                    const contextText = contextEl ? (contextEl.innerText || '').toLowerCase() : '';
                    const isPinned = contextText.includes('pinned') || contextText.includes('置顶');
                    const isRetweet = contextText.includes('reposted') || contextText.includes('转推');
                    
                    if (isRetweet) return; // 过滤纯转推
                    
                    // 从 time 元素的父级 <a> 获取帖子永久链接（最可靠的方式）
                    let postUrl = '';
                    let postTime = '';
                    const timeEl = article.querySelector('time');
                    if (timeEl) {
                        postTime = timeEl.getAttribute('datetime') || '';
                        const timeLink = timeEl.closest('a');
                        if (timeLink && /\\/status\\/\\d+/.test(timeLink.href)) {
                            const match = timeLink.href.match(/(https:\\/\\/x\\.com\\/[^/]+\\/status\\/\\d+)/);
                            if (match) postUrl = match[1];
                        }
                    }
                    if (!postUrl) return;
                    
                    // 通用提取函数：优先从按钮可见文本提取数字（语言无关），fallback 到 aria-label
                    function extractCount(btn) {
                        if (!btn) return 0;
                        // 方法1: 可见文本（如 "3", "1.2K", "12万"）
                        const visText = (btn.innerText || '').trim();
                        if (visText) {
                            // 处理 K/M 后缀 (1.2K -> 1200, 3M -> 3000000)
                            const kmMatch = visText.match(/([\d.]+)\s*([KkMm万])/);
                            if (kmMatch) {
                                const num = parseFloat(kmMatch[1]);
                                const suffix = kmMatch[2];
                                if (suffix === 'K' || suffix === 'k') return Math.round(num * 1000);
                                if (suffix === 'M' || suffix === 'm') return Math.round(num * 1000000);
                                if (suffix === '万') return Math.round(num * 10000);
                            }
                            const numMatch = visText.match(/(\d[\d,]*)/);
                            if (numMatch) return parseInt(numMatch[1].replace(/,/g, ''), 10) || 0;
                        }
                        // 方法2: aria-label fallback
                        const label = btn.getAttribute('aria-label') || '';
                        const match = label.match(/(\d[\d,]*)/);
                        if (match) return parseInt(match[1].replace(/,/g, ''), 10) || 0;
                        return 0;
                    }
                    
                    const likeBtn = article.querySelector('[data-testid="like"]') || article.querySelector('[data-testid="unlike"]');
                    const favesCount = extractCount(likeBtn);
                    const replyBtn = article.querySelector('[data-testid="reply"]');
                    const replyCount = extractCount(replyBtn);
                    const textEl = article.querySelector('[data-testid="tweetText"]');
                    const tweetText = textEl ? (textEl.innerText || '').slice(0, 400) : '';

                    results.push({url: postUrl, faves: favesCount, replies: replyCount, time: postTime, isPinned: isPinned, text: tweetText});
                });
                return results;
            }''')
            
            prev_count = len(collected_posts)
            old_post_count = 0
            for post in posts:
                url = post['url']
                
                # KOL 模式：通过 URL 中的用户名过滤转推（URL 如 x.com/other_user/status/xxx 说明是转推）
                if is_kol and kol_username:
                    _post_user_match = _re.search(r'x\.com/([^/]+)/status/', url)
                    if _post_user_match and _post_user_match.group(1).lower() != kol_username:
                        logging.debug(f"[Twitter] Filtered retweet (author={_post_user_match.group(1)}, kol={kol_username}): {url}")
                        continue
                
                if url not in collected_posts:
                    collected_posts[url] = {
                        'faves': post['faves'],
                        'replies': post['replies'],
                        'time': post.get('time'),
                        'isPinned': post.get('isPinned'),
                        'text': post.get('text', '')
                    }
                    logging.debug(f"[Twitter] Post: faves={post['faves']}, replies={post['replies']}, time={post.get('time')}, isPinned={post.get('isPinned')}, url={url}")
                
                # KOL 提前停止检测：如果该帖子早于 max_age_cutoff，且不是置顶帖（置顶帖通常是最老的但排在最前）
                if log_identifier.startswith('kol') and max_age_cutoff and not post.get('isPinned'):
                    pt = post.get('time')
                    if pt:
                        try:
                            from datetime import datetime, timezone
                            post_dt = datetime.fromisoformat(pt.replace('Z', '+00:00'))
                            if post_dt.tzinfo is None:
                                post_dt = post_dt.replace(tzinfo=timezone.utc)
                            if post_dt < max_age_cutoff:
                                old_post_count += 1
                        except Exception:
                            pass
            
            new_count = len(collected_posts) - prev_count
                    
        except Exception as e:
            logging.warning(f"[Twitter] Error extracting posts on scroll {scroll_i}: {e}")
            break
        
        # 停止条件1: 连续2次滚动无新帖子（页面到底了）
        if new_count == 0:
            no_new_count += 1
            if no_new_count >= 2:
                logging.info(f"[Twitter] No new posts for 2 consecutive scrolls, stopping (total={len(collected_posts)})")
                break
        else:
            no_new_count = 0
            
        # 停止条件4 (KOL特有): 如果当前批次发现 >= 2 个超出时限且非置顶的老帖子，停止滚动
        if old_post_count >= 2:
            logging.info(f"[Twitter] Found {old_post_count} posts older than max age on KOL homepage, stopping early.")
            break
        
        # 停止条件2: 通过 URL 去重 + 热度过滤后的有效帖子数 >= max_posts
        qualified_count = sum(
            1 for url, data in collected_posts.items()
            if (data['faves'] >= min_faves and data['replies'] >= min_replies)
            and (user_db is None or not user_db.article_url_exists(url))
        )
        if qualified_count >= max_posts:
            logging.info(f"[Twitter] Qualified posts ({qualified_count}) >= max_posts ({max_posts}), stopping (total collected={len(collected_posts)})")
            break
        
        # 停止条件3: 当前滚动中所有新发现帖子的点赞和回复都低于最低要求
        if new_count > 0 and scroll_i > 0:
            all_below = all(
                p['faves'] < min_faves and p['replies'] < min_replies
                for p in [collected_posts[url] for url in list(collected_posts.keys())[-new_count:]]
            )
            if all_below:
                logging.info(f"[Twitter] All new posts below threshold (faves<{min_faves} AND replies<{min_replies}), stopping")
                break
        
        # 滚动加载更多。promo 场景像人一样分段慢滚，其余场景直接跳到底（快、用于数据采集）
        try:
            if human_scroll:
                _human_scroll_replies(page)
            else:
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(2000)
        except Exception:
            break
    
    if not collected_posts:
        logging.warning(f"[Twitter] No posts found for '{log_identifier}'.")
        logging.warning(f"[Twitter] Current URL: {page.url}")
        return []
    
    result = [{'url': url, 'faves': data['faves'], 'replies': data['replies'], 'time': data.get('time'), 'isPinned': data.get('isPinned'), 'text': data.get('text', '')}
              for url, data in collected_posts.items()]
    return result

def _human_scroll_replies(page):
    """详情页加载更多回复时，像人一样分段小幅滚动约一屏 + 段间停顿，
    而不是一次跳到底部（跳底部既不像真人，也更容易触发风控）。"""
    try:
        viewport_h = page.evaluate("window.innerHeight") or 800
    except Exception:
        page.wait_for_timeout(2000)
        return
    # 只滚约 0.6-0.85 屏，拆成更多小段、段间停顿更长，整体放慢更像真人
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
        if random.random() < 0.15:  # 偶尔停下来多看两眼
            page.wait_for_timeout(random.randint(700, 1600))
    # 滚完后像人一样停下来扫一眼
    page.wait_for_timeout(random.randint(1200, 2800))


def _fetch_tweet_content(page, tweet_url: str, log_prefix: str = '[Twitter] ',
                         human_scroll: bool = False) -> tuple:
    """
    提取单条推文的主贴内容和回复内容。
    通过 URL 中的 status ID 定位目标推文，正确处理回复链。
    互动数据（faves/replies）由搜索页提取，此处不再重复获取。

    Returns:
        Tuple of (full_text, tweet_title, published_at_str, last_reply_time)
    """
    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(20000)
    try:
        page.goto(tweet_url, wait_until='domcontentloaded', timeout=20000)
        try:
            page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
        except Exception:
            page.wait_for_timeout(3000)
    except Exception as e:
        logging.warning(f"{log_prefix}Failed to load tweet page: {tweet_url}, error: {e}")
        return None, None, None, None
    
    # 展开被截断的长推文（"Show more" / "显示更多"按钮）
    try:
        show_more_buttons = page.locator('[data-testid="tweet-text-show-more-link"]')
        for i in range(show_more_buttons.count()):
            try:
                show_more_buttons.nth(i).click()
                page.wait_for_timeout(500)
            except Exception:
                pass
    except Exception:
        pass
    
    # 从 URL 提取 status ID
    import re
    status_match = re.search(r'/status/(\d+)', tweet_url)
    target_status_id = status_match.group(1) if status_match else ''

    # 智能滚动加载回复：持续滚动直到无新回复或达到上限
    # 注意：Twitter 详情页在向下滚动时，早期 DOM 节点会被卸载，因此需要在滚动循环中实时提取并累积
    max_scrolls = 10
    no_new_count = 0
    seen_articles = set()
    collected_articles = []
    
    stop_scrolling = False
    
    for scroll_i in range(max_scrolls + 1):
        try:
            # 提取当前视口内可见的内容
            batch_result = page.evaluate('''(targetId) => {
                const cells = document.querySelectorAll('div[data-testid="cellInnerDiv"]');
                const articles = [];
                let hitStopHeading = false;
                
                for (let i = 0; i < cells.length; i++) {
                    const cell = cells[i];
                    
                    const heading = cell.querySelector('h2, [role="heading"]');
                    if (heading) {
                        const hText = heading.innerText.toLowerCase();
                        if (hText.includes('more') || hText.includes('explore') || 
                            hText.includes('discover') || hText.includes('更多') || 
                            hText.includes('发现') || hText.includes('related') || 
                            hText.includes('follow') || hText.includes('关注')) {
                            hitStopHeading = true;
                            break;
                        }
                    }
                    
                    const article = cell.querySelector('article[data-testid="tweet"]');
                    if (article) {
                        const textEls = article.querySelectorAll('[data-testid="tweetText"]');
                        let text = '';
                        textEls.forEach((el, idx) => {
                            text += (idx > 0 ? '\\n[引用推文]: ' : '') + el.innerText + '\\n';
                        });
                        text = text.trim();
                        
                        const userEl = article.querySelector('[data-testid="User-Name"]');
                        let user = 'Unknown';
                        if (userEl) {
                            const parts = userEl.innerText.split('\\n').filter(s => s.trim());
                            const displayName = parts[0] || '';
                            const handle = parts.find(p => p.startsWith('@')) || '';
                            user = handle ? `${displayName} (${handle})` : displayName;
                        }
                        
                        const timeEl = article.querySelector('time');
                        const time = timeEl ? timeEl.getAttribute('datetime') : '';
                        
                        let isTarget = false;
                        const timeLinks = article.querySelectorAll('a[href*="/status/' + targetId + '"]');
                        for (const link of timeLinks) {
                            const hasTime = link.querySelector('time') || link.closest('time');
                            if (hasTime) {
                                isTarget = true;
                                break;
                            }
                        }
                        if (!isTarget) {
                            const link = article.querySelector('a[href*="/status/' + targetId + '"]');
                            if (link) isTarget = true;
                        }
                        
                        // 使用 用户+时间+摘要 作为唯一ID，用于跨滚动去重
                        const articleId = user + "|" + time + "|" + text.substring(0, 50);
                        
                        articles.push({
                            id: articleId,
                            user: user,
                            time: time,
                            text: text,
                            isTarget: isTarget
                        });
                    }
                }
                return { articles: articles, hitStopHeading: hitStopHeading };
            }''', target_status_id)
            
            if not batch_result:
                break
                
            batch_articles = batch_result.get('articles', [])
            hit_stop = batch_result.get('hitStopHeading', False)
            
            new_added = 0
            for a in batch_articles:
                if a['id'] not in seen_articles:
                    seen_articles.add(a['id'])
                    collected_articles.append(a)
                    new_added += 1
            
            if hit_stop:
                logging.debug(f"[Twitter] Hit explore/more heading, stopping scroll")
                stop_scrolling = True
                
            if new_added == 0:
                no_new_count += 1
                if no_new_count >= 2:
                    logging.debug(f"[Twitter] Detail page scroll stopped: no new replies for 2 rounds (total articles={len(collected_articles)})")
                    break
            else:
                no_new_count = 0
                
            if scroll_i < max_scrolls and not stop_scrolling:
                if human_scroll:
                    _human_scroll_replies(page)  # promo 场景：像人一样分段滚动，别一次跳到底
                else:
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    page.wait_for_timeout(2000)
            elif stop_scrolling:
                break
                
        except Exception as e:
            logging.warning(f"[Twitter] Error during detail page scroll {scroll_i}: {e}")
            break
            
    if not collected_articles:
        try:
            diag = page.evaluate("() => ({title: document.title, body: document.body.innerText.slice(0,300)})")
            logging.warning(f"{log_prefix}No content extracted from {tweet_url} | title={diag['title']!r} | body={diag['body']!r}")
        except Exception:
            logging.warning(f"{log_prefix}No content extracted from {tweet_url}")
        return None, None, None, None

    # 定位主贴索引
    target_idx = -1
    for i, a in enumerate(collected_articles):
        if a.get('isTarget'):
            target_idx = i
            break
            
    if target_idx == -1:
        target_idx = 0  # 默认第一个为主贴
        
    texts = []
    main_time = ''
    main_text = ''
    last_reply_time = ''
    
    for i, a in enumerate(collected_articles):
        text = a.get('text', '')
        time = a.get('time', '')
        user = a.get('user', 'Unknown')
        
        if i == target_idx:
            main_time = time
            main_text = text
            
        if i > target_idx and time:
            last_reply_time = time
            
        if text:
            if i < target_idx:
                prefix = '[上文]'
            elif i == target_idx:
                prefix = '[主贴]'
            else:
                prefix = '[回复]'
            texts.append(f"{prefix} {user} ({time}):\n{text}")
            
    content = {
        'texts': texts,
        'mainTime': main_time,
        'mainText': main_text,
        'lastReplyTime': last_reply_time,
        'replyCount': len(collected_articles) - target_idx - 1
    }
    
    full_text = '\n\n'.join(content['texts'])
    title = content['mainText'][:100] if content.get('mainText') else 'X.com Post'
    published_at = content.get('mainTime', '')
    last_reply_time = content.get('lastReplyTime', '') or None
    reply_count = content.get('replyCount', 0)
    
    logging.info(f"[Twitter] Extracted {len(content['texts'])} sections ({reply_count} replies, {len(full_text)} chars), last_reply={last_reply_time} from {tweet_url}")
    return full_text, title, published_at, last_reply_time

def fetch_twitter_posts(twitter_sources, user_db, ai_engine, vector_store, fetch_stats: 'FetchStats' = None) -> int:
    """
    从 X.com 采集指定关键词或KOL的大V主页上的高互动帖子，处理后入库。
    
    Returns:
        Number of posts processed
    """
    import time
    import random
    import urllib.parse
    from lsh_dedup import compute_simhash
    
    config = ConfigLoader.get_instance()
    fetching_config = config.get('fetching', {})
    twitter_config = fetching_config.get('twitter', {})
    discussion_cfg = fetching_config.get('discussion', {})

    if not twitter_config.get('enabled', False):
        logging.info("[Twitter] Twitter fetching is disabled in config")
        return 0
    
    tasks = []
    for source in twitter_sources:
        desc_original = source.get('description', '').strip()
        desc_lower = desc_original.lower()
        if desc_lower.startswith('keyword '):
            keyword = desc_original[len('keyword '):].strip()
            if keyword:
                tasks.append({'type': 'keyword', 'value': keyword, 'source': source})
        elif desc_lower.startswith('kol '):
            kol = desc_original[len('kol '):].strip()
            if kol:
                tasks.append({'type': 'kol', 'value': kol, 'source': source})

    if not tasks:
        logging.info("[Twitter] No keywords or KOLs found in RSS sources")
        return 0
    
    tasks.sort(key=lambda t: (
        t['source'].get('last_fetch_at') is not None,
        t['source'].get('last_fetch_at', '')
    ))
    
    # 计算 since 日期
    discussion_max_age_days = discussion_cfg.get('max_age_days', 3)
    since_date = (datetime.now(timezone.utc) - timedelta(days=discussion_max_age_days)).strftime('%Y-%m-%d')
    max_age_cutoff = datetime.now(timezone.utc) - timedelta(days=discussion_max_age_days)

    min_faves = discussion_cfg.get('min_likes', 100)
    min_replies = discussion_cfg.get('min_replies', 50)
    max_per_keyword = twitter_config.get('max_posts_per_keyword', 10)
    twitter_max_per_run = twitter_config.get('max_per_run', 50)
    search_delay_min = discussion_cfg.get('search_delay_min', 15)
    search_delay_max = discussion_cfg.get('search_delay_max', 30)
    post_delay_min = discussion_cfg.get('post_delay_min', 5)
    post_delay_max = discussion_cfg.get('post_delay_max', 10)
    max_input_chars = fetching_config.get('max_input_chars', 80000)

    # Discussion 通用时间过滤参数
    discussion_max_update_hours = discussion_cfg.get('max_update_hours', 24)
    discussion_update_cutoff = datetime.now(timezone.utc) - timedelta(hours=discussion_max_update_hours)
    discussion_min_age_hours = discussion_cfg.get('min_age_hours', 6)
    discussion_too_new_cutoff = datetime.now(timezone.utc) - timedelta(hours=discussion_min_age_hours)
    
    # 启动浏览器（使用独立 profile 目录，避免与 RSS/Reddit/YouTube 并行管道的 Chrome 锁冲突）
    try:
        pw, ctx, page = _create_browser(force_visible=False, profile_name='twitter')
    except Exception as e:
        logging.error(f"[Twitter] Failed to launch browser: {e}")
        return 0

    is_interactive = False

    # 检查登录态
    needs_login = _check_logins_needed(ctx, False, False, needs_twitter=True)
    if needs_login:
        _close_browser(pw, ctx)
        try:
            pw, ctx, page = _create_browser(force_visible=True, profile_name='twitter')
        except Exception as e:
            logging.error(f"[Twitter] Failed to relaunch browser in visible mode: {e}")
            return 0
        is_interactive = True
        _ensure_browser_logins(ctx, page, False, False, needs_twitter=True)
    
    # 使用共享或新建统计实例
    if fetch_stats is None:
        fetch_stats = FetchStats()
    processed = 0
    global_fetched = 0
    
    try:
        for i, task in enumerate(tasks):
            if global_fetched >= twitter_max_per_run:
                logging.info(f"[Twitter] Reached global limit ({twitter_max_per_run}), stopping Twitter pipeline.")
                break
            if i > 0:
                delay = random.uniform(search_delay_min, search_delay_max)
                logging.info(f"[Twitter] Waiting {delay:.1f}s before next task...")
                time.sleep(delay)
            
            task_type = task['type']
            task_value = task['value']
            source = task['source']
            twitter_source_id = source['id']
            task_desc = source.get('description') or f"{task_type} {task_value}"
            log_prefix = f"[Twitter][desc={task_desc}] "

            if task_type == 'keyword':
                logging.info(f"{log_prefix}=== Searching keyword {i+1}/{len(tasks)}: '{task_value}' ===")
                query = f'{task_value} (lang:zh OR lang:en) since:{since_date}'
                target_url = f'https://x.com/search?q={urllib.parse.quote(query)}&src=typed_query&f=top'
                log_id = f"keyword: {task_value}"
                error_id = f"X.com/{task_value}"
            else:
                logging.info(f"{log_prefix}=== Scraping KOL {i+1}/{len(tasks)}: '{task_value}' ===")
                target_url = source.get('url', '')
                if not target_url.startswith('http'):
                    logging.error(f"{log_prefix}Invalid URL '{target_url}' for KOL: {task_value}")
                    continue
                log_id = f"kol: {task_value}"
                error_id = f"X.com/kol/{task_value}"

            search_results = _scrape_twitter_feed(
                page, target_url, log_id, min_faves, min_replies, max_per_keyword, user_db, max_age_cutoff=max_age_cutoff
            )
            
            if not search_results:
                fetch_stats.record_failure('https://x.com', error_id, 'no_search_results')
                continue
            
            keyword_fetched = 0  # 当前任务已进入详情页的帖子数
            keyword_skip_reasons = {}  # 统计各跳过原因的数量
            
            for post_info in search_results:
                tweet_url = post_info['url']
                search_faves = post_info.get('faves', 0)
                search_replies = post_info.get('replies', 0)
                meets_threshold = search_faves >= min_faves and search_replies >= min_replies
                
                # 1. URL 去重（articles 表 + fetched_urls 表含 next_fetch_at 判断）
                if user_db.article_url_exists(tweet_url):
                    reason = 'url_exists'
                    logging.debug(f"{log_prefix}Skipping existing URL: {tweet_url}")
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_skip('https://x.com', error_id, reason)
                    continue
                
                # 2. 搜索页互动数过滤：低于阈值直接记录延迟重扫，不访问详情页
                if search_faves < min_faves or search_replies < min_replies:
                    gap_ratio = 1 - max(
                        search_faves / max(min_faves, 1),
                        search_replies / max(min_replies, 1)
                    )
                    gap_ratio = max(0.0, min(1.0, gap_ratio))
                    delay_hours = discussion_cfg.get('min_age_hours', 2) + gap_ratio * 6
                    next_fetch_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
                    user_db.record_fetched_url(tweet_url, twitter_source_id, next_fetch_at=next_fetch_at)
                    logging.debug(f"{log_prefix}Low engagement (faves={search_faves}, replies={search_replies}), "
                                  f"next_fetch_at={next_fetch_at.strftime('%m-%d %H:%M')}, skipping detail page")
                    reason = 'low_engagement'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_skip('https://x.com', error_id, 'twitter_low_engagement')
                    continue
                
                # 2.5 列表页时间预过滤：如果在列表页读取到了时间，并且早于 max_age_cutoff (仅限 KOL，非置顶)，跳过详情页抓取
                feed_time = post_info.get('time')
                if task_type == 'kol' and max_age_cutoff and feed_time and not post_info.get('isPinned'):
                    try:
                        pt_dt = datetime.fromisoformat(feed_time.replace('Z', '+00:00'))
                        if pt_dt.tzinfo is None:
                            pt_dt = pt_dt.replace(tzinfo=timezone.utc)
                        if pt_dt < max_age_cutoff:
                            logging.debug(f"{log_prefix}Skipping old post directly from feed list: {tweet_url}")
                            reason = 'feed_too_old'
                            keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                            fetch_stats.record_skip('https://x.com', error_id, reason)
                            continue
                    except Exception:
                        pass
                
                # 3. 满足互动数阈值的帖子，限制进入详情页的数量
                if keyword_fetched >= max_per_keyword:
                    logging.info(f"{log_prefix}Reached max_posts_per_keyword ({max_per_keyword}) for '{task_value}', next task")
                    break

                if global_fetched >= twitter_max_per_run:
                    logging.info(f"{log_prefix}Reached global limit ({twitter_max_per_run}), stopping Twitter pipeline.")
                    break

                if keyword_fetched > 0:
                    delay = random.uniform(post_delay_min, post_delay_max)
                    logging.info(f"{log_prefix}Waiting {delay:.1f}s before next post...")
                    time.sleep(delay)

                logging.info(f"{log_prefix}-> Fetching details for: {tweet_url} (faves: {search_faves}, replies: {search_replies})")
                # 4. 进入详情页获取全文内容（互动数据使用搜索页结果）
                full_text, title, published_at, last_reply_time = _fetch_tweet_content(page, tweet_url, log_prefix=log_prefix)
                if not full_text:
                    reason = 'content_extract_failed'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_failure('https://x.com', error_id, reason)
                    continue
                
                faves_count = search_faves
                reply_count = search_replies
                keyword_fetched += 1
                global_fetched += 1
                
                # === published_at 时间过滤：太新的帖子互动少 ===
                if published_at:
                    try:
                        pub_dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        if pub_dt > discussion_too_new_cutoff:
                            logging.debug(f"{log_prefix}Too new (published: {published_at}, < {discussion_min_age_hours}h ago): {title[:50]}")
                            user_db.record_fetched_url(tweet_url, twitter_source_id)
                            reason = 'too_new'
                            keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                            fetch_stats.record_skip('https://x.com', error_id, 'discussion_too_new')
                            continue
                    except Exception as e:
                        logging.debug(f"{log_prefix}Failed to parse published_at '{published_at}': {e}")
                
                # === 最新回复活跃度过滤（使用 config 中的 discussion_max_update_hours） ===
                if last_reply_time:
                    try:
                        last_reply_dt = datetime.fromisoformat(last_reply_time.replace('Z', '+00:00'))
                        if last_reply_dt.tzinfo is None:
                            last_reply_dt = last_reply_dt.replace(tzinfo=timezone.utc)
                        if last_reply_dt < discussion_update_cutoff:
                            logging.debug(f"{log_prefix}Inactive (last reply {last_reply_time} > {discussion_max_update_hours}h ago): {title[:50]}")
                            user_db.record_fetched_url(tweet_url, twitter_source_id)
                            reason = 'inactive'
                            keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                            fetch_stats.record_skip('https://x.com', error_id, 'discussion_update_old')
                            continue
                    except Exception as e:
                        logging.debug(f"{log_prefix}Failed to parse last_reply_time '{last_reply_time}': {e}")
                
                # 记录已获取 URL（通过所有过滤）
                user_db.record_fetched_url(tweet_url, twitter_source_id)
                
                # 截断过长内容
                if len(full_text) > max_input_chars:
                    full_text = full_text[:max_input_chars]
                
                # LSH 去重
                content_hash = compute_simhash(full_text)
                if user_db.get_similar_article(content_hash):
                    logging.debug(f"{log_prefix}LSH duplicate: {title[:50]}")
                    reason = 'lsh_duplicate'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_skip('https://x.com', error_id, reason)
                    continue
                
                # LLM 摘要（使用 discussion 类别）
                logging.info(f"{log_prefix}Summarizing: {title[:50]}...")
                try:
                    summary_en, summary_zh, long_summary_en, long_summary_zh, \
                        ai_importance, tags, title_en, title_zh, china_blocked = \
                        ai_engine.summarize_content(full_text, title=title, category='discussion')
                except Exception as e:
                    logging.error(f"{log_prefix}LLM summarization failed for {tweet_url}: {e}")
                    reason = 'summarize_failed'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_failure('https://x.com', error_id, reason)
                    continue
                
                if china_blocked:
                    logging.warning(f"{log_prefix}China compliance blocked: {tweet_url}")
                    reason = 'china_blocked'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_skip('https://x.com', error_id, reason)
                    continue

                # 阿里云内容安全二次过滤
                from compliance_filter import check_summary
                _safe, _labels = check_summary(title_en, title_zh, summary_en, summary_zh,
                                               long_summary_en, long_summary_zh)
                if not _safe:
                    logging.warning(f"{log_prefix}Aliyun Green blocked [{_labels}]: {tweet_url}")
                    reason = 'aliyun_green_blocked'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_skip('https://x.com', error_id, reason)
                    continue

                if not summary_en and not summary_zh:
                    logging.warning(f"{log_prefix}Empty summary for {tweet_url}")
                    reason = 'summarize_failed'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_failure('https://x.com', error_id, reason)
                    continue
                
                # 向量去重
                similar = vector_store.find_similar_articles(
                    summary=summary_en or summary_zh, tags=tags, threshold=0.85, limit=1
                )
                if similar:
                    dup = similar[0]
                    logging.debug(f"{log_prefix}Semantic duplicate (similarity={dup['similarity']:.3f}): {title[:50]}")
                    reason = 'vector_duplicate'
                    keyword_skip_reasons[reason] = keyword_skip_reasons.get(reason, 0) + 1
                    fetch_stats.record_skip('https://x.com', error_id, reason)
                    continue
                
                final_summary_en = f"[{faves_count} likes/{reply_count} replies] {summary_en}" if summary_en else summary_en
                final_summary_zh = f"[{faves_count}赞/{reply_count}评] {summary_zh}" if summary_zh else summary_zh

                if task_type == 'kol':
                    if title_en:
                        title_en = f"{task_value}: {title_en}"
                    if title_zh:
                        title_zh = f"{task_value}：{title_zh}"

                # 入库
                article_data = {
                    'rss_source_id': twitter_source_id,
                    'title': title,
                    'title_en': title_en,
                    'title_zh': title_zh,
                    'url': tweet_url,
                    'summary_en': final_summary_en,
                    'summary_zh': final_summary_zh,
                    'long_summary_en': long_summary_en,
                    'long_summary_zh': long_summary_zh,
                    'category': 'discussion',
                    'importance_score': ai_importance,
                    'tags': tags,
                    'content_hash': content_hash,
                    'published_at': published_at or None
                }
                article_id = user_db.add_article(article_data)
                if article_id:
                    vector_store.add_article(
                        article_id=article_id,
                        summary=summary_en or summary_zh,
                        tags=tags,
                        source='X.com (Twitter)',
                        category='discussion'
                    )
                    try:
                        from knowledge_ingest import ingest_async
                        ingest_async(article_id, {**article_data, 'source': 'X.com (Twitter)'}, full_text=full_text, source_name='X.com (Twitter)')
                    except Exception as _e:
                        logging.warning(f"KB ingest_async dispatch failed: {_e}")
                    processed += 1
                    
                    # 记录 LLM 统计
                    llm_input_chars = min(len(full_text), max_input_chars)
                    llm_output_chars = len(summary_en or '') + len(summary_zh or '') + \
                                       len(long_summary_en or '') + len(long_summary_zh or '') + \
                                       len(title_en or '') + len(title_zh or '')
                    fetch_stats.record_success('https://x.com', error_id, llm_input_chars, llm_output_chars)
                    logging.info(f"{log_prefix}✓ Stored article: {title[:50]}... (importance={ai_importance})")
            
            # 任务处理结束后输出汇总日志
            total = len(search_results)
            qualified = sum(1 for p in search_results if p.get('faves', 0) >= min_faves and p.get('replies', 0) >= min_replies)
            total_skipped = sum(keyword_skip_reasons.values())
            skip_detail = ', '.join(f"{r}={c}" for r, c in keyword_skip_reasons.items()) if keyword_skip_reasons else 'none'
            logging.info(f"{log_prefix}Task '{task_value}' ({task_type}): total={total}, qualified={qualified}, "
                         f"fetched_detail={keyword_fetched}, skipped={total_skipped} ({skip_detail})")
            
            user_db.update_rss_source_fetch_time(twitter_source_id)
    
    except Exception as e:
        logging.error(f"[Twitter] Pipeline error: {e}", exc_info=True)
    finally:
        _close_browser(pw, ctx)
    
    logging.info(f"[Twitter] Pipeline finished. Processed={processed}")
    return processed

