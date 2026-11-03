import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { pct } from "../../lib/format";

import type { AuthUser, ManagedUser, Stats } from "../../types";
import { DashBar, DashCard, DashError, DashTile } from "./parts";

interface Props {
  user: AuthUser | null;
  onBack: () => void;
}

interface Pipeline {
  pending: number;
  verified: number;
  collected: number;
}

/**
 * Campus oversight: volumes, the category mix, the claim funnel and the quality
 * of the staff labels the re-ranker trains on. Deliberately not a second staff
 * desk — admins do not see staff actions here.
 */
export default function AdminDashboard({ user, onBack }: Props) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [roleBusy, setRoleBusy] = useState<string | null>(null);
  const [roleMessage, setRoleMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statsPage, pending, verified, collected, userPage] = await Promise.all([
        api.stats(),
        api.claims("pending"),
        api.claims("verified"),
        api.claims("collected"),
        api.adminUsers(),
      ]);
      setStats(statsPage);
      setPipeline({ pending: pending.count, verified: verified.count, collected: collected.count });
      setUsers(userPage.users);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the admin dashboard.");
    } finally {
      setLoading(false);
    }
  }, []);

  async function changeRole(target: ManagedUser, role: "student" | "staff" | "superadmin") {
    setRoleBusy(target.id);
    setRoleMessage(null);
    try {
      const updated = await api.adminSetRole(target.id, role);
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setRoleMessage(`${updated.email ?? updated.id} is now ${updated.role}.`);
    } catch (err) {
      setRoleMessage(err instanceof Error ? err.message : "Could not update this user's role.");
    } finally {
      setRoleBusy(null);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  const shown = (value: number | null | undefined) => (loading || value == null ? "—" : value);
  const categories = stats ? Object.entries(stats.by_category).slice(0, 8) : [];
  const categoryMax = categories.reduce((max, [, value]) => Math.max(max, value), 0);
  const labels = stats ? stats.confirmed + stats.rejected : 0;
  const pipelineMax = pipeline ? Math.max(pipeline.pending, pipeline.verified, pipeline.collected, 1) : 1;

  return (
    <div className="dashboard findly-dashboard">
      <header className="dash-head">
        <div>
          <span className="dash-eyebrow">Campus oversight · administrator access</span>
          <h2>Admin dashboard</h2>
          <p>
            Campus-level oversight: what is coming in, how much of it is resolved, and whether the staff
            labels behind the matcher are trustworthy.
          </p>
        </div>
        <div className="dash-head-actions">
          <button type="button" className="dash-back" onClick={onBack}>← Browse items</button>
          <button type="button" className="dash-refresh" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh analytics"}
          </button>
        </div>
      </header>

      {error && <DashError error={error} onRetry={() => void load()} />}

      <div className="dash-tiles">
        <DashTile value={shown(stats?.reports)} label="reports" />
        <DashTile value={shown(stats?.lost)} label="lost" />
        <DashTile value={shown(stats?.found)} label="found" />
        <DashTile value={shown(stats?.matched)} label="matched" tone="good" />
      </div>

      <DashCard title="Administrator controls">
        <div className="dash-action-grid">
          <div className="dash-action">
            <span className="dash-action-icon">▦</span>
            <div><strong>Monitor campus volume</strong><small>Track reports, matches, categories, and resolution progress.</small></div>
          </div>
          <div className="dash-action">
            <span className="dash-action-icon">⌁</span>
            <div><strong>Audit the claim pipeline</strong><small>Watch pending, verified, and collected handovers.</small></div>
          </div>
          <div className="dash-action">
            <span className="dash-action-icon">♙</span>
            <div><strong>Manage user permissions</strong><small>Promote users to staff or super admin from the access table below.</small></div>
          </div>
        </div>
      </DashCard>

      <div className="dash-grid">
        <DashCard title="Category mix" aside={<span className="muted mono">{categories.length} shown</span>}>
          {loading && (
            <p className="dash-note" aria-busy="true">
              Loading categories…
            </p>
          )}
          {!loading && categories.length === 0 && <p className="dash-note">No categorised reports yet.</p>}
          <div className="dash-bars">
            {categories.map(([category, value]) => (
              <DashBar key={category} label={category} value={value} max={categoryMax} />
            ))}
          </div>
        </DashCard>

        <DashCard title="Claim pipeline">
          {loading && (
            <p className="dash-note" aria-busy="true">
              Loading claims…
            </p>
          )}
          {!loading && pipeline && (
            <div className="dash-bars">
              <DashBar label="pending" value={pipeline.pending} max={pipelineMax} />
              <DashBar label="verified" value={pipeline.verified} max={pipelineMax} />
              <DashBar label="collected" value={pipeline.collected} max={pipelineMax} />
            </div>
          )}
          <p className="dash-note">
            Verified claims are closed at the desk with the student&apos;s handover code. Every collection
            writes an audit entry against both reports.
          </p>
        </DashCard>
      </div>

      <div className="dash-grid">
        <DashCard title="Match labels" aside={<span className="muted mono">{labels} total</span>}>
          <div className="dash-bars">
            <DashBar label="confirmed" value={stats?.confirmed ?? 0} max={Math.max(labels, 1)} />
            <DashBar label="rejected" value={stats?.rejected ?? 0} max={Math.max(labels, 1)} />
          </div>
          <p className="dash-note">
            {labels === 0
              ? "No staff verdicts yet, so the re-ranker has nothing to learn from."
              : `${pct((stats?.confirmed ?? 0) / labels)} of staff verdicts confirm a match. Rejections are the evidence the matcher abstains on.`}
          </p>
        </DashCard>

        <DashCard title="Access & roles">
          <div className="dash-scope">
            <span>
              Signed in as <strong>{user?.email ?? user?.id ?? "—"}</strong>
            </span>
            <span>
              Scope <strong>{user?.campus_id ?? "default campus"}</strong>
            </span>
          </div>
          <ul className="dash-list">
            <li className="dash-row">
              <span>My dashboard</span>
              <span className="muted mono">every role</span>
            </li>
            <li className="dash-row">
              <span>Staff dashboard</span>
              <span className="muted mono">staff only</span>
            </li>
            <li className="dash-row">
              <span>Admin dashboard</span>
              <span className="muted mono">campus admin · super admin</span>
            </li>
          </ul>
          <div className="admin-users">
            <div className="admin-users-head">
              <strong>Manage user permissions</strong>
              <span className="muted">{users.length} accounts</span>
            </div>
            {users.length === 0 && <p className="dash-note">No provisioned users found in this campus.</p>}
            {users.map((managed) => (
              <div className="admin-user-row" key={managed.id}>
                <div>
                  <strong>{managed.email ?? managed.phone_e164 ?? managed.id}</strong>
                  <small>{managed.role} · {managed.id.slice(0, 8)}</small>
                </div>
                <select
                  value={managed.role}
                  disabled={roleBusy === managed.id || managed.id === user?.id}
                  aria-label={`Role for ${managed.email ?? managed.id}`}
                  onChange={(event) =>
                    void changeRole(
                      managed,
                      event.target.value as "student" | "staff" | "superadmin",
                    )
                  }
                >
                  <option value="student">Student</option>
                  <option value="staff">Staff</option>
                  <option value="superadmin">Super admin</option>
                </select>
              </div>
            ))}
            {roleMessage && <p className="dash-note" role="status">{roleMessage}</p>}
          </div>
          <p className="dash-note">
            You can manage users in this campus. Your own role cannot be changed from this screen.
          </p>
        </DashCard>
      </div>
    </div>
  );
}
