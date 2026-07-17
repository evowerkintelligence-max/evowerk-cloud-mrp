# Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retune the frontend's design tokens to a neutral dark base with a
5-hue accent system, add a "hero metric" layout variant to `StatCard`, and
add four motion primitives (micro-interactions, number/chart animation,
route transitions, live-data pulse) — all via the shared token layer and
shared components so all ~40 existing routes inherit the new look without
per-page edits.

**Architecture:** No new components library, no new runtime dependencies
except one dev-only test runner (`vitest`, already compatible with the
existing Vite 5 setup). Every change lands in `frontend/src/index.css`,
`frontend/tailwind.config.js`, or one of the existing shared components
(`components/ui/*`, `components/StatCard.tsx`, `components/AppShell.tsx`,
`pages/analytics/shared.tsx`). A handful of pages get small wiring changes
to demonstrate the new primitives actually rendering:
`pages/analytics/Oee.tsx` (hero-metric prove-out), `EngineScreen` in
`shared.tsx` (live-pulse prove-out), and the three existing `PageTabs`
call sites — `Procurement.tsx`, `Warehouse.tsx`,
`workflows/RunHistory.tsx` (tab-switch fade, small enough in number to do
outright rather than defer). Promoting the hero layout beyond OEE to the
other ~39 pages is the one piece explicitly deferred (see spec).

**Tech Stack:** React 18 + TypeScript + Vite 5 + Tailwind CSS 3 (HSL
CSS-variable tokens) + ECharts 6. Test runner: `vitest` (new dev
dependency, zero new runtime dependency).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-17-visual-redesign-design.md`
- Do not rename any existing CSS custom property or Tailwind color key
  (`--primary`, `--accent`, `--warning`, `--destructive`, `--success`,
  `--brand`) — only retune their HSL values. Existing call sites
  (`text-primary`, `bg-warning/15`, etc.) must keep working unchanged.
- `--terracotta` is the only new CSS variable / Tailwind color.
- All new CSS animation must be inert under
  `@media (prefers-reduced-motion: reduce)` — the existing global rule in
  `index.css` (lines 117-124) already zeroes `animation-duration` /
  `transition-duration` for everything, so pure-CSS additions get this for
  free. The one JS-driven animation (`useCountUp`, uses
  `requestAnimationFrame`, not CSS) must check `matchMedia` itself.
- No new UI component library; no changes to `components/ui/select.tsx`,
  `dialog.tsx`, `input.tsx`, `checkbox.tsx`, `label.tsx`, `skeleton.tsx`,
  `alert.tsx`, `table.tsx` (out of scope per spec — token retuning reaches
  them automatically since they reference the same CSS vars, no direct
  edits needed).

---

### Task 1: Retune color tokens + add `--terracotta`

**Files:**
- Modify: `frontend/src/index.css:11-175`
- Modify: `frontend/tailwind.config.js:41`

**Interfaces:**
- Produces: `--terracotta` CSS variable (both themes) and a
  `terracotta: "hsl(var(--terracotta))"` Tailwind color, consumed by Task 3
  (Badge) and Task 5 (StatCard `TONE`).

This task is pure CSS/config — no logic, no unit test. Verification is
visual (Step 4 below).

- [ ] **Step 1: Replace the `:root` (light theme) token block**

In `frontend/src/index.css`, replace lines 11-37 with:

```css
  :root {
    --background: 40 20% 97%;
    --foreground: 30 15% 12%;
    --card: 0 0% 100%;
    --card-foreground: 30 15% 12%;
    --popover: 0 0% 100%;
    --popover-foreground: 30 15% 12%;
    --primary: 165 45% 32%;
    --primary-foreground: 0 0% 100%;
    --secondary: 30 15% 94%;
    --secondary-foreground: 30 15% 16%;
    --muted: 30 15% 94%;
    --muted-foreground: 30 10% 42%;
    --accent: 258 65% 55%;
    --accent-foreground: 0 0% 100%;
    --destructive: 352 68% 46%;
    --destructive-foreground: 0 0% 100%;
    --success: 150 60% 32%;
    --success-foreground: 0 0% 100%;
    --warning: 40 78% 40%;
    --warning-foreground: 0 0% 100%;
    --border: 30 15% 89%;
    --input: 30 15% 85%;
    --ring: 165 45% 32%;
    --brand: 165 48% 38%;
    --terracotta: 18 58% 48%;
    --radius: 0.75rem;
  }
