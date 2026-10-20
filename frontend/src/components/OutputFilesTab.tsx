/** Reviews extracted clauses with synchronized search, selection, previews, and export actions. */

import {
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  FileText,
  Library,
  LoaderCircle,
  type LucideIcon,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
} from "lucide-preact";
import { type ComponentChildren, Fragment } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import {
  deleteOutput,
  downloadOutputs,
  getOutputPreview,
  retryOutput,
  searchOutputs,
} from "../api";
import type {
  OutputExportFormat,
  OutputFileSummary,
  OutputSearchMatch,
  PreviewRow,
} from "../api-contracts";
import { PageHeader } from "./PageHeader";
import { SearchField } from "./SearchField";

type OutputFilesTabProps = {
  files: OutputFileSummary[];
  onRefresh: () => Promise<void>;
  onNewExtraction: () => void;
  onRetryStarted: () => void;
};

const DOWNLOAD_FORMATS = [
  {
    label: "JSON",
    value: "json",
    description: "Download structured JSON",
  },
  {
    label: "CSV",
    value: "csv",
    description: "Download a CSV spreadsheet",
  },
  {
    label: "Excel",
    value: "xlsx",
    description: "Download an Excel workbook",
  },
] as const;

const PREVIEW_FIELDS = [
  "date",
  "source_document",
  "clause_header",
  "clause_content",
  "mitigation",
] as const;

type PreviewField = (typeof PREVIEW_FIELDS)[number];

const ACTIVE_FILENAME_SNIPPET_CONTEXT_CHARS = 18;
const ACTIVE_PREVIEW_SNIPPET_CONTEXT_CHARS = 320;
const HOLD_STEP_START_DELAY_MS = 280;
const HEADER_HEIGHT_PX = 44;

type OutputActionMode = "remove" | "retry";

const OUTPUT_ACTION_CONFIG: Record<
  OutputActionMode,
  {
    buttonClassName: string;
    confirmButtonLabel: string;
    confirmClassName: string;
    confirmTitle: string;
    icon: LucideIcon;
    pendingLabel: string;
    title: string;
  }
> = {
  remove: {
    title: "Remove result",
    confirmButtonLabel: "Remove",
    confirmTitle: "Remove this result?",
    pendingLabel: "Removing...",
    icon: Trash2,
    buttonClassName: "text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
    confirmClassName: "btn-destructive",
  },
  retry: {
    title: "Re-extract PDF",
    confirmButtonLabel: "Re-extract",
    confirmTitle: "Replace this result?",
    pendingLabel: "Starting...",
    icon: RotateCw,
    buttonClassName: "text-muted-foreground hover:bg-accent hover:text-foreground",
    confirmClassName: "btn-primary",
  },
};

type PreviewState = {
  loading: boolean;
  rows: PreviewRow[];
  rowCount: number;
};

type PreviewSearchMatch = {
  field: PreviewField;
  occurrenceIndex: number;
  rowIndex: number;
};

type PreviewSearchState = {
  activeMatchIndex: number;
  activeMatch: PreviewSearchMatch | null;
  matches: PreviewSearchMatch[];
  query: string;
};

function formatTimestamp(unixSeconds: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(unixSeconds * 1000));
}

function formatSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = sizeBytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatPreviewValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return JSON.stringify(value);
}

function renderHighlightedText(
  text: string,
  query: string,
  options?: { activeOccurrenceIndex?: number | null },
) {
  const activeOccurrenceIndex = options?.activeOccurrenceIndex ?? null;
  const normalizedQuery = query.trim();
  if (!normalizedQuery) return text;

  const lowerText = text.toLowerCase();
  const lowerQuery = normalizedQuery.toLowerCase();
  const parts: ComponentChildren[] = [];
  let cursor = 0;
  let matchIndex = lowerText.indexOf(lowerQuery);
  let occurrenceIndex = 0;

  while (matchIndex !== -1) {
    if (matchIndex > cursor) {
      parts.push(text.slice(cursor, matchIndex));
    }
    const matchedText = text.slice(matchIndex, matchIndex + normalizedQuery.length);
    parts.push(
      <mark className={activeOccurrenceIndex === occurrenceIndex ? "mark-active" : "mark"}>
        {matchedText}
      </mark>,
    );
    cursor = matchIndex + normalizedQuery.length;
    occurrenceIndex += 1;
    matchIndex = lowerText.indexOf(lowerQuery, cursor);
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }

  return parts.length === 0 ? text : parts;
}

/** Cuts `text` around its occurrenceIndex-th match and re-bases that index to the snippet, so renderHighlightedText marks the same occurrence. */
function buildMatchSnippet(
  text: string,
  query: string,
  occurrenceIndex = 0,
  contextChars = ACTIVE_FILENAME_SNIPPET_CONTEXT_CHARS,
): { text: string; activeOccurrenceIndex: number | null } {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) {
    return { text, activeOccurrenceIndex: null };
  }

  const lowerText = text.toLowerCase();
  const lowerQuery = normalizedQuery.toLowerCase();
  const matchIndexes: number[] = [];
  let searchStart = 0;
  while (true) {
    const matchIndex = lowerText.indexOf(lowerQuery, searchStart);
    if (matchIndex === -1) {
      break;
    }
    matchIndexes.push(matchIndex);
    searchStart = matchIndex + lowerQuery.length;
  }
  if (matchIndexes.length === 0) {
    return { text, activeOccurrenceIndex: null };
  }

  const targetOccurrenceIndex = Math.min(Math.max(occurrenceIndex, 0), matchIndexes.length - 1);
  const targetMatchIndex = matchIndexes[targetOccurrenceIndex];

  const start = Math.max(0, targetMatchIndex - contextChars);
  const end = Math.min(text.length, targetMatchIndex + normalizedQuery.length + contextChars);
  const prefix = start > 0 ? "...\u200b" : "";
  const suffix = end < text.length ? "\u200b..." : "";
  const activeOccurrenceIndex = matchIndexes.filter(
    (index) => index >= start && index < targetMatchIndex,
  ).length;
  return {
    text: `${prefix}${text.slice(start, end)}${suffix}`,
    activeOccurrenceIndex,
  };
}

