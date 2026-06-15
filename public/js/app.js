const ABS_MIN = 45;
const ABS_MAX = 95;
const DIAL_SWEEP_DEG = 270;
const DIAL_START_DEG = 225;
const CONFIG_STORAGE_KEY = "smartThermostat.config.v2";
const HA_SYNC_INTERVAL_MS = 3000;
const HA_SYNC_AFTER_COMMAND_DELAYS = [900, 2400, 5200];
const HA_AUDIO_SYNC_INTERVAL_MS = 2500;
const HA_AUDIO_SYNC_AFTER_COMMAND_DELAYS = [700, 2200, 5000];
let haSyncInFlight = false;
let haSyncLastError = "";
let haAudioSyncInFlight = false;
let haAudioControlSyncInFlight = false;
let haAudioSyncLastError = "";
let audioVolumeDebounce = null;
let audioToneDebounces = { gain: null, bass: null, treble: null };
let audioTrackCommandLock = null;
let audioPlaybackLockedUntil = 0;
const AUDIO_TRACK_ACK_TIMEOUT_MS = 6500;
const AUDIO_PLAYBACK_LOCK_MS = 1400;
const AUDIO_SLIDER_RELEASE_MS = 900;
const AUDIO_VOLUME_SETTLE_MS = 1800;
const AUDIO_TONE_SETTLE_MS = 1500;
let lastBlindUserInteractionAt = 0;
let lastAudioUserInteractionAt = 0;
let audioSliderActive = { name: "", until: 0 };
let audioVolumeHoldUntil = 0;
let audioToneHoldUntil = { gain: 0, bass: 0, treble: 0 };

const defaultBlindConfig = {
  room: "living",
  rooms: {
    living: {
      label: "Living Room",
      blinds: [
        { id: "lr-1", name: "Left Window", position: 65, haEntityId: "", haName: "" },
        { id: "lr-2", name: "Center Left", position: 65, haEntityId: "", haName: "" },
        { id: "lr-3", name: "Center Right", position: 73, haEntityId: "", haName: "" },
        { id: "lr-4", name: "Right Window", position: 65, haEntityId: "", haName: "" },
      ],
    },
    kitchen: {
      label: "Kitchen",
      blinds: [
        { id: "kit-1", name: "Sink Window", position: 45, haEntityId: "", haName: "" },
        { id: "kit-2", name: "Table Window", position: 55, haEntityId: "", haName: "" },
        { id: "kit-3", name: "Door Window", position: 75, haEntityId: "", haName: "" },
      ],
    },
  },
};

const defaultIntegrations = {
  homeAssistant: {
    url: "",
    token: "",
    coverEntities: [],
    mediaPlayerEntities: [],
    selectedMediaPlayerId: "",
    audioControlEntities: { gain: null, bass: null, treble: null },
  },
};

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
    entityId: "",
    entityName: "",
    status: "idle",
    title: "",
    artist: "",
    album: "",
    artUrl: "",
    source: "",
    sourceList: [],
    mediaContentId: "",
    mediaPosition: null,
    mediaDuration: null,
    tracks: [],
  },
  blinds: JSON.parse(JSON.stringify(defaultBlindConfig)),
  integrations: JSON.parse(JSON.stringify(defaultIntegrations)),
  entityPicker: { roomKey: null, blindId: null },
  diagnostics: { haLogs: [] },
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
  settingsSheet: document.getElementById("settingsSheet"),
  settingsButton: document.getElementById("settingsButton"),
  headerCurrentTemp: document.getElementById("headerCurrentTemp"),
  headerSetTemp: document.getElementById("headerSetTemp"),
  headerSetPill: document.getElementById("headerSetPill"),
  settingsClose: document.getElementById("settingsClose"),
  settingsDone: document.getElementById("settingsDone"),
  settingsTitle: document.getElementById("settingsTitle"),
  settingsEyebrow: document.getElementById("settingsEyebrow"),
  settingsFooter: document.getElementById("settingsFooter"),
  thermostatSettingsView: document.getElementById("thermostatSettingsView"),
  blindSettingsView: document.getElementById("blindSettingsView"),
  audioSettingsView: document.getElementById("audioSettingsView"),
  blindSetupView: document.getElementById("blindSetupView"),
  blindHaView: document.getElementById("blindHaView"),
  audioHaView: document.getElementById("audioHaView"),
  roomTabs: document.getElementById("roomTabs"),
  roomConfigList: document.getElementById("roomConfigList"),
  blindCards: document.getElementById("blindCards"),
  blindRoomTitle: document.getElementById("blindRoomTitle"),
  haUrlInput: document.getElementById("haUrlInput"),
  haTokenInput: document.getElementById("haTokenInput"),
  audioHaUrlInput: document.getElementById("audioHaUrlInput"),
  audioHaTokenInput: document.getElementById("audioHaTokenInput"),
  haCoverList: document.getElementById("haCoverList"),
  haEntityCount: document.getElementById("haEntityCount"),
  haStatusLine: document.getElementById("haStatusLine"),
  haLogList: document.getElementById("haLogList"),
  testHaConnection: document.getElementById("testHaConnection"),
  clearHaLog: document.getElementById("clearHaLog"),
  loadCoverEntities: document.getElementById("loadCoverEntities"),
  entityPickerOverlay: document.getElementById("entityPickerOverlay"),
  entityPickerTitle: document.getElementById("entityPickerTitle"),
  entityPickerList: document.getElementById("entityPickerList"),
  entityPickerClose: document.getElementById("entityPickerClose"),
  trackTitle: document.getElementById("trackTitle"),
  trackArtist: document.getElementById("trackArtist"),
  trackAlbum: document.getElementById("trackAlbum"),
  trackProgress: document.getElementById("trackProgress"),
  playPause: document.getElementById("playPause"),
  prevTrack: document.getElementById("prevTrack"),
  nextTrack: document.getElementById("nextTrack"),
  audioSourceCard: document.getElementById("audioSourceCard"),
  audioSourceSelect: document.getElementById("audioSourceSelect"),
  audioSourceValue: document.getElementById("audioSourceValue"),
  albumArt: document.getElementById("albumArt"),
  albumInitials: document.getElementById("albumInitials"),
  audioPlayerName: document.getElementById("audioPlayerName"),
  audioDeviceState: document.getElementById("audioDeviceState"),
  audioSetupView: document.getElementById("audioSetupView"),
  mediaPlayerList: document.getElementById("mediaPlayerList"),
  mediaPlayerCount: document.getElementById("mediaPlayerCount"),
  loadMediaPlayers: document.getElementById("loadMediaPlayers"),
  loadAudioMediaPlayers: document.getElementById("loadAudioMediaPlayers"),
  backToAudioSetup: document.getElementById("backToAudioSetup"),
  selectedMediaDeviceText: document.getElementById("selectedMediaDeviceText"),
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function titleCase(value) {
  return String(value || "").charAt(0).toUpperCase() + String(value || "").slice(1);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function maskToken(token) {
  const value = String(token || "").trim();
  if (!value) return "";
  if (value.length <= 12) return "••••";
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

function redactForLog(value) {
  let text = String(value ?? "");
  const token = state?.integrations?.homeAssistant?.token || "";
  if (token) text = text.split(token).join(maskToken(token));
  text = text.replace(/Bearer\s+[A-Za-z0-9._\-]+/g, "Bearer [redacted]");
  return text;
}

function renderHaLog() {
  if (!elements.haLogList) return;
  const logs = state.diagnostics.haLogs || [];
  elements.haLogList.innerHTML = logs.length
    ? logs.map((entry) => `
      <div class="ha-log-row ${entry.level}">
        <span class="ha-log-time">${entry.time}</span>
        <strong class="ha-log-level">${entry.level.toUpperCase()}</strong>
        <div class="ha-log-message">${entry.message}</div>
      </div>
    `).join("")
    : `<div class="empty-state compact">No connection attempts yet.</div>`;
  elements.haLogList.scrollTop = elements.haLogList.scrollHeight;
}

function setHaStatus(message, level = "info") {
  if (!elements.haStatusLine) return;
  elements.haStatusLine.textContent = message;
  elements.haStatusLine.className = `ha-status-line ${level}`;
}

function addHaLog(level, message, detail = "") {
  const now = new Date();
  const fullMessage = detail ? `${message} — ${redactForLog(detail)}` : message;
  state.diagnostics.haLogs.push({
    level,
    time: now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }),
    message: fullMessage,
  });
  state.diagnostics.haLogs = state.diagnostics.haLogs.slice(-40);
  renderHaLog();
  const logger = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
  logger(`[HA ${level}] ${fullMessage}`);
}

