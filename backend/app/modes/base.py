"""Mode interface.

A *mode* is one thing the dongle can do at a given moment (waterfall, FM radio,
ADS-B, ...). Because a single RTL-SDR has exactly one tuner, only one mode is
active at a time; the :class:`~app.device.DeviceManager` enforces that.

Two families of mode exist:

* **IQ modes** (``owns_device = True``): the backend opens the RTL-SDR in a
  dedicated worker thread and calls :meth:`process` with each block of raw IQ.
  These methods run *off* the event loop, so they may block on USB / heavy DSP.
  To send results to clients they call ``self.manager.emit_*`` (thread-safe).
  Examples: spectrum, radio.

* **Subprocess modes** (``owns_device = False``): an external decoder
  (``dump1090``, ``AIS-catcher``) opens the device; the mode spawns and reads it
  from an asyncio task. These implement the async :meth:`run`. Examples: adsb, ais.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.device import DeviceManager


class Mode:
    name: str = "base"
    owns_device: bool = True  # True -> worker-thread IQ reader; False -> async subprocess
    controls_tuning: bool = False  # True -> mode drives retuning itself (sweep), see scan
    #: When entering this mode, snap the dongle to ``default_center_freq`` /
    #: ``default_sample_rate``. Free-tuning views (spectrum, radio) set this False
    #: so they keep whatever band you were already on across a mode switch.
    resets_tuning: bool = True

    #: Default radio settings the DeviceManager applies when this mode starts.
    default_center_freq: float = 100_000_000.0
    default_sample_rate: float = 2_400_000.0

    #: Samples per IQ read block (IQ modes only). Must be a multiple of 512.
    block_size: int = 65_536

    def __init__(self, manager: "DeviceManager") -> None:
        self.manager = manager

    # --- IQ modes (run in the worker thread; may block) ---------------------
    def on_start(self) -> None:
        """Called once in the worker thread before the first read."""

    def process(self, samples: "np.ndarray") -> None:
        """Handle one block of complex IQ samples. Emit via ``manager.emit_*``."""

    def on_stop(self) -> None:
        """Called once in the worker thread after the read loop ends."""

    def snapshot(self) -> list[dict]:
        """Config messages a freshly-connected client needs to sync its UI."""
        return []

    # --- self-tuning modes (controls_tuning=True) ---------------------------
    def sweep(self, sdr, stop_event) -> None:
        """Own the device and drive retuning directly until ``stop_event`` is set.

        Used by modes that scan across frequencies (see ScanMode) instead of
        streaming a fixed band. Runs in the device worker thread.
        """

    # --- Subprocess modes (async) -------------------------------------------
    async def run(self) -> None:
        """Own the full lifetime of an external decoder (owns_device=False).

        Loops until cancelled, reading the decoder's output and pushing results
        through ``self.manager.hub``.
        """

    # --- Subprocess modes: diagnosable child failures -----------------------
    # Children are spawned with ``stderr=PIPE`` (never DEVNULL) so that when one
    # exits unexpectedly we can report *why* instead of a generic "exited". The
    # helpers below drain that stderr to the log and keep a rolling tail to fold
    # into the error surfaced to the UI.
    def _watch_stderr(self, proc: "asyncio.subprocess.Process",
                      lines: int = 8) -> "deque[str]":
        """Drain ``proc.stderr`` to the log and return a deque of its last lines.

        Starts a background task (cancelled via :meth:`_cancel_stderr_watch`)
        that reads stderr line by line, logs each at WARNING, and keeps the most
        recent ``lines`` in the returned deque for :meth:`_exit_error`.
        """
        tail: deque[str] = deque(maxlen=lines)
        log = logging.getLogger(f"sdr.{self.name}")

        async def drain() -> None:
            assert proc.stderr is not None
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode(errors="ignore").rstrip()
                if line:
                    tail.append(line)
                    log.warning("%s: %s", self.name, line)

        self._stderr_task = asyncio.ensure_future(drain())
        return tail

    def _cancel_stderr_watch(self) -> None:
        """Stop the stderr-drain task started by :meth:`_watch_stderr`, if any."""
        task = getattr(self, "_stderr_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._stderr_task = None

    @staticmethod
    def _exit_error(what: str, *tails: "deque[str]") -> str:
        """Build a diagnosable 'child exited' message from its captured output.

        Pass one or more tails (e.g. stderr, and for piped tools their stdout);
        their last lines are folded in so the real cause shows up in the log and
        the UI error instead of a generic hint.
        """
        detail = " / ".join(line for tail in tails for line in tail)
        if detail:
            return f"{what} exited — {detail}"
        return f"{what} exited — no output captured; is the dongle free and connected?"
