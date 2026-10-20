/** Guides PDF ingestion and exposes actual batch activity without inventing per-document progress. */

import {
  BrainCircuit,
  CircleAlert,
  CircleCheck,
  Download,
  FileText,
  FileUp,
  LoaderCircle,
  Play,
  ScanText,
  SlidersHorizontal,
  Square,
  SquareTerminal,
  WifiOff,
} from "lucide-preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import type { LogConnection, RunSnapshot, UploadedFile } from "../api-contracts";
import { PageHeader } from "./PageHeader";
import { SearchField } from "./SearchField";

interface RunPipelineTabProps {
  logs: string[];
  isRunning: boolean;
  run: RunSnapshot;
  connection: LogConnection;
  onReconnect: () => void;
  isSubmitting: boolean;
  isUploading: boolean;
  uploadedFiles: UploadedFile[];
  handleUpload: (files: FileList | null) => Promise<void>;
  handleRun: () => Promise<void>;
  handleStop: () => Promise<void>;
  onViewResults: () => void;
  onConfigure: () => void;
  model: string;
}

function runNotice(run: RunSnapshot, isRunning: boolean) {
  if (run.status === "stopping")
    return {
      title: "Stopping extraction…",
      description: "Waiting for active work to finish. You can refresh this page.",
    };
  if (run.status === "stopped")
    return {
      title: "Extraction stopped",
      description: "Completed results are kept. Start again to process remaining PDFs.",
    };
  if (isRunning)
    return {
      title: "Extraction running",
      description: "You can refresh or leave this page; processing continues.",
    };
  const failed = run.status === "failed" || run.status === "partial";
  const title =
    run.status === "partial"
      ? "Some PDFs need attention"
      : failed
        ? "Extraction failed"
        : "Extraction complete";
  const result = run.result;
  if (result)
    return {
      title,
      description: `${result.succeeded ?? 0} processed, ${result.failed ?? 0} failed${result.skipped ? `, ${result.skipped} already had results` : ""}.`,
    };
  return {
    title,
    description: failed
      ? "Review the errors below, check your settings, and retry."
      : "Your results are ready to review.",
  };
}

/**
 * Parses FILE_LOG_FORMAT lines from logging_utils.py and rejoins messages that themselves contain " - ".
 * `2026-05-01 09:30:12,345 - legaldoc_extraction.pipeline - INFO - run_batch_pipeline:325 - [MAIN] Loader - Input: 10` → `{ timestamp: "09:30:12", level: "INFO", message: "[MAIN] Loader - Input: 10" }`
 */
function parseLog(line: string) {
  const parts = line.split(" - ");
  const level = parts.find((part) => /^(INFO|WARNING|ERROR|DEBUG)$/.test(part)) ?? "INFO";
  const timestamp = line.match(/^\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})/)?.[1] ?? "—";
  const message =
    parts.length >= 5
      ? parts.slice(4).join(" - ")
      : parts.length >= 3
        ? parts.slice(2).join(" - ")
        : line;
  return { timestamp, level, message };
}

/** Reads the last `[MAIN] Total - …` line that the batch pipeline and result retry log, so that log wording is a UI contract. */
function tokenUsage(lines: string[]) {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const match = lines[index].match(/Total - Input:\s*([\d,]+), Output:\s*([\d,]+)/);
    if (match)
      return {
        input: Number(match[1].replace(/,/g, "")),
        output: Number(match[2].replace(/,/g, "")),
      };
  }
  return null;
}

