import { useEffect, useState } from "react";

// Guarded: this now runs during render, which the node-env renderToString tests also hit.
const PREFERS_REDUCED_MOTION = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Types a string out one character at a time on mount. The full text is laid
 * out invisibly underneath so the reveal can't reflow whatever sits below it.
 * ponytail: one re-render per character - fine for the handful of descriptions
 * on a page; batch into a rAF loop if a page ever renders hundreds. */
export function Typewriter({ text, speed = 18 }: { text: string; speed?: number }) {
  const [n, setN] = useState(() => (PREFERS_REDUCED_MOTION() ? text.length : 0));
  // Restart the reveal when the text changes (adjust-state-during-render, not an effect).
  const [shownText, setShownText] = useState(text);
  if (shownText !== text) {
    setShownText(text);
    setN(PREFERS_REDUCED_MOTION() ? text.length : 0);
  }

  useEffect(() => {
    if (n >= text.length) return;
    const id = setTimeout(() => setN(n + 1), speed);
    return () => clearTimeout(id);
  }, [n, text, speed]);

  return (
    <span className="relative inline-block">
      {/* opacity-0 rather than invisible: reserves the layout box and stays in the a11y tree, so the reveal is purely visual. */}
      <span className="opacity-0">{text}</span>
      <span className="absolute inset-0" aria-hidden>
        {text.slice(0, n)}
        {n < text.length && <span className="animate-pulse text-foreground/70">|</span>}
      </span>
    </span>
  );
}
