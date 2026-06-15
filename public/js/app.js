const ABS_MIN = 45;
const ABS_MAX = 95;
const DIAL_SWEEP_DEG = 270;
const DIAL_START_DEG = 225;

const state = {
  pages: ["blinds", "thermostat", "audio"],
  currentPage: "thermostat",
  thermostat: {
    currentTemp: 72,
    targetTemp: 70,
    lastComfortTarget: 70,
    mode: "cool",
    fan: "auto",
    away: false,
    awayHeat: 55,
    awayCool: 85,
    humidity: 45,
    limits: {
      cool: { min: 65, max: 80 },
      heat: { min: 60, max: 78 },
    },
  },
  audio: {
    playing: false,
    trackIndex: 0,
    progress: 34,
    volume: 42,
    gain: 0,
    bass: 2,
    treble: 4,
    tracks: [
      { title: "Midnight Drive", artist: "Glass Skyline" },
      { title: "Soft Neon", artist: "North Room" },
      { title: "Afterglow Circuit", artist: "The Luma Set" },
    ],
  },
  blinds: {
    room: "living",
    rooms: {
      living: {
        label: "Living Room",
        blinds: [
          { id: "lr-1", name: "Left Window", position: 65 },
          { id: "lr-2", name: "Center Left", position: 65 },
          { id: "lr-3", name: "Center Right", position: 73 },
          { id: "lr-4", name: "Right Window", position: 65 },
        ],
      },
      kitchen: {
        label: "Kitchen",
        blinds: [
          { id: "kit-1", name: "Sink Window", position: 45 },
          { id: "kit-2", name: "Table Window", position: 55 },
          { id: "kit-3", name: "Door Window", position: 75 },
        ],
      },
    },
  },
};

const elements = {
  app: document.getElementById("app"),
  screenTrack: document.getElementById("screenTrack"),
  clock: document.getElementById("clock"),
  toast: document.getElementById("toast"),
  currentTemp: document.getElementById("currentTemp"),
  targetTemp: document.getElementById("targetTemp"),
  humidityValue: document.getElementById("humidityValue"),
  thermoDial: document.getElementById("thermoDial"),
  modeBadge: document.getElementById("modeBadge"),
  runtimeState: document.getElementById("runtimeState"),
  awayToggle: document.getElementById("awayToggle"),
  awayHeatValue: document.getElementById("awayHeatValue"),
  awayCoolValue: document.getElementById("awayCoolValue"),
  fanSummary: document.getElementById("fanSummary"),
  dialMinLabel: document.getElementById("dialMinLabel"),
  dialMaxLabel: document.getElementById("dialMaxLabel"),
  settingsOverlay: document.getElementById("settingsOverlay"),
  settingsButton: document.getElementById("settingsButton"),
  settingsClose: document.getElementById("settingsClose"),
  settingsDone: document.getElementById("settingsDone"),
  blindCards: document.getElementById("blindCards"),
  blindRoomTitle: document.getElementById("blindRoomTitle"),
  trackTitle: document.getElementById("trackTitle"),
  trackArtist: document.getElementById("trackArtist"),
  trackProgress: document.getElementById("trackProgress"),
  playPause: document.getElementById("playPause"),
};

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function titleCase(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => elements.toast.classList.remove("show"), 1600);
}