function clearHaLog() {
  state.diagnostics.haLogs = [];
  renderHaLog();
  setHaStatus("Connection log cleared.", "info");
}

function readHaFieldsFromScreen(context = "blinds") {
  const urlInput = context === "audio" ? elements.audioHaUrlInput : elements.haUrlInput;
  const tokenInput = context === "audio" ? elements.audioHaTokenInput : elements.haTokenInput;
  if (urlInput) state.integrations.homeAssistant.url = urlInput.value.trim();
  if (tokenInput) state.integrations.homeAssistant.token = tokenInput.value.trim();
}


function slugify(value) {
  const base = String(value || "room").toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "room";
  let key = base;
  let index = 2;
  while (state.blinds.rooms[key]) {
    key = `${base}-${index}`;
    index += 1;
  }
  return key;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => elements.toast.classList.remove("show"), 1600);
}

function getRoomKeys() {
  return Object.keys(state.blinds.rooms);
}

function getActiveRoom() {
  const keys = getRoomKeys();
  if (!state.blinds.rooms[state.blinds.room]) state.blinds.room = keys[0];
  return state.blinds.rooms[state.blinds.room];
}

function buildSavedConfig() {
  return {
    version: 4,
    blinds: state.blinds,
    integrations: state.integrations,
  };
}

function loadSavedConfig() {
  try {
    const raw = localStorage.getItem(CONFIG_STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved?.blinds?.rooms) state.blinds = saved.blinds;
    if (saved?.integrations?.homeAssistant) {
      state.integrations.homeAssistant = {
        ...clone(defaultIntegrations.homeAssistant),
        ...saved.integrations.homeAssistant,
      };
    }
  } catch (error) {
    console.warn("Unable to load saved config", error);
  }
}

function saveConfig(options = {}) {
  localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(buildSavedConfig(), null, 2));
  if (options.toast) showToast("Config saved");
  // Future Raspberry Pi backend hook: POST buildSavedConfig() to a local service
  // that writes /etc/smart-thermostat/config.json or the project config file.
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
  if (pageName === "blinds") pollHomeAssistantLinkedCovers({ force: true });
  if (pageName === "audio") pollHomeAssistantMediaPlayer({ force: true });
}

function goRelative(direction) {
  const currentIndex = state.pages.indexOf(state.currentPage);
  gotoPage(state.pages[clamp(currentIndex + direction, 0, state.pages.length - 1)]);
}

function getModeLimits() {
  const t = state.thermostat;
  const limits = t.limits[t.mode];
  const range = { min: limits.min, max: limits.max };
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
  if (elements.headerCurrentTemp) elements.headerCurrentTemp.textContent = `${Math.round(t.currentTemp)}°`;
  if (elements.headerSetTemp) elements.headerSetTemp.textContent = `${Math.round(t.targetTemp)}°`;
  if (elements.headerSetPill) {
    elements.headerSetPill.classList.toggle("heat", t.mode === "heat" && !t.away);
    elements.headerSetPill.classList.toggle("cool", t.mode === "cool" && !t.away);
    elements.headerSetPill.classList.toggle("away", t.away);
  }
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

function hideAllSettingsViews() {
  [elements.thermostatSettingsView, elements.blindSettingsView, elements.audioSettingsView].forEach((view) => { view.hidden = true; });
  elements.settingsSheet.classList.remove("full-setup", "ha-focus");
  elements.settingsFooter.hidden = false;
}

function openSettings() {
  hideAllSettingsViews();
  if (state.currentPage === "blinds") {
    elements.settingsTitle.textContent = "Blind Setup";
    elements.settingsEyebrow.textContent = "Shade Setup";
    elements.settingsSheet.classList.add("full-setup");
    elements.blindSettingsView.hidden = false;
    showBlindSetupView();
  } else if (state.currentPage === "audio") {
    elements.audioSettingsView.hidden = false;
    showAudioSetupView();
  } else {
    elements.settingsTitle.textContent = "Comfort Setup";
    elements.settingsEyebrow.textContent = "Panel Settings";
    elements.thermostatSettingsView.hidden = false;
  }
  elements.settingsOverlay.classList.add("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "false");
}

function closeSettings() {
  elements.settingsOverlay.classList.remove("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "true");
}

function showBlindSetupView() {
  elements.blindSetupView.hidden = false;
  elements.blindHaView.hidden = true;
  elements.settingsSheet.classList.add("full-setup");
  elements.settingsSheet.classList.remove("ha-focus");
  elements.settingsTitle.textContent = "Blind Setup";
  elements.settingsEyebrow.textContent = "Shade Setup";
  elements.settingsFooter.hidden = false;
  renderRoomConfigList();
}

function showBlindHaView() {
  elements.blindSetupView.hidden = true;
  elements.blindHaView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "ha-focus");
  elements.settingsTitle.textContent = "Home Assistant Config";
  elements.settingsEyebrow.textContent = "Blinds";
  elements.settingsFooter.hidden = true;
  renderHaFields("blinds");
  renderCoverPreview();
}

function showAudioSetupView() {
  if (elements.audioSetupView) elements.audioSetupView.hidden = false;
  if (elements.audioHaView) elements.audioHaView.hidden = true;
  elements.settingsSheet.classList.add("full-setup");
  elements.settingsSheet.classList.remove("ha-focus");
  elements.settingsTitle.textContent = "Audio Setup";
  elements.settingsEyebrow.textContent = "Media Setup";
  elements.settingsFooter.hidden = false;
  renderHaFields("audio");
  renderMediaPlayerList();
}

function showAudioHaView() {
  if (elements.audioSetupView) elements.audioSetupView.hidden = true;
  if (elements.audioHaView) elements.audioHaView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "ha-focus");
  elements.settingsTitle.textContent = "Home Assistant Config";
  elements.settingsEyebrow.textContent = "Audio";
  elements.settingsFooter.hidden = true;
  renderHaFields("audio");
}

function renderHaFields(context) {
  const ha = state.integrations.homeAssistant;
  if (context === "audio") {
    elements.audioHaUrlInput.value = ha.url || "";
    elements.audioHaTokenInput.value = ha.token || "";
  } else {
    elements.haUrlInput.value = ha.url || "";
    elements.haTokenInput.value = ha.token || "";
  }
}

function saveHaFields(context, options = {}) {
  readHaFieldsFromScreen(context);
  saveConfig({ toast: !options.silent });
  if (!options.silent) {
    addHaLog("info", `${titleCase(context)} Home Assistant config saved`, `URL: ${state.integrations.homeAssistant.url || "not set"}, token: ${maskToken(state.integrations.homeAssistant.token) || "not set"}`);
    setHaStatus("Config saved locally. Load cover entries to test it.", "info");
  }
  renderCoverPreview();
}

function getMediaPlayers() {
  return state.integrations.homeAssistant.mediaPlayerEntities || [];
}

function getSelectedMediaPlayer() {
  const selected = state.integrations.homeAssistant.selectedMediaPlayerId || state.audio.entityId || "";
  return getMediaPlayers().find((entity) => entity.entityId === selected) || null;
}

function normalizeVolume(entity, fallback = state.audio.volume) {
  const raw = entity?.volumeLevel;
  if (raw !== undefined && raw !== null && raw !== "") return clamp(Math.round(Number(raw) * 100), 0, 100);
  return clamp(Number(fallback || 0), 0, 100);
}

function mediaProgressPercent(entity) {
  const duration = Number(entity?.mediaDuration);
  const position = Number(entity?.mediaPosition);
  if (Number.isFinite(duration) && duration > 0 && Number.isFinite(position)) {
    return clamp(Math.round((position / duration) * 100), 0, 100);
  }
  return state.audio.playing ? state.audio.progress : 0;
}

function initialsFromName(value) {
  const parts = String(value || "Audio").trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part.charAt(0).toUpperCase()).join("") || "♪";
}

