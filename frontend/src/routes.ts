/** Keeps workspace navigation and browser history on the same stable routes. */

import type { LucideIcon } from "lucide-preact";
import { Library, ScanText, SlidersHorizontal } from "lucide-preact";

export type AppRoute = "settings" | "pipeline" | "explorer";

export const DEFAULT_ROUTE: AppRoute = "pipeline";

export const ROUTE_PATHS: Record<AppRoute, string> = {
  settings: "/settings",
  pipeline: "/pipeline",
  explorer: "/explorer",
};

export const NAV_ITEMS: Array<{
  id: AppRoute;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "pipeline", label: "Extraction", icon: ScanText },
  { id: "explorer", label: "Results library", icon: Library },
  { id: "settings", label: "Configuration", icon: SlidersHorizontal },
];

export function getRoutePath(route: AppRoute): string {
  return ROUTE_PATHS[route];
}

export function getRouteFromPath(pathname: string): AppRoute {
  const normalizedPath = pathname.replace(/\/+$/, "") || "/";
  if (normalizedPath === "/") {
    return DEFAULT_ROUTE;
  }
  for (const [route, routePath] of Object.entries(ROUTE_PATHS) as Array<[AppRoute, string]>) {
    if (normalizedPath === routePath) {
      return route;
    }
  }
  return DEFAULT_ROUTE;
}