function updateClock() {
  const now = new Date();
  elements.clock.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function gotoPage(pageName) {
  if (!state.pages.includes(pageName)) return;
  state.currentPage = pageName;
  const index = state.pages.indexOf(pageName);
  elements.screenTrack.style.transform = `translateX(-${index * 33.3333}%)`;
  document.querySelectorAll(".nav-pill").forEach((button) => button.classList.toggle("active", button.dataset.goto === pageName));
}

function goRelative(direction) {
  const currentIndex = state.pages.indexOf(state.currentPage);
  gotoPage(state.pages[clamp(currentIndex + direction, 0, state.pages.length - 1)]);
}

function getModeLimits() {
  const t = state.thermostat;
  const limits = t.limits[t.mode];
  const range = { min: limits.min, max: limits.max };

  // Away safety is separate from normal comfort limits, but the dial should
  // still visually include the active safety target so the UI does not jump.
  if (t.away) {
    if (t.mode === "cool") range.max = Math.max(range.max, t.awayCool);
    if (t.mode === "heat") range.min = Math.min(range.min, t.awayHeat);
  }

  return range;
}

function tempToDialSweep(temp) {
  const { min, max } = getModeLimits();
  const percent = clamp((temp - min) / (max - min), 0, 1);
  return percent * DIAL_SWEEP_DEG;
}

function setDialVisual(temp) {
  const sweep = tempToDialSweep(temp);
  const cssDeg = (DIAL_START_DEG + sweep) % 360;
  const radians = (cssDeg * Math.PI) / 180;
  const radius = 43;
  const knobX = 50 + radius * Math.sin(radians);
  const knobY = 50 - radius * Math.cos(radians);
  elements.thermoDial.style.setProperty("--angle", `${sweep}deg`);
  elements.thermoDial.style.setProperty("--knob-x", `${knobX}%`);
  elements.thermoDial.style.setProperty("--knob-y", `${knobY}%`);
  elements.thermoDial.setAttribute("aria-valuemin", String(getModeLimits().min));
  elements.thermoDial.setAttribute("aria-valuemax", String(getModeLimits().max));
  elements.thermoDial.setAttribute("aria-valuenow", String(Math.round(temp)));
}

function pointerToTemp(clientX, clientY) {
  const rect = elements.thermoDial.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const dx = clientX - cx;
  const dy = clientY - cy;
  const cssDeg = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;
  let sweep = (cssDeg - DIAL_START_DEG + 360) % 360;
  if (sweep > DIAL_SWEEP_DEG) sweep = sweep > 315 ? 0 : DIAL_SWEEP_DEG;
  const percent = clamp(sweep / DIAL_SWEEP_DEG, 0, 1);
  const { min, max } = getModeLimits();
  return Math.round(min + percent * (max - min));
}

function applyAwayTarget() {
  const t = state.thermostat;
  t.targetTemp = t.mode === "heat" ? t.awayHeat : t.awayCool;
}

function setTargetTemp(temp, options = {}) {
  const t = state.thermostat;
  if (t.away && !options.keepAway) {
    t.away = false;
    showToast("Returned home");
  }
  const { min, max } = getModeLimits();
  const next = clamp(Math.round(temp), min, max);
  t.targetTemp = next;
  if (!t.away) t.lastComfortTarget = next;
  renderThermostat();
}

function renderThermostat() {
  const t = state.thermostat;
  const { min, max } = getModeLimits();
  elements.currentTemp.textContent = Math.round(t.currentTemp);
  elements.targetTemp.textContent = Math.round(t.targetTemp);
  elements.humidityValue.textContent = t.humidity;
  elements.awayHeatValue.textContent = t.awayHeat;
  elements.awayCoolValue.textContent = t.awayCool;
  elements.fanSummary.textContent = titleCase(t.fan);
  document.getElementById("coolMinValue").textContent = t.limits.cool.min;
  document.getElementById("coolMaxValue").textContent = t.limits.cool.max;
  document.getElementById("heatMinValue").textContent = t.limits.heat.min;
  document.getElementById("heatMaxValue").textContent = t.limits.heat.max;
  elements.dialMinLabel.textContent = `${min}°`;
  elements.dialMaxLabel.textContent = `${max}°`;

  setDialVisual(t.targetTemp);
  elements.thermoDial.classList.toggle("heat", t.mode === "heat");
  elements.app.classList.toggle("away-active", t.away);
  elements.app.classList.toggle("heat-mode", t.mode === "heat" && !t.away);
  elements.app.classList.toggle("cool-mode", t.mode === "cool" && !t.away);

  const isCalling = t.mode === "cool" ? t.currentTemp > t.targetTemp : t.currentTemp < t.targetTemp;
  const action = isCalling ? (t.mode === "cool" ? "Cooling" : "Heating") : "Idle";
  elements.runtimeState.textContent = t.away ? `Away • ${action}` : action;

  elements.modeBadge.textContent = t.away ? `${titleCase(t.mode)} Safety` : `${titleCase(t.mode)} Target`;
  elements.modeBadge.className = `mode-badge ${t.mode}`;
  elements.awayToggle.classList.toggle("active", t.away);
  elements.awayToggle.classList.toggle("home-state", t.away);
  elements.awayToggle.textContent = t.away ? "Home" : "Away";

  document.querySelectorAll(".mode-button[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === t.mode);
  });
  document.querySelectorAll(".segment[data-fan]").forEach((button) => {
    button.classList.toggle("active", button.dataset.fan === t.fan);
  });
}

