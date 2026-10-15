/** Derives frontend request and view types from the generated API contract. */

import type { components } from "./api-generated";

export type StatusResponse = components["schemas"]["StatusResponse"];
export type UploadResponse = components["schemas"]["UploadResponse"];
export type LogsResponse = components["schemas"]["LogsResponse"];
export type RunSnapshot = components["schemas"]["RunSnapshot"];
export type UploadedFile = components["schemas"]["UploadedFileInfo"];
export type UploadsResponse = components["schemas"]["UploadsResponse"];
export type LogConnection = "connected" | "reconnecting" | "disconnected";
export type EnvResponse = components["schemas"]["EnvResponse"];
export type EnvUpdateRequest = components["schemas"]["EnvUpdateRequest"];
export type RunRequest = components["schemas"]["RunRequest"];
export type PlatformDefaults = components["schemas"]["PlatformDefaultsResponse"];
export type OutputFileSummary = components["schemas"]["OutputFileSummary"];
export type OutputsResponse = components["schemas"]["OutputsResponse"];
export type OutputPreviewResponse = components["schemas"]["OutputPreviewResponse"];
export type OutputSearchMatch = components["schemas"]["OutputSearchMatch"];
export type OutputSearchResponse = components["schemas"]["OutputSearchResponse"];
export type PlatformName = NonNullable<EnvResponse["platform"]>;
export type OutputExportFormat = NonNullable<OutputFileSummary["canonical_format"]>;
export type PreviewRow = OutputPreviewResponse["rows"][number];

export interface EnvFormState {
  platform: PlatformName;
  llm: string;
  embedding_model: string;
  max_fixer_turns: number;
  llm_base_url: string;
  llm_api_key_configured: boolean;
}

export interface RuntimeSettings {
  pagesPerChunk: number;
  maxConcurrency: number;
}
