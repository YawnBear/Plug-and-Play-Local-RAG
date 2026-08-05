import type { SVGProps } from "react";

interface IconProps extends SVGProps<SVGSVGElement> {
  name:
    | "activity"
    | "admin"
    | "chat"
    | "check"
    | "chevron"
    | "close"
    | "collapse"
    | "collapse-all"
    | "details"
    | "error"
    | "external"
    | "file"
    | "folder"
    | "knowledge"
    | "menu"
    | "moon"
    | "more"
    | "new"
    | "panel"
    | "pin"
    | "refresh"
    | "search"
    | "send"
    | "sign-out"
    | "source"
    | "stop"
    | "sun"
    | "system"
    | "upload"
    | "warning";
}

export function Icon({ name, ...props }: IconProps) {
  const common = {
    "aria-hidden": true,
    fill: "none",
    height: 20,
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.8,
    viewBox: "0 0 24 24",
    width: 20,
    ...props,
  };

  switch (name) {
    case "activity":
      return <svg {...common}><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /></svg>;
    case "admin":
      return <svg {...common}><path d="M4 20v-7h6v7M14 20v-4h6v4M4 9V4h6v5M14 12V4h6v8" /></svg>;
    case "chat":
      return <svg {...common}><path d="M5 18.5 3.5 21l4-1.2A9 9 0 1 0 5 18.5Z" /></svg>;
    case "check":
      return <svg {...common}><path d="m5 12.5 4.25 4.25L19 7" /></svg>;
    case "chevron":
      return <svg {...common}><path d="m9 6 6 6-6 6" /></svg>;
    case "close":
      return <svg {...common}><path d="m6 6 12 12M18 6 6 18" /></svg>;
    case "collapse":
      return <svg {...common}><path d="M4 5h16M4 12h10M4 19h16" /><path d="m18 9-3 3 3 3" /></svg>;
    case "collapse-all":
      return <svg {...common}><path d="m8 9 4-4 4 4M8 15l4 4 4-4" /><path d="M4 12h16" /></svg>;
    case "details":
      return <svg {...common}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></svg>;
    case "error":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></svg>;
    case "external":
      return <svg {...common}><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" /></svg>;
    case "file":
      return <svg {...common}><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v5h5M9 13h6M9 17h5" /></svg>;
    case "folder":
      return <svg {...common}><path d="M3 6.5h7l2 2h9v10.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>;
    case "knowledge":
      return <svg {...common}><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5v-16ZM20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5v-16Z" /></svg>;
    case "menu":
      return <svg {...common}><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
    case "moon":
      return <svg {...common}><path d="M20 15.2A8.5 8.5 0 0 1 8.8 4 8.5 8.5 0 1 0 20 15.2Z" /></svg>;
    case "more":
      return <svg {...common}><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></svg>;
    case "new":
      return <svg {...common}><path d="M12 5v14M5 12h14" /></svg>;
    case "panel":
      return <svg {...common}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 14h18" /></svg>;
    case "pin":
      return <svg {...common}><path d="m9 3 6 6M8 8l8-3-1 8 3 3-6 2-6-6 2-4ZM5 19l4-4" /></svg>;
    case "refresh":
      return <svg {...common}><path d="M20 11a8 8 0 0 0-14.7-4.3L3 9M4 13a8 8 0 0 0 14.7 4.3L21 15" /><path d="M3 4v5h5M21 20v-5h-5" /></svg>;
    case "search":
      return <svg {...common}><circle cx="11" cy="11" r="7" /><path d="m16.5 16.5 4 4" /></svg>;
    case "send":
      return <svg {...common}><path d="m5 12 7-7 7 7M12 5v14" /></svg>;
    case "sign-out":
      return <svg {...common}><path d="M10 5H5v14h5M14 8l4 4-4 4M18 12H9" /></svg>;
    case "source":
      return <svg {...common}><path d="M7 3h10v18H7z" /><path d="M10 8h4M10 12h4M10 16h3" /></svg>;
    case "stop":
      return <svg {...common}><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" /></svg>;
    case "sun":
      return <svg {...common}><circle cx="12" cy="12" r="3.5" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
    case "system":
      return <svg {...common}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></svg>;
    case "upload":
      return <svg {...common}><path d="M12 16V4M7 9l5-5 5 5M4 20h16" /></svg>;
    case "warning":
      return <svg {...common}><path d="M12 3 2.75 20h18.5L12 3Z" /><path d="M12 9v5M12 17.5v.01" /></svg>;
  }
}