function setMode(mode) {
  const t = state.thermostat;
  t.mode = mode;
  if (t.away) {
    applyAwayTarget();
  } else {
    const { min, max } = getModeLimits();
    t.targetTemp = clamp(t.targetTemp, min, max);
    t.lastComfortTarget = clamp(t.lastComfortTarget, min, max);
  }
  renderThermostat();
  showToast(`${titleCase(mode)} mode selected`);
}

function adjustSetpoint(delta) {
  setTargetTemp(state.thermostat.targetTemp + delta);
}

function toggleAway() {
  const t = state.thermostat;
  t.away = !t.away;
  if (t.away) {
    t.lastComfortTarget = t.targetTemp;
    applyAwayTarget();
    showToast("Away mode active");
  } else {
    const { min, max } = getModeLimits();
    t.targetTemp = clamp(t.lastComfortTarget, min, max);
    showToast("Home comfort restored");
  }
  renderThermostat();
}

function setAwaySafety(kind, delta) {
  const t = state.thermostat;
  if (kind === "heat") t.awayHeat = clamp(t.awayHeat + delta, 45, 72);
  if (kind === "cool") t.awayCool = clamp(t.awayCool + delta, 72, 95);
  if (t.away) applyAwayTarget();
  renderThermostat();
}

function adjustLimit(mode, bound, delta) {
  const limits = state.thermostat.limits[mode];
  if (bound === "min") limits.min = clamp(limits.min + delta, ABS_MIN, limits.max - 2);
  if (bound === "max") limits.max = clamp(limits.max + delta, limits.min + 2, ABS_MAX);

  if (state.thermostat.mode === mode && !state.thermostat.away) {
    state.thermostat.targetTemp = clamp(state.thermostat.targetTemp, limits.min, limits.max);
    state.thermostat.lastComfortTarget = clamp(state.thermostat.lastComfortTarget, limits.min, limits.max);
  }
  renderThermostat();
}

