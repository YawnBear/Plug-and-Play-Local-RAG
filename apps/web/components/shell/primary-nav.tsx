"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Icon } from "./icons";
import { Tooltip } from "@/components/ui/tooltip";

export function PrimaryNav({
  collapsed,
  isAdmin,
  onNavigate,
}: {
  collapsed: boolean;
  isAdmin: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const homeActive = pathname === "/";
  const knowledgeBaseActive = pathname.startsWith("/knowledge-base");
  const adminActive = pathname.startsWith("/admin");
  const systemActive = pathname.startsWith("/system");

  function navLink({
    active,
    href,
    icon,
    label,
  }: {
    active: boolean;
    href: string;
    icon: "chat" | "knowledge" | "admin" | "system";
    label: string;
  }) {
    const link = (
      <Link
        href={href}
        aria-current={active ? "page" : undefined}
        className="primary-nav__link"
        onClick={onNavigate}
      >
        <Icon name={icon} />
        <span>{label}</span>
      </Link>
    );

    return collapsed ? (
      <Tooltip content={label}>{link}</Tooltip>
    ) : (
      link
    );
  }

  return (
    <nav aria-label="Primary" className="primary-nav">
      {navLink({
        active: homeActive,
        href: "/",
        icon: "chat",
        label: "Chat",
      })}
      {navLink({
        active: knowledgeBaseActive,
        href: "/knowledge-base",
        icon: "knowledge",
        label: "Knowledge Base",
      })}
      {isAdmin
        ? navLink({
            active: adminActive,
            href: "/admin/users",
            icon: "admin",
            label: "Administration",
          })
        : null}
      {isAdmin
        ? navLink({
            active: systemActive,
            href: "/system/overview",
            icon: "system",
            label: "System",
          })
        : null}
    </nav>
  );
}
