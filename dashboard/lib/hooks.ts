"use client";

import { useEffect, useRef, useCallback, useState } from "react";

/**
 * Returns true if current time is within US market hours (9:30 AM - 4:00 PM ET, Mon-Fri).
 */
function isMarketHours(): boolean {
  const now = new Date();
  // Resolve day-of-week and time in America/New_York using Intl so US DST
  // is applied correctly regardless of the viewer's local timezone.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const weekday = parts.find((p) => p.type === "weekday")?.value;
  if (weekday === "Sat" || weekday === "Sun") return false;
  const hRaw = parseInt(parts.find((p) => p.type === "hour")!.value, 10);
  const h = hRaw === 24 ? 0 : hRaw;
  const m = parseInt(parts.find((p) => p.type === "minute")!.value, 10);
  const etMinutes = h * 60 + m;
  return etMinutes >= 9 * 60 + 30 && etMinutes <= 16 * 60;
}

/**
 * Auto-refresh hook that polls at different intervals based on market hours.
 *
 * @param refreshFn - async function to call on each refresh
 * @param marketIntervalMs - polling interval during market hours (default 30s)
 * @param offMarketIntervalMs - polling interval outside market hours (default 5m)
 */
export function useAutoRefresh(
  refreshFn: () => Promise<void>,
  marketIntervalMs = 30_000,
  offMarketIntervalMs = 300_000,
) {
  const [paused, setPaused] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshRef = useRef(refreshFn);
  refreshRef.current = refreshFn;

  const scheduleNext = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    const interval = isMarketHours() ? marketIntervalMs : offMarketIntervalMs;
    timerRef.current = setTimeout(async () => {
      try {
        await refreshRef.current();
      } catch {
        // errors handled by caller
      }
      scheduleNext();
    }, interval);
  }, [marketIntervalMs, offMarketIntervalMs]);

  useEffect(() => {
    if (paused) {
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }
    scheduleNext();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [paused, scheduleNext]);

  return { paused, setPaused };
}

/**
 * Hook that returns a live "Xs ago" / "Xm ago" string from a timestamp.
 */
export function useRelativeTime(timestamp: Date | null): string {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (!timestamp) return "";
  const seconds = Math.max(0, Math.floor((now - timestamp.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m ago`;
}
