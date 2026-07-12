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


def _parse_github_trending_page(html: str) -> list:
    """
    Parse GitHub Trending page HTML and extract repository info.
    
    Returns:
        List of dicts with keys: owner, repo, url, description, stars, forks, language
        Ordered by trending rank (first = #1).
    """
    from bs4 import BeautifulSoup
    
    soup = BeautifulSoup(html, 'html.parser')
    repos = []
    
    # Each trending repo is in an <article> element with class "Box-row"
    articles = soup.select('article.Box-row')
    
    for article in articles:
        try:
            # Extract repo name from h2 > a
            h2 = article.select_one('h2')
            if not h2:
                continue
            link = h2.select_one('a')
            if not link:
                continue
            
            href = link.get('href', '').strip('/')
            parts = href.split('/')
            if len(parts) < 2:
                continue
            
            owner = parts[0]
            repo_name = parts[1]
            url = f"https://github.com/{owner}/{repo_name}"
            
            # Extract description
            desc_elem = article.select_one('p')
            description = desc_elem.get_text(strip=True) if desc_elem else ''
            
            # Extract stars (look for links to /stargazers)
            stars = 0
            star_link = article.select_one('a[href$="/stargazers"]')
            if star_link:
                star_text = star_link.get_text(strip=True).replace(',', '')
                try:
                    stars = int(star_text)
                except ValueError:
                    pass
            
            # Extract forks
            forks = 0
            fork_link = article.select_one('a[href$="/forks"]')
            if fork_link:
                fork_text = fork_link.get_text(strip=True).replace(',', '')
                try:
                    forks = int(fork_text)
                except ValueError:
                    pass
            
            # Extract language
            language = ''
            lang_span = article.select_one('[itemprop="programmingLanguage"]')
            if lang_span:
                language = lang_span.get_text(strip=True)
            
            repos.append({
                'owner': owner,
                'repo': repo_name,
                'url': url,
                'description': description,
                'stars': stars,
                'forks': forks,
                'language': language,
            })
        except Exception as e:
            logging.debug(f"[GitHub] Failed to parse trending repo entry: {e}")
            continue
    
    return repos

def _fetch_github_readme(owner: str, repo: str, timeout: int = 30) -> str:
    """
    Fetch a GitHub repo's README content via GitHub API.
    
    Returns:
        README text content, or empty string if failed.
    """
    import requests
    import base64
    
    github_token = os.environ.get('GITHUB_TOKEN', '')
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'InBrief-Bot/1.0',
    }
    if github_token:
        headers['Authorization'] = f'token {github_token}'
    
    api_url = f'https://api.github.com/repos/{owner}/{repo}/readme'
    
    try:
        resp = requests.get(api_url, headers=headers, timeout=timeout)
        
        if resp.status_code == 404:
            logging.debug(f"[GitHub] No README found for {owner}/{repo}")
            return ''
        
        if resp.status_code == 403:
            # Rate limit exceeded
            remaining = resp.headers.get('X-RateLimit-Remaining', '?')
            reset_time = resp.headers.get('X-RateLimit-Reset', '?')
            logging.warning(f"[GitHub] API rate limit exceeded (remaining={remaining}, reset={reset_time})")
            return ''
        
        resp.raise_for_status()
        data = resp.json()
        
        # Decode base64 content
        content = data.get('content', '')
        encoding = data.get('encoding', 'base64')
        
        if encoding == 'base64' and content:
            readme_text = base64.b64decode(content).decode('utf-8', errors='replace')
            return readme_text
        
        return ''
    except Exception as e:
        logging.warning(f"[GitHub] Failed to fetch README for {owner}/{repo}: {e}")
        return ''

