/** Boots the workspace after resolving runtime authentication and keeps sign-in feedback on the shared surface. */

import Keycloak from "keycloak-js";
import { render } from "preact";

import { setAccessTokenProvider } from "./api";
import { App } from "./App";

import "./styles.css";

type FrontendRuntimeConfig = {
  authEnabled?: string;
  keycloakClientId?: string;
  keycloakRealm?: string;
  keycloakUrl?: string;
};

declare global {
  interface Window {
    __APP_CONFIG__?: FrontendRuntimeConfig;
  }
}

const rootElement = document.getElementById("app");
const runtimeConfig = window.__APP_CONFIG__ ?? {};
const authEnabled = !["0", "false", "no", "off"].includes(
  (runtimeConfig.authEnabled ?? import.meta.env.VITE_AUTH_ENABLED ?? "").trim().toLowerCase(),
);
const keycloakUrl = (runtimeConfig.keycloakUrl ?? import.meta.env.VITE_KEYCLOAK_URL ?? "").trim();
const keycloakRealm = (
  runtimeConfig.keycloakRealm ??
  import.meta.env.VITE_KEYCLOAK_REALM ??
  ""
).trim();
const keycloakClientId = (
  runtimeConfig.keycloakClientId ??
  import.meta.env.VITE_KEYCLOAK_CLIENT_ID ??
  ""
).trim();

if (!rootElement) {
  throw new Error("App root not found");
}

const root = rootElement;

function renderStatus(message: string): void {
  render(
    <div
      className="grid min-h-dvh place-items-center bg-background px-6 text-sm text-muted-foreground"
      role="status"
    >
      {message}
    </div>,
    root,
  );
}

async function bootstrap(): Promise<void> {
  if (!authEnabled) {
    render(<App />, root);
    return;
  }

  if (!keycloakUrl || !keycloakRealm || !keycloakClientId) {
    renderStatus("Authentication is enabled but Keycloak is not configured.");
    return;
  }

  renderStatus("Signing you in...");

  const keycloak = new Keycloak({
    url: keycloakUrl,
    realm: keycloakRealm,
    clientId: keycloakClientId,
  });

  try {
    const authenticated = await keycloak.init({
      onLoad: "login-required",
      pkceMethod: "S256",
      checkLoginIframe: false,
      scope: "openid",
    });

    if (!authenticated) {
      await keycloak.login();
      return;
    }

    setAccessTokenProvider(async () => {
      try {
        await keycloak.updateToken(30);
      } catch {
        await keycloak.login();
        return null;
      }
      return keycloak.token ?? null;
    });

    keycloak.onTokenExpired = () => {
      void keycloak.updateToken(30).catch(() => keycloak.login());
    };

    render(<App />, root);
  } catch (error) {
    console.error("Failed to initialize Keycloak", error);
    renderStatus("Authentication failed. Check Keycloak configuration.");
  }
}

void bootstrap();
