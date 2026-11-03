import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ClaimSummary } from "../types";
import { timeAgo } from "../lib/format";

interface Props {
  refreshKey: number;
  onCollected?: () => void;
}

/**
 * The other half of the handover: staff see verified claims, read the code the
 * student shows them, and close the counter. That single action marks both
 * reports recovered and writes an audit entry.
 */
export default function DeskQueue({ refreshKey, onCollected }: Props) {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [verified, pending] = await Promise.all([api.claims("verified"), api.claims("pending")]);
      setClaims([...verified.claims, ...pending.claims]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the claim queue.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function collect(claimId: string) {
    setBusy(claimId);
    setError(null);
    setMessage(null);
    try {
      const res = await api.collectClaim(claimId, codes[claimId] ?? "");
      setMessage(`${res.reports.join(" + ")} marked recovered. Handover recorded.`);
      setCodes((prev) => ({ ...prev, [claimId]: "" }));
      await load();
      onCollected?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not complete the handover.");
    } finally {
      setBusy(null);
    }
  }

  if (claims.length === 0 && !error) {
    return <p className="empty">No claims waiting. Verified claims appear here for the counter handover.</p>;
  }

  return (
    <div className="desk">
      <h3 className="mine-head">Recovery queue</h3>
      <ul className="mine-list">
        {claims.map((claim) => (
          <li key={claim.claim_id} className="mine-row">
            <div className="mine-top">
              <span className="mono">{claim.claim_id}</span>
              <span className={`mine-status status-${claim.status}`}>{claim.status}</span>
              <span className="muted mono">
                {claim.query_id} ↔ {claim.candidate_id}
              </span>
            </div>
            <p className="mine-meta">
              opened <time dateTime={claim.created_at}>{timeAgo(claim.created_at)}</time>
            </p>
            {claim.status === "verified" ? (
              <div className="desk-collect">
                <input
                  value={codes[claim.claim_id] ?? ""}
                  onChange={(event) =>
                    setCodes((prev) => ({ ...prev, [claim.claim_id]: event.target.value }))
                  }
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="6-digit code"
                  aria-label={`Handover code for ${claim.claim_id}`}
                />
                <button
                  type="button"
                  className="btn-claim"
                  disabled={busy === claim.claim_id || (codes[claim.claim_id] ?? "").length < 4}
                  onClick={() => void collect(claim.claim_id)}
                >
                  {busy === claim.claim_id ? "Recording…" : "Complete handover"}
                </button>
              </div>
            ) : (
              <p className="claim-note">
                Too few details to verify online. Check photo ID and something that identifies the item, then
                collect it against the lost report.
              </p>
            )}
          </li>
        ))}
      </ul>
      {message && <p className="mine-verified">{message}</p>}
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