def fetch_github_trending(user_db, ai_engine, vector_store, fetch_stats: 'FetchStats' = None) -> int:
    """
    从 GitHub Trending 抓取热门开源项目，筛选 AI 相关项目，生成摘要后入库。
    每天执行一次，全量替换 category='opensource' 的文章。
    
    Returns:
        Number of projects processed
    """
    import requests
    import time
    
    config = ConfigLoader.get_instance()
    gh_config = config.get('fetching', {}).get('github_trending', {})
    
    if not gh_config.get('enabled', False):
        logging.info("[GitHub] GitHub Trending fetching is disabled in config")
        return 0
    
    # 今日是否已采集：检查 opensource 文章中 published_at 落在今日的数量。
    # 命中复用会刷新 published_at，所以即使没有新增行也能反映"今天跑过"。
    if user_db.get_articles_count_by_category_published_today('opensource') > 0:
        logging.info("[GitHub] Already fetched GitHub Trending today, skipping")
        return 0

    max_repos = gh_config.get('max_repos', 25)
    since = gh_config.get('since', 'weekly')
    fetching_config = config.get('fetching', {})
    max_input_chars = fetching_config.get('max_input_chars', 80000)
    
    # 初始化统计
    if fetch_stats is None:
        fetch_stats = FetchStats()
    
    # 获取或创建 GitHub Trending 虚拟 RSS 源
    try:
        gh_source_id = user_db.get_or_create_rss_source(
            url=f'https://github.com/trending?since={since}',
            name='GitHub Trending',
            description=f'GitHub Trending repositories ({since})',
            category='opensource'
        )
    except Exception as e:
        logging.error(f"[GitHub] Database error creating GitHub Trending source: {e}", exc_info=True)
        return 0
    
    if not gh_source_id:
        logging.error("[GitHub] Failed to create/get GitHub Trending RSS source record")
        return 0
    
    # === Step 1: 抓取 Trending 页面 ===
    trending_url = f'https://github.com/trending?since={since}'
    logging.info(f"[GitHub] Fetching trending page: {trending_url}")
    
    try:
        resp = requests.get(trending_url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"[GitHub] Failed to fetch trending page: {e}")
        fetch_stats.record_failure('https://github.com/trending', 'GitHub Trending', 'page_fetch_failed')
        return 0
    
    repos = _parse_github_trending_page(resp.text)
    if not repos:
        logging.warning("[GitHub] No repos parsed from trending page")
        fetch_stats.record_failure('https://github.com/trending', 'GitHub Trending', 'parse_failed')
        return 0
    
    # 限制处理数量
    repos = repos[:max_repos]
    logging.info(f"[GitHub] Parsed {len(repos)} trending repos")
    
    # === Step 2: 逐个处理 ===
    import re as _re
    _star_prefix_re = _re.compile(r'^⭐ [\d,]+ \| ')
    processed = 0
    refreshed = 0
    skipped_non_ai = 0
    skipped_no_readme = 0
    skipped_llm_failed = 0

    for rank, repo_info in enumerate(repos, 1):
        owner = repo_info['owner']
        repo_name = repo_info['repo']
        repo_url = repo_info['url']
        description = repo_info['description']
        stars = repo_info['stars']

        logging.info(f"[GitHub] [{rank}/{len(repos)}] Processing: {owner}/{repo_name} (⭐{stars:,})")

        # 复用已有记录：URL 命中则只刷新 star/排名/时间，跳过 LLM 流程
        existing = user_db.get_article_by_url(repo_url)
        if existing and existing.get('category') == 'opensource':
            importance_score = max(1, 101 - rank)
            base_en = _star_prefix_re.sub('', existing.get('summary_en') or '')
            base_zh = _star_prefix_re.sub('', existing.get('summary_zh') or '')
            new_summary_en = f"⭐ {stars:,} | {base_en}" if base_en else ''
            new_summary_zh = f"⭐ {stars:,} | {base_zh}" if base_zh else ''
            user_db.update_article_refresh(
                article_id=existing['id'],
                published_at=datetime.now(timezone.utc).isoformat(),
                importance_score=importance_score,
                summary_en=new_summary_en,
                summary_zh=new_summary_zh,
            )
            refreshed += 1
            logging.info(f"[GitHub] ↻ Refreshed: {owner}/{repo_name} (rank={rank}, ⭐{stars:,})")
            continue

        # 获取 README
        readme_text = _fetch_github_readme(owner, repo_name)
        
        if not readme_text and not description:
            logging.info(f"[GitHub] No README or description for {owner}/{repo_name}, skipping")
            skipped_no_readme += 1
            fetch_stats.record_skip('https://github.com/trending', 'GitHub Trending', 'no_readme')
            continue
        
        # 组合内容：描述 + README（截断超长内容）
        content_parts = []
        content_parts.append(f"Project: {owner}/{repo_name}")
        content_parts.append(f"Stars: {stars:,}")
        if repo_info['language']:
            content_parts.append(f"Language: {repo_info['language']}")
        if description:
            content_parts.append(f"Description: {description}")
        if readme_text:
            content_parts.append(f"\n--- README ---\n{readme_text}")
        
        full_text = '\n'.join(content_parts)
        if len(full_text) > max_input_chars:
            full_text = full_text[:max_input_chars]
        
        # LLM 摘要 + AI 相关性判断
        title = f"{owner}/{repo_name}: {description[:80]}" if description else f"{owner}/{repo_name}"
        
        try:
            summary_en, summary_zh, long_summary_en, long_summary_zh, \
                ai_importance, tags, title_en, title_zh, china_blocked = \
                ai_engine.summarize_content(full_text, title=title, category='opensource')
        except Exception as e:
            logging.error(f"[GitHub] LLM summarization failed for {owner}/{repo_name}: {e}")
            skipped_llm_failed += 1
            fetch_stats.record_failure('https://github.com/trending', 'GitHub Trending', 'summarize_failed')
            continue
            
        if china_blocked:
            logging.warning(f"[GitHub] China compliance blocked: {owner}/{repo_name}")
            fetch_stats.record_skip('https://github.com/trending', 'GitHub Trending', 'china_blocked')
            continue

        # 阿里云内容安全二次过滤
        from compliance_filter import check_summary
        _safe, _labels = check_summary(title_en, title_zh, summary_en, summary_zh,
                                       long_summary_en, long_summary_zh)
        if not _safe:
            logging.warning(f"[GitHub] Aliyun Green blocked [{_labels}]: {owner}/{repo_name}")
            fetch_stats.record_skip('https://github.com/trending', 'GitHub Trending', 'aliyun_green_blocked')
            continue

        if not summary_en and not summary_zh:
            logging.warning(f"[GitHub] Empty summary for {owner}/{repo_name}")
            skipped_llm_failed += 1
            fetch_stats.record_failure('https://github.com/trending', 'GitHub Trending', 'summarize_failed')
            continue
        
        # AI 相关性过滤 (prompt 中: 非AI项目 importance < 60)
        if ai_importance < 60:
            logging.info(f"[GitHub] Non-AI project (score={ai_importance}): {owner}/{repo_name}, skipping")
            skipped_non_ai += 1
            fetch_stats.record_skip('https://github.com/trending', 'GitHub Trending', 'non_ai_project')
            continue
        
        # 覆写 importance_score: 用 trending 排名（越前越高）
        # rank 1 → 100, rank 25 → 76
        importance_score = max(1, 101 - rank)
        
        # 入库
        article_data = {
            'rss_source_id': gh_source_id,
            'title': title,
            'title_en': title_en,
            'title_zh': title_zh,
            'url': repo_url,
            'summary_en': f"⭐ {stars:,} | {summary_en}" if summary_en else summary_en,
            'summary_zh': f"⭐ {stars:,} | {summary_zh}" if summary_zh else summary_zh,
            'long_summary_en': long_summary_en,
            'long_summary_zh': long_summary_zh,
            'category': 'opensource',
            'importance_score': importance_score,
            'tags': tags,
            'content_hash': None,
            'published_at': datetime.now(timezone.utc).isoformat(),
        }
        article_id = user_db.add_article(article_data)
        
        if article_id:
            vector_store.add_article(
                article_id=article_id,
                summary=summary_en or summary_zh,
                tags=tags,
                source='GitHub Trending',
                category='opensource'
            )
            try:
                from knowledge_ingest import ingest_async
                ingest_async(article_id, {**article_data, 'source': 'GitHub Trending'}, full_text=full_text, source_name='GitHub Trending')
            except Exception as _e:
                logging.warning(f"KB ingest_async dispatch failed: {_e}")
            processed += 1
            
            llm_input_chars = min(len(full_text), max_input_chars)
            llm_output_chars = len(summary_en or '') + len(summary_zh or '') + \
                               len(long_summary_en or '') + len(long_summary_zh or '') + \
                               len(title_en or '') + len(title_zh or '')
            fetch_stats.record_success('https://github.com/trending', 'GitHub Trending', llm_input_chars, llm_output_chars)
            logging.info(f"[GitHub] ✓ Stored: {owner}/{repo_name} (rank={rank}, score={importance_score})")
        
        # 适当延迟避免 GitHub API 限流
        time.sleep(1)
    
    # === Step 3: 清理掉榜项目（不在本次榜单中的旧 opensource 文章） ===
    current_urls = [r['url'] for r in repos]
    stale_ids = user_db.delete_articles_by_category_excluding_urls('opensource', current_urls)
    if stale_ids:
        vector_store.delete_articles(stale_ids)
        logging.info(f"[GitHub] Cleared {len(stale_ids)} stale opensource articles + vectors")

    logging.info(f"[GitHub] Pipeline finished. Processed={processed}, refreshed={refreshed}, "
                 f"stale_cleared={len(stale_ids)}, "
                 f"non_ai_skipped={skipped_non_ai}, no_readme={skipped_no_readme}, "
                 f"llm_failed={skipped_llm_failed}")
    return processed + refreshed

