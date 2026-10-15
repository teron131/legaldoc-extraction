/** Connects the UI to backend endpoints and the live log stream. */

import createClient from "openapi-fetch";

import type {
  EnvFormState,
  EnvResponse,
  EnvUpdateRequest,
  LogConnection,
  LogsResponse,
  OutputExportFormat,
  OutputPreviewResponse,
  OutputSearchResponse,
  OutputsResponse,
  PlatformDefaults,
  RunRequest,
  RunSnapshot,
  StatusResponse,
  UploadedFile,
  UploadResponse,
  UploadsResponse,
} from "./api-contracts";
import type { paths } from "./api-generated";

const DEV_BACKEND_URL = import.meta.env.VITE_BACKEND_URL?.trim();
export const MOCK_API_ENABLED = import.meta.env.DEV && !DEV_BACKEND_URL;
type AccessTokenProvider = () => Promise<string | null>;

let accessTokenProvider: AccessTokenProvider | null = null;

async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(
    init?.headers ?? (input instanceof Request ? input.headers : undefined),
  );
  if (accessTokenProvider) {
    const token = await accessTokenProvider();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }
  return fetch(input, { ...init, headers });
}

const apiClient = createClient<paths>({
  baseUrl: DEV_BACKEND_URL || undefined,
  fetch: authenticatedFetch,
});

const MOCK_PLATFORM_DEFAULTS: PlatformDefaults = {
  "Vercel AI Gateway": {
    llm: "openai/gpt-5.6-luna",
    embed: "google/gemini-embedding-2",
  },
  Gemini: {
    llm: "gemini-3.8-flash",
    embed: "gemini-embedding-2",
  },
  Custom: {
    llm: "",
    embed: "",
  },
};

const MOCK_PIPELINE_LINES = [
  "[MAIN] Frontend mock mode enabled.",
  "[LOADER] Queued uploaded PDFs for ingestion.",
  "[EXTRACTOR] Synthesizing placeholder extraction progress.",
  "[FIXER] Simulating post-processing review.",
  "[MAIN] Mock pipeline finished.",
] as const;

type LogStreamHandlers = {
  onDone: (state: RunSnapshot) => void;
  onConnectionChange: (state: LogConnection) => void;
  onReset: () => void;
  onMessage: (line: string) => void;
  cursor: number;
  runId: string;
};

type LogSubscriber = {
  onDone: (state: RunSnapshot) => void;
  onMessage: (line: string) => void;
};

type ApiResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

let mockEnv: EnvFormState = {
  platform: "Vercel AI Gateway",
  llm: MOCK_PLATFORM_DEFAULTS["Vercel AI Gateway"].llm,
  embedding_model: MOCK_PLATFORM_DEFAULTS["Vercel AI Gateway"].embed,
  max_fixer_turns: 30,
  llm_base_url: "",
  llm_api_key_configured: false,
};
let mockLogs: string[] = [];
let mockRunTimer: number | null = null;
let mockRunning = false;
let mockRun: RunSnapshot = { run_id: "", status: "idle", result: null };
let mockUploads: UploadedFile[] = [];
const mockSubscribers = new Set<LogSubscriber>();

export interface LogStreamHandle {
  close: () => void;
}

export function setAccessTokenProvider(provider: AccessTokenProvider | null): void {
  accessTokenProvider = provider;
}

function resolveApiPath(path: string): string {
  if (!DEV_BACKEND_URL) {
    return path;
  }
  return new URL(path, DEV_BACKEND_URL).toString();
}
async function requireData<T>(request: Promise<ApiResult<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (data !== undefined) {
    return data;
  }

  const detail =
    typeof error === "object" && error !== null && "detail" in error ? error.detail : null;
  const errorMessage =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail
            .map((issue) => (typeof issue?.msg === "string" ? issue.msg : ""))
            .filter(Boolean)
            .join("; ")
        : "";

  throw new Error(errorMessage || `Request failed with status ${response.status}`);
}