function normalizeSourceList(value) {
  if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

function formatControlValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return Math.abs(number % 1) < 0.001 ? String(Math.round(number)) : number.toFixed(1);
}

function isAudioSliderActive(name) {
  return audioSliderActive.name === name && Date.now() < audioSliderActive.until;
}

function markAudioSliderActive(name, duration = AUDIO_SLIDER_RELEASE_MS) {
  audioSliderActive = { name, until: Date.now() + duration };
}

function releaseAudioSlider(name) {
  if (!name || audioSliderActive.name === name) {
    audioSliderActive.until = Math.min(audioSliderActive.until, Date.now() + 120);
  }
}

function getAudioToneControls() {
  const ha = state.integrations.homeAssistant;
  if (!ha.audioControlEntities) ha.audioControlEntities = { gain: null, bass: null, treble: null };
  return ha.audioControlEntities;
}

function getAudioToneControl(kind) {
  return getAudioToneControls()[kind] || null;
}

function normalizeAudioControlValue(control, fallback = 0) {
  const raw = control?.value ?? control?.state;
  const number = Number(raw);
  return Number.isFinite(number) ? number : Number(fallback || 0);
}

function applyAudioControlEntity(kind, control, options = {}) {
  if (!["gain", "bass", "treble"].includes(kind)) return false;
  const controls = getAudioToneControls();
  controls[kind] = control || null;
  if (!control) return false;
  if (Date.now() < (audioToneHoldUntil[kind] || 0) && !options.force) return false;
  state.audio[kind] = normalizeAudioControlValue(control, state.audio[kind]);
  return true;
}

function applyAudioControls(controls, options = {}) {
  if (!controls) return false;
  let changed = false;
  ["gain", "bass", "treble"].forEach((kind) => {
    if (Object.prototype.hasOwnProperty.call(controls, kind)) {
      if (applyAudioControlEntity(kind, controls[kind], options)) changed = true;
    }
  });
  return changed;
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    let payload = null;
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      throw new Error(payload?.error || `Local backend returned ${response.status}`);
    }
    return payload || {};
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Local backend request timed out");
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function setRangeVisual(slider) {
  if (!slider) return;
  const min = Number(slider.min || 0);
  const max = Number(slider.max || 100);
  const value = Number(slider.value || 0);
  const pct = max === min ? 0 : clamp(((value - min) / (max - min)) * 100, 0, 100);
  slider.style.setProperty("--range-fill", `${pct}%`);
}

function updateAllRangeVisuals() {
  ["volume", "gain", "bass", "treble"].forEach((name) => setRangeVisual(document.getElementById(`${name}Slider`)));
}

function mediaSignatureFromValues(title, contentId, artUrl) {
  return [title || "", contentId || "", artUrl || ""].join("|");
}

function mediaSignatureFromEntity(entity) {
  return mediaSignatureFromValues(entity?.mediaTitle, entity?.mediaContentId, entity?.pictureUrl);
}

function getCurrentAudioSignature() {
  return mediaSignatureFromValues(state.audio.title, state.audio.mediaContentId, state.audio.artUrl);
}

function isAudioTrackLocked() {
  if (!audioTrackCommandLock) return false;
  if (Date.now() > audioTrackCommandLock.until) {
    audioTrackCommandLock = null;
    return false;
  }
  return true;
}

function maybeResolveAudioTrackLock(entity = null) {
  if (!audioTrackCommandLock) return;
  const signature = entity ? mediaSignatureFromEntity(entity) : getCurrentAudioSignature();
  if (Date.now() > audioTrackCommandLock.until || (signature && signature !== audioTrackCommandLock.before)) {
    audioTrackCommandLock = null;
  }
}

function applyMediaEntityState(entity) {
  if (!entity?.entityId) return;
  state.audio.entityId = entity.entityId;
  state.audio.entityName = entity.name || entity.entityId;
  state.audio.status = entity.state || "unknown";
  state.audio.playing = String(entity.state || "").toLowerCase() === "playing";
  state.audio.title = entity.mediaTitle || "";
  state.audio.artist = entity.mediaArtist || "";
  state.audio.album = entity.mediaAlbum || "";
  state.audio.artUrl = entity.pictureUrl || "";
  state.audio.source = entity.source || "";
  state.audio.sourceList = normalizeSourceList(entity.sourceList);
  state.audio.mediaContentId = entity.mediaContentId || "";
  if (Date.now() >= audioVolumeHoldUntil && !isAudioSliderActive("volume")) {
    state.audio.volume = normalizeVolume(entity, state.audio.volume);
  }
  state.audio.mediaPosition = entity.mediaPosition ?? null;
  state.audio.mediaDuration = entity.mediaDuration ?? null;
  maybeResolveAudioTrackLock(entity);
}

function renderAudio() {
  const a = state.audio;
  const selected = getSelectedMediaPlayer();
  maybeResolveAudioTrackLock();

  const hasDevice = Boolean(state.integrations.homeAssistant.selectedMediaPlayerId || a.entityId);
  const deviceName = a.entityName || selected?.name || "No device selected";
  const title = hasDevice ? (a.title || "Nothing Playing") : "Select Audio Device";
  const artist = hasDevice ? (a.artist || deviceName || "Waiting for media") : "Open settings to choose a media player.";
  const album = hasDevice ? (a.album || a.source || "") : "";
  const progress = selected ? mediaProgressPercent(selected) : (a.playing ? a.progress : 0);
  const artUrl = a.artUrl || selected?.pictureUrl || "";
  const stateText = hasDevice ? titleCase(a.status || selected?.state || "idle") : "Setup";

  elements.trackTitle.textContent = title;
  elements.trackArtist.textContent = artist;
  if (elements.trackAlbum) elements.trackAlbum.textContent = album;
  elements.trackProgress.style.width = `${progress}%`;
  elements.playPause.textContent = a.playing ? "⏸" : "▶";
  if (elements.audioPlayerName) elements.audioPlayerName.textContent = deviceName;
  if (elements.audioDeviceState) {
    elements.audioDeviceState.textContent = stateText;
    elements.audioDeviceState.classList.toggle("playing", a.playing);
  }
  if (elements.albumArt) {
    elements.albumArt.classList.toggle("has-image", Boolean(artUrl));
    elements.albumArt.style.backgroundImage = artUrl ? `linear-gradient(180deg, rgba(0,0,0,.02), rgba(0,0,0,.18)), url("${artUrl}")` : "";
  }
  if (elements.albumInitials) elements.albumInitials.textContent = hasDevice ? initialsFromName(title || deviceName) : "♪";

  const trackLocked = isAudioTrackLocked();
  if (elements.prevTrack) elements.prevTrack.disabled = trackLocked;
  if (elements.nextTrack) elements.nextTrack.disabled = trackLocked;
  if (elements.playPause) elements.playPause.disabled = Date.now() < audioPlaybackLockedUntil;

  const sourceList = normalizeSourceList(a.sourceList || selected?.sourceList);
  if (elements.audioSourceCard && elements.audioSourceSelect) {
    const showSources = hasDevice && sourceList.length > 0;
    elements.audioSourceCard.hidden = !showSources;
    if (showSources) {
      const selectedSource = a.source || sourceList[0] || "";
      if (document.activeElement !== elements.audioSourceSelect) {
        elements.audioSourceSelect.innerHTML = sourceList.map((source) =>
          `<option value="${escapeHtml(source)}" ${source === selectedSource ? "selected" : ""}>${escapeHtml(source)}</option>`
        ).join("");
        elements.audioSourceSelect.value = selectedSource;
      }
      if (elements.audioSourceValue) elements.audioSourceValue.textContent = selectedSource || "—";
    }
  }

  ["volume", "gain", "bass", "treble"].forEach((name) => {
    const slider = document.getElementById(`${name}Slider`);
    const value = document.getElementById(`${name}Value`);
    if (!slider) return;

    if (name !== "volume") {
      const control = getAudioToneControl(name);
      const min = control?.min ?? -10;
      const max = control?.max ?? 10;
      const step = control?.step ?? 1;
      slider.min = Number.isFinite(Number(min)) ? String(min) : "-10";
      slider.max = Number.isFinite(Number(max)) ? String(max) : "10";
      slider.step = Number.isFinite(Number(step)) && Number(step) > 0 ? String(step) : "1";
      if (control && Date.now() >= (audioToneHoldUntil[name] || 0) && !isAudioSliderActive(name)) {
        state.audio[name] = normalizeAudioControlValue(control, state.audio[name]);
      }
    }

    if (!isAudioSliderActive(name)) slider.value = a[name];
    setRangeVisual(slider);
    if (value) value.textContent = name === "volume" ? Math.round(Number(a[name] || 0)) : formatControlValue(a[name]);
  });
}

