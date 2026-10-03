import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { InlineMarkdown, Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders headings, bold, paragraphs and lists as elements", () => {
    const { container } = render(
      <Markdown text={"## Background\n\nSome **bold** text.\nSecond line.\n\n- one\n- two\n\n1. first\n2. second"} />,
    );
    expect(screen.getByRole("heading", { name: "Background" })).toBeInTheDocument();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.textContent).not.toContain("##");
    expect(container.textContent).not.toContain("**");
  });

  it("never renders HTML from the text", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> <script>x</script> **ok**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img");
    expect(container.querySelector("strong")).toHaveTextContent("ok");
  });

  it("leaves unmatched markers as plain text", () => {
    const { container } = render(<Markdown text="a ** b" />);
    expect(container.textContent).toBe("a ** b");
  });

  it("renders nothing for empty text", () => {
    const { container } = render(<Markdown text="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("InlineMarkdown", () => {
  it("renders bold inline and drops heading markers", () => {
    const { container } = render(<InlineMarkdown text={"## Title\nA **key** point"} />);
    expect(container.querySelector("strong")).toHaveTextContent("key");
    expect(container.textContent).not.toContain("#");
    expect(container.textContent).not.toContain("**");
  });
});
