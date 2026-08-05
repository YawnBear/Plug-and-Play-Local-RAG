import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Tooltip } from "@/components/ui/tooltip";

describe("Tooltip", () => {
  it("describes its trigger, supports hover and focus, and dismisses on Escape", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Tooltip content="Helpful text">
        <button type="button" aria-label="Action">A</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Action" });
    const tooltip = screen.getByRole("tooltip");

    expect(trigger).toHaveAttribute("aria-describedby", tooltip.id);
    expect(container.querySelector("[title]")).toBeNull();
    expect(tooltip).not.toHaveAttribute("data-visible");

    await user.hover(trigger);
    expect(tooltip).toHaveAttribute("data-visible", "true");
    expect(tooltip).toHaveClass("tooltip__content");
    expect(tooltip.style.top).not.toBe("");
    expect(tooltip.parentElement).toBe(document.body);
    await user.unhover(trigger);
    fireEvent.focus(trigger);
    expect(tooltip).toHaveAttribute("data-visible", "true");
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(tooltip).not.toHaveAttribute("data-visible");
  });

  it("dismisses after activation even when the trigger remains focused", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="Collapse sidebar">
        <button type="button">Toggle sidebar</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Toggle sidebar" });
    const tooltip = screen.getByRole("tooltip");

    await user.hover(trigger);
    expect(tooltip).toHaveAttribute("data-visible", "true");

    await user.click(trigger);
    await user.unhover(trigger);
    expect(trigger).toHaveFocus();
    expect(tooltip).not.toHaveAttribute("data-visible");

    await user.hover(trigger);
    expect(tooltip).toHaveAttribute("data-visible", "true");
  });
});
