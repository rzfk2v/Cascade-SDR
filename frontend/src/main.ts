// App shell: wires the WebSocket, mode tabs, tuning controls, waterfall,
// tuning overlay, and audio.

import { SdrSocket, FrameTag } from "./ws";
import { Waterfall } from "./waterfall";
import { SpectrumScope } from "./scope";
import { Tuner } from "./tuner";
import { AudioPlayer } from "./audio";
import {
  AdsbMap,
  flagEmoji,
  vsArrow,
  type Aircraft,
  type Vessel,
  type Station,
} from "./adsbmap";
import {
  loadSettings,
  saveSettings,
  loadBookmarks,
  saveBookmarks,
  type Bookmark,
} from "./storage";
import { bandAt, bandsInSpan } from "./bands";
import { antennaText } from "./antenna";
import { WavRecorder, downloadBlob } from "./recorder";
import { AptImage } from "./aptimage";
import { SstvImage } from "./sstvimage";

const BASE = import.meta.env.BASE_URL || "/";
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
// keep waterfall + scope display-zoom in sync with the tuner's zoom window
tuner.onViewChange = (lo, hi) => {
  waterfall.setView(lo, hi);
  scope.setView(lo, hi);
};
const wavRec = new WavRecorder(48000);
const adsbMap = new AdsbMap();

const fftView = document.getElementById("fft-view")!;
const mapDiv = document.getElementById("map")!;
const adsbControls = document.getElementById("adsb-controls")!;
const adsbStatus = document.getElementById("adsb-status")!;
const adsbCount = document.getElementById("adsb-count")!;
const adsbRoutes = document.getElementById("adsb-routes") as HTMLInputElement;
adsbRoutes.addEventListener("change", () =>
  sock.send({ cmd: "config", params: { routes: adsbRoutes.checked } }),
);
const aircraftPanel = document.getElementById("aircraft-panel")!;
const apBody = document.getElementById("ap-body")!;
const apCount = document.getElementById("ap-count")!;
const apTitle = document.getElementById("ap-title")!;
const apHead = document.getElementById("ap-head")!;
const rxLoc = document.getElementById("rx-loc") as HTMLInputElement;
const locationControls = document.getElementById("location-controls")!;
const rxLocate = document.getElementById("rx-locate") as HTMLButtonElement;
const rxLocStatus = document.getElementById("rx-loc-status")!;
const mapOptions = document.getElementById("map-options")!;
const showTracks = document.getElementById("show-tracks") as HTMLInputElement;
const aisControls = document.getElementById("ais-controls")!;
const aisStatus = document.getElementById("ais-status")!;
const aisCount = document.getElementById("ais-count")!;
const aisNote = document.getElementById("ais-note")!;
const aisNoteTarget = document.getElementById("ais-note-target")!;
const aisNoteInput = document.getElementById("ais-note-input") as HTMLInputElement;
const aisNoteSave = document.getElementById("ais-note-save") as HTMLButtonElement;
let noteMmsi: string | null = null;
const aprsControls = document.getElementById("aprs-controls")!;
const aprsStatus = document.getElementById("aprs-status")!;
const aprsCount = document.getElementById("aprs-count")!;
const acarsControls = document.getElementById("acars-controls")!;
const acarsStatus = document.getElementById("acars-status")!;
const acarsCount = document.getElementById("acars-count")!;
const acarsView = document.getElementById("acars-view")!;
const acarsLog = document.getElementById("acars-log")!;
const acarsFeedCount = document.getElementById("acars-feedcount")!;
const ismControls = document.getElementById("ism-controls")!;
const ismStatus = document.getElementById("ism-status")!;
const ismCount = document.getElementById("ism-count")!;
const ismView = document.getElementById("ism-view")!;
const ismLog = document.getElementById("ism-log")!;
const ismFeedCount = document.getElementById("ism-feedcount")!;
const ismFilterSel = document.getElementById("ism-filter") as HTMLSelectElement;
const ismBandSel = document.getElementById("ism-band") as HTMLSelectElement;
const aptControls = document.getElementById("apt-controls")!;
const aptStatus = document.getElementById("apt-status")!;
const aptSat = document.getElementById("apt-sat") as HTMLSelectElement;
const aptView = document.getElementById("apt-view")!;
const replayApt = document.getElementById("replay-apt") as HTMLInputElement;
const aptImage = new AptImage(document.getElementById("apt-canvas") as HTMLCanvasElement);
let aptLineCount = 0;
const sstvControls = document.getElementById("sstv-controls")!;
const sstvStatus = document.getElementById("sstv-status")!;
const sstvView = document.getElementById("sstv-view")!;
const sstvImage = new SstvImage(document.getElementById("sstv-canvas") as HTMLCanvasElement);
let sstvRowCount = 0;
const pagerControls = document.getElementById("pager-controls")!;
const pagerStatus = document.getElementById("pager-status")!;
const pagerCount = document.getElementById("pager-count")!;
const pagerView = document.getElementById("pager-view")!;
const pagerLog = document.getElementById("pager-log")!;
const pagerFeedCount = document.getElementById("pager-feedcount")!;
const pagerChannel = document.getElementById("pager-channel") as HTMLSelectElement;
const pagerFreq = document.getElementById("pager-freq") as HTMLInputElement;
const dabControls = document.getElementById("dab-controls")!;
const dabChannel = document.getElementById("dab-channel") as HTMLSelectElement;
const dabStatus = document.getElementById("dab-status")!;
const dabEnsembleSide = document.getElementById("dab-ensemble")!;
const dabView = document.getElementById("dab-view")!;
const dabEnsName = document.getElementById("dab-ens-name")!;
const dabNow = document.getElementById("dab-now")!;
const dabStationsEl = document.getElementById("dab-stations")!;
const dabAudio = document.getElementById("dab-audio") as HTMLAudioElement;
let dabPlayingSid: number | null = null;

// populate Band III blocks (5A..12D, 13A..13F)
(() => {
  const blocks: string[] = [];
  for (let n = 5; n <= 12; n++) for (const L of "ABCD") blocks.push(`${n}${L}`);
  for (const L of "ABCDEF") blocks.push(`13${L}`);
  const STHLM: Record<string, string> = {
    "12A": " — Stockholm",
    "12C": " — Stockholm (SR)",
    "12D": " — Stockholm",
  };
  dabChannel.innerHTML = blocks
    .map(
      (b) =>
        `<option value="${b}"${b === "12C" ? " selected" : ""}>${b}${STHLM[b] || ""}</option>`,
    )
    .join("");
})();

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
let desiredGainDb = 0; // last manual gain (dB), used before the step list arrives
const radioControls = document.getElementById("radio-controls")!;
const replayControls = document.getElementById("replay-controls")!;
const replayStatus = document.getElementById("replay-status")!;
const replayList = document.getElementById("replay-list")!;
let replayFile: string | null = null;
const demodSel = document.getElementById("demod") as HTMLSelectElement;
const deemphSel = document.getElementById("deemph") as HTMLSelectElement;
const stereoOn = document.getElementById("stereo-on") as HTMLInputElement;
const rdsOn = document.getElementById("rds-on") as HTMLInputElement;
const rdsBox = document.getElementById("rds-box")!;
const rdsPs = document.getElementById("rds-ps")!;
const rdsRt = document.getElementById("rds-rt")!;
const rdsMeta = document.getElementById("rds-meta")!;
const bwInput = document.getElementById("bw") as HTMLInputElement;
const volInput = document.getElementById("vol") as HTMLInputElement;
const sqlInput = document.getElementById("sql") as HTMLInputElement;
const levelMeter = document.getElementById("level-meter")!;
const cwText = document.getElementById("cw-text")!;
const cwOut = document.getElementById("cw-out")!;
const scanControls = document.getElementById("scan-controls")!;
const scanStart = document.getElementById("scan-start") as HTMLInputElement;
const scanStop = document.getElementById("scan-stop") as HTMLInputElement;
const scanPreset = document.getElementById("scan-preset") as HTMLSelectElement;
const scannerControls = document.getElementById("scanner-controls")!;
const scannerPreset = document.getElementById("scanner-preset") as HTMLSelectElement;
const scannerSql = document.getElementById("scanner-sql") as HTMLInputElement;
const scannerVol = document.getElementById("scanner-vol") as HTMLInputElement;
const scannerStatus = document.getElementById("scanner-status")!;
const scannerPrio = document.getElementById("scanner-prio") as HTMLSelectElement;
const scannerEditList = document.getElementById("scanner-edit-list")!;
const scannerAdd = document.getElementById("scanner-add")!;
const scannerName = document.getElementById("scanner-name") as HTMLInputElement;
const scannerSave = document.getElementById("scanner-save")!;
const scannerDel = document.getElementById("scanner-del")!;
const scannerView = document.getElementById("scanner-view")!;
const scannerNow = document.getElementById("scanner-now")!;
const scannerGrid = document.getElementById("scanner-grid")!;
type ScanChan = { label: string; mhz: number; demod: string };
let scannerChannels: ScanChan[] = [];
let editChannels: ScanChan[] = [];   // working copy for the channel editor
let scannerPresetId = "";            // current preset id (built-in key or custom name)
const zoomOutBtn = document.getElementById("zoom-out") as HTMLButtonElement;
const displayControls = document.getElementById("display-controls")!;
const tuningControls = document.getElementById("tuning-controls")!;
const receptionControls = document.getElementById("reception-controls")!;
const recordingControls = document.getElementById("recording-controls")!;
const wfAuto = document.getElementById("wf-auto") as HTMLInputElement;
const wfFloor = document.getElementById("wf-floor") as HTMLInputElement;
const wfCeil = document.getElementById("wf-ceil") as HTMLInputElement;
const peakHold = document.getElementById("peak-hold") as HTMLInputElement;
const averaging = document.getElementById("averaging") as HTMLSelectElement;
const bmName = document.getElementById("bm-name") as HTMLInputElement;
const bmList = document.getElementById("bm-list")!;
const bandInfo = document.getElementById("band-info")!;
const antennaInfo = document.getElementById("antenna-info")!;
let viewCenter = 100e6;
let viewRate = 2.4e6;
let viewTuned = 100e6;

