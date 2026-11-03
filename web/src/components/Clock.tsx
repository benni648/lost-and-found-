import { useEffect, useState } from "react";

/** Desk clock. Local time, ticking, with a blinking indicator. */
export default function Clock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const time = now.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  return (
    <span className="clock" title="Local desk time">
      <span className="clock-lamp" aria-hidden="true" />
      <span className="clock-label">local</span>
      <span className="mono clock-time">{time}</span>
    </span>
  );
}
