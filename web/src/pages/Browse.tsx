import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getArticles, getTags } from "../api/client";
import type { ArticlesOut, TagOut } from "../api/types";
import { useLang } from "../i18n/LangContext";
import { ArticleCard, fromArticle } from "../components/ArticleCard";
import { CategoryTabs } from "../components/CategoryTabs";
import { Pagination } from "../components/Pagination";
import { RetryError } from "../components/RetryError";
import { SearchBox } from "../components/SearchBox";
import { TagChips } from "../components/TagChips";
import { parseFilters, toSearchParams, withFilter, PAGE_SIZE, type Filters } from "../lib/filters";
import { messageOf, type Load } from "../lib/load";

export function Browse() {
  const { t } = useLang();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  // Results are tagged with the filters that produced them so a render (or the page clamp below) never
  // pairs one query's results with another's, e.g. after back/forward navigation.
  const [result, setResult] = useState<({ filters: Filters } & Load<ArticlesOut>) | null>(null);
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
    setResult({ filters, status: "loading" });
    getArticles(filters, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ filters, status: "ready", data });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setResult({ filters, status: "error", message: messageOf(err) });
      });
    return () => controller.abort();
  }, [filters, attempt]);

  const list: Load<ArticlesOut> = result && result.filters === filters ? result : { status: "loading" };
  const total = list.status === "ready" ? list.data.total : 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // A shared URL can point past the end (data shrank); land on the last page instead of an empty one.
  useEffect(() => {
    if (list.status === "ready" && total > 0 && filters.page > pages) {
      setParams(toSearchParams({ ...filters, page: pages }), { replace: true });
    }
  }, [list.status, total, pages, filters, setParams]);

  return (
    <section>
      <h1>{t.browseArticles}</h1>
      <SearchBox value={filters.q} onCommit={(q) => update({ q })} />
      <CategoryTabs value={filters.category} onChange={(category) => update({ category })} />
      <TagChips tags={tags} active={filters.tag} onChange={(tag) => update({ tag })} />
      {list.status === "loading" && <p role="status">{t.loadingArticles}</p>}
      {list.status === "error" && (
        <RetryError onRetry={() => setAttempt((n) => n + 1)}>{t.couldNotLoadArticles(list.message)}</RetryError>
      )}
      {list.status === "ready" &&
        (list.data.articles.length === 0 ? (
          <p className="notice">{t.noArticlesMatch}</p>
        ) : (
          <div className="cards">
            {list.data.articles.map((a) => (
              <ArticleCard key={a.id} article={fromArticle(a)} />
            ))}
          </div>
        ))}
      {list.status === "ready" && (
        <Pagination page={filters.page} pages={pages} onChange={(page) => update({ page })} />
      )}
    </section>
  );
}
