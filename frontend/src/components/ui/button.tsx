import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 active:translate-y-px [&_svg]:size-4 [&_svg]:shrink-0",
  {
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
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-10 px-4 text-sm",
        lg: "h-11 px-6 text-sm",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

export { buttonVariants };
