/** Keeps route navigation and active-run or preview status available: a sidebar on wide screens, a top tab bar on narrow ones. */

import { FileText } from "lucide-preact";

import { type AppRoute, getRoutePath, NAV_ITEMS } from "../routes";

interface NavigationProps {
  activeTab: AppRoute;
  onTabChange: (tab: AppRoute) => void;
  isRunning: boolean;
  resultCount: number;
  isPreview: boolean;
}

export function Navigation({
  activeTab,
  onTabChange,
  isRunning,
  resultCount,
  isPreview,
}: NavigationProps) {
  const statusLabel = isPreview
    ? isRunning
      ? "Simulated run"
      : "Preview mode"
    : "Extraction running";

  function navigate(event: MouseEvent, tab: AppRoute) {
    event.preventDefault();
    onTabChange(tab);
  }

  return (
    <aside className="sticky top-0 z-40 flex flex-col border-b bg-sidebar md:fixed md:inset-y-0 md:left-0 md:w-[var(--sidebar-width)] md:border-b-0 md:border-r">
      <div className="flex h-[var(--header-height)] items-center gap-3 px-4 md:border-b">
        <a
          className="flex min-w-0 items-center gap-2 font-semibold tracking-tight"
          href="/pipeline"
          onClick={(event) => navigate(event, "pipeline")}
        >
          <span className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
            <FileText aria-hidden="true" className="size-3.5" />
          </span>
          <span className="truncate">LegalDoc Extraction</span>
        </a>
        {(isPreview || isRunning) && (
          <RunStatus className="ml-auto md:hidden" isRunning={isRunning} label={statusLabel} />
        )}
      </div>
      <nav
        aria-label="Workspace"
        className="no-scrollbar flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:gap-0.5 md:overflow-visible md:py-3"
      >
        {NAV_ITEMS.map((item) => {
          const active = activeTab === item.id;
          const Icon = item.icon;
          return (
            <a
              key={item.id}
              href={getRoutePath(item.id)}
              aria-current={active ? "page" : undefined}
              className={`flex h-9 shrink-0 items-center gap-2.5 rounded-lg px-2.5 text-sm transition-colors ${
                active
                  ? "bg-accent font-medium text-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              }`}
              onClick={(event) => navigate(event, item.id)}
            >
              <Icon aria-hidden="true" className="size-4" />
              <span>{item.label}</span>
              {item.id === "explorer" && resultCount > 0 && (
                <span className="count ml-auto" aria-label={`${resultCount} results`}>
                  {resultCount}
                </span>
              )}
            </a>
          );
        })}
      </nav>
      {(isPreview || isRunning) && (
        <div className="mt-auto hidden border-t p-3 md:block">
          <RunStatus isRunning={isRunning} label={statusLabel} />
        </div>
      )}
    </aside>
  );
}

/** One status rule for both layouts: a pulsing green dot while a run is active, amber for preview mode. */
function RunStatus({
  isRunning,
  label,
  className = "",
}: {
  isRunning: boolean;
  label: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs text-muted-foreground ${className}`}
      role="status"
    >
      <span
        className={`size-1.5 rounded-full ${isRunning ? "animate-pulse bg-success" : "bg-warning"}`}
      />
      {label}
    </span>
  );
}