function clampCellStyle(lines = 8): Record<string, string | number> {
  return {
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: lines,
    overflow: "hidden",
  };
}

function getHoldStepInterval(elapsedMs: number): number {
  return elapsedMs > 2200 ? 100 : elapsedMs > 1200 ? 125 : 155;
}

function buildPreviewCellPresentation(options: {
  activeGlobalMatch: boolean;
  activeGlobalOccurrenceIndex: number | null;
  activePreviewMatch: boolean;
  activePreviewOccurrenceIndex: number | null;
  query: string;
  value: string;
}): {
  activeOccurrenceIndex: number | null;
  shouldClamp: boolean;
  text: string;
} {
  const {
    activeGlobalMatch,
    activeGlobalOccurrenceIndex,
    activePreviewMatch,
    activePreviewOccurrenceIndex,
    query,
    value,
  } = options;

  if (activeGlobalMatch) {
    const snippet = buildMatchSnippet(
      value,
      query,
      activeGlobalOccurrenceIndex ?? 0,
      ACTIVE_PREVIEW_SNIPPET_CONTEXT_CHARS,
    );
    return {
      text: snippet.text,
      activeOccurrenceIndex: snippet.activeOccurrenceIndex,
      shouldClamp: false,
    };
  }

  if (activePreviewMatch && query.trim()) {
    const snippet = buildMatchSnippet(
      value,
      query,
      activePreviewOccurrenceIndex ?? 0,
      ACTIVE_PREVIEW_SNIPPET_CONTEXT_CHARS,
    );
    return {
      text: snippet.text,
      activeOccurrenceIndex: snippet.activeOccurrenceIndex,
      shouldClamp: false,
    };
  }

  return {
    text: value,
    activeOccurrenceIndex: null,
    shouldClamp: true,
  };
}

function getPreviewMatches(rows: PreviewRow[], query: string): PreviewSearchMatch[] {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) return [];
  const lowerQuery = normalizedQuery.toLowerCase();

  return rows.reduce<PreviewSearchMatch[]>((matches, row, rowIndex) => {
    PREVIEW_FIELDS.forEach((field) => {
      const value = formatPreviewValue(row[field]);
      const lowerValue = value.toLowerCase();
      let searchStart = 0;
      let occurrenceIndex = 0;

      while (true) {
        const matchIndex = lowerValue.indexOf(lowerQuery, searchStart);
        if (matchIndex === -1) break;
        matches.push({
          rowIndex,
          field,
          occurrenceIndex,
        });
        occurrenceIndex += 1;
        searchStart = matchIndex + lowerQuery.length;
      }
    });
    return matches;
  }, []);
}

function getWrappedIndex(currentIndex: number, direction: 1 | -1, total: number): number {
  return (currentIndex + direction + total) % total;
}

function buildPreviewSearchState(
  matches: PreviewSearchMatch[],
  query: string,
  activeMatchIndex: number,
): PreviewSearchState {
  if (matches.length === 0) {
    return {
      activeMatchIndex: 0,
      activeMatch: null,
      matches,
      query,
    };
  }

  const safeActiveMatchIndex = Math.min(activeMatchIndex, matches.length - 1);

  return {
    activeMatchIndex: safeActiveMatchIndex,
    activeMatch: matches[safeActiveMatchIndex] ?? null,
    matches,
    query,
  };
}

type MatchStepperProps = {
  total: number;
  value: string;
  onValueChange: (value: string) => void;
  onCommit: (value: string) => void;
  onStep: (direction: 1 | -1) => void;
};

/** Steps through matches by click, accelerating while held; the click that ends a hold does not step again. */
function MatchStepper({ total, value, onValueChange, onCommit, onStep }: MatchStepperProps) {
  const holdTimeoutRef = useRef<number | null>(null);
  const holdFrameRef = useRef<number | null>(null);
  const holdStepRef = useRef<(() => void) | null>(null);
  const suppressClickRef = useRef(false);

  function stopHold(): void {
    if (holdTimeoutRef.current) {
      window.clearTimeout(holdTimeoutRef.current);
      holdTimeoutRef.current = null;
    }
    if (holdFrameRef.current) {
      window.cancelAnimationFrame(holdFrameRef.current);
      holdFrameRef.current = null;
    }
    holdStepRef.current = null;
  }

  useEffect(() => {
    window.addEventListener("mouseup", stopHold);
    window.addEventListener("touchend", stopHold);
    window.addEventListener("touchcancel", stopHold);
    return () => {
      stopHold();
      window.removeEventListener("mouseup", stopHold);
      window.removeEventListener("touchend", stopHold);
      window.removeEventListener("touchcancel", stopHold);
    };
  }, []);

  function startHold(step: () => void): void {
    suppressClickRef.current = true;
    step();
    stopHold();
    holdStepRef.current = step;
    holdTimeoutRef.current = window.setTimeout(() => {
      const startedAt = performance.now();
      let lastTickAt = startedAt;
      const tick = (now: number) => {
        const activeStep = holdStepRef.current;
        if (!activeStep) return;
        if (now - lastTickAt >= getHoldStepInterval(now - startedAt)) {
          activeStep();
          lastTickAt = now;
        }
        holdFrameRef.current = window.requestAnimationFrame(tick);
      };
      holdFrameRef.current = window.requestAnimationFrame(tick);
    }, HOLD_STEP_START_DELAY_MS);
  }

  function renderStepButton(direction: 1 | -1) {
    const step = () => onStep(direction);
    const Icon = direction === -1 ? ChevronUp : ChevronDown;
    return (
      <button
        className="btn btn-outline btn-icon"
        aria-label={direction === -1 ? "Previous match" : "Next match"}
        onClick={() => {
          if (suppressClickRef.current) {
            suppressClickRef.current = false;
            return;
          }
          step();
        }}
        onMouseDown={(event) => {
          if (event.button !== 0) return;
          // Keep focus in the match input so its blur commit cannot undo this step.
          event.preventDefault();
          startHold(step);
        }}
        onTouchStart={() => startHold(step)}
        type="button"
      >
        <Icon aria-hidden="true" />
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      {renderStepButton(-1)}
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <input
          className="field h-8 w-12 px-2 text-right text-xs tabular-nums"
          aria-label="Match number"
          inputMode="numeric"
          max={total}
          min="1"
          onBlur={(event) => onCommit(event.currentTarget.value)}
          onInput={(event) => onValueChange(event.currentTarget.value.replace(/\D/g, ""))}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              onCommit(event.currentTarget.value);
            }
          }}
          type="text"
          value={value}
        />
        <span className="tabular-nums">of {total}</span>
      </div>
      {renderStepButton(1)}
    </div>
  );
}

