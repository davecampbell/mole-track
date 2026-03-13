"use strict";

// ─── State ────────────────────────────────────────────────────────────────────

const API = "/api";

let anchors = [];             // [{x: 0..1, y: 0..1}, ...]  normalized
let detectorState = "idle";   // mirrors DetectorState enum values from backend
let lastTriggered = false;
let statusPoller  = null;
let audioCtx      = null;     // Web Audio API context — created on first user gesture

let alertInterval      = null;   // repeating 1-Hz beep while alert is active
let timerInterval      = null;   // 1-Hz UI timer updater
let detectionStartTime = null;   // Date.now() when first trigger fired
let silenced           = false;  // user pressed Silence — no more beeps this session
let currentThreshold   = 8.0;   // kept in sync with settings, used for per-point colouring
let showingGray        = false;  // stream toggle — color vs CLAHE normalized gray

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  setupOverlay();
  loadSettings();
  pollHealth();
  setInterval(pollHealth, 10_000);
});

// ─── Camera overlay ───────────────────────────────────────────────────────────

function setupOverlay() {
  const img    = document.getElementById("camera-feed");
  const canvas = document.getElementById("overlay-canvas");

  const syncSize = () => {
    canvas.width  = img.clientWidth;
    canvas.height = img.clientHeight;
    redrawAnchors();
  };

  img.addEventListener("load",   syncSize);
  window.addEventListener("resize", syncSize);

  canvas.addEventListener("click",    handleCanvasTap);
  canvas.addEventListener("touchend", (e) => {
    e.preventDefault();
    const t = e.changedTouches[0];
    handleCanvasTapAt(t.clientX, t.clientY, canvas);
  }, { passive: false });
}

function handleCanvasTap(e) {
  handleCanvasTapAt(e.clientX, e.clientY, e.currentTarget);
}

function handleCanvasTapAt(clientX, clientY, canvas) {
  if (detectorState === "running") return;
  const rect = canvas.getBoundingClientRect();
  const x = (clientX - rect.left)  / rect.width;
  const y = (clientY - rect.top)   / rect.height;
  anchors.push({ x, y });
  redrawAnchors();
  updateUI();
}

// Draw static calibration anchors (used when not running)
function redrawAnchors() {
  const canvas = document.getElementById("overlay-canvas");
  const ctx    = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  anchors.forEach(({ x, y }, i) => {
    const cx = x * canvas.width;
    const cy = y * canvas.height;

    ctx.beginPath();
    ctx.arc(cx, cy, 11, 0, 2 * Math.PI);
    ctx.strokeStyle = "#00c875";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "rgba(0, 200, 117, 0.25)";
    ctx.fill();

    ctx.beginPath();
    ctx.arc(cx, cy, 2.5, 0, 2 * Math.PI);
    ctx.fillStyle = "#00c875";
    ctx.fill();

    ctx.fillStyle = "#fff";
    ctx.font = "bold 11px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(i + 1, cx, cy - 18);
  });
}

