import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getArticles } from "../api/client";
import type { ArticlesOut } from "../api/types";
import { ArticleCard, fromArticle } from "../components/ArticleCard";
import { RetryError } from "../components/RetryError";
import { parseFilters } from "../lib/filters";
import { messageOf, type Load } from "../lib/load";

export function Browse() {
  const [params] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const [list, setList] = useState<Load<ArticlesOut>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setList({ status: "loading" });
    getArticles(filters, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setList({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setList({ status: "error", message: messageOf(err) });
      });
    return () => controller.abort();
  }, [filters, attempt]);

  return (
    <section>
      <h1>Browse articles</h1>
      {list.status === "loading" && <p role="status">Loading articles…</p>}
      {list.status === "error" && (
        <RetryError onRetry={() => setAttempt((n) => n + 1)}>{`Could not load articles: ${list.message}`}</RetryError>
      )}
      {list.status === "ready" &&
        (list.data.articles.length === 0 ? (
          <p className="notice">No articles match these filters.</p>
        ) : (
          <div className="cards">
            {list.data.articles.map((a) => (
              <ArticleCard key={a.id} article={fromArticle(a)} />
            ))}
          </div>
        ))}
    </section>
  );
}
