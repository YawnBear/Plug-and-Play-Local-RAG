"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getLibraryTree, listDocuments } from "@/features/library/api";
import type {
  DocumentSummary,
  LibraryTreeNode,
} from "@/features/library/contracts";
import { requireUuid } from "@/lib/uuid";

import {
  getAdminAccessContext,
  listAdminTeams,
  listAdminUsers,
} from "./api";
import { AclConfirmation } from "./acl-confirmation";
import { AdminError, AdminPage } from "./admin-page";
import type {
  AclOperation,
  AdminAccessContext,
  AdminTeam,
  AdminUser,
} from "./contracts";

interface AccessNode {
  id: string;
  name: string;
  path: string;
  kind: "folder" | "file";
}

interface PendingChange {
  operation: AclOperation;
  title: string;
}

function flattenFolders(
  nodes: readonly LibraryTreeNode[],
): AccessNode[] {
  return nodes.flatMap((node) => [
    {
      id: node.node_id,
      name: node.name,
      path: node.logical_path,
      kind: "folder" as const,
    },
    ...flattenFolders(node.children),
  ]);
}

function fileNodes(documents: readonly DocumentSummary[]): AccessNode[] {
  return documents.map((document) => ({
    id: document.node_id,
    name: document.display_name,
    path: document.logical_path,
    kind: "file",
  }));
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Access data failed to load.";
}

