---
name: EvoWerk Cloud MRP — Marketing Site
description: One system for the whole plant, read like a live signal off the floor.
colors:
  bg: "oklch(0.08 0 0)"
  surface: "oklch(0.15 0 0)"
  surface-raised: "oklch(0.19 0 0)"
  ink: "oklch(0.96 0 0)"
  muted: "oklch(0.64 0 0)"
  primary: "oklch(0.72 0.10 174)"
  primary-deep: "oklch(0.58 0.11 174)"
  primary-dim: "oklch(0.30 0.05 174)"
  on-primary: "oklch(0.22 0.03 174)"
  accent: "oklch(0.65 0.17 295)"
  accent-deep: "oklch(0.54 0.17 295)"
  border: "oklch(0.26 0 0)"
  danger: "oklch(0.60 0.19 25)"
typography:
  display:
    fontFamily: "'Space Grotesk', Inter, system-ui, sans-serif"
    fontSize: "clamp(2.75rem, 6vw, 5.75rem)"
    fontWeight: 500
    lineHeight: 0.98
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "'Space Grotesk', Inter, system-ui, sans-serif"
    fontSize: "clamp(1.75rem, 3vw, 2.75rem)"
    fontWeight: 500
    lineHeight: 1.08
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "'JetBrains Mono', 'IBM Plex Mono', ui-monospace, monospace"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.02em"
  wordmark:
    fontFamily: "'Space Grotesk', Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.01em"
rounded:
  sm: "4px"
  md: "8px"
  lg: "14px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "24px"
  lg: "48px"
  xl: "96px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.sm}"
    padding: "16px 32px"
  button-primary-hover:
    backgroundColor: "{colors.primary-deep}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.sm}"
    padding: "16px 32px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "16px 32px"
---

# Design System: EvoWerk Cloud MRP — Marketing Site

## 1. Overview

**Creative North Star: "The Blueprint"**

The site reads like a dark-mode engineering workstation, not a startup pitch deck: a near-black graphite canvas, one signal teal doing the work an oscilloscope trace does on a technical readout, and a circuit violet reserved for the things that would light up on a machine — a status indicator, a highlight, a proof point worth stopping on. Teal and violet are not invented for this site — they're EvoWerk's actual product colors, pulled directly from the app's dark-mode tokens, so the marketing site and the product it's selling look like they come from the same place. It borrows its darkness and unhurried pacing from otsuka-air.jp, its nerve to commit a single saturated color to real surface area from coffee-tech.com, and its confidence in oversized display type from houseofhoney.com — but none of their warmth-by-default. This is premium-enterprise read through an industrial-craft lens: precision, not friendliness.

