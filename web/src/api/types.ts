// Mirrors api/schemas.py. Keep in sync when the API changes.

export interface DatesOut {
  dates: string[];
  latest: string | null;
}

export interface TagOut {
  tag: string;
  count: number;
}

export interface TagsOut {
  tags: TagOut[];
}

export interface ArchiveArticleOut {
  article_id: string;
  rank: number;
  title: string | null;
  title_en: string | null;
  title_zh: string | null;
  summary_en: string | null;
  summary_zh: string | null;
  long_summary_en: string | null;
  long_summary_zh: string | null;
  importance_score: number | null;
  relevance_score: number | null;
  source: string | null;
  category: string | null;
  published_at: string | null;
  url: string | null;
  labels: string[];
}

export interface DigestOut {
  date: string;
  articles: ArchiveArticleOut[];
}

export interface ArticleOut {
  id: string;
  title: string;
  title_en: string | null;
  title_zh: string | null;
  summary_en: string | null;
  summary_zh: string | null;
  long_summary_en: string | null;
  long_summary_zh: string | null;
  importance_score: number | null;
  tags: string[];
  category: string | null;
  url: string;
  source_name: string | null;
  published_at: string | null;
}

export interface ArticlesOut {
  articles: ArticleOut[];
  total: number;
}
