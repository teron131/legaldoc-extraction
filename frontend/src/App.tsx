/** Owns workspace state, including autosaved settings, the backend run and its log stream, uploads, and results, and hands each tab its slice. */

import { CircleAlert, FileText, LoaderCircle, X } from "lucide-preact";
import { useEffect, useRef, useState } from "preact/hooks";

import {
  getEnv,
  getLogs,
  getOutputs,
  getPlatforms,
  getUploads,
  MOCK_API_ENABLED,
  runPipeline,
  setEnv,
  stopPipeline,
  subscribeToLogs,
  uploadFiles,
} from "./api";
import type {
  EnvFormState,
  EnvResponse,
  EnvUpdateRequest,
  LogConnection,
  LogsResponse,
  OutputFileSummary,
  PlatformDefaults,
  PlatformName,
  RunRequest,
  RunSnapshot,
  RuntimeSettings,
  UploadedFile,
} from "./api-contracts";
import { ConfigurationTab } from "./components/ConfigurationTab";
import { Navigation } from "./components/Navigation";
import { OutputFilesTab } from "./components/OutputFilesTab";
import { RunPipelineTab } from "./components/RunPipelineTab";
import { type AppRoute, getRouteFromPath, getRoutePath } from "./routes";

const DEFAULT_PLATFORM: PlatformName = "Vercel AI Gateway";
const SAVE_DEBOUNCE_MS = 700;
const EMPTY_PLATFORM_DEFAULTS: PlatformDefaults = {
  "Vercel AI Gateway": {
    llm: "",
    embed: "",
  },
  Gemini: {
    llm: "",
    embed: "",
  },
  Custom: {
    llm: "",
    embed: "",
  },
};

function createDefaultEnv(
  platformDefaults: PlatformDefaults,
  platform: PlatformName = DEFAULT_PLATFORM,
): EnvFormState {
  const defaults = platformDefaults[platform];

  return {
    platform,
    llm: defaults.llm,
    embedding_model: defaults.embed,
    max_fixer_turns: 30,
    llm_base_url: "",
    llm_api_key_configured: false,
  };
}

/** Autosave payload; the Custom connection is saved only through saveConnection. */
function normalizeEnvPayload(env: EnvFormState): EnvUpdateRequest {
  return {
    platform: env.platform,
    llm: env.llm.trim(),
    embedding_model: env.embedding_model.trim(),
    max_fixer_turns: env.max_fixer_turns,
  };
}

function envPayloadsEqual(left: EnvFormState | null, right: EnvFormState): boolean {
  if (!left) return false;
  return JSON.stringify(normalizeEnvPayload(left)) === JSON.stringify(normalizeEnvPayload(right));
}

function buildEnvFormState(platformDefaults: PlatformDefaults, env: EnvResponse): EnvFormState {
  const defaultEnv = createDefaultEnv(platformDefaults, env.platform ?? DEFAULT_PLATFORM);
  return {
    ...defaultEnv,
    ...env,
    platform: env.platform ?? defaultEnv.platform,
    llm: env.llm ?? defaultEnv.llm,
    embedding_model: env.embedding_model ?? defaultEnv.embedding_model,
    max_fixer_turns: env.max_fixer_turns ?? defaultEnv.max_fixer_turns,
  };
}

