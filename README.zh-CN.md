# AI News Brief

[English](README.md) | **中文**

为 [inbrief.info](https://inbrief.info) 提供动力的 AI 资讯采集与精选流水线。

它持续从 RSS、Hacker News、Reddit、X（Twitter）、YouTube 和 GitHub Trending 拉取 AI 相关内容，逐条经过 LLM 流水线处理——相关性过滤、中英双语摘要、标签提取、重要性评分——再做两级去重，最终把精选结果写入 PostgreSQL。

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

## 说明

- Reddit / X / YouTube 采集通过 Playwright 驱动真实 Chromium。首次运行可能需要在弹出的浏览器窗口中手动登录一次；会话持久化在 `data/playwright_profile`（已被 git 忽略，仅保存在本机）。
- 摘要按双语存储（`*_en` / `*_zh` 字段），只需要单语言时忽略另一列即可。
- `data/` 目录存放运行时状态（浏览器 profile、cookies、每日标志），不会被提交。

## 许可证

[MIT](LICENSE)
