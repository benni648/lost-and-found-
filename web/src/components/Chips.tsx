import type { Reason } from "../types";

const MARK = { positive: "✓", negative: "✕", neutral: "·" } as const;

export default function Chips({ reasons, limit = 6 }: { reasons: Reason[]; limit?: number }) {
  if (!reasons.length) return null;
  return (
    <ul className="chips">
      {reasons.slice(0, limit).map((r, i) => (
        <li key={`${r.label}-${i}`} className={`chip chip-${r.kind}`}>
          <span aria-hidden="true">{MARK[r.kind]}</span>
          {r.label}
        </li>
      ))}
    </ul>
  );
}
