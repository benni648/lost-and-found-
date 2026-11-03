import type {
  Campus,
  ExtractPreview,
  FeedbackAction,
  FeedbackPage,
  Kind,
  MatchResult,
  AuthSession,
  AuthUser,
  ManagedUser,
  ClaimChallenge,
  ClaimResult,
  ClaimSummary,
  OperatingPoint,
  PublicStats,
  Report,
  ReportPage,
  Stats,
} from "./types";

const BASE = "";
const SESSION_STORAGE_KEY = "findly.auth.session";
type StoredSession = Pick<AuthSession, "access_token" | "refresh_token">;

function readStoredSession(): StoredSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredSession>;
    return parsed.access_token && parsed.refresh_token
      ? { access_token: parsed.access_token, refresh_token: parsed.refresh_token }
      : null;
  } catch {
    return null;
  }
}

const storedSession = readStoredSession();
let accessToken: string | null = storedSession?.access_token ?? null;
let refreshToken: string | null = storedSession?.refresh_token ?? null;
let refreshPromise: Promise<string> | null = null;
let sessionExpiredHandler: (() => void) | null = null;

function setAuthorization(init: RequestInit | undefined, token: string | null): RequestInit {
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  else headers.delete("Authorization");
  return { ...init, headers };
}

function expireSession(): void {
  const hadSession = accessToken !== null || refreshToken !== null;
  accessToken = null;
  refreshToken = null;
  refreshPromise = null;
  if (typeof window !== "undefined") window.localStorage.removeItem(SESSION_STORAGE_KEY);
  if (hadSession) sessionExpiredHandler?.();
}