export function App() {
  const [activeTab, setActiveTab] = useState<AppRoute>(() =>
    getRouteFromPath(window.location.pathname),
  );

  const [platformDefaults, setPlatformDefaults] =
    useState<PlatformDefaults>(EMPTY_PLATFORM_DEFAULTS);
  const [envState, setEnvState] = useState<EnvFormState | null>(null);
  const [runtime, setRuntime] = useState<RuntimeSettings>({
    pagesPerChunk: 3,
    maxConcurrency: 3,
  });
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [outputFiles, setOutputFiles] = useState<OutputFileSummary[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [run, setRun] = useState<RunSnapshot>({ run_id: "", status: "idle", result: null });
  const [connection, setConnection] = useState<LogConnection>("connected");
  const isRunning = run.status === "running" || run.status === "stopping";
  const [errorMessage, setErrorMessage] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "error">("saved");

  const eventSourceRef = useRef<{ close: () => void } | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const latestSaveRef = useRef<Promise<void>>(Promise.resolve());
  const lastSavedEnvRef = useRef<EnvFormState | null>(null);
  const saveRevisionRef = useRef(0);

  useEffect(() => {
    void bootstrap();

    function syncRouteFromLocation() {
      const nextRoute = getRouteFromPath(window.location.pathname);
      const nextPath = getRoutePath(nextRoute);
      if (window.location.pathname !== nextPath) {
        window.history.replaceState({}, "", nextPath);
      }
      setActiveTab(nextRoute);
    }

    syncRouteFromLocation();
    window.addEventListener("popstate", syncRouteFromLocation);
    return () => {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      eventSourceRef.current?.close();
      window.removeEventListener("popstate", syncRouteFromLocation);
    };
  }, []);

  function navigateTo(tab: AppRoute) {
    const nextPath = getRoutePath(tab);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
    setActiveTab(tab);
  }

  async function bootstrap() {
    setErrorMessage("");
    try {
      const [platforms, env, initialLogs, uploads] = await Promise.all([
        getPlatforms(),
        getEnv(),
        getLogs(),
        getUploads(),
      ]);
      setPlatformDefaults(platforms);

      const nextEnv = buildEnvFormState(platforms, env);
      setEnvState(nextEnv);
      lastSavedEnvRef.current = nextEnv;
      setLogs(initialLogs.lines);
      setRun(initialLogs.run);
      setUploadedFiles(uploads.files);
      void refreshOutputs();

      if (initialLogs.running) {
        startLogStream(initialLogs);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load state.");
    }
  }

  function updateEnv(update: Partial<EnvFormState>) {
    setEnvState((current) => {
      if (!current) return current;
      const next = { ...current, ...update };
      scheduleEnvSave(next);
      return next;
    });
  }

  function applyPlatformDefaults(platform: PlatformName) {
    const defaults = platformDefaults[platform];
    updateEnv({
      platform,
      llm: defaults.llm,
      embedding_model: defaults.embed,
    });
  }

  /** Saves write-only connection credentials through the same queue that a new run waits for. */
  function saveConnection(baseUrl: string, apiKey: string): Promise<void> {
    const save = latestSaveRef.current.then(async () => {
      await setEnv({
        llm_base_url: baseUrl.trim(),
        ...(apiKey.trim() ? { llm_api_key: apiKey.trim() } : {}),
      });
      const saved = await getEnv();
      setEnvState((current) =>
        current
          ? {
              ...current,
              llm_base_url: saved.llm_base_url ?? "",
              llm_api_key_configured: saved.llm_api_key_configured ?? false,
            }
          : current,
      );
    });
    latestSaveRef.current = save.catch(() => {});
    return save;
  }

  function scheduleEnvSave(nextEnv: EnvFormState) {
    const revision = ++saveRevisionRef.current;
    setSaveStatus("saving");
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      queueEnvSave(nextEnv, revision);
    }, SAVE_DEBOUNCE_MS);
  }

  /** Serializes saves so older requests cannot overwrite the user's latest configuration. */
  function queueEnvSave(nextEnv: EnvFormState, revision: number) {
    latestSaveRef.current = latestSaveRef.current.then(() => persistEnv(nextEnv, revision));
  }

  async function persistEnv(nextEnv: EnvFormState, revision: number) {
    if (envPayloadsEqual(lastSavedEnvRef.current, nextEnv)) {
      if (revision === saveRevisionRef.current) setSaveStatus("saved");
      return;
    }
    try {
      await setEnv(normalizeEnvPayload(nextEnv));
      lastSavedEnvRef.current = nextEnv;
      if (revision === saveRevisionRef.current) setSaveStatus("saved");
    } catch (error) {
      if (revision === saveRevisionRef.current) setSaveStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Failed to save.");
    }
  }

  async function flushEnvSave() {
    if (!envState) return;
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
      queueEnvSave(envState, saveRevisionRef.current);
    }
    await latestSaveRef.current;
    if (!envPayloadsEqual(lastSavedEnvRef.current, envState)) {
      throw new Error(
        "Model settings have not been saved. Check configuration before starting extraction.",
      );
    }
  }

  function startLogStream(snapshot: LogsResponse) {
    eventSourceRef.current?.close();
    const stream = subscribeToLogs({
      cursor: snapshot.cursor,
      runId: snapshot.run.run_id ?? "",
      onDone: (state) => {
        stream.close();
        if (eventSourceRef.current === stream) eventSourceRef.current = null;
        setRun(state);
        setConnection("connected");
        setIsSubmitting(false);
        void refreshOutputs();
        void refreshUploads();
      },
      onConnectionChange: setConnection,
      onReset: () => void refreshRun(),
      onMessage: (line) => {
        setLogs((current) => [...current, line]);
      },
    });
    eventSourceRef.current = stream;
  }

  /** Reconcile with the backend without mistaking a lost connection for a finished job. */
  async function refreshRun() {
    try {
      const snapshot = await getLogs();
      setLogs(snapshot.lines);
      setRun(snapshot.run);
      setIsSubmitting(false);
      setConnection("connected");
      if (snapshot.running) startLogStream(snapshot);
      else {
        eventSourceRef.current?.close();
        eventSourceRef.current = null;
        void refreshOutputs();
        void refreshUploads();
      }
    } catch {
      setConnection("disconnected");
    }
  }

  async function refreshUploads() {
    try {
      setUploadedFiles((await getUploads()).files);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not load uploaded PDFs.");
    }
  }

  async function handleUpload(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    setIsUploading(true);
    setErrorMessage("");

    try {
      await uploadFiles(files);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      await refreshUploads();
      setIsUploading(false);
    }
  }

  async function handleStop() {
    setErrorMessage("");
    try {
      await stopPipeline(run.run_id);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not stop extraction.");
    } finally {
      await refreshRun();
    }
  }

  async function handleRun() {
    if (!envState) return;
    setErrorMessage("");
    setIsSubmitting(true);

    try {
      await flushEnvSave();
      if (envState.platform === "Custom") {
        if (!envState.llm_base_url || !envState.llm_api_key_configured) {
          throw new Error("Save the Custom base URL and API key in Configuration before starting.");
        }
        if (!envState.llm.trim() || !envState.embedding_model.trim()) {
          throw new Error(
            "Set a primary model and an embedding model for Custom in Configuration.",
          );
        }
      }
      const payload: RunRequest = {
        max_concurrency: runtime.maxConcurrency,
        max_fixer_turns: envState.max_fixer_turns,
        pages_per_chunk: runtime.pagesPerChunk,
        platform: envState.platform,
      };

      try {
        await runPipeline(payload);
        setLogs([]);
        setRun({ run_id: "", status: "running", result: null });
        await refreshRun();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed";
        if (message.includes("409") || message.includes("busy")) {
          await refreshRun();
        } else {
          throw error;
        }
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Run failed to start.");
      setIsSubmitting(false);
    }
  }

  async function refreshOutputs() {
    try {
      setOutputFiles((await getOutputs()).files);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load outputs.");
    }
  }

  if (!envState) {
    return (
      <main className="grid min-h-dvh place-items-center bg-background px-6">
        <div className="flex max-w-sm flex-col items-center gap-3 text-center">
          <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground">
            <FileText aria-hidden="true" className="size-5" />
          </span>
          <h1 className="text-base font-semibold">
            {errorMessage ? "Workspace unavailable" : "Opening your workspace…"}
          </h1>
          {errorMessage ? (
            <>
              <p className="text-sm text-muted-foreground" role="alert">
                {errorMessage}
              </p>
              <button type="button" className="btn btn-primary" onClick={() => void bootstrap()}>
                Try again
              </button>
            </>
          ) : (
            <LoaderCircle
              aria-hidden="true"
              className="size-4 animate-spin text-muted-foreground"
            />
          )}
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-dvh bg-background">
      <Navigation
        activeTab={activeTab}
        onTabChange={navigateTo}
        isRunning={isRunning}
        resultCount={outputFiles.length}
        isPreview={MOCK_API_ENABLED}
      />

      <main className="min-w-0 md:pl-[var(--sidebar-width)]" id="main-content">
        {errorMessage && (
          <div
            className="fixed inset-x-4 bottom-4 z-50 flex items-start gap-3 rounded-xl border border-destructive/30 bg-card px-4 py-3 text-sm text-destructive shadow-lg sm:left-auto sm:right-6 sm:max-w-md"
            role="alert"
          >
            <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            <p className="min-w-0 flex-1">{errorMessage}</p>
            <button
              type="button"
              className="btn btn-ghost btn-icon -my-1.5 -mr-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
              aria-label="Dismiss error"
              onClick={() => setErrorMessage("")}
            >
              <X aria-hidden="true" />
            </button>
          </div>
        )}
        {activeTab === "settings" && (
          <ConfigurationTab
            envState={envState}
            runtime={runtime}
            updateEnv={updateEnv}
            setRuntime={(update) => setRuntime((cur) => ({ ...cur, ...update }))}
            applyPlatformDefaults={applyPlatformDefaults}
            saveStatus={saveStatus}
            saveConnection={saveConnection}
          />
        )}

        {activeTab === "pipeline" && (
          <RunPipelineTab
            logs={logs}
            isRunning={isRunning}
            run={run}
            connection={connection}
            onReconnect={() => void refreshRun()}
            isSubmitting={isSubmitting}
            isUploading={isUploading}
            uploadedFiles={uploadedFiles}
            handleUpload={handleUpload}
            handleRun={handleRun}
            handleStop={handleStop}
            onViewResults={() => navigateTo("explorer")}
            onConfigure={() => navigateTo("settings")}
            model={envState.llm}
          />
        )}

        {activeTab === "explorer" && (
          <OutputFilesTab
            files={outputFiles}
            onRefresh={refreshOutputs}
            onNewExtraction={() => navigateTo("pipeline")}
            onRetryStarted={() => {
              setRun({ run_id: "", status: "running", result: null });
              void refreshRun();
              navigateTo("pipeline");
            }}
          />
        )}
      </main>
    </div>
  );
}