function renderMainTableColGroup() {
  return (
    <colgroup>
      <col className="w-12" />
      <col />
      <col className="w-24" />
      <col className="w-44" />
      <col className="w-24" />
      <col className="w-[13.5rem]" />
      <col className="w-[6.5rem]" />
    </colgroup>
  );
}

export function OutputFilesTab({
  files,
  onRefresh,
  onNewExtraction,
  onRetryStarted,
}: OutputFilesTabProps) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortOrder, setSortOrder] = useState("recent");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [confirmingAction, setConfirmingAction] = useState<{
    fileId: string;
    mode: OutputActionMode;
  } | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    fileId: string;
    mode: OutputActionMode;
  } | null>(null);
  const [previewState, setPreviewState] = useState<Record<string, PreviewState>>({});
  const [previewQueryById, setPreviewQueryById] = useState<Record<string, string>>({});
  const [activePreviewMatchIndexById, setActivePreviewMatchIndexById] = useState<
    Record<string, number>
  >({});
  const [previewMatchInputById, setPreviewMatchInputById] = useState<Record<string, string>>({});
  const [searchMatches, setSearchMatches] = useState<OutputSearchMatch[]>([]);
  const [activeSearchMatchIndex, setActiveSearchMatchIndex] = useState(0);
  const [searchMatchInput, setSearchMatchInput] = useState("1");
  const [stickyChromeHeight, setStickyChromeHeight] = useState(0);
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  const selectionAnchorRef = useRef<string | null>(null);
  const toolbarRef = useRef<HTMLDivElement | null>(null);
  const mainTableHeaderScrollRef = useRef<HTMLDivElement | null>(null);
  const mainTableBodyScrollRef = useRef<HTMLDivElement | null>(null);
  const actionPopoverRef = useRef<HTMLDivElement | null>(null);
  const rowElementByIdRef = useRef<Record<string, HTMLTableRowElement | null>>({});
  const previewSectionByIdRef = useRef<Record<string, HTMLDivElement | null>>({});
  const previewRowElementByKeyRef = useRef<Record<string, HTMLElement | null>>({});
  const fileById = useMemo(() => new Map(files.map((file) => [file.id, file])), [files]);
  const previewRows = expandedRow ? previewState[expandedRow]?.rows : undefined;
  const previewQuery = expandedRow ? (previewQueryById[expandedRow] ?? "") : "";
  const previewMatches = useMemo(
    () => getPreviewMatches(previewRows ?? [], previewQuery),
    [previewRows, previewQuery],
  );
  const matchedFileIds = useMemo(
    () => new Set(searchMatches.map((match) => match.output_id)),
    [searchMatches],
  );
  const visibleFiles = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const matching = query
      ? files.filter(
          (file) => file.name.toLowerCase().includes(query) || matchedFileIds.has(file.id),
        )
      : [...files];
    return matching.sort((left, right) =>
      sortOrder === "name"
        ? left.name.localeCompare(right.name)
        : sortOrder === "clauses"
          ? right.row_count - left.row_count
          : right.modified_at - left.modified_at,
    );
  }, [files, matchedFileIds, searchQuery, sortOrder]);
  const allFileIds = visibleFiles.map((file) => file.id);
  const hasSelection = selectedFileIds.length > 0;
  const selectedVisibleCount = allFileIds.filter((id) => selectedFileIds.includes(id)).length;
  const isAllSelected = allFileIds.length > 0 && selectedVisibleCount === allFileIds.length;
  const isPartiallySelected = selectedVisibleCount > 0 && !isAllSelected;

  useEffect(() => {
    if (!selectAllRef.current) return;
    selectAllRef.current.indeterminate = isPartiallySelected;
  }, [isPartiallySelected]);

  useEffect(() => {
    if (!confirmingAction) return;

    function handleOutsidePointer(event: MouseEvent): void {
      if (
        actionPopoverRef.current &&
        event.target instanceof Node &&
        !actionPopoverRef.current.contains(event.target)
      ) {
        setConfirmingAction(null);
      }
    }

    window.addEventListener("mousedown", handleOutsidePointer);
    return () => {
      window.removeEventListener("mousedown", handleOutsidePointer);
    };
  }, [confirmingAction]);

  useEffect(() => {
    /** Sticky chrome is the page header, results toolbar, and column header on wide screens, and the top tab bar on narrow ones. */
    function measureStickyLayout(): void {
      const wide = window.matchMedia("(min-width: 768px)").matches;
      const toolbar = toolbarRef.current?.getBoundingClientRect().height ?? 0;
      const columnHeader = mainTableHeaderScrollRef.current?.getBoundingClientRect().height ?? 0;
      const navigation = document.querySelector("aside")?.getBoundingClientRect().height ?? 0;
      setStickyChromeHeight(wide ? HEADER_HEIGHT_PX + toolbar + columnHeader : navigation);
    }

    measureStickyLayout();
    window.addEventListener("resize", measureStickyLayout);
    return () => {
      window.removeEventListener("resize", measureStickyLayout);
    };
  }, [files.length, hasSelection, searchMatches.length, searchQuery]);

  useEffect(() => {
    const query = searchQuery.trim();
    let cancelled = false;
    if (!query) {
      setSearchMatches([]);
      setActiveSearchMatchIndex(0);
      setSearchMatchInput("1");
      return;
    }

    const timeoutId = window.setTimeout(() => {
      void searchOutputs(query)
        .then((response) => {
          if (cancelled) return;
          setSearchMatches(response.matches);
          setActiveSearchMatchIndex(0);
        })
        .catch(() => {
          if (cancelled) return;
          setSearchMatches([]);
        });
    }, 200);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [searchQuery, files]);

  function toggleSelectAll() {
    selectionAnchorRef.current = null;
    setSelectedFileIds(isAllSelected ? [] : [...allFileIds]);
  }

  function selectRange(fileId: string): string[] {
    const anchorId = selectionAnchorRef.current;
    const targetIndex = allFileIds.indexOf(fileId);
    if (!anchorId || targetIndex === -1) {
      return [fileId];
    }
    const anchorIndex = allFileIds.indexOf(anchorId);
    if (anchorIndex === -1) {
      return [fileId];
    }

    const [start, end] =
      anchorIndex < targetIndex ? [anchorIndex, targetIndex] : [targetIndex, anchorIndex];
    return allFileIds.slice(start, end + 1);
  }

  /** Ignores clicks on row controls; the composed path is fixed at dispatch, so it still holds the control after a re-render swaps the clicked icon out. */
  function isInteractiveElement(event: Event): boolean {
    return event
      .composedPath()
      .some((node) => node instanceof Element && node.matches("button, input, a, label, select"));
  }

  function updateSelection(
    fileId: string,
    modifiers?: { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean },
  ) {
    const shiftKey = Boolean(modifiers?.shiftKey);
    const additiveKey = Boolean(modifiers?.ctrlKey || modifiers?.metaKey);

    if (shiftKey) {
      setSelectedFileIds(selectRange(fileId));
      return;
    }

    if (additiveKey) {
      setSelectedFileIds((current) =>
        current.includes(fileId) ? current.filter((id) => id !== fileId) : [...current, fileId],
      );
      selectionAnchorRef.current = fileId;
      return;
    }

    setSelectedFileIds([fileId]);
    selectionAnchorRef.current = fileId;
  }

  function handleCheckboxSelection(
    fileId: string,
    checked: boolean,
    modifiers?: { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean },
  ) {
    if (!checked) {
      setSelectedFileIds((current) => current.filter((id) => id !== fileId));
      if (selectionAnchorRef.current === fileId) {
        selectionAnchorRef.current = null;
      }
      return;
    }

    if (modifiers?.shiftKey) {
      const rangeIds = selectRange(fileId);
      setSelectedFileIds((current) => [
        ...current.filter((id) => !rangeIds.includes(id)),
        ...rangeIds,
      ]);
      selectionAnchorRef.current = fileId;
      return;
    }

    setSelectedFileIds((current) => (current.includes(fileId) ? current : [...current, fileId]));
    selectionAnchorRef.current = fileId;
  }

  function handleRowClick(event: MouseEvent, fileId: string): void {
    if (isInteractiveElement(event)) {
      return;
    }
    updateSelection(fileId, event);
  }

  async function handleDownload(
    targetFiles: OutputFileSummary[],
    format: OutputExportFormat,
  ): Promise<void> {
    if (!targetFiles.length) return;
    setActionError("");
    try {
      await downloadOutputs(
        targetFiles.map((file) => file.id),
        format,
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not download results.");
    }
  }

  function handlePanelDownload(scope: "all" | "selected", format: OutputExportFormat): void {
    const targetFiles =
      scope === "all" ? files : files.filter((file) => selectedFileIds.includes(file.id));
    void handleDownload(targetFiles, format);
  }

  function syncMainTableHeaderScroll(): void {
    if (!mainTableHeaderScrollRef.current || !mainTableBodyScrollRef.current) {
      return;
    }
    mainTableHeaderScrollRef.current.scrollLeft = mainTableBodyScrollRef.current.scrollLeft;
  }

  async function handleRefresh() {
    setIsRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setIsRefreshing(false);
    }
  }

  async function handleOutputAction(file: OutputFileSummary, mode: OutputActionMode) {
    setPendingAction({ fileId: file.id, mode });
    setActionError("");
    try {
      if (mode === "remove") {
        await deleteOutput(file.id);
      } else {
        await retryOutput(file.id);
      }

      setSelectedFileIds((current) => current.filter((id) => id !== file.id));
      if (expandedRow === file.id) {
        setExpandedRow(null);
      }
      setConfirmingAction(null);
      await onRefresh();
      if (mode === "retry") onRetryStarted();
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : `Failed to ${mode === "remove" ? "remove" : "retry"} result.`,
      );
    } finally {
      setPendingAction(null);
    }
  }

  async function ensurePreviewLoaded(file: OutputFileSummary) {
    if (previewState[file.id]) return;
    setActionError("");

    setPreviewState((current) => ({
      ...current,
      [file.id]: { loading: true, rows: [], rowCount: file.row_count },
    }));

    try {
      const preview = await getOutputPreview(file.id);
      setPreviewState((current) => ({
        ...current,
        [file.id]: {
          loading: false,
          rows: preview.rows,
          rowCount: preview.row_count,
        },
      }));
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : `Could not load the preview for ${file.name}.`,
      );
      setExpandedRow(null);
      setPreviewState((current) => {
        const next = { ...current };
        delete next[file.id];
        return next;
      });
    }
  }

  async function togglePreview(file: OutputFileSummary) {
    if (expandedRow === file.id) {
      setExpandedRow(null);
      return;
    }

    setExpandedRow(file.id);
    await ensurePreviewLoaded(file);
  }

  function getStickyOffset(extraOffset = 0): number {
    return stickyChromeHeight + extraOffset;
  }

  function scrollElementIntoViewport(element: HTMLElement, extraOffset = 12): void {
    const targetTop =
      element.getBoundingClientRect().top + window.scrollY - getStickyOffset(extraOffset);
    window.scrollTo({
      top: Math.max(targetTop, 0),
      behavior: "smooth",
    });
  }

  function scrollSearchMatchIntoView(match: OutputSearchMatch) {
    if (match.row_index == null) {
      const fileRow = rowElementByIdRef.current[match.output_id];
      if (fileRow) {
        scrollElementIntoViewport(fileRow);
      }
      return;
    }

    scrollPreviewMatchIntoView(match.output_id, match.row_index);
  }

  function scrollPreviewMatchIntoView(fileId: string, rowIndex: number): void {
    const previewRow = previewRowElementByKeyRef.current[`${fileId}:${rowIndex}`];
    if (!previewRow) return;

    const previewSection = previewSectionByIdRef.current[fileId];
    const previewPanel = previewRow.closest(
      "[data-preview-scroll-container]",
    ) as HTMLDivElement | null;

    if (previewSection) {
      scrollElementIntoViewport(previewSection, 16);
    }

    if (!previewPanel) return;

    const panelRect = previewPanel.getBoundingClientRect();
    const rowRect = previewRow.getBoundingClientRect();
    previewPanel.scrollTo({
      top:
        previewPanel.scrollTop +
        (rowRect.top - panelRect.top) -
        panelRect.height / 2 +
        rowRect.height / 2,
      behavior: "smooth",
    });
  }

  function handlePreviewSearchInput(fileId: string, value: string): void {
    setPreviewQueryById((current) => ({
      ...current,
      [fileId]: value,
    }));
    setActivePreviewMatchIndexById((current) => ({
      ...current,
      [fileId]: 0,
    }));
    setPreviewMatchInputById((current) => ({
      ...current,
      [fileId]: "1",
    }));

    const firstMatch = getPreviewMatches(previewState[fileId]?.rows ?? [], value)[0];
    if (firstMatch === undefined) return;

    window.requestAnimationFrame(() => {
      scrollPreviewMatchIntoView(fileId, firstMatch.rowIndex);
    });
  }

  function stepPreviewMatch(fileId: string, direction: 1 | -1): void {
    const matches = previewMatches;
    if (matches.length === 0) return;

    let nextIndex = 0;
    setActivePreviewMatchIndexById((current) => {
      nextIndex = getWrappedIndex(current[fileId] ?? 0, direction, matches.length);
      return {
        ...current,
        [fileId]: nextIndex,
      };
    });
    setPreviewMatchInputById((current) => ({
      ...current,
      [fileId]: String(nextIndex + 1),
    }));

    window.requestAnimationFrame(() => {
      scrollPreviewMatchIntoView(fileId, matches[nextIndex].rowIndex);
    });
  }

  function commitPreviewMatchInput(fileId: string, rawValue: string): void {
    const matches = previewMatches;
    if (matches.length === 0) {
      setPreviewMatchInputById((current) => ({
        ...current,
        [fileId]: "1",
      }));
      return;
    }

    const parsedValue = Number.parseInt(rawValue, 10);
    const safeValue = Number.isFinite(parsedValue)
      ? Math.min(Math.max(parsedValue, 1), matches.length)
      : (activePreviewMatchIndexById[fileId] ?? 0) + 1;

    setPreviewMatchInputById((current) => ({
      ...current,
      [fileId]: String(safeValue),
    }));
    setActivePreviewMatchIndexById((current) => ({
      ...current,
      [fileId]: safeValue - 1,
    }));

    window.requestAnimationFrame(() => {
      scrollPreviewMatchIntoView(fileId, matches[safeValue - 1].rowIndex);
    });
  }

  async function openSearchMatch(match: OutputSearchMatch) {
    const file = fileById.get(match.output_id);
    if (!file) return;

    setExpandedRow(file.id);
    await ensurePreviewLoaded(file);

    window.requestAnimationFrame(() => {
      scrollSearchMatchIntoView(match);
    });
  }

  function stepSearchMatch(direction: 1 | -1): void {
    if (searchMatches.length === 0) return;
    setActiveSearchMatchIndex((currentIndex) =>
      getWrappedIndex(currentIndex, direction, searchMatches.length),
    );
  }

  function commitSearchMatchInput(rawValue: string): void {
    if (searchMatches.length === 0) {
      setSearchMatchInput("1");
      return;
    }

    const parsedValue = Number.parseInt(rawValue, 10);
    if (!Number.isFinite(parsedValue)) {
      setSearchMatchInput(String(activeSearchMatchIndex + 1));
      return;
    }

    const clampedValue = Math.min(Math.max(parsedValue, 1), searchMatches.length);
    setSearchMatchInput(String(clampedValue));
    setActiveSearchMatchIndex(clampedValue - 1);
  }

  useEffect(() => {
    if (searchMatches.length === 0) return;
    void openSearchMatch(searchMatches[activeSearchMatchIndex]);
  }, [activeSearchMatchIndex, searchMatches]);

  useEffect(() => {
    setSearchMatchInput(String(activeSearchMatchIndex + 1));
  }, [activeSearchMatchIndex]);

  const totalClauses = files.reduce((total, file) => total + file.row_count, 0);

  return (
    <div>
      <PageHeader title="Results library" icon={Library}>
        <button
          className="btn btn-ghost btn-icon"
          disabled={isRefreshing}
          aria-label="Refresh results"
          title="Refresh results"
          onClick={() => void handleRefresh()}
          type="button"
        >
          <RefreshCw aria-hidden="true" className={isRefreshing ? "animate-spin" : undefined} />
        </button>
        <button type="button" className="btn btn-primary btn-sm" onClick={onNewExtraction}>
          <Plus aria-hidden="true" />
          New extraction
        </button>
      </PageHeader>
      <div className="mx-auto w-full max-w-[90rem] px-4 py-6 sm:px-6">
        <p className="mb-3 text-sm text-muted-foreground" hidden={!files.length}>
          <span className="font-medium tabular-nums text-foreground">{files.length}</span>{" "}
          {files.length === 1 ? "document" : "documents"},{" "}
          <span className="font-medium tabular-nums text-foreground">
            {totalClauses.toLocaleString()}
          </span>{" "}
          extracted clauses
        </p>
        <div className="rounded-xl border bg-card">
          <div className="z-20 rounded-t-xl bg-card md:sticky md:top-[var(--header-height)]">
            <div
              className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3"
              ref={toolbarRef}
              hidden={!files.length}
            >
              <SearchField
                className="w-full sm:w-80"
                placeholder="Search documents, clauses, mitigations…"
                aria-label="Search results"
                value={searchQuery}
                onInput={(event) => setSearchQuery(event.currentTarget.value)}
                type="text"
              />
              <div
                className="flex items-center gap-3 text-xs tabular-nums text-muted-foreground"
                hidden={!searchQuery.trim()}
              >
                <span>{visibleFiles.length} results</span>
                {searchQuery.trim() && <span>{searchMatches.length} matches</span>}
              </div>
              {searchMatches.length > 0 && (
                <MatchStepper
                  total={searchMatches.length}
                  value={searchMatchInput}
                  onValueChange={setSearchMatchInput}
                  onCommit={commitSearchMatchInput}
                  onStep={stepSearchMatch}
                />
              )}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs md:ml-auto">
                <label className="inline-flex items-center gap-2 text-muted-foreground">
                  Sort by
                  <select
                    className="field h-8 w-auto py-0 pl-2.5 pr-8 text-xs"
                    value={sortOrder}
                    onChange={(event) => setSortOrder(event.currentTarget.value)}
                  >
                    <option value="recent">Newest first</option>
                    <option value="name">Document name</option>
                    <option value="clauses">Most clauses</option>
                  </select>
                </label>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-muted-foreground">Export all</span>
                  {DOWNLOAD_FORMATS.map((format) => (
                    <button
                      key={format.value}
                      className="btn btn-outline btn-sm"
                      disabled={files.length === 0}
                      onClick={() => handlePanelDownload("all", format.value)}
                      title={format.description}
                      type="button"
                    >
                      {format.label}
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-1.5" hidden={!hasSelection}>
                  <span className="text-muted-foreground">
                    Selected{hasSelection ? ` (${selectedFileIds.length})` : ""}
                  </span>
                  {DOWNLOAD_FORMATS.map((format) => (
                    <button
                      key={format.value}
                      className="btn btn-outline btn-sm"
                      disabled={!hasSelection}
                      onClick={() => handlePanelDownload("selected", format.value)}
                      title={format.description}
                      type="button"
                    >
                      {format.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div
              ref={mainTableHeaderScrollRef}
              hidden={visibleFiles.length === 0}
              className="no-scrollbar overflow-x-auto border-y bg-muted/40"
            >
              <table
                className={`${visibleFiles.length ? "min-w-[56rem]" : ""} w-full table-fixed border-collapse text-left`}
              >
                {visibleFiles.length > 0 && renderMainTableColGroup()}
                <thead>
                  <tr className="h-10 text-xs font-medium text-muted-foreground">
                    <th className="px-4 text-center">
                      <input
                        className="check"
                        checked={isAllSelected}
                        aria-label="Select all visible results"
                        onChange={toggleSelectAll}
                        ref={selectAllRef}
                        type="checkbox"
                      />
                    </th>
                    <th className="px-2 font-medium">Document</th>
                    <th className="px-2 font-medium">Clauses</th>
                    <th className="px-2 font-medium">Modified</th>
                    <th className="px-2 font-medium">Size</th>
                    <th className="px-2 font-medium">Download</th>
                    <th className="px-2 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
              </table>
            </div>
          </div>

          {actionError && (
            <div
              className="border-b border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive"
              role="alert"
            >
              {actionError}
            </div>
          )}

          <div
            ref={mainTableBodyScrollRef}
            className="@container overflow-x-auto"
            onScroll={syncMainTableHeaderScroll}
          >
            <table
              className={`${visibleFiles.length ? "min-w-[56rem]" : ""} w-full table-fixed border-collapse text-left text-sm`}
            >
              {visibleFiles.length > 0 && renderMainTableColGroup()}
              <tbody>
                {visibleFiles.map((file) => {
                  const preview = previewState[file.id];
                  const previewSearch = buildPreviewSearchState(
                    expandedRow === file.id ? previewMatches : [],
                    previewQueryById[file.id] ?? "",
                    activePreviewMatchIndexById[file.id] ?? 0,
                  );
                  const activeSearchMatch = searchMatches[activeSearchMatchIndex];
                  const previewHighlightQuery = previewSearch.query.trim() || searchQuery;
                  const isActionConfirming = confirmingAction?.fileId === file.id;
                  const confirmMode: OutputActionMode | null = isActionConfirming
                    ? (confirmingAction?.mode ?? null)
                    : null;
                  const pendingActionLabel =
                    pendingAction?.fileId === file.id &&
                    pendingAction.mode === confirmMode &&
                    confirmMode
                      ? OUTPUT_ACTION_CONFIG[confirmMode].pendingLabel
                      : null;
                  const activeFilenameSnippet = buildMatchSnippet(
                    file.name,
                    searchQuery,
                    activeSearchMatch?.output_id === file.id && activeSearchMatch?.row_index == null
                      ? activeSearchMatch.occurrence_index
                      : 0,
                  );
                  const isSelected = selectedFileIds.includes(file.id);
                  const isExpanded = expandedRow === file.id;

                  /** Renders one preview field with the same match highlighting, snippet, and clamp rules for every layout position. */
                  const renderField = (
                    row: PreviewRow,
                    rowIndex: number,
                    field: PreviewField,
                    clampLines?: number,
                  ) => {
                    const value = formatPreviewValue(row[field]);
                    const isActivePreviewMatch =
                      Boolean(previewSearch.query.trim()) &&
                      previewSearch.activeMatch?.rowIndex === rowIndex &&
                      previewSearch.activeMatch?.field === field;
                    const isActiveGlobalPreviewMatch =
                      !previewSearch.query.trim() &&
                      activeSearchMatch?.output_id === file.id &&
                      activeSearchMatch?.row_index === rowIndex &&
                      activeSearchMatch?.matched_field === field;
                    const presentation = buildPreviewCellPresentation({
                      value,
                      query: previewHighlightQuery,
                      activeGlobalMatch: isActiveGlobalPreviewMatch,
                      activeGlobalOccurrenceIndex: activeSearchMatch?.occurrence_index ?? null,
                      activePreviewMatch: isActivePreviewMatch,
                      activePreviewOccurrenceIndex: isActivePreviewMatch
                        ? (previewSearch.activeMatch?.occurrenceIndex ?? null)
                        : null,
                    });
                    const content = renderHighlightedText(
                      presentation.text,
                      previewHighlightQuery,
                      {
                        activeOccurrenceIndex: presentation.activeOccurrenceIndex,
                      },
                    );
                    if (!clampLines) return content;
                    return (
                      <div
                        className="whitespace-pre-wrap break-words"
                        style={presentation.shouldClamp ? clampCellStyle(clampLines) : undefined}
                        title={value}
                      >
                        {content}
                      </div>
                    );
                  };

                  return (
                    <Fragment key={file.id}>
                      <tr
                        ref={(element) => {
                          rowElementByIdRef.current[file.id] = element;
                        }}
                        className={`border-b transition-colors ${isSelected ? "bg-accent/70" : "hover:bg-muted/40"}`}
                        onClick={(event) => handleRowClick(event, file.id)}
                        style={{ scrollMarginTop: `${getStickyOffset(16)}px` }}
                      >
                        <td className="px-4 py-3 text-center">
                          <input
                            className="check"
                            checked={isSelected}
                            aria-label={`Select ${file.name}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              handleCheckboxSelection(file.id, event.currentTarget.checked, event);
                            }}
                            type="checkbox"
                          />
                        </td>
                        <td className="max-w-0 px-2 py-3">
                          <div className="flex min-w-0 items-center gap-2.5">
                            <FileText
                              aria-hidden="true"
                              className="size-4 shrink-0 text-muted-foreground"
                            />
                            <span className="block truncate font-medium" title={file.name}>
                              {renderHighlightedText(activeFilenameSnippet.text, searchQuery, {
                                activeOccurrenceIndex:
                                  activeSearchMatch?.output_id === file.id &&
                                  activeSearchMatch?.row_index == null
                                    ? activeFilenameSnippet.activeOccurrenceIndex
                                    : null,
                              })}
                            </span>
                          </div>
                        </td>
                        <td className="px-2 py-3 tabular-nums">{file.row_count}</td>
                        <td className="px-2 py-3 text-muted-foreground">
                          {formatTimestamp(file.modified_at)}
                        </td>
                        <td className="px-2 py-3 font-mono text-xs text-muted-foreground">
                          {formatSize(file.size_bytes)}
                        </td>
                        <td className="px-2 py-3">
                          <div className="flex items-center gap-1.5">
                            {DOWNLOAD_FORMATS.filter((format) =>
                              file.available_export_formats.includes(format.value),
                            ).map((format) => (
                              <button
                                key={format.value}
                                className="btn btn-outline btn-sm h-7 px-2"
                                onClick={() => void handleDownload([file], format.value)}
                                title={format.description}
                                type="button"
                              >
                                {format.label}
                              </button>
                            ))}
                          </div>
                        </td>
                        <td className="px-2 py-3">
                          <div className="relative flex items-center justify-end gap-0.5 pr-2">
                            <button
                              onClick={() => void togglePreview(file)}
                              aria-label={`${isExpanded ? "Close" : "Open"} preview for ${file.name}`}
                              aria-expanded={isExpanded}
                              title={isExpanded ? "Close preview" : "Open preview"}
                              className={`btn btn-icon ${isExpanded ? "bg-accent text-foreground" : "btn-ghost text-muted-foreground"}`}
                              disabled={!file.preview_available}
                              type="button"
                            >
                              {isExpanded ? (
                                <EyeOff aria-hidden="true" />
                              ) : (
                                <Eye aria-hidden="true" />
                              )}
                            </button>
                            {(Object.keys(OUTPUT_ACTION_CONFIG) as OutputActionMode[]).map(
                              (mode) => {
                                const action = OUTPUT_ACTION_CONFIG[mode];
                                const Icon = action.icon;
                                return (
                                  <button
                                    key={mode}
                                    className={`btn btn-icon ${action.buttonClassName}`}
                                    disabled={Boolean(pendingAction)}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setConfirmingAction((current) =>
                                        current?.fileId === file.id && current.mode === mode
                                          ? null
                                          : { fileId: file.id, mode },
                                      );
                                    }}
                                    title={action.title}
                                    aria-label={action.title}
                                    type="button"
                                  >
                                    <Icon aria-hidden="true" />
                                  </button>
                                );
                              },
                            )}
                            {confirmMode && (
                              <div
                                ref={actionPopoverRef}
                                className="absolute right-0 top-full z-20 mt-2 w-60 rounded-lg border bg-card p-3 text-left shadow-lg"
                              >
                                <p className="text-sm font-medium">
                                  {OUTPUT_ACTION_CONFIG[confirmMode].confirmTitle}
                                </p>
                                {confirmMode === "retry" && (
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    Removes this result and runs the PDF again.
                                  </p>
                                )}
                                <div className="mt-3 flex justify-end gap-2">
                                  <button
                                    className="btn btn-ghost btn-sm"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setConfirmingAction(null);
                                    }}
                                    type="button"
                                  >
                                    Cancel
                                  </button>
                                  <button
                                    className={`btn btn-sm ${OUTPUT_ACTION_CONFIG[confirmMode].confirmClassName}`}
                                    disabled={Boolean(pendingAction)}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void handleOutputAction(file, confirmMode);
                                    }}
                                    type="button"
                                  >
                                    {pendingActionLabel ??
                                      OUTPUT_ACTION_CONFIG[confirmMode].confirmButtonLabel}
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="border-b bg-muted/30">
                          <td className="p-0" colSpan={7}>
                            {preview?.loading ? (
                              <div className="sticky left-0 w-[100cqw] flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
                                <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
                                Loading preview…
                              </div>
                            ) : preview?.rows.length ? (
                              <div
                                className="sticky left-0 w-[100cqw] px-4 py-4 sm:px-6"
                                data-preview-section
                                ref={(element) => {
                                  previewSectionByIdRef.current[file.id] = element;
                                }}
                                style={{ scrollMarginTop: `${getStickyOffset(16)}px` }}
                              >
                                <div className="flex flex-wrap items-center justify-between gap-3 pb-3">
                                  <p className="text-sm font-medium">
                                    {preview.rowCount}{" "}
                                    {preview.rowCount === 1 ? "clause" : "clauses"}
                                  </p>
                                  <div className="flex flex-wrap items-center justify-end gap-2">
                                    <SearchField
                                      compact
                                      className="w-full sm:w-64"
                                      aria-label={`Search clauses in ${file.name}`}
                                      onInput={(event) =>
                                        handlePreviewSearchInput(file.id, event.currentTarget.value)
                                      }
                                      placeholder="Search these clauses…"
                                      type="text"
                                      value={previewSearch.query}
                                    />
                                    {previewSearch.query.trim() && (
                                      <>
                                        <span className="text-xs tabular-nums text-muted-foreground">
                                          {previewSearch.matches.length} matches
                                        </span>
                                        {previewSearch.matches.length > 0 && (
                                          <MatchStepper
                                            total={previewSearch.matches.length}
                                            value={
                                              previewMatchInputById[file.id] ??
                                              String(previewSearch.activeMatchIndex + 1)
                                            }
                                            onValueChange={(value) =>
                                              setPreviewMatchInputById((current) => ({
                                                ...current,
                                                [file.id]: value,
                                              }))
                                            }
                                            onCommit={(value) =>
                                              commitPreviewMatchInput(file.id, value)
                                            }
                                            onStep={(direction) =>
                                              stepPreviewMatch(file.id, direction)
                                            }
                                          />
                                        )}
                                      </>
                                    )}
                                  </div>
                                </div>
                                <div
                                  className="thin-scrollbar max-h-[38rem] divide-y overflow-y-auto rounded-xl border bg-card"
                                  data-preview-scroll-container
                                >
                                  {preview.rows.map((row, index) => {
                                    const hasMitigation =
                                      formatPreviewValue(row.mitigation).trim() !== "";
                                    return (
                                      <article
                                        ref={(element) => {
                                          previewRowElementByKeyRef.current[`${file.id}:${index}`] =
                                            element;
                                        }}
                                        key={index}
                                        className="grid gap-x-6 gap-y-3 px-4 py-4 lg:grid-cols-[1.75rem_minmax(0,3fr)_minmax(0,2fr)]"
                                        style={{ scrollMarginTop: "1rem" }}
                                      >
                                        <span className="hidden pt-px text-xs font-medium tabular-nums text-muted-foreground lg:block">
                                          {index + 1}
                                        </span>
                                        <div className="min-w-0">
                                          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                                            <h4 className="whitespace-pre-wrap text-sm font-semibold">
                                              {renderField(row, index, "clause_header")}
                                            </h4>
                                            <span className="text-xs text-muted-foreground">
                                              {renderField(row, index, "source_document")}
                                            </span>
                                            <span className="text-xs text-muted-foreground">
                                              {renderField(row, index, "date")}
                                            </span>
                                          </div>
                                          <div className="mt-2 text-sm leading-6">
                                            {renderField(row, index, "clause_content", 8)}
                                          </div>
                                        </div>
                                        <aside className="min-w-0 self-start rounded-lg bg-muted/60 px-3.5 py-3">
                                          <p className="field-label mb-1">Mitigation</p>
                                          <div className="text-sm leading-6">
                                            {hasMitigation ? (
                                              renderField(row, index, "mitigation", 8)
                                            ) : (
                                              <span className="text-muted-foreground">
                                                No mitigation matched
                                              </span>
                                            )}
                                          </div>
                                        </aside>
                                      </article>
                                    );
                                  })}
                                </div>
                              </div>
                            ) : (
                              <div className="sticky left-0 w-[100cqw] py-10 text-center text-sm text-muted-foreground">
                                No preview rows available for {file.name}.
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {visibleFiles.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      <div className="flex flex-col items-center gap-3 px-4 py-16 text-center">
                        <p className="text-sm font-medium">
                          {searchQuery ? "No matching results" : "No results yet"}
                        </p>
                        <p className="text-sm text-muted-foreground">
                          {searchQuery
                            ? "Try a clause number, a heading, or words from a mitigation."
                            : "Extracted clauses appear here once a PDF has been processed."}
                        </p>
                        {searchQuery.trim() ? (
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={() => setSearchQuery("")}
                          >
                            Clear search
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            onClick={onNewExtraction}
                          >
                            <Plus aria-hidden="true" />
                            New extraction
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