function formatMockLog(message: string): string {
  const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  return `${timestamp},000 - INFO - frontend - mock - ${message}`;
}

function emitMockLine(message: string): void {
  const line = formatMockLog(message);
  mockLogs = [...mockLogs, line];
  mockSubscribers.forEach((subscriber) => {
    subscriber.onMessage(line);
  });
}

function finishMockRun(): void {
  mockRunning = false;
  mockRunTimer = null;
  mockRun = { ...mockRun, status: "completed" };
  mockSubscribers.forEach((subscriber) => {
    subscriber.onDone(mockRun);
  });
}

function scheduleMockLine(index: number): void {
  if (index >= MOCK_PIPELINE_LINES.length) {
    finishMockRun();
    return;
  }

  mockRunTimer = window.setTimeout(() => {
    emitMockLine(MOCK_PIPELINE_LINES[index]);
    scheduleMockLine(index + 1);
  }, 650);
}

function startMockRun(): void {
  if (mockRunTimer) {
    window.clearTimeout(mockRunTimer);
  }
  mockLogs = [];
  mockRunning = true;
  mockRun = { run_id: crypto.randomUUID(), status: "running", result: null };
  scheduleMockLine(0);
}

async function uploadFormData(formData: FormData): Promise<UploadResponse> {
  const response = await authenticatedFetch(resolveApiPath("/upload"), {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const message = typeof error?.detail === "string" ? error.detail : "Upload failed.";
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as UploadResponse;
}

export function getPlatforms(): Promise<PlatformDefaults> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve(MOCK_PLATFORM_DEFAULTS);
  }
  return requireData(apiClient.GET("/platforms"));
}

export function getEnv(): Promise<EnvResponse> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve({ ...mockEnv });
  }
  return requireData(apiClient.GET("/env"));
}

export function setEnv(payload: EnvUpdateRequest): Promise<StatusResponse> {
  if (MOCK_API_ENABLED) {
    const { llm_api_key, ...settings } = payload;
    mockEnv = {
      ...mockEnv,
      ...settings,
      llm_api_key_configured: Boolean(llm_api_key?.trim()) || mockEnv.llm_api_key_configured,
    };
    return Promise.resolve({ status: "ok" });
  }
  return requireData(
    apiClient.POST("/env", {
      body: payload,
    }),
  );
}

export function getLogs(): Promise<LogsResponse> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve({
      lines: [...mockLogs],
      running: mockRunning,
      cursor: mockLogs.length,
      run: mockRun,
    });
  }
  return requireData(apiClient.GET("/logs"));
}

export function stopPipeline(runId: string): Promise<RunSnapshot> {
  if (MOCK_API_ENABLED) {
    if (runId !== mockRun.run_id) return Promise.reject(new Error("This run is no longer current"));
    if (mockRunning) {
      if (mockRunTimer !== null) window.clearTimeout(mockRunTimer);
      mockRunTimer = null;
      mockRunning = false;
      mockRun = { ...mockRun, status: "stopped" };
      emitMockLine("[MAIN] Extraction stopped");
      mockSubscribers.forEach((subscriber) => subscriber.onDone(mockRun));
    }
    return Promise.resolve(mockRun);
  }
  return requireData(apiClient.POST("/run/{run_id}/stop", { params: { path: { run_id: runId } } }));
}

export function runPipeline(payload: RunRequest): Promise<StatusResponse> {
  if (MOCK_API_ENABLED) {
    mockEnv = {
      ...mockEnv,
      ...payload,
    };

    if (mockRunning) {
      return Promise.reject(new Error("busy"));
    }

    startMockRun();
    return Promise.resolve({ status: "started" });
  }
  return requireData(
    apiClient.POST("/run", {
      body: payload,
    }),
  );
}

export function uploadFiles(files: File[]): Promise<UploadResponse> {
  if (MOCK_API_ENABLED) {
    const accepted = files.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
    const uploaded = new Map(mockUploads.map((file) => [file.id, file]));
    accepted.forEach((file) =>
      uploaded.set(file.name, { id: file.name, name: file.name, size: file.size }),
    );
    mockUploads = [...uploaded.values()];
    const saved = accepted.length;
    return Promise.resolve({ saved });
  }

  const formData = new FormData();
  files.forEach((file) => {
    formData.append("files", file);
  });
  return uploadFormData(formData);
}

