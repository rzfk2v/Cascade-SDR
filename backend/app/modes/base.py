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

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.device import DeviceManager


class Mode:
    name: str = "base"
    owns_device: bool = True  # True -> worker-thread IQ reader; False -> async subprocess
    controls_tuning: bool = False  # True -> mode drives retuning itself (sweep), see scan

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
