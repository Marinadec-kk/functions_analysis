"""
Base classes and helpers shared by Tkinter applications in the refactored suite.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Optional

import tkinter as tk


class AppBase:
    """
    Base class that encapsulates worker-thread orchestration and safe interaction
    with Tkinter widgets via a UI queue.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.ui_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.root.after(100, self._drain_ui_queue)

    # ------------------------------------------------------------------ UI queue
    def _drain_ui_queue(self) -> None:
        try:
            while True:
                event, payload = self.ui_queue.get_nowait()
                self.handle_ui_event(event, payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def handle_ui_event(self, event: str, payload: Any) -> None:
        """
        Override in subclasses to respond to events emitted from worker threads.
        """
        raise NotImplementedError

    def post_ui_event(self, event: str, payload: Any = None) -> None:
        self.ui_queue.put((event, payload))

    # ----------------------------------------------------------------- threading
    def run_in_thread(self, target: Callable[..., Any], *args, daemon: bool = True, **kwargs) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            raise RuntimeError("Worker thread already running.")
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=target,
            args=args,
            kwargs=kwargs,
            daemon=daemon,
        )
        self._worker_thread.start()

    def stop_worker(self) -> None:
        self._stop_event.set()

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    # -------------------------------------------------------------- UI utilities
    def wait_for_worker(self, poll_interval_ms: int = 200) -> None:
        """Poll the worker thread until it finishes."""
        if not self._worker_thread:
            return
        if not self._worker_thread.is_alive():
            return
        self.root.after(poll_interval_ms, self.wait_for_worker, poll_interval_ms)