function refreshAccessToken(): Promise<string> {
  if (refreshPromise) return refreshPromise;
  const currentRefreshToken = refreshToken;
  if (!currentRefreshToken) {
    expireSession();
    return Promise.reject(new Error("Your session expired. Please sign in again."));
  }

  refreshPromise = (async () => {
    let response: Response;
    try {
      response = await fetch(`${BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: currentRefreshToken }),
      });
    } catch {
      throw new Error("Cannot reach the API. Start it with: python -m lostfound.cli serve");
    }

    if (!response.ok) {
      expireSession();
      throw new Error("Your session expired. Please sign in again.");
    }

    let session: AuthSession;
    try {
      session = (await response.json()) as AuthSession;
    } catch {
      expireSession();
      throw new Error("The API returned an invalid refreshed session.");
    }
    if (!session.access_token || !session.refresh_token) {
      expireSession();
      throw new Error("The API returned an invalid refreshed session.");
    }
    accessToken = session.access_token;
    refreshToken = session.refresh_token;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        SESSION_STORAGE_KEY,
        JSON.stringify({ access_token: accessToken, refresh_token: refreshToken }),
      );
    }
    return accessToken;
  })();

  void refreshPromise.catch(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function responseError(res: Response): Promise<Error> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* keep the status text */
  }
  return new Error(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const originalToken = accessToken;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, setAuthorization(init, originalToken));
  } catch {
    throw new Error("Cannot reach the API. Start it with: python -m lostfound.cli serve");
  }
  const mayRefresh =
    res.status === 401 &&
    originalToken !== null &&
    path !== "/api/auth/login" &&
    path !== "/api/auth/register" &&
    path !== "/api/auth/refresh";
  if (mayRefresh) {
    const latestToken = accessToken !== originalToken ? accessToken : await refreshAccessToken();
    if (!latestToken) {
      expireSession();
      throw new Error("Your session expired. Please sign in again.");
    }
    try {
      res = await fetch(`${BASE}${path}`, setAuthorization(init, latestToken));
    } catch {
      throw new Error("Cannot reach the API. Start it with: python -m lostfound.cli serve");
    }
  }
  if (!res.ok) {
    throw await responseError(res);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  setSession: (session: AuthSession | null) => {
    accessToken = session?.access_token ?? null;
    refreshToken = session?.refresh_token ?? null;
    refreshPromise = null;
    if (typeof window !== "undefined") {
      if (session) {
        window.localStorage.setItem(
          SESSION_STORAGE_KEY,
          JSON.stringify({ access_token: accessToken, refresh_token: refreshToken }),
        );
      } else {
        window.localStorage.removeItem(SESSION_STORAGE_KEY);
      }
    }
  },
  setSessionExpiredHandler: (handler: (() => void) | null) => {
    sessionExpiredHandler = handler;
  },
  login: (email: string, password: string) =>
    request<AuthSession>("/api/auth/login", json({ email, password })),
  register: (email: string, password: string) =>
    request<Partial<AuthSession> & { user?: AuthUser }>("/api/auth/register", json({ email, password })),
  me: () => request<AuthUser>("/api/auth/me"),
  adminUsers: () => request<{ users: ManagedUser[] }>("/api/admin/users"),
  adminSetRole: (userId: string, role: "student" | "staff" | "superadmin") =>
    request<ManagedUser>(`/api/admin/users/${encodeURIComponent(userId)}/role`, {
      ...json({ role }),
      method: "PATCH",
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  campus: () => request<{ campus: Campus; stats: string }>("/api/campus"),
  publicStats: () => request<PublicStats>("/api/public/stats"),
  stats: () => request<Stats>("/api/stats"),
  feedbackList: (limit = 10) => request<FeedbackPage>(`/api/feedback?limit=${limit}`),
  operatingPoint: () => request<OperatingPoint>("/api/config/operating-point"),
  reports: (limit = 100, cursor?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) params.set("cursor", cursor);
    return request<ReportPage>(`/api/reports?${params.toString()}`);
  },
  report: (id: string) => request<{ report: Report }>(`/api/reports/${encodeURIComponent(id)}`),
  verifyOwnership: (id: string, ownershipToken: string) =>
    request<{ ok: boolean; report_id: string; status: string }>(
      `/api/reports/${encodeURIComponent(id)}/claim`,
      json({ ownership_token: ownershipToken }),
    ),
  match: (id: string, top = 5) =>
    request<MatchResult>(`/api/match/${encodeURIComponent(id)}?top=${top}`, { method: "POST" }),
  extract: (description: string) => request<ExtractPreview>("/api/extract", json({ description })),
  create: (body: {
    kind: Kind;
    description: string;
    node_key?: string | null;
    latitude?: number | null;
    longitude?: number | null;
    location_accuracy_m?: number | null;
    lost_from?: string | null;
    lost_to?: string | null;
    contact_email: string;
    contact_phone: string;
    website: string;
    form_started_at: number;
  }) => request<{ report: Report; ownership_token: string }>("/api/reports", json(body)),
  createWithPhoto: (form: FormData) =>
    request<{ report: Report; ownership_token: string }>("/api/reports/upload", {
      method: "POST",
      body: form,
    }),
  feedback: (body: { query_id: string; candidate_id: string; action: FeedbackAction; staff?: string }) =>
    request<{ ok: boolean; status: string; counts: { confirmed: number; rejected: number } }>(
      "/api/feedback",
      json(body),
    ),
  claimChallenge: (queryId: string, candidateId: string) => {
    const params = new URLSearchParams({ query_id: queryId, candidate_id: candidateId });
    return request<ClaimChallenge>(`/api/claims/challenge?${params.toString()}`);
  },
  createClaim: (body: {
    query_id: string;
    candidate_id: string;
    answers: Record<string, string>;
    contact_email?: string;
  }) => request<ClaimResult>("/api/claims", json(body)),
  lookupClaim: (claimId: string, token: string) =>
    request<ClaimSummary>(`/api/claims/${encodeURIComponent(claimId)}/lookup`, json({ token })),
  claims: (status?: string) =>
    request<{ count: number; claims: ClaimSummary[] }>(
      `/api/claims${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  collectClaim: (claimId: string, code: string) =>
    request<{ ok: boolean; claim_id: string; status: string; reports: string[] }>(
      `/api/claims/${encodeURIComponent(claimId)}/collect`,
      json({ code }),
    ),
};
