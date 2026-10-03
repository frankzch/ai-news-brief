import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { Home } from "./Home";
import type { ArchiveArticleOut, DigestOut } from "../api/types";

function art(id: string, rank: number, title: string): ArchiveArticleOut {
  return {
    article_id: id,
    rank,
    title,
    title_en: title,
    title_zh: null,
    summary_en: `Summary of ${title}`,
    summary_zh: null,
    long_summary_en: null,
    long_summary_zh: null,
    importance_score: 5,
    relevance_score: 0,
    source: "Feed",
    category: "news",
    published_at: null,
    url: `https://example.com/${id}`,
    labels: ["llm"],
  };
}

const DATES = { dates: ["2026-10-03", "2026-10-02"], latest: "2026-10-03" };

const DIGESTS: Record<string, DigestOut> = {
  "2026-10-03": { date: "2026-10-03", articles: [art("b", 2, "Second story"), art("a", 1, "First story")] },
  "2026-10-02": { date: "2026-10-02", articles: [art("c", 1, "Older story")] },
};

// Routes /api/dates and /api/digest/<date> to canned JSON; records requested paths.
function stubApi(overrides: Record<string, () => Promise<Response>> = {}) {
  const fetchFn = vi.fn((path: string) => {
    if (overrides[path]) return overrides[path]();
    if (path === "/api/dates") return Promise.resolve(new Response(JSON.stringify(DATES)));
    const m = /^\/api\/digest\/(.+)$/.exec(path);
    const digest = m && DIGESTS[decodeURIComponent(m[1])];
    if (digest) return Promise.resolve(new Response(JSON.stringify(digest)));
    return Promise.resolve(new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }));
  });
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

function Where() {
  const { search } = useLocation();
  return <span data-testid="search">{search}</span>;
}

function renderHome(entry = "/") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Home />
      <Where />
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Home digest", () => {
  it("shows loading, then the latest digest as cards in rank order", async () => {
    const fetchFn = stubApi();
    renderHome();
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByRole("heading")).toHaveTextContent("First story");
    expect(within(cards[1]).getByRole("heading")).toHaveTextContent("Second story");
    expect(fetchFn.mock.calls.map((c) => c[0])).toEqual(["/api/dates", "/api/digest/2026-10-03"]);
  });

  it("shows an empty state when there are no archives", async () => {
    stubApi({ "/api/dates": () => Promise.resolve(new Response(JSON.stringify({ dates: [], latest: null }))) });
    renderHome();
    expect(await screen.findByText(/no digests yet/i)).toBeInTheDocument();
  });

  it("lists only available dates in the picker, defaulting to the latest", async () => {
    stubApi();
    renderHome();
    const picker = await screen.findByLabelText(/date/i);
    const options = within(picker).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["2026-10-03", "2026-10-02"]);
    expect(picker).toHaveValue("2026-10-03");
  });

  it("switching dates fetches that digest and updates ?date=", async () => {
    const fetchFn = stubApi();
    renderHome();
    const picker = await screen.findByLabelText(/date/i);
    await screen.findByText("First story");
    await userEvent.selectOptions(picker, "2026-10-02");
    expect(await screen.findByText("Older story")).toBeInTheDocument();
    expect(screen.queryByText("First story")).not.toBeInTheDocument();
    expect(screen.getByTestId("search")).toHaveTextContent("?date=2026-10-02");
    expect(fetchFn.mock.calls.map((c) => c[0])).toContain("/api/digest/2026-10-02");
  });

  it("honours a valid ?date= on load", async () => {
    const fetchFn = stubApi();
    renderHome("/?date=2026-10-02");
    expect(await screen.findByText("Older story")).toBeInTheDocument();
    expect(fetchFn.mock.calls.map((c) => c[0])).not.toContain("/api/digest/2026-10-03");
  });

  it("falls back to the latest date for an unknown ?date=", async () => {
    stubApi();
    renderHome("/?date=1999-01-01");
    expect(await screen.findByText("First story")).toBeInTheDocument();
    expect(screen.getByLabelText(/date/i)).toHaveValue("2026-10-03");
    expect(screen.getByTestId("search")).toHaveTextContent("?date=2026-10-03");
  });
});