function updateBandInfo(): void {
  let label = "";
  if (currentMode === "adsb") {
    label = "ADS-B · 1090 MHz (Mode S)";
  } else if (currentMode === "ais") {
    label = "Marine AIS · 162 MHz";
  } else if (currentMode === "aprs") {
    label = "APRS · 144.800 MHz (packet)";
  } else if (currentMode === "acars") {
    label = "ACARS · ~131 MHz (aircraft data)";
  } else if (currentMode === "apt") {
    label = "NOAA APT · 137 MHz (weather sat)";
  } else if (currentMode === "sstv") {
    label = "SSTV · 144.500 MHz (slow-scan TV)";
  } else if (currentMode === "pager") {
    label = "Pager · POCSAG/FLEX";
  } else if (currentMode === "ism") {
    label = `ISM · ${ismFreqMhz} MHz (sensors)`;
  } else if (currentMode === "radio" || currentMode === "replay") {
    const b = bandAt(viewTuned / 1e6);
    label = b ? `Band: ${b}` : "";
  } else if (currentMode === "spectrum" || currentMode === "scan") {
    const names = bandsInSpan(
      (viewCenter - viewRate / 2) / 1e6,
      (viewCenter + viewRate / 2) / 1e6,
    );
    label = names.length ? `Band: ${names.slice(0, 4).join(" · ")}` : "";
  }
  bandInfo.textContent = label;
  const fMHz =
    (currentMode === "radio" || currentMode === "replay" ? viewTuned : viewCenter) / 1e6;
  antennaInfo.textContent = antennaText(fMHz);
}

let currentMode = "idle";

// RDS program-type names (EU RDS table, 0..31)
const PTY = [
  "None", "News", "Current affairs", "Info", "Sport", "Education", "Drama",
  "Culture", "Science", "Varied", "Pop music", "Rock music", "Easy listening",
  "Light classical", "Serious classical", "Other music", "Weather", "Finance",
  "Children", "Social affairs", "Religion", "Phone-in", "Travel", "Leisure",
  "Jazz", "Country", "National music", "Oldies", "Folk music", "Documentary",
  "Alarm test", "Alarm",
];

// --- connection + status -------------------------------------------------
sock.onJson((msg) => {
  switch (msg.type) {
    case "_open":
      dot.classList.add("ok");
      connText.textContent = "connected";
      // push persisted device settings so ppm/gain/bias-T stick across reloads
      sock.send({
        cmd: "tune",
        ppm: parseInt(ppmInput.value, 10) || 0,
        bias_tee: biasTee.checked,
        gain: gainAuto.checked ? "auto" : desiredGainDb,
      });
      break;
    case "_close":
      dot.classList.remove("ok");
      connText.textContent = "reconnecting…";
      break;
    case "status":
      currentMode = msg.mode;
      viewCenter = msg.center_freq;
      viewRate = msg.sample_rate;
      updateBandInfo();
      renderStatus(msg);
      syncGain(msg);
      if (typeof msg.ppm === "number" && document.activeElement !== ppmInput)
        ppmInput.value = msg.ppm.toString();
      if (typeof msg.bias_tee === "boolean") biasTee.checked = msg.bias_tee;
      highlightMode(msg.mode);
      tuner.setBand(msg.center_freq, msg.sample_rate);
      tuner.setActive(msg.mode === "radio" || msg.mode === "replay");
      radioControls.hidden = !(msg.mode === "radio" || msg.mode === "replay");
      replayControls.hidden = msg.mode !== "replay";
      scanControls.hidden = msg.mode !== "scan";
      scannerControls.hidden = msg.mode !== "scanner";
      adsbControls.hidden = msg.mode !== "adsb";
      aisControls.hidden = msg.mode !== "ais";
      aprsControls.hidden = msg.mode !== "aprs";
      // the location field + track toggle apply to every map mode
      locationControls.hidden = !["adsb", "ais", "aprs"].includes(msg.mode);
      mapOptions.hidden = !["adsb", "ais", "aprs"].includes(msg.mode);
      acarsControls.hidden = msg.mode !== "acars";
      ismControls.hidden = msg.mode !== "ism";
      aptControls.hidden = msg.mode !== "apt";
      sstvControls.hidden = msg.mode !== "sstv";
      pagerControls.hidden = msg.mode !== "pager";
      dabControls.hidden = msg.mode !== "dab";
      zoomOutBtn.hidden = !["scan", "spectrum", "radio", "replay"].includes(msg.mode);
      displayControls.hidden = !["spectrum", "scan", "radio", "replay"].includes(msg.mode);
      // Center/sample-rate only apply to the free-tune spectrum view; decoder
      // modes self-tune. Gain/PPM/Bias-T affect reception in every running mode.
      tuningControls.hidden = !["spectrum", "radio"].includes(msg.mode);
      receptionControls.hidden = msg.mode === "idle";
      // IQ capture needs a fixed-tuned IQ mode (not scan/replay/decoders).
      recordingControls.hidden = !["spectrum", "radio", "apt", "sstv"].includes(msg.mode);
      showView(msg.mode);
      break;
    case "spectrum_config":
      viewCenter = msg.center_freq;
      viewRate = msg.sample_rate;
      updateBandInfo();
      tuner.setBand(msg.center_freq, msg.sample_rate);
      break;
    case "radio_config":
      viewTuned = msg.tuned_freq;
      updateBandInfo();
      tuner.setTuned(msg.tuned_freq);
      tuner.setBandwidth(msg.bandwidth);
      demodSel.value = msg.demod;
      if (msg.deemph) deemphSel.value = String(Math.round(msg.deemph));
      if (typeof msg.rds === "boolean") rdsOn.checked = msg.rds;
      if (typeof msg.stereo === "boolean") stereoOn.checked = msg.stereo;
      rdsBox.hidden = !(msg.demod === "wfm" && rdsOn.checked);
      if (msg.demod !== "wfm") { rdsPs.textContent = "—"; rdsRt.textContent = ""; rdsMeta.textContent = ""; }
      bwInput.value = Math.round(msg.bandwidth / 1000).toString();
      cwText.hidden = msg.demod !== "cw";
      if (msg.demod !== "cw") cwOut.textContent = "";
      if (msg.squelch > -100) sqlInput.value = Math.round(msg.squelch).toString();
      tunedLine.textContent =
        `tuned ${(msg.tuned_freq / 1e6).toFixed(3)} MHz · ` +
        `${msg.demod.toUpperCase()} · BW ${(msg.bandwidth / 1e3).toFixed(0)} kHz`;
      break;
    case "replay_status":
      replayFile = msg.file;
      replayStatus.textContent = msg.file
        ? `${msg.playing ? "▶" : "⏸"} ${msg.file.replace("iq_", "").replace(".cu8", "")}`
        : "pick a recording…";
      renderReplayList();
      break;
    case "radio_level":
      levelMeter.textContent =
        `${msg.db.toFixed(0)} dB ${msg.open ? "▶" : "🔇"}${msg.stereo ? " ◖◗ stereo" : ""}`;
      levelMeter.classList.toggle("open", msg.open);
      break;
    case "adsb_status":
      adsbStatus.textContent = msg.message;
      break;
    case "adsb_config":
      adsbRoutes.checked = !!msg.routes;
      break;
    case "cw_text":
      cwOut.textContent = (cwOut.textContent + msg.text).slice(-400);
      cwText.scrollTop = cwText.scrollHeight;
      break;
    case "rds":
      rdsPs.textContent = msg.ps || "—";
      rdsRt.textContent = msg.rt || "";
      rdsMeta.textContent = [
        msg.pi ? `PI ${msg.pi}` : "",
        msg.pty != null ? PTY[msg.pty] || `PTY ${msg.pty}` : "",
      ].filter(Boolean).join(" · ");
      break;
    case "aircraft":
      lastAircraft = msg.aircraft;
      adsbMap.update(msg.aircraft);
      adsbCount.textContent = `${msg.positioned} shown · ${msg.count} tracked`;
      renderAircraftList(msg.aircraft);
      break;
    case "ais_status":
      aisStatus.textContent = msg.message;
      break;
    case "vessels":
      lastVessels = msg.vessels;
      adsbMap.updateVessels(msg.vessels);
      aisCount.textContent = `${msg.positioned} shown · ${msg.count} tracked`;
      renderVesselList(msg.vessels);
      break;
    case "aprs_status":
      aprsStatus.textContent = msg.message;
      break;
    case "stations":
      lastStations = msg.stations;
      adsbMap.updateStations(msg.stations);
      aprsCount.textContent = `${msg.positioned} shown · ${msg.count} tracked`;
      renderStationList(msg.stations);
      break;
    case "acars_status":
      acarsStatus.textContent = msg.message;
      break;
    case "acars":
      renderAcars(msg.messages);
      acarsCount.textContent = `${msg.count} messages`;
      break;
    case "sstv_start":
      sstvImage.start(msg.mode, msg.width, msg.height);
      sstvRowCount = 0;
      sstvStatus.textContent = `▶ ${msg.mode} · ${msg.width}×${msg.height}`;
      break;
    case "pager_config":
      renderPagerChannels(msg.channels || [], msg.freq);
      break;
    case "pager_status":
      pagerStatus.textContent = msg.message;
      break;
    case "pager":
      renderPager(msg.messages);
      pagerCount.textContent = `${msg.count} messages`;
      break;
    case "ism_config":
      renderIsmBands(msg);
      break;
    case "ism_status":
      ismStatus.textContent = msg.message;
      break;
    case "ism":
      renderIsm(msg.devices);
      ismCount.textContent = `${msg.count} device${msg.count === 1 ? "" : "s"}`;
      break;
    case "dab_status":
      dabStatus.textContent = msg.message;
      break;
    case "dab_ensemble":
      renderDab(msg);
      break;
    case "scanner_config":
      scannerChannels = msg.channels || [];
      renderScannerPresets(msg.presets || [], msg.preset);
      if (typeof msg.squelch === "number" && document.activeElement !== scannerSql)
        scannerSql.value = String(msg.squelch);
      renderScannerPrio(msg.priority || "");
      // Reload the editor's working copy only when the preset actually changes,
      // so a squelch/priority push doesn't wipe edits in progress.
      if (msg.preset !== scannerPresetId) {
        scannerPresetId = msg.preset;
        editChannels = scannerChannels.map((c) => ({ ...c }));
        scannerName.value = msg.builtin ? "" : msg.preset;
        scannerDel.hidden = !!msg.builtin;
        renderScannerEditor();
      }
      renderScannerGrid();
      break;
    case "scanner_state":
      updateScannerState(msg);
      break;
    case "rec_status":
      iqRecording = msg.recording;
      recIqBtn.textContent = iqRecording ? "■ Stop IQ recording" : "● Record IQ";
      if (msg.message) recIqStatus.textContent = msg.message;
      else if (iqRecording) recIqStatus.textContent = "● recording IQ…";
      else if (msg.stopped) recIqStatus.textContent = `saved ${msg.stopped}`;
      else recIqStatus.textContent = "";
      refreshRecordings();
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
  } else if (tag === FrameTag.APT) {
    aptImage.pushLine(new Uint8Array(body.buffer, body.byteOffset, body.byteLength));
    aptLineCount++;
    aptStatus.textContent = `▶ decoding · ${aptLineCount} lines`;
  } else if (tag === FrameTag.SSTV) {
    sstvImage.pushRow(new Uint8Array(body.buffer, body.byteOffset, body.byteLength));
    sstvRowCount++;
    const label = sstvImage.modeLabel();
    sstvStatus.textContent = `▶ ${label} · ${sstvRowCount} rows`;
  } else if (tag === FrameTag.AUDIO) {
    audio.pushInt16(body);                       // interleaved L,R stereo
    if (wavRec.recording) {
      // downmix to mono for the WAV recording
      const st = new Int16Array(body.buffer, body.byteOffset, body.byteLength >> 1);
      const mono = new Int16Array(st.length >> 1);
      for (let i = 0; i < mono.length; i++) mono[i] = (st[2 * i] + st[2 * i + 1]) >> 1;
      wavRec.addPcm(mono);
    }
  }
});

