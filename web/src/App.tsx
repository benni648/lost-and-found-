import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api } from "./api";
import AdminDashboard from "./components/dashboards/AdminDashboard";
import StaffDashboard from "./components/dashboards/StaffDashboard";
import UserDashboard from "./components/dashboards/UserDashboard";
import MatchPanel from "./components/MatchPanel";
import MyReports from "./components/MyReports";
import ReportForm from "./components/ReportForm";
import ReportList from "./components/ReportList";
import { DASHBOARD_LABEL, dashboardsFor, roleOf, type View } from "./lib/roles";
import { loadOwnedReports, rememberReport } from "./lib/ownership";
import type {
  Campus,
  AuthUser,
  AuthSession,
  FeedbackAction,
  Kind,
  MatchResult,
  OperatingPoint,
  PublicStats,
  Report,
  ReportDraft,
} from "./types";

type Tab = "feed" | "report" | "mine";
type InfoPage = "browse" | "how" | "safety" | "login" | "register";

export default function App() {
  const [campus, setCampus] = useState<Campus | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [operatingPoint, setOperatingPoint] = useState<OperatingPoint | null>(null);
  const [tab, setTab] = useState<Tab>("feed");
  const [infoPage, setInfoPage] = useState<InfoPage>("browse");
  const [kind, setKind] = useState<"all" | Kind>("all");
  const [category, setCategory] = useState("All Items");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Report | null>(null);
  const [result, setResult] = useState<MatchResult | null>(null);
  const [feedback, setFeedback] = useState<Record<string, FeedbackAction>>({});
  const [matching, setMatching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ownershipToken, setOwnershipToken] = useState<string | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [mineRefresh, setMineRefresh] = useState(0);
  const [view, setView] = useState<View>("workspace");
  const restoringView = useRef(false);
  const ownedCount = useMemo(() => loadOwnedReports().length, [mineRefresh]);
  const role = roleOf(authUser);
  const dashboards = useMemo(() => dashboardsFor(role), [role]);

  // Signing in as another role, or signing out, must not leave a dashboard open
  // that the new role is not offered. The API rejects those calls anyway; the
  // navigation should never point at a view the role cannot use.
  useEffect(() => {
    if (view !== "workspace" && !dashboards.includes(view)) setView("workspace");
  }, [view, dashboards]);

  useEffect(() => {
    if (!authUser) return;
    const savedView = window.localStorage.getItem("findly.active.view");
    if ((savedView === "user" || savedView === "staff" || savedView === "admin") && dashboards.includes(savedView)) {
      restoringView.current = true;
      setView(savedView);
    }
  }, [authUser, dashboards]);

  useEffect(() => {
    if (restoringView.current) {
      restoringView.current = false;
      return;
    }
    if (authUser && view !== "workspace") window.localStorage.setItem("findly.active.view", view);
  }, [authUser, view]);

  // Render only what this role is offered, even for the frame before the effect
  // above resets the state: a signed-out tab must not call a privileged endpoint.
  const activeView: View = view === "workspace" || dashboards.includes(view) ? view : "workspace";

  const load = useCallback(async () => {
    try {
      const [c, s, r, o] = await Promise.all([
        api.campus(),
        api.publicStats(),
        api.reports(100),
        api.operatingPoint(),
      ]);
      setCampus(c.campus);
      setStats(s);
      setOperatingPoint(o);
      setReports(r.reports);
      setNextCursor(r.next_cursor);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the corpus.");
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await api.reports(100, nextCursor);
      setReports((current) => [...current, ...page.reports]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load more reports.");
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, nextCursor]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.setSessionExpiredHandler(() => {
      setAuthUser(null);
      setError("Your session expired. Please sign in again.");
    });
    return () => api.setSessionExpiredHandler(null);
  }, []);

  useEffect(() => {
    if (!window.localStorage.getItem("findly.auth.session")) return;
    void api.me()
      .then(setAuthUser)
      .catch(() => {
        api.setSession(null);
      });
  }, []);

  const closeMatch = useCallback(() => {
    setResult(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("report");
    window.history.replaceState({}, "", url);
  }, []);

  const openMatch = useCallback(async (report: Report) => {
    setSelected(report);
    setResult(null);
    setFeedback({});
    setMatching(true);
    setError(null);
    // deep-linkable: /?report=LR-0007 opens that case (handy for the desk)
    const url = new URL(window.location.href);
    url.searchParams.set("report", report.id);
    window.history.replaceState({}, "", url);
    try {
      const res = await api.match(report.id, 5);
      setResult(res);
      setFeedback(res.feedback ?? {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Matching failed.");
    } finally {
      setMatching(false);
    }
  }, []);

  const sendFeedback = useCallback(
    async (candidateId: string, action: FeedbackAction) => {
      if (!selected) return;
      await api.feedback({ query_id: selected.id, candidate_id: candidateId, action });
      setFeedback((prev) => ({ ...prev, [candidateId]: action }));
      void load(); // statuses and the confirmed counter
    },
    [selected, load],
  );

  const openReportById = useCallback(
    async (id: string) => {
      const target = reports.find((r) => r.id === id);
      if (target) {
        await openMatch(target);
        return;
      }
      try {
        const { report } = await api.report(id);
        await openMatch(report);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load that report.");
      }
    },
    [reports, openMatch],
  );

  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current || reports.length === 0) return;
    deepLinked.current = true;
    const id = new URLSearchParams(window.location.search).get("report");
    if (!id) return;
    const target = reports.find((r) => r.id === id);
    if (target) {
      void openMatch(target);
      return;
    }
    void api
      .report(id)
      .then(({ report }) => openMatch(report))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Could not load the linked report."),
      );
  }, [reports, openMatch]);

  async function submitReport(draft: ReportDraft) {
    if (!authUser) {
      setInfoPage("login");
      setError("Please sign in or create an account before reporting an item.");
      return;
    }
    setBusy(true);
    setOwnershipToken(null);
    try {
      let created: { report: Report; ownership_token: string };
      if (draft.photos.length > 0) {
        const form = new FormData();
        form.append("kind", draft.kind);
        form.append("description", draft.description);
        if (draft.node_key) form.append("node_key", draft.node_key);
        if (draft.latitude != null) form.append("latitude", String(draft.latitude));
        if (draft.longitude != null) form.append("longitude", String(draft.longitude));
        if (draft.location_accuracy_m != null)
          form.append("location_accuracy_m", String(draft.location_accuracy_m));
        if (draft.lost_from) form.append("lost_from", draft.lost_from);
        if (draft.lost_to) form.append("lost_to", draft.lost_to);
        form.append("contact_email", draft.contact_email);
        form.append("contact_phone", draft.contact_phone);
        form.append("website", draft.website);
        form.append("form_started_at", String(draft.form_started_at));
        for (const photo of draft.photos.slice(0, 4)) form.append("photos", photo);
        created = await api.createWithPhoto(form);
      } else {
        created = await api.create({
          kind: draft.kind,
          description: draft.description,
          node_key: draft.node_key,
          latitude: draft.latitude,
          longitude: draft.longitude,
          location_accuracy_m: draft.location_accuracy_m,
          lost_from: draft.lost_from,
          lost_to: draft.lost_to,
          contact_email: draft.contact_email,
          contact_phone: draft.contact_phone,
          website: draft.website,
          form_started_at: draft.form_started_at,
        });
      }
      setOwnershipToken(created.ownership_token);
      rememberReport(created.report, created.ownership_token);
      setMineRefresh((n) => n + 1);
      await load();
      setSelected(created.report);
      setTab("feed");
      if (created.report.status === "pending_review") {
        setResult(null);
        setError("Thanks. Your report is queued for a quick review before it appears in the feed.");
        return;
      }
      setMatching(true);
      setResult(null);
      const res = await api.match(created.report.id, 5);
      setResult(res);
      setFeedback(res.feedback ?? {});
    } finally {
      setBusy(false);
      setMatching(false);
    }
  }

  const signIn = useCallback(async (email: string, password: string) => {
    const session = await api.login(email, password);
    api.setSession(session);
    const user = await api.me();
    setAuthUser(user);
    setView("workspace");
    setInfoPage("browse");
    setTab("feed");
    closeMatch();
    setError(null);
  }, [closeMatch]);

  const register = useCallback(async (email: string, password: string) => {
    const result = await api.register(email, password);
    if (result.access_token && result.refresh_token) {
      api.setSession(result as AuthSession);
      const user = await api.me();
      setAuthUser(user);
      setView("workspace");
      setInfoPage("browse");
      setTab("feed");
      setError(null);
      return;
    }
    setInfoPage("login");
    setError("Account created. Check your email to verify your account, then sign in.");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // The local session is dropped either way.
    }
    api.setSession(null);
    setAuthUser(null);
    setView("workspace");
    window.localStorage.removeItem("findly.active.view");
  }, []);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return reports.filter((r) => {
      if (kind !== "all" && r.kind !== kind) return false;
      if (!matchesCategory(r.category, category)) return false;
      if (!q) return true;
      return (
        r.description.toLowerCase().includes(q) ||
        (r.category ?? "").toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q)
      );
    });
  }, [reports, kind, category, query]);

  const showingMatch = matching || result !== null;

  return (
    <div className="shell">
      <header className="topbar findly-topbar">
        <div className="brand">
          <div className="findly-pin" aria-hidden="true">●</div>
          <h1>Findly</h1>
        </div>
        <nav className="findly-nav">
          <button className={infoPage === "browse" && tab === "feed" && activeView === "workspace" ? "is-active" : ""} onClick={() => { setView("workspace"); setInfoPage("browse"); setTab("feed"); closeMatch(); }}>Browse Items</button>
          <button className={infoPage === "how" && activeView === "workspace" ? "is-active" : ""} onClick={() => { setView("workspace"); setInfoPage("how"); closeMatch(); }}>How It Works</button>
          <button className={infoPage === "safety" && activeView === "workspace" ? "is-active" : ""} onClick={() => { setView("workspace"); setInfoPage("safety"); closeMatch(); }}>Safety</button>
        </nav>
        <div className="findly-actions">
          {authUser ? (
            <button className="account-control" onClick={() => void signOut()} title="Sign out" aria-label="Sign out">
              <span className="account-avatar">{(authUser.email ?? "U").slice(0, 1).toUpperCase()}</span>
            </button>
          ) : (
            <button className="sign-in" onClick={() => { setError(null); setInfoPage("login"); closeMatch(); }}>Sign In</button>
          )}
          {authUser && dashboards.length > 0 && (
            <div className="findly-dashboard-switcher" aria-label="Dashboards">
              {dashboards.map((dashboard) => (
                <button
                  key={dashboard}
                  type="button"
                  className={activeView === dashboard ? "is-active" : ""}
                  onClick={() => {
                    setView(dashboard);
                    setInfoPage("browse");
                    closeMatch();
                  }}
                >
                  {DASHBOARD_LABEL[dashboard]}
                </button>
              ))}
            </div>
          )}
          <button className="report-item" onClick={() => {
            closeMatch();
            if (!authUser) {
              setInfoPage("login");
              setError("Please sign in or create an account before reporting an item.");
              return;
            }
            setInfoPage("browse");
            setTab("report");
          }}>Report an Item</button>
        </div>
      </header>

      {error && (
        <div className="banner-error" role="alert">
          {error}
          <button onClick={() => void load()}>retry</button>
        </div>
      )}
      {ownershipToken && (
        <div className="ownership-note" role="status">
          Save this one-time ownership token to verify a later claim: <code>{ownershipToken}</code>
        </div>
      )}

      {activeView === "workspace" && (infoPage === "how" || infoPage === "safety") ? (
        <InfoPage
          page={infoPage}
          onBrowse={() => { setInfoPage("browse"); setTab("feed"); }}
          onReport={() => {
            if (!authUser) {
              setInfoPage("login");
              setError("Please sign in or create an account before reporting an item.");
              return;
            }
            setInfoPage("browse");
            setTab("report");
          }}
        />
      ) : activeView === "workspace" && infoPage === "login" ? (
        <LoginPage
          onSignIn={signIn}
          onCreateAccount={() => { setError(null); setInfoPage("register"); }}
          onBack={() => { setInfoPage("browse"); setTab("feed"); }}
        />
      ) : activeView === "workspace" && infoPage === "register" ? (
        <RegisterPage
          onRegister={register}
          onSignIn={() => setInfoPage("login")}
          onBack={() => { setInfoPage("browse"); setTab("feed"); }}
        />
      ) : activeView === "workspace" ? (
        <div className={tab === "feed" && !showingMatch ? "findly-home" : "layout findly-secondary"}>
          {tab === "feed" && !showingMatch && (
            <>
              <section className="findly-hero">
                <div>
                  <h2>Lost something?<br /><em>Let’s bring it home.</em></h2>
                  <p>A kinder, safer place to reunite people with<br />the things that matter.</p>
                </div>
                <div className="findly-search-area">
                  <div className="findly-stat-bar" aria-label="public stats">
                    <span>{stats?.reports ?? 0} reports</span>
                    <span>{stats?.lost ?? 0} lost</span>
                    <span>{stats?.found ?? 0} found</span>
                  </div>
                  <div className="findly-kind-toggle">
                    <button className={kind !== "found" ? "is-active" : ""} onClick={() => setKind("lost")}>Lost</button>
                    <button className={kind === "found" ? "is-active" : ""} onClick={() => setKind("found")}>Found</button>
                  </div>
                  <input className="hero-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search item, location, or date" />
                </div>
              </section>
              <section className="trust-strip" id="safety">
                <div><b>♢</b><span><strong>Verified reports</strong><small>Real people, real items, safer reunions.</small></span></div>
                <div><b>▤</b><span><strong>Privacy-safe messaging</strong><small>Keep your personal details protected.</small></span></div>
                <div><b>♧</b><span><strong>Community matches</strong><small>More eyes, more happy endings.</small></span></div>
              </section>
              <div className="findly-section-heading">
                <h3>Recently reported items</h3><button>View all items →</button>
              </div>
            </>
          )}
          {tab === "report" && !showingMatch && (
            <section className="report-intro">
              <h2>Report a lost or found item</h2>
              <p>Help reunite people with their belongings. Fill in the details below.</p>
              <div className="report-steps" aria-label="Report progress">
                <span className="is-active"><b>1</b><small>Details</small></span>
                <span><b>2</b><small>Photos</small></span>
                <span><b>3</b><small>Location</small></span>
                <span><b>4</b><small>Contact</small></span>
              </div>
            </section>
          )}
          <section className={tab === "feed" && !showingMatch ? "findly-content" : "panel panel-side"}>
            <div className="panel-head">
              <div className="segmented" role="tablist" aria-label="Work area">
                <button
                  role="tab"
                  aria-selected={tab === "feed" && !showingMatch}
                  className={tab === "feed" && !showingMatch ? "is-active" : ""}
                  onClick={() => {
                    setInfoPage("browse");
                    setTab("feed");
                    closeMatch();
                  }}
                >
                  Browse
                </button>
                <button
                  role="tab"
                  aria-selected={tab === "report" && !showingMatch}
                  className={tab === "report" && !showingMatch ? "is-active" : ""}
                  onClick={() => {
                    setInfoPage("browse");
                    setTab("report");
                    closeMatch();
                  }}
                >
                  Report
                </button>
                <button
                  role="tab"
                  aria-selected={tab === "mine" && !showingMatch}
                  className={tab === "mine" && !showingMatch ? "is-active" : ""}
                  onClick={() => {
                    setInfoPage("browse");
                    setTab("mine");
                    closeMatch();
                  }}
                >
                  My reports{ownedCount > 0 ? ` (${ownedCount})` : ""}
                </button>
              </div>
              <span className="muted mono">{visible.length} reports</span>
            </div>

            <div className="panel-body findly-panel-body">
              {matching && <MatchSkeleton />}
              {!matching && result && operatingPoint && (
                <MatchPanel
                  result={result}
                  operatingPoint={operatingPoint}
                  canReview={["staff", "campus_admin", "superadmin"].includes(authUser?.role ?? "")}
                  labels={feedback}
                  onFeedback={sendFeedback}
                  onBack={closeMatch}
                  campus={campus ?? { buildings: [], nodes: [], edges: [] }}
                  report={selected ?? {
                    id: result.query.id,
                    kind: result.query.kind,
                    status: "open",
                    created_at: result.query.created_at,
                    campus_timezone: result.query.campus_timezone,
                    description: result.query.description,
                    category: result.query.category,
                    location: result.query.location,
                    node_key: null,
                    image_path: result.query.image_path,
                    image_url: result.query.image_url ?? null,
                    has_photo: Boolean(result.query.image_url),
                    is_synthetic_art: false,
                    attrs: result.query.attrs,
                    lost_from: null,
                    lost_to: null,
                  }}
                />
              )}
              {!matching && !result && tab === "feed" && (
                <ReportList
                  reports={visible}
                  selectedId={selected?.id ?? null}
                  onSelect={(r) => void openMatch(r)}
                  kind={kind}
                  onKind={setKind}
                  category={category}
                  onCategory={setCategory}
                  hasMore={nextCursor !== null}
                  loadingMore={loadingMore}
                  onLoadMore={() => void loadMore()}
                />
              )}
              {!matching && !result && tab === "report" && campus && (
                <div className="report-reference-layout">
                  <ReportForm campus={campus} busy={busy} onSubmit={submitReport} />
                  <aside className="report-live-preview">
                    <h3>Live preview</h3>
                    <p>This is how your report will appear to others.</p>
                    <div className="preview-placeholder">
                      <span className="preview-placeholder-icon">⌂</span>
                      <strong>Your item preview</strong>
                      <small>Complete the details and add a photo to see it here.</small>
                    </div>
                    <div className="preview-safety">
                      <strong>Stay safe</strong>
                      <span>Never share sensitive personal information. Meet in public places.</span>
                    </div>
                  </aside>
                </div>
              )}
              {!matching && !result && tab === "mine" && (
                <MyReports refreshKey={mineRefresh} onOpen={(id) => void openReportById(id)} />
              )}
            </div>
          </section>
        </div>
      ) : activeView === "user" ? (
        <div className="findly-secondary">
          <UserDashboard
            user={authUser}
            role={role}
            refreshKey={mineRefresh}
            onOpenReport={(id) => {
              setView("workspace");
              void openReportById(id);
            }}
            onGoTo={(next) => {
              setView("workspace");
              setTab(next);
              closeMatch();
            }}
            onSignIn={signIn}
            onSignOut={signOut}
          />
        </div>
      ) : activeView === "staff" ? (
        <div className="findly-secondary">
          <StaffDashboard
            refreshKey={mineRefresh}
            onBack={() => setView("workspace")}
            onOpenReport={(report) => {
              setView("workspace");
              void openMatch(report);
            }}
          />
        </div>
      ) : activeView === "admin" ? (
        <div className="findly-secondary">
          <AdminDashboard user={authUser} onBack={() => setView("workspace")} />
        </div>
      ) : null}
    </div>
  );
}

