import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Drawer } from "@/components/ui/drawer";
import { NativeDialog } from "@/components/ui/native-dialog";

function DialogHarness({
  initialInput = false,
  closeDisabled = false,
}: {
  initialInput?: boolean;
  closeDisabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open settings
      </button>
      <NativeDialog
        open={open}
        onClose={() => setOpen(false)}
        title="Settings"
        initialFocusRef={initialInput ? inputRef : undefined}
        closeDisabled={closeDisabled}
      >
        <label>
          Name
          <input ref={inputRef} />
        </label>
      </NativeDialog>
    </>
  );
}

describe("native dialog", () => {
  it("focuses the requested control and returns focus to its trigger", async () => {
    const user = userEvent.setup();
    render(<DialogHarness initialInput />);
    const trigger = screen.getByRole("button", { name: "Open settings" });

    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "Close Settings" }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("maps the native Escape cancel event to the close hook", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    const dialog = screen.getByRole("dialog");

    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
  });

  it("cannot be dismissed while a mutation disables closing", async () => {
    const user = userEvent.setup();
    render(<DialogHarness closeDisabled />);
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    const dialog = screen.getByRole("dialog");
    const close = screen.getByRole("button", { name: "Close Settings" });

    expect(close).toBeDisabled();
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(dialog).toHaveAttribute("open");
  });
});

describe("drawer", () => {
  it("closes only when pointer input lands outside the drawer bounds", () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="Source preview">
        <button type="button">Preview content</button>
      </Drawer>,
    );
    const dialog = screen.getByRole("dialog");
    vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue({
      x: 100,
      y: 0,
      top: 0,
      right: 500,
      bottom: 800,
      left: 100,
      width: 400,
      height: 800,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(dialog, { clientX: 200, clientY: 100 });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.pointerDown(dialog, { clientX: 50, clientY: 100 });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes through its route-change hook without reading router state", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Drawer open onClose={onClose} title="Conversations" routeKey="/?chat=one">
        <button type="button">Conversation</button>
      </Drawer>,
    );

    rerender(
      <Drawer open onClose={onClose} title="Conversations" routeKey="/?chat=two">
        <button type="button">Conversation</button>
      </Drawer>,
    );
    expect(onClose).toHaveBeenCalledOnce();
  });
});
