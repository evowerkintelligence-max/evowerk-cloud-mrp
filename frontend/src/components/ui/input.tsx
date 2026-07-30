import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background/60 text-sm text-foreground shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60",
        // file:h-full keeps the native "Choose File" button's own border+padding within the
        // input's fixed h-10 box - without it the button is taller than the space left by the
        // host's px-3 py-2 and pokes out through the rounded border.
        type === "file"
          ? "file:mr-3 file:h-full file:rounded-l-md file:border-0 file:bg-secondary file:px-3 file:text-sm file:text-foreground"
          : "px-3 py-2",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