function upsertMediaPlayerEntity(entity) {
  if (!entity?.entityId) return;
  const list = state.integrations.homeAssistant.mediaPlayerEntities || [];
  const existing = list.find((item) => item.entityId === entity.entityId);
  if (existing) Object.assign(existing, entity);
  else list.push(entity);
  state.integrations.homeAssistant.mediaPlayerEntities = list;
}

function renderMediaPlayerList() {
  if (!elements.mediaPlayerList) return;
  const players = getMediaPlayers();
  const selectedId = state.integrations.homeAssistant.selectedMediaPlayerId || "";
  if (elements.mediaPlayerCount) elements.mediaPlayerCount.textContent = String(players.length);
  if (elements.selectedMediaDeviceText) {
    const selected = players.find((entity) => entity.entityId === selectedId);
    elements.selectedMediaDeviceText.textContent = selected ? `Selected: ${selected.name}` : "No media player selected.";
  }
  elements.mediaPlayerList.innerHTML = players.length
    ? players.map((entity) => {
      const meta = entity.mediaTitle || entity.source || "No active media";
      return `
        <button class="media-player-row ${entity.entityId === selectedId ? "active" : ""}" data-media-player-id="${escapeHtml(entity.entityId)}">
          <div class="media-player-main">
            <strong>${escapeHtml(entity.name || entity.entityId)}</strong>
            <span class="media-player-entity">${escapeHtml(entity.entityId)}</span>
            <span class="media-player-meta">${escapeHtml(meta)}</span>
          </div>
          <em class="media-player-state">${escapeHtml(entity.state || "idle")}</em>
        </button>
      `;
    }).join("")
    : `<div class="empty-state compact">No media devices loaded yet.</div>`;
}

function selectMediaPlayer(entityId) {
  const player = getMediaPlayers().find((entity) => entity.entityId === entityId);
  if (!player) return;
  state.integrations.homeAssistant.selectedMediaPlayerId = entityId;
  applyMediaEntityState(player);
  saveConfig({ toast: true });
  renderMediaPlayerList();
  renderAudio();
  pollHomeAssistantMediaPlayer({ force: true });
  loadAudioControlsForSelected({ quiet: true });
}

async function fetchMediaPlayersViaLocalBackend() {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const response = await fetch("/api/ha/media_players", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token }),
  });
  if (!response.ok) {
    let message = `Local backend returned ${response.status}`;
    try { const payload = await response.json(); message = payload.error || message; } catch (_) {}
    throw new Error(message);
  }
  const payload = await response.json();
  return payload.players || [];
}

async function fetchMediaStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const payload = await fetchJsonWithTimeout("/api/ha/media/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  }, 9000);
  return payload.players || [];
}

async function fetchAudioControlsViaLocalBackend(mediaPlayerId, mediaPlayerName) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token || !mediaPlayerId) throw new Error("Missing Home Assistant audio config");
  const payload = await fetchJsonWithTimeout("/api/ha/audio/controls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, mediaPlayerId, mediaPlayerName }),
  }, 12000);
  return payload.controls || {};
}

async function fetchAudioControlStatesViaLocalBackend() {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  const controls = getAudioToneControls();
  const hasControls = ["gain", "bass", "treble"].some((kind) => controls[kind]?.entityId);
  if (!baseUrl || !ha.token || !hasControls) return {};
  const payload = await fetchJsonWithTimeout("/api/ha/audio/control_states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, controls }),
  }, 9000);
  return payload.controls || {};
}

async function loadAudioControlsForSelected(options = {}) {
  const entityId = state.integrations.homeAssistant.selectedMediaPlayerId || state.audio.entityId;
  if (!entityId || haAudioControlSyncInFlight) return {};
  const selected = getSelectedMediaPlayer();
  haAudioControlSyncInFlight = true;
  try {
    const controls = await fetchAudioControlsViaLocalBackend(entityId, selected?.name || state.audio.entityName || "");
    applyAudioControls(controls, { force: true });
    saveConfig();
    renderAudio();
    if (!options.quiet) {
      const found = ["gain", "bass", "treble"].filter((kind) => controls[kind]?.entityId);
      showToast(found.length ? `Linked audio controls: ${found.join(", ")}` : "No HA tone controls found");
    }
    return controls;
  } catch (error) {
    if (!options.quiet) showToast("Could not load audio controls");
    addHaLog("warn", "Audio tone control lookup failed", error.message || String(error));
    return {};
  } finally {
    haAudioControlSyncInFlight = false;
  }
}

async function sendAudioToneAction(kind, value) {
  const control = getAudioToneControl(kind);
  if (!control?.entityId) {
    saveConfig();
    return null;
  }
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant config");
  audioToneHoldUntil[kind] = Date.now() + AUDIO_TONE_SETTLE_MS;
  try {
    const payload = await fetchJsonWithTimeout("/api/ha/audio/control/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: baseUrl, token: ha.token, entityId: control.entityId, value }),
    }, 10000);
    if (payload.control) {
      payload.control.kind = kind;
      getAudioToneControls()[kind] = payload.control;
      if (Date.now() >= audioToneHoldUntil[kind]) state.audio[kind] = normalizeAudioControlValue(payload.control, state.audio[kind]);
    }
    scheduleAudioSync();
    return payload.control || null;
  } catch (error) {
    addHaLog("error", `${titleCase(kind)} control failed`, error.message || String(error));
    showToast(`${titleCase(kind)} command failed`);
    return null;
  }
}

