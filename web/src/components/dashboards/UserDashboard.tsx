import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../../api";
import { loadOwnedReports, loadStoredClaims } from "../../lib/ownership";
import { roleLabel, type Role } from "../../lib/roles";
import type { AuthUser, ClaimSummary, Report } from "../../types";
import MyReports from "../MyReports";
import { DashCard, DashError, DashTile } from "./parts";

interface Props {
  user: AuthUser | null;
  role: Role;
  refreshKey: number;
  onOpenReport: (id: string) => void;
  onGoTo: (tab: "feed" | "report" | "mine") => void;
  onSignIn: (email: string, password: string) => Promise<void>;
  onSignOut: () => Promise<void>;
}

const ACTIVE_CLAIMS: ClaimSummary["status"][] = ["pending", "verified"];

/**
 * The dashboard every visitor has: the reports this browser filed, the state of
 * their matches, and the claims they started. Staff and admin dashboards are
 * additive; this one is the baseline, not a privilege.
 */
export default function UserDashboard({
  user,
  role,
  refreshKey,
  onOpenReport,
  onGoTo,
  onSignIn,
  onSignOut,
}: Props) {
  const [reports, setReports] = useState<Report[]>([]);
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setAuthBusy(true);
    setAuthError(null);
    try {
      await onSignIn(authEmail, authPassword);
      setAuthPassword("");
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    setAuthBusy(true);
    try {
      await onSignOut();
    } finally {
      setAuthBusy(false);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const owned = loadOwnedReports();
      const loaded = await Promise.all(
        owned.map(async (entry) => {
          try {
            return (await api.report(entry.id)).report;
          } catch {
            return null;
          }
        }),
      );
      setReports(loaded.filter((report): report is Report => report !== null));

      const stored = loadStoredClaims();
      const summaries = await Promise.all(
        stored.map(async (claim) => {
          try {
            return await api.lookupClaim(claim.claim_id, claim.claim_token);
          } catch {
            return null;
          }
        }),
      );
      setClaims(summaries.filter((claim): claim is ClaimSummary => claim !== null));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your dashboard.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const filed = reports.length;
  const awaiting = reports.filter((report) => report.status === "open").length;
  const resolved = reports.filter(
    (report) => report.status === "matched" || report.status === "recovered",
  ).length;
  const activeClaims = claims.filter((claim) => ACTIVE_CLAIMS.includes(claim.status)).length;
  const shown = (value: number) => (loading ? "—" : value);

  return (
    <div className="dashboard">
      <header className="dash-head">
        <div>
          <h2>My dashboard</h2>
          <p>Your reports, what the matcher found for them, and every claim you started from this browser.</p>
        </div>
      </header>

      {error && <DashError error={error} onRetry={() => void load()} />}

      <div className="dash-tiles">
        <DashTile value={shown(filed)} label="reports filed" note="from this browser" />
        <DashTile value={shown(awaiting)} label="awaiting a match" />
        <DashTile value={shown(resolved)} label="matched or recovered" tone="good" />
        <DashTile value={shown(activeClaims)} label="active claims" tone="warn" />
      </div>

      <div className="dash-grid">
        <DashCard title="Quick actions">
          <div className="dash-actions">
            <button type="button" className="dash-action" onClick={() => onGoTo("report")}>
              Report a lost or found item
            </button>
            <button type="button" className="dash-action" onClick={() => onGoTo("feed")}>
              Browse the feed
            </button>
            <button type="button" className="dash-action" onClick={() => onGoTo("mine")}>
              Open my reports
            </button>
          </div>
          <p className="dash-note">
            A lost report is matched against found reports and the other way round. If the evidence is thin
            the matcher says so instead of guessing, and the desk takes it from there.
          </p>
        </DashCard>

        <DashCard title="Your access">
          <div className="dash-scope">
            <span>
              Role <strong>{user ? roleLabel(role) : "guest"}</strong>
            </span>
            <span>
              Account <strong>{user?.email ?? user?.phone_e164 ?? user?.id ?? "not signed in"}</strong>
            </span>
            <span>
              Campus <strong>{user?.campus_id ?? "default"}</strong>
            </span>
          </div>

          {user ? (
            <div className="signin-row">
              <p className="dash-note">
                {role === "student"
                  ? "Staff and admin dashboards are not shown to this account. The API enforces the same rule, so a hidden button is never the only protection."
                  : "Your role also unlocks its own dashboard in the navigation above."}
              </p>
              <button type="button" className="btn-claim" onClick={() => void signOut()} disabled={authBusy}>
                {authBusy ? "Signing out…" : "Sign out"}
              </button>
            </div>
          ) : (
            <form className="signin-form" onSubmit={signIn}>
              <span className="field-label">Staff sign in</span>
              <input
                className="search"
                type="email"
                aria-label="Staff email"
                placeholder="Staff email"
                autoComplete="username"
                value={authEmail}
                onChange={(e) => setAuthEmail(e.target.value)}
                required
              />
              <input
                className="search"
                type="password"
                aria-label="Password"
                placeholder="Password"
                autoComplete="current-password"
                value={authPassword}
                onChange={(e) => setAuthPassword(e.target.value)}
                required
              />
              <button className="primary" type="submit" disabled={authBusy}>
                {authBusy ? "Signing in…" : "Sign in"}
              </button>
              {authError && <p className="form-error">{authError}</p>}
              <p className="dash-note">
                Desk and admin accounts are provisioned by the campus — there is no self-registration.
              </p>
            </form>
          )}
        </DashCard>
      </div>

      <DashCard>
        <MyReports refreshKey={refreshKey} onOpen={onOpenReport} />
      </DashCard>
    </div>
  );
}
