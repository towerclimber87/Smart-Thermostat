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
    humidity: 45,
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
          { id: "lr-3", name: "Center Right", position: 65 },
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
  pageTitle: document.getElementById("pageTitle"),
  clock: document.getElementById("clock"),
  toast: document.getElementById("toast"),
  currentTemp: document.getElementById("currentTemp"),
  targetTemp: document.getElementById("targetTemp"),
  humidityValue: document.getElementById("humidityValue"),
  thermoDial: document.getElementById("thermoDial"),
  modeBadge: document.getElementById("modeBadge"),
  runtimeState: document.getElementById("runtimeState"),
  awayToggle: document.getElementById("awayToggle"),
  awayHint: document.getElementById("awayHint"),
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
  elements.pageTitle.textContent = titleCase(pageName);

  document.querySelectorAll(".nav-pill").forEach((button) => {
    button.classList.toggle("active", button.dataset.goto === pageName);
  });

  document.querySelectorAll(".page-dots span").forEach((dot) => {
    dot.classList.toggle("active", dot.dataset.dot === pageName);
  });
}

function goRelative(direction) {
  const currentIndex = state.pages.indexOf(state.currentPage);
  const nextIndex = clamp(currentIndex + direction, 0, state.pages.length - 1);
  gotoPage(state.pages[nextIndex]);
}

function renderThermostat() {
  const t = state.thermostat;
  elements.currentTemp.textContent = Math.round(t.currentTemp);
  elements.targetTemp.textContent = Math.round(t.targetTemp);
  elements.humidityValue.textContent = t.humidity;

  const progress = clamp(((t.targetTemp - 50) / 40) * 100, 0, 100);
  elements.thermoDial.style.setProperty("--progress", `${progress}%`);
  elements.thermoDial.classList.toggle("heat", t.mode === "heat");

  const isCalling = t.mode === "cool" ? t.currentTemp > t.targetTemp : t.currentTemp < t.targetTemp;
  const action = isCalling ? (t.mode === "cool" ? "Cooling" : "Heating") : "Idle";
  elements.runtimeState.textContent = action;
  elements.modeBadge.textContent = t.away ? `${titleCase(t.mode)} Away` : `${titleCase(t.mode)} Ready`;
  elements.modeBadge.className = `mode-badge ${t.mode}`;

  elements.awayToggle.classList.toggle("active", t.away);
  elements.awayHint.textContent = t.away
    ? `Away mode active. ${titleCase(t.mode)} safety target applied.`
    : "Away mode is off. Tap Away to apply safety setpoints.";

  document.querySelectorAll(".mode-button[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === t.mode);
  });

  document.querySelectorAll(".segment[data-fan]").forEach((button) => {
    button.classList.toggle("active", button.dataset.fan === t.fan);
  });
}

function setMode(mode) {
  state.thermostat.mode = mode;
  if (state.thermostat.away) {
    state.thermostat.targetTemp = mode === "heat" ? 55 : 85;
  }
  renderThermostat();
  showToast(`${titleCase(mode)} mode selected`);
}

function adjustSetpoint(delta) {
  const t = state.thermostat;
  t.away = false;
  t.targetTemp = clamp(t.targetTemp + delta, 50, 90);
  t.lastComfortTarget = t.targetTemp;
  renderThermostat();
}

function toggleAway() {
  const t = state.thermostat;
  t.away = !t.away;
  if (t.away) {
    t.lastComfortTarget = t.targetTemp;
    t.targetTemp = t.mode === "heat" ? 55 : 85;
    showToast("Away safety setpoint applied");
  } else {
    t.targetTemp = t.lastComfortTarget;
    showToast("Comfort setpoint restored");
  }
  renderThermostat();
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
  showToast(`Now playing: ${a.tracks[a.trackIndex].title}`);
}

function togglePlayback() {
  state.audio.playing = !state.audio.playing;
  renderAudio();
  showToast(state.audio.playing ? "Playback started" : "Playback paused");
}

