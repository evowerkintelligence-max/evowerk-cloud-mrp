# Theme

Dark-first design system driven by HSL CSS custom properties (channels only, no `hsl()`
wrapper, so Tailwind can compose alpha via `hsl(var(--token) / <alpha>)`). Light values
live on `:root`; dark overrides on `.dark`. The app ships with `<html class="dark">` by
default (`html { color-scheme: dark; }` in `index.css`) — dark is the primary/shipped
theme, light exists as a token set but isn't actively toggled in the UI today.

Font: **Inter** (via `@fontsource/inter`, weights 400/500/600/700, loaded in `main.tsx`).

---

## `frontend/src/index.css` (full)

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/*
  Token layer. Colors are HSL channels (no hsl() wrapper) so Tailwind can compose
  them with alpha via hsl(var(--token) / <alpha>). Light values live on :root;
  the app ships dark-first via <html class="dark">.
*/
@layer base {
  :root {
    --background: 210 40% 98%;
    --foreground: 222 47% 11%;
    --card: 0 0% 100%;
    --card-foreground: 222 47% 11%;
    --popover: 0 0% 100%;
    --popover-foreground: 222 47% 11%;
    --primary: 190 90% 38%;
    --primary-foreground: 0 0% 100%;
    --secondary: 210 40% 94%;
    --secondary-foreground: 222 47% 15%;
    --muted: 210 40% 94%;
    --muted-foreground: 215 16% 42%;
    --accent: 258 82% 57%;
    --accent-foreground: 0 0% 100%;
    --destructive: 0 72% 48%;
    --destructive-foreground: 0 0% 100%;
    --success: 152 58% 34%;
    --success-foreground: 0 0% 100%;
    --warning: 34 92% 42%;
    --warning-foreground: 0 0% 100%;
    --border: 214 32% 88%;
    --input: 214 32% 84%;
    --ring: 190 90% 38%;
    --brand: 201 82% 42%;
    --radius: 0.625rem;
  }

  .dark {
    --background: 224 42% 4%;
    --foreground: 213 31% 93%;
    --card: 223 36% 7%;
    --card-foreground: 213 31% 93%;
    --popover: 224 40% 6%;
    --popover-foreground: 213 31% 93%;
    --primary: 189 94% 55%;
    --primary-foreground: 202 84% 8%;
    --secondary: 220 28% 13%;
    --secondary-foreground: 213 31% 93%;
    --muted: 220 28% 11%;
    --muted-foreground: 217 15% 58%;
    --accent: 258 90% 70%;
    --accent-foreground: 210 40% 98%;
    --destructive: 0 72% 55%;
    --destructive-foreground: 210 40% 98%;
    --success: 152 62% 45%;
    --success-foreground: 155 80% 8%;
    --warning: 38 95% 58%;
    --warning-foreground: 30 90% 8%;
    --border: 221 28% 14%;
    --input: 221 28% 19%;
    --ring: 189 94% 55%;
    --brand: 201 88% 56%;
    --radius: 0.625rem;
  }

  * {
    border-color: hsl(var(--border));
  }

  html {
    color-scheme: dark;
  }

  body {
    @apply bg-background text-foreground font-sans antialiased;
    font-feature-settings: "cv02", "cv03", "cv04", "cv11";
    text-rendering: optimizeLegibility;
  }

  ::selection {
    background: hsl(var(--primary) / 0.25);
    color: hsl(var(--foreground));
  }

  /* Thin, theme-matched scrollbars. */
  * {
    scrollbar-width: thin;
    scrollbar-color: hsl(var(--border)) transparent;
  }
  ::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  ::-webkit-scrollbar-track {
    background: transparent;
  }
  ::-webkit-scrollbar-thumb {
    background: hsl(var(--border));
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: hsl(var(--input));
  }

  /* Native file inputs pick up the button system instead of browser chrome. */
  input[type="file"] {
    @apply text-sm text-muted-foreground;
  }
  input[type="file"]::file-selector-button {
    @apply mr-3 cursor-pointer rounded-md border border-solid border-input bg-secondary px-3 py-1.5 text-sm font-medium text-secondary-foreground transition-colors;
  }
  input[type="file"]:hover::file-selector-button {
    @apply bg-secondary/70;
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
}

@layer components {
  /* Fixed subtle grid + radial glow behind the app shell and auth screens. */
  .app-backdrop {
    position: fixed;
    inset: 0;
    z-index: -1;
    background-color: hsl(var(--background));
    background-image:
      radial-gradient(50% 38% at 70% 0%, hsl(var(--primary) / 0.07), transparent 70%),
      radial-gradient(40% 30% at 20% 0%, hsl(var(--accent) / 0.05), transparent 70%),
      linear-gradient(to right, hsl(var(--border) / 0.28) 1px, transparent 1px),
      linear-gradient(to bottom, hsl(var(--border) / 0.28) 1px, transparent 1px);
    background-size: 100% 100%, 100% 100%, 48px 48px, 48px 48px;
    background-position: 0 0, 0 0, -1px -1px, -1px -1px;
    -webkit-mask-image: radial-gradient(120% 90% at 50% 0%, #000 45%, transparent 100%);
    mask-image: radial-gradient(120% 90% at 50% 0%, #000 45%, transparent 100%);
  }

  /* Thin gradient brand rule / accent bar. */
  .brand-gradient {
    background-image: linear-gradient(
      90deg,
      hsl(189 94% 53%),
      hsl(212 95% 60%),
      hsl(262 83% 68%)
    );
  }

  /* Data values: lining, tabular figures so columns of numbers align. */
  .tabular {
    font-variant-numeric: tabular-nums lining-nums;
  }

  /* Hairline conic border glow for the one signature surface (auth card). */
  .glow-card {
    position: relative;
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
  }
  .glow-card::before {
    content: "";
    position: absolute;
    inset: -1px;
    z-index: -1;
    border-radius: inherit;
    background: linear-gradient(
      165deg,
      hsl(var(--primary) / 0.5),
      transparent 30%,
      transparent 70%,
      hsl(var(--accent) / 0.35)
    );
    filter: blur(0.5px);
  }
}
```

---

## `frontend/tailwind.config.js` (full)

```js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        brand: "hsl(var(--brand))",
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "sans-serif"],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Cascadia Code",
          "JetBrains Mono",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        glow: "0 0 0 1px hsl(var(--primary) / 0.25), 0 8px 32px -8px hsl(var(--primary) / 0.45)",
        "glow-sm": "0 0 18px -6px hsl(var(--primary) / 0.55)",
        card: "0 1px 2px 0 hsl(224 42% 2% / 0.4)",
        overlay:
          "0 0 0 1px hsl(var(--border)), 0 16px 48px -12px hsl(224 42% 2% / 0.8), 0 4px 16px -4px hsl(224 42% 2% / 0.5)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "translateY(8px) scale(0.97)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        shimmer: {
          from: { backgroundPosition: "200% 0" },
          to: { backgroundPosition: "-200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out",
        "scale-in": "scale-in 0.18s cubic-bezier(0.16, 1, 0.3, 1)",
        shimmer: "shimmer 2s linear infinite",
      },
    },
  },
  plugins: [],
};
```

---

## Extracted reference

### Colors (dark theme — shipped default, `.dark` class)

| Token | HSL | Swatch meaning |
|---|---|---|
| `--background` | `224 42% 4%` | near-black page background |
| `--foreground` | `213 31% 93%` | primary text |
| `--card` | `223 36% 7%` | card/panel surface |
| `--popover` | `224 40% 6%` | dialog/palette surface |
| `--primary` | `189 94% 55%` | cyan brand accent (buttons, active nav, focus ring) |
| `--primary-foreground` | `202 84% 8%` | text on primary |
| `--secondary` | `220 28% 13%` | subdued surface (secondary buttons, hover fills) |
| `--muted` / `--muted-foreground` | `220 28% 11%` / `217 15% 58%` | muted backgrounds / muted text |
| `--accent` | `258 90% 70%` | violet secondary accent |
| `--destructive` | `0 72% 55%` | error/danger red |
| `--success` | `152 62% 45%` | success green |
| `--warning` | `38 95% 58%` | warning amber |
| `--border` | `221 28% 14%` | hairline borders |
| `--input` | `221 28% 19%` | input borders |
| `--ring` | `189 94% 55%` | focus ring (= primary) |
| `--brand` | `201 88% 56%` | secondary brand blue (used ad hoc) |

### Colors (light theme — `:root`, token set exists but app isn't actively toggled to it)

| Token | HSL |
|---|---|
| `--background` | `210 40% 98%` |
| `--foreground` | `222 47% 11%` |
| `--card` / `--popover` | `0 0% 100%` |
| `--primary` | `190 90% 38%` |
| `--secondary` / `--muted` | `210 40% 94%` |
| `--accent` | `258 82% 57%` |
| `--destructive` | `0 72% 48%` |
| `--success` | `152 58% 34%` |
| `--warning` | `34 92% 42%` |
| `--border` | `214 32% 88%` |
| `--input` | `214 32% 84%` |

Badge/alert components also use raw Tailwind color families directly (not tokenized):
`sky`, `emerald`, `amber`, `rose`, `indigo`, `teal` at `-500` with `/10`–`/30` alpha, for
Alert and Badge variant colors respectively.

### Typography

- Font family: **Inter** (`@fontsource/inter` 400/500/600/700) → `font-sans`.
- Monospace stack (`font-mono`): `ui-monospace, SFMono-Regular, Cascadia Code, JetBrains Mono, Menlo, Consolas, monospace`.
- No custom `fontSize` scale — uses Tailwind defaults (`text-xs` … `text-3xl`) directly in components, e.g. page titles `text-2xl sm:text-[28px]`, stat values `text-3xl`, eyebrow labels `text-[11px] uppercase tracking-[0.16em]`.
- Numeric data uses the `.tabular` utility class (`font-variant-numeric: tabular-nums lining-nums`) for column alignment.

### Spacing / Radius

- Radius scale derives from one variable: `--radius: 0.625rem` (10px).
  - `rounded-lg` → `var(--radius)` (10px)
  - `rounded-md` → `calc(var(--radius) - 2px)` (8px)
  - `rounded-sm` → `calc(var(--radius) - 4px)` (6px)
- No custom spacing scale extension — uses Tailwind's default spacing scale throughout (`p-4`, `p-5`, `p-6`, `gap-2`/`gap-3`/`gap-4`, etc.)
- Layout containers: sidebar `w-[248px]`, topbar/sidebar-header `h-14`, main content `max-w-6xl`, outer app shell `max-w-[1680px]`.

### Shadows

| Token | Value | Usage |
|---|---|---|
| `shadow-card` | `0 1px 2px 0 hsl(224 42% 2% / 0.4)` | default card/table elevation |
| `shadow-glow-sm` | `0 0 18px -6px hsl(var(--primary) / 0.55)` | primary button hover glow |
| `shadow-glow` | `0 0 0 1px hsl(var(--primary) / 0.25), 0 8px 32px -8px hsl(var(--primary) / 0.45)` | (defined, larger glow variant) |
| `shadow-overlay` | `0 0 0 1px hsl(var(--border)), 0 16px 48px -12px hsl(224 42% 2% / 0.8), 0 4px 16px -4px hsl(224 42% 2% / 0.5)` | modal/dialog/command-palette elevation |

### Motion

| Animation | Keyframe | Duration/easing | Usage |
|---|---|---|---|
| `animate-fade-in` | translateY(4px)→0, opacity 0→1 | 0.25s ease-out | page content mount, overlays |
| `animate-scale-in` | translateY(8px) scale(.97) → translateY(0) scale(1), opacity 0→1 | 0.18s cubic-bezier(0.16,1,0.3,1) | dialog/palette pop-in |
| `animate-shimmer` | backgroundPosition 200%→-200% | 2s linear infinite | `Skeleton` loading shimmer |

`prefers-reduced-motion: reduce` collapses all animation/transition durations to `0.01ms`.

### Breakpoints

Default Tailwind breakpoints (no override in config): `sm: 640px`, `md: 768px`, `lg: 1024px`, `xl: 1280px`, `2xl: 1536px`. Notable responsive behavior:
- `AppShell` sidebar hidden below `md`, replaced by a horizontal tab strip.
- `AuthLayout` pitch panel hidden below `lg`.
- `PageHeader` actions row stacks below `sm`.

### Special surfaces

- `.app-backdrop` — fixed, `z-index:-1` background behind `AppShell`/`AuthLayout`: two soft radial glows (primary top-right, accent top-left) over a 48px grid of hairline lines, masked to fade out toward the bottom via a radial mask.
- `.glow-card` — auth form card: a `::before` pseudo-element conic-ish diagonal gradient border (primary → transparent → accent) simulating a glowing 1px edge.
- `.brand-gradient` — 3-stop cyan→blue→violet linear gradient utility (defined, not directly referenced elsewhere in current pages — available for accent bars).