function matchesCategory(value: string | null, selected: string): boolean {
  if (selected === "All Items") return true;
  const category = (value ?? "").toLowerCase();
  if (selected === "Bags & Luggage") return /bag|backpack|luggage/.test(category);
  if (selected === "Electronics") return /electronic|phone|laptop|earbud|headphone|calculator|camera|charger|tablet|computer/.test(category);
  if (selected === "Keys") return /key/.test(category);
  if (selected === "Pets") return /pet|cat|dog/.test(category);
  if (selected === "Clothing") return /cloth|hoodie|shirt|jacket|shoe|cap|hat/.test(category);
  if (selected === "Wallets & Money") return /wallet|money|purse/.test(category);
  if (selected === "Sports & Outdoors") return /sport|outdoor|fitness/.test(category);
  if (selected === "Other") {
    return !/bag|backpack|luggage|electronic|phone|laptop|earbud|headphone|calculator|camera|charger|tablet|computer|key|pet|cat|dog|wallet|money|purse|cloth|hoodie|shirt|jacket|shoe|cap|hat|sport|outdoor|fitness/.test(category);
  }
  return category.includes(selected.toLowerCase().replace(/s$/, ""));
}

function MatchSkeleton() {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Matching">
      <div className="sk-line sk-title" />
      <div className="sk-line" />
      <div className="sk-line sk-short" />
      <div className="sk-card" />
      <div className="sk-card" />
      <div className="sk-card" />
    </div>
  );
}

