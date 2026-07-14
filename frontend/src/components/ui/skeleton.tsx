import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-shimmer rounded-md bg-gradient-to-r from-secondary/60 via-secondary/30 to-secondary/60 bg-[length:200%_100%]",
        className,
      )}
      {...props}
    />
  );
}