async function loadMediaPlayersFromHomeAssistant(options = {}) {
  if (!elements.audioHaView || !elements.audioHaView.hidden) readHaFieldsFromScreen("audio");
  const ha = state.integrations.homeAssistant;
  if (!getHaBaseUrl() || !ha.token) {
    showToast("Add HA URL and token first");
    return [];
  }
  if (elements.loadMediaPlayers) elements.loadMediaPlayers.disabled = true;
  if (elements.loadAudioMediaPlayers) elements.loadAudioMediaPlayers.disabled = true;
  try {
    const players = await fetchMediaPlayersViaLocalBackend();
    state.integrations.homeAssistant.mediaPlayerEntities = players;
    const selectedId = state.integrations.homeAssistant.selectedMediaPlayerId;
    const selected = players.find((entity) => entity.entityId === selectedId) || players[0];
    if (selected && !selectedId) state.integrations.homeAssistant.selectedMediaPlayerId = selected.entityId;
    if (selected) applyMediaEntityState(selected);
    if (selected) await loadAudioControlsForSelected({ quiet: true });
    saveConfig({ toast: !options.quiet });
    renderMediaPlayerList();
    renderAudio();
    showToast(`Loaded ${players.length} media devices`);
    return players;
  } catch (error) {
    showToast("Could not load media devices");
    addHaLog("error", "Media player load failed", error.message || String(error));
    return [];
  } finally {
    if (elements.loadMediaPlayers) elements.loadMediaPlayers.disabled = false;
    if (elements.loadAudioMediaPlayers) elements.loadAudioMediaPlayers.disabled = false;
  }
}

function scheduleAudioSync() {
  HA_AUDIO_SYNC_AFTER_COMMAND_DELAYS.forEach((delay) => {
    window.setTimeout(() => pollHomeAssistantMediaPlayer({ force: true }), delay);
  });
}

async function pollHomeAssistantMediaPlayer(options = {}) {
  const entityId = state.integrations.homeAssistant.selectedMediaPlayerId;
  if (!entityId) return;
  if (!options.force && state.currentPage !== "audio") return;
  if (!options.force && document.visibilityState === "hidden") return;
  if (!options.force && Date.now() - lastAudioUserInteractionAt < 650) return;
  if (haAudioSyncInFlight) return;

  haAudioSyncInFlight = true;
  try {
    const [players, controls] = await Promise.all([
      fetchMediaStatesViaLocalBackend([entityId]),
      fetchAudioControlStatesViaLocalBackend().catch((error) => {
        addHaLog("warn", "Audio tone sync skipped", error.message || String(error));
        return null;
      }),
    ]);
    players.forEach(upsertMediaPlayerEntity);
    if (players[0]) applyMediaEntityState(players[0]);
    if (controls) applyAudioControls(controls);
    renderAudio();
    renderMediaPlayerList();
    if (haAudioSyncLastError) haAudioSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haAudioSyncLastError) {
      haAudioSyncLastError = message;
      addHaLog("warn", "Live audio sync paused", message);
    }
  } finally {
    haAudioSyncInFlight = false;
  }
}

async function callMediaActionViaLocalBackend(action, value = null) {
  const ha = state.integrations.homeAssistant;
  const entityId = ha.selectedMediaPlayerId;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token || !entityId) throw new Error("Missing Home Assistant media player config");
  const body = { url: baseUrl, token: ha.token, entityId, action };
  if (value !== null && value !== undefined) body.value = value;
  const payload = await fetchJsonWithTimeout("/api/ha/media/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, 12000);
  return payload.state;
}

async function sendAudioAction(action, value = null) {
  if (!state.integrations.homeAssistant.selectedMediaPlayerId) {
    showToast("Select an audio device first");
    return null;
  }
  lastAudioUserInteractionAt = Date.now();
  try {
    const entityState = await callMediaActionViaLocalBackend(action, value);
    if (entityState?.entityId) {
      if (action === "volume" && value !== null && value !== undefined) {
        audioVolumeHoldUntil = Date.now() + AUDIO_VOLUME_SETTLE_MS;
        entityState.volumeLevel = clamp(Number(value), 0, 100) / 100;
      }
      if (action === "source" && value) {
        entityState.source = String(value);
      }
      upsertMediaPlayerEntity(entityState);
      applyMediaEntityState(entityState);
      saveConfig();
      renderAudio();
      renderMediaPlayerList();
    }
    scheduleAudioSync();
    return entityState;
  } catch (error) {
    addHaLog("error", `Media ${action} failed`, error.message || String(error));
    showToast("Media command failed");
    return null;
  }
}

async function changeTrack(delta) {
  if (isAudioTrackLocked()) {
    showToast("Changing track…");
    return;
  }
  const before = getCurrentAudioSignature();
  audioTrackCommandLock = { before, until: Date.now() + AUDIO_TRACK_ACK_TIMEOUT_MS };
  renderAudio();
  const result = await sendAudioAction(delta > 0 ? "next" : "previous");
  if (!result) audioTrackCommandLock = null;
  maybeResolveAudioTrackLock(result);
  renderAudio();
}
function togglePlayback() {
  if (Date.now() < audioPlaybackLockedUntil) return;
  audioPlaybackLockedUntil = Date.now() + AUDIO_PLAYBACK_LOCK_MS;
  renderAudio();
  sendAudioAction("play_pause").finally(() => {
    window.setTimeout(() => { renderAudio(); }, AUDIO_PLAYBACK_LOCK_MS);
  });
}

function renderRoomTabs() {
  elements.roomTabs.innerHTML = "";
  getRoomKeys().forEach((key) => {
    const room = state.blinds.rooms[key];
    const tab = document.createElement("button");
    tab.className = "room-tab";
    tab.dataset.room = key;
    tab.textContent = room.label;
    tab.classList.toggle("active", key === state.blinds.room);
    elements.roomTabs.appendChild(tab);
  });
}

function renderBlinds() {
  const room = getActiveRoom();
  elements.blindRoomTitle.textContent = room.label;
  elements.blindCards.innerHTML = "";
  renderRoomTabs();

  const columns = clamp(room.blinds.length, 1, 6);
  elements.blindCards.style.setProperty("--blind-columns", columns);

  room.blinds.forEach((blind) => {
    const card = document.createElement("div");
    card.className = "blind-card";
    card.dataset.blindCard = blind.id;
    card.dataset.roomKey = state.blinds.room;
    card.style.setProperty("--blind-open", `${blind.position}%`);
    card.innerHTML = `
      <div class="blind-top">
        <div>
          <div class="blind-name">${blind.name}</div>
        </div>
        <div class="blind-percent">${blind.position}%</div>
      </div>
      <button class="blind-action primary" data-blind-id="${blind.id}" data-action="open">Open</button>
      <div class="shade-stage" data-blind-stage="${blind.id}" role="slider" aria-label="${blind.name} position" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${blind.position}" tabindex="0">
        <div class="blind-window" aria-hidden="true"></div>
      </div>
      <button class="blind-action close-blind" data-blind-id="${blind.id}" data-action="close">Close</button>
    `;
    elements.blindCards.appendChild(card);
  });
}

function setBlindPosition(blindId, position) {
  lastBlindUserInteractionAt = Date.now();
  const room = getActiveRoom();
  const blind = room.blinds.find((item) => item.id === blindId);
  if (!blind) return;
  blind.position = clamp(Number(position), 0, 100);
  const card = document.querySelector(`[data-blind-card="${blindId}"]`);
  if (!card) return;
  card.style.setProperty("--blind-open", `${blind.position}%`);
  card.querySelector(".blind-percent").textContent = `${blind.position}%`;
  card.querySelector("[data-blind-stage]").setAttribute("aria-valuenow", String(blind.position));
  saveConfig();
}

async function applyBlindAction(action, blindId = null) {
  const room = getActiveRoom();
  const normalizedAction = action.includes("open") ? "open" : action.includes("close") ? "close" : action;
  const updateBlind = (blind) => {
    if (normalizedAction === "open") blind.position = 100;
    if (normalizedAction === "close") blind.position = 0;
  };

  const targets = blindId
    ? room.blinds.filter((item) => item.id === blindId)
    : room.blinds.slice();
  if (!targets.length) return;

  targets.forEach(updateBlind);
  saveConfig();
  renderBlinds();

  const linkedTargets = targets.filter((blind) => blind.haEntityId);
  if (!linkedTargets.length) return;
  await Promise.all(linkedTargets.map((blind) => sendBlindToHomeAssistant(blind, normalizedAction)));
  scheduleHaBlindSync();
}