```

- [ ] **Step 2: Replace the `.dark` token block**

Replace lines 39-65 with:

```css
  .dark {
    --background: 0 0% 6%;
    --foreground: 0 0% 92%;
    --card: 0 0% 9%;
    --card-foreground: 0 0% 92%;
    --popover: 0 0% 8%;
    --popover-foreground: 0 0% 92%;
    --primary: 166 42% 52%;
    --primary-foreground: 166 30% 9%;
    --secondary: 0 0% 14%;
    --secondary-foreground: 0 0% 92%;
    --muted: 0 0% 12%;
    --muted-foreground: 0 0% 58%;
    --accent: 258 70% 68%;
    --accent-foreground: 210 40% 98%;
    --destructive: 352 65% 55%;
    --destructive-foreground: 210 40% 98%;
    --success: 150 65% 42%;
    --success-foreground: 155 80% 8%;
    --warning: 42 82% 56%;
    --warning-foreground: 36 85% 9%;
    --border: 0 0% 16%;
    --input: 0 0% 20%;
    --ring: 166 42% 52%;
    --brand: 166 50% 60%;
    --terracotta: 18 55% 58%;
    --radius: 0.75rem;
  }
```

- [ ] **Step 3: Update the hardcoded `brand-gradient` stops**

`.brand-gradient` and `.brand-gradient-animate` (around line 146-175)
hardcode their gradient stops instead of reading the CSS vars, so they need
updating by hand to match the new brand hue family. Replace both blocks:

```css
  /* Thin gradient brand rule / accent bar. */
  .brand-gradient {
    background-image: linear-gradient(
      90deg,
      hsl(166 55% 55%),
      hsl(190 55% 55%),
      hsl(258 65% 65%)
    );
  }

  /* Slowly-shifting teal -> sky -> violet gradient for brand moments: active
     nav indicator, org avatar chip, hero name, hairline card accents. */
  .brand-gradient-animate {
    background-image: linear-gradient(
      90deg,
      hsl(166 55% 55%),
      hsl(190 55% 55%),
      hsl(258 65% 65%),
      hsl(166 55% 55%)
    );
    background-size: 200% auto;
    animation: brand-gradient-shift 8s linear infinite;
  }
```

(Only the `background-image` gradient stops changed; the keyframe below it
is unchanged.)

- [ ] **Step 4: Add `terracotta` to the Tailwind color config**

In `frontend/tailwind.config.js`, line 41 currently reads:

```js
        brand: "hsl(var(--brand))",
```

Add a line directly after it:

```js
        brand: "hsl(var(--brand))",
        terracotta: "hsl(var(--terracotta))",
```

- [ ] **Step 5: Verify visually**

Run `npm run dev` (via the project's normal dev workflow) and load the app
in both themes. Confirm: dark mode background reads as neutral near-black
(no blue tint), light mode background reads as warm paper (no blue tint),
the sidebar's active-nav gradient bar and org-avatar chip show the new
teal→sky→violet sweep, and no page throws a console error about a missing
Tailwind class.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/index.css frontend/tailwind.config.js
git commit -m "feat: retune design tokens to neutral base + 5-hue accent system"
```

---

### Task 2: Hover-lift/glow on base Card and Button

**Files:**
- Modify: `frontend/src/components/ui/card.tsx:4-16`
- Modify: `frontend/src/components/ui/button.tsx:11-20`

**Interfaces:**
- Consumes: `--primary` (Task 1).
- Produces: no new props — `Card` and `Button` keep their existing
  signatures, this only adds classes to their default className string.

Today only `StatCard` (which wraps `Card`) has its own hover-lift/glow;
plain `Card` usages elsewhere (e.g. `Oee.tsx`'s `<Card>` wrapping the
Morning Brief, dialogs) have no hover feedback at all. This makes it a base
`Card` behavior.

- [ ] **Step 1: Add hover-lift to base `Card`**

In `frontend/src/components/ui/card.tsx`, replace:

```tsx
export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card text-card-foreground shadow-card",
        className,
      )}
      {...props}
    />
  ),
);
```

with:

```tsx
export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card text-card-foreground shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:shadow-glow-sm",
        className,
      )}
      {...props}
    />
  ),
);
```

- [ ] **Step 2: Add glow on hover/focus to the `primary` button variant**

`Button` already has `hover:shadow-glow-sm` on the `primary` variant
(`button.tsx:12`) — extend it to also glow on keyboard focus, and give
`secondary`/`outline` a lift so every variant gets *some* motion feedback.
In `frontend/src/components/ui/button.tsx`, replace the `variants.variant`
block:

```tsx
    variants: {
      variant: {
        primary:
          "bg-primary font-semibold text-primary-foreground shadow-[inset_0_1px_0_0_hsl(0_0%_100%/0.25),0_1px_2px_0_hsl(224_42%_2%/0.5)] hover:bg-primary/90 hover:shadow-glow-sm active:bg-primary/85",
        secondary:
          "border border-input bg-secondary text-secondary-foreground shadow-card hover:bg-secondary/70",
        outline:
          "border border-input bg-transparent text-foreground hover:border-muted-foreground/40 hover:bg-secondary/50",
        ghost: "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
        destructive:
          "bg-destructive font-semibold text-destructive-foreground shadow-[inset_0_1px_0_0_hsl(0_0%_100%/0.15)] hover:bg-destructive/90 active:bg-destructive/85",
      },
```