export function RunPipelineTab({
  logs,
  isRunning,
  run,
  connection,
  onReconnect,
  isSubmitting,
  isUploading,
  uploadedFiles,
  handleUpload,
  handleRun,
  handleStop,
  onViewResults,
  onConfigure,
  model,
}: RunPipelineTabProps) {
  const [dragActive, setDragActive] = useState(false);
  const [logQuery, setLogQuery] = useState("");
  const [logFilter, setLogFilter] = useState("all");
  const [followLogs, setFollowLogs] = useState(true);
  const [uploadError, setUploadError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const logViewportRef = useRef<HTMLDivElement>(null);
  const busy = isRunning || isSubmitting;
  const failed = run.status === "failed" || run.status === "partial";
  const notice = runNotice(run, isRunning);
  const stopping = run.status === "stopping";
  const waiting = stopping || isSubmitting;
  let actionLabel = failed ? "Retry extraction" : "Start extraction";
  if (isRunning) actionLabel = "Stop extraction";
  else if (isSubmitting) actionLabel = "Starting extraction…";
  if (stopping) actionLabel = "Stopping…";
  const documents = useMemo(() => {
    const uniqueFiles = new Map<string, UploadedFile>();
    for (const file of uploadedFiles) {
      if (!uniqueFiles.has(file.name.toLowerCase())) uniqueFiles.set(file.name.toLowerCase(), file);
    }
    return [...uniqueFiles.values()];
  }, [uploadedFiles]);
  const entries = useMemo(() => logs.map(parseLog), [logs]);
  const filteredEntries = entries.filter(
    (entry) =>
      (logFilter === "all" || entry.level === "WARNING" || entry.level === "ERROR") &&
      entry.message.toLowerCase().includes(logQuery.toLowerCase()),
  );
  const usage = useMemo(() => tokenUsage(logs), [logs]);
  const issueCount = entries.filter(
    (entry) => entry.level === "WARNING" || entry.level === "ERROR",
  ).length;

  useEffect(() => {
    if (followLogs && logViewportRef.current)
      logViewportRef.current.scrollTop = logViewportRef.current.scrollHeight;
  }, [logs, followLogs, logQuery, logFilter]);

  useEffect(() => {
    if (run.run_id) {
      setLogQuery("");
      setLogFilter("all");
    }
  }, [run.run_id]);

  /** Rejects non-PDF drops explicitly and allows choosing the same file again after an upload. */
  async function ingest(files: FileList | null) {
    if (!files?.length || isUploading || busy) return;
    setUploadError("");
    if (Array.from(files).some((file) => !file.name.toLowerCase().endsWith(".pdf"))) {
      setUploadError("Choose PDF files only. No files from this selection were uploaded.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    await handleUpload(files);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function downloadLogs() {
    const url = URL.createObjectURL(new Blob([logs.join("\n")], { type: "text/plain" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "legaldoc-extraction-log.txt";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const noticeTone = failed
    ? "border-destructive/30 bg-destructive/5"
    : run.status === "completed"
      ? "border-success/30 bg-success/5"
      : "bg-muted/40";

  return (
    <div>
      <PageHeader title="Extraction" icon={ScanText}>
        <button type="button" className="btn btn-outline btn-sm" onClick={onViewResults}>
          View results
        </button>
      </PageHeader>
      <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6 sm:px-6">
        {connection !== "connected" ? (
          <div
            className="flex flex-wrap items-center gap-3 rounded-xl border border-warning/40 bg-warning/5 px-4 py-3"
            role="status"
          >
            <WifiOff aria-hidden="true" className="size-4 shrink-0 text-warning" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                {connection === "reconnecting" ? "Reconnecting…" : "Connection lost"}
              </p>
              <p className="text-sm text-muted-foreground">
                The extraction may still be running. Reconnect to check its status.
              </p>
            </div>
            <button type="button" className="btn btn-outline btn-sm" onClick={onReconnect}>
              Reconnect
            </button>
          </div>
        ) : (
          run.status !== "idle" && (
            <div
              className={`flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 ${noticeTone}`}
              role={failed ? "alert" : "status"}
            >
              {isRunning ? (
                <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
              ) : failed ? (
                <CircleAlert aria-hidden="true" className="size-4 shrink-0 text-destructive" />
              ) : (
                <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-success" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{notice.title}</p>
                <p className="text-sm text-muted-foreground">{notice.description}</p>
              </div>
              {failed && (
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => setLogFilter("issues")}
                >
                  Show errors
                </button>
              )}
            </div>
          )
        )}
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
          <section className="panel" aria-labelledby="source-heading">
            <div className="panel-header">
              <h2 id="source-heading" className="panel-title">
                Documents
              </h2>
              {documents.length > 0 && <span className="count">{documents.length}</span>}
            </div>
            <div className="space-y-4 p-4">
              <button
                type="button"
                disabled={isUploading || busy}
                className={`flex w-full flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-7 text-center transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  dragActive
                    ? "border-foreground bg-accent"
                    : "border-input hover:border-foreground/40 hover:bg-accent/50"
                }`}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!busy && !isUploading) setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragActive(false);
                  void ingest(event.dataTransfer?.files ?? null);
                }}
              >
                {isUploading ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="size-5 animate-spin text-muted-foreground"
                  />
                ) : (
                  <FileUp aria-hidden="true" className="size-5 text-muted-foreground" />
                )}
                <span className="text-sm font-medium">
                  {isUploading
                    ? "Uploading documents…"
                    : busy
                      ? "Extraction in progress"
                      : "Drop PDFs or browse files"}
                </span>
                <span className="text-xs text-muted-foreground">PDF only, up to 50 MB each</span>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf"
                multiple
                className="hidden"
                aria-label="Upload PDF documents"
                onChange={(event) => void ingest(event.currentTarget.files)}
              />
              {uploadError && (
                <p className="text-sm text-destructive" role="alert">
                  {uploadError}
                </p>
              )}
              <ul
                className="divide-y rounded-lg border"
                hidden={!documents.length}
                aria-label="Uploaded PDFs"
              >
                {documents.map((file) => (
                  <li className="flex items-center gap-3 px-3 py-2.5" key={file.id}>
                    <FileText
                      aria-hidden="true"
                      className="size-4 shrink-0 text-muted-foreground"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium" title={file.name}>
                        {file.name}
                      </span>
                      <span className="block font-mono text-xs text-muted-foreground">
                        {file.size < 1024 * 1024
                          ? `${Math.max(1, Math.round(file.size / 1024))} KB`
                          : `${(file.size / 1024 / 1024).toFixed(1)} MB`}
                      </span>
                    </span>
                    <CircleCheck aria-label="Uploaded" className="size-4 shrink-0 text-success" />
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-3 rounded-lg bg-muted/60 px-3 py-2.5">
                <BrainCircuit
                  aria-hidden="true"
                  className="size-4 shrink-0 text-muted-foreground"
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs text-muted-foreground">Extraction model</span>
                  <span className="block truncate text-sm font-medium" title={model}>
                    {model || "Not configured"}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={onConfigure}
                  className="btn btn-ghost btn-icon"
                  aria-label="Configure extraction model"
                  title="Configure extraction model"
                >
                  <SlidersHorizontal aria-hidden="true" />
                </button>
              </div>
              <div className="space-y-2">
                <button
                  type="button"
                  className={`btn btn-lg w-full ${isRunning ? "btn-outline" : "btn-primary"}`}
                  disabled={
                    waiting ||
                    (!isRunning && (isUploading || !documents.length || connection !== "connected"))
                  }
                  onClick={() => void (isRunning ? handleStop() : handleRun())}
                >
                  {waiting ? (
                    <LoaderCircle aria-hidden="true" className="animate-spin" />
                  ) : isRunning ? (
                    <Square aria-hidden="true" />
                  ) : (
                    <Play aria-hidden="true" />
                  )}
                  {actionLabel}
                </button>
                <p className="text-center text-xs text-muted-foreground">
                  {documents.length
                    ? "Only new or failed PDFs are processed."
                    : "Upload a PDF to start."}
                </p>
              </div>
            </div>
          </section>
          <section className="panel flex min-h-[32rem] flex-col" aria-labelledby="activity-heading">
            <div className="panel-header">
              <h2 id="activity-heading" className="panel-title">
                <SquareTerminal aria-hidden="true" className="size-4 text-muted-foreground" />
                Activity
                {logs.length > 0 && <span className="count">{logs.length}</span>}
              </h2>
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                onClick={downloadLogs}
                hidden={!logs.length}
                aria-label="Download activity log"
                title="Download activity log"
              >
                <Download aria-hidden="true" />
              </button>
            </div>
            <div
              className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5"
              hidden={!logs.length}
            >
              <div className="segmented" aria-label="Activity filter">
                <button
                  type="button"
                  aria-pressed={logFilter === "all"}
                  onClick={() => setLogFilter("all")}
                >
                  All activity
                </button>
                <button
                  type="button"
                  aria-pressed={logFilter === "issues"}
                  onClick={() => setLogFilter("issues")}
                >
                  Warnings & errors <span className="tabular-nums">{issueCount}</span>
                </button>
              </div>
              <SearchField
                compact
                className="w-full sm:w-56"
                type="search"
                aria-label="Search activity"
                placeholder="Filter activity…"
                value={logQuery}
                onInput={(event) => setLogQuery(event.currentTarget.value)}
              />
            </div>
            <div
              className="thin-scrollbar max-h-[34rem] min-h-0 flex-1 overflow-y-auto py-2"
              ref={logViewportRef}
            >
              {filteredEntries.length === 0 ? (
                <div className="grid h-full min-h-48 place-items-center px-4 text-center">
                  <p className="text-sm text-muted-foreground">
                    {logs.length
                      ? "No matching activity"
                      : busy
                        ? "Waiting for the first update"
                        : "No activity yet. Start an extraction to see each stage here."}
                  </p>
                </div>
              ) : (
                filteredEntries.map((entry, index) => {
                  const issue = entry.level === "WARNING" || entry.level === "ERROR";
                  return (
                    <div
                      className={`grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 px-4 py-1 font-mono text-xs leading-5 ${
                        entry.level === "ERROR"
                          ? "bg-destructive/5 text-destructive"
                          : entry.level === "WARNING"
                            ? "bg-warning/5"
                            : ""
                      }`}
                      key={index}
                    >
                      <time className="tabular-nums text-muted-foreground">
                        {entry.timestamp === "—" ? "" : entry.timestamp}
                      </time>
                      <p className="whitespace-pre-wrap break-words">
                        {issue && (
                          <span
                            className={`mr-2 font-medium ${entry.level === "ERROR" ? "text-destructive" : "text-warning"}`}
                          >
                            {entry.level === "ERROR" ? "Error" : "Warning"}
                          </span>
                        )}
                        {entry.message}
                      </p>
                    </div>
                  );
                })
              )}
            </div>
            <div
              className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2.5 text-xs text-muted-foreground"
              hidden={!logs.length}
            >
              <label className="inline-flex items-center gap-2">
                <input
                  type="checkbox"
                  className="check"
                  checked={followLogs}
                  onChange={(event) => setFollowLogs(event.currentTarget.checked)}
                />
                Follow latest
              </label>
              <span className="tabular-nums">
                {usage
                  ? `${usage.input.toLocaleString()} input / ${usage.output.toLocaleString()} output tokens`
                  : null}
              </span>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
