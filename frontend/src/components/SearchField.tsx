/** Search input with a leading icon, shared by the activity filter and the results library so every search field looks and behaves alike. */

import { Search } from "lucide-preact";
import type { JSX } from "preact";

type SearchFieldProps = Omit<JSX.InputHTMLAttributes<HTMLInputElement>, "className" | "size"> & {
  className?: string;
  compact?: boolean;
};

export function SearchField({ className = "", compact = false, ...input }: SearchFieldProps) {
  return (
    <label className={`relative block ${className}`}>
      <Search
        aria-hidden="true"
        className={`pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground ${compact ? "size-3.5" : "size-4"}`}
      />
      <input {...input} className={`field pl-8 ${compact ? "h-8 text-xs" : ""}`} />
    </label>
  );
}
