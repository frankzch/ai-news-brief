import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TagChips } from "./TagChips";

const many = Array.from({ length: 30 }, (_, i) => ({ tag: `tag${i}`, count: 30 - i }));

describe("TagChips", () => {
  it("shows the top tags and a toggle for the rest", async () => {
    render(<TagChips tags={many} active={null} onChange={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: /^tag\d+/ })).toHaveLength(15);
    await userEvent.click(screen.getByRole("button", { name: /show all 30 tags/i }));
    expect(screen.getAllByRole("button", { name: /^tag\d+/ })).toHaveLength(30);
    await userEvent.click(screen.getByRole("button", { name: /show fewer/i }));
    expect(screen.getAllByRole("button", { name: /^tag\d+/ })).toHaveLength(15);
  });

  it("has no toggle when the tags fit", () => {
    render(<TagChips tags={many.slice(0, 5)} active={null} onChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /show all/i })).not.toBeInTheDocument();
  });

  it("keeps an active tag visible even when it is beyond the cap", () => {
    render(<TagChips tags={many} active="tag25" onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: /tag25/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("button", { name: /^tag\d+/ })).toHaveLength(16);
  });
});
