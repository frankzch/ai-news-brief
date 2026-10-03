import type { ArchiveArticleOut } from "../api/types";

// Article urls come from scraped feeds, so only plain http(s) links are made clickable.
function safeHref(url: string | null): string | null {
  if (!url) return null;
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

export function ArticleCard({ article }: { article: ArchiveArticleOut }) {
  const title = article.title_en || article.title || "Untitled";
  const href = safeHref(article.url);
  return (
    <article className="card">
      <span className="card-rank" aria-label={`Rank ${article.rank}`}>
        {article.rank}
      </span>
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
          {article.published_at && (
            <time dateTime={article.published_at}>{new Date(article.published_at).toLocaleString()}</time>
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
