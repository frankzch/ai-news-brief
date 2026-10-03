import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError, getArticle } from "../api/client";
import type { ArticleOut } from "../api/types";
import { localeOf, useLang } from "../i18n/LangContext";
import { pick } from "../i18n/pick";
import { categoryLabel } from "../i18n/strings";
import { Markdown } from "../components/Markdown";
import { RetryError } from "../components/RetryError";
import { messageOf, type Load } from "../lib/load";
import { safeHref } from "../lib/url";

export function ArticleDetail() {
  const { id = "" } = useParams();
  const location = useLocation();
  const { lang, t } = useLang();
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
      <Link to={backTo}>{t.backToArticles}</Link>
    </p>
  );

  if (state.status === "loading") return <p role="status">{t.loadingArticle}</p>;
  if (state.status === "missing") {
    return (
      <section>
        {back}
        <p className="notice">{t.articleNotFound}</p>
      </section>
    );
  }
  if (state.status === "error") {
    return (
      <section>
        {back}
        <RetryError onRetry={() => setAttempt((n) => n + 1)}>{t.couldNotLoadArticle(state.message)}</RetryError>
      </section>
    );
  }

  const a = state.data;
  const published = a.published_at ? new Date(a.published_at) : null;
  const href = safeHref(a.url);
  const title = pick(lang, { en: a.title_en, zh: a.title_zh }, a.title);
  const body =
    pick(lang, { en: a.long_summary_en, zh: a.long_summary_zh }) ??
    pick(lang, { en: a.summary_en, zh: a.summary_zh });
  return (
    <article className="detail">
      {back}
      <h1>{title}</h1>
      <p className="card-meta">
        {a.source_name && <span>{a.source_name}</span>}
        {a.category && <span className="badge">{categoryLabel(t, a.category)}</span>}
        {a.importance_score != null && <span className="importance">{t.importance(a.importance_score)}</span>}
        {published && !Number.isNaN(published.getTime()) && (
          <time dateTime={published.toISOString()}>{published.toLocaleString(localeOf(lang))}</time>
        )}
      </p>
      {body && (
        <div className="detail-body">
          <Markdown text={body} />
        </div>
      )}
      {a.tags.length > 0 && (
        <ul className="chips" aria-label={t.tags}>
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
            {t.openOriginal}
          </a>
        </p>
      )}
    </article>
  );
}
