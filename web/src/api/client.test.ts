import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getArticle, getArticles, getDates, getDigest, getJson, getTags } from "./client";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("getJson", () => {
  it("returns parsed JSON from a relative /api path", async () => {
    const fetchFn = mockFetch(200, { ok: 1 });
    await expect(getJson<{ ok: number }>("/api/health")).resolves.toEqual({ ok: 1 });
    expect(fetchFn).toHaveBeenCalledWith("/api/health", expect.objectContaining({ method: "GET" }));
  });

  it("throws ApiError carrying the status on a non-2xx response", async () => {
    mockFetch(404, { detail: "No digest for this date" });
    const err = await getJson("/api/digest/2020-01-01").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("No digest for this date");
  });

  it("rejects paths outside /api", async () => {
    await expect(getJson("https://example.com/x")).rejects.toThrow();
  });
});

describe("getDates", () => {
  it("fetches /api/dates", async () => {
    const fetchFn = mockFetch(200, { dates: ["2026-10-03"], latest: "2026-10-03" });
    await expect(getDates()).resolves.toEqual({ dates: ["2026-10-03"], latest: "2026-10-03" });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/dates");
  });
});

describe("getDigest", () => {
  it("fetches /api/digest/<date> with the date encoded", async () => {
    const fetchFn = mockFetch(200, { date: "2026-10-03", articles: [] });
    await expect(getDigest("2026-10-03")).resolves.toEqual({ date: "2026-10-03", articles: [] });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/digest/2026-10-03");
    mockFetch(200, { date: "a/b", articles: [] });
    await getDigest("a/b");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/digest/a%2Fb");
  });
});

describe("article endpoints", () => {
  it("getArticles sends only set filters plus limit/offset", async () => {
    const fetchFn = mockFetch(200, { articles: [], total: 0 });
    await getArticles({ category: "news", tag: "a b", q: "x&y", page: 3 });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/articles?category=news&tag=a+b&q=x%26y&limit=30&offset=60");
  });

  it("getArticles omits unset filters", async () => {
    const fetchFn = mockFetch(200, { articles: [], total: 0 });
    await getArticles({ category: null, tag: null, q: "", page: 1 });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/articles?limit=30&offset=0");
  });

  it("getArticle encodes the id", async () => {
    const fetchFn = mockFetch(200, {});
    await getArticle("a/b?c");
    expect(fetchFn.mock.calls[0][0]).toBe("/api/articles/a%2Fb%3Fc");
  });

  it("getTags hits /api/tags", async () => {
    const fetchFn = mockFetch(200, { tags: [] });
    await getTags();
    expect(fetchFn.mock.calls[0][0]).toBe("/api/tags");
  });
});
