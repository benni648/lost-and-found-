import type { Decision, Kind, OperatingPoint } from "../types";

/** "3 d ago", tolerant of clock skew between the simulator and the browser. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "time unknown";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "time unknown";
  const diff = Date.now() - then;
  const future = diff < 0;
  const mins = Math.round(Math.abs(diff) / 60000);
  const text =
    mins < 1
      ? "just now"
      : mins < 60
        ? `${mins} min`
        : mins < 1440
          ? `${Math.round(mins / 60)} h`
          : `${Math.round(mins / 1440)} d`;
  if (mins < 1) return text;
  return future ? `in ${text}` : `${text} ago`;
}

export function timeAbsolute(iso: string | null | undefined, timezone: string): string {
  if (!iso) return "time unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "time unknown";
  return new Intl.DateTimeFormat("en-IN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
    timeZoneName: "short",
  }).format(date);
}

export function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function fixed(value: number, digits = 2): string {
  return value.toFixed(digits);
}

export const KIND_LABEL: Record<Kind, string> = { lost: "Lost", found: "Found" };

export const DECISION_COPY: Record<Decision, { title: string; tone: string; note: string }> = {
  match: {
    title: "Match found",
    tone: "match",
    note: "One candidate leads the field on visual, attribute, time and location evidence.",
  },
  review: {
    title: "Needs a human decision",
    tone: "review",
    note: "The shortlist is above the floor, but either the confidence or the margin is too thin to auto-alert.",
  },
  no_match: {
    title: "No confident match",
    tone: "none",
    note: "Nothing in the found pool is plausible enough. The system abstains rather than guessing.",
  },
};

export function verdictFor(prob: number, operatingPoint: OperatingPoint): { label: string; tone: string } {
  if (prob >= operatingPoint.bands.strong) return { label: "strong", tone: "match" };
  if (prob >= operatingPoint.bands.probable) return { label: "probable", tone: "review" };
  if (prob >= operatingPoint.bands.weak) return { label: "weak", tone: "none" };
  return { label: "unlikely", tone: "none" };
}
