/** Edits persisted provider and model settings alongside session-scoped processing limits. */

import { CircleAlert, CloudCheck, Plug, RefreshCw, SlidersHorizontal } from "lucide-preact";
import { useEffect, useState } from "preact/hooks";

import type { EnvFormState, PlatformName, RuntimeSettings } from "../api-contracts";
import { PageHeader } from "./PageHeader";

interface ConfigurationTabProps {
  envState: EnvFormState;
  runtime: RuntimeSettings;
  updateEnv: (update: Partial<EnvFormState>) => void;
  setRuntime: (update: Partial<RuntimeSettings>) => void;
  applyPlatformDefaults: (platform: PlatformName) => void;
  saveStatus: "saved" | "saving" | "error";
  saveConnection: (baseUrl: string, apiKey: string) => Promise<void>;
}

const PLATFORMS: { name: PlatformName; logo?: string; invertInDark?: boolean }[] = [
  { name: "Vercel AI Gateway", logo: "/providers/vercel.svg", invertInDark: true },
  { name: "Gemini", logo: "/providers/google.svg" },
  { name: "Custom" },
];

const CHAT_MODELS = [
  "openai/gpt-5.6-luna",
  "deepseek/deepseek-v4-flash-0731",
  "z-ai/glm-5.3-flash",
  "google/gemini-3.8-flash",
];

const MODEL_FIELDS = [
  {
    key: "llm",
    label: "Model",

    placeholder: "Enter a model name",
  },
  {
    key: "embedding_model",
    label: "Embedding model",

    placeholder: "Enter an embedding model",
  },
] as const;