function renderStatus(s: any): void {
  statusLine.classList.remove("err");
  const dev = s.device_present ? "device ✓" : "no device";
  const run = s.running ? "running" : "stopped";
  statusLine.textContent =
    `${dev} · ${run} · ${(s.center_freq / 1e6).toFixed(3)} MHz · ` +
    `${(s.sample_rate / 1e6).toFixed(2)} MS/s`;
  // don't clobber a value the user is mid-edit (these now tune on change)
  if (document.activeElement !== freqInput)
    freqInput.value = (s.center_freq / 1e6).toString();
  if (document.activeElement !== rateInput)
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

function showView(mode: string): void {
  const isMap = mode === "adsb" || mode === "ais" || mode === "aprs";
  const isDab = mode === "dab";
  const isAcars = mode === "acars";
  const isIsm = mode === "ism";
  const isApt = mode === "apt" || (mode === "replay" && replayApt.checked);
  const isSstv = mode === "sstv";
  const isPager = mode === "pager";
  const isScanner = mode === "scanner";
  const isFft =
    !isMap && !isDab && !isAcars && !isIsm && !isApt && !isSstv && !isPager &&
    !isScanner; // radio/sweep/idle
  fftView.hidden = !isFft;
  mapDiv.hidden = !isMap;
  aircraftPanel.hidden = !isMap;
  dabView.hidden = !isDab;
  acarsView.hidden = !isAcars;
  ismView.hidden = !isIsm;
  aptView.hidden = !isApt;
  sstvView.hidden = !isSstv;
  pagerView.hidden = !isPager;
  scannerView.hidden = !isScanner;
  if (isApt || isSstv) zoomOutBtn.hidden = true;
  if (isApt) requestAnimationFrame(() => {
    const r = aptView.getBoundingClientRect();
    aptImage.resize(Math.round(r.width), Math.round(r.height));
  });
  if (isSstv) requestAnimationFrame(() => {
    const r = sstvView.getBoundingClientRect();
    sstvImage.resize(Math.round(r.width), Math.round(r.height));
  });
  if (isMap) {
    adsbMap.ensure("map");
    adsbMap.setTracksVisible(showTracks.checked);
    const rx = rxLatLon();             // show the blue "you are here" dot
    if (rx) adsbMap.setHome(rx[0], rx[1]);
    else adsbMap.clearHome();
    apTitle.textContent =
      mode === "ais" ? "Vessels" : mode === "aprs" ? "Stations" : "Aircraft";
    // clear the two layers that aren't active in this mode
    if (mode !== "adsb") adsbMap.clearAircraft();
    if (mode !== "ais") adsbMap.clearVessels();
    if (mode !== "aprs") adsbMap.clearStations();
    apBody.innerHTML = "";
    apCount.textContent = "";
  } else if (isFft) {
    // canvases were display:none -> re-measure now that they're visible again
    requestAnimationFrame(layoutCanvases);
  }
  if (!isDab) {
    dabAudio.pause();
    dabPlayingSid = null;
  }
  if (mode !== "radio") cwText.hidden = true;
  if (mode !== "ais") { aisNote.hidden = true; noteMmsi = null; }
}

function rxLatLon(): [number, number] | null {
  const m = rxLoc.value.split(",").map((s) => parseFloat(s.trim()));
  if (m.length === 2 && isFinite(m[0]) && isFinite(m[1])) return [m[0], m[1]];
  return null;
}

// When the reference point changes, move the blue "home" dot and recompute
// distances now (otherwise distances would only refresh on the next ~1 s update).
function applyLocation(): void {
  const rx = rxLatLon();
  if (rx) adsbMap.setHome(rx[0], rx[1]);
  else adsbMap.clearHome();
  if (currentMode === "ais") renderVesselList(lastVessels);
  else if (currentMode === "adsb") renderAircraftList(lastAircraft);
  else if (currentMode === "aprs") renderStationList(lastStations);
}

// Fill "My location" from the browser's geolocation (works on localhost / HTTPS).
rxLocate.addEventListener("click", () => {
  if (!navigator.geolocation) {
    rxLocStatus.textContent = "geolocation not available in this browser";
    return;
  }
  rxLocStatus.textContent = "locating…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      rxLoc.value =
        `${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}`;
      rxLocStatus.textContent = `📍 using your location · ${rxLoc.value}`;
      persist();              // value set programmatically -> persist explicitly
      applyLocation();
    },
    // The desktop app's system web view has no GPS, so this fails there — point
    // the user at manual entry instead of showing a cryptic error.
    () => {
      rxLocStatus.textContent =
        "location unavailable here — type your lat, lon above (it'll be saved).";
    },
    { enableHighAccuracy: true, timeout: 8000 },
  );
});
// Manual entry: validate, persist, and confirm. The value sticks (localStorage)
// until you edit it again or use the button.
function applyManualLocation(): void {
  if (rxLatLon()) {
    persist();
    rxLocStatus.textContent = `saved · ${rxLoc.value.trim()}`;
  } else {
    rxLocStatus.textContent = "use decimal degrees: lat, lon  (e.g. 59.33, 18.06)";
  }
  applyLocation();
}
rxLoc.addEventListener("change", applyManualLocation);
// Enter applies immediately (some web views don't fire "change" on Enter alone)
rxLoc.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); applyManualLocation(); }
});

