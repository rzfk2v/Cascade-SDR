// App shell: wires the WebSocket, mode tabs, tuning controls, waterfall,
// tuning overlay, and audio.

import { SdrSocket, FrameTag } from "./ws";
import { Waterfall } from "./waterfall";
import { SpectrumScope } from "./scope";
import { Tuner } from "./tuner";
import { AudioPlayer } from "./audio";
import { AdsbMap } from "./adsbmap";

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
const adsbMap = new AdsbMap();

const fftView = document.getElementById("fft-view")!;
const mapDiv = document.getElementById("map")!;
const adsbControls = document.getElementById("adsb-controls")!;
const adsbStatus = document.getElementById("adsb-status")!;
const adsbCount = document.getElementById("adsb-count")!;

const dot = document.getElementById("dot")!;
const connText = document.getElementById("conn-text")!;
const statusLine = document.getElementById("status-line")!;
const tunedLine = document.getElementById("tuned-line")!;
const freqInput = document.getElementById("freq") as HTMLInputElement;
const rateInput = document.getElementById("rate") as HTMLInputElement;
const gainAuto = document.getElementById("gain-auto") as HTMLInputElement;
const gainSlider = document.getElementById("gain") as HTMLInputElement;
const gainVal = document.getElementById("gain-val")!;
const ppmInput = document.getElementById("ppm") as HTMLInputElement;
const biasTee = document.getElementById("bias-tee") as HTMLInputElement;
let gainSteps: number[] = [];
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
const zoomOutBtn = document.getElementById("zoom-out") as HTMLButtonElement;

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
      syncGain(msg);
      if (typeof msg.ppm === "number" && document.activeElement !== ppmInput)
        ppmInput.value = msg.ppm.toString();
      if (typeof msg.bias_tee === "boolean") biasTee.checked = msg.bias_tee;
      highlightMode(msg.mode);
      tuner.setBand(msg.center_freq, msg.sample_rate);
      tuner.setActive(msg.mode === "radio");
      radioControls.hidden = msg.mode !== "radio";
      scanControls.hidden = msg.mode !== "scan";
      adsbControls.hidden = msg.mode !== "adsb";
      zoomOutBtn.hidden = !(msg.mode === "scan" || msg.mode === "spectrum");
      showAdsb(msg.mode === "adsb");
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
    case "adsb_status":
      adsbStatus.textContent = msg.message;
      break;
    case "aircraft":
      adsbMap.update(msg.aircraft);
      adsbCount.textContent = `${msg.positioned} shown · ${msg.count} tracked`;
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

function syncGain(s: any): void {
  if (Array.isArray(s.gains) && s.gains.length && s.gains.length !== gainSteps.length) {
    gainSteps = s.gains;
    gainSlider.max = (gainSteps.length - 1).toString();
  }
  const auto = s.gain === "auto" || s.gain == null;
  gainAuto.checked = auto;
  gainSlider.disabled = auto;
  if (auto) {
    gainVal.textContent = "auto";
  } else if (gainSteps.length) {
    // snap slider to the nearest known step
    let idx = gainSteps.findIndex((g) => g >= s.gain - 0.05);
    if (idx < 0) idx = gainSteps.length - 1;
    gainSlider.value = idx.toString();
    gainVal.textContent = `${gainSteps[idx]} dB`;
  } else {
    gainVal.textContent = `${s.gain} dB`;
  }
}

