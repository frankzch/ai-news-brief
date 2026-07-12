# AI News Brief

**English** | [中文](README.zh-CN.md)

The AI news collection and curation pipeline that powers [inbrief.info](https://inbrief.info).



https://github.com/user-attachments/assets/4c2a209e-46dd-422a-ae8f-82d92c73f68c



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

## Source catalog

The production instance at [inbrief.info](https://inbrief.info) currently tracks **94 sources** across four categories. The engine ships with an empty source table — use this catalog as a starting point and add the ones you want via `admin_rss.py`.

### 📰 News & blogs — 16 RSS feeds

| Source | Feed |
|---|---|
| OpenAI Blog | `https://openai.com/news/rss.xml` |
| Google DeepMind Blog | `https://deepmind.google/blog/rss.xml` |
| Google Research Blog | `https://research.google/blog/rss/` |
| Apple Machine Learning | `https://machinelearning.apple.com/rss.xml` |
| Microsoft AI Blog | `https://blogs.microsoft.com/ai/feed/` |
| Nvidia Deep Learning Blog | `https://blogs.nvidia.com/blog/category/deep-learning/feed/` |
| Nvidia Developer Blog | `https://developer.nvidia.com/blog/feed/` |
| Hugging Face Blog | `https://huggingface.co/blog/feed.xml` |
| HF Daily Papers (community-voted, links to arXiv) | `https://huggingface.co/api/daily_papers` |
| TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` |
| The Verge | `https://www.theverge.com/rss/index.xml` |
| MIT Technology Review AI | `https://www.technologyreview.com/topic/artificial-intelligence/feed/` |
| VentureBeat AI | `https://venturebeat.com/category/ai/feed` |
| MarkTechPost | `https://www.marktechpost.com/feed/` |
| AI News (artificialintelligence-news.com) | `https://www.artificialintelligence-news.com/feed/` |
| Machine Learning Mastery | `https://machinelearningmastery.com/feed/` |


### 💬 Discussion — Hacker News, 16 Reddit sources, 47 X sources

**Hacker News** — front page via `https://news.ycombinator.com/rss`, with full comment-thread extraction and engagement gates (min upvotes / comments).

**Reddit — 11 subreddits** (hot posts): r/OpenAI, r/artificial, r/MachineLearning, r/ChatGPT, r/ClaudeAI, r/GeminiAI, r/DeepSeek, r/PromptEngineering, r/ArtificialInteligence, r/openclaw, r/AIToolTesting

**Reddit — 5 keyword searches**: `llm`, `codex`, `prompt ai`, `agent ai`, `skill ai`

**X.com — 35 KOL accounts** (high-engagement posts from their timelines):

| | | | |
|---|---|---|---|
| Sam Altman ([@sama](https://x.com/sama)) | Andrej Karpathy ([@karpathy](https://x.com/karpathy)) | Yann LeCun ([@ylecun](https://x.com/ylecun)) | Demis Hassabis ([@demishassabis](https://x.com/demishassabis)) |
| Fei-Fei Li ([@drfeifei](https://x.com/drfeifei)) | François Chollet ([@fchollet](https://x.com/fchollet)) | John Carmack ([@ID_AA_Carmack](https://x.com/ID_AA_Carmack)) | Lilian Weng ([@lilianweng](https://x.com/lilianweng)) |
| Amanda Askell ([@AmandaAskell](https://x.com/AmandaAskell)) | Alex Albert ([@alexalbert__](https://x.com/alexalbert__)) | Boris Cherny ([@bcherny](https://x.com/bcherny)) | Cat Wu ([@_catwu](https://x.com/_catwu)) |
| Simon Willison ([@simonw](https://x.com/simonw)) | swyx ([@swyx](https://x.com/swyx)) | Riley Goodside ([@goodside](https://x.com/goodside)) | Jeremy Howard ([@jeremyphoward](https://x.com/jeremyphoward)) |
| Guillermo Rauch ([@rauchg](https://x.com/rauchg)) | Amjad Masad ([@amasad](https://x.com/amasad)) | Aaron Levie ([@levie](https://x.com/levie)) | Garry Tan ([@garrytan](https://x.com/garrytan)) |
| Kevin Weil ([@kevinweil](https://x.com/kevinweil)) | Peter Steinberger ([@steipete](https://x.com/steipete)) | Peter Yang ([@petergyang](https://x.com/petergyang)) | Dan Shipper ([@danshipper](https://x.com/danshipper)) |
| Matt Turck ([@mattturck](https://x.com/mattturck)) | Nan Yu ([@thenanyu](https://x.com/thenanyu)) | Nikunj Kothari ([@nikunj](https://x.com/nikunj)) | Josh Woodward ([@joshwoodward](https://x.com/joshwoodward)) |
| Ryo Lu ([@ryolu_](https://x.com/ryolu_)) | Thariq ([@trq212](https://x.com/trq212)) | Aditya Agarwal ([@adityaag](https://x.com/adityaag)) | Madhu Guru ([@realmadhuguru](https://x.com/realmadhuguru)) |
| Claude ([@claudeai](https://x.com/claudeai)) | ClaudeDevs ([@ClaudeDevs](https://x.com/ClaudeDevs)) | Google Labs ([@GoogleLabs](https://x.com/GoogleLabs)) | |

**X.com — 12 keyword searches**: `AI`, `Anthropic`, `OpenAI`, `ChatGPT`, `Gemini`, `LLM`, `claude code`, `codex`, `OpenClaw`, `prompt ai`, `agent ai`, `skill ai`


### 🎬 Video — 13 YouTube channels (with transcript extraction)

| Channel | Focus |
|---|---|
| [Lex Fridman](https://www.youtube.com/@lexfridman) | Long-form AI interviews |
| [Dwarkesh Patel](https://www.youtube.com/@DwarkeshPatel) | Deep interviews with AI researchers |
| [Two Minute Papers](https://www.youtube.com/@TwoMinutePapers) | Paper explainers |
| [Yannic Kilcher](https://www.youtube.com/@YannicKilcher) | Paper deep-dives |
| [Fireship](https://www.youtube.com/@Fireship) | Dev news in 100 seconds |
| [Matt Wolfe](https://www.youtube.com/@mreflow) | AI tools & news roundups |
| [Wes Roth](https://www.youtube.com/@WesRoth) | AI news commentary |
| [Latent Space](https://www.youtube.com/@LatentSpacePod) | AI engineering podcast |
| [No Priors](https://www.youtube.com/@NoPriorsPodcast) | AI founders & investors |
| [Sequoia Capital](https://www.youtube.com/@sequoiacapital) | Training Data podcast |
| [Redpoint AI](https://www.youtube.com/@RedpointAI) | Unsupervised Learning podcast |
| [Every Inc](https://www.youtube.com/@EveryInc) | AI & work essays |
| [Data Driven NYC](https://www.youtube.com/@DataDrivenNYC) | Data/AI talks |


### 🚀 Open source — GitHub Trending

Weekly [GitHub Trending](https://github.com/trending?since=weekly) repositories (top 25 by default), each summarized from its README and repo metadata. Configured in `config.yaml` under `fetching.github_trending` — no source entry needed.


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
