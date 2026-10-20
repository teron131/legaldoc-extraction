"""Own one background job, its terminal outcome, and cursor-based log recovery independently of browser connections."""

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Literal
from uuid import uuid4

from ..cancellation import RunStopped, check_stop, stop_signal
from ..config import LOG_FILE, LOGS_DIR
from ..pipeline import BatchResult

logger = logging.getLogger(__name__)
LOG_STREAM_POLL_INTERVAL_SECONDS = 0.5
RECENT_LOG_LINE_COUNT = 200


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """The current backend-owned run identity and outcome; a page reload does not create a new run."""

    run_id: str = ""
    status: Literal[
        "idle", "running", "stopping", "stopped", "completed", "partial", "failed"
    ] = "idle"
    result: BatchResult | None = None

    @property
    def active(self) -> bool:
        """Stopping retains the run reservation until every worker exits."""
        return self.status in {"running", "stopping"}


class PipelineRunner:
    """Serialize background work and retain its outcome for the lifetime of the backend process."""

    def __init__(self, *, log_file: Path, logs_dir: Path) -> None:
        self._log_file = log_file
        self._logs_dir = logs_dir
        self._lock = threading.Lock()
        self._run = RunSnapshot()
        self._stop = threading.Event()

    def state(self) -> RunSnapshot:
        with self._lock:
            return self._run

    def is_running(self) -> bool:
        return self.state().active

    def start(self, target: Callable[..., BatchResult | None], *args: Any) -> bool:
        """Return False while a run is active; otherwise reserve a generation and clear its log atomically, releasing the reservation if setup fails."""
        with self._lock:
            if self._run.active:
                return False
            self._stop = threading.Event()
            run_id = uuid4().hex
            self._run = RunSnapshot(run_id=run_id, status="running")
            try:
                self._logs_dir.mkdir(parents=True, exist_ok=True)
                self._log_file.write_text("", encoding="utf-8")
            except Exception:
                self._run = RunSnapshot(run_id=run_id, status="failed")
                raise
        try:
            threading.Thread(
                target=self._run_target, args=(target, *args), daemon=True
            ).start()
        except Exception:
            with self._lock:
                self._run = RunSnapshot(run_id=run_id, status="failed")
            raise
        return True

    def stop(self, run_id: str) -> RunSnapshot | None:
        """Return None for a stale run_id; an active generation stays reserved as stopping until its workers exit."""
        with self._lock:
            if not run_id or run_id != self._run.run_id:
                return None
            if self._run.active:
                self._stop.set()
                self._run = RunSnapshot(run_id=run_id, status="stopping")
            return self._run

    def snapshot(
        self, lines: int = RECENT_LOG_LINE_COUNT
    ) -> tuple[list[str], int, RunSnapshot]:
        """Pair recent complete log lines with a byte cursor and generation so a subscriber resumes without replay."""
        with self._lock:
            state = self._run
            content = self._log_file.read_bytes() if self._log_file.exists() else b""
            cursor = len(content)
            if state.active:
                cursor = content.rfind(b"\n") + 1
            return (
                content[:cursor]
                .decode("utf-8", errors="replace")
                .splitlines()[-lines:],
                cursor,
                state,
            )

    def stream_logs(
        self,
        poll_interval: float = LOG_STREAM_POLL_INTERVAL_SECONDS,
        *,
        after: int = 0,
        run_id: str | None = None,
    ) -> Iterator[str]:
        """Resume complete records using SSE IDs; signal a reset rather than mix logs from different runs."""
        expected_run = self.state().run_id if run_id is None else run_id
        position = after
        yield "retry: 2000\n\n"
        while True:
            events: list[str] = []
            with self._lock:
                state = self._run
                reset = state.run_id != expected_run
                if not reset and self._log_file.exists():
                    with self._log_file.open("rb") as handle:
                        if position > self._log_file.stat().st_size:
                            position = 0
                        handle.seek(position)
                        while line := handle.readline():
                            if not line.endswith(b"\n") and state.active:
                                break
                            position = handle.tell()
                            message = line.decode("utf-8", errors="replace").rstrip(
                                "\r\n"
                            )
                            events.append(
                                f"id: {expected_run}:{position}\ndata: {message}\n\n"
                            )
            if reset:
                yield "event: reset\ndata: reset\n\n"
                return
            yield from events
            if not state.active:
                yield f"event: done\ndata: {json.dumps(asdict(state))}\n\n"
                return
            time.sleep(poll_interval)

    def _run_target(
        self, target: Callable[..., BatchResult | None], *args: Any
    ) -> None:
        """Publish the terminal outcome only after the final log records have been written."""
        result = None
        status = "completed"
        token = stop_signal.set(self._stop)
        try:
            check_stop()
            result = target(*args)
            if result is not None and result.failed:
                status = "partial" if result.succeeded else "failed"
        except RunStopped:
            status = "stopped"
        except Exception as error:
            status = "failed"
            logger.exception("[MAIN] Extraction failed: %s", error)
        finally:
            stop_signal.reset(token)
            logger.info("[MAIN] Execution finished")
            with self._lock:
                self._run = RunSnapshot(
                    run_id=self._run.run_id,
                    status="stopped" if self._stop.is_set() else status,
                    result=result,
                )


runner = PipelineRunner(log_file=LOG_FILE, logs_dir=LOGS_DIR)
