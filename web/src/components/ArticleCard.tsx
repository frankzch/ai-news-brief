import type { ArchiveArticleOut, ArticleOut } from "../api/types";
import { safeHref } from "../lib/url";

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
  const title = article.title_en || article.title || "Untitled";
  const href = safeHref(article.url);
  return (
    <article className="card">
      {article.rank > 0 && (
        <span className="card-rank" aria-label={`Rank ${article.rank}`}>
          {article.rank}
        </span>
      )}
      <div className="card-body">
        <h2 className="card-title">
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {title}
            </a>
          ) : (
            title
          )}
        </h2>
        {article.summary_en && <p className="card-summary">{article.summary_en}</p>}
        <p className="card-meta">
          {article.source && <span>{article.source}</span>}
          {article.category && <span className="badge">{article.category}</span>}
          {article.importance_score != null && (
            <span className="importance">Importance {article.importance_score}</span>
          )}
          {published && !Number.isNaN(published.getTime()) && (
            <time dateTime={published.toISOString()}>{published.toLocaleString()}</time>
          )}
        </p>
        {article.labels.length > 0 && (
          <ul className="chips" aria-label="Tags">
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
