import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type ComponentPropsWithoutRef, type ElementRef, type HTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "../lib/utils";

const buttonStyles = cva("inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50", {
  variants: {
    variant: {
      default: "bg-blue-600 text-white hover:bg-blue-700",
      outline: "border border-slate-200 bg-white text-slate-700 hover:border-blue-300 hover:bg-blue-50",
      ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-950",
      destructive: "bg-red-600 text-white hover:bg-red-700",
      success: "bg-emerald-500 text-navy-950 hover:bg-emerald-400",
    },
    size: { default: "h-10 px-4 py-2", sm: "h-8 rounded-md px-3 text-xs", lg: "h-11 px-5", icon: "size-9" },
  },
  defaultVariants: { variant: "default", size: "default" },
});

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonStyles>;
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) =>
  <button ref={ref} className={cn(buttonStyles({ variant, size, className }))} {...props} />);
Button.displayName = "Button";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-2xl border border-slate-200/80 bg-white shadow-sm", className)} {...props} />;
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-start justify-between gap-4 p-5 pb-3", className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5 pt-2", className)} {...props} />;
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn("flex h-10 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100", className)} {...props} />;
}

export function Badge({ children, tone = "neutral", className }: { children: ReactNode; tone?: "neutral" | "success" | "warning" | "danger" | "info"; className?: string }) {
  const tones = {
    neutral: "bg-slate-100 text-slate-600",
    success: "bg-emerald-50 text-emerald-700 ring-emerald-600/10",
    warning: "bg-amber-50 text-amber-800 ring-amber-600/10",
    danger: "bg-red-50 text-red-700 ring-red-600/10",
    info: "bg-blue-50 text-blue-700 ring-blue-600/10",
  };
  return <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-inset", tones[tone], className)}>{children}</span>;
}

export function PageTitle({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
    <div>{eyebrow && <p className="mb-2 text-[10px] font-bold uppercase tracking-[.18em] text-blue-600">{eyebrow}</p>}<h1 className="font-display text-3xl font-extrabold tracking-tight text-navy-950 sm:text-4xl">{title}</h1>{description && <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">{description}</p>}</div>
    {actions && <div className="flex items-center gap-2">{actions}</div>}
  </div>;
}

export function EmptyState({ icon, title, description, action }: { icon: ReactNode; title: string; description: string; action?: ReactNode }) {
  return <div className="flex min-h-56 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white px-6 text-center">
    <div className="mb-4 grid size-12 place-items-center rounded-2xl bg-blue-50 text-blue-600">{icon}</div><h3 className="font-display text-base font-bold text-navy-950">{title}</h3><p className="mt-1 max-w-md text-sm leading-6 text-slate-500">{description}</p>{action && <div className="mt-4">{action}</div>}
  </div>;
}

export function LoadingBlock({ label = "Loading workspace" }: { label?: string }) {
  return <div className="flex min-h-40 items-center justify-center gap-3 text-sm text-slate-500"><span className="size-5 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600 motion-reduce:animate-none" />{label}</div>;
}

export function AlertBox({ children, tone = "error", className }: { children: ReactNode; tone?: "error" | "warning" | "success" | "info"; className?: string }) {
  const colors = { error: "border-red-200 bg-red-50 text-red-800", warning: "border-amber-200 bg-amber-50 text-amber-900", success: "border-emerald-200 bg-emerald-50 text-emerald-800", info: "border-blue-200 bg-blue-50 text-blue-800" };
  return <div role={tone === "error" || tone === "warning" ? "alert" : "status"} className={cn("rounded-xl border px-4 py-3 text-sm leading-6", colors[tone], className)}>{children}</div>;
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmText, onConfirm, busy, destructive = false }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description: ReactNode; confirmText: string;
  onConfirm: () => void; busy?: boolean; destructive?: boolean;
}) {
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-navy-950/35 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
      <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
        <DialogPrimitive.Close className="absolute right-4 top-4 rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><X size={18} /></DialogPrimitive.Close>
        <DialogPrimitive.Title className="font-display text-xl font-bold text-navy-950">{title}</DialogPrimitive.Title>
        <DialogPrimitive.Description asChild><div className="mt-3 text-sm leading-6 text-slate-600">{description}</div></DialogPrimitive.Description>
        <div className="mt-6 flex justify-end gap-2"><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant={destructive ? "destructive" : "default"} disabled={busy} onClick={onConfirm}>{busy ? "Saving…" : confirmText}</Button></div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </DialogPrimitive.Root>;
}

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = forwardRef<
  ElementRef<typeof TooltipPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 max-w-sm rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-700 shadow-xl select-text",
        className
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;
