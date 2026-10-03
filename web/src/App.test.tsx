import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "./App";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ dates: [], latest: null })))),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe("AppRoutes", () => {
  it("renders the home page inside the layout", async () => {
    renderAt("/");
    expect(screen.getByRole("banner")).toHaveTextContent(/AI News Brief/);
    expect(await screen.findByText(/no digests yet/i)).toBeInTheDocument();
  });

  it("renders NotFound for unknown paths, still inside the layout", () => {
    renderAt("/nope");
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to digest/i })).toHaveAttribute("href", "/");
  });

  it("renders the browse page and header nav", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((path: string) =>
        Promise.resolve(new Response(JSON.stringify(path === "/api/tags" ? { tags: [] } : { articles: [], total: 0 }))),
      ),
    );
    renderAt("/articles");
    expect(await screen.findByRole("heading", { name: /browse articles/i })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: /main/i });
    expect(nav).toHaveTextContent("Digest");
    expect(screen.getByRole("link", { name: "Browse" })).toHaveAttribute("href", "/articles");
    expect(screen.getByRole("link", { name: "Browse" })).toHaveAttribute("aria-current", "page");
  });
});