// Draw live tracked point positions during detection
function redrawTrackedPoints(points, displacements) {
  const canvas = document.getElementById("overlay-canvas");
  const ctx    = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  points.forEach(({ x, y }, i) => {
    const cx  = x * canvas.width;
    const cy  = y * canvas.height;
    const d   = displacements[i] ?? 0;
    const hot = d >= currentThreshold;
    const col = hot ? "#ff3b30" : "#00c875";

    // Ring
    ctx.beginPath();
    ctx.arc(cx, cy, 11, 0, 2 * Math.PI);
    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = hot ? "rgba(255,59,48,0.25)" : "rgba(0,200,117,0.20)";
    ctx.fill();

    // Centre dot
    ctx.beginPath();
    ctx.arc(cx, cy, 2.5, 0, 2 * Math.PI);
    ctx.fillStyle = col;
    ctx.fill();

    // Displacement label above ring
    ctx.fillStyle = hot ? "#ff3b30" : "#fff";
    ctx.font = `bold 11px -apple-system, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`${d.toFixed(1)}`, cx, cy - 20);

    // Point number inside ring
    ctx.fillStyle = "#fff";
    ctx.font = "10px -apple-system, sans-serif";
    ctx.fillText(i + 1, cx, cy);
  });
}

function clearAnchors() {
  anchors = [];
  redrawAnchors();
  updateUI();
}

function toggleStream() {
  showingGray = !showingGray;
  const img = document.getElementById("camera-feed");
  const btn = document.getElementById("btn-toggle-stream");
  img.src = showingGray ? `${API}/stream-gray.mjpeg` : `${API}/stream.mjpeg`;
  btn.textContent = showingGray ? "Show color" : "Show normalized gray";
  btn.classList.toggle("btn--active", showingGray);
}

// ─── API calls ────────────────────────────────────────────────────────────────

async function sendCalibration() {
  if (anchors.length < 2) return;
  try {
    const resp = await fetch(`${API}/calibrate`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ points: anchors }),
    });
    const data = await resp.json();
    if (data.success) {
      detectorState = "calibrated";
      updateUI();
    }
  } catch (e) {
    console.error("Calibrate failed:", e);
  }
}

async function startDetection() {
  // AudioContext MUST be created/resumed here — inside a direct user-gesture handler,
  // before any await — so iOS Safari doesn't consider the gesture consumed.
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === "suspended") {
    await audioCtx.resume();
  }

  silenced = false;   // clear any previous silence for this new session

  try {
    const resp = await fetch(`${API}/detection/start`, { method: "POST" });
    const data = await resp.json();
    if (data.success) {
      detectorState = "running";
      lastTriggered = false;
      statusPoller  = setInterval(pollStatus, 500);
      updateUI();
    }
  } catch (e) {
    console.error("Start detection failed:", e);
  }
}

async function stopDetection() {
  clearInterval(statusPoller);
  clearInterval(alertInterval);
  clearInterval(timerInterval);
  statusPoller = alertInterval = timerInterval = null;
  detectionStartTime = null;
  silenced = false;
  lastTriggered = false;

  try {
    await fetch(`${API}/detection/stop`, { method: "POST" });
  } catch (e) {
    console.error("Stop detection failed:", e);
  }

  // Reset overlay back to static anchors
  redrawAnchors();
  detectorState = "calibrated";
  updateUI();

  // Hide timer, silence button, agg stats, point list
  document.getElementById("timer-row").style.display  = "none";
  document.getElementById("btn-silence").style.display = "none";
  document.getElementById("agg-stats").style.display   = "none";
  document.getElementById("win-stats").style.display   = "none";
  document.getElementById("cum-stats").style.display   = "none";
  document.getElementById("odo-stats").style.display   = "none";
  document.getElementById("point-list").innerHTML       = "";
}

async function pollHealth() {
  const badge = document.getElementById("health-badge");
  try {
    const resp = await fetch(`${API}/health`);
    const data = await resp.json();
    badge.textContent = data.status;
    badge.className = `badge badge--${data.status}`;
  } catch {
    badge.textContent = "offline";
    badge.className = "badge badge--offline";
  }
}

async function pollStatus() {
  try {
    const resp = await fetch(`${API}/status`);
    const data = await resp.json();

    detectorState = data.detector_state;

    // Per-frame aggregate stats
    document.getElementById("stat-mean").textContent  = data.displacement_mean.toFixed(1);
    document.getElementById("stat-max").textContent   = data.displacement_max.toFixed(1);
    document.getElementById("stat-total").textContent = data.displacement_total.toFixed(1);
    document.getElementById("stat-pts").textContent   = `${data.active_points}/${data.total_points}`;
    document.getElementById("agg-stats").style.display = "";

    // Windowed accumulation stats
    document.getElementById("stat-win-mean").textContent  = data.windowed_mean.toFixed(1);
    document.getElementById("stat-win-max").textContent   = data.windowed_max.toFixed(1);
    document.getElementById("stat-win-total").textContent = data.windowed_total.toFixed(1);
    document.getElementById("win-stats").style.display = "";

    // Cumulative stats (from Start)
    document.getElementById("stat-cum-mean").textContent  = data.cumulative_mean.toFixed(1);
    document.getElementById("stat-cum-max").textContent   = data.cumulative_max.toFixed(1);
    document.getElementById("stat-cum-total").textContent = data.cumulative_total.toFixed(1);
    document.getElementById("cum-stats").style.display = "";

    // Odometer stats (path length since Start — never decreases)
    document.getElementById("stat-odo-mean").textContent  = data.odometer_mean.toFixed(1);
    document.getElementById("stat-odo-max").textContent   = data.odometer_max.toFixed(1);
    document.getElementById("stat-odo-total").textContent = data.odometer_total.toFixed(1);
    document.getElementById("odo-stats").style.display = "";

    highlightActiveAgg(data.displacement_mode);

    // Per-point list — show values that match the active mode family
    const mode = data.displacement_mode;
    const perPoint = mode.startsWith("odometer_")   ? data.odometer_per_point
                   : mode.startsWith("cumulative_") ? data.cumulative_per_point
                   : mode.startsWith("windowed_")   ? data.windowed_per_point
                   : data.point_displacements;
    updatePointList(perPoint);

    // Canvas overlay with live tracked positions
    if (data.current_points && data.current_points.length > 0) {
      redrawTrackedPoints(data.current_points, data.point_displacements);
    }

    // Alert logic — fires only on false→true transition
    if (data.triggered && !lastTriggered) {
      triggerAlert();
    }
    lastTriggered = data.triggered;
    updateUI();
  } catch (e) {
    console.warn("Status poll failed:", e);
  }
}

// ─── Alert ────────────────────────────────────────────────────────────────────

function triggerAlert() {
  // Visual
  const indicator = document.getElementById("detection-indicator");
  indicator.textContent = "MOLE DETECTED";
  indicator.className   = "indicator indicator--triggered";

  // Timer — start on first trigger, don't reset if already running
  if (!detectionStartTime) {
    detectionStartTime = Date.now();
    updateTimer();
    timerInterval = setInterval(updateTimer, 1000);
    document.getElementById("timer-row").style.display = "";
  }

  // Audio — repeating beep unless user has silenced
  if (!silenced) {
    playBeep();
    if (!alertInterval) {
      alertInterval = setInterval(() => { if (!silenced) playBeep(); }, 1000);
    }
    document.getElementById("btn-silence").style.display = "";
  }
}

function silenceAlert() {
  silenced = true;
  clearInterval(alertInterval);
  alertInterval = null;
  document.getElementById("btn-silence").style.display = "none";
}

function updateTimer() {
  if (!detectionStartTime) return;
  const elapsed = Math.floor((Date.now() - detectionStartTime) / 1000);
  const m = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const s = String(elapsed % 60).padStart(2, "0");
  document.getElementById("detection-timer").textContent = `${m}:${s}`;
}

function playBeep() {
  if (!audioCtx) return;
  try {
    const osc  = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);

    osc.type = "sine";
    osc.frequency.value = 880;

    const t = audioCtx.currentTime;
    gain.gain.setValueAtTime(0.0, t);
    gain.gain.linearRampToValueAtTime(0.7, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.8);

    osc.start(t);
    osc.stop(t + 0.8);
  } catch (e) {
    console.warn("Audio playback error:", e);
  }
}

// ─── Per-point display ────────────────────────────────────────────────────────

function updatePointList(displacements) {
  const list = document.getElementById("point-list");
  if (!displacements || displacements.length === 0) {
    list.innerHTML = "";
    return;
  }

  // Bar width scaled so threshold = 100%
  list.innerHTML = displacements.map((d, i) => {
    const hot     = d >= currentThreshold;
    const barPct  = Math.min(100, (d / currentThreshold) * 100).toFixed(1);
    return `<div class="point-item${hot ? " point-item--hot" : ""}">
      <span class="point-dot">${i + 1}</span>
      <span class="point-bar-wrap">
        <span class="point-bar" style="width:${barPct}%"></span>
      </span>
      <span class="point-value">${d.toFixed(1)} px</span>
    </div>`;
  }).join("");
}

// ─── Settings ─────────────────────────────────────────────────────────────────

async function loadSettings() {
  try {
    const resp = await fetch(`${API}/settings`);
    const data = await resp.json();
    document.getElementById("sel-mode").value      = data.detection_mode;
    document.getElementById("inp-threshold").value = data.displacement_threshold;
    document.getElementById("inp-debounce").value  = data.detection_debounce;
    document.getElementById("inp-window").value    = data.accumulation_window;
    currentThreshold = data.displacement_threshold;
  } catch (e) {
    console.warn("Could not load settings:", e);
  }
}

async function applySettings() {
  const mode       = document.getElementById("sel-mode").value;
  const threshold  = parseFloat(document.getElementById("inp-threshold").value);
  const debounce   = parseInt(document.getElementById("inp-debounce").value, 10);
  const windowSize = parseInt(document.getElementById("inp-window").value, 10);
  const hint       = document.getElementById("settings-hint");

  if (isNaN(threshold) || threshold <= 0) {
    hint.textContent = "Threshold must be a positive number.";
    return;
  }

  try {
    const resp = await fetch(`${API}/settings`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        detection_mode:         mode,
        displacement_threshold: threshold,
        detection_debounce:     debounce,
        accumulation_window:    windowSize,
      }),
    });
    const data = await resp.json();
    currentThreshold = data.displacement_threshold;
    hint.textContent = `Saved — ${data.detection_mode} mode, threshold ${data.displacement_threshold} px, debounce ${data.detection_debounce} frames, window ${data.accumulation_window} frames`;
    setTimeout(() => { hint.textContent = ""; }, 3_000);
  } catch (e) {
    hint.textContent = "Failed to save settings.";
    console.error("Settings apply failed:", e);
  }
}

// Highlight which agg-stat box corresponds to the active detection mode
function highlightActiveAgg(mode) {
  const isWindowed   = mode.startsWith("windowed_");
  const isCumulative = mode.startsWith("cumulative_");
  const isOdometer   = mode.startsWith("odometer_");
  const base = isWindowed   ? mode.slice("windowed_".length)
             : isCumulative ? mode.slice("cumulative_".length)
             : isOdometer   ? mode.slice("odometer_".length)
             : mode;
  const isPerFrame = !isWindowed && !isCumulative && !isOdometer;

  // Per-frame row
  ["mean", "max", "total"].forEach(m => {
    document.getElementById(`agg-${m}`)
      .classList.toggle("agg-stat--active", isPerFrame && m === mode);
  });

  // Windowed row
  ["mean", "max", "total"].forEach(m => {
    const el = document.getElementById(`win-${m}`);
    if (el) el.classList.toggle("agg-stat--active", isWindowed && m === base);
  });

  // Cumulative row
  ["mean", "max", "total"].forEach(m => {
    const el = document.getElementById(`cum-${m}`);
    if (el) el.classList.toggle("agg-stat--active", isCumulative && m === base);
  });

  // Odometer row
  ["mean", "max", "total"].forEach(m => {
    const el = document.getElementById(`odo-${m}`);
    if (el) el.classList.toggle("agg-stat--active", isOdometer && m === base);
  });
}

// ─── UI state machine ─────────────────────────────────────────────────────────

function updateUI() {
  const anchorCount  = document.getElementById("anchor-count");
  const btnClear     = document.getElementById("btn-clear");
  const btnCalibrate = document.getElementById("btn-calibrate");
  const btnStart     = document.getElementById("btn-start");
  const btnStop      = document.getElementById("btn-stop");
  const indicator    = document.getElementById("detection-indicator");
  const hint         = document.getElementById("camera-hint");

  const running    = detectorState === "running";
  const calibrated = detectorState === "calibrated" || detectorState === "tracking_lost";

  anchorCount.textContent   = anchors.length;
  btnClear.disabled         = running;
  btnCalibrate.disabled     = anchors.length < 2 || running;
  btnStart.disabled         = !calibrated;
  btnStop.disabled          = !running;

  hint.textContent = running
    ? "Detection active — do not tap the image"
    : "Tap the image to place calibration anchors";

  if (!running) {
    switch (detectorState) {
      case "calibrated":
        indicator.textContent = "Ready";
        indicator.className   = "indicator indicator--idle";
        break;
      case "tracking_lost":
        indicator.textContent = "Tracking Lost \u2014 Recalibrate";
        indicator.className   = "indicator indicator--error";
        break;
      default:
        indicator.textContent = "Idle";
        indicator.className   = "indicator indicator--idle";
    }
  } else if (!indicator.className.includes("triggered")) {
    indicator.textContent = "Watching\u2026";
    indicator.className   = "indicator indicator--running";
  }
}
