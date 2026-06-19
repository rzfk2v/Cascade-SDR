"""Pager mode — decode POCSAG/FLEX pager messages.

Pagers send short text/numeric messages as NBFM (POCSAG: 512/1200/2400 baud FSK;
FLEX: 1600–6400 baud). We don't demodulate them ourselves: we pipe ``rtl_fm``
(NBFM audio) into **multimon-ng**, read the messages it prints, parse them, and
forward a rolling feed to the browser — same idea as ACARS (a log, not a map).

Subprocess mode (``owns_device = False``): the piped ``rtl_fm`` owns the dongle;
:meth:`run` is cancelled on a mode switch and the process group is killed so the
dongle is freed. A frequency change (from the UI) breaks the inner read loop and
relaunches the pipe on the new channel, à la the ISM mode.

multimon-ng: ``brew install multimon-ng`` (rtl_fm ships with rtl-sdr). Set
``MULTIMON_BIN`` to override the binary path.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import time
from collections import deque

from app.modes.base import Mode

MAX_FEED = 200
AUDIO_RATE = 22_050   # multimon-ng's native input rate (raw S16LE mono)

# Pager channels worth watching. POCSAG is common on these in the EU; DAPNET is
# the amateur-radio POCSAG network. The UI picks one (rtl_fm tunes a single NBFM
# channel at a time).
CHANNELS = [
    (439_987_500.0, "DAPNET 439.9875 (ham POCSAG)"),
    (466_025_000.0, "466.025 (EU POCSAG)"),
    (466_075_000.0, "466.075 (EU POCSAG)"),
    (466_175_000.0, "466.175 (EU POCSAG)"),
    (153_350_000.0, "153.350 (VHF POCSAG)"),
]

# multimon-ng prints e.g.:
#   POCSAG1200: Address:  1234567  Function: 3  Alpha:   HELLO WORLD
#   POCSAG512: Address:   123456  Function: 0  Numeric: 12345
#   FLEX: 2009-... 1600/2/K  001.029.024  ALN  message text
_POCSAG = re.compile(
    r"(POCSAG\d+):\s*Address:\s*(\d+)\s*Function:\s*(\d+)\s*"
    r"(Alpha|Numeric|Skyper):\s*(.*)$"
)
_FLEX = re.compile(r"FLEX[:|].*?(\d{4,})\b.*?\b(ALN|NUM|ALPHA|TONE)\b\s*(.*)$")


def parse_pager(line: str) -> dict | None:
    """Parse one multimon-ng output line into a feed row, or None if not a message."""
    m = _POCSAG.search(line)
    if m:
        proto, addr, func, kind, text = m.groups()
        text = text.strip()
        if not text:
            return None
        return {"t": time.time(), "proto": proto, "addr": addr,
                "func": int(func), "kind": kind, "text": text}
    m = _FLEX.search(line)
    if m:
        capcode, kind, text = m.groups()
        text = text.strip()
        if not text:
            return None
        return {"t": time.time(), "proto": "FLEX", "addr": capcode,
                "kind": kind.title(), "text": text}
    return None


class PagerMode(Mode):
    name = "pager"
    owns_device = False
    default_center_freq = CHANNELS[0][0]

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.feed: deque[dict] = deque(maxlen=MAX_FEED)
        self._seen: set[tuple] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._freq = CHANNELS[0][0]
        self._restart = False
        self._dirty = False

    @staticmethod
    def _have_tools() -> tuple[str | None, str | None]:
        mm = os.environ.get("MULTIMON_BIN")
        if not (mm and os.access(mm, os.X_OK)):
            mm = shutil.which("multimon-ng")
        return shutil.which("rtl_fm"), mm

    def _cmd(self, multimon: str) -> str:
        gain = (f"-g {int(self.manager.gain)}"
                if isinstance(self.manager.gain, (int, float)) else "")
        ppm = (f"-p {int(self.manager.freq_correction)}"
               if self.manager.freq_correction else "")
        freq = int(self._freq)
        # rtl_fm: NBFM audio at 22050, squelch off (-l 0). multimon-ng: read raw
        # S16LE mono from stdin (-t raw -), decode the common POCSAG rates + FLEX.
        return (f"rtl_fm -f {freq} -M fm -s {AUDIO_RATE} -l 0 {gain} {ppm} - "
                f"| {multimon} -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -a FLEX "
                f"-f alpha -t raw -")

    def _config_msg(self) -> dict:
        return {
            "type": "pager_config",
            "channels": [{"freq": f, "label": lbl} for f, lbl in CHANNELS],
            "freq": self._freq,
        }

    def _label(self) -> str:
        for f, lbl in CHANNELS:
            if f == self._freq:
                return lbl
        return f"{self._freq / 1e6:.4f} MHz"

    def configure(self, params: dict) -> None:
        if params.get("freq") is not None:
            f = float(params["freq"])
            if f != self._freq:
                self._freq = f
                self._restart = True   # break the read loop and relaunch on the new channel

    def _announce_tuned(self) -> None:
        """Reflect the channel rtl_fm is actually on in the device status readout.

        We own no IQ worker (rtl_fm holds the dongle), so center_freq is just the
        displayed frequency — keep it in sync so it doesn't show the stale default.
        """
        self.manager.center_freq = self._freq
        self.manager.emit_json(self.manager.status())
        self.manager.emit_json({"type": "pager_status",
                                "message": f"multimon-ng running · {self._label()}"})

    async def run(self) -> None:
        rtl, multimon = self._have_tools()
        if rtl is None or multimon is None:
            missing = "rtl_fm" if rtl is None else "multimon-ng"
            self.manager.emit_json({
                "type": "error",
                "message": f"{missing} not found. Install multimon-ng "
                           "(brew install multimon-ng).",
            })
            return

        self.manager.emit_json(self._config_msg())
        retries = 0
        try:
            # (Re)spawn loop: a freq change in configure() breaks the inner loop,
            # kills the pipe, and comes back here to relaunch on the new channel.
            while True:
                self._restart = False
                self._proc = await asyncio.create_subprocess_shell(
                    self._cmd(multimon),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,    # own process group, so we can kill the pipe
                )
                err = self._watch_stderr(self._proc)
                out_tail: deque[str] = deque(maxlen=8)
                self._announce_tuned()
                assert self._proc.stdout is not None
                started = time.monotonic()
                last_emit = 0.0
                crashed = False
                while not self._restart:
                    if self._proc.returncode is not None:
                        crashed = True
                        break
                    try:
                        raw = await asyncio.wait_for(self._proc.stdout.readline(), 0.5)
                        if raw:
                            line = raw.decode(errors="ignore").strip()
                            if line:
                                out_tail.append(line)
                            self._on_line(line)
                    except asyncio.TimeoutError:
                        pass
                    now = time.monotonic()
                    if self._dirty and now - last_emit >= 0.5:
                        last_emit = now
                        self._dirty = False
                        self._emit()
                await self._kill_proc()
                self._cancel_stderr_watch()
                # Let librtlsdr fully release the USB device before relaunching —
                # otherwise the next rtl_fm hits 'usb_claim_interface error -6'
                # (device busy), which is the dongle error seen when switching
                # channels. This grace matters most on a Pi over USB.
                await asyncio.sleep(0.7)
                if crashed and not self._restart:
                    # Died on its own (often a transient busy dongle just after a
                    # retune). Retry a few times before giving up.
                    if time.monotonic() - started > 5.0:
                        retries = 0       # it ran fine for a while; fresh hiccup
                    retries += 1
                    if retries > 3:
                        raise RuntimeError(
                            self._exit_error("rtl_fm/multimon-ng", out_tail, err))
                    self.manager.emit_json({"type": "pager_status",
                                            "message": "restarting (dongle busy)…"})
                else:
                    retries = 0           # a clean user retune
        finally:
            await self._kill_proc()

    async def _kill_proc(self) -> None:
        self._cancel_stderr_watch()
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        except Exception:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except Exception:
                pass

    # --- message handling ---------------------------------------------------
    def _on_line(self, line: str) -> None:
        msg = parse_pager(line)
        if msg is None:
            return
        key = (msg["proto"], msg["addr"], msg["text"])
        if key in self._seen:      # multimon can repeat a message across batches
            return
        self._seen.add(key)
        if len(self._seen) > MAX_FEED * 2:
            self._seen.clear()
        self.feed.appendleft(msg)
        self._dirty = True

    def _feed_msg(self) -> dict:
        return {"type": "pager", "messages": list(self.feed), "count": len(self.feed)}

    def _emit(self) -> None:
        self.manager.emit_json(self._feed_msg())

    def snapshot(self) -> list[dict]:
        return [self._config_msg(), self._feed_msg()]