This system explicitly rejects the generic SaaS-cream template — no warm beige or near-white backgrounds, no interchangeable stock-photo hero, no logo-soup "trusted by" row (there are no logos yet, and the design doesn't pretend otherwise). It also rejects the dated legacy-enterprise look: no dense gray toolbars, no corporate blue-and-white, nothing that reads as 2012-era chrome.

**Key Characteristics:**
- Near-black graphite base; signal teal is the one color that carries real surface weight
- Circuit violet reserved for proof, alerts, and the few moments that should feel like a live signal
- Display type large enough to hold a claim with conviction; body type set for reading, not skimming
- Monospace used deliberately for technical specifics (part counts, metrics, system facts) — the visual signature of a system that actually does engineering
- Flat by default; depth comes from layered graphite surfaces and a soft teal glow on interactive elements, never from a drop shadow doing the work of a border

## 2. Colors

Color strategy: **Committed**, verging on **Drenched** in the hero and section dividers — signal teal carries 30–50% of any given screen's weight, with graphite doing the rest and violet appearing only where something needs to feel alive.

### Primary
- **Signal Teal** (oklch(0.72 0.10 174)): the brand color, matched to the product app's own `--primary` dark-mode token so the two surfaces read as one company. CTAs, key headline accents, active nav state, the wash behind hero claims. Light enough that it takes **dark** text (oklch(0.22 0.03 174), a deep teal-black — not white; white fails contrast at this lightness), not the other way around.

### Secondary
- **Circuit Violet** (oklch(0.65 0.17 295)): matched to the product app's own `--accent` token. Reserved for proof and signal — metric callouts, the security/audit-trail specifics that substitute for testimonials, live-indicator dots, technical highlight terms. Distinct from primary in both hue (~120° apart) and role: teal is the brand, violet is "this is real and it's working."

### Neutral
- **Graphite** (oklch(0.08 0 0)): page background. Pure near-black, zero chroma — matches the product app's own dark bg exactly.
- **Graphite Surface** (oklch(0.15 0 0)): card and section backgrounds, one step up from bg. Zero chroma, same as the product app's `--card` token.
- **Graphite Raised** (oklch(0.19 0 0)): the topmost layer — modals, active cards, anything that should read as closest to the viewer.
- **Signal White** (oklch(0.96 0 0)): primary text on dark surfaces. ~18:1 against Graphite bg.
- **Steel Muted** (oklch(0.64 0 0)): secondary text, captions, timestamps. ~5.6:1 against Graphite bg — comfortably clears body-text contrast even though it's a supporting role.
- **Border Line** (oklch(0.26 0 0)): hairline dividers and card borders. Never a colored stripe.

### Named Rules
**The One Signal Rule.** Teal is the only color allowed to carry a CTA or an active state. Violet never appears on a button — it marks proof, not action, so the two never compete for the eye.

**The Dark-on-Light-Fill Rule.** Any element filled with Signal Teal (buttons, active pills) takes dark text, not white — the teal is light enough (L 0.72) that white fails WCAG contrast on it, exactly matching the product app's own `--primary-foreground` convention.

## 3. Typography

**Display Font:** Space Grotesk (fallback: Inter, system-ui)
**Body Font:** Inter
**Label/Mono Font:** JetBrains Mono (fallback: IBM Plex Mono, ui-monospace)
**Wordmark:** Space Grotesk Bold, uppercase, +0.01em tracking — matches the weight and geometry of the EvoWerk logotype.

**Character:** A geometric-grotesk display paired with a humanist body on the same axis of restraint, plus a monospace voice reserved for anything that's actually a system fact — a spec, a count, a metric — so precision has its own typographic signature instead of being dressed up like marketing copy.

### Hierarchy
- **Display** (500, clamp(2.75rem, 6vw, 5.75rem), line-height 0.98, letter-spacing -0.03em): hero claim and section-opening statements only. `text-wrap: balance`.
- **Headline** (500, clamp(1.75rem, 3vw, 2.75rem), line-height 1.08, letter-spacing -0.02em): section headers.
- **Title** (600, 1.25rem, line-height 1.3): card titles, feature names.
- **Body** (400, 1.0625rem, line-height 1.6): all prose. Capped at 68ch. `text-wrap: pretty`.
- **Label** (500, 0.8125rem, letter-spacing 0.02em, monospace): eyebrow-free — used instead for inline technical facts ("append-only · Postgres trigger-enforced"), metric units, and nav/UI chrome, uppercase only when it's genuinely a system label (nav items), never as decorative section kickers.
- **Wordmark** (700, 1rem, uppercase, letter-spacing 0.01em): the EvoWerk logotype only. Never reused for body labels.

### Named Rules
**The No-Kicker Rule.** No uppercase-tracked eyebrow sits above section headings as scaffolding. Where a technical label is genuinely useful (a spec, a stat's unit), it earns its place in monospace, inline with the content it describes — never as a standalone ritual line above every section.

## 4. Elevation

Flat by default. Depth is conveyed through the three-step graphite layering (bg → surface → raised) and a soft teal glow on hover/focus, not drop shadows pretending to be lighting. The one exception is the hero and any full-bleed teal section, which uses a large, very low-opacity radial glow (teal, with a touch of violet) to suggest depth behind the type — atmosphere, not object shadow.

### Shadow Vocabulary
- **focus-glow** (`box-shadow: 0 0 0 3px oklch(0.72 0.10 174 / 0.35)`): keyboard focus ring on all interactive elements, teal-tinted for brand consistency.
- **hover-lift** (`box-shadow: 0 8px 32px oklch(0.72 0.10 174 / 0.25)`): primary buttons and interactive cards on hover, paired with a 2px translateY.
- **ambient-hero** (`box-shadow: none; background: radial-gradient(...)`): not a true shadow — see above.

### Named Rules
**The Flat-By-Default Rule.** Surfaces sit flat at rest. Shadows and glow appear only in response to state — hover, focus, active — never as permanent card decoration.

## 5. Components

### Buttons
- **Shape:** 4px radius — sharp enough to feel engineered, not a rounded pill.
- **Primary:** Signal Teal background, dark on-primary text, 16px/32px padding, no border.
- **Hover / Focus:** background shifts to Primary Deep (oklch(0.58 0.11 174)), 2px lift, hover-lift shadow; focus adds focus-glow ring, never removes the outline without replacing it.
- **Ghost / Secondary:** transparent background, Signal White text, 1px Border Line stroke; hover fills to Graphite Surface.

### Cards / Containers
- **Corner Style:** 8px radius (rounded.md).
- **Background:** Graphite Surface, or Graphite Raised for the emphasized/active state.
- **Shadow Strategy:** flat at rest; hover-lift on interactive cards only.
- **Border:** 1px Border Line on cards that sit directly on bg (no surface-color separation needed otherwise).
- **Internal Padding:** 24px (spacing.md) minimum, 48px (spacing.lg) for feature-level cards.

### Inputs / Fields
- **Style:** Graphite Surface background, 1px Border Line stroke, 8px radius.
- **Focus:** border shifts to Signal Teal, focus-glow ring appears.
- **Error:** border shifts to Danger (oklch(0.60 0.19 25)), no glow.

### Navigation
- Fixed top bar, Graphite bg at low opacity with backdrop-blur once scrolled past the hero (a deliberate, sparing use of the one glassmorphism moment this system allows). Label-style (monospace, 13px) nav items. Active/hover state underlines in Signal Teal, never a background pill.

### Logo Mark
The real EvoWerk logo (`website/public/evowerk-logo.svg`, provided by the user — never redrawn or approximated): a thin-stroke ring containing a signal/waveform glyph, paired with the EVOWERK wordmark. `src/components/Logo.astro` crops it via SVG viewBox — the `full` variant (nav, footer) shows ring + wordmark; the `icon` variant (favicon) shows just the ring. The waveform doubles as a visual motif that echoes the sensor/signal language used throughout Trust and Show-Floor (the live audit log, the sparklines).

### Marquee Strip (signature component)
A horizontal, slow-scrolling text strip (borrowed from coffee-tech's "Beyond your expectations /" pattern, repurposed) used exactly once — between the hero and the pain section — repeating the single memorable line ("One system for the whole plant · Not eleven tools stitched together ·") in Label/mono type at low opacity against Graphite bg. Pauses entirely under `prefers-reduced-motion`.

## 6. Do's and Don'ts

### Do:
- **Do** let Signal Teal carry 30–50% of hero and divider surface area — this is a Committed strategy, not a restrained accent.
- **Do** use dark text on any Signal Teal fill, never white — see the Dark-on-Light-Fill Rule.
- **Do** use monospace for every system fact (metrics, spec counts, technical labels) so precision reads as a typographic signature.
- **Do** open every major section by naming the real cost of the status quo (spreadsheets, legacy ERP) before the pitch, per PRODUCT.md's belief ladder.
- **Do** ship a `prefers-reduced-motion` alternative for every animation, including the marquee strip.
- **Do** vary scroll-reveal treatment by hierarchy: the default `.reveal` fade+rise for ordinary blocks, the `.reveal-scan` clip-path wipe reserved for the one "hero panel" per section (module explorer, audit terminal). Every block sharing an identical entrance is the tell of a templated page.
- **Do** limit pulsing/breathing indicators to one per page (the terminal cursor blink) — a second "live" pulse elsewhere reads as decorative, not informative.

### Don't:
- **Don't** use a warm beige, sand, or near-white background anywhere — this is PRODUCT.md's named anti-reference (the generic SaaS-cream template) and it is fully banned here, including in light-mode surfaces if any are added later.
- **Don't** ship a stock-photo hero or an interchangeable "team in a modern office" image.
- **Don't** add a "trusted by" logo row — there are no logos yet; don't imply otherwise.
- **Don't** use `border-left`/`border-right` as a colored accent stripe on any card, list item, or callout.
- **Don't** use gradient text (`background-clip: text` + gradient).
- **Don't** add an uppercase-tracked eyebrow above section headings as default scaffolding — see the No-Kicker Rule.
- **Don't** default to numbered section markers (01 / 02 / 03) unless a section is a genuine ordered sequence.
- **Don't** let Circuit Violet appear on a clickable CTA — violet marks proof, teal marks action.
- **Don't** invent brand colors that drift from the product app's real tokens (`frontend/src/index.css` `--primary` / `--accent`). This site and the product it sells must look related.
