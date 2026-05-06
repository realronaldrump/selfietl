import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...values: ClassValue[]) {
  return twMerge(clsx(values));
}

export function Button({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "md" | "sm" | "icon";
}) {
  const variants = {
    primary: "bg-ink text-paper hover:bg-graphite",
    secondary: "bg-paper text-ink shadow-line hover:bg-bone",
    ghost: "bg-transparent text-ink hover:bg-ink/5",
    danger: "bg-coral text-paper hover:bg-coral/90",
  };
  const sizes = {
    md: "min-h-11 px-4 py-2 text-sm",
    sm: "min-h-10 px-3 py-1.5 text-xs",
    icon: "h-11 w-11 p-0",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}

export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-lg bg-paper p-4 shadow-line", className)}>{children}</section>;
}

export function Label({ children }: { children: ReactNode }) {
  return <label className="text-xs font-bold uppercase tracking-[0.08em] text-ink/60">{children}</label>;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "min-h-11 w-full rounded-md border border-ink/15 bg-white px-3 text-sm text-ink outline-none transition placeholder:text-ink/35 focus:border-teal focus:ring-2 focus:ring-teal/20",
        props.className,
      )}
    />
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        "min-h-11 w-full cursor-pointer rounded-md border border-ink/15 bg-paper px-3 text-sm font-semibold text-ink shadow-line outline-none transition hover:border-teal/45 focus:border-teal focus:bg-white focus:ring-2 focus:ring-teal/20",
        props.className,
      )}
    />
  );
}

export function Metric({ label, value, tone = "default" }: { label: string; value: ReactNode; tone?: "default" | "warn" | "good" }) {
  const tones = {
    default: "border-ink/10 bg-white",
    warn: "border-coral/30 bg-coral/10",
    good: "border-teal/30 bg-teal/10",
  };
  return (
    <div className={cn("rounded-md border p-3", tones[tone])}>
      <div className="text-[0.68rem] font-bold uppercase tracking-[0.08em] text-ink/55">{label}</div>
      <div className="mt-1 text-xl font-black text-ink">{value}</div>
    </div>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-2 overflow-hidden rounded-full bg-ink/10">
      <div className="h-full rounded-full bg-teal transition-all" style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function Badge({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "good" | "warn" | "bad" }) {
  const tones = {
    default: "bg-ink/8 text-ink",
    good: "bg-teal/15 text-teal",
    warn: "bg-amber/20 text-ink",
    bad: "bg-coral/15 text-coral",
  };
  return <span className={cn("inline-flex rounded px-2 py-1 text-xs font-bold", tones[tone])}>{children}</span>;
}
