"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { NativeDialog } from "./native-dialog";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  side?: "left" | "right";
  routeKey?: string;
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  side = "left",
  routeKey,
}: DrawerProps) {
  const previousRouteKey = useRef(routeKey);

  useEffect(() => {
    if (
      open &&
      previousRouteKey.current !== undefined &&
      routeKey !== previousRouteKey.current
    ) {
      onClose();
    }
    previousRouteKey.current = routeKey;
  }, [onClose, open, routeKey]);

  return (
    <NativeDialog
      open={open}
      onClose={onClose}
      title={title}
      className={`drawer drawer--${side}`}
      closeOnBackdrop
    >
      {children}
    </NativeDialog>
  );
}
