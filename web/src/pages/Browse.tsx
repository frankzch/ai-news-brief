import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getArticles, getTags } from "../api/client";
import type { ArticlesOut, TagOut } from "../api/types";
import { ArticleCard, fromArticle } from "../components/ArticleCard";
import { CategoryTabs } from "../components/CategoryTabs";
import { RetryError } from "../components/RetryError";
import { SearchBox } from "../components/SearchBox";
import { TagChips } from "../components/TagChips";
import { parseFilters, toSearchParams, withFilter, type Filters } from "../lib/filters";
import { messageOf, type Load } from "../lib/load";

export function Browse() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const [list, setList] = useState<Load<ArticlesOut>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [tags, setTags] = useState<TagOut[]>([]);

  // Tag chips are an enhancement: if /api/tags fails the list still works.
  useEffect(() => {
    const controller = new AbortController();
    getTags(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setTags(data.tags);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);

  function update(change: Partial<Filters>) {
    setParams(toSearchParams(withFilter(filters, change)));
  }

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
      <SearchBox value={filters.q} onCommit={(q) => update({ q })} />
      <CategoryTabs value={filters.category} onChange={(category) => update({ category })} />
      <TagChips tags={tags} active={filters.tag} onChange={(tag) => update({ tag })} />
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