// show/hide the track trails (AIS / ADS-B / APRS); persisted across sessions
showTracks.addEventListener("change", () => adsbMap.setTracksVisible(showTracks.checked));

function haversineKm(a: [number, number], lat: number, lon: number): number {
  const R = 6371;
  const dLat = ((lat - a[0]) * Math.PI) / 180;
  const dLon = ((lon - a[1]) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a[0] * Math.PI) / 180) * Math.cos((lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
}

function renderAircraftList(list: Aircraft[]): void {
  const rx = rxLatLon();
  const rows = list.map((ac) => {
    const dist =
      rx && ac.lat != null && ac.lon != null ? haversineKm(rx, ac.lat, ac.lon) : null;
    return { ac, dist };
  });
  // nearest first; aircraft without a position go last
  rows.sort((a, b) => (a.dist ?? 1e9) - (b.dist ?? 1e9));

  apHead.innerHTML =
    "<tr><th>Flight</th><th>Alt</th><th>Spd</th><th>Trk</th><th>Dist</th></tr>";
  apCount.textContent = `(${list.length})`;
  apBody.innerHTML = rows
    .map(({ ac, dist }) => {
      const name = ac.flight || ac.icao.toUpperCase();
      const vs = vsArrow(ac.vert_rate);
      const arrow = vs.arrow
        ? ` <span class="vs ${vs.cls}" title="${vs.label}">${vs.arrow}</span>`
        : "";
      const alt = ac.alt != null ? `${ac.alt.toLocaleString()}${arrow}` : "—";
      const spd = ac.speed != null ? ac.speed : "—";
      const trk = ac.track != null ? `${ac.track}°` : "—";
      const d = dist != null ? `${dist.toFixed(0)} km` : "—";
      const cls = ac.lat != null ? "" : ' class="no-pos"';
      return `<tr data-icao="${ac.icao}"${cls}><td>${name}</td><td>${alt}</td><td>${spd}</td><td>${trk}</td><td>${d}</td></tr>`;
    })
    .join("");
}

// AIS vessel-list sorting — click a column header to change it. Nearest-first by
// default; `lastVessels` lets a header click re-sort without waiting for the next
// 1 s update.
type VesselSortKey = "name" | "speed" | "course" | "dist";
// latest list per map mode, so re-sorting or a location change can re-render now
let lastVessels: Vessel[] = [];
let lastAircraft: Aircraft[] = [];
let lastStations: Station[] = [];
let vesselSort: { key: VesselSortKey; dir: 1 | -1 } = { key: "dist", dir: 1 };
// sensible direction the first time you click a column (toggles thereafter)
const VESSEL_DEFAULT_DIR: Record<VesselSortKey, 1 | -1> = {
  name: 1, speed: -1, course: 1, dist: 1,
};

function vesselHead(): string {
  const cols: [VesselSortKey, string][] = [
    ["name", "Vessel"], ["speed", "Spd"], ["course", "Crs"], ["dist", "Dist"],
  ];
  return "<tr>" + cols
    .map(([k, label]) => {
      const arrow = vesselSort.key === k ? (vesselSort.dir === 1 ? " ▲" : " ▼") : "";
      return `<th data-sort="${k}" class="sortable">${label}${arrow}</th>`;
    })
    .join("") + "</tr>";
}

function renderVesselList(list: Vessel[]): void {
  const rx = rxLatLon();
  const rows = list.map((v) => ({
    v,
    dist: rx && v.lat != null && v.lon != null ? haversineKm(rx, v.lat, v.lon) : null,
  }));
  const { key, dir } = vesselSort;
  rows.sort((a, b) => {
    if (key === "name") {
      const an = (a.v.name || String(a.v.mmsi)).toLowerCase();
      const bn = (b.v.name || String(b.v.mmsi)).toLowerCase();
      return an < bn ? -dir : an > bn ? dir : 0;
    }
    // missing speed/course sort low; missing distance (no position) sorts last
    const val = (r: (typeof rows)[number]) =>
      key === "speed" ? r.v.speed ?? -1
      : key === "course" ? r.v.course ?? -1
      : r.dist ?? Infinity;
    return (val(a) - val(b)) * dir;
  });

  apHead.innerHTML = vesselHead();
  apCount.textContent = `(${list.length})`;
  apBody.innerHTML = rows
    .map(({ v, dist }) => {
      const flag = v.country ? flagEmoji(v.country) + " " : "";
      const name = v.name || String(v.mmsi);
      const title = esc(name).replace(/"/g, "&quot;");
      const note = v.comment
        ? ` <span class="note-tag" title="${esc(v.comment).replace(/"/g, "&quot;")}">📝</span>`
        : "";
      const spd = v.speed != null ? `${v.speed}` : "—";
      const crs = v.course != null ? `${v.course}°` : "—";
      const d = dist != null ? `${dist.toFixed(0)} km` : "—";
      const cls = v.lat != null ? "" : ' class="no-pos"';
      return `<tr data-mmsi="${v.mmsi}"${cls}><td><span class="ap-name" title="${title}">${flag}${esc(name)}</span>${note}</td><td>${spd}</td><td>${crs}</td><td>${d}</td></tr>`;
    })
    .join("");
}

function renderStationList(list: Station[]): void {
  const rx = rxLatLon();
  const rows = list.map((s) => ({
    s,
    dist: rx && s.lat != null && s.lon != null ? haversineKm(rx, s.lat, s.lon) : null,
  }));
  rows.sort((a, b) => (a.dist ?? 1e9) - (b.dist ?? 1e9));

  apHead.innerHTML = "<tr><th>Station</th><th>Info</th><th>Dist</th></tr>";
  apCount.textContent = `(${list.length})`;
  apBody.innerHTML = rows
    .map(({ s, dist }) => {
      const info = (s.comment || s.kind || "").slice(0, 22) || "—";
      const d = dist != null ? `${dist.toFixed(0)} km` : "—";
      const cls = s.lat != null ? "" : ' class="no-pos"';
      return `<tr data-call="${s.call}"${cls}><td>${s.call}</td><td>${info}</td><td>${d}</td></tr>`;
    })
    .join("");
}

apBody.addEventListener("click", (e) => {
  const tr = (e.target as HTMLElement).closest("tr") as HTMLElement | null;
  if (tr?.dataset.icao) adsbMap.focus(tr.dataset.icao);
  else if (tr?.dataset.mmsi) {
    adsbMap.vesselFocus(tr.dataset.mmsi);
    openNoteEditor(tr.dataset.mmsi);
  } else if (tr?.dataset.call) adsbMap.stationFocus(tr.dataset.call);
});

// --- AIS per-vessel note (saved in the backend cache, keyed by MMSI) -----
function openNoteEditor(mmsi: string): void {
  noteMmsi = mmsi;
  const v = lastVessels.find((x) => String(x.mmsi) === mmsi);
  aisNoteTarget.textContent = v?.name || mmsi;
  aisNoteInput.value = v?.comment || "";
  aisNote.hidden = false;
  aisNoteInput.focus();
}
function saveNote(): void {
  if (noteMmsi == null) return;
  sock.send({
    cmd: "config",
    params: { set_comment: { mmsi: Number(noteMmsi), text: aisNoteInput.value.trim() } },
  });
}
aisNoteSave.addEventListener("click", saveNote);
aisNoteInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); saveNote(); }
});

// Click a vessel-list column header to sort by it (toggles direction on repeat).
apHead.addEventListener("click", (e) => {
  if (currentMode !== "ais") return; // only the AIS list has sortable headers
  const th = (e.target as HTMLElement).closest("th[data-sort]") as HTMLElement | null;
  if (!th) return;
  const key = th.dataset.sort as VesselSortKey;
  if (vesselSort.key === key) vesselSort.dir = vesselSort.dir === 1 ? -1 : 1;
  else vesselSort = { key, dir: VESSEL_DEFAULT_DIR[key] };
  renderVesselList(lastVessels);
});

function esc(s: string): string {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
}

function renderAcars(messages: any[]): void {
  acarsFeedCount.textContent = `(${messages.length})`;
  if (!messages.length) {
    acarsLog.innerHTML =
      '<div class="acars-empty">No messages yet. ACARS is sparse — leave it running near an airport.</div>';
    return;
  }
  acarsLog.innerHTML = messages
    .map((m) => {
      const time = new Date((m.t || 0) * 1000).toLocaleTimeString();
      const id = m.flight || m.tail || "—";
      const tail = m.flight && m.tail ? ` ${m.tail}` : "";
      const label = m.label ? ` · ${esc(m.label)}` : "";
      const freq = m.freq != null ? ` · ${m.freq} MHz` : "";
      const body = m.text ? `<div class="body">${esc(m.text)}</div>` : "";
      return `<div class="acars-row"><div class="meta"><span class="time">${time}</span> ` +
        `${esc(id)}${esc(tail)}${label}${freq}</div>${body}</div>`;
    })
    .join("");
}

