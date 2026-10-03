import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
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

describe("Browse filters", () => {
  const LIST = { articles: [art("a", "First")], total: 1 };

  it("category tabs filter the list and update the URL", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse();
    await screen.findByText("First");
    const group = screen.getByRole("group", { name: /category/i });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "All",
      "news",
      "discussion",
      "video",
      "opensource",
    ]);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "video" }));
    await waitFor(() => expect(articleCalls(fetchFn)).toContain("/api/articles?category=video&limit=30&offset=0"));
    expect(screen.getByTestId("search")).toHaveTextContent("?category=video");
    expect(screen.getByRole("button", { name: "video" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
  });

  it("tag chips come from /api/tags and clicking filters; clicking again clears", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse();
    const chip = await screen.findByRole("button", { name: /agents/ });
    await userEvent.click(chip);
    await waitFor(() => expect(articleCalls(fetchFn)).toContain("/api/articles?tag=agents&limit=30&offset=0"));
    expect(screen.getByTestId("search")).toHaveTextContent("?tag=agents");
    expect(screen.getByRole("button", { name: /agents/ })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: /agents/ }));
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
  });

  it("shows an active tag from the URL even when it is not in the tag list", async () => {
    stubApi(LIST);
    renderBrowse("/articles?tag=obscure");
    expect(await screen.findByRole("button", { name: /obscure/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("still lists articles when /api/tags fails", async () => {
    stubApi(LIST, (p) => (p === "/api/tags" ? json({ detail: "x" }, 500) : undefined));
    renderBrowse();
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("category, tag and search combine into one request and one URL", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse();
    await userEvent.click(await screen.findByRole("button", { name: "news" }));
    await userEvent.click(await screen.findByRole("button", { name: /llm/ }));
    await userEvent.type(screen.getByRole("searchbox"), "gpt");
    const expected = "/api/articles?category=news&tag=llm&q=gpt&limit=30&offset=0";
    await waitFor(() => expect(articleCalls(fetchFn)).toContain(expected));
    expect(screen.getByTestId("search")).toHaveTextContent("?category=news&tag=llm&q=gpt");
  });

  it("restores filters from a shared URL, including the search box", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse("/articles?category=opensource&tag=llm&q=rust");
    await screen.findByText("First");
    expect(articleCalls(fetchFn)).toEqual(["/api/articles?category=opensource&tag=llm&q=rust&limit=30&offset=0"]);
    expect(screen.getByRole("searchbox")).toHaveValue("rust");
    expect(screen.getByRole("button", { name: "opensource" })).toHaveAttribute("aria-pressed", "true");
  });

  it("ignores an unknown category in the URL", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse("/articles?category=bogus");
    await screen.findByText("First");
    expect(articleCalls(fetchFn)).toEqual(["/api/articles?limit=30&offset=0"]);
  });

  it("debounces search typing into a single request", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse();
    await screen.findByText("First");
    await userEvent.type(screen.getByRole("searchbox"), "abc");
    expect(articleCalls(fetchFn)).toHaveLength(1);
    await waitFor(() => expect(articleCalls(fetchFn)).toHaveLength(2));
    expect(articleCalls(fetchFn)[1]).toContain("q=abc");
  });

  it("Enter commits the search immediately", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse();
    await screen.findByText("First");
    await userEvent.type(screen.getByRole("searchbox"), "xyz{Enter}");
    expect(screen.getByTestId("search")).toHaveTextContent("?q=xyz");
    await waitFor(() => expect(articleCalls(fetchFn).some((p) => p.includes("q=xyz"))).toBe(true));
  });

  it("a category click during the search debounce is not overwritten by the pending search", async () => {
    const fetchFn = stubApi(LIST);
    renderBrowse();
    await screen.findByText("First");
    await userEvent.type(screen.getByRole("searchbox"), "gpt");
    await userEvent.click(screen.getByRole("button", { name: "video" }));
    await waitFor(() => expect(articleCalls(fetchFn).some((p) => p.includes("category=video") && p.includes("q=gpt"))).toBe(true));
    expect(screen.getByTestId("search")).toHaveTextContent("category=video&q=gpt");
  });
});

describe("Browse pagination", () => {
  const PAGED = { articles: [art("a", "First")], total: 65 };

  it("shows no pager for a single page", async () => {
    stubApi({ articles: [art("a", "First")], total: 30 });
    renderBrowse();
    await screen.findByText("First");
    expect(screen.queryByRole("navigation", { name: /pagination/i })).not.toBeInTheDocument();
  });

  it("pages with Next/Prev, keeping page in the URL", async () => {
    const fetchFn = stubApi(PAGED);
    renderBrowse();
    await screen.findByText("First");
    const nav = screen.getByRole("navigation", { name: /pagination/i });
    expect(nav).toHaveTextContent("Page 1 of 3");
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(articleCalls(fetchFn)).toContain("/api/articles?limit=30&offset=30"));
    expect(screen.getByTestId("search")).toHaveTextContent("?page=2");
    expect(screen.getByRole("navigation", { name: /pagination/i })).toHaveTextContent("Page 2 of 3");
    await userEvent.click(screen.getByRole("button", { name: /previous/i }));
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
  });

  it("disables Next on the last page", async () => {
    stubApi(PAGED);
    renderBrowse("/articles?page=3");
    await screen.findByText("First");
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("resets to page 1 when a filter changes", async () => {
    const fetchFn = stubApi(PAGED);
    renderBrowse("/articles?page=2");
    await screen.findByText("First");
    await userEvent.click(screen.getByRole("button", { name: "news" }));
    await waitFor(() => expect(articleCalls(fetchFn)).toContain("/api/articles?category=news&limit=30&offset=0"));
    expect(screen.getByTestId("search")).toHaveTextContent("?category=news");
    expect(screen.getByTestId("search")).not.toHaveTextContent("page");
  });

  it("clamps a page past the end to the last page", async () => {
    const fetchFn = stubApi(PAGED, (p) =>
      p.includes("offset=240") ? json({ articles: [], total: 65 }) : undefined,
    );
    renderBrowse("/articles?page=9");
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent("?page=3"));
    await waitFor(() => expect(articleCalls(fetchFn)).toContain("/api/articles?limit=30&offset=60"));
  });
});

describe("Browse stale results", () => {
  it("does not clamp the page using the previous filters' total (history navigation)", async () => {
    const fetchFn = stubApi({ articles: [art("a", "First")], total: 10 }, (p) =>
      p.includes("offset=30") ? json({ articles: [art("z", "Deep page")], total: 65 }) : undefined,
    );
    function Jump() {
      const navigate = useNavigate();
      return (
        <button type="button" onClick={() => navigate("/articles?page=2")}>
          jump
        </button>
      );
    }
    render(
      <MemoryRouter initialEntries={["/articles?category=news"]}>
        <Browse />
        <Jump />
        <Where />
      </MemoryRouter>,
    );
    await screen.findByText("First");
    await userEvent.click(screen.getByRole("button", { name: "jump" }));
    expect(await screen.findByText("Deep page")).toBeInTheDocument();
    expect(screen.getByTestId("search")).toHaveTextContent("?page=2");
    expect(articleCalls(fetchFn)).not.toContain("/api/articles?limit=30&offset=0");
  });
});
