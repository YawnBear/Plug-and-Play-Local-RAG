"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  createAdminUser,
  listAdminUsers,
  resetAdminUser,
  updateAdminUser,
} from "./api";
import { AdminError, AdminPage } from "./admin-page";
import type { AdminUser } from "./contracts";

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "User operation failed.";
}

export function UsersWorkspace() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [activationCode, setActivationCode] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listAdminUsers(signal));
    } catch (reason) {
      if (!signal?.aborted) setError(errorMessage(reason));
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
    const role = data.get("role");
    if (role !== "admin" && role !== "member") return;
    setWorkingId("create");
    setError(null);
    try {
      const result = await createAdminUser({
        username: String(data.get("username") ?? ""),
        display_name: String(data.get("display_name") ?? ""),
        role,
      });
      setActivationCode(result.activation_code);
      form.reset();
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function change(user: AdminUser, form: HTMLFormElement) {
    const data = new FormData(form);
    const role = data.get("role");
    const status = data.get("status");
    const confirmation = String(data.get("confirmation") ?? "");
    if (
      (role !== "admin" && role !== "member") ||
      (status !== "active" && status !== "disabled" && status !== "deleted")
    ) {
      return;
    }
    if (status !== "active" && confirmation !== user.username) {
      setError(`Type ${user.username} before disabling or deleting this account.`);
      return;
    }
    setWorkingId(user.id);
    setError(null);
    try {
      await updateAdminUser(user.id, { role, status });
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function reset(user: AdminUser) {
    setWorkingId(user.id);
    setError(null);
    try {
      const result = await resetAdminUser(user.id);
      setActivationCode(result.activation_code);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setWorkingId(null);
    }
  }

  return (
    <AdminPage
      title="Users"
      description="Create accounts, manage roles and lifecycle state, and issue one-time activation codes."
    >
      <form
        onSubmit={create}
        className="mb-8 grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 md:grid-cols-4"
      >
        <label className="grid gap-1 text-sm">
          Username
          <input required name="username" className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3" />
        </label>
        <label className="grid gap-1 text-sm">
          Display name
          <input required name="display_name" className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3" />
        </label>
        <label className="grid gap-1 text-sm">
          Role
          <select name="role" defaultValue="member" className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-3">
            <option value="member">Member</option>
            <option value="admin">Administrator</option>
          </select>
        </label>
        <button disabled={workingId !== null} className="self-end rounded-lg bg-[var(--foreground)] px-4 text-[var(--background)]">
          Create user
        </button>
      </form>

      {activationCode ? (
        <div role="status" className="mb-6 rounded-xl border border-[var(--success)] bg-[var(--surface)] p-4">
          <strong>One-time activation code</strong>
          <code className="mt-2 block select-all font-mono">{activationCode}</code>
          <p className="mb-0 text-sm text-[var(--muted-foreground)]">
            Share this securely. It will not be shown again after leaving this view.
          </p>
        </div>
      ) : null}
      {error ? <AdminError message={error} onRetry={() => void load()} /> : null}
      {loading ? <p role="status">Loading users…</p> : null}
      {!loading ? (
        <div className="grid gap-3">
          {users.map((user) => (
            <form
              key={user.id}
              onSubmit={(event) => {
                event.preventDefault();
                void change(user, event.currentTarget);
              }}
              className="grid gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 lg:grid-cols-[minmax(180px,1fr)_150px_160px_minmax(180px,1fr)_auto]"
            >
              <div>
                <strong>{user.display_name}</strong>
                <span className="block font-mono text-sm text-[var(--muted-foreground)]">@{user.username}</span>
              </div>
              <label className="grid gap-1 text-sm">
                Role
                <select name="role" defaultValue={user.role} className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2">
                  <option value="member">Member</option>
                  <option value="admin">Administrator</option>
                </select>
              </label>
              <label className="grid gap-1 text-sm">
                Status
                <select name="status" defaultValue={user.status} className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2">
                  <option value="active">Active</option>
                  <option value="disabled">Disabled</option>
                  <option value="deleted">Deleted</option>
                </select>
              </label>
              <label className="grid gap-1 text-sm">
                Confirmation for destructive state
                <input name="confirmation" placeholder={`Type ${user.username}`} className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2" />
              </label>
              <div className="flex flex-wrap items-end gap-2">
                <button disabled={workingId !== null} className="rounded-lg border border-[var(--border)] px-3">Save</button>
                <button disabled={workingId !== null} type="button" onClick={() => void reset(user)} className="rounded-lg border border-[var(--border)] px-3">Reset</button>
              </div>
            </form>
          ))}
        </div>
      ) : null}
    </AdminPage>
  );
}