function renderPager(messages: any[]): void {
  pagerFeedCount.textContent = `(${messages.length})`;
  if (!messages.length) {
    pagerLog.innerHTML =
      '<div class="acars-empty">No messages yet. Pager traffic is bursty — leave it running.</div>';
    return;
  }
  pagerLog.innerHTML = messages
    .map((m) => {
      const time = new Date((m.t || 0) * 1000).toLocaleTimeString();
      const proto = esc(m.proto || "");
      const addr = m.addr ? ` · ${esc(m.addr)}` : "";
      const kind = m.kind ? ` · ${esc(m.kind)}` : "";
      const body = m.text ? `<div class="body">${esc(m.text)}</div>` : "";
      return `<div class="acars-row"><div class="meta"><span class="time">${time}</span> ` +
        `${proto}${addr}${kind}</div>${body}</div>`;
    })
    .join("");
}

function renderPagerChannels(channels: any[], freq: number): void {
  if (pagerChannel.options.length !== channels.length + 1) {
    pagerChannel.innerHTML =
      '<option value="">Custom…</option>' +
      channels
        .map((c) => `<option value="${c.freq}">${esc(c.label)}</option>`)
        .join("");
  }
  setPagerFreqHz(freq);
}

// Reflect the active pager frequency in both the MHz field and the quick-pick
// (selecting the matching preset, or "Custom…" when it isn't one).
function setPagerFreqHz(hz: number): void {
  if (hz == null || !isFinite(hz)) return;
  if (document.activeElement !== pagerFreq) pagerFreq.value = (hz / 1e6).toFixed(4);
  const match = [...pagerChannel.options].find(
    (o) => o.value && Math.abs(parseFloat(o.value) - hz) < 1,
  );
  pagerChannel.value = match ? match.value : "";
}

// ISM device field name -> friendly label (everything else falls back to the key)
const ISM_LABELS: Record<string, string> = {
  temperature_C: "Temp", temperature_F: "Temp", humidity: "Humidity",
  battery_ok: "Battery", wind_avg_km_h: "Wind", wind_max_km_h: "Gust",
  wind_dir_deg: "Dir", rain_mm: "Rain", rain_rate_mm_h: "Rain rate",
  pressure_hPa: "Pressure", pressure_kPa: "Pressure", moisture: "Moisture",
  light_lux: "Light", uv: "UV", type: "Type", status: "Status",
};
const ISM_UNITS: Record<string, string> = {
  temperature_C: "°C", temperature_F: "°F", humidity: "%", wind_avg_km_h: "km/h",
  wind_max_km_h: "km/h", wind_dir_deg: "°", rain_mm: "mm", rain_rate_mm_h: "mm/h",
  pressure_hPa: "hPa", pressure_kPa: "kPa", moisture: "%", light_lux: "lux",
};

function ismVal(key: string, v: any): string {
  if (key === "battery_ok") return v ? "OK" : "low";
  const unit = ISM_UNITS[key] || "";
  return `${v}${unit ? " " + unit : ""}`;
}

