"""DeviceManager — the single owner of the RTL-SDR dongle.

Responsibilities:
  * Hold the current radio settings (center frequency, sample rate, gain).
  * Track which :class:`~app.modes.base.Mode` is active (at most one).
  * For IQ modes, run a **dedicated worker thread** that opens the device and
    loops on the blocking ``read_samples`` call, feeding each block to the mode.
    Keeping USB I/O and DSP off the event loop is essential: librtlsdr's read and
    close calls block, and running them on the loop deadlocks WebSocket I/O.
  * For subprocess modes, run the mode's async ``run()`` coroutine.
  * Switch modes cleanly: fully tear down (and release the device) before the new
    pipeline touches it — dump1090/AIS-catcher and our own librtlsdr access can
    never hold the dongle at the same time.

Hardware is optional: if no dongle is present (or pyrtlsdr/librtlsdr is missing),
the manager still runs so the UI and WebSocket layer work; starting an IQ mode
simply reports an error back to clients.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from app.hub import FrameTag, Hub
from app.modes.base import Mode

log = logging.getLogger("sdr.device")

try:
    from rtlsdr import RtlSdr  # type: ignore
    _HAVE_RTLSDR = True
except Exception:  # pragma: no cover - import guarded for hardware-less dev
    RtlSdr = None  # type: ignore
    _HAVE_RTLSDR = False


class DeviceManager:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.center_freq: float = 100_000_000.0
        self.sample_rate: float = 2_400_000.0
        self.gain: float | str = "auto"
        self.mode: Optional[Mode] = None
        self.mode_name: str = "idle"
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()
        # IQ-mode worker thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._retune_event = threading.Event()
        self._sdr = None
        # subprocess-mode task
        self._task: Optional[asyncio.Task] = None

    # --- introspection ------------------------------------------------------
    @property
    def running(self) -> bool:
        thread_alive = self._thread is not None and self._thread.is_alive()
        task_alive = self._task is not None and not self._task.done()
        return thread_alive or task_alive

    def status(self) -> dict:
        return {
            "type": "status",
            "mode": self.mode_name,
            "running": self.running,
            "center_freq": self.center_freq,
            "sample_rate": self.sample_rate,
            "gain": self.gain,
            "device_present": self.device_present(),
            "clients": self.hub.client_count,
        }

    @staticmethod
    def device_present() -> bool:
        if not _HAVE_RTLSDR:
            return False
        try:
            serials = RtlSdr.get_device_serial_addresses()  # type: ignore[attr-defined]
            return len(serials) > 0
        except Exception:
            return True  # enumeration helper missing on some builds; assume maybe

    # --- thread-safe emit helpers (callable from the worker thread) ---------
    def emit_json(self, message: dict[str, Any]) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.hub.broadcast_json(message), self._loop)

    def emit_binary(self, tag: FrameTag, payload: bytes) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self.hub.broadcast_binary(tag, payload), self._loop
            )

    # --- control ------------------------------------------------------------
    async def set_mode(self, name: str, registry: dict[str, type[Mode]]) -> None:
        if name not in registry and name != "idle":
            raise ValueError(f"unknown mode: {name}")
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            await self._stop_locked()
            self.mode_name = name
            if name == "idle":
                self.mode = None
                await self._announce()
                return
            mode = registry[name](self)
            self.center_freq = mode.default_center_freq
            self.sample_rate = mode.default_sample_rate
            self.mode = mode
            await self._start_locked()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            if self.mode is None:
                await self.hub.broadcast_json(
                    {"type": "error", "message": "No mode selected."}
                )
                return
            await self._start_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
            await self._announce()

    async def retune(self, center_freq: float | None = None,
                     sample_rate: float | None = None,
                     gain: float | str | None = None) -> None:
        """Update radio settings. The worker applies them between read blocks."""
        if center_freq is not None:
            self.center_freq = float(center_freq)
        if sample_rate is not None:
            self.sample_rate = float(sample_rate)
        if gain is not None:
            self.gain = gain
        self._retune_event.set()
        await self._announce()

    def configure_mode(self, params: dict) -> None:
        """Pass mode-specific settings (tuned freq, bandwidth, demod, ...) through.

        Safe to call while an IQ mode is streaming: the mode stores plain
        attributes that its ``process`` reads on the next block.
        """
        mode = self.mode
        if mode is not None and hasattr(mode, "configure"):
            mode.configure(params)

    def mode_snapshot(self) -> list[dict]:
        """Config messages a freshly-connected client needs to sync its UI."""
        return self.mode.snapshot() if self.mode is not None else []

    # --- internals (call with lock held) -----------------------------------
    async def _start_locked(self) -> None:
        if self.mode is None or self.running:
            return
        if self.mode.owns_device:
            self._stop_event.clear()
            self._retune_event.clear()
            self._thread = threading.Thread(
                target=self._iq_worker, args=(self.mode,), daemon=True
            )
            self._thread.start()
        else:
            self._task = asyncio.create_task(self._subprocess_loop(self.mode))
        await self._announce()

    async def _stop_locked(self) -> None:
        # Stop the IQ worker thread without blocking the event loop.
        if self._thread is not None:
            self._stop_event.set()
            thread, self._thread = self._thread, None
            assert self._loop is not None
            await self._loop.run_in_executor(None, thread.join, 5.0)
            if thread.is_alive():
                log.warning("IQ worker did not stop within timeout")
        # Stop a subprocess mode.
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._sdr = None

    def _apply_settings(self, sdr) -> None:
        sdr.sample_rate = self.sample_rate
        sdr.center_freq = self.center_freq
        try:
            sdr.gain = self.gain  # pyrtlsdr accepts 'auto' or a float
        except Exception:
            sdr.gain = 0.0

    def _iq_worker(self, mode: Mode) -> None:
        """Owns the device for this mode's lifetime, via two threads:

        * a *reader* that does nothing but call ``read_samples`` back-to-back so
          the device buffer stays drained (no dropped samples) — this thread must
          not be slowed by DSP, or USB overflows and audio gets gappy;
        * this *processor* thread, which pulls IQ blocks off the queue and runs
          ``mode.process`` (DSP is ~15% of real-time, so it keeps up easily).

        The queue is bounded and drops the oldest block if the processor ever
        falls behind, keeping latency bounded.
        """
        if not _HAVE_RTLSDR:
            self.emit_json({
                "type": "error",
                "message": "pyrtlsdr/librtlsdr not available on this machine.",
            })
            return
        import queue as _queue

        q: "_queue.Queue" = _queue.Queue(maxsize=8)
        sdr = None

        def reader() -> None:
            try:
                while not self._stop_event.is_set():
                    if self._retune_event.is_set():
                        self._retune_event.clear()
                        self._apply_settings(sdr)
                    block = sdr.read_samples(mode.block_size)
                    try:
                        q.put_nowait(block)
                    except _queue.Full:
                        try:
                            q.get_nowait()  # drop oldest, keep latency bounded
                        except _queue.Empty:
                            pass
                        try:
                            q.put_nowait(block)
                        except _queue.Full:
                            pass
            except Exception as exc:  # device error in the reader
                self._stop_event.set()
                self.emit_json({"type": "error", "message": f"Device error: {exc}"})

        reader_thread = None
        try:
            sdr = RtlSdr()  # type: ignore[operator]
            self._apply_settings(sdr)
            self._sdr = sdr
            mode.on_start()
            if mode.controls_tuning:
                # Mode drives retuning itself (e.g. scan); no separate reader.
                mode.sweep(sdr, self._stop_event)
            else:
                reader_thread = threading.Thread(target=reader, daemon=True)
                reader_thread.start()
                while not self._stop_event.is_set():
                    try:
                        block = q.get(timeout=0.2)
                    except _queue.Empty:
                        continue
                    mode.process(block)
        except Exception as exc:
            log.exception("IQ worker error")
            self.emit_json({"type": "error", "message": f"Device error: {exc}"})
        finally:
            self._stop_event.set()
            if reader_thread is not None:
                reader_thread.join(timeout=2.0)
            try:
                mode.on_stop()
            except Exception:
                pass
            if sdr is not None:
                try:
                    sdr.close()
                except Exception:
                    pass
            self._sdr = None

    async def _subprocess_loop(self, mode: Mode) -> None:
        try:
            await mode.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("subprocess loop error")
            await self.hub.broadcast_json(
                {"type": "error", "message": f"Decoder error: {exc}"}
            )

    async def _announce(self) -> None:
        await self.hub.broadcast_json(self.status())
