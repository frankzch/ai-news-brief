import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "../App";
import { LangProvider } from "../i18n/LangContext";

const ARTICLE = {
  id: "a1",
  title: "Raw",
  title_en: "English headline",
  title_zh: "中文标题",
  summary_en: "English summary",
  summary_zh: "中文摘要",
  long_summary_en: null,
  long_summary_zh: null,
  importance_score: 5,
  tags: ["llm"],
  category: "news",
  url: "https://example.com/a1",
  source_name: "Feed",
  published_at: null,
};

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) => {
      if (path === "/api/tags") return json({ tags: [{ tag: "llm", count: 3 }] });
      if (path.startsWith("/api/articles?")) return json({ articles: [ARTICLE], total: 1 });
      return json({ detail: "Not found" }, 404);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function renderAt(path: string) {
  return render(
    <LangProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </LangProvider>,
  );
}

describe("Browse in both languages", () => {
  it("switches filters, cards and chrome live when the toggle is used", async () => {
    renderAt("/articles");
    expect(await screen.findByRole("heading", { name: "English headline" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Browse articles" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "中文" }));

    expect(screen.getByRole("heading", { name: "中文标题" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "浏览文章" })).toBeInTheDocument();
    expect(screen.getByLabelText("搜索")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "分类" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部" })).toBeInTheDocument();
  });

  it("keeps category URL values English-valued regardless of language", async () => {
    localStorage.setItem("inbrief.lang", "zh");
    renderAt("/articles");
    await screen.findByRole("heading", { name: "中文标题" });
    await userEvent.click(screen.getByRole("button", { name: "新闻" }));
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(calls.some((u) => u.includes("category=news"))).toBe(true);
  });
});

describe("Home and NotFound in Chinese", () => {
  it("shows the empty digest state in Chinese", async () => {
    localStorage.setItem("inbrief.lang", "zh");
    vi.stubGlobal("fetch", vi.fn(() => json({ dates: [], latest: null })));
    renderAt("/");
    expect(await screen.findByText("暂无简报。请运行流水线生成。")).toBeInTheDocument();
  });

  it("shows NotFound in Chinese", () => {
    localStorage.setItem("inbrief.lang", "zh");
    renderAt("/nope");
    expect(screen.getByRole("heading", { name: "页面不存在" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回每日简报" })).toBeInTheDocument();
  });
});