function openSettings() {
  elements.settingsOverlay.classList.add("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "false");
}
function closeSettings() {
  elements.settingsOverlay.classList.remove("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "true");
}

function renderAudio() {
  const a = state.audio;
  const track = a.tracks[a.trackIndex];
  elements.trackTitle.textContent = track.title;
  elements.trackArtist.textContent = track.artist;
  elements.trackProgress.style.width = `${a.progress}%`;
  elements.playPause.textContent = a.playing ? "⏸" : "▶";
  ["volume", "gain", "bass", "treble"].forEach((name) => {
    const slider = document.getElementById(`${name}Slider`);
    const value = document.getElementById(`${name}Value`);
    if (slider) slider.value = a[name];
    if (value) value.textContent = a[name];
  });
}

function changeTrack(delta) {
  const a = state.audio;
  a.trackIndex = (a.trackIndex + delta + a.tracks.length) % a.tracks.length;
  a.progress = 8;
  renderAudio();
}
function togglePlayback() {
  state.audio.playing = !state.audio.playing;
  renderAudio();
}

function renderBlinds() {
  const room = state.blinds.rooms[state.blinds.room];
  elements.blindRoomTitle.textContent = room.label;
  elements.blindCards.innerHTML = "";
  document.querySelectorAll(".room-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.room === state.blinds.room));

  room.blinds.forEach((blind) => {
    const card = document.createElement("div");
    card.className = "blind-card";
    card.dataset.blindCard = blind.id;
    card.style.setProperty("--blind-open", `${blind.position}%`);
    card.innerHTML = `
      <div class="blind-top"><div class="blind-name">${blind.name}</div><div class="blind-percent">${blind.position}%</div></div>
      <div class="blind-actions two"><button class="blind-action primary" data-blind-id="${blind.id}" data-action="open">Open</button><button class="blind-action close-blind" data-blind-id="${blind.id}" data-action="close">Close</button></div>
      <div class="shade-stage" data-blind-stage="${blind.id}" role="slider" aria-label="${blind.name} position" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${blind.position}" tabindex="0">
        <div class="blind-window" aria-hidden="true"></div>
        <div class="blind-handle" aria-hidden="true"></div>
      </div>
      <div class="blind-actions single"><button class="blind-action" data-blind-id="${blind.id}" data-action="stop">Stop</button></div>
    `;
    elements.blindCards.appendChild(card);
  });
}

function setBlindPosition(blindId, position) {
  const room = state.blinds.rooms[state.blinds.room];
  const blind = room.blinds.find((item) => item.id === blindId);
  if (!blind) return;
  blind.position = clamp(Number(position), 0, 100);
  const card = document.querySelector(`[data-blind-card="${blindId}"]`);
  if (!card) return;
  card.style.setProperty("--blind-open", `${blind.position}%`);
  card.querySelector(".blind-percent").textContent = `${blind.position}%`;
  card.querySelector("[data-blind-stage]").setAttribute("aria-valuenow", String(blind.position));
}

function applyBlindAction(action, blindId = null) {
  const room = state.blinds.rooms[state.blinds.room];
  const updateBlind = (blind) => {
    if (action.includes("open")) blind.position = 100;
    if (action.includes("close")) blind.position = 0;
  };
  if (blindId) {
    const blind = room.blinds.find((item) => item.id === blindId);
    if (!blind) return;
    updateBlind(blind);
  } else {
    room.blinds.forEach(updateBlind);
  }
  renderBlinds();
}

function isInteractiveTarget(target) {
  return Boolean(target.closest("button, input, .thermo-dial, .settings-sheet, .settings-overlay, [data-blind-stage]"));
}

function bindSwipeNavigation() {
  let pointerId = null, startX = 0, startY = 0, moved = false;
  elements.app.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    if (isInteractiveTarget(event.target)) return;
    pointerId = event.pointerId; startX = event.clientX; startY = event.clientY; moved = false;
  });
  elements.app.addEventListener("pointermove", (event) => {
    if (pointerId !== event.pointerId) return;
    const dx = event.clientX - startX, dy = event.clientY - startY;
    if (Math.abs(dx) > 12 || Math.abs(dy) > 12) moved = true;
  });
  const finishSwipe = (event) => {
    if (pointerId !== event.pointerId) return;
    const dx = event.clientX - startX, dy = event.clientY - startY;
    pointerId = null;
    if (moved && Math.abs(dx) > 85 && Math.abs(dx) > Math.abs(dy) * 1.35) goRelative(dx < 0 ? 1 : -1);
  };
  elements.app.addEventListener("pointerup", finishSwipe);
  elements.app.addEventListener("pointercancel", () => { pointerId = null; });
}

function bindThermostatDial() {
  let dragging = false;
  const updateFromEvent = (event) => setTargetTemp(pointerToTemp(event.clientX, event.clientY));
  elements.thermoDial.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault(); event.stopPropagation(); dragging = true;
    elements.thermoDial.setPointerCapture(event.pointerId); updateFromEvent(event);
  });
  elements.thermoDial.addEventListener("pointermove", (event) => { if (dragging) { event.preventDefault(); updateFromEvent(event); } });
  const finishDial = (event) => { if (!dragging) return; dragging = false; try { elements.thermoDial.releasePointerCapture(event.pointerId); } catch (_) {} };
  elements.thermoDial.addEventListener("pointerup", finishDial);
  elements.thermoDial.addEventListener("pointercancel", finishDial);
  elements.thermoDial.addEventListener("keydown", (event) => {
    if (["ArrowUp","ArrowRight"].includes(event.key)) { event.preventDefault(); adjustSetpoint(1); }
    if (["ArrowDown","ArrowLeft"].includes(event.key)) { event.preventDefault(); adjustSetpoint(-1); }
  });
}

