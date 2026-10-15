/** Sticky page header shared by every workspace screen: route icon and title on the left, the screen's own actions on the right. */

import type { LucideIcon } from "lucide-preact";
import type { ComponentChildren } from "preact";

interface PageHeaderProps {
  title: string;
  icon: LucideIcon;
  children?: ComponentChildren;
}

export function PageHeader({ title, icon: Icon, children }: PageHeaderProps) {
  return (
    <header className="flex h-[var(--header-height)] items-center justify-between gap-3 border-b border-border/60 bg-background/80 px-4 backdrop-blur-md sm:px-6 md:sticky md:top-0 md:z-30">
      <div className="flex min-w-0 items-center gap-2">
        <Icon aria-hidden="true" className="size-[18px] shrink-0" />
        <h1 className="truncate text-[15px] font-medium">{title}</h1>
      </div>
      {children ? <div className="flex shrink-0 items-center gap-2">{children}</div> : null}
    </header>
  );
}