function createBlind(roomKey, index) {
  return {
    id: `${roomKey}-${Date.now().toString(36)}-${index}`,
    name: `Blind ${index}`,
    position: 65,
    haEntityId: "",
    haName: "",
  };
}

function setRoomBlindCount(roomKey, count) {
  const room = state.blinds.rooms[roomKey];
  if (!room) return;
  const target = clamp(Number(count), 1, 6);
  while (room.blinds.length < target) room.blinds.push(createBlind(roomKey, room.blinds.length + 1));
  while (room.blinds.length > target) room.blinds.pop();
  room.blinds.forEach((blind, index) => {
    if (!blind.name) blind.name = `Blind ${index + 1}`;
  });
  saveConfig();
  renderRoomConfigList();
  renderBlinds();
}

function addRoom() {
  const roomNumber = getRoomKeys().length + 1;
  const label = `New Room ${roomNumber}`;
  const key = slugify(label);
  state.blinds.rooms[key] = {
    label,
    blinds: [createBlind(key, 1)],
  };
  state.blinds.room = key;
  saveConfig({ toast: true });
  renderRoomConfigList();
  renderBlinds();
}

function deleteRoom(roomKey) {
  const keys = getRoomKeys();
  if (keys.length <= 1) {
    showToast("At least one room is required");
    return;
  }
  delete state.blinds.rooms[roomKey];
  if (state.blinds.room === roomKey) state.blinds.room = getRoomKeys()[0];
  saveConfig({ toast: true });
  renderRoomConfigList();
  renderBlinds();
}

function renameRoom(roomKey, label) {
  const room = state.blinds.rooms[roomKey];
  if (!room) return;
  room.label = label.trim() || "Room";
  saveConfig();
  renderBlinds();
}

function renameBlind(roomKey, blindId, label) {
  const room = state.blinds.rooms[roomKey];
  const blind = room?.blinds.find((item) => item.id === blindId);
  if (!blind) return;
  blind.name = label.trim() || "Blind";
  saveConfig();
  renderBlinds();
}

function renderRoomConfigList() {
  if (!elements.roomConfigList) return;
  elements.roomConfigList.innerHTML = "";
  getRoomKeys().forEach((key) => {
    const room = state.blinds.rooms[key];
    const card = document.createElement("div");
    card.className = "room-config-card";
    card.dataset.roomConfig = key;
    const countOptions = Array.from({ length: 6 }, (_, idx) => idx + 1)
      .map((count) => `<option value="${count}" ${room.blinds.length === count ? "selected" : ""}>${count}</option>`)
      .join("");
    const blindInputs = room.blinds.map((blind, index) => `
      <label class="mini-field">Blind ${index + 1}
        <input type="text" value="${blind.name}" data-blind-name-input data-room-key="${key}" data-blind-id="${blind.id}" />
      </label>
    `).join("");
    card.innerHTML = `
      <div class="room-config-main">
        <label class="form-field compact-field">Room Name
          <input type="text" value="${room.label}" data-room-name-input data-room-key="${key}" />
        </label>
        <label class="form-field compact-field">Blinds
          <select data-room-count-select data-room-key="${key}">${countOptions}</select>
        </label>
        <button class="danger-button" data-delete-room="${key}">Delete</button>
      </div>
      <div class="blind-name-grid">${blindInputs}</div>
    `;
    elements.roomConfigList.appendChild(card);
  });
}

function getHaBaseUrl() {
  return String(state.integrations.homeAssistant.url || "").replace(/\/+$/, "");
}

function normalizeHaPosition(entity, fallback = 50) {
  const raw = entity?.currentPosition;
  if (raw !== undefined && raw !== null && raw !== "") return clamp(Math.round(Number(raw)), 0, 100);
  const coverState = String(entity?.state || "").toLowerCase();
  if (coverState === "open") return 100;
  if (coverState === "closed") return 0;
  return clamp(Number(fallback), 0, 100);
}

function findCoverEntity(entityId) {
  return (state.integrations.homeAssistant.coverEntities || []).find((entity) => entity.entityId === entityId);
}

function applyEntityStateToBlind(blind, entity) {
  if (!blind || !entity) return;
  blind.position = normalizeHaPosition(entity, blind.position);
  if (entity.name) {
    blind.haName = entity.name;
    blind.name = entity.name;
  }
}

function syncLinkedBlindsFromCovers(covers = state.integrations.homeAssistant.coverEntities || [], options = {}) {
  if (!covers.length) return false;
  let changed = false;
  Object.values(state.blinds.rooms || {}).forEach((room) => {
    (room.blinds || []).forEach((blind) => {
      if (!blind.haEntityId) return;
      const entity = covers.find((item) => item.entityId === blind.haEntityId);
      if (!entity) return;
      const before = blind.position;
      applyEntityStateToBlind(blind, entity);
      changed = changed || before !== blind.position;
    });
  });
  if (changed && !options.skipSave) saveConfig();
  return changed;
}

function upsertCoverEntity(entity) {
  if (!entity?.entityId) return;
  const list = state.integrations.homeAssistant.coverEntities || [];
  const existing = list.find((item) => item.entityId === entity.entityId);
  if (existing) Object.assign(existing, entity);
  else list.push(entity);
  state.integrations.homeAssistant.coverEntities = list;
}

function getLinkedCoverEntityIds() {
  const ids = [];
  Object.values(state.blinds.rooms || {}).forEach((room) => {
    (room.blinds || []).forEach((blind) => {
      if (blind.haEntityId && !ids.includes(blind.haEntityId)) ids.push(blind.haEntityId);
    });
  });
  return ids;
}

async function fetchLinkedCoverStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const response = await fetch("/api/ha/cover/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  });
  if (!response.ok) {
    let message = `Local backend returned ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (_) {}
    throw new Error(message);
  }
  const payload = await response.json();
  return payload.covers || [];
}

function scheduleHaBlindSync() {
  HA_SYNC_AFTER_COMMAND_DELAYS.forEach((delay) => {
    window.setTimeout(() => pollHomeAssistantLinkedCovers({ force: true }), delay);
  });
}

async function pollHomeAssistantLinkedCovers(options = {}) {
  const linkedEntityIds = getLinkedCoverEntityIds();
  if (!linkedEntityIds.length) return;
  if (!options.force && state.currentPage !== "blinds") return;
  if (!options.force && document.visibilityState === "hidden") return;
  if (!options.force && Date.now() - lastBlindUserInteractionAt < 1200) return;
  if (haSyncInFlight) return;

  haSyncInFlight = true;
  try {
    const covers = await fetchLinkedCoverStatesViaLocalBackend(linkedEntityIds);
    covers.forEach(upsertCoverEntity);
    const changed = syncLinkedBlindsFromCovers(covers);
    if (changed) renderBlinds();
    if (haSyncLastError) {
      addHaLog("info", "Live blind sync recovered", `${covers.length} linked cover states refreshed`);
      haSyncLastError = "";
    }
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haSyncLastError) {
      haSyncLastError = message;
      addHaLog("warn", "Live blind sync paused", message);
    }
  } finally {
    haSyncInFlight = false;
  }
}

async function callCoverActionViaLocalBackend(entityId, action, position = null) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const body = { url: baseUrl, token: ha.token, entityId, action };
  if (position !== null && position !== undefined) body.position = position;
  const response = await fetch("/api/ha/cover/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `Local backend returned ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (_) {}
    throw new Error(message);
  }
  const payload = await response.json();
  return payload.state;
}

