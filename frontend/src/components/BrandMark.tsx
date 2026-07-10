// Evowerk brand mark: a ring enclosing the angular waveform — a bold rounded
// zig-zag (low-left → peak → valley → peak → valley → high top-right tail).
// Monochrome (currentColor), so it inverts with the theme to match the
// EVOWERK logo (white on dark / black on light).

export function BrandMark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 100 100"
      className={className}
      fill="none"
      stroke="currentColor"
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
    >
      {title && <title>{title}</title>}
      <circle cx="50" cy="50" r="40" strokeWidth="5.5" />
      <polyline
        points="30,58 40,42 48,58 56,45 63,57 74,39"
        strokeWidth="8.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
