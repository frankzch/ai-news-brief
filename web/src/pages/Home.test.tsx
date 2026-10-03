import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Home } from "./Home";

function stubFetch(impl: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

afterEach(() => vi.unstubAllGlobals());

describe("Home", () => {
  it("shows loading, then the latest date and available dates", async () => {
    stubFetch(() =>
      Promise.resolve(
        new Response(JSON.stringify({ dates: ["2026-10-03", "2026-10-02"], latest: "2026-10-03" })),
      ),
    );
    render(<Home />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    expect(await screen.findByText(/latest digest/i)).toHaveTextContent("2026-10-03");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("shows an empty state when there are no archives", async () => {
    stubFetch(() => Promise.resolve(new Response(JSON.stringify({ dates: [], latest: null }))));
    render(<Home />);
    expect(await screen.findByText(/no digests yet/i)).toBeInTheDocument();
  });

  it("shows an error message when the request fails", async () => {
    stubFetch(() => Promise.resolve(new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
    render(<Home />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });
});
