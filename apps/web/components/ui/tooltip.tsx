"use client";

import {
  cloneElement,
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
} from "react";
import { createPortal } from "react-dom";

type TriggerProps = {
  "aria-describedby"?: string;
  onBlur?: (event: FocusEvent<HTMLElement>) => void;
  onFocus?: (event: FocusEvent<HTMLElement>) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLElement>) => void;
  onClick?: (event: MouseEvent<HTMLElement>) => void;
  onPointerEnter?: (event: PointerEvent<HTMLElement>) => void;
  onPointerLeave?: (event: PointerEvent<HTMLElement>) => void;
};

export function Tooltip({
  children,
  content,
}: {
  children: ReactElement<TriggerProps>;
  content: string;
}) {
  const id = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState({
    top: 0,
    left: 0,
    placement: "bottom" as "bottom" | "top",
  });
  const visible = (hovered || focused) && !dismissed;
  const describedBy = [children.props["aria-describedby"], id]
    .filter(Boolean)
    .join(" ");

  const trigger = cloneElement(children, {
    "aria-describedby": describedBy,
    onPointerEnter(event) {
      setHovered(true);
      setDismissed(false);
      children.props.onPointerEnter?.(event);
    },
    onPointerLeave(event) {
      setHovered(false);
      children.props.onPointerLeave?.(event);
    },
    onFocus(event) {
      setFocused(true);
      setDismissed(false);
      children.props.onFocus?.(event);
    },
    onBlur(event) {
      setFocused(false);
      children.props.onBlur?.(event);
    },
    onKeyDown(event) {
      if (event.key === "Escape" && visible) {
        setDismissed(true);
        event.stopPropagation();
      }
      children.props.onKeyDown?.(event);
    },
    onClick(event) {
      setDismissed(true);
      children.props.onClick?.(event);
    },
  });

  useEffect(() => {
    if (!visible) return;
    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const gap = 8;
      const below = rect.bottom + gap;
      const above = rect.top - gap;
      const placement = below + 32 <= window.innerHeight ? "bottom" : "top";
      const top = placement === "bottom" ? below : Math.max(8, above);
      const halfWidth = (tooltipRef.current?.offsetWidth ?? 0) / 2;
      setPosition({
        top,
        left: Math.min(
          Math.max(rect.left + rect.width / 2, 8 + halfWidth),
          window.innerWidth - 8 - halfWidth,
        ),
        placement,
      });
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [visible]);

  const tooltip = (
    <span
      className="tooltip__content"
      data-placement={position.placement}
      data-visible={visible ? "true" : undefined}
      id={id}
      role="tooltip"
      ref={tooltipRef}
      style={{ left: position.left, top: position.top }}
    >
      {content}
    </span>
  );

  return (
    <span className="tooltip" ref={triggerRef}>
      {trigger}
      {typeof document === "undefined" ? null : createPortal(tooltip, document.body)}
    </span>
  );
}
