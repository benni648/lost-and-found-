interface Props {
  value: string | number;
  className?: string;
}

/**
 * Splits a short value into mechanical flap tiles. Each character remounts when
 * the value changes, so a counter visibly flips instead of just repainting.
 */
export default function FlapText({ value, className }: Props) {
  const text = String(value);
  return (
    <span className={className ? `flap ${className}` : "flap"} role="img" aria-label={text}>
      {[...text].map((character, index) => (
        <span
          // Keying on the character restarts the flip only for tiles that change.
          key={`${index}-${character}`}
          className="flap-char"
          style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
          aria-hidden="true"
        >
          <span className="flap-glyph">{character === " " ? "\u00a0" : character}</span>
        </span>
      ))}
    </span>
  );
}
