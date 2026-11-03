import { useState } from "react";
import Chips from "./Chips";
import ClaimForm from "./ClaimForm";
import FlapText from "./FlapText";
import CampusMap from "./CampusMap";
import type { Campus, FeedbackAction, MatchResult, OperatingPoint, Report } from "../types";
import { DECISION_COPY, KIND_LABEL, pct, timeAbsolute, timeAgo, verdictFor } from "../lib/format";

interface Props {
  result: MatchResult;
  operatingPoint: OperatingPoint;
  canReview: boolean;
  labels: Record<string, FeedbackAction>;
  onFeedback: (candidateId: string, action: FeedbackAction) => Promise<void>;
  onBack: () => void;
  campus: Campus;
  report: Report;
}

export default function MatchPanel({
  result,
  operatingPoint,
  canReview,
  labels,
  onFeedback,
  onBack,
  campus,
  report,
}: Props) {
  const copy = DECISION_COPY[result.decision];
  const threshold = operatingPoint.prob_alert;
  const floor = operatingPoint.plausible_floor;
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [claimingFor, setClaimingFor] = useState<string | null>(null);

  async function act(candidateId: string, action: FeedbackAction) {
    setActionError(null);
    setPending(candidateId);
    try {
      await onFeedback(candidateId, action);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not record that decision.");
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="match">
      <button className="link-back" onClick={onBack}>
        ← back to reports
      </button>

      <div className="query">
        <div className="query-head">
          <span className={`tag tag-${result.query.kind}`}>{KIND_LABEL[result.query.kind]}</span>
          <span className="mono query-id">{result.query.id}</span>
        </div>
        <div className="query-body">
          {result.query.image_url && (
            <img className="query-thumb" src={result.query.image_url} alt="Reported item" />
          )}
          <div className="query-copy">
            <p className="query-text">{result.query.description}</p>
            <div className="query-location">
              <span aria-hidden="true">⌖</span>
              <div>
                <strong>Last seen / found</strong>
                <span>{result.query.location || "Location not added"}</span>
              </div>
            </div>
            <p className="query-meta">
              {result.query.category ?? "unclassified"} ·{" "}
              <time
                dateTime={result.query.created_at}
                title={timeAbsolute(result.query.created_at, result.query.campus_timezone)}
              >
                {timeAgo(result.query.created_at)}
              </time>
            </p>
          </div>
        </div>
      </div>

      <section className="findly-detail-map">
        <div className="detail-map-heading">
          <div>
            <span className="info-eyebrow">Item location</span>
            <h3>{result.query.location || "Location not added"}</h3>
          </div>
          <span className="detail-map-label">Approximate location</span>
        </div>
        {report.latitude != null || report.node_key ? (
          <CampusMap
            campus={campus}
            reports={[report]}
            selectedId={report.id}
            onSelect={() => undefined}
          />
        ) : (
          <div className="detail-map-empty">
            <span aria-hidden="true">⌖</span>
            <strong>No map location was added</strong>
            <p>This report does not include a map pin.</p>
          </div>
        )}
      </section>

      <section className={`verdict verdict-${copy.tone}`}>
        <div className="verdict-head">
          <div>
            <h2>{copy.title}</h2>
            <p className="verdict-note">{copy.note}</p>
          </div>
          <div className="verdict-score">
            <FlapText className="verdict-num" value={pct(result.confidence)} />
            <span className="verdict-label">confidence</span>
          </div>
        </div>

        <div className="meter" aria-hidden="true">
          <div className="meter-fill" style={{ width: `${Math.min(100, result.confidence * 100)}%` }} />
          <div className="meter-tick" style={{ left: `${floor * 100}%` }} />
          <div className="meter-tick meter-tick-alert" style={{ left: `${threshold * 100}%` }} />
        </div>
        <div className="meter-scale">
          <span>0%</span>
          <span style={{ left: `${floor * 100}%` }} className="meter-scale-mark">
            plausible {pct(floor)}
          </span>
          <span style={{ left: `${threshold * 100}%` }} className="meter-scale-mark">
            alert {pct(threshold)}
          </span>
          <span>100%</span>
        </div>

        <p className="verdict-reason">
          {result.reason} · margin to runner-up <strong className="mono">{result.margin.toFixed(3)}</strong>
        </p>
      </section>

      <div className="candidates">
        <div className="candidates-head">
          <span className="field-label">Candidate shortlist</span>
          <span className="mono muted">{result.matches.length} shown</span>
        </div>

        {result.matches.length === 0 && <p className="empty">The candidate pool is empty.</p>}

        <ol className="cand-list">
          {result.matches.map((c, i) => {
            const v = verdictFor(c.prob, operatingPoint);
            return (
              <li key={c.candidate_id} className={`cand cand-${v.tone}`}>
                <div className="cand-top">
                  <span className="cand-rank mono">{i + 1}</span>
                  {c.image_url ? (
                    <img className="cand-thumb" src={c.image_url} alt="" loading="lazy" />
                  ) : (
                    <span className={`cand-thumb cand-thumb-empty dot-${c.kind}`} aria-hidden="true" />
                  )}
                  <div className="cand-main">
                    <p className="cand-desc">{c.description}</p>
                    <p className="cand-meta">
                      <span className="mono">{c.candidate_id}</span> · {c.location || "no location"} ·{" "}
                      <time dateTime={c.created_at} title={timeAbsolute(c.created_at, c.campus_timezone)}>
                        {timeAgo(c.created_at)}
                      </time>
                    </p>
                  </div>
                  <div className="cand-prob">
                    <span className="mono">{pct(c.prob)}</span>
                    <span className={`pill pill-${v.tone}`}>{v.label}</span>
                  </div>
                </div>

                <Chips reasons={c.reasons} limit={5} />

                {c.flags.length > 0 && (
                  <p className="cand-flags">
                    {c.flags.map((f) => f.replace(/_/g, " ")).join(" · ")} — treated as an identity conflict
                  </p>
                )}

                {/* Only the owner of the lost report is the claimant, so the
                    claim action exists solely on a lost query. */}
                {result.query.kind === "lost" && c.kind === "found" && (
                  <div className="cand-claim">
                    {claimingFor === c.candidate_id ? (
                      <ClaimForm
                        lostId={result.query.id}
                        foundId={c.candidate_id}
                        onClose={() => setClaimingFor(null)}
                      />
                    ) : (
                      <button
                        type="button"
                        className="btn-claim"
                        onClick={() => setClaimingFor(c.candidate_id)}
                      >
                        This is mine — claim it
                      </button>
                    )}
                  </div>
                )}

                {canReview ? (
                  <div className="cand-actions">
                    {labels[c.candidate_id] === "confirm" && (
                      <span className="cand-state state-confirm">
                        ✓ Confirmed by staff · both reports marked matched
                      </span>
                    )}
                    {labels[c.candidate_id] === "reject" && (
                      <span className="cand-state state-reject">✕ Rejected by staff</span>
                    )}
                    <div className="cand-buttons">
                      <button
                        className={`btn-feedback btn-confirm ${
                          labels[c.candidate_id] === "confirm" ? "is-active" : ""
                        }`}
                        disabled={pending === c.candidate_id}
                        onClick={() => void act(c.candidate_id, "confirm")}
                      >
                        {labels[c.candidate_id] === "confirm" ? "Confirmed" : "Confirm match"}
                      </button>
                      <button
                        className={`btn-feedback btn-reject ${
                          labels[c.candidate_id] === "reject" ? "is-active" : ""
                        }`}
                        disabled={pending === c.candidate_id}
                        onClick={() => void act(c.candidate_id, "reject")}
                      >
                        {labels[c.candidate_id] === "reject" ? "Rejected" : "Not this item"}
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="match-foot">Staff sign in to confirm or reject a candidate.</p>
                )}
              </li>
            );
          })}
        </ol>
      </div>

      {actionError && <p className="form-error">{actionError}</p>}

      <p className="match-foot">
        A match is a lead, not a handover: staff confirm or reject each candidate, and every decision is
        stored as a training label for the learned re-ranker.
      </p>
    </div>
  );
}