function renderBlinds() {
  const roomKey = state.blinds.room;
  const room = state.blinds.rooms[roomKey];
  elements.blindRoomTitle.textContent = room.label;
  elements.blindCards.innerHTML = "";

  document.querySelectorAll(".room-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.room === roomKey);
  });

  room.blinds.forEach((blind) => {
    const card = document.createElement("div");
    card.className = "blind-card";
    card.innerHTML = `
      <div class="blind-top">
        <div class="blind-name">${blind.name}</div>
        <div class="blind-percent">${blind.position}%</div>
      </div>
      <input type="range" min="0" max="100" value="${blind.position}" data-blind-slider="${blind.id}" aria-label="${blind.name} position" />
      <div class="blind-actions">
        <button class="blind-action" data-blind-id="${blind.id}" data-action="open">Open</button>
        <button class="blind-action" data-blind-id="${blind.id}" data-action="stop">Stop</button>
        <button class="blind-action" data-blind-id="${blind.id}" data-action="close">Close</button>
      </div>
    `;
    elements.blindCards.appendChild(card);
  });
}

function setBlindPosition(blindId, position) {
  const room = state.blinds.rooms[state.blinds.room];
  const blind = room.blinds.find((item) => item.id === blindId);
  if (!blind) return;
  blind.position = clamp(Number(position), 0, 100);
  renderBlinds();
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
    showToast(`${blind.name}: ${titleCase(action)}`);
  } else {
    room.blinds.forEach(updateBlind);
    showToast(`${room.label}: ${action.includes("open") ? "Opened" : action.includes("close") ? "Closed" : "Stopped"}`);
  }
  renderBlinds();
}

function bindEvents() {
  document.querySelectorAll(".nav-pill").forEach((button) => {
    button.addEventListener("click", () => gotoPage(button.dataset.goto));
  });

  document.getElementById("leftZone").addEventListener("click", () => goRelative(-1));
  document.getElementById("rightZone").addEventListener("click", () => goRelative(1));
  document.getElementById("tempDown").addEventListener("click", () => adjustSetpoint(-1));
  document.getElementById("tempUp").addEventListener("click", () => adjustSetpoint(1));
  elements.awayToggle.addEventListener("click", toggleAway);

  document.querySelectorAll(".mode-button[data-mode]").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });

  document.querySelectorAll(".segment[data-fan]").forEach((button) => {
    button.addEventListener("click", () => {
      state.thermostat.fan = button.dataset.fan;
      renderThermostat();
      showToast(`Fan ${button.dataset.fan}`);
    });
  });

  document.getElementById("prevTrack").addEventListener("click", () => changeTrack(-1));
  document.getElementById("nextTrack").addEventListener("click", () => changeTrack(1));
  elements.playPause.addEventListener("click", togglePlayback);

  ["volume", "gain", "bass", "treble"].forEach((name) => {
    const slider = document.getElementById(`${name}Slider`);
    slider.addEventListener("input", (event) => {
      state.audio[name] = Number(event.target.value);
      renderAudio();
    });
  });

  document.querySelectorAll(".room-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.blinds.room = tab.dataset.room;
      renderBlinds();
      showToast(`${state.blinds.rooms[state.blinds.room].label} selected`);
    });
  });

  document.querySelectorAll("[data-blind-action]").forEach((button) => {
    button.addEventListener("click", () => applyBlindAction(button.dataset.blindAction));
  });

  elements.blindCards.addEventListener("input", (event) => {
    const blindId = event.target.dataset.blindSlider;
    if (blindId) setBlindPosition(blindId, event.target.value);
  });

  elements.blindCards.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-blind-id]");
    if (!button) return;
    if (button.dataset.action === "stop") {
      showToast(`${button.closest(".blind-card").querySelector(".blind-name").textContent}: Stopped`);
      return;
    }
    applyBlindAction(button.dataset.action, button.dataset.blindId);
  });

  let touchStartX = null;
  let touchStartY = null;
  elements.app.addEventListener("touchstart", (event) => {
    const touch = event.changedTouches[0];
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
  }, { passive: true });

  elements.app.addEventListener("touchend", (event) => {
    if (touchStartX === null || touchStartY === null) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - touchStartX;
    const dy = touch.clientY - touchStartY;
    touchStartX = null;
    touchStartY = null;

    if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.4) {
      // User requested: swiping right moves to the page on the right.
      goRelative(dx > 0 ? 1 : -1);
    }
  }, { passive: true });

  window.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") goRelative(1);
    if (event.key === "ArrowLeft") goRelative(-1);
    if (event.key === "+" || event.key === "=") adjustSetpoint(1);
    if (event.key === "-" || event.key === "_") adjustSetpoint(-1);
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
