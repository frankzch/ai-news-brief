import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError, getArticle } from "../api/client";
import type { ArticleOut } from "../api/types";
import { Markdown } from "../components/Markdown";
import { RetryError } from "../components/RetryError";
import { messageOf, type Load } from "../lib/load";
import { safeHref } from "../lib/url";

export function ArticleDetail() {
  const { id = "" } = useParams();
  const location = useLocation();
  const [state, setState] = useState<Load<ArticleOut> | { status: "missing" }>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    getArticle(id, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) setState({ status: "missing" });
        else setState({ status: "error", message: messageOf(err) });
      });
    return () => controller.abort();
  }, [id, attempt]);

  // Cards pass the list's query string along so Back restores the same filters.
  const from = (location.state as { from?: unknown } | null)?.from;
  const backTo = typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/articles";
  const back = (
    <p>
      <Link to={backTo}>← Back to articles</Link>
    </p>
  );

  if (state.status === "loading") return <p role="status">Loading article…</p>;
  if (state.status === "missing") {
    return (
      <section>
        {back}
        <p className="notice">Article not found.</p>
      </section>
    );
  }
  if (state.status === "error") {
    return (
      <section>
        {back}
        <RetryError onRetry={() => setAttempt((n) => n + 1)}>{`Could not load article: ${state.message}`}</RetryError>
      </section>
    );
  }

  const a = state.data;
  const published = a.published_at ? new Date(a.published_at) : null;
  const href = safeHref(a.url);
  const body = a.long_summary_en || a.summary_en;
  return (
    <article className="detail">
      {back}
      <h1>{a.title_en || a.title}</h1>
      <p className="card-meta">
        {a.source_name && <span>{a.source_name}</span>}
        {a.category && <span className="badge">{a.category}</span>}
        {a.importance_score != null && <span className="importance">Importance {a.importance_score}</span>}
        {published && !Number.isNaN(published.getTime()) && (
          <time dateTime={published.toISOString()}>{published.toLocaleString()}</time>
        )}
      </p>
      {body && (
        <div className="detail-body">
          <Markdown text={body} />
        </div>
      )}
      {a.tags.length > 0 && (
        <ul className="chips" aria-label="Tags">
          {a.tags.map((t) => (
            <li key={t} className="chip">
              {t}
            </li>
          ))}
        </ul>
      )}
      {href && (
        <p>
          <a className="button button-link" href={href} target="_blank" rel="noopener noreferrer">
            Open original
          </a>
        </p>
      )}
    </article>
  );
}
