import { Link, useLocation } from "react-router-dom";
import type { ArchiveArticleOut, ArticleOut } from "../api/types";
import { localeOf, useLang } from "../i18n/LangContext";
import { pick } from "../i18n/pick";
import { categoryLabel } from "../i18n/strings";
import { safeHref } from "../lib/url";
import { InlineMarkdown } from "./Markdown";

// List results use ArticleOut; the card renders the digest shape, so map once here (no rank outside a digest).
export function fromArticle(a: ArticleOut): ArchiveArticleOut {
  return {
    article_id: a.id,
    rank: 0,
    title: a.title,
    title_en: a.title_en,
    title_zh: a.title_zh,
    summary_en: a.summary_en,
    summary_zh: a.summary_zh,
    long_summary_en: a.long_summary_en,
    long_summary_zh: a.long_summary_zh,
    importance_score: a.importance_score,
    relevance_score: null,
    source: a.source_name,
    category: a.category,
    published_at: a.published_at,
    url: a.url,
    labels: a.tags,
  };
}

export function ArticleCard({ article }: { article: ArchiveArticleOut }) {
  const published = article.published_at ? new Date(article.published_at) : null;
  const { lang, t } = useLang();
  const title = pick(lang, { en: article.title_en, zh: article.title_zh }, article.title) ?? t.untitled;
  const summary = pick(lang, { en: article.summary_en, zh: article.summary_zh });
  const href = safeHref(article.url);
  const location = useLocation();
  return (
    <article className="card">
      {article.rank > 0 && (
        <span className="card-rank" aria-label={t.rank(article.rank)}>
          {article.rank}
        </span>
      )}
      <div className="card-body">
        <h2 className="card-title">
          <Link
            to={`/articles/${encodeURIComponent(article.article_id)}`}
            state={{ from: `${location.pathname}${location.search}` }}
          >
            {title}
          </Link>
        </h2>
        {summary && (
          <p className="card-summary">
            <InlineMarkdown text={summary} />
          </p>
        )}
        <p className="card-meta">
          {article.source && <span>{article.source}</span>}
          {href && (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {t.original}
            </a>
          )}
          {article.category && <span className="badge">{categoryLabel(t, article.category)}</span>}
          {article.importance_score != null && (
            <span className="importance">{t.importance(article.importance_score)}</span>
          )}
          {published && !Number.isNaN(published.getTime()) && (
            <time dateTime={published.toISOString()}>{published.toLocaleString(localeOf(lang))}</time>
          )}
        </p>
        {article.labels.length > 0 && (
          <ul className="chips" aria-label={t.tags}>
            {article.labels.map((label) => (
              <li key={label} className="chip">
                {label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}
