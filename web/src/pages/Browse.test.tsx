import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { Browse } from "./Browse";
import type { ArticleOut, ArticlesOut } from "../api/types";

function art(id: string, title: string, over: Partial<ArticleOut> = {}): ArticleOut {
  return {
    id,
    title,
    title_en: title,
    title_zh: null,
    summary_en: `Summary of ${title}`,
    summary_zh: null,
    long_summary_en: null,
    long_summary_zh: null,
    importance_score: 5,
    tags: ["llm"],
    category: "news",
    url: `https://example.com/${id}`,
    source_name: "Feed",
    published_at: null,
    ...over,
  };
}

const TAGS = { tags: [{ tag: "llm", count: 9 }, { tag: "agents", count: 4 }] };
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

type Handler = (path: string) => Promise<Response> | undefined;

// Routes /api/articles and /api/tags to canned JSON unless `override` handles the path.
function stubApi(list: ArticlesOut, override: Handler = () => undefined) {
  const fetchFn = vi.fn((path: string) => {
    const o = override(path);
    if (o) return o;
    if (path === "/api/tags") return json(TAGS);
    if (path.startsWith("/api/articles?")) return json(list);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

function Where() {
  const { search } = useLocation();
  return <span data-testid="search">{search}</span>;
}

function renderBrowse(entry = "/articles") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Browse />
      <Where />
    </MemoryRouter>,
  );
}

const articleCalls = (fn: ReturnType<typeof stubApi>) =>
  fn.mock.calls.map((c) => c[0]).filter((p) => p.startsWith("/api/articles?"));

afterEach(() => vi.unstubAllGlobals());

describe("Browse list", () => {
  it("shows loading, then article cards", async () => {
    const fetchFn = stubApi({ articles: [art("a", "First"), art("b", "Second")], total: 2 });
    renderBrowse();
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    expect(await screen.findAllByRole("article")).toHaveLength(2);
    expect(articleCalls(fetchFn)).toEqual(["/api/articles?limit=30&offset=0"]);
  });

  it("shows an empty state", async () => {
    stubApi({ articles: [], total: 0 });
    renderBrowse();
    expect(await screen.findByText(/no articles match/i)).toBeInTheDocument();
  });

  it("shows an error with Retry that refetches", async () => {
    let fail = true;
    const fetchFn = stubApi({ articles: [art("a", "First")], total: 1 }, (p) =>
      fail && p.startsWith("/api/articles?") ? json({ detail: "boom" }, 500) : undefined,
    );
    renderBrowse();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(articleCalls(fetchFn)).toHaveLength(2);
  });
});