with:

```tsx
    variants: {
      variant: {
        primary:
          "bg-primary font-semibold text-primary-foreground shadow-[inset_0_1px_0_0_hsl(0_0%_100%/0.25),0_1px_2px_0_hsl(224_42%_2%/0.5)] hover:-translate-y-px hover:bg-primary/90 hover:shadow-glow-sm focus-visible:shadow-glow-sm active:translate-y-0 active:bg-primary/85",
        secondary:
          "border border-input bg-secondary text-secondary-foreground shadow-card hover:-translate-y-px hover:bg-secondary/70",
        outline:
          "border border-input bg-transparent text-foreground hover:-translate-y-px hover:border-muted-foreground/40 hover:bg-secondary/50",
        ghost: "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
        destructive:
          "bg-destructive font-semibold text-destructive-foreground shadow-[inset_0_1px_0_0_hsl(0_0%_100%/0.15)] hover:-translate-y-px hover:bg-destructive/90 active:translate-y-0 active:bg-destructive/85",
      },
```

(`active:translate-y-px` already exists on the base `buttonVariants` string
at line 7 for the press-down feedback — the per-variant
`hover:-translate-y-px` lifts on hover, then the base `active:translate-y-px`
still fires on press, so the two combine correctly: lift on hover, settle
back down on click.)

- [ ] **Step 3: Verify visually**

Reload the app, hover over a few `Card`s (e.g. the Dashboard's Morning
Brief card) and buttons across a couple of pages. Confirm a subtle lift +
glow, and that clicking a button still shows the existing press-down
feedback (no visual conflict between the new hover lift and old active
state).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ui/card.tsx frontend/src/components/ui/button.tsx
git commit -m "feat: base Card/Button hover-lift and glow micro-interactions"
```

---

### Task 3: Token-based Badge variants + terracotta

**Files:**
- Modify: `frontend/src/components/ui/badge.tsx:9-19`

**Interfaces:**
- Consumes: `--success`, `--warning`, `--destructive`, `--accent`,
  `--terracotta` (Task 1).
- Produces: `Badge`'s `variant` prop gains `"terracotta"`; existing
  variants (`success`/`warning`/`danger`/`info`) keep their names but
  switch from hardcoded Tailwind palette colors to the design tokens, so
  they retune along with everything else instead of staying stuck on the
  old emerald/amber/rose/sky hues.

`badge.tsx` today hardcodes `emerald-500`/`amber-500`/`rose-500`/`sky-500`
for `success`/`warning`/`danger`/`info` — the only shared component that
doesn't route through the CSS-variable token layer. That means Task 1's
retune has no visible effect on badges unless this task also lands.

- [ ] **Step 1: Replace the variant map**

In `frontend/src/components/ui/badge.tsx`, replace:

```tsx
    variants: {
      variant: {
        neutral: "bg-secondary text-secondary-foreground ring-border",
        primary: "bg-primary/15 text-primary ring-primary/30",
        accent: "bg-accent/15 text-accent ring-accent/30",
        success: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
        warning: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
        danger: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
        info: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
        indigo: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/30",
        teal: "bg-teal-500/15 text-teal-300 ring-teal-500/30",
      },
    },
```

with:

```tsx
    variants: {
      variant: {
        neutral: "bg-secondary text-secondary-foreground ring-border",
        primary: "bg-primary/15 text-primary ring-primary/30",
        accent: "bg-accent/15 text-accent ring-accent/30",
        success: "bg-success/15 text-success ring-success/30",
        warning: "bg-warning/15 text-warning ring-warning/30",
        danger: "bg-destructive/15 text-destructive ring-destructive/30",
        info: "bg-accent/15 text-accent ring-accent/30",
        terracotta: "bg-terracotta/15 text-terracotta ring-terracotta/30",
        indigo: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/30",
        teal: "bg-teal-500/15 text-teal-300 ring-teal-500/30",
      },
    },
```

(`indigo`/`teal` are decorative-only variants unrelated to the semantic
palette — left untouched per spec scope. `info` becomes identical to
`accent` since both map to the same `--accent`/violet role — this is
intentional, giving callers a semantically-named option without a second
token.)

- [ ] **Step 2: Verify visually**

Grep for `variant="success"` / `variant="warning"` / `variant="danger"` /
`variant="info"` usage (e.g. `pages/Alerts.tsx`, `pages/RcaCases.tsx`) and
confirm those badges now render in the retuned token colors instead of the
old hardcoded Tailwind hues.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/badge.tsx
git commit -m "feat: Badge variants use design tokens, add terracotta"
```