function bindEvents() {
  document.querySelectorAll(".nav-pill").forEach((button) => button.addEventListener("click", () => gotoPage(button.dataset.goto)));
  document.getElementById("tempDown").addEventListener("click", () => adjustSetpoint(-1));
  document.getElementById("tempUp").addEventListener("click", () => adjustSetpoint(1));
  elements.awayToggle.addEventListener("click", toggleAway);

  elements.settingsButton.addEventListener("click", openSettings);
  elements.settingsClose.addEventListener("click", closeSettings);
  elements.settingsDone.addEventListener("click", closeSettings);
  document.querySelectorAll("[data-close-settings]").forEach((el) => el.addEventListener("click", closeSettings));
  document.querySelectorAll("[data-away-adjust]").forEach((button) => button.addEventListener("click", () => {
    const [kind, delta] = button.dataset.awayAdjust.split(":");
    setAwaySafety(kind, Number(delta));
  }));
  document.querySelectorAll("[data-limit-adjust]").forEach((button) => button.addEventListener("click", () => {
    const [mode, bound, delta] = button.dataset.limitAdjust.split(":");
    adjustLimit(mode, bound, Number(delta));
  }));
  document.querySelectorAll(".mode-button[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  document.querySelectorAll(".segment[data-fan]").forEach((button) => button.addEventListener("click", () => { state.thermostat.fan = button.dataset.fan; renderThermostat(); }));

  document.getElementById("prevTrack").addEventListener("click", () => changeTrack(-1));
  document.getElementById("nextTrack").addEventListener("click", () => changeTrack(1));
  elements.playPause.addEventListener("click", togglePlayback);
  ["volume", "gain", "bass", "treble"].forEach((name) => {
    document.getElementById(`${name}Slider`).addEventListener("input", (event) => {
      state.audio[name] = Number(event.target.value); renderAudio();
    });
  });

  document.querySelectorAll(".room-tab").forEach((tab) => tab.addEventListener("click", () => { state.blinds.room = tab.dataset.room; renderBlinds(); }));
  document.querySelectorAll("[data-blind-action]").forEach((button) => button.addEventListener("click", () => applyBlindAction(button.dataset.blindAction)));
  elements.blindCards.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-blind-id]");
    if (!button) return;
    if (button.dataset.action === "stop") return;
    applyBlindAction(button.dataset.action, button.dataset.blindId);
  });

  let activeShadeId = null;
  const updateBlindFromPointer = (event) => {
    const stage = document.querySelector(`[data-blind-stage="${activeShadeId}"]`) || event.target.closest("[data-blind-stage]");
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    const pct = clamp(Math.round(((rect.bottom - event.clientY) / rect.height) * 100), 0, 100);
    setBlindPosition(stage.dataset.blindStage, pct);
  };
  elements.blindCards.addEventListener("pointerdown", (event) => {
    const stage = event.target.closest("[data-blind-stage]");
    if (!stage) return;
    event.preventDefault(); event.stopPropagation(); activeShadeId = stage.dataset.blindStage; stage.setPointerCapture(event.pointerId); updateBlindFromPointer(event);
  });
  elements.blindCards.addEventListener("pointermove", (event) => { if (activeShadeId) { event.preventDefault(); updateBlindFromPointer(event); } });
  const finishShadeDrag = (event) => {
    if (!activeShadeId) return;
    const stage = document.querySelector(`[data-blind-stage="${activeShadeId}"]`);
    try { stage?.releasePointerCapture(event.pointerId); } catch (_) {}
    activeShadeId = null;
  };
  elements.blindCards.addEventListener("pointerup", finishShadeDrag);
  elements.blindCards.addEventListener("pointercancel", finishShadeDrag);

  bindSwipeNavigation();
  bindThermostatDial();
  window.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") goRelative(1);
    if (event.key === "ArrowLeft") goRelative(-1);
    if (event.key === "+" || event.key === "=") adjustSetpoint(1);
    if (event.key === "-" || event.key === "_") adjustSetpoint(-1);
    if (event.key === "Escape") closeSettings();
  });
}

function mockSensorDrift() {
  const t = state.thermostat;
  const direction = t.currentTemp > t.targetTemp ? -0.1 : 0.08;
  const idleNoise = (Math.random() - 0.5) * 0.06;
  t.currentTemp = Number((t.currentTemp + direction * 0.18 + idleNoise).toFixed(1));
  renderThermostat();
}
function mockTrackProgress() {
  if (!state.audio.playing) return;
  state.audio.progress += 1;
  if (state.audio.progress > 100) changeTrack(1);
  renderAudio();
}

function init() {
  state.thermostat.away = false;
  bindEvents();
  updateClock();
  renderThermostat();
  renderAudio();
  renderBlinds();
  gotoPage("thermostat");
  setInterval(updateClock, 1000);
  setInterval(mockSensorDrift, 4500);
  setInterval(mockTrackProgress, 1200);
}

init();
