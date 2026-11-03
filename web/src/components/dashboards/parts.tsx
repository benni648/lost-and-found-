import type { ReactNode } from "react";
import FlapText from "../FlapText";

interface TileProps {
  value: string | number;
  label: string;
  note?: string;
  tone?: "good" | "warn";
}

export function DashTile({ value, label, note, tone }: TileProps) {
  const toneClass = tone ? ` is-${tone}` : "";
  return (
    <div className={`dash-tile${toneClass}`}>
      <span className="dash-tile-value">
        <FlapText value={value} />
      </span>
      <span className="dash-tile-label">{label}</span>
      {note && <span className="dash-tile-note">{note}</span>}
    </div>
  );
}

interface CardProps {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
}

export function DashCard({ title, aside, children }: CardProps) {
  return (
    <section className="dash-card">
      {title && (
        <div className="dash-card-head">
          <h3>{title}</h3>
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

interface BarProps {
  label: string;
  value: number;
  max: number;
}

export function DashBar({ label, value, max }: BarProps) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="dash-bar-row">
      <span className="dash-bar-label">{label}</span>
      <span className="dash-bar-track">
        <span className="dash-bar-fill" style={{ width: `${width}%` }} />
      </span>
      <span className="dash-bar-count">{value}</span>
    </div>
  );
}

interface ErrorProps {
  error: string;
  onRetry: () => void;
}

export function DashError({ error, onRetry }: ErrorProps) {
  return (
    <p className="dash-error" role="alert">
      {error}{" "}
      <button type="button" className="dash-row-button" onClick={onRetry}>
        retry
      </button>
    </p>
  );
}
