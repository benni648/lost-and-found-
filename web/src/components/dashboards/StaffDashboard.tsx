import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { KIND_LABEL, timeAgo } from "../../lib/format";
import type { FeedbackItem, Report, Stats } from "../../types";
import DeskQueue from "../DeskQueue";
import { DashCard, DashError, DashTile } from "./parts";

interface Props {
  refreshKey: number;
  onBack: () => void;
  onOpenReport: (report: Report) => void;
}

/**
 * The staff desk: the recovery handover, the newest cases that need a human
 * match decision, and the labels staff have already applied. Staff do not see
 * the admin dashboard, and admins do not see this one.
 */
export default function StaffDashboard({ refreshKey, onBack, onOpenReport }: Props) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [awaiting, setAwaiting] = useState<number | null>(null);
  const [pendingClaims, setPendingClaims] = useState<number | null>(null);
  const [recent, setRecent] = useState<Report[]>([]);
  const [labels, setLabels] = useState<FeedbackItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statsPage, verified, pending, feedback, reports] = await Promise.all([
        api.stats(),
        api.claims("verified"),
        api.claims("pending"),
        api.feedbackList(6),
        api.reports(8),
      ]);
      setStats(statsPage);
      setAwaiting(verified.count);
      setPendingClaims(pending.count);
      setLabels(feedback.items);
      setRecent(reports.reports);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the staff dashboard.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const shown = (value: number | null) => (loading || value === null ? "—" : value);

  return (
    <div className="dashboard findly-dashboard">
      <header className="dash-head">
        <div>
          <span className="dash-eyebrow">Campus operations · staff access</span>
          <h2>Staff dashboard</h2>
          <p>
            Desk operations for this campus: close handovers, ratify the matcher&apos;s shortlists, and keep
            the labels it learns from honest.
          </p>
        </div>
        <div className="dash-head-actions">
          <button type="button" className="dash-back" onClick={onBack}>← Browse items</button>
          <button type="button" className="dash-refresh" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh queue"}
          </button>
        </div>
      </header>

      {error && <DashError error={error} onRetry={() => void load()} />}

      <div className="dash-tiles">
        <DashTile
          value={shown(stats?.reports ?? null)}
          label="reports in campus"
          note={stats ? `${stats.lost} lost · ${stats.found} found` : undefined}
        />
        <DashTile value={shown(stats?.matched ?? null)} label="matched reports" tone="good" />
        <DashTile
          value={shown(awaiting)}
          label="awaiting handover"
          note={pendingClaims === null ? undefined : `${pendingClaims} claims pending`}
          tone="warn"
        />
        <DashTile
          value={shown(stats?.confirmed ?? null)}
          label="confirmed matches"
          note={stats ? `${stats.rejected} rejected` : undefined}
        />
      </div>

      <DashCard title="Staff actions">
        <div className="dash-action-grid">
          <div className="dash-action">
            <span className="dash-action-icon">✓</span>
            <div><strong>Review matches</strong><small>Open a case and confirm or reject suggested matches.</small></div>
          </div>
          <div className="dash-action">
            <span className="dash-action-icon">↗</span>
            <div><strong>Complete handovers</strong><small>Verify the student&apos;s code and mark both reports recovered.</small></div>
          </div>
          <div className="dash-action">
            <span className="dash-action-icon">↺</span>
            <div><strong>Keep the queue current</strong><small>Refresh after a desk decision or a new claim.</small></div>
          </div>
        </div>
      </DashCard>

      <div className="dash-grid">
        <DashCard>
          <DeskQueue refreshKey={refreshKey} onCollected={() => void load()} />
        </DashCard>

        <DashCard title="Recent decisions" aside={<span className="muted mono">{labels.length}</span>}>
          {loading && (
            <p className="dash-note" aria-busy="true">
              Loading labels…
            </p>
          )}
          {!loading && labels.length === 0 && (
            <p className="dash-note">No confirm or reject labels yet. Open a case and judge its shortlist.</p>
          )}
          <ul className="dash-list">
            {labels.map((label) => (
              <li key={`${label.query_id}-${label.candidate_id}`} className="dash-row">
                <div className="dash-row-main">
                  <span className={`dash-pill is-${label.action === "confirm" ? "confirm" : "reject"}`}>
                    {label.action === "confirm" ? "confirmed" : "rejected"}
                  </span>
                  <span className="mono">
                    {label.query_id} ↔ {label.candidate_id}
                  </span>
                </div>
                <span className="muted mono">{timeAgo(label.created_at)}</span>
              </li>
            ))}
          </ul>
        </DashCard>
      </div>

      <DashCard title="Newest cases" aside={<span className="muted mono">{recent.length}</span>}>
        {loading && (
          <p className="dash-note" aria-busy="true">
            Loading cases…
          </p>
        )}
        {!loading && recent.length === 0 && <p className="dash-note">No reports in this campus yet.</p>}
        <ul className="dash-list">
          {recent.map((report) => (
            <li key={report.id} className="dash-row">
              <div className="dash-row-main">
                <span className={`dot dot-${report.kind}`} aria-hidden="true" />
                <span className="mono">{report.id}</span>
                <span className={`mine-status status-${report.status}`}>
                  {report.status.replace(/_/g, " ")}
                </span>
                <span className="dash-row-desc">{report.description || KIND_LABEL[report.kind]}</span>
              </div>
              <button type="button" className="dash-row-button" onClick={() => onOpenReport(report)}>
                Review matches
              </button>
            </li>
          ))}
        </ul>
      </DashCard>
    </div>
  );
}
