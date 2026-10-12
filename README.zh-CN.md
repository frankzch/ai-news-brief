# AI News Brief

[English](README.md) | **中文**

开源、可自建的 **AI 新闻聚合器**：把 370+ 个信息源——AI 大厂博客、Hacker News、Reddit、X 上约 310 位 AI 从业者、13 个 YouTube 频道（基于字幕）和 GitHub Trending——整理成去重后的**每日 AI 资讯摘要**，中英双语。它是 [inbrief.info](https://inbrief.info) 背后的引擎；只想看摘要的话，直接用网站或 [Agent Skill](https://github.com/frankzch/ai-news-skill)，不必自己部署。

它持续从 RSS、Hacker News、Reddit、X（Twitter）、YouTube 和 GitHub Trending 拉取 AI 相关内容，逐条经过 LLM 流水线处理——相关性过滤、中英双语摘要、标签提取、重要性评分——再做两级去重，最终把精选结果写入 PostgreSQL。

https://github.com/user-attachments/assets/4c2a209e-46dd-422a-ae8f-82d92c73f68c

```
调度器 (main.py)
  └─ pipeline_runner ─ 采集器 (RSS / HN / Reddit / X / YouTube / GitHub Trending)
       └─ content_processor  (trafilatura + curl_cffi + DrissionPage 多级回退)
            └─ ai_engine     (LLM：过滤 / 摘要 / 打标 / 评分)
                 └─ 去重      (SimHash 快筛 + pgvector 语义去重)
                      └─ PostgreSQL (articles 表，schema 自动创建)
```

## 功能特性

- **多源采集器** — RSS/Atom、Hugging Face 每周论文榜、Hacker News（含评论区）、Reddit（subreddit 热帖或关键词搜索）、X.com 关键词搜索、YouTube（含字幕提取）、GitHub Trending。
- **抗反爬抓取** — 分层策略：`curl_cffi` TLS 指纹伪装 → DrissionPage 真实浏览器 → httpx；Reddit / X / YouTube 使用 Playwright 持久化登录 profile。
- **LLM 精选** — 按类目（新闻 / 社媒 / 视频 / 开源）使用不同提示词，产出双语摘要、标签和重要性分数；兼容任意 OpenAI 格式 API（默认 DeepSeek）。
- **两级去重** — SimHash 指纹快筛 + pgvector 余弦相似度（近 24 小时文章使用更严阈值）。
- **互动门槛与延迟重扫** — 低热度帖子先记录、延后复查，避免反复抓取浪费配额。
- **可选内容审核** — 入库前经阿里云内容安全（Green）文本审核（密钥留空则跳过）。

## 信息源目录

[inbrief.info](https://inbrief.info) 生产实例目前追踪 **370+ 个信息源**，分四个类目。引擎本身不预置任何源——可以参考这份目录，用 `admin_rss.py` 添加你需要的部分。

### 📰 新闻与博客 — 16 个 RSS 源

| 来源 | Feed |
|---|---|
| OpenAI 博客 | `https://openai.com/news/rss.xml` |
| Google DeepMind 博客 | `https://deepmind.google/blog/rss.xml` |
| Google Research 博客 | `https://research.google/blog/rss/` |
| Apple 机器学习 | `https://machinelearning.apple.com/rss.xml` |
| Microsoft AI 博客 | `https://blogs.microsoft.com/ai/feed/` |
| Nvidia 深度学习博客 | `https://blogs.nvidia.com/blog/category/deep-learning/feed/` |
| Nvidia 开发者博客 | `https://developer.nvidia.com/blog/feed/` |
| Hugging Face 博客 | `https://huggingface.co/blog/feed.xml` |
| HF Daily Papers（社区投票精选论文，链接指向 arXiv） | `https://huggingface.co/api/daily_papers` |
| TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` |
| The Verge | `https://www.theverge.com/rss/index.xml` |
| MIT 科技评论 AI | `https://www.technologyreview.com/topic/artificial-intelligence/feed/` |
| VentureBeat AI | `https://venturebeat.com/category/ai/feed` |
| MarkTechPost | `https://www.marktechpost.com/feed/` |
| AI News（artificialintelligence-news.com） | `https://www.artificialintelligence-news.com/feed/` |
| Machine Learning Mastery | `https://machinelearningmastery.com/feed/` |


### 💬 社媒讨论 — Hacker News、16 个 Reddit 源、320+ 个 X 源

**Hacker News** — 首页 `https://news.ycombinator.com/rss`，抓取完整评论区，按点赞/评论数设互动门槛。

**Reddit — 11 个 subreddit**（热帖）：r/OpenAI、r/artificial、r/MachineLearning、r/ChatGPT、r/ClaudeAI、r/GeminiAI、r/DeepSeek、r/PromptEngineering、r/ArtificialInteligence、r/openclaw、r/AIToolTesting

**Reddit — 5 个关键词搜索**：`llm`、`codex`、`prompt ai`、`agent ai`、`skill ai`

**X.com — 约 310 个 AI 账号**：放在一个 X List 里按一条时间线读取，List 成员的转发也计入热度信号。初始名单是下面 35 位 KOL 加上 AINews 日报历史上引用最多的账号，之后自动扩充：帖子反复获得高分的作者会被自动加入 List。初始 KOL：

| | | | |
|---|---|---|---|
| Sam Altman ([@sama](https://x.com/sama)) | Andrej Karpathy ([@karpathy](https://x.com/karpathy)) | Yann LeCun ([@ylecun](https://x.com/ylecun)) | Demis Hassabis ([@demishassabis](https://x.com/demishassabis)) |
| 李飞飞 ([@drfeifei](https://x.com/drfeifei)) | François Chollet ([@fchollet](https://x.com/fchollet)) | John Carmack ([@ID_AA_Carmack](https://x.com/ID_AA_Carmack)) | Lilian Weng ([@lilianweng](https://x.com/lilianweng)) |
| Amanda Askell ([@AmandaAskell](https://x.com/AmandaAskell)) | Alex Albert ([@alexalbert__](https://x.com/alexalbert__)) | Boris Cherny ([@bcherny](https://x.com/bcherny)) | Cat Wu ([@_catwu](https://x.com/_catwu)) |
| Simon Willison ([@simonw](https://x.com/simonw)) | swyx ([@swyx](https://x.com/swyx)) | Riley Goodside ([@goodside](https://x.com/goodside)) | Jeremy Howard ([@jeremyphoward](https://x.com/jeremyphoward)) |
| Guillermo Rauch ([@rauchg](https://x.com/rauchg)) | Amjad Masad ([@amasad](https://x.com/amasad)) | Aaron Levie ([@levie](https://x.com/levie)) | Garry Tan ([@garrytan](https://x.com/garrytan)) |
| Kevin Weil ([@kevinweil](https://x.com/kevinweil)) | Peter Steinberger ([@steipete](https://x.com/steipete)) | Peter Yang ([@petergyang](https://x.com/petergyang)) | Dan Shipper ([@danshipper](https://x.com/danshipper)) |
| Matt Turck ([@mattturck](https://x.com/mattturck)) | Nan Yu ([@thenanyu](https://x.com/thenanyu)) | Nikunj Kothari ([@nikunj](https://x.com/nikunj)) | Josh Woodward ([@joshwoodward](https://x.com/joshwoodward)) |
| Ryo Lu ([@ryolu_](https://x.com/ryolu_)) | Thariq ([@trq212](https://x.com/trq212)) | Aditya Agarwal ([@adityaag](https://x.com/adityaag)) | Madhu Guru ([@realmadhuguru](https://x.com/realmadhuguru)) |
| Claude ([@claudeai](https://x.com/claudeai)) | ClaudeDevs ([@ClaudeDevs](https://x.com/ClaudeDevs)) | Google Labs ([@GoogleLabs](https://x.com/GoogleLabs)) | |

**X.com — 2 个全站高赞搜索**（AI 关键词，英文 `min_faves:1000`、中文 `min_faves:200`），List 之外账号的爆款 AI 帖也不会漏掉。

*（X List 与全站高赞搜索目前只在生产实例运行；本开源版本采集单个账号时间线与关键词搜索。）*

**X.com — 12 个关键词搜索**：`AI`、`Anthropic`、`OpenAI`、`ChatGPT`、`Gemini`、`LLM`、`claude code`、`codex`、`OpenClaw`、`prompt ai`、`agent ai`、`skill ai`


### 🎬 视频 — 13 个 YouTube 频道（含字幕提取）

| 频道 | 定位 |
|---|---|
| [Lex Fridman](https://www.youtube.com/@lexfridman) | 长篇 AI 访谈 |
| [Dwarkesh Patel](https://www.youtube.com/@DwarkeshPatel) | AI 研究者深度访谈 |
| [Two Minute Papers](https://www.youtube.com/@TwoMinutePapers) | 论文速讲 |
| [Yannic Kilcher](https://www.youtube.com/@YannicKilcher) | 论文深度解读 |
| [Fireship](https://www.youtube.com/@Fireship) | 100 秒开发者快讯 |
| [Matt Wolfe](https://www.youtube.com/@mreflow) | AI 工具与资讯盘点 |
| [Wes Roth](https://www.youtube.com/@WesRoth) | AI 新闻评论 |
| [Latent Space](https://www.youtube.com/@LatentSpacePod) | AI 工程播客 |
| [No Priors](https://www.youtube.com/@NoPriorsPodcast) | AI 创始人与投资人 |
| [Sequoia Capital](https://www.youtube.com/@sequoiacapital) | Training Data 播客 |
| [Redpoint AI](https://www.youtube.com/@RedpointAI) | Unsupervised Learning 播客 |
| [Every Inc](https://www.youtube.com/@EveryInc) | AI 与工作方式 |
| [Data Driven NYC](https://www.youtube.com/@DataDrivenNYC) | 数据/AI 演讲 |


### 🚀 开源 — GitHub Trending

每周 [GitHub Trending](https://github.com/trending?since=weekly) 仓库（默认前 25 个），基于 README 和仓库元数据生成摘要。在 `config.yaml` 的 `fetching.github_trending` 中配置，无需添加源条目。


## 环境要求

- Python 3.10+
- 带 [pgvector](https://github.com/pgvector/pgvector) 扩展的 PostgreSQL（Supabase 免费项目开箱即用）
- 任意 OpenAI 兼容的 LLM API key

## 快速开始

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # 填入 LLM key 和 Postgres 连接串
```

在数据库中启用一次 pgvector：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

所有表在首次运行时自动创建。

添加信息源（存储在 `rss_sources` 表中）：

```bash
python admin_rss.py add https://openai.com/news/rss.xml "OpenAI Blog" --category news
python admin_rss.py add https://www.reddit.com/r/LocalLLaMA/ "r/LocalLLaMA" --category discussion
python admin_rss.py list
```

源类型按 URL 自动路由：`reddit.com/r/<sub>` → subreddit 热帖；`reddit.com` + 以 `keyword ` 开头的描述 → Reddit 关键词搜索；`x.com` → X 关键词搜索；YouTube 频道 feed → 字幕流水线；其余 → RSS/Atom。GitHub Trending 在 `config.yaml` 中开启，无需添加源。

运行：

```bash
python main.py --now   # 立即执行一轮采集，适合首次测试
python main.py         # 调度模式：每 schedule.interval_hours 小时执行一轮
```

模型、超时、互动门槛、各平台配额与数据保留策略均在 [config.yaml](config.yaml) 中配置。

## 用 AI Agent 直接查询 —— 无需自建

不想自己跑这套流水线?同一份精选信息流还提供一个 **agent skill**,直连托管的 [inbrief.info](https://inbrief.info) API —— 把它装进 Claude Code / Codex / OpenClaw / Antigravity,用自然语言提问即可。无需密钥、无需抓取,开箱即用。

```bash
# Claude Code（其他 agent 改目标目录即可，如 ~/.codex/skills、~/.agents/skills）
git clone https://github.com/frankzch/ai-news-skill.git ~/.claude/skills/ai-news-skill
```

Agent 会把你的意图翻译成精确的过滤条件（类目、来源、时间范围、条数、摘要长度、语言）：

- *"过去 5 天头部 KOL 发布的 AI 视频，排除 Fireship。"*
- *"今天的 AI 新闻，但去掉 TechReview。"*
- *"过去 3 天 Reddit 的 AI 讨论，要长摘要。"*

访客每次请求最多返回 3 条；在 [inbrief.info](https://inbrief.info) 免费注册可解锁更多。完整说明见 **[github.com/frankzch/ai-news-skill](https://github.com/frankzch/ai-news-skill)**。

## 说明

- Reddit / X / YouTube 采集通过 Playwright 驱动真实 Chromium。首次运行可能需要在弹出的浏览器窗口中手动登录一次；会话持久化在 `data/playwright_profile`（已被 git 忽略，仅保存在本机）。
- 摘要按双语存储（`*_en` / `*_zh` 字段），只需要单语言时忽略另一列即可。
- `data/` 目录存放运行时状态（浏览器 profile、cookies、每日标志），不会被提交。

## 常见问题

**和 RSS 阅读器有什么区别？**
RSS 阅读器会原样列出每个订阅源的每一条。本流水线还会抓取 Reddit、X、Hacker News 评论区和 YouTube 字幕，剔除离题和低热度内容，把不同来源里的同一条新闻合并去重，并为每条写出摘要和重要性评分。

**需要什么大模型？**
任意 OpenAI 兼容接口，默认 DeepSeek。在 `.env` / [config.yaml](config.yaml) 里配置模型和密钥。

**不自己部署能看到这些摘要吗？**
能。同一份内容可在 [inbrief.info](https://inbrief.info) 免费浏览，也可以用 [Agent Skill](https://github.com/frankzch/ai-news-skill) 在 Claude Code / Codex 里直接查询。

## 许可证

[MIT](LICENSE)
