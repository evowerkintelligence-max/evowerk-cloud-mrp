// Evowerk brand mark: the gear/binary logo, cropped to the mark (no wordmark)
// with a transparent backdrop - see frontend/public/logo.png.

export function BrandMark({ className, title }: { className?: string; title?: string }) {
  return (
    <img
      src="/logo.png"
      className={className}
      alt={title ?? ""}
      aria-hidden={title ? undefined : true}
      draggable={false}
    />
  );
}
