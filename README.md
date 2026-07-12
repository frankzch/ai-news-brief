# InBrief Engine

**English** | [中文](README.zh-CN.md)

The AI news collection and curation pipeline that powers [inbrief.info](https://inbrief.info).

It continuously pulls AI-related content from RSS feeds, Hacker News, Reddit, X (Twitter), YouTube and GitHub Trending, then runs every item through an LLM pipeline — relevance filtering, bilingual (EN/ZH) summarization, tag extraction, importance scoring — deduplicates it in two stages, and stores the curated result in PostgreSQL.

```
scheduler (main.py)
  └─ pipeline_runner ─ fetchers (RSS / HN / Reddit / X / YouTube / GitHub Trending)
       └─ content_processor  (trafilatura + curl_cffi + DrissionPage fallbacks)
            └─ ai_engine     (LLM: filter / summarize / tag / score)
                 └─ dedup    (SimHash quick screen + pgvector semantic)
                      └─ PostgreSQL (articles, auto-created schema)
```

## Features

- **Multi-source fetchers** — RSS/Atom, Hugging Face weekly papers, Hacker News (with comment threads), Reddit (subreddit hot or keyword search), X.com keyword search, YouTube (with transcript extraction), GitHub Trending.
- **Anti-bot resilient scraping** — layered strategy: `curl_cffi` TLS impersonation → DrissionPage real browser → httpx; Playwright with a persistent login profile for Reddit / X / YouTube.
- **LLM curation** — per-category prompts (news / discussion / video / opensource) produce bilingual summaries, tags and an importance score; any OpenAI-compatible API works (DeepSeek by default).
- **Two-stage deduplication** — SimHash fingerprint quick screening, then pgvector cosine similarity with dynamic thresholds (stricter for last-24h articles).
- **Engagement gates & delayed re-scan** — low-traction posts are recorded and re-checked later instead of being fetched repeatedly.
- **Optional content moderation** — Aliyun Green text moderation before storage (skipped when keys are absent).

## Requirements

- Python 3.10+
- PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension (a free Supabase project works out of the box)
- An OpenAI-compatible LLM API key

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # fill in LLM key + Postgres URL
```

Enable pgvector once in your database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

All tables are created automatically on first run.

Add some sources (they live in the `rss_sources` table):

```bash
python admin_rss.py add https://openai.com/news/rss.xml "OpenAI Blog" --category news
python admin_rss.py add https://www.reddit.com/r/LocalLLaMA/ "r/LocalLLaMA" --category discussion
python admin_rss.py list
```

Source routing is inferred from the URL: `reddit.com/r/<sub>` → subreddit hot posts, `reddit.com` + a description starting with `keyword ` → Reddit keyword search, `x.com` → X keyword search, YouTube channel feeds → transcript pipeline, everything else → RSS/Atom. GitHub Trending is enabled in `config.yaml` and needs no source entry.

Run:

```bash
python main.py --now   # single fetch round, good for a first test
python main.py         # scheduler mode: runs every schedule.interval_hours
```

Models, timeouts, engagement thresholds, per-platform quotas and retention are all in [config.yaml](config.yaml).

## Notes

- Reddit / X / YouTube fetching drives a real Chromium via Playwright. On first run you may be prompted to log in once in the opened browser window; the session persists in `data/playwright_profile` (git-ignored, stays on your machine).
- Summaries are stored bilingually (`*_en` / `*_zh` columns). If you only need one language you can simply ignore the other.
- The `data/` directory holds runtime state (browser profile, cookies, daily flags) and is never committed.

## License

[MIT](LICENSE)
