/** Build and development configuration that connects the frontend to local runtime services. */

import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import preact from "@preact/preset-vite";
import type { Plugin } from "vite";
import { defineConfig, loadEnv } from "vite";

const frontendRoot = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(frontendRoot, "..");
const backendTarget = process.env.VITE_BACKEND_URL?.trim();
const proxiedPaths = ["/env", "/logs", "/outputs", "/platforms", "/run", "/upload"] as const;
const contractSources = [
  resolve(repoRoot, "src/legaldoc_extraction/api/schemas.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/application.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/configuration.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/frontend.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/outputs.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/runs.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/runner.py"),
  resolve(repoRoot, "src/legaldoc_extraction/api/uploads.py"),
  resolve(repoRoot, "src/legaldoc_extraction/config.py"),
  resolve(repoRoot, "scripts/export_openapi.py"),
] as const;

function syncOpenApiContracts() {
  execFileSync("uv", ["run", "python", "scripts/export_openapi.py"], {
    cwd: repoRoot,
    stdio: "inherit",
  });
  execFileSync("pnpm", ["run", "generate:types"], {
    cwd: frontendRoot,
    stdio: "inherit",
  });
}

function openApiContractSyncPlugin(): Plugin {
  let syncTimer: ReturnType<typeof setTimeout> | undefined;

  function scheduleSync() {
    if (syncTimer) {
      clearTimeout(syncTimer);
    }

    syncTimer = setTimeout(() => {
      syncTimer = undefined;
      syncOpenApiContracts();
    }, 150);
  }

  return {
    name: "sync-openapi-contracts",
    apply: "serve",
    configureServer(server) {
      syncOpenApiContracts();
      server.watcher.add(contractSources);
      server.watcher.on("change", (file) => {
        if (contractSources.includes(file as (typeof contractSources)[number])) {
          scheduleSync();
        }
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const runtimeEnv = loadEnv(mode, repoRoot, "");
  const frontendAuthEnv = {
    "import.meta.env.VITE_AUTH_ENABLED": JSON.stringify(
      runtimeEnv.AUTH_ENABLED ?? runtimeEnv.VITE_AUTH_ENABLED ?? "",
    ),
    "import.meta.env.VITE_KEYCLOAK_URL": JSON.stringify(
      runtimeEnv.KEYCLOAK_BASE_URL ?? runtimeEnv.VITE_KEYCLOAK_URL ?? "",
    ),
    "import.meta.env.VITE_KEYCLOAK_REALM": JSON.stringify(
      runtimeEnv.KEYCLOAK_REALM ?? runtimeEnv.VITE_KEYCLOAK_REALM ?? "",
    ),
    "import.meta.env.VITE_KEYCLOAK_CLIENT_ID": JSON.stringify(
      runtimeEnv.KEYCLOAK_CLIENT_ID ?? runtimeEnv.VITE_KEYCLOAK_CLIENT_ID ?? "",
    ),
  };

  return {
    define: frontendAuthEnv,
    plugins: [preact(), openApiContractSyncPlugin()],
    server: {
      host: "localhost",
      port: 5173,
      strictPort: true,
      proxy: backendTarget
        ? Object.fromEntries(proxiedPaths.map((path) => [path, backendTarget]))
        : undefined,
    },
  };
});
