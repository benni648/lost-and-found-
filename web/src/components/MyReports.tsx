import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { forgetReport, loadOwnedReports, loadStoredClaims } from "../lib/ownership";
import type { ClaimSummary, Report } from "../types";
import { KIND_LABEL, timeAgo } from "../lib/format";

interface Props {
  onOpen: (reportId: string) => void;
  refreshKey: number;
}

interface ReportRow {
  report: Report;
  token: string;
}

interface ClaimRow {
  claim_id: string;
  query_id: string;
  summary: ClaimSummary | null;
}

const CLAIM_COPY: Record<ClaimSummary["status"], string> = {
  pending: "waiting for the desk",
  verified: "verified — collect at the desk",
  collected: "handed over",
  rejected: "rejected",
  expired: "expired",
};

/**
 * Anonymous reports are tracked by the ownership token this browser kept. No
 * account is required to come back and see a report, prove ownership, or follow
 * a claim — which is the student-facing half of "students and staff".
 */
export default function MyReports({ onOpen, refreshKey }: Props) {
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [missing, setMissing] = useState<string[]>([]);
  const [claims, setClaims] = useState<ClaimRow[]>([]);
  const [verified, setVerified] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const owned = loadOwnedReports();
    const loaded = await Promise.all(
      owned.map(async (entry) => {
        try {
          const { report } = await api.report(entry.id);
          return { report, token: entry.token } satisfies ReportRow;
        } catch {
          return null;
        }
      }),
    );
    setRows(loaded.filter((row): row is ReportRow => row !== null));
    setMissing(owned.filter((_, index) => loaded[index] === null).map((entry) => entry.id));

    const stored = loadStoredClaims();
    const summarised = await Promise.all(
      stored.map(async (claim): Promise<ClaimRow | null> => {
        try {
          return {
            claim_id: claim.claim_id,
            query_id: claim.query_id,
            summary: await api.lookupClaim(claim.claim_id, claim.claim_token),
          };
        } catch {
          return null;
        }
      }),
    );
    setClaims(summarised.filter((row): row is ClaimRow => row !== null));
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function proveOwnership(id: string, token: string) {
    setError(null);
    try {
      await api.verifyOwnership(id, token);
      setVerified((prev) => ({ ...prev, [id]: true }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not verify ownership.");
    }
  }

  if (rows.length === 0 && missing.length === 0 && claims.length === 0) {
    return (
      <p className="empty">
        Reports you file from this browser appear here, with their status and any claim you started. Nothing
        is stored on the server beyond the report itself.
      </p>
    );
  }

  return (
    <div className="mine">
      <h3 className="mine-head">Reports filed from this browser</h3>
      <ul className="mine-list">
        {rows.map(({ report, token }) => (
          <li key={report.id} className="mine-row">
            <div className="mine-top">
              <span className={`kind-label kind-${report.kind}`}>{KIND_LABEL[report.kind]}</span>
              <span className="mono">{report.id}</span>
              <span className={`mine-status status-${report.status}`}>{report.status.replace(/_/g, " ")}</span>
            </div>
            <p className="mine-desc">{report.description || "Photo-only report"}</p>
            <p className="mine-meta">
              {report.location || "no location"} · <time dateTime={report.created_at}>{timeAgo(report.created_at)}</time>
            </p>
            <div className="mine-actions">
              <button type="button" className="btn-claim" onClick={() => onOpen(report.id)}>
                {report.kind === "lost" ? "See possible matches" : "See who reported it"}
              </button>
              {verified[report.id] ? (
                <span className="mine-verified">✓ ownership verified</span>
              ) : (
                <button type="button" className="link-back" onClick={() => void proveOwnership(report.id, token)}>
                  Prove ownership
                </button>
              )}
              <button type="button" className="link-back" onClick={() => { forgetReport(report.id); void load(); }}>
                Remove
              </button>
            </div>
          </li>
        ))}
      </ul>

      {missing.length > 0 && (
        <p className="mine-missing">
          {missing.length} saved report{missing.length === 1 ? "" : "s"} no longer visible (removed or still in
          review). {missing.join(", ")}
        </p>
      )}

      {claims.length > 0 && (
        <>
          <h3 className="mine-head">My claims</h3>
          <ul className="mine-list">
            {claims.map((claim) => (
              <li key={claim.claim_id} className="mine-row">
                <div className="mine-top">
                  <span className="mono">{claim.claim_id}</span>
                  <span className={`mine-status status-${claim.summary?.status ?? "pending"}`}>
                    {claim.summary ? CLAIM_COPY[claim.summary.status] : "status unavailable"}
                  </span>
                </div>
                <p className="mine-meta">
                  for {claim.query_id} · opened <time dateTime={claim.summary?.created_at ?? ""}>{timeAgo(claim.summary?.created_at ?? "")}</time>
                </p>
                <p className="claim-note">
                  {claim.summary?.status === "verified"
                    ? "Show the six-digit code you were given, and photo ID, at the lost property desk."
                    : claim.summary?.status === "collected"
                      ? "This case is closed."
                      : "The desk will contact you with the next step."}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}

      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
