import { useEffect, useState } from "react";
import { api } from "../api";
import { rememberClaim } from "../lib/ownership";
import type { ClaimChallenge, ClaimResult } from "../types";

interface Props {
  lostId: string;
  foundId: string;
  onClose: () => void;
  onClaimed?: (result: ClaimResult) => void;
}

/**
 * Answer the challenge questions drawn from the claimant's own lost report.
 * Two outcomes matter: a verified claim shows a one-time handover code, and a
 * report with too few distinguishing details goes to the desk instead of being
 * refused.
 */
export default function ClaimForm({ lostId, foundId, onClose, onClaimed }: Props) {
  const [challenge, setChallenge] = useState<ClaimChallenge | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ClaimResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .claimChallenge(lostId, foundId)
      .then((data) => {
        if (!cancelled) setChallenge(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load the challenge.");
      });
    return () => {
      cancelled = true;
    };
  }, [lostId, foundId]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.createClaim({
        query_id: lostId,
        candidate_id: foundId,
        answers,
      });
      rememberClaim({
        claim_id: res.claim_id,
        claim_token: res.claim_token,
        query_id: lostId,
        created_at: new Date().toISOString(),
      });
      setResult(res);
      onClaimed?.(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not place the claim.");
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="claim-box claim-done" role="status">
        {result.handover_code ? (
          <>
            <p className="claim-title">Verified — your handover code</p>
            <p className="handover-code mono">{result.handover_code}</p>
            <p className="claim-note">{result.note}</p>
          </>
        ) : (
          <>
            <p className="claim-title">Sent to the desk</p>
            <p className="claim-note">{result.reason}</p>
          </>
        )}
        <button type="button" className="btn-claim" onClick={onClose}>
          Close
        </button>
      </div>
    );
  }

  if (error && !challenge) {
    return (
      <div className="claim-box">
        <p className="form-error">{error}</p>
        <button type="button" className="btn-claim" onClick={onClose}>
          Close
        </button>
      </div>
    );
  }

  if (!challenge) {
    return (
      <div className="claim-box">
        <p className="claim-note">Loading the questions only you can answer…</p>
      </div>
    );
  }

  if (!challenge.verifiable_online) {
    return (
      <div className="claim-box">
        <p className="claim-title">The desk will check this one</p>
        <p className="claim-note">
          Your report has too few distinguishing details to prove ownership online. Send it to the desk and
          bring something that identifies the item.
        </p>
        <form onSubmit={submit} className="claim-actions">
          <button type="submit" className="btn-claim" disabled={busy}>
            {busy ? "Sending…" : "Ask the desk to verify"}
          </button>
          <button type="button" className="link-back" onClick={onClose}>
            Cancel
          </button>
        </form>
        {error && <p className="form-error">{error}</p>}
      </div>
    );
  }

  return (
    <form className="claim-box" onSubmit={submit}>
      <p className="claim-title">Prove this is yours</p>
      <p className="claim-note">
        These questions come from the report you filed. Answers are checked and then discarded — they are
        never stored.
      </p>
      {challenge.questions.map((question) => (
        <label key={question.key} className="claim-field">
          <span>{question.prompt}</span>
          <input
            value={answers[question.key] ?? ""}
            onChange={(event) => setAnswers((prev) => ({ ...prev, [question.key]: event.target.value }))}
            autoComplete="off"
            required
          />
        </label>
      ))}
      <div className="claim-actions">
        <button type="submit" className="btn-claim" disabled={busy}>
          {busy ? "Checking…" : "Submit claim"}
        </button>
        <button type="button" className="link-back" onClick={onClose}>
          Cancel
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </form>
  );
}