function InfoPage({
  page,
  onBrowse,
  onReport,
}: {
  page: "how" | "safety";
  onBrowse: () => void;
  onReport: () => void;
}) {
  const isHow = page === "how";
  return (
    <main className="info-page">
      <section className="info-hero">
        <span className="info-eyebrow">{isHow ? "Simple by design" : "A safer way to reunite"}</span>
        <h2>{isHow ? "How Findly works" : "Safety comes first."}</h2>
        <p>
          {isHow
            ? "A few thoughtful steps help lost items find their way back to the people who miss them."
            : "Findly is built to help people reconnect without putting personal information at risk."}
        </p>
      </section>
      <section className="info-cards">
        {(isHow
          ? [
              ["01", "Report an item", "Tell us what was lost or found, add a photo, and share the approximate place."],
              ["02", "We find possible matches", "Findly compares descriptions, photos, locations, and dates to surface useful matches."],
              ["03", "Connect safely", "Review the details, message privately, and arrange a public handover."],
            ]
          : [
              ["01", "Keep details private", "Use Findly messaging instead of sharing your phone number, address, or personal documents."],
              ["02", "Meet in public", "Choose a busy, well-lit place such as a campus desk, library, or reception area."],
              ["03", "Trust your instincts", "If something feels uncomfortable, pause the exchange and report the conversation to the team."],
            ]
        ).map(([number, title, copy]) => (
          <article className="info-card" key={number}>
            <span>{number}</span>
            <h3>{title}</h3>
            <p>{copy}</p>
          </article>
        ))}
      </section>
      <section className="info-callout">
        <div>
          <strong>{isHow ? "Ready to help an item get home?" : "Need to report something now?"}</strong>
          <p>{isHow ? "Start with a clear description and Findly will guide you from there." : "Share only what is needed to identify the item. We will help with the rest."}</p>
        </div>
        <button className="report-item" onClick={isHow ? onReport : onBrowse}>{isHow ? "Report an Item" : "Browse Items"}</button>
      </section>
    </main>
  );
}