---

### Task 4: `useCountUp` hook + test runner setup

**Files:**
- Create: `frontend/src/lib/useCountUp.ts`
- Create: `frontend/src/lib/useCountUp.test.ts`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces:
  - `interpolateCountUp(start: number, end: number, elapsedMs: number, durationMs: number): number`
    — pure function, exported for testing.
  - `useCountUp(value: number, durationMs?: number): number` — React hook,
    default `durationMs = 600`. Consumed by Task 5 (`StatCard`).

The frontend has no test runner at all today. Vitest is added as the only
new dependency in this whole plan (dev-only) because it's a drop-in for
the existing Vite 5 config (same `defineConfig`, same resolve alias, zero
extra plugins) — the alternative of hand-rolling a test harness would cost
more than adopting the tool Vite already ships a first-party integration
for.

- [ ] **Step 1: Add vitest as a dev dependency**

In `frontend/package.json`, add to `"scripts"`:

```json
    "test": "vitest run",
```

so the block reads:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint .",
    "test": "vitest run"
  },
```

Add to `"devDependencies"`:

```json
    "vitest": "^2.1.9",
```

Then run:

```bash
cd frontend && npm install
```

Expected: `vitest` appears in `node_modules` and `package-lock.json` is
updated. No other dependency versions should change.

- [ ] **Step 2: Point `vite.config.ts` at vitest's config types**

Replace the import line in `frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
```

with:

```ts
import { defineConfig } from "vitest/config";
```

Then add a `test` block to the returned config object (after the existing
`server` block):

```ts
  test: {
    environment: "node",
  },
```

So the full file's export becomes:

```ts
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "^/(auth|orgs|items|boms|suppliers|purchase-orders|lots|locations|inventory|forecasts|mrp|datasets|analytics|ai|reports|bi|workflows|automl|kpis|kpi-dashboards|integrations|realtime|simulation|health|alerts|computer-vision|data-chat|downtime-tracking|predictive|rca-cases|scenarios|pace)(/|$|\\?)":
        `http://localhost:${backendPort}`,
    },
  },
  test: {
    environment: "node",
  },
});
```

(`environment: "node"` is enough because the only test in this plan
exercises the pure `interpolateCountUp` function, not React rendering — no
`jsdom` dependency needed.)

- [ ] **Step 3: Write the failing test**

Create `frontend/src/lib/useCountUp.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { interpolateCountUp } from "./useCountUp";

