// App shell: wires the WebSocket, mode tabs, tuning controls, waterfall,
// tuning overlay, and audio.

import { SdrSocket, FrameTag } from "./ws";
import { Waterfall } from "./waterfall";
import { SpectrumScope } from "./scope";
import { Tuner } from "./tuner";
import { AudioPlayer } from "./audio";

const sock = new SdrSocket();
const waterfall = new Waterfall(
  document.getElementById("waterfall") as HTMLCanvasElement,
);
const scope = new SpectrumScope(
  document.getElementById("scope") as HTMLCanvasElement,
);
const tuner = new Tuner(
  document.getElementById("overlay") as HTMLCanvasElement,
  document.getElementById("axis") as HTMLCanvasElement,
);
const audio = new AudioPlayer(48000);

const dot = document.getElementById("dot")!;
const connText = document.getElementById("conn-text")!;
const statusLine = document.getElementById("status-line")!;
const tunedLine = document.getElementById("tuned-line")!;
const freqInput = document.getElementById("freq") as HTMLInputElement;
const rateInput = document.getElementById("rate") as HTMLInputElement;
const radioControls = document.getElementById("radio-controls")!;
const demodSel = document.getElementById("demod") as HTMLSelectElement;
const bwInput = document.getElementById("bw") as HTMLInputElement;
const volInput = document.getElementById("vol") as HTMLInputElement;
const sqlInput = document.getElementById("sql") as HTMLInputElement;
const levelMeter = document.getElementById("level-meter")!;
const scanControls = document.getElementById("scan-controls")!;
const scanStart = document.getElementById("scan-start") as HTMLInputElement;
const scanStop = document.getElementById("scan-stop") as HTMLInputElement;
const scanPreset = document.getElementById("scan-preset") as HTMLSelectElement;

let currentMode = "idle";

// --- connection + status -------------------------------------------------
sock.onJson((msg) => {
  switch (msg.type) {
    case "_open":
      dot.classList.add("ok");
      connText.textContent = "connected";
      break;
    case "_close":
      dot.classList.remove("ok");
      connText.textContent = "reconnecting…";
      break;
    case "status":
      currentMode = msg.mode;
      renderStatus(msg);
      highlightMode(msg.mode);
      tuner.setBand(msg.center_freq, msg.sample_rate);
      tuner.setActive(msg.mode === "radio");
      radioControls.hidden = msg.mode !== "radio";
      scanControls.hidden = msg.mode !== "scan";
      break;
    case "spectrum_config":
      tuner.setBand(msg.center_freq, msg.sample_rate);
      break;
    case "radio_config":
      tuner.setTuned(msg.tuned_freq);
      tuner.setBandwidth(msg.bandwidth);
      demodSel.value = msg.demod;
      bwInput.value = Math.round(msg.bandwidth / 1000).toString();
      if (msg.squelch > -100) sqlInput.value = Math.round(msg.squelch).toString();
      tunedLine.textContent =
        `tuned ${(msg.tuned_freq / 1e6).toFixed(3)} MHz · ` +
        `${msg.demod.toUpperCase()} · BW ${(msg.bandwidth / 1e3).toFixed(0)} kHz`;
      break;
    case "radio_level":
      levelMeter.textContent = `${msg.db.toFixed(0)} dB ${msg.open ? "▶" : "🔇"}`;
      levelMeter.classList.toggle("open", msg.open);
      break;
    case "error":
      statusLine.textContent = `⚠ ${msg.message}`;
      statusLine.classList.add("err");
      break;
  }
});

sock.onBinary((tag, body) => {
  if (tag === FrameTag.FFT) {
    const row = new Float32Array(body.buffer, body.byteOffset, body.byteLength / 4);
    waterfall.pushRow(row);
    scope.pushRow(row);
  } else if (tag === FrameTag.AUDIO) {
    audio.pushInt16(body);
  }
});

function renderStatus(s: any): void {
  statusLine.classList.remove("err");
  const dev = s.device_present ? "device ✓" : "no device";
  const run = s.running ? "running" : "stopped";
  statusLine.textContent =
    `${dev} · ${run} · ${(s.center_freq / 1e6).toFixed(3)} MHz · ` +
    `${(s.sample_rate / 1e6).toFixed(2)} MS/s`;
  freqInput.value = (s.center_freq / 1e6).toString();
  rateInput.value = (s.sample_rate / 1e6).toString();
}

function highlightMode(mode: string): void {
  document.querySelectorAll("#mode-tabs button").forEach((b) => {
    b.classList.toggle("active", (b as HTMLElement).dataset.mode === mode);
  });
}

// --- mode tabs -----------------------------------------------------------
document.getElementById("mode-tabs")!.addEventListener("click", async (e) => {
  const btn = (e.target as HTMLElement).closest("button");
  if (!btn || (btn as HTMLButtonElement).disabled) return;
  const mode = btn.dataset.mode!;
  if (mode === "radio") await audio.init(); // user gesture: unlock audio
  if (mode !== "idle") {
    waterfall.clear();
    scope.clear();
  }
  sock.send({ cmd: "set_mode", mode });
});

// --- click / drag to tune ------------------------------------------------
tuner.onTune = async (freqHz) => {
  if (currentMode === "radio") {
    // tune within the currently captured band, no hardware retune
    sock.send({ cmd: "config", params: { tuned_freq: freqHz } });
    tuner.setTuned(freqHz);
  } else {
    // coming from waterfall/scan: re-center the dongle on the signal, then listen
    await audio.init();
    sock.send({ cmd: "set_mode", mode: "radio" });
    sock.send({ cmd: "tune", center_freq: freqHz });
    sock.send({ cmd: "config", params: { tuned_freq: freqHz } });
    tuner.setTuned(freqHz);
  }
};
tuner.onBandwidth = (bwHz, centerHz) => {
  if (currentMode === "radio") {
    sock.send({ cmd: "config", params: { tuned_freq: centerHz, bandwidth: bwHz } });
  }
  // in scan/waterfall a drag falls through to onTune(center) -> jump to radio
};

// --- radio controls ------------------------------------------------------
demodSel.addEventListener("change", () =>
  sock.send({ cmd: "config", params: { demod: demodSel.value } }),
);
bwInput.addEventListener("change", () =>
  sock.send({ cmd: "config", params: { bandwidth: parseFloat(bwInput.value) * 1000 } }),
);
volInput.addEventListener("input", () =>
  sock.send({ cmd: "config", params: { volume: parseFloat(volInput.value) } }),
);
sqlInput.addEventListener("input", () =>
  sock.send({ cmd: "config", params: { squelch: parseFloat(sqlInput.value) } }),
);

// --- scan controls -------------------------------------------------------
function applyScanRange(): void {
  sock.send({
    cmd: "config",
    params: {
      start_freq: parseFloat(scanStart.value) * 1e6,
      stop_freq: parseFloat(scanStop.value) * 1e6,
    },
  });
}
document.getElementById("scan-apply")!.addEventListener("click", applyScanRange);
scanPreset.addEventListener("change", () => {
  if (!scanPreset.value) return;
  const [a, b] = scanPreset.value.split(",");
  scanStart.value = a;
  scanStop.value = b;
  applyScanRange();
});

// --- dongle tuning -------------------------------------------------------
document.getElementById("apply")!.addEventListener("click", () => {
  sock.send({
    cmd: "tune",
    center_freq: parseFloat(freqInput.value) * 1e6,
    sample_rate: parseFloat(rateInput.value) * 1e6,
  });
});

sock.connect();