function LoginPage({
  onSignIn,
  onCreateAccount,
  onBack,
}: {
  onSignIn: (email: string, password: string) => Promise<void>;
  onCreateAccount: () => void;
  onBack: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setAuthError(null);
    try {
      await onSignIn(email, password);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-brand"><span className="findly-pin" aria-hidden="true">●</span><strong>Findly</strong></div>
        <h2>Welcome back</h2>
        <p className="login-subtitle">Sign in to manage your reports and help reunite more items.</p>
        <form onSubmit={submit} className="login-form">
          <label>
            <span>Email address</span>
            <input type="email" placeholder="you@example.com" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label>
            <span>Password</span>
            <input type="password" placeholder="Enter your password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <div className="login-options">
            <label className="remember"><input type="checkbox" /> Remember me</label>
            <button type="button" className="forgot">Forgot password?</button>
          </div>
          <button className="login-submit" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in to Findly"}</button>
          {authError && <p className="login-error" role="alert">{authError}</p>}
        </form>
        <div className="login-divider"><span>or</span></div>
        <button type="button" className="create-account" onClick={onCreateAccount}>Create a new account</button>
        <p className="login-note">New to Findly? Create an account first. If you just registered, verify your email before signing in.</p>
        <button type="button" className="login-back" onClick={onBack}>← Back to Browse Items</button>
      </section>
    </main>
  );
}

function RegisterPage({
  onRegister,
  onSignIn,
  onBack,
}: {
  onRegister: (email: string, password: string) => Promise<void>;
  onSignIn: () => void;
  onBack: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < 8) {
      setErrorMessage("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setErrorMessage("Passwords do not match.");
      return;
    }
    setBusy(true);
    setErrorMessage(null);
    try {
      await onRegister(email, password);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Could not create your account.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-brand"><span className="findly-pin" aria-hidden="true">●</span><strong>Findly</strong></div>
        <h2>Create your account</h2>
        <p className="login-subtitle">Join Findly to report items, follow matches, and reconnect safely.</p>
        <form onSubmit={submit} className="login-form">
          <label><span>Email address</span><input type="email" placeholder="you@example.com" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label><span>Password</span><input type="password" placeholder="At least 8 characters" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          <label><span>Confirm password</span><input type="password" placeholder="Repeat your password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></label>
          <button className="login-submit" type="submit" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
          {errorMessage && <p className="login-error" role="alert">{errorMessage}</p>}
        </form>
        <p className="login-switch">Already have an account? <button type="button" onClick={onSignIn}>Sign in</button></p>
        <button type="button" className="login-back" onClick={onBack}>← Back to Browse Items</button>
      </section>
    </main>
  );
}
