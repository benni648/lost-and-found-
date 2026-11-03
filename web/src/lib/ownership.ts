// Anonymous reporters have no account, so the browser remembers the one-time
// ownership token the API returned. It is the only proof that the person who
// filed a report is the person asking about it, and it never leaves this device
// except to prove ownership over HTTPS.
import type { Kind } from "../types";

const REPORTS_KEY = "lostfound.ownership.v1";
const CLAIMS_KEY = "lostfound.claims.v1";
const MAX_ENTRIES = 50;

export interface OwnedReport {
  id: string;
  kind: Kind;
  token: string;
  created_at: string;
}

export interface StoredClaim {
  claim_id: string;
  claim_token: string;
  query_id: string;
  created_at: string;
}

function read<T>(key: string): T[] {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function write(key: string, value: unknown[]): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value.slice(0, MAX_ENTRIES)));
  } catch {
    /* private mode or quota: the report is still filed, only the shortcut is lost */
  }
}

export function loadOwnedReports(): OwnedReport[] {
  return read<OwnedReport>(REPORTS_KEY);
}

export function rememberReport(report: { id: string; kind: Kind; created_at: string }, token: string): void {
  const rest = loadOwnedReports().filter((entry) => entry.id !== report.id);
  write(REPORTS_KEY, [{ id: report.id, kind: report.kind, token, created_at: report.created_at }, ...rest]);
}

export function forgetReport(id: string): void {
  write(
    REPORTS_KEY,
    loadOwnedReports().filter((entry) => entry.id !== id),
  );
}

export function loadStoredClaims(): StoredClaim[] {
  return read<StoredClaim>(CLAIMS_KEY);
}

export function rememberClaim(claim: StoredClaim): void {
  const rest = loadStoredClaims().filter((entry) => entry.claim_id !== claim.claim_id);
  write(CLAIMS_KEY, [claim, ...rest]);
}