function showAdsb(on: boolean): void {
  fftView.hidden = on;
  mapDiv.hidden = !on;
  if (on) {
    adsbMap.ensure("map");
  } else {
    // canvases were display:none -> re-measure now that they're visible again
    requestAnimationFrame(layoutCanvases);
  }
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
tuner.onSelect = (loHz, hiHz) => {
  if (currentMode === "radio") {
    // drag sets the demod bandwidth + re-centers the channel
    sock.send({
      cmd: "config",
      params: { tuned_freq: (loHz + hiHz) / 2, bandwidth: hiHz - loHz },
    });
  } else if (currentMode === "scan") {
    // drag zooms the scan into the selected sub-range
    scanStart.value = (loHz / 1e6).toFixed(3);
    scanStop.value = (hiHz / 1e6).toFixed(3);
    waterfall.clear();
    scope.clear();
    sock.send({ cmd: "config", params: { start_freq: loHz, stop_freq: hiHz } });
  } else {
    // spectrum/idle: re-center the dongle and narrow the captured band
    const center = (loHz + hiHz) / 2;
    const rate = Math.min(2.4e6, Math.max(0.96e6, hiHz - loHz)); // RTL valid range
    waterfall.clear();
    scope.clear();
    sock.send({ cmd: "tune", center_freq: center, sample_rate: rate });
  }
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

// zoom out (×2) — widens the scan range or the captured band
zoomOutBtn.addEventListener("click", () => {
  const center = tuner.centerFreq;
  const span = tuner.sampleRate * 2;
  waterfall.clear();
  scope.clear();
  if (currentMode === "scan") {
    const lo = Math.max(0, center - span / 2);
    const hi = center + span / 2;
    scanStart.value = (lo / 1e6).toFixed(3);
    scanStop.value = (hi / 1e6).toFixed(3);
    sock.send({ cmd: "config", params: { start_freq: lo, stop_freq: hi } });
  } else if (currentMode === "spectrum") {
    sock.send({ cmd: "tune", sample_rate: Math.min(2.4e6, tuner.sampleRate * 2) });
  }
});

// --- dongle tuning -------------------------------------------------------
document.getElementById("apply")!.addEventListener("click", () => {
  sock.send({
    cmd: "tune",
    center_freq: parseFloat(freqInput.value) * 1e6,
    sample_rate: parseFloat(rateInput.value) * 1e6,
  });
});

// --- gain + ppm ----------------------------------------------------------
gainAuto.addEventListener("change", () => {
  if (gainAuto.checked) {
    gainSlider.disabled = true;
    gainVal.textContent = "auto";
    sock.send({ cmd: "tune", gain: "auto" });
  } else {
    gainSlider.disabled = false;
    const g = gainSteps[parseInt(gainSlider.value, 10)] ?? 0;
    gainVal.textContent = `${g} dB`;
    sock.send({ cmd: "tune", gain: g });
  }
});
gainSlider.addEventListener("input", () => {
  const g = gainSteps[parseInt(gainSlider.value, 10)] ?? 0;
  gainVal.textContent = `${g} dB`;
  sock.send({ cmd: "tune", gain: g });
});
ppmInput.addEventListener("change", () =>
  sock.send({ cmd: "tune", ppm: parseInt(ppmInput.value, 10) || 0 }),
);
biasTee.addEventListener("change", () =>
  sock.send({ cmd: "tune", bias_tee: biasTee.checked }),
);

// --- responsive canvas sizing -------------------------------------------
const scopeCanvas = document.getElementById("scope") as HTMLCanvasElement;
const wfCanvas = document.getElementById("waterfall") as HTMLCanvasElement;
const overlayCanvas = document.getElementById("overlay") as HTMLCanvasElement;
const axisCanvas = document.getElementById("axis") as HTMLCanvasElement;

function fit(c: HTMLCanvasElement): { w: number; h: number } {
  const r = c.getBoundingClientRect();
  return { w: Math.max(1, Math.round(r.width)), h: Math.max(1, Math.round(r.height)) };
}

function layoutCanvases(): void {
  const s = fit(scopeCanvas);
  scope.resize(s.w, s.h);
  const wf = fit(wfCanvas);
  waterfall.resize(wf.w, wf.h);
  const ov = fit(overlayCanvas);
  overlayCanvas.width = ov.w;
  overlayCanvas.height = ov.h;
  const ax = fit(axisCanvas);
  axisCanvas.width = ax.w;
  axisCanvas.height = ax.h;
  tuner.drawAxis();
  tuner.draw();
}

let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(layoutCanvases, 120);
});
// initial sizing after the flex layout settles
requestAnimationFrame(() => requestAnimationFrame(layoutCanvases));

sock.connect();
