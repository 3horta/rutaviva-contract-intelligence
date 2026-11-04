import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders HTML in an answer as text, never as elements", () => {
    const { container } = render(
      <Markdown text={'<img src=x onerror="alert(1)"> and **bold** and `<script>alert(2)</script>`'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container).toHaveTextContent('<img src=x onerror="alert(1)">');
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelector("code")).toHaveTextContent("<script>alert(2)</script>");
  });

  it("renders lists as list elements", () => {
    const { container } = render(<Markdown text={"- one\n- two\n\n1. first"} />);
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelectorAll("ol > li")).toHaveLength(1);
  });
});