async function sendBlindToHomeAssistant(blind, action, position = null) {
  if (!blind?.haEntityId) return null;
  const optimistic = action === "open" ? 100 : action === "close" ? 0 : position;
  if (optimistic !== null && optimistic !== undefined) blind.position = clamp(Number(optimistic), 0, 100);
  renderBlinds();

  try {
    const entityState = await callCoverActionViaLocalBackend(blind.haEntityId, action, position);
    if (entityState?.entityId) {
      const existing = findCoverEntity(entityState.entityId);
      if (existing) Object.assign(existing, entityState);
      else state.integrations.homeAssistant.coverEntities.push(entityState);
      applyEntityStateToBlind(blind, entityState);
      saveConfig();
      renderBlinds();
      renderCoverPreview();
    }
    scheduleHaBlindSync();
    return entityState;
  } catch (error) {
    addHaLog("error", `Cover ${action} failed`, `${blind.haEntityId}: ${error.message || error}`);
    showToast("Home Assistant cover command failed");
    return null;
  }
}

async function fetchCoverEntitiesViaLocalBackend(baseUrl, token) {
  const response = await fetch("/api/ha/covers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token }),
  });
  if (!response.ok) {
    let message = `Local backend returned ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (_) {}
    throw new Error(message);
  }
  const payload = await response.json();
  return payload.covers || [];
}

async function fetchCoverEntitiesDirect(baseUrl, token) {
  const response = await fetch(`${baseUrl}/api/states`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) throw new Error(`Home Assistant returned ${response.status}`);
  const states = await response.json();
  return states
    .filter((item) => String(item.entity_id || "").startsWith("cover."))
    .map((item) => ({
      entityId: item.entity_id,
      name: item.attributes?.friendly_name || item.entity_id,
      state: item.state || "unknown",
      currentPosition: item.attributes?.current_position,
      supportedFeatures: item.attributes?.supported_features,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

async function loadCoverEntitiesFromHomeAssistant(options = {}) {
  readHaFieldsFromScreen("blinds");
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  renderCoverPreview();

  if (!baseUrl || !ha.token) {
    setHaStatus("Missing Home Assistant URL or token.", "warn");
    addHaLog("warn", "Load skipped", "Missing URL or token");
    showToast("Add HA URL and token first");
    return ha.coverEntities;
  }

  setHaStatus("Loading cover entries…", "info");
  addHaLog("info", "Starting cover load", `URL: ${baseUrl}, token: ${maskToken(ha.token)}`);
  if (elements.loadCoverEntities) elements.loadCoverEntities.disabled = true;
  if (elements.testHaConnection) elements.testHaConnection.disabled = true;

  let covers = [];
  let backendError = null;
  let directError = null;

  try {
    addHaLog("info", "Trying local backend proxy", "/api/ha/covers");
    covers = await fetchCoverEntitiesViaLocalBackend(baseUrl, ha.token);
    addHaLog("info", "Local backend proxy worked", `${covers.length} cover entries returned`);
  } catch (error) {
    backendError = error;
    addHaLog("warn", "Local backend proxy unavailable or failed", error.message);
    try {
      addHaLog("info", "Trying direct browser call", `${baseUrl}/api/states`);
      covers = await fetchCoverEntitiesDirect(baseUrl, ha.token);
      addHaLog("info", "Direct browser call worked", `${covers.length} cover entries returned`);
    } catch (error2) {
      directError = error2;
      addHaLog("error", "Direct browser call failed", error2.message || String(error2));
    }
  } finally {
    if (elements.loadCoverEntities) elements.loadCoverEntities.disabled = false;
    if (elements.testHaConnection) elements.testHaConnection.disabled = false;
  }

  if (covers.length) {
    state.integrations.homeAssistant.coverEntities = covers;
    syncLinkedBlindsFromCovers();
    saveConfig({ toast: !options.quiet });
    renderBlinds();
    pollHomeAssistantLinkedCovers({ force: true });
    setHaStatus(`Connected. Loaded ${covers.length} Home Assistant cover entries.`, "ok");
    showToast(`Loaded ${covers.length} cover entries`);
  } else {
    const likelyCors = directError && /failed to fetch|networkerror|cors/i.test(String(directError.message || directError));
    const message = likelyCors
      ? "Browser call was blocked or failed. Run with server.py so the local backend can proxy Home Assistant."
      : "No cover entries loaded. See the connection log below.";
    setHaStatus(message, "error");
    addHaLog("error", "No covers loaded", `Backend: ${backendError?.message || "n/a"}; Direct: ${directError?.message || "n/a"}`);
    showToast("Could not load HA covers");
  }

  renderCoverPreview();
  return state.integrations.homeAssistant.coverEntities;
}

function renderCoverPreview() {
  if (!elements.haCoverList) return;
  const covers = state.integrations.homeAssistant.coverEntities || [];
  if (elements.haEntityCount) elements.haEntityCount.textContent = String(covers.length);
  elements.haCoverList.innerHTML = covers.length
    ? covers.map((entity) => {
        const stateText = entity.state ? String(entity.state) : "unknown";
        const pct = normalizeHaPosition(entity);
        return `<div class="entity-row"><strong>${entity.name}</strong><span>${entity.entityId}</span><em>${pct}% · ${stateText}</em></div>`;
      }).join("")
    : `<div class="empty-state compact">No covers loaded.</div>`;
}

async function openEntityPicker(roomKey, blindId) {
  const room = state.blinds.rooms[roomKey];
  const blind = room?.blinds.find((item) => item.id === blindId);
  if (!blind) return;
  state.entityPicker = { roomKey, blindId };
  elements.entityPickerTitle.textContent = `Assign ${blind.name}`;
  elements.entityPickerOverlay.classList.add("open");
  elements.entityPickerOverlay.setAttribute("aria-hidden", "false");
  renderEntityPickerList(true);
  await loadCoverEntitiesFromHomeAssistant();
  renderEntityPickerList(false);
}

function closeEntityPicker() {
  elements.entityPickerOverlay.classList.remove("open");
  elements.entityPickerOverlay.setAttribute("aria-hidden", "true");
  state.entityPicker = { roomKey: null, blindId: null };
}

function renderEntityPickerList(loading = false) {
  const covers = state.integrations.homeAssistant.coverEntities || [];
  if (loading) {
    elements.entityPickerList.innerHTML = `<div class="empty-state">Loading cover entries…</div>`;
    return;
  }
  if (!covers.length) {
    elements.entityPickerList.innerHTML = `<div class="empty-state">No cover entries available. Add Home Assistant config first.</div>`;
    return;
  }
  elements.entityPickerList.innerHTML = `
    <button class="entity-pick clear" data-clear-entity>Unlink this blind</button>
    ${covers.map((entity) => `<button class="entity-pick" data-entity-id="${entity.entityId}" data-entity-name="${entity.name}"><strong>${entity.name}</strong><span>${entity.entityId}</span></button>`).join("")}
  `;
}

function assignEntityToBlind(entityId, name) {
  const { roomKey, blindId } = state.entityPicker;
  const room = state.blinds.rooms[roomKey];
  const blind = room?.blinds.find((item) => item.id === blindId);
  if (!blind) return;
  blind.haEntityId = entityId || "";
  blind.haName = name || "";
  if (name) blind.name = name;
  const entity = entityId ? findCoverEntity(entityId) : null;
  if (entity) applyEntityStateToBlind(blind, entity);
  saveConfig({ toast: true });
  renderBlinds();
  pollHomeAssistantLinkedCovers({ force: true });
  closeEntityPicker();
}

function isInteractiveTarget(target) {
  return Boolean(target.closest("button, input, textarea, select, .thermo-dial, .settings-sheet, .settings-overlay, [data-blind-stage]"));
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

function bindBlindInteractions() {
  let activeShadeId = null;
  let longPressTimer = null;
  let press = null;

  const cancelLongPress = () => {
    clearTimeout(longPressTimer);
    longPressTimer = null;
    press = null;
  };

  const updateBlindFromPointer = (event) => {
    const stage = document.querySelector(`[data-blind-stage="${activeShadeId}"]`) || event.target.closest("[data-blind-stage]");
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    const pct = clamp(Math.round(((rect.bottom - event.clientY) / rect.height) * 100), 0, 100);
    setBlindPosition(stage.dataset.blindStage, pct);
  };

  elements.blindCards.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    if (event.target.closest("button")) return;
    const card = event.target.closest("[data-blind-card]");
    const stage = event.target.closest("[data-blind-stage]");
    if (!card) return;

    press = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, roomKey: card.dataset.roomKey, blindId: card.dataset.blindCard };
    longPressTimer = setTimeout(() => {
      const pending = press;
      cancelLongPress();
      activeShadeId = null;
      openEntityPicker(pending.roomKey, pending.blindId);
    }, 720);

    if (stage) {
      event.preventDefault(); event.stopPropagation();
      activeShadeId = stage.dataset.blindStage;
      stage.setPointerCapture(event.pointerId);
      updateBlindFromPointer(event);
    }
  });

  elements.blindCards.addEventListener("pointermove", (event) => {
    if (press && press.pointerId === event.pointerId) {
      const dx = Math.abs(event.clientX - press.x);
      const dy = Math.abs(event.clientY - press.y);
      if (dx > 10 || dy > 10) cancelLongPress();
    }
    if (activeShadeId) { event.preventDefault(); updateBlindFromPointer(event); }
  });

  const finishShadeDrag = (event) => {
    cancelLongPress();
    if (!activeShadeId) return;
    const stage = document.querySelector(`[data-blind-stage="${activeShadeId}"]`);
    const blindId = activeShadeId;
    try { stage?.releasePointerCapture(event.pointerId); } catch (_) {}
    activeShadeId = null;

    const room = getActiveRoom();
    const blind = room.blinds.find((item) => item.id === blindId);
    if (blind?.haEntityId) sendBlindToHomeAssistant(blind, "position", blind.position);
  };
  elements.blindCards.addEventListener("pointerup", finishShadeDrag);
  elements.blindCards.addEventListener("pointercancel", finishShadeDrag);
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
  document.getElementById("addRoomButton").addEventListener("click", addRoom);
  document.getElementById("openBlindHaConfig").addEventListener("click", showBlindHaView);
  document.getElementById("backToBlindSetup").addEventListener("click", showBlindSetupView);
  document.getElementById("saveHaConfig").addEventListener("click", () => saveHaFields("blinds"));
  document.getElementById("loadCoverEntities").addEventListener("click", () => loadCoverEntitiesFromHomeAssistant());
  elements.testHaConnection?.addEventListener("click", () => loadCoverEntitiesFromHomeAssistant({ quiet: true }));
  elements.clearHaLog?.addEventListener("click", clearHaLog);
  document.getElementById("openAudioHaConfig").addEventListener("click", showAudioHaView);
  elements.backToAudioSetup?.addEventListener("click", showAudioSetupView);
  document.getElementById("saveAudioHaConfig").addEventListener("click", () => { saveHaFields("audio"); showAudioSetupView(); });
  elements.loadMediaPlayers?.addEventListener("click", () => loadMediaPlayersFromHomeAssistant());
  elements.loadAudioMediaPlayers?.addEventListener("click", () => loadMediaPlayersFromHomeAssistant());
  elements.mediaPlayerList?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-media-player-id]");
    if (row) selectMediaPlayer(row.dataset.mediaPlayerId);
  });
  elements.audioSourceSelect?.addEventListener("change", (event) => {
    const source = event.target.value;
    state.audio.source = source;
    lastAudioUserInteractionAt = Date.now();
    renderAudio();
    sendAudioAction("source", source);
  });

  elements.roomConfigList.addEventListener("input", (event) => {
    const roomNameInput = event.target.closest("[data-room-name-input]");
    const blindNameInput = event.target.closest("[data-blind-name-input]");
    if (roomNameInput) renameRoom(roomNameInput.dataset.roomKey, roomNameInput.value);
    if (blindNameInput) renameBlind(blindNameInput.dataset.roomKey, blindNameInput.dataset.blindId, blindNameInput.value);
  });
  elements.roomConfigList.addEventListener("change", (event) => {
    const countSelect = event.target.closest("[data-room-count-select]");
    if (countSelect) setRoomBlindCount(countSelect.dataset.roomKey, countSelect.value);
  });
  elements.roomConfigList.addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-delete-room]");
    if (deleteButton) deleteRoom(deleteButton.dataset.deleteRoom);
  });

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
    const slider = document.getElementById(`${name}Slider`);
    if (!slider) return;
    const scheduleSliderCommand = (delay = 420) => {
      if (name === "volume" && state.integrations.homeAssistant.selectedMediaPlayerId) {
        audioVolumeHoldUntil = Date.now() + AUDIO_VOLUME_SETTLE_MS;
        clearTimeout(audioVolumeDebounce);
        audioVolumeDebounce = setTimeout(() => sendAudioAction("volume", state.audio.volume), delay);
      }
      if (["gain", "bass", "treble"].includes(name)) {
        audioToneHoldUntil[name] = Date.now() + AUDIO_TONE_SETTLE_MS;
        const control = getAudioToneControl(name);
        if (control?.entityId) {
          clearTimeout(audioToneDebounces[name]);
          audioToneDebounces[name] = setTimeout(() => sendAudioToneAction(name, state.audio[name]), delay);
        } else {
          saveConfig();
        }
      }
    };
    slider.addEventListener("pointerdown", () => markAudioSliderActive(name, 60 * 1000));
    slider.addEventListener("pointerup", () => { releaseAudioSlider(name); scheduleSliderCommand(0); });
    slider.addEventListener("pointercancel", () => releaseAudioSlider(name));
    slider.addEventListener("blur", () => releaseAudioSlider(name));
    slider.addEventListener("input", (event) => {
      state.audio[name] = Number(event.target.value);
      lastAudioUserInteractionAt = Date.now();
      markAudioSliderActive(name);
      setRangeVisual(event.target);
      renderAudio();
      scheduleSliderCommand(420);
    });
    slider.addEventListener("change", () => {
      releaseAudioSlider(name);
      scheduleSliderCommand(0);
    });
  });

  elements.roomTabs.addEventListener("click", (event) => {
    const tab = event.target.closest(".room-tab");
    if (!tab) return;
    state.blinds.room = tab.dataset.room;
    saveConfig();
    renderBlinds();
  });
  document.querySelectorAll("[data-blind-action]").forEach((button) => button.addEventListener("click", () => applyBlindAction(button.dataset.blindAction)));
  elements.blindCards.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-blind-id]");
    if (!button) return;
    applyBlindAction(button.dataset.action, button.dataset.blindId);
  });

  elements.entityPickerClose.addEventListener("click", closeEntityPicker);
  document.querySelectorAll("[data-close-entity-picker]").forEach((el) => el.addEventListener("click", closeEntityPicker));
  elements.entityPickerList.addEventListener("click", (event) => {
    const clear = event.target.closest("[data-clear-entity]");
    if (clear) return assignEntityToBlind("", "");
    const button = event.target.closest("[data-entity-id]");
    if (!button) return;
    assignEntityToBlind(button.dataset.entityId, button.dataset.entityName);
  });

  bindSwipeNavigation();
  bindThermostatDial();
  bindBlindInteractions();
  window.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") goRelative(1);
    if (event.key === "ArrowLeft") goRelative(-1);
    if (event.key === "+" || event.key === "=") adjustSetpoint(1);
    if (event.key === "-" || event.key === "_") adjustSetpoint(-1);
    if (event.key === "Escape") { closeSettings(); closeEntityPicker(); }
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
  if (state.integrations.homeAssistant.selectedMediaPlayerId) return;
  if (!state.audio.playing) return;
  state.audio.progress += 1;
  if (state.audio.progress > 100) changeTrack(1);
  renderAudio();
}

function init() {
  loadSavedConfig();
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
  setInterval(() => pollHomeAssistantLinkedCovers(), HA_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantMediaPlayer(), HA_AUDIO_SYNC_INTERVAL_MS);
}

init();
