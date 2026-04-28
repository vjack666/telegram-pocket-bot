import json
import logging
import os
import sys
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ShutdownSnapshot:
    reason: str = "unknown"
    component: str = "main"


class BlackBoxRecorder:
    def __init__(
        self,
        base_dir: str = "runtime/blackbox",
        max_events: int = 200,
        shutdown_snapshot: Callable[[], ShutdownSnapshot] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = self._base_dir / f"blackbox_{timestamp}_{os.getpid()}.jsonl"
        self.snapshot_path = self._base_dir / f"blackbox_{timestamp}_{os.getpid()}_summary.json"
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._shutdown_snapshot = shutdown_snapshot

    def _current_shutdown(self) -> ShutdownSnapshot:
        if self._shutdown_snapshot is None:
            return ShutdownSnapshot()
        try:
            return self._shutdown_snapshot()
        except Exception:
            self._safe_stderr("No se pudo obtener snapshot de shutdown para blackbox")
            return ShutdownSnapshot()

    @staticmethod
    def _safe_stderr(message: str) -> None:
        try:
            print(f"[blackbox] {message}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass

    def record(self, event: str, **fields: Any) -> None:
        shutdown = self._current_shutdown()
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            "pid": os.getpid(),
            "shutdown_reason": shutdown.reason,
            "component": fields.pop("component", shutdown.component),
            **fields,
        }
        with self._lock:
            self._events.append(payload)
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
            except Exception:
                self._safe_stderr("No se pudo escribir evento en blackbox")

    def dump_summary(self, shutdown: dict[str, Any]) -> None:
        with self._lock:
            summary = {
                "generated_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "shutdown": shutdown,
                "events": list(self._events),
            }
            try:
                self.snapshot_path.write_text(
                    json.dumps(summary, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                self._safe_stderr("No se pudo escribir resumen de blackbox")


class DeferredBlackBoxRecorder:
    def __init__(
        self,
        base_dir: str = "runtime/blackbox",
        max_events: int = 200,
        shutdown_snapshot: Callable[[], ShutdownSnapshot] | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._max_events = max_events
        self._shutdown_snapshot = shutdown_snapshot
        self._recorder: BlackBoxRecorder | None = None

    @property
    def started(self) -> bool:
        return self._recorder is not None

    @property
    def path(self) -> Path:
        if self._recorder is None:
            self.start()
        return self._recorder.path

    def start(self) -> None:
        if self._recorder is not None:
            return
        self._recorder = BlackBoxRecorder(
            base_dir=self._base_dir,
            max_events=self._max_events,
            shutdown_snapshot=self._shutdown_snapshot,
        )

    def record(self, event: str, **fields: Any) -> None:
        if self._recorder is None:
            return
        self._recorder.record(event, **fields)

    def dump_summary(self, shutdown: dict[str, Any]) -> None:
        if self._recorder is None:
            return
        self._recorder.dump_summary(shutdown)