export function getUploads(): Promise<UploadsResponse> {
  if (MOCK_API_ENABLED) return Promise.resolve({ files: mockUploads });
  return requireData(apiClient.GET("/upload"));
}

export function subscribeToLogs({
  onDone,
  onConnectionChange,
  onReset,
  onMessage,
  cursor,
  runId,
}: LogStreamHandlers): LogStreamHandle {
  if (MOCK_API_ENABLED) {
    onConnectionChange("connected");
    const subscriber: LogSubscriber = { onDone, onMessage };
    mockSubscribers.add(subscriber);
    return {
      close: () => {
        mockSubscribers.delete(subscriber);
      },
    };
  }

  let stream: EventSource | null = null;
  let isClosed = false;

  void (async () => {
    try {
      const streamUrl = new URL(resolveApiPath("/logs/stream"), window.location.origin);
      streamUrl.searchParams.set("after", String(cursor));
      streamUrl.searchParams.set("run_id", runId);
      if (accessTokenProvider) {
        const token = await accessTokenProvider();
        if (token) {
          streamUrl.searchParams.set("access_token", token);
        }
      }
      if (isClosed) {
        return;
      }

      stream = new EventSource(streamUrl.toString());
      stream.onopen = () => onConnectionChange("connected");
      stream.onmessage = (event) => {
        onMessage(event.data);
      };

      stream.addEventListener("done", (event) => {
        stream?.close();
        onDone(JSON.parse((event as MessageEvent).data) as RunSnapshot);
      });
      stream.addEventListener("reset", () => {
        stream?.close();
        onReset();
      });

      stream.onerror = () => {
        onConnectionChange(
          stream?.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting",
        );
      };
    } catch {
      onConnectionChange("disconnected");
    }
  })();

  return {
    close: () => {
      isClosed = true;
      stream?.close();
    },
  };
}

export function getOutputs(): Promise<OutputsResponse> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve({ files: [] });
  }
  return requireData(apiClient.GET("/outputs"));
}

/** Downloads through the authenticated API and keeps credentials out of navigation URLs. */
export async function downloadOutputs(
  outputIds: string[],
  format: OutputExportFormat,
): Promise<void> {
  const params = new URLSearchParams({ format });
  outputIds.forEach((id) => params.append("id", id));
  const response = await authenticatedFetch(resolveApiPath(`/outputs/download?${params}`));
  if (!response.ok) {
    await requireData(
      Promise.resolve({ error: await response.json().catch(() => null), response }),
    );
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download =
    response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ??
    `outputs.${outputIds.length === 1 ? format : "zip"}`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function deleteOutput(outputId: string): Promise<StatusResponse> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve({ status: "ok" });
  }
  return requireData(
    apiClient.DELETE("/outputs", {
      params: {
        query: { id: outputId },
      },
    }),
  );
}

export function retryOutput(outputId: string): Promise<StatusResponse> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve({ status: "started" });
  }
  return requireData(
    apiClient.POST("/outputs/retry", {
      params: {
        query: { id: outputId },
      },
    }),
  );
}

export function getOutputPreview(outputId: string): Promise<OutputPreviewResponse> {
  if (MOCK_API_ENABLED) {
    return Promise.resolve({ id: outputId, name: "", row_count: 0, rows: [] });
  }
  return requireData(
    apiClient.GET("/outputs/preview", {
      params: {
        query: { id: outputId },
      },
    }),
  );
}

export function searchOutputs(query: string): Promise<OutputSearchResponse> {
  if (MOCK_API_ENABLED || !query.trim()) {
    return Promise.resolve({ matches: [] });
  }
  return requireData(
    apiClient.GET("/outputs/search", {
      params: {
        query: { q: query },
      },
    }),
  );
}