export function ConfigurationTab({
  envState,
  runtime,
  updateEnv,
  setRuntime,
  applyPlatformDefaults,
  saveStatus,
  saveConnection,
}: ConfigurationTabProps) {
  const [baseUrl, setBaseUrl] = useState(envState.llm_base_url);
  const [apiKey, setApiKey] = useState("");
  const [isSavingConnection, setIsSavingConnection] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [connectionSaved, setConnectionSaved] = useState(false);

  useEffect(() => setBaseUrl(envState.llm_base_url), [envState.llm_base_url]);

  /** Submits a complete URL and never repopulates the password field with a stored credential. */
  async function submitConnection() {
    setIsSavingConnection(true);
    setConnectionError("");
    setConnectionSaved(false);
    try {
      await saveConnection(baseUrl, apiKey);
      setApiKey("");
      setConnectionSaved(true);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : "Could not save the connection.");
    } finally {
      setIsSavingConnection(false);
    }
  }

  const ranges = [
    {
      id: "max-concurrency",
      label: "Concurrent tasks",
      description: "Lower this if you hit rate limits.",
      value: runtime.maxConcurrency,
      min: 1,
      max: 5,
      unit: ["task", "tasks"],
      onInput: (value: number) => setRuntime({ maxConcurrency: value }),
    },
    {
      id: "max-fixer-turns",
      label: "Repair turn limit",
      description: "Per agent session, including tool calls.",
      value: envState.max_fixer_turns,
      min: 3,
      max: 30,
      unit: ["turn", "turns"],
      onInput: (value: number) => updateEnv({ max_fixer_turns: value }),
    },
    {
      id: "pages-per-chunk",
      label: "Pages per chunk",
      description: "Pages sent to the vision model per request.",
      value: runtime.pagesPerChunk,
      min: 1,
      max: 5,
      unit: ["page", "pages"],
      onInput: (value: number) => setRuntime({ pagesPerChunk: value }),
    },
  ] as const;

  return (
    <div>
      <PageHeader title="Configuration" icon={SlidersHorizontal}>
        <span
          className={`inline-flex items-center gap-1.5 text-xs ${saveStatus === "error" ? "text-destructive" : "text-muted-foreground"}`}
          role="status"
        >
          {saveStatus === "saved" ? (
            <CloudCheck aria-hidden="true" className="size-3.5" />
          ) : saveStatus === "saving" ? (
            <RefreshCw aria-hidden="true" className="size-3.5 animate-spin" />
          ) : (
            <CircleAlert aria-hidden="true" className="size-3.5" />
          )}
          {saveStatus === "saved"
            ? "Saved"
            : saveStatus === "saving"
              ? "Saving…"
              : "Settings could not be saved"}
        </span>
      </PageHeader>
      <div className="mx-auto w-full max-w-3xl px-4 py-7 sm:px-6 sm:py-9">
        <section aria-labelledby="provider-title">
          <div className="border-b pb-3">
            <h2 className="text-sm font-semibold" id="provider-title">
              Model provider
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Changing provider resets model choices.
            </p>
          </div>
          <div className="grid gap-2 py-5 sm:grid-cols-3" role="group" aria-label="Model provider">
            {PLATFORMS.map((platform) => {
              const selected = envState.platform === platform.name;
              return (
                <button
                  type="button"
                  className={`flex items-center gap-3 rounded-lg border px-3.5 py-3 text-left text-sm transition-colors ${
                    selected
                      ? "border-foreground bg-accent/50 ring-1 ring-foreground"
                      : "hover:bg-accent/50"
                  }`}
                  aria-pressed={selected}
                  onClick={() => applyPlatformDefaults(platform.name)}
                  key={platform.name}
                >
                  {platform.logo ? (
                    <img
                      className={`size-5 shrink-0 ${platform.invertInDark ? "dark:invert" : ""}`}
                      src={platform.logo}
                      alt=""
                      aria-hidden="true"
                      width="20"
                      height="20"
                    />
                  ) : (
                    <Plug aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
                  )}
                  <span className="min-w-0 flex-1 truncate font-medium">{platform.name}</span>
                  <span
                    aria-hidden="true"
                    className={`grid size-4 shrink-0 place-items-center rounded-full border ${selected ? "border-foreground" : "border-input"}`}
                  >
                    {selected && <span className="size-2 rounded-full bg-foreground" />}
                  </span>
                </button>
              );
            })}
          </div>
          {envState.platform === "Custom" && (
            <form
              className="mb-2 space-y-4 rounded-lg border bg-muted/30 p-4"
              onSubmit={(event) => {
                event.preventDefault();
                void submitConnection();
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="grid gap-2" htmlFor="custom-base-url">
                  <span className="field-label">Base URL</span>
                  <input
                    id="custom-base-url"
                    className="field"
                    type="url"
                    value={baseUrl}
                    required
                    placeholder="https://api.openai.com/v1"
                    spellcheck={false}
                    onInput={(event) => {
                      setBaseUrl(event.currentTarget.value);
                      setConnectionSaved(false);
                    }}
                  />
                </label>
                <label className="grid gap-2" htmlFor="custom-api-key">
                  <span className="field-label">API key</span>
                  <input
                    id="custom-api-key"
                    className="field"
                    type="password"
                    value={apiKey}
                    required={!envState.llm_api_key_configured}
                    autoComplete="new-password"
                    placeholder={
                      envState.llm_api_key_configured
                        ? "Saved — leave blank to keep"
                        : "Enter API key"
                    }
                    onInput={(event) => {
                      setApiKey(event.currentTarget.value);
                      setConnectionSaved(false);
                    }}
                  />
                </label>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="submit"
                  className="btn btn-primary btn-sm"
                  disabled={isSavingConnection}
                >
                  {isSavingConnection ? "Saving…" : "Save connection"}
                </button>
                {connectionSaved && (
                  <span
                    className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                    role="status"
                  >
                    <CloudCheck aria-hidden="true" className="size-3.5" />
                    Connection saved
                  </span>
                )}
              </div>
              {connectionError && (
                <p className="text-sm text-destructive" role="alert">
                  {connectionError}
                </p>
              )}
            </form>
          )}
        </section>

        <section className="mt-8" aria-labelledby="models-title">
          <div className="border-b pb-3">
            <h2 className="text-sm font-semibold" id="models-title">
              Models
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              The model reads PDFs, extracts clauses, and runs repairs; it needs image input,
              structured output, and tool calling.
            </p>
          </div>
          <div className="grid gap-x-5 gap-y-4 py-5 sm:grid-cols-2">
            {MODEL_FIELDS.map((field) => (
              <label className="grid gap-2" htmlFor={field.key} key={field.key}>
                <span className="field-label">{field.label}</span>
                <input
                  id={field.key}
                  className="field"
                  type="text"
                  list={field.key === "embedding_model" ? undefined : "chat-models"}
                  value={envState[field.key]}
                  placeholder={field.placeholder}
                  onInput={(event) => updateEnv({ [field.key]: event.currentTarget.value })}
                  autoComplete="off"
                  spellcheck={false}
                />
              </label>
            ))}
          </div>
          <datalist id="chat-models">
            {CHAT_MODELS.filter(
              (model) => envState.platform !== "Gemini" || model.startsWith("google/"),
            ).map((model) => (
              <option
                key={model}
                value={envState.platform === "Vercel AI Gateway" ? model : model.split("/")[1]}
              />
            ))}
          </datalist>
          {envState.platform === "Vercel AI Gateway" && (
            <p className="text-xs text-muted-foreground">
              Use provider/model names, e.g. openai/gpt-5.6-luna.
            </p>
          )}
        </section>

        <section className="mt-8" aria-labelledby="limits-title">
          <div className="border-b pb-3">
            <h2 className="text-sm font-semibold" id="limits-title">
              Processing limits
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Concurrency and chunk size reset on reload.
            </p>
          </div>
          <div className="grid gap-x-6 gap-y-6 py-5 sm:grid-cols-3">
            {ranges.map((range) => (
              <div className="grid content-start gap-2" key={range.id}>
                <div className="flex items-baseline justify-between gap-2">
                  <label className="text-sm font-medium" htmlFor={range.id}>
                    {range.label}
                  </label>
                  <output className="text-sm font-semibold tabular-nums" htmlFor={range.id}>
                    {range.value}
                  </output>
                </div>
                <p className="text-xs leading-5 text-muted-foreground">{range.description}</p>
                <input
                  id={range.id}
                  className="range"
                  type="range"
                  min={range.min}
                  max={range.max}
                  value={range.value}
                  onInput={(event) => range.onInput(Number(event.currentTarget.value))}
                />
                <div className="flex justify-between text-[11px] text-muted-foreground">
                  <span>
                    {range.min} {range.min === 1 ? range.unit[0] : range.unit[1]}
                  </span>
                  <span>
                    {range.max} {range.unit[1]}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