export function AccessWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedParam = searchParams.get("node");
  const [nodes, setNodes] = useState<AccessNode[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [teams, setTeams] = useState<AdminTeam[]>([]);
  const [context, setContext] = useState<AdminAccessContext | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingChange | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState<number | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const [tree, documents, nextUsers, nextTeams] =
        await Promise.all([
          getLibraryTree(signal),
          listDocuments(signal),
          listAdminUsers(signal),
          listAdminTeams(signal),
        ]);
      setNodes([...flattenFolders(tree), ...fileNodes(documents)]);
      setUsers(nextUsers);
      setTeams(nextTeams);
    } catch (reason) {
      if (!signal?.aborted) setError(message(reason));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load]);

  const selectedId = useMemo(() => {
    if (!selectedParam) return nodes[0]?.id ?? null;
    try {
      return requireUuid(selectedParam, "library node");
    } catch {
      return nodes[0]?.id ?? null;
    }
  }, [nodes, selectedParam]);
  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const selectedContext = context?.node_id === selectedId ? context : null;
  const directGrants = selectedContext?.direct_grants ?? [];
  const directCreateGrants = selectedContext?.direct_create_grants ?? [];
  const selectedStartsBoundary =
    selectedContext?.nearest_boundary_node_id === selectedId;

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setContextError(null);
      void getAdminAccessContext(selectedId, controller.signal)
        .then(setContext)
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) setContextError(message(reason));
        });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [selectedId, version]);

  function selectNode(nodeId: string) {
    const next = new URLSearchParams(searchParams.toString());
    next.set("node", nodeId);
    router.replace(`/admin/access?${next}`);
  }

  function hasUserGrant(userId: string): boolean {
    return directGrants.some((grant) => grant.user_id === userId);
  }

  function hasTeamGrant(teamId: string): boolean {
    return directGrants.some((grant) => grant.team_id === teamId);
  }

  function hasUserCreateGrant(userId: string): boolean {
    return directCreateGrants.some((grant) => grant.user_id === userId);
  }

  function hasTeamCreateGrant(teamId: string): boolean {
    return directCreateGrants.some((grant) => grant.team_id === teamId);
  }

  function requestGrant(
    principal: AdminUser | AdminTeam,
    principalKind: "user" | "team",
    present: boolean,
  ) {
    if (!selected) return;
    const principalLabel =
      "display_name" in principal ? principal.display_name : principal.name;
    setPending({
      operation: {
        kind: "set_grant",
        node_id: selected.id,
        ...(principalKind === "user"
          ? { user_id: principal.id }
          : { team_id: principal.id }),
        present,
      },
      title: `${present ? "Grant" : "Remove"} ${principalLabel} access?`,
    });
  }

  function requestCreateGrant(
    principal: AdminUser | AdminTeam,
    principalKind: "user" | "team",
    present: boolean,
  ) {
    if (!selected || selected.kind !== "folder") return;
    const principalLabel =
      "display_name" in principal ? principal.display_name : principal.name;
    setPending({
      operation: {
        kind: "set_create_children_grant",
        folder_id: selected.id,
        ...(principalKind === "user"
          ? { user_id: principal.id }
          : { team_id: principal.id }),
        present,
      },
      title: `${present ? "Grant" : "Remove"} ${principalLabel} create-subfolders capability?`,
    });
  }

  return (
    <AdminPage
      title="Access"
      description="Select a library node, edit direct user or team grants, and review the server-calculated impact before applying."
    >
      {version ? (
        <p role="status" className="mb-4 rounded-lg bg-[var(--surface-subtle)] p-3 text-sm">
          Access refreshed at authorization version <span className="font-mono">{version}</span>.
        </p>
      ) : null}
      {error ? <AdminError message={error} onRetry={() => void load()} /> : null}
      {loading ? <p role="status">Loading access controls…</p> : null}
      {!loading && nodes.length === 0 ? (
        <p className="rounded-xl border border-[var(--border)] p-6 text-[var(--muted-foreground)]">
          The library is empty. Upload a document before configuring access.
        </p>
      ) : null}
      {nodes.length > 0 ? (
        <div className="access-workspace__panes grid min-w-0 gap-5 lg:h-[calc(100dvh-20rem)] lg:min-h-80 lg:grid-cols-[280px_minmax(0,1fr)] lg:items-stretch">
          <aside className="access-workspace__nodes flex min-w-0 flex-col rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 lg:min-h-0 lg:overflow-hidden">
            <h2 className="px-2 py-2">Library nodes</h2>
            <ul className="access-workspace__node-list m-0 list-none p-0 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:overscroll-contain lg:[scrollbar-gutter:stable]">
              {nodes.map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    aria-current={node.id === selectedId ? "true" : undefined}
                    onClick={() => selectNode(node.id)}
                    className="w-full rounded-lg px-3 py-2 text-left hover:bg-[var(--surface-hover)] aria-[current=true]:bg-[var(--surface-hover)]"
                  >
                    <strong className="block">{node.name}</strong>
                    <span className="block truncate font-mono text-xs text-[var(--muted-foreground)]">{node.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          {selected ? (
            <section aria-labelledby="access-node-title" className="access-workspace__detail min-w-0 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 sm:p-6 lg:flex lg:min-h-0 lg:flex-col lg:overflow-hidden">
              <header className="shrink-0">
                <p className="m-0 text-sm capitalize text-[var(--muted-foreground)]">{selected.kind}</p>
                <h2 id="access-node-title" className="mt-1 text-xl">{selected.name}</h2>
                <p className="mt-1 font-mono text-sm text-[var(--muted-foreground)]">{selected.path}</p>
              </header>
              <div className="access-workspace__detail-scroll lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:overscroll-contain lg:pr-2 lg:[scrollbar-gutter:stable]">
                {contextError ? (
                <AdminError
                  message={contextError}
                  onRetry={() => setVersion((current) => (current ?? 0) + 1)}
                />
                ) : null}
                <section
                aria-labelledby="access-inheritance-title"
                className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-lg bg-[var(--surface-subtle)] p-4"
              >
                <div>
                  <h3 className="text-base" id="access-inheritance-title">
                    Access inheritance
                  </h3>
                  <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                    {!selectedContext
                      ? "Loading inheritance status…"
                      : selectedStartsBoundary
                        ? "This folder starts an inheritance boundary."
                        : selectedContext.nearest_boundary_node_id
                          ? "A parent folder already limits inherited access."
                          : "Access inherited from parent folders continues here."}
                  </p>
                </div>
                <button
                  className={`${selectedStartsBoundary ? "admin-action--danger " : ""}rounded-lg border border-[var(--border)] px-3`}
                  disabled={!selectedContext}
                  onClick={() =>
                    setPending({
                      operation: {
                        kind: "set_boundary",
                        node_id: selected.id,
                        enabled: !selectedStartsBoundary,
                      },
                      title: selectedStartsBoundary
                        ? `Remove the access boundary at ${selected.name}?`
                        : `Start an access boundary at ${selected.name}?`,
                    })
                  }
                  type="button"
                >
                  {selectedStartsBoundary ? "Remove boundary" : "Set boundary"}
                </button>
                </section>
                <section aria-labelledby="read-access-title" className="mt-8">
                <h3 className="text-base" id="read-access-title">
                  Read access
                </h3>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                  Grant read access directly to users or teams on this {selected.kind}.
                </p>
                <div className="mt-4 grid gap-6 xl:grid-cols-2">
                  <PrincipalList
                    title="Users"
                    principals={users.filter((user) => user.status === "active")}
                    label={(user) => user.display_name}
                    secondary={(user) => `@${user.username}`}
                    present={hasUserGrant}
                    onChange={(user, present) =>
                      requestGrant(user, "user", present)
                    }
                  />
                  <PrincipalList
                    title="Teams"
                    principals={teams.filter((team) => team.is_active)}
                    label={(team) => team.name}
                    secondary={(team) => `${team.member_count} members`}
                    present={hasTeamGrant}
                    onChange={(team, present) =>
                      requestGrant(team, "team", present)
                    }
                  />
                </div>
                {selectedContext && selectedContext.inherited_grants.length > 0 ? (
                  <InheritedGrantList
                    grants={selectedContext.inherited_grants}
                    title="Inherited read access"
                  />
                ) : null}
                </section>

                {selected.kind === "folder" ? (
                  <section aria-labelledby="create-subfolders-title" className="mt-10">
                  <h3 className="text-base" id="create-subfolders-title">
                    Create subfolders
                  </h3>
                  <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                    Grant this capability directly to users or teams. It does not grant read access.
                  </p>
                  <div className="mt-4 grid gap-6 xl:grid-cols-2">
                    <PrincipalList
                      title="Users"
                      principals={users.filter(
                        (user) => user.status === "active",
                      )}
                      label={(user) => user.display_name}
                      secondary={(user) => `@${user.username}`}
                      present={hasUserCreateGrant}
                      onChange={(user, present) =>
                        requestCreateGrant(user, "user", present)
                      }
                    />
                    <PrincipalList
                      title="Teams"
                      principals={teams.filter((team) => team.is_active)}
                      label={(team) => team.name}
                      secondary={(team) => `${team.member_count} members`}
                      present={hasTeamCreateGrant}
                      onChange={(team, present) =>
                        requestCreateGrant(team, "team", present)
                      }
                    />
                  </div>
                  {selectedContext &&
                  selectedContext.inherited_create_grants.length > 0 ? (
                    <InheritedGrantList
                      grants={selectedContext.inherited_create_grants}
                      title="Inherited create-subfolder capability"
                    />
                  ) : null}
                  </section>
                ) : null}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
      <AclConfirmation
        operation={pending?.operation ?? null}
        subjectName={selected?.name ?? ""}
        title={pending?.title ?? "Review access change"}
        onClose={() => setPending(null)}
        onApplied={(authorizationVersion) => {
          setPending(null);
          setVersion(authorizationVersion);
          void load();
        }}
      />
    </AdminPage>
  );
}

function InheritedGrantList({
  grants,
  title,
}: {
  grants: AdminAccessContext["inherited_grants"];
  title: string;
}) {
  return (
    <section className="mt-5 rounded-lg bg-[var(--surface-subtle)] p-3">
      <h4 className="m-0 text-sm font-semibold text-[var(--foreground)]">
        {title}
      </h4>
      <ul className="mt-2 list-none divide-y divide-[var(--border)] p-0 text-sm">
        {grants.map((grant) => (
          <li
            className="py-2"
            key={`${grant.source_node_id}-${grant.user_id ?? grant.team_id}`}
          >
            {grant.user_id ? `User ${grant.user_id}` : `Team ${grant.team_id}`} {" "}
            <span className="font-mono text-[var(--muted-foreground)]">
              from {grant.source_node_id}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PrincipalList<T extends { id: string }>({
  title,
  principals,
  label,
  secondary,
  present,
  onChange,
}: {
  title: string;
  principals: readonly T[];
  label: (principal: T) => string;
  secondary: (principal: T) => string;
  present: (id: string) => boolean;
  onChange: (principal: T, present: boolean) => void;
}) {
  return (
    <section>
      <h4 className="m-0 text-sm font-semibold text-[var(--foreground)]">
        {title}
      </h4>
      <ul className="mt-2 list-none p-0">
        {principals.map((principal) => {
          const granted = present(principal.id);
          return (
            <li key={principal.id} className="flex min-h-14 items-center justify-between gap-3 border-b border-[var(--border)]">
              <span>
                <strong className="block">{label(principal)}</strong>
                <span className="block text-sm text-[var(--muted-foreground)]">{secondary(principal)}</span>
              </span>
              <button
                type="button"
                onClick={() => onChange(principal, !granted)}
                className={`rounded-lg border border-[var(--border)] px-3 ${
                  granted ? "admin-action--danger" : ""
                }`}
              >
                {granted ? "Remove" : "Grant"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
