import { useEffect, useRef, useState } from "react";

/**
 * Ease-out-cubic interpolation between start and end, given elapsed and
 * total duration in milliseconds. Pure function so it's unit-testable
 * without mounting a component or mocking requestAnimationFrame.
 */
export function interpolateCountUp(
  start: number,
  end: number,
  elapsedMs: number,
  durationMs: number,
): number {
  if (durationMs <= 0 || elapsedMs >= durationMs) return end;
  const t = Math.max(0, elapsedMs / durationMs);
  const eased = 1 - Math.pow(1 - t, 3);
  return start + (end - start) * eased;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Animates a displayed number toward `value` whenever it changes.
 * Snaps immediately under prefers-reduced-motion.
 */
export function useCountUp(value: number, durationMs = 600): number {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  const reduced = prefersReducedMotion();

  useEffect(() => {
    if (reduced) {
      fromRef.current = value;
      return;
    }

    const start = fromRef.current;
    const startTime = performance.now();
    let frame: number;

    function tick(now: number) {
      const elapsed = now - startTime;
      setDisplay(interpolateCountUp(start, value, elapsed, durationMs));
      if (elapsed < durationMs) {
        frame = requestAnimationFrame(tick);
      } else {
        fromRef.current = value;
      }
    }

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, durationMs, reduced]);

  return reduced ? value : display;
}
