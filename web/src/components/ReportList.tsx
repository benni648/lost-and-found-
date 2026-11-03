import type { Kind, Report } from "../types";
import { KIND_LABEL, timeAbsolute } from "../lib/format";

interface Props {
  reports: Report[];
  selectedId: string | null;
  onSelect: (report: Report) => void;
  kind: "all" | Kind;
  onKind: (kind: "all" | Kind) => void;
  category: string;
  onCategory: (category: string) => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}

export default function ReportList({
  reports,
  selectedId,
  onSelect,
  kind,
  onKind,
  category,
  onCategory,
  hasMore,
  loadingMore,
  onLoadMore,
}: Props) {
  const categories = ["All Items", "Bags & Luggage", "Electronics", "Keys", "Pets", "Wallets & Money", "Clothing", "Sports & Outdoors", "Other"];
  return (
    <div className="feed">
      <div className="feed-controls findly-controls">
        <div className="findly-toggle" role="tablist" aria-label="Filter by kind">
          {(["all", "lost", "found"] as const).map((k) => (
            <button
              key={k}
              role="tab"
              aria-selected={kind === k}
              className={kind === k ? "is-active" : ""}
              onClick={() => onKind(k)}
            >
              {k === "all" ? "All Items" : KIND_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      <div className="category-chips" aria-label="Item categories">
        {categories.map((label) => (
          <button key={label} className={category === label ? "category-chip is-active" : "category-chip"} type="button" onClick={() => onCategory(label)}>
            {label}
          </button>
        ))}
      </div>

      {reports.length === 0 ? (
        <p className="empty">No reports match this filter.</p>
      ) : (
        <ul className="feed-list findly-grid">
          {reports.map((r) => (
            <li key={r.id}>
              <button
                className={`feed-row feed-${r.kind} ${r.id === selectedId ? "is-selected" : ""}`}
                onClick={() => onSelect(r)}
              >
                <span className="item-image-wrap">
                  {r.image_url ? (
                    <img className="item-image" src={r.image_url} alt="" loading="lazy" />
                  ) : (
                    <span className="item-image-placeholder">{r.category?.slice(0, 1) ?? "?"}</span>
                  )}
                  <span className={`item-status item-status-${r.kind}`}>{KIND_LABEL[r.kind]}</span>
                  <span className="item-heart" aria-hidden="true">♡</span>
                </span>
                <span className="feed-body">
                  <span className="feed-topline">
                    {r.status === "matched" && <span className="badge-matched">matched</span>}
                  </span>
                  <strong className="item-title">{r.description.split(".")[0] || "Reported item"}</strong>
                  <span className="item-location">
                    <span className="location-pin" aria-hidden="true">⌖</span>
                    <span>{r.location || "Location not added"}</span>
                  </span>
                  <time className="item-date" dateTime={r.created_at} title={timeAbsolute(r.created_at, r.campus_timezone)}>
                    ▣ {new Date(r.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                  </time>
                  <span className="item-description">{r.description}</span>
                </span>
                <span className="feed-go" aria-hidden="true">
                  {r.id}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {hasMore && (
        <button className="primary" type="button" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? "Loading reports…" : "Load more reports"}
        </button>
      )}
    </div>
  );
}
