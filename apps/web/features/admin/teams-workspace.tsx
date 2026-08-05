"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  createAdminTeam,
  listAdminTeams,
  listAdminUsers,
} from "./api";
import { AclConfirmation } from "./acl-confirmation";
import { AdminError, AdminPage } from "./admin-page";
import type { AclOperation, AdminTeam, AdminUser } from "./contracts";

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Team operation failed.";
}

interface PendingChange {
  operation: AclOperation;
  subject: string;
  title: string;
}

export function TeamsWorkspace() {
  const [teams, setTeams] = useState<AdminTeam[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [pending, setPending] = useState<PendingChange | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [version, setVersion] = useState<number | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const [nextTeams, nextUsers] = await Promise.all([
        listAdminTeams(signal),
        listAdminUsers(signal),
      ]);
      setTeams(nextTeams);
      setUsers(nextUsers);
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

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setWorking(true);
    setError(null);
    try {
      await createAdminTeam(String(data.get("name") ?? ""));
      form.reset();
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setWorking(false);
    }
  }

  function membership(team: AdminTeam, userId: string, present: boolean) {
    const user = users.find((item) => item.id === userId);
    setPending({
      operation: {
        kind: "set_membership",
        team_id: team.id,
        user_id: userId,
        present,
      },
      subject: team.name,
      title: `${present ? "Add" : "Remove"} ${user?.display_name ?? "member"} ${present ? "to" : "from"} ${team.name}?`,
    });
  }

  return (
    <AdminPage
      title="Teams"
      description="Team grants follow active membership. Every membership or team-state change is previewed before it is applied."
    >
      <form onSubmit={create} className="mb-8 flex flex-wrap gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <label className="min-w-[min(100%,280px)] flex-1">
          Team name
          <input required name="name" className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3" />
        </label>
        <button disabled={working} className="self-end rounded-lg bg-[var(--foreground)] px-4 text-[var(--background)]">Create team</button>
      </form>
      {version ? (
        <p role="status" className="rounded-lg bg-[var(--surface-subtle)] p-3 text-sm">
          Access updated at authorization version <span className="font-mono">{version}</span>.
        </p>
      ) : null}
      {error ? <AdminError message={error} onRetry={() => void load()} /> : null}
      {loading ? <p role="status">Loading teams…</p> : null}
      <div className="grid gap-4">
        {teams.map((team) => (
          <section key={team.id} aria-labelledby={`team-${team.id}`} className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 id={`team-${team.id}`}>{team.name}</h2>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                  {team.member_count} member{team.member_count === 1 ? "" : "s"} · {team.is_active ? "Active" : "Inactive"}
                </p>
              </div>
              <button
                type="button"
                onClick={() =>
                  setPending({
                    operation: {
                      kind: "set_team_active",
                      team_id: team.id,
                      active: !team.is_active,
                    },
                    subject: team.name,
                    title: `${team.is_active ? "Deactivate" : "Activate"} ${team.name}?`,
                  })
                }
                className={`rounded-lg border border-[var(--border)] px-3 ${
                  team.is_active ? "admin-action--danger" : ""
                }`}
              >
                {team.is_active ? "Deactivate" : "Activate"}
              </button>
            </div>
            <ul className="mt-4 grid list-none gap-1 p-0">
              {users.filter((user) => user.status === "active").map((user) => {
                const included = team.member_ids.includes(user.id);
                return (
                  <li key={user.id} className="flex min-h-11 items-center justify-between gap-3 rounded-lg px-2 hover:bg-[var(--surface-hover)]">
                    <span>{user.display_name} <span className="font-mono text-sm text-[var(--muted-foreground)]">@{user.username}</span></span>
                    <button
                      type="button"
                      onClick={() => membership(team, user.id, !included)}
                      className={`rounded-lg border border-[var(--border)] px-3 ${
                        included ? "admin-action--danger" : ""
                      }`}
                    >
                      {included ? "Remove" : "Add"}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      <AclConfirmation
        operation={pending?.operation ?? null}
        subjectName={pending?.subject ?? ""}
        title={pending?.title ?? "Review team change"}
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
