"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";

import {
  useSidebarContent,
  type SidebarContentSlot,
} from "./protected-shell";

export type WorkspaceSectionRoute = readonly [href: string, label: string];

export function useWorkspaceSectionNavigation(
  label: string,
  routes: readonly WorkspaceSectionRoute[],
) {
  const pathname = usePathname();
  const navigation = useMemo<SidebarContentSlot>(
    () => ({
      render: (onNavigate) => (
        <nav aria-label={label} className="workspace-section-nav">
          {routes.map(([href, routeLabel]) => (
            <Link
              aria-current={pathname === href ? "page" : undefined}
              className="workspace-section-nav__link"
              href={href}
              key={href}
              onClick={onNavigate}
            >
              {routeLabel}
            </Link>
          ))}
        </nav>
      ),
    }),
    [label, pathname, routes],
  );

  useSidebarContent(navigation);
}
