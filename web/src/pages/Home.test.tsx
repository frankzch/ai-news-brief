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

  it("shows an empty state for a date with no articles", async () => {
    stubApi({
      "/api/digest/2026-10-03": () =>
        Promise.resolve(new Response(JSON.stringify({ date: "2026-10-03", articles: [] }))),
    });
    renderHome();
    expect(await screen.findByText(/no articles for this date/i)).toBeInTheDocument();
  });

  it("treats a 404 digest as empty, not an error", async () => {
    stubApi({
      "/api/digest/2026-10-03": () =>
        Promise.resolve(new Response(JSON.stringify({ detail: "No digest" }), { status: 404 })),
    });
    renderHome();
    expect(await screen.findByText(/no articles for this date/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a digest error with Retry that refetches", async () => {
    let fail = true;
    const fetchFn = stubApi({
      "/api/digest/2026-10-03": () =>
        fail
          ? Promise.resolve(new Response(JSON.stringify({ detail: "boom" }), { status: 500 }))
          : Promise.resolve(new Response(JSON.stringify(DIGESTS["2026-10-03"]))),
    });
    renderHome();
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText("First story")).toBeInTheDocument();
    expect(fetchFn.mock.calls.filter((c) => c[0] === "/api/digest/2026-10-03")).toHaveLength(2);
  });

  it("shows a dates error with Retry that refetches", async () => {
    let fail = true;
    stubApi({
      "/api/dates": () =>
        fail
          ? Promise.resolve(new Response(JSON.stringify({ detail: "down" }), { status: 503 }))
          : Promise.resolve(new Response(JSON.stringify(DATES))),
    });
    renderHome();
    expect(await screen.findByRole("alert")).toHaveTextContent(/down/);
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText("First story")).toBeInTheDocument();
  });

  it("never lets a slow earlier response replace a newer selection", async () => {
    let releaseSlow: () => void = () => {};
    stubApi({
      "/api/digest/2026-10-03": () =>
        new Promise<Response>((resolve) => {
          releaseSlow = () => resolve(new Response(JSON.stringify(DIGESTS["2026-10-03"])));
        }),
    });
    renderHome();
    const picker = await screen.findByLabelText(/date/i);
    await userEvent.selectOptions(picker, "2026-10-02");
    expect(await screen.findByText("Older story")).toBeInTheDocument();
    releaseSlow();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText("Older story")).toBeInTheDocument();
    expect(screen.queryByText("First story")).not.toBeInTheDocument();
  });

  it("does not show the previous date's cards while the next date loads", async () => {
    stubApi({ "/api/digest/2026-10-02": () => new Promise<Response>(() => {}) });
    renderHome();
    await screen.findByText("First story");
    await userEvent.selectOptions(screen.getByLabelText(/date/i), "2026-10-02");
    expect(screen.queryByText("First story")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });
});