describe("interpolateCountUp", () => {
  it("returns the start value at elapsed=0", () => {
    expect(interpolateCountUp(0, 100, 0, 500)).toBe(0);
  });

  it("returns the end value once the duration has fully elapsed", () => {
    expect(interpolateCountUp(0, 100, 500, 500)).toBe(100);
  });

  it("clamps to the end value if elapsed exceeds duration", () => {
    expect(interpolateCountUp(0, 100, 750, 500)).toBe(100);
  });

  it("returns a value strictly between start and end mid-animation", () => {
    const mid = interpolateCountUp(0, 100, 250, 500);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(100);
  });

  it("returns the end value immediately when duration is 0", () => {
    expect(interpolateCountUp(10, 20, 0, 0)).toBe(20);
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
cd frontend && npm test -- useCountUp
```

Expected: FAIL — `frontend/src/lib/useCountUp.ts` does not exist yet.

- [ ] **Step 5: Implement `useCountUp.ts`**

Create `frontend/src/lib/useCountUp.ts`:

```ts
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

  useEffect(() => {
    if (prefersReducedMotion()) {
      setDisplay(value);
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
  }, [value, durationMs]);

  return display;
}
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd frontend && npm test -- useCountUp
```

Expected: PASS, 5 tests.

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/lib/useCountUp.ts frontend/src/lib/useCountUp.test.ts
git commit -m "feat: add useCountUp hook with vitest test setup"
```

---

### Task 5: `StatCard` hero size variant + extended `TONE`

**Files:**
- Modify: `frontend/src/components/StatCard.tsx`

**Interfaces:**
- Consumes: `useCountUp` from `frontend/src/lib/useCountUp.ts` (Task 4);
  `terracotta` Tailwind color (Task 1).
- Produces: `StatCard` gains `size?: "default" | "hero"`,
  `heroValue?: number`, `heroFormat?: (n: number) => string`,
  `heroColor?: string`, `delta?: ReactNode` props — all optional, so every
  existing call site (all ~40 pages) is unaffected. `TONE` gains `info` and
  `terracotta` keys; `STAT_TONES` (already `Object.keys(TONE)`, already
  consumed modulo-safely by `Spc.tsx` and `Realtime.tsx`) picks these up
  automatically with no other file needing changes.

- [ ] **Step 1: Extend the `TONE` map**

In `frontend/src/components/StatCard.tsx`, replace:

```tsx
const TONE = {
  primary: {
    icon: "bg-primary/15 text-primary ring-primary/30",
    blob: "bg-primary/10",
    glow: "hover:border-primary/50 hover:shadow-glow-sm",
  },
  accent: {
    icon: "bg-accent/15 text-accent ring-accent/30",
    blob: "bg-accent/10",
    glow: "hover:border-accent/50 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.5)]",
  },
  success: {
    icon: "bg-success/15 text-success ring-success/30",
    blob: "bg-success/10",
    glow: "hover:border-success/50 hover:shadow-[0_0_18px_-4px_hsl(var(--success)/0.5)]",
  },
  warning: {
    icon: "bg-warning/15 text-warning ring-warning/30",
    blob: "bg-warning/10",
    glow: "hover:border-warning/50 hover:shadow-[0_0_18px_-4px_hsl(var(--warning)/0.5)]",
  },
} as const;
```

with:

```tsx
const TONE = {
  primary: {
    icon: "bg-primary/15 text-primary ring-primary/30",
    blob: "bg-primary/10",
    glow: "hover:border-primary/50 hover:shadow-glow-sm",
    text: "text-primary",
  },
  accent: {
    icon: "bg-accent/15 text-accent ring-accent/30",
    blob: "bg-accent/10",
    glow: "hover:border-accent/50 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.5)]",
    text: "text-accent",
  },
  success: {
    icon: "bg-success/15 text-success ring-success/30",
    blob: "bg-success/10",
    glow: "hover:border-success/50 hover:shadow-[0_0_18px_-4px_hsl(var(--success)/0.5)]",
    text: "text-success",
  },
  warning: {
    icon: "bg-warning/15 text-warning ring-warning/30",
    blob: "bg-warning/10",
    glow: "hover:border-warning/50 hover:shadow-[0_0_18px_-4px_hsl(var(--warning)/0.5)]",
    text: "text-warning",
  },
  info: {
    icon: "bg-accent/15 text-accent ring-accent/30",
    blob: "bg-accent/10",
    glow: "hover:border-accent/50 hover:shadow-[0_0_18px_-4px_hsl(var(--accent)/0.5)]",
    text: "text-accent",
  },
  terracotta: {
    icon: "bg-terracotta/15 text-terracotta ring-terracotta/30",
    blob: "bg-terracotta/10",
    glow: "hover:border-terracotta/50 hover:shadow-[0_0_18px_-4px_hsl(var(--terracotta)/0.5)]",
    text: "text-terracotta",
  },
} as const;
```

- [ ] **Step 2: Add the hero-size props and rendering branch**

Replace the whole component function:

```tsx
export function StatCard({
  label,
  value,
  icon,
  hint,
  tone = "primary",
  title,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
  tone?: keyof typeof TONE;
  /** Hover tooltip explaining what this metric means. */
  title?: string;
}) {
  const t = TONE[tone];
  return (
    <Card
      className={cn(
        "group relative overflow-hidden rounded-xl bg-card/70 p-5 backdrop-blur-sm transition-all duration-300 hover:-translate-y-0.5",
        t.glow,
      )}
    >
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute -right-8 -top-8 size-24 rounded-full opacity-0 blur-2xl transition-opacity group-hover:opacity-100",
          t.blob,
        )}
      />
      <div className="flex items-center justify-between gap-3">
        <span
          className={cn(
            "text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground",
            title && "cursor-help",
          )}
          title={title}
        >
          {label}
        </span>
        {icon && (
          <span
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset [&_svg]:size-4",
              t.icon,
            )}
          >
            {icon}
          </span>
        )}
      </div>
      <div className="tabular mt-3 text-3xl font-extrabold leading-none tracking-tight">
        {value}
      </div>
      {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}
```

with:

```tsx
function HeroValue({
  heroValue,
  heroFormat,
  heroColor,
}: {
  heroValue: number;
  heroFormat: (n: number) => string;
  heroColor?: string;
}) {
  const animated = useCountUp(heroValue);
  return (
    <span style={heroColor ? { color: heroColor } : undefined}>
      {heroFormat(animated)}
    </span>
  );
}

export function StatCard({
  label,
  value,
  icon,
  hint,
  tone = "primary",
  title,
  size = "default",
  heroValue,
  heroFormat = (n) => String(Math.round(n)),
  heroColor,
  delta,
  className,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
  tone?: keyof typeof TONE;
  /** Hover tooltip explaining what this metric means. */
  title?: string;
  /** "hero" renders an oversized, animated primary metric with the label demoted below it. */
  size?: "default" | "hero";
  /** Numeric value to animate via useCountUp. Only used when size="hero"; falls back to static `value` if omitted. */
  heroValue?: number;
  /** Formats the animated number for display, e.g. (n) => `${n.toFixed(1)}%`. */
  heroFormat?: (n: number) => string;
  /** Overrides the hero number's text color (e.g. a dynamic threshold color), instead of the tone's default foreground. */
  heroColor?: string;
  /** Inline delta shown next to the hero number, e.g. "+2.1%". Only rendered when size="hero". */
  delta?: ReactNode;
  className?: string;
}) {
  const t = TONE[tone];
  const isHero = size === "hero";
  return (
    <Card
      className={cn(
        "group relative overflow-hidden rounded-xl bg-card/70 p-5 backdrop-blur-sm transition-all duration-300 hover:-translate-y-0.5",
        t.glow,
        className,
      )}
    >
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute -right-8 -top-8 size-24 rounded-full opacity-0 blur-2xl transition-opacity group-hover:opacity-100",
          t.blob,
        )}
      />
      {isHero ? (
        <>
          <div className="flex items-center justify-between gap-3">
            {icon && (
              <span
                className={cn(
                  "flex size-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset [&_svg]:size-4",
                  t.icon,
                )}
              >
                {icon}
              </span>
            )}
          </div>
          <div className="tabular mt-1 text-4xl font-extrabold leading-none tracking-tight lg:text-5xl">
            {heroValue !== undefined ? (
              <HeroValue heroValue={heroValue} heroFormat={heroFormat} heroColor={heroColor} />
            ) : (
              value
            )}
            {delta && <span className={cn("ml-2 text-sm font-semibold", t.text)}>{delta}</span>}
          </div>
          <div
            className={cn(
              "mt-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground",
              title && "cursor-help",
            )}
            title={title}
          >
            {label}
          </div>
          {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
        </>
      ) : (
        <>
          <div className="flex items-center justify-between gap-3">
            <span
              className={cn(
                "text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground",
                title && "cursor-help",
              )}
              title={title}
            >
              {label}
            </span>
            {icon && (
              <span
                className={cn(
                  "flex size-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset [&_svg]:size-4",
                  t.icon,
                )}
              >
                {icon}
              </span>
            )}
          </div>
          <div className="tabular mt-3 text-3xl font-extrabold leading-none tracking-tight">
            {value}
          </div>
          {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
        </>
      )}
    </Card>
  );
}
```

Add the import at the top of the file (alongside the existing `Card` and
`cn` imports):

```tsx
import { useCountUp } from "@/lib/useCountUp";
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc -b --noEmit
```

Expected: no new type errors (existing callers pass only `label`, `value`,
`icon?`, `hint?`, `tone?`, `title?` — all still valid since the new props
are optional).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StatCard.tsx
git commit -m "feat: StatCard hero size variant with animated count-up"
```

---

### Task 6: Wire OEE to the hero layout (prove-out)

**Files:**
- Modify: `frontend/src/pages/analytics/Oee.tsx:503-517`

**Interfaces:**
- Consumes: `StatCard`'s `size`/`heroValue`/`heroFormat`/`heroColor`/`delta`
  props (Task 5).

`Oee.tsx` already has exactly the shape the hero layout is for: one primary
metric (OEE) with a dynamic threshold color via `oeeColor()`, plus three
equal-weight secondary metrics (Availability/Performance/Quality). This is
the one real page wired up in this plan; adopting the hero layout on the
other ~39 pages is an explicit follow-up (see spec Scope), not part of
this task.

- [ ] **Step 1: Replace the four-`StatCard` grid**

Replace:

```tsx
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="OEE"
              value={
                <span style={{ color: oeeColor(r.oee) }}>
                  {r.oee}%{r.performance_estimated ? "*" : ""}
                </span>
              }
              tone="primary"
              title={METRIC_HELP.OEE}
            />
            <StatCard label="Availability" value={pct(r.availability)} tone="accent" title={METRIC_HELP.Availability} />
            <StatCard label="Performance" value={pct(r.performance)} tone="success" title={METRIC_HELP.Performance} />
            <StatCard label="Quality" value={pct(r.quality)} tone="warning" title={METRIC_HELP.Quality} />
          </div>
```

with:

```tsx
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="OEE"
              size="hero"
              value={null}
              heroValue={r.oee}
              heroFormat={(n) => `${n.toFixed(1)}%${r.performance_estimated ? "*" : ""}`}
              heroColor={oeeColor(r.oee)}
              tone="primary"
              title={METRIC_HELP.OEE}
              className="lg:col-span-2"
            />
            <StatCard label="Availability" value={pct(r.availability)} tone="accent" title={METRIC_HELP.Availability} />
            <StatCard label="Performance" value={pct(r.performance)} tone="success" title={METRIC_HELP.Performance} />
            <StatCard label="Quality" value={pct(r.quality)} tone="warning" title={METRIC_HELP.Quality} />
          </div>
```

(`StatCard`'s `className` prop, added in Task 5, is what makes
`className="lg:col-span-2"` above work — the hero card spans two grid
tracks so it reads as visually heavier than the three secondary tiles.)

Note the grid becomes 5 conceptual columns of width (OEE spans 2 of the 4
`lg:grid-cols-4` tracks) — on the `grid-cols-2` mobile breakpoint
`lg:col-span-2` has no effect, so mobile still stacks 2-per-row as before.

- [ ] **Step 2: Verify visually**

Run the dev server, navigate to `/analytics/oee`, run a computation.
Confirm: OEE renders large or same-position, formatted as `94.2%` (with
the existing `*` suffix when `performance_estimated`), animates counting
up from 0 (or from its previous value) on load/recompute, and keeps its
existing dynamic threshold coloring (red/amber/green via `oeeColor`)
instead of the tone's default color.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/analytics/Oee.tsx
git commit -m "feat: wire OEE metric to StatCard's hero layout"
```

---

### Task 7: Chart entrance animation

**Files:**
- Modify: `frontend/src/pages/analytics/shared.tsx:276-278`

**Interfaces:**
- No new exports — tunes the existing `EChart` wrapper that every chart in
  the app already renders through.

ECharts already animates on initial mount by default
(`animation: true` is the library default), but nothing in this codebase
sets an explicit duration/easing, and every `setOption(..., true)` call
(full replace, on every data update) re-triggers that default entrance
animation on every refresh — this task makes the animation intentional and
consistent rather than relying on library defaults silently doing
*something*.

- [ ] **Step 1: Set explicit animation options in the shared `EChart` wrapper**

Replace:

```tsx
  useEffect(() => {
    chart.current?.setOption({ backgroundColor: "transparent", ...option }, true);
  }, [option]);
```

with:

```tsx
  useEffect(() => {
    chart.current?.setOption(
      {
        backgroundColor: "transparent",
        animation: true,
        animationDuration: 700,
        animationEasing: "cubicOut",
        ...option,
      },
      true,
    );
  }, [option]);
```

(`...option` still comes last, so any chart that explicitly sets its own
`animation*` options in the future can override these defaults.)

- [ ] **Step 2: Verify visually**

Load `/analytics/oee` (or any analytics page with a chart) and confirm the
chart's bars/line draw in smoothly rather than snapping into place.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/analytics/shared.tsx
git commit -m "feat: explicit entrance animation for the shared EChart wrapper"
```

---

### Task 8: Route and tab-switch fade transitions

**Files:**
- Modify: `frontend/src/components/AppShell.tsx:258-260`
- Modify: `frontend/src/pages/Procurement.tsx:34-43`
- Modify: `frontend/src/pages/Warehouse.tsx:44-46`
- Modify: `frontend/src/pages/workflows/RunHistory.tsx:86`

**Interfaces:**
- Consumes: the existing `animate-fade-in` Tailwind animation (already
  defined in `tailwind.config.js`, currently applied once on `<main>`'s
  initial mount only).

Today `<main className="... animate-fade-in ...">` never re-mounts when
navigating between routes (only its child `<Outlet/>` content changes), so
the fade-in only ever plays once, on the very first page load. Keying a
wrapper `div` by the route path forces React to remount it on every
navigation, replaying the animation each time. `PageTabs` itself only
renders the tab buttons — it doesn't own the content region — so the
tab-switch fade is wired the same way (`key={tab}` on the content
container) at each of its three call sites rather than inside `PageTabs`.

- [ ] **Step 1: Key the Outlet wrapper by pathname**

In `frontend/src/components/AppShell.tsx`, replace:

```tsx
          <main className="mx-auto w-full max-w-6xl flex-1 animate-fade-in px-6 py-8">
            <Outlet />
          </main>
```

with:

```tsx
          <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
            <div key={location.pathname} className="animate-fade-in">
              <Outlet />
            </div>
          </main>
```

(`location` is already destructured from `useLocation()` at the top of
`AppShell` for the breadcrumb — no new import needed.)

- [ ] **Step 2: Key `Procurement.tsx`'s tab content by `tab`**

Replace:

```tsx
      {tab === "purchase-orders" && <PurchaseOrdersPanel />}
      {tab === "suppliers" && <SuppliersPanel />}
      {tab === "generate" && (
        <GeneratePosPanel
          initialHorizonStart={searchParams.get("horizonStart")}
          initialHorizonEnd={searchParams.get("horizonEnd")}
        />
      )}
      {tab === "build-calculator" && <BuildCalculatorPanel />}
```

with:

```tsx
      <div key={tab} className="animate-fade-in">
        {tab === "purchase-orders" && <PurchaseOrdersPanel />}
        {tab === "suppliers" && <SuppliersPanel />}
        {tab === "generate" && (
          <GeneratePosPanel
            initialHorizonStart={searchParams.get("horizonStart")}
            initialHorizonEnd={searchParams.get("horizonEnd")}
          />
        )}
        {tab === "build-calculator" && <BuildCalculatorPanel />}
      </div>
```

- [ ] **Step 3: Key `Warehouse.tsx`'s tab content by `tab`**

Replace:

```tsx
      {tab === "inventory" && <InventoryPanel />}
      {tab === "locations" && <LocationsPanel />}
      {tab === "approvals" && canSeeApprovals && <ApprovalsPanel />}
```

with:

```tsx
      <div key={tab} className="animate-fade-in">
        {tab === "inventory" && <InventoryPanel />}
        {tab === "locations" && <LocationsPanel />}
        {tab === "approvals" && canSeeApprovals && <ApprovalsPanel />}
      </div>
```

- [ ] **Step 4: Key `RunHistory.tsx`'s list `Card` by `tab`**

`RunHistory` doesn't branch its JSX per tab — `tab` instead filters the
`filtered` list feeding one shared `<Card>`. Replace the card's opening
tag:

```tsx
      <Card>
        <CardContent className="space-y-3 p-4">
```

with:

```tsx
      <Card key={tab} className="animate-fade-in">
        <CardContent className="space-y-3 p-4">
```

- [ ] **Step 5: Verify visually**

Navigate between two routes (e.g. Dashboard → Items) using the sidebar and
confirm the new page's content fades in each time, not just on first load
(and that clicking the same nav item twice doesn't re-fade, since only a
pathname *change* retriggers `key={location.pathname}`). Then visit
`/procurement`, `/warehouse`, and `/runs` and click between their tabs,
confirming each tab switch cross-fades instead of hard-cutting.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AppShell.tsx frontend/src/pages/Procurement.tsx frontend/src/pages/Warehouse.tsx frontend/src/pages/workflows/RunHistory.tsx
git commit -m "feat: replay fade-in transition on route changes and tab switches"
```

---

### Task 9: Live-pulse indicator on auto-refresh

**Files:**
- Modify: `frontend/src/index.css` (append to `@layer components`)
- Modify: `frontend/src/pages/analytics/shared.tsx:703-709`

**Interfaces:**
- Produces: `.live-pulse-dot` CSS utility class, consumed by
  `EngineScreen` (used by every analytics engine page: Oee, Downtime,
  DowntimeTracking, Throughput, Scrap, Shifts, Spc, PaceTracking,
  PlantSummary — one shared-component edit reaches all of them).

- [ ] **Step 1: Add the pulse keyframe + utility class**

In `frontend/src/index.css`, inside the existing `@layer components`
block (after `.glow-card`'s closing brace, before the final `}` of the
layer), add:

```css

  /* Small breathing dot next to "Live"/auto-refresh indicators. */
  .live-pulse-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 9999px;
    background: hsl(var(--primary));
    box-shadow: 0 0 6px hsl(var(--primary) / 0.7);
    animation: live-pulse 1.6s ease-in-out infinite;
  }
  @keyframes live-pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.4;
    }
  }
```

This inherits the existing global `prefers-reduced-motion` rule (lines
117-124 of the same file) automatically, since it zeroes
`animation-duration` for every element.

- [ ] **Step 2: Show the dot next to the auto-refresh label when active**

In `frontend/src/pages/analytics/shared.tsx`, replace:

```tsx
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh (30s)
            </label>
```

with:

```tsx
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh (30s)
              {autoRefresh && <span aria-hidden className="live-pulse-dot" />}
            </label>
```

- [ ] **Step 3: Verify visually**

On `/analytics/oee` (or any engine page), run a computation, check
"Auto-refresh (30s)", and confirm a small pulsing dot appears next to the
label and keeps gently pulsing while the checkbox stays checked.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/index.css frontend/src/pages/analytics/shared.tsx
git commit -m "feat: live-pulse indicator on active auto-refresh"
```

---

## Follow-up (explicitly out of scope for this plan)

Per the spec: promoting a `size="hero"` metric on the other ~39 pages
beyond OEE is a per-page decision left for a later pass — this plan only
proves out the hero layout on one representative page (Task 6) and the
live-pulse indicator on one shared component (Task 9).