function agoText(t: number): string {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function ismNum(n: number): string {
  return Number(n.toFixed(2)).toString();   // trim trailing zeros
}

// Tiny inline sparkline of a value series, normalised to its own min/max.
function sparkline(vals: number[]): string {
  const w = 88, h = 22, pad = 2;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const n = vals.length;
  const pts = vals
    .map((v, i) => {
      const x = pad + (i / (n - 1)) * (w - 2 * pad);
      const y = h - pad - ((v - min) / span) * (h - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" ` +
    `preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;
}

// Current ISM band (MHz), reflected in the band label; updated by ism_config.
let ismFreqMhz = 433.92;

// Populate the band selector and reflect the active frequency.
function renderIsmBands(msg: any): void {
  const bands = msg.bands || [];
  const sig = bands.map((b: any) => b.mhz).join("|");
  if (ismBandSel.dataset.sig !== sig) {
    ismBandSel.dataset.sig = sig;
    ismBandSel.innerHTML = bands
      .map((b: any) => `<option value="${b.mhz}">${esc(b.label)}</option>`)
      .join("");
  }
  if (typeof msg.freq_mhz === "number") {
    ismFreqMhz = msg.freq_mhz;
    ismBandSel.value = String(msg.freq_mhz);
    if (currentMode === "ism") updateBandInfo();
  }
}

ismBandSel.addEventListener("change", () => {
  sock.send({ cmd: "config", params: { freq_mhz: parseFloat(ismBandSel.value) } });
});

// Latest feed kept so the type filter / close button can re-render instantly,
// without waiting for the next push from the backend.
let lastIsmDevices: any[] = [];
let ismFilter = String((loadSettings() as any).ismFilter || "");
let ismFilterSig = "";

// Rebuild the type dropdown only when the set of seen models changes, so it
// doesn't reset while the user is interacting with it.
function updateIsmFilterOptions(devices: any[]): void {
  const models = [...new Set(devices.map((d) => String(d.model)))]
    .sort((a, b) => a.localeCompare(b));
  if (ismFilter && !models.includes(ismFilter)) models.push(ismFilter); // keep selection alive
  const sig = models.join("|");
  if (sig !== ismFilterSig) {
    ismFilterSig = sig;
    ismFilterSel.innerHTML = `<option value="">All types</option>` +
      models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  }
  ismFilterSel.value = ismFilter;
}

function renderIsm(devices: any[]): void {
  lastIsmDevices = devices;
  updateIsmFilterOptions(devices);
  const shown = ismFilter
    ? devices.filter((d) => String(d.model) === ismFilter) : devices;
  ismFeedCount.textContent = ismFilter
    ? `(${shown.length} of ${devices.length})` : `(${devices.length})`;

  if (!shown.length) {
    ismLog.innerHTML = devices.length
      ? `<div class="acars-empty">No <b>${esc(ismFilter)}</b> devices right now.</div>`
      : '<div class="acars-empty">No devices yet. ISM-band sensors transmit ' +
        "periodically — give it a minute (busiest in the evening).</div>";
    return;
  }
  ismLog.innerHTML = shown
    .map((d) => {
      const id = d.id != null ? ` · id ${esc(String(d.id))}` : "";
      const ch = d.channel != null ? ` · ch ${esc(String(d.channel))}` : "";
      const meta = `<span class="muted">×${d.count} · ${agoText(d.last || 0)}` +
        (d.rssi != null ? ` · ${esc(String(d.rssi))} dB` : "") + "</span>";
      const hist = d.history || {};
      // Chips: fields without a 2+ point series yet (flags, text, first reading).
      const chips = Object.entries(d.fields || {})
        .filter(([k]) => !(Array.isArray(hist[k]) && hist[k].length >= 2))
        .map(([k, v]) =>
          `<span class="ism-chip"><b>${esc(ISM_LABELS[k] || k)}</b> ` +
          `${esc(ismVal(k, v))}</span>`)
        .join("");
      // Metrics: numeric fields with enough history to trend, as sparklines.
      const metrics = Object.entries(hist)
        .filter(([, s]) => Array.isArray(s) && (s as number[]).length >= 2)
        .map(([k, s]) => {
          const series = s as number[];
          const cur = series[series.length - 1];
          const lo = Math.min(...series), hi = Math.max(...series);
          return `<div class="ism-metric"><div class="ism-metric-head">` +
            `<b>${esc(ISM_LABELS[k] || k)}</b> ` +
            `<span class="cur">${esc(ismVal(k, cur))}</span></div>` +
            `${sparkline(series)}<div class="ism-metric-range muted">` +
            `${esc(ismNum(lo))} – ${esc(ismNum(hi))}</div></div>`;
        })
        .join("");
      const chipsHtml = chips ? `<div class="ism-chips">${chips}</div>` : "";
      const metricsHtml = metrics ? `<div class="ism-metrics">${metrics}</div>` : "";
      const close = `<button class="ism-close" data-key="${esc(String(d.key))}" ` +
        `title="Remove this device and its history" aria-label="Remove">×</button>`;
      return `<div class="ism-row"><div class="ism-name"><span class="ism-title">` +
        `${esc(d.model)}${id}${ch} ${meta}</span>${close}</div>` +
        `${chipsHtml}${metricsHtml}</div>`;
    })
    .join("");
}

// Type filter: persist the choice and re-render immediately.
ismFilterSel.addEventListener("change", () => {
  ismFilter = ismFilterSel.value;
  const s = loadSettings() as any;
  s.ismFilter = ismFilter;
  saveSettings(s);
  renderIsm(lastIsmDevices);
});

// Close button: tell the backend to drop the device + its history, and remove
// it from the view right away (optimistic).
ismLog.addEventListener("click", (e) => {
  const btn = (e.target as HTMLElement).closest(".ism-close") as HTMLElement | null;
  if (!btn) return;
  const key = btn.dataset.key;
  if (!key) return;
  sock.send({ cmd: "config", params: { remove: key } });
  lastIsmDevices = lastIsmDevices.filter((d) => String(d.key) !== key);
  renderIsm(lastIsmDevices);
});

function highlightMode(mode: string): void {
  document.querySelectorAll("#mode-tabs button").forEach((b) => {
    b.classList.toggle("active", (b as HTMLElement).dataset.mode === mode);
  });
}

// --- mobile nav drawer ---------------------------------------------------
const appEl = document.querySelector(".app")!;
const closeNav = () => appEl.classList.remove("nav-open");
document
  .getElementById("nav-toggle")!
  .addEventListener("click", () => appEl.classList.toggle("nav-open"));
document.getElementById("nav-backdrop")!.addEventListener("click", closeNav);

// --- mode tabs -----------------------------------------------------------
document.getElementById("mode-tabs")!.addEventListener("click", async (e) => {
  const btn = (e.target as HTMLElement).closest("button");
  if (!btn || (btn as HTMLButtonElement).disabled) return;
  const mode = btn.dataset.mode!;
  closeNav(); // on mobile, reveal the result after picking a mode
  if (mode === "radio" || mode === "replay" || mode === "scanner")
    await audio.init(); // user gesture: unlock audio
  if (mode !== "idle") {
    waterfall.clear();
    scope.clear();
  }
  sock.send({ cmd: "set_mode", mode });
  if (mode === "radio" || mode === "replay") sendRadioPrefs();
  if (mode === "scanner") sendScannerPrefs();
  if (mode === "replay") {
    renderReplayList();
    if (replayFile) sock.send({ cmd: "config", params: { file: replayFile, playing: true } });
  }
  if (mode === "apt") {
    aptImage.clear();
    aptLineCount = 0;
    aptStatus.textContent = "waiting for a pass…";
    sock.send({ cmd: "tune", center_freq: parseFloat(aptSat.value) });
  }
  if (mode === "sstv") {
    sstvImage.clear();
    sstvRowCount = 0;
    sstvStatus.textContent = "waiting for a transmission…";
  }
  if (mode === "pager") {
    // Carry the frequency over from Radio/Replay (the tuned channel) or
    // Sweep/Spectrum (the band centre) — handy when you spot a POCSAG burst and
    // hit Pager. Otherwise keep whatever the MHz field already shows.
    let hz = NaN;
    if (currentMode === "radio" || currentMode === "replay") hz = viewTuned;
    else if (currentMode === "spectrum" || currentMode === "scan") hz = viewCenter;
    if (!isFinite(hz) || hz <= 0) hz = parseFloat(pagerFreq.value) * 1e6;
    if (isFinite(hz) && hz > 0) {
      sock.send({ cmd: "config", params: { freq: Math.round(hz) } });
      setPagerFreqHz(hz);
    }
  }
  if (mode === "dab") sock.send({ cmd: "config", params: { channel: dabChannel.value } });
});

// Apply persisted demod/volume/squelch after entering radio mode.
function sendRadioPrefs(): void {
  sock.send({
    cmd: "config",
    params: {
      demod: demodSel.value,
      volume: parseFloat(volInput.value),
      squelch: parseFloat(sqlInput.value),
      deemph: parseFloat(deemphSel.value),
      rds: rdsOn.checked,
      stereo: stereoOn.checked,
    },
  });
}

// --- click / drag to tune ------------------------------------------------
tuner.onTune = async (freqHz) => {
  if (currentMode === "radio" || currentMode === "replay") {
    // tune within the currently captured band, no hardware retune
    sock.send({ cmd: "config", params: { tuned_freq: freqHz } });
    tuner.setTuned(freqHz);
  } else {
    // coming from waterfall/scan: re-center the dongle on the signal, then listen
    await audio.init();
    sock.send({ cmd: "set_mode", mode: "radio" });
    sendRadioPrefs();
    sock.send({ cmd: "tune", center_freq: freqHz });
    sock.send({ cmd: "config", params: { tuned_freq: freqHz } });
    tuner.setTuned(freqHz);
  }
};
tuner.onSelect = (loHz, hiHz) => {
  if (currentMode === "radio" || currentMode === "replay") {
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
deemphSel.addEventListener("change", () =>
  sock.send({ cmd: "config", params: { deemph: parseFloat(deemphSel.value) } }),
);
rdsOn.addEventListener("change", () => {
  rdsBox.hidden = !(demodSel.value === "wfm" && rdsOn.checked);
  sock.send({ cmd: "config", params: { rds: rdsOn.checked } });
});
stereoOn.addEventListener("change", () =>
  sock.send({ cmd: "config", params: { stereo: stereoOn.checked } }),
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

// --- audio recording -----------------------------------------------------
const recAudioBtn = document.getElementById("rec-audio") as HTMLButtonElement;
const recAudioStatus = document.getElementById("rec-audio-status")!;
let recTimer = 0;

recAudioBtn.addEventListener("click", () => {
  if (!wavRec.recording) {
    wavRec.start();
    recAudioBtn.textContent = "■ Stop recording";
    recTimer = window.setInterval(() => {
      recAudioStatus.textContent = `● recording ${wavRec.elapsedSec().toFixed(0)} s`;
    }, 500);
  } else {
    clearInterval(recTimer);
    const blob = wavRec.stop();
    recAudioBtn.textContent = "● Record audio";
    if (blob) {
      const mhz = (viewTuned / 1e6).toFixed(3);
      const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      downloadBlob(blob, `sdr_${mhz}MHz_${ts}.wav`);
      recAudioStatus.textContent = `saved (${(blob.size / 1024).toFixed(0)} kB)`;
    } else {
      recAudioStatus.textContent = "nothing recorded";
    }
  }
});

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

// --- scanner -------------------------------------------------------------
function sendScannerPrefs(): void {
  sock.send({
    cmd: "config",
    params: {
      squelch: parseFloat(scannerSql.value),
      volume: parseFloat(scannerVol.value),
      priority: String((loadSettings() as any).scannerPrio || ""),  // persisted choice
    },
  });
}
// Preset dropdown, split into Built-in / Custom groups.
function renderScannerPresets(presets: any[], current: string): void {
  const grp = (builtin: boolean) => presets
    .filter((p) => !!p.builtin === builtin)
    .map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`)
    .join("");
  const custom = grp(false);
  scannerPreset.innerHTML =
    `<optgroup label="Built-in">${grp(true)}</optgroup>` +
    (custom ? `<optgroup label="Custom">${custom}</optgroup>` : "");
  scannerPreset.value = current;
}
// Priority dropdown = Off + the current preset's channels.
function renderScannerPrio(current: string): void {
  if (document.activeElement === scannerPrio) return;   // don't disrupt mid-pick
  scannerPrio.innerHTML = `<option value="">Off</option>` +
    scannerChannels
      .map((c) => `<option value="${esc(c.label)}">${esc(c.label)} · ${c.mhz.toFixed(3)}</option>`)
      .join("");
  scannerPrio.value = current;
}
// Channel editor (working copy in editChannels).
function renderScannerEditor(): void {
  scannerEditList.innerHTML = editChannels
    .map((c, i) =>
      `<div class="sc-edit-row" data-i="${i}">` +
      `<input class="sc-label" data-f="label" value="${esc(c.label)}" maxlength="12" placeholder="name" />` +
      `<input class="sc-mhz" data-f="mhz" type="number" step="0.0125" min="24" max="1766" value="${c.mhz}" />` +
      `<select class="sc-demod" data-f="demod">` +
      `<option value="nfm"${c.demod === "nfm" ? " selected" : ""}>NFM</option>` +
      `<option value="am"${c.demod === "am" ? " selected" : ""}>AM</option></select>` +
      `<button type="button" class="sc-up" title="Move up">↑</button>` +
      `<button type="button" class="sc-down" title="Move down">↓</button>` +
      `<button type="button" class="sc-rm" title="Remove">×</button></div>`,
    )
    .join("") ||
    `<div class="muted" style="font-size:.8rem">No channels — add one below.</div>`;
}
// Live-update editChannels from a field edit (no re-render, to keep focus).
scannerEditList.addEventListener("input", (e) => {
  const el = e.target as HTMLElement;
  const row = el.closest(".sc-edit-row") as HTMLElement | null;
  const f = (el as HTMLElement).dataset.f;
  if (!row || !f) return;
  const i = Number(row.dataset.i);
  const v = (el as HTMLInputElement).value;
  if (f === "mhz") editChannels[i].mhz = parseFloat(v);
  else if (f === "label") editChannels[i].label = v;
  else if (f === "demod") editChannels[i].demod = v;
});
// Reorder / remove (structural -> re-render).
scannerEditList.addEventListener("click", (e) => {
  const btn = (e.target as HTMLElement).closest("button");
  const row = (e.target as HTMLElement).closest(".sc-edit-row") as HTMLElement | null;
  if (!btn || !row) return;
  const i = Number(row.dataset.i);
  if (btn.classList.contains("sc-rm")) editChannels.splice(i, 1);
  else if (btn.classList.contains("sc-up") && i > 0)
    [editChannels[i - 1], editChannels[i]] = [editChannels[i], editChannels[i - 1]];
  else if (btn.classList.contains("sc-down") && i < editChannels.length - 1)
    [editChannels[i + 1], editChannels[i]] = [editChannels[i], editChannels[i + 1]];
  renderScannerEditor();
});
scannerAdd.addEventListener("click", () => {
  editChannels.push({ label: "", mhz: 156.8, demod: "nfm" });
  renderScannerEditor();
});
scannerSave.addEventListener("click", () => {
  const name = scannerName.value.trim();
  const channels = editChannels.filter((c) => c.label.trim() && isFinite(c.mhz));
  if (!name) { scannerStatus.textContent = "name the preset first"; return; }
  if (!channels.length) { scannerStatus.textContent = "add at least one channel"; return; }
  sock.send({ cmd: "config", params: { save_preset: { name, channels } } });
});
scannerDel.addEventListener("click", () => {
  if (scannerPresetId) sock.send({ cmd: "config", params: { delete_preset: scannerPresetId } });
});
scannerPrio.addEventListener("change", () => {
  sock.send({ cmd: "config", params: { priority: scannerPrio.value } });
  persist();
});
function renderScannerGrid(): void {
  scannerGrid.innerHTML = scannerChannels
    .map((c, i) =>
      `<div class="scan-ch" data-i="${i}"><span class="ch-label">${esc(c.label)}</span>` +
      `<span class="ch-freq">${c.mhz.toFixed(3)} MHz</span>` +
      `<div class="ch-bar"><div class="ch-fill"></div></div></div>`,
    )
    .join("");
}
function updateScannerState(msg: any): void {
  const tiles = scannerGrid.querySelectorAll(".scan-ch");
  (msg.channels || []).forEach((ch: any, i: number) => {
    const t = tiles[i] as HTMLElement | undefined;
    if (!t) return;
    t.classList.toggle("active", !!ch.active);
    t.classList.toggle("parked", i === msg.parked);
    // live signal strength (dB over noise) so you can see near-misses the
    // squelch skipped — full bar ≈ 24 dB
    const fill = t.querySelector(".ch-fill") as HTMLElement | null;
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, ((ch.level ?? 0) / 24) * 100))}%`;
  });
  const c = msg.parked >= 0 ? scannerChannels[msg.parked] : undefined;
  if (c) {
    scannerNow.textContent = `▶ ${c.label} · ${c.mhz.toFixed(3)} MHz`;
    scannerStatus.textContent = `▶ on ${c.label} (${c.mhz.toFixed(3)} MHz)`;
  } else {
    scannerNow.textContent = "Scanning…";
    scannerStatus.textContent = "scanning…";
  }
}
scannerPreset.addEventListener("change", () =>
  sock.send({ cmd: "config", params: { preset: scannerPreset.value } }),
);
scannerSql.addEventListener("input", () =>
  sock.send({ cmd: "config", params: { squelch: parseFloat(scannerSql.value) } }),
);
scannerVol.addEventListener("input", () =>
  sock.send({ cmd: "config", params: { volume: parseFloat(scannerVol.value) } }),
);

// --- display: contrast + peak hold ---------------------------------------
function applyContrast(): void {
  const auto = wfAuto.checked;
  wfFloor.disabled = auto;
  wfCeil.disabled = auto;
  waterfall.setRange(parseFloat(wfFloor.value), parseFloat(wfCeil.value), auto);
}
wfAuto.addEventListener("change", applyContrast);
wfFloor.addEventListener("input", applyContrast);
wfCeil.addEventListener("input", applyContrast);
peakHold.addEventListener("change", () => scope.setPeakHold(peakHold.checked));
averaging.addEventListener("change", () =>
  scope.setAveraging(parseInt(averaging.value, 10) || 1),
);

// zoom out (×2) — widens the scan range or the captured band
zoomOutBtn.addEventListener("click", () => {
  // In the spectrum/replay views, this resets the display zoom (no retune).
  if (currentMode === "radio" || currentMode === "replay") {
    tuner.resetView();
    return;
  }
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
// The Center / Sample-rate fields tune the dongle directly on change (Enter or
// blur) — no separate "apply" button.
freqInput.addEventListener("change", () => {
  const mhz = parseFloat(freqInput.value);
  if (!isFinite(mhz)) return;
  const hz = mhz * 1e6;
  sock.send({ cmd: "tune", center_freq: hz });
  // In the Spectrum view, typing a frequency should both re-center AND listen to
  // it (the channel follows the centre), so the tuned cursor doesn't get left
  // behind on the old station.
  if (currentMode === "radio" || currentMode === "spectrum") {
    sock.send({ cmd: "config", params: { tuned_freq: hz } });
    tuner.setTuned(hz);
  }
});
rateInput.addEventListener("change", () => {
  const ms = parseFloat(rateInput.value);
  if (isFinite(ms)) sock.send({ cmd: "tune", sample_rate: ms * 1e6 });
});
// Pan the captured band by exactly one capture width (~sample rate), to walk
// across the spectrum block by block looking for signals.
function stepBlock(dir: 1 | -1): void {
  sock.send({ cmd: "tune", center_freq: tuner.centerFreq + dir * tuner.sampleRate });
}
document.getElementById("block-prev")!.addEventListener("click", () => stepBlock(-1));
document.getElementById("block-next")!.addEventListener("click", () => stepBlock(1));
// --- APT (weather satellite) controls ------------------------------------
aptSat.addEventListener("change", () => {
  aptImage.clear();
  aptLineCount = 0;
  sock.send({ cmd: "tune", center_freq: parseFloat(aptSat.value) });
});
document.getElementById("apt-save")!.addEventListener("click", () => {
  const url = aptImage.toPng();
  if (!url) { aptStatus.textContent = "nothing to save yet"; return; }
  const a = document.createElement("a");
  a.href = url;
  a.download = `apt_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.png`;
  a.click();
});
document.getElementById("apt-clear")!.addEventListener("click", () => {
  aptImage.clear();
  aptLineCount = 0;
  aptStatus.textContent = "cleared";
});
replayApt.addEventListener("change", () => {
  if (replayApt.checked) { aptImage.clear(); aptLineCount = 0; }
  sock.send({ cmd: "config", params: { apt: replayApt.checked } });
  showView(currentMode);
});

// --- SSTV controls -------------------------------------------------------
document.getElementById("sstv-save")!.addEventListener("click", () => {
  const url = sstvImage.toPng();
  if (!url) { sstvStatus.textContent = "nothing to save yet"; return; }
  const a = document.createElement("a");
  a.href = url;
  a.download = `sstv_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.png`;
  a.click();
});
document.getElementById("sstv-clear")!.addEventListener("click", () => {
  sstvImage.clear();
  sstvRowCount = 0;
  sstvStatus.textContent = "cleared";
});

// --- Pager controls ------------------------------------------------------
pagerChannel.addEventListener("change", () => {
  if (!pagerChannel.value) return;     // "Custom…" — leave the typed frequency
  const hz = parseFloat(pagerChannel.value);
  sock.send({ cmd: "config", params: { freq: hz } });
  setPagerFreqHz(hz);
});
pagerFreq.addEventListener("change", () => {
  const mhz = parseFloat(pagerFreq.value);
  if (!isFinite(mhz)) return;
  const hz = Math.round(mhz * 1e6);
  sock.send({ cmd: "config", params: { freq: hz } });
  setPagerFreqHz(hz);
});

// live antenna advice as the user types a frequency (before tuning)
freqInput.addEventListener("input", () => {
  const f = parseFloat(freqInput.value);
  if (isFinite(f)) antennaInfo.textContent = antennaText(f);
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
    desiredGainDb = g;
    gainVal.textContent = `${g} dB`;
    sock.send({ cmd: "tune", gain: g });
  }
});
gainSlider.addEventListener("input", () => {
  const g = gainSteps[parseInt(gainSlider.value, 10)] ?? 0;
  desiredGainDb = g;
  gainVal.textContent = `${g} dB`;
  sock.send({ cmd: "tune", gain: g });
});
ppmInput.addEventListener("change", () =>
  sock.send({ cmd: "tune", ppm: parseInt(ppmInput.value, 10) || 0 }),
);
biasTee.addEventListener("change", () =>
  sock.send({ cmd: "tune", bias_tee: biasTee.checked }),
);

// --- settings persistence (localStorage) ---------------------------------
const persistValues: Record<string, HTMLInputElement | HTMLSelectElement> = {
  ppm: ppmInput, demod: demodSel, vol: volInput, sql: sqlInput,
  scanStart, scanStop, wfFloor, wfCeil, rxLoc, deemph: deemphSel, averaging,
  scannerSql, scannerVol, scannerPrio,
};
const persistChecks: Record<string, HTMLInputElement> = {
  gainAuto, biasTee, wfAuto, peakHold, rdsOn, stereoOn, showTracks,
};

function persist(): void {
  const s: Record<string, string | number | boolean> = { gainDb: desiredGainDb };
  for (const k in persistValues) s[k] = persistValues[k].value;
  for (const k in persistChecks) s[k] = persistChecks[k].checked;
  saveSettings(s);
}

function restoreSettings(): void {
  const s = loadSettings();
  for (const k in persistValues)
    if (s[k] !== undefined) persistValues[k].value = String(s[k]);
  for (const k in persistChecks)
    if (s[k] !== undefined) persistChecks[k].checked = Boolean(s[k]);
  // recover from a corrupt saved position (would otherwise blank all distances)
  if (rxLatLon() === null) rxLoc.value = "59.33, 18.06";
  if (typeof s.gainDb === "number") desiredGainDb = s.gainDb;
  applyContrast();
  scope.setPeakHold(peakHold.checked);
  scope.setAveraging(parseInt(averaging.value, 10) || 1);
  gainSlider.disabled = gainAuto.checked;
  gainVal.textContent = gainAuto.checked ? "auto" : `${desiredGainDb} dB`;
}

[...Object.values(persistValues), ...Object.values(persistChecks), gainSlider]
  // rxLoc persists explicitly (only on a complete, validated edit) so a
  // half-typed "lat," can't get saved on a keystroke and blank out distances.
  .filter((el) => el !== rxLoc)
  .forEach((el) => {
    el.addEventListener("change", persist);
    el.addEventListener("input", persist);
  });

// --- bookmarks -----------------------------------------------------------
function currentFreqHz(): number {
  return currentMode === "radio" ? tuner.tuned : tuner.centerFreq;
}

// In-memory source of truth: loaded once at startup, then persisted to
// localStorage on every change. Re-reading storage on each save lost bookmarks
// whenever the web-storage backend was flaky (e.g. the desktop web view), which
// looked like "saving a second one overwrites the first".
let bookmarks: Bookmark[] = loadBookmarks();

function renderBookmarks(): void {
  if (!bookmarks.length) {
    bmList.innerHTML = '<div class="bm-empty">No bookmarks yet.</div>';
    return;
  }
  bmList.innerHTML = bookmarks
    .map((b, i) => {
      const label = b.name || `${b.mhz.toFixed(3)} MHz`;
      const sub = `${b.demod ? b.demod.toUpperCase() + " · " : ""}${b.mhz.toFixed(3)}`;
      return `<div class="bm-row"><button class="recall" data-i="${i}">${label} <span class="muted">${sub}</span></button><button class="del" data-i="${i}" title="delete">×</button></div>`;
    })
    .join("");
}

document.getElementById("bm-save")!.addEventListener("click", () => {
  bookmarks.push({
    name: bmName.value.trim(),
    mhz: currentFreqHz() / 1e6,
    demod: currentMode === "radio" ? demodSel.value : undefined,
  });
  saveBookmarks(bookmarks);
  bmName.value = "";
  renderBookmarks();
});

bmList.addEventListener("click", async (e) => {
  const btn = (e.target as HTMLElement).closest("button");
  if (!btn) return;
  const i = parseInt((btn as HTMLElement).dataset.i!, 10);
  const b = bookmarks[i];
  if (!b) return;
  if (btn.classList.contains("del")) {
    bookmarks.splice(i, 1);
    saveBookmarks(bookmarks);
    renderBookmarks();
    return;
  }
  await recallBookmark(b);
});

async function recallBookmark(b: Bookmark): Promise<void> {
  await audio.init();
  sock.send({ cmd: "set_mode", mode: "radio" });
  if (b.demod) demodSel.value = b.demod;
  sendRadioPrefs();
  const hz = b.mhz * 1e6;
  sock.send({ cmd: "tune", center_freq: hz });
  sock.send({ cmd: "config", params: { tuned_freq: hz } });
  tuner.setTuned(hz);
}

restoreSettings();
renderBookmarks();

// --- IQ recording --------------------------------------------------------
const recIqBtn = document.getElementById("rec-iq") as HTMLButtonElement;
const recIqStatus = document.getElementById("rec-iq-status")!;
const recList = document.getElementById("rec-list")!;
let iqRecording = false;

recIqBtn.addEventListener("click", () => {
  sock.send({ cmd: "record", action: iqRecording ? "stop" : "start" });
});

async function refreshRecordings(): Promise<void> {
  try {
    const d = await (await fetch(`${BASE}api/recordings`)).json();
    const list: any[] = d.recordings || [];
    recList.innerHTML = list
      .map(
        (it) =>
          `<div class="bm-row"><a class="recall" href="${BASE}recordings/${it.name}" download>` +
          `${it.name.replace("iq_", "").replace(".cu8", "")} ` +
          `<span class="muted">${(it.size / 1e6).toFixed(1)} MB</span></a>` +
          `<button class="del" data-name="${it.name}" title="delete">×</button></div>`,
      )
      .join("");
  } catch {
    /* offline */
  }
}

recList.addEventListener("click", async (e) => {
  const btn = (e.target as HTMLElement).closest("button.del") as HTMLElement | null;
  if (!btn) return;
  await fetch(`${BASE}api/recordings/${btn.dataset.name}`, { method: "DELETE" });
  refreshRecordings();
});

refreshRecordings();

// --- Replay (play a saved .cu8 back through the spectrum view) ------------
async function renderReplayList(): Promise<void> {
  try {
    const d = await (await fetch(`${BASE}api/recordings`)).json();
    const list: any[] = d.recordings || [];
    if (!list.length) {
      replayList.innerHTML =
        '<div class="bm-empty">No recordings yet — record some IQ first.</div>';
      return;
    }
    replayList.innerHTML = list
      .map((it) => {
        const label = it.name.replace("iq_", "").replace(".cu8", "");
        const playing = it.name === replayFile ? " playing" : "";
        return (
          `<div class="bm-row"><button class="recall${playing}" data-name="${it.name}">` +
          `${label} <span class="muted">${(it.size / 1e6).toFixed(1)} MB</span></button></div>`
        );
      })
      .join("");
  } catch {
    /* offline */
  }
}

replayList.addEventListener("click", (e) => {
  const btn = (e.target as HTMLElement).closest("button.recall") as HTMLElement | null;
  if (!btn?.dataset.name) return;
  sock.send({ cmd: "config", params: { file: btn.dataset.name, playing: true } });
});

// --- DAB ----------------------------------------------------------------
let dabPort = 7979;

function renderDab(msg: any): void {
  dabPort = msg.web_port || 7979;
  const services: any[] = msg.services || [];
  dabEnsName.textContent = msg.ensemble || "(searching…)";
  dabStatus.textContent = services.length
    ? `${services.length} stations · SNR ${msg.snr} dB`
    : `no ensemble yet · SNR ${msg.snr} dB`;
  dabEnsembleSide.textContent = msg.ensemble || "";
  if (!services.length) {
    dabStationsEl.innerHTML =
      `<div class="dab-empty">No stations on block ${msg.channel} yet. ` +
      `DAB needs a good Band III antenna — try another block.</div>`;
    return;
  }
  dabStationsEl.innerHTML = services
    .map(
      (s) =>
        `<button class="dab-station${s.sid === dabPlayingSid ? " playing" : ""}" ` +
        `data-sid="${s.sid}" data-mp3="${s.mp3 || ""}">${s.label}</button>`,
    )
    .join("");
}

dabStationsEl.addEventListener("click", async (e) => {
  const btn = (e.target as HTMLElement).closest(".dab-station") as HTMLElement | null;
  if (!btn || !btn.dataset.mp3) return;
  dabPlayingSid = Number(btn.dataset.sid);
  dabAudio.src = `http://${location.hostname}:${dabPort}${btn.dataset.mp3}`;
  dabNow.textContent = "▶ " + btn.textContent;
  dabStationsEl.querySelectorAll(".dab-station").forEach((el) =>
    el.classList.toggle("playing", Number((el as HTMLElement).dataset.sid) === dabPlayingSid),
  );
  try {
    await dabAudio.play();
  } catch {
    /* autoplay/network — ignore */
  }
});

dabChannel.addEventListener("change", () => {
  dabAudio.pause();
  dabPlayingSid = null;
  dabNow.textContent = "";
  sock.send({ cmd: "config", params: { channel: dabChannel.value } });
});

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
  if (!aptView.hidden) {
    const r = aptView.getBoundingClientRect();
    aptImage.resize(Math.round(r.width), Math.round(r.height));
  }
  if (!sstvView.hidden) {
    const r = sstvView.getBoundingClientRect();
    sstvImage.resize(Math.round(r.width), Math.round(r.height));
  }
}

let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(layoutCanvases, 120);
});
// initial sizing after the flex layout settles
requestAnimationFrame(() => requestAnimationFrame(layoutCanvases));

sock.connect();
