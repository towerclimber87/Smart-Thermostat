const ABS_MIN = 45;
const ABS_MAX = 95;
const VIRTUAL_TEMP_MIN = 50;
const VIRTUAL_TEMP_MAX = 90;
const DIAL_SWEEP_DEG = 270;
const DIAL_START_DEG = 225;
const LEGACY_CONFIG_STORAGE_KEY = "smartThermostat.config.v2";
const CONFIG_API_ENDPOINT = "/api/config";
const HISTORY_API_ENDPOINT = "/api/history";
const HA_SYNC_INTERVAL_MS = 5000;
const HA_SYNC_AFTER_COMMAND_DELAYS = [900, 2400, 5200, 9800, 18000];
const BLIND_COMMAND_HOLD_MS = 90000;
const BLIND_POSITION_TOLERANCE = 2;
const COVER_FEATURE_OPEN_TILT = 16;
const COVER_FEATURE_CLOSE_TILT = 32;
const COVER_FEATURE_STOP_TILT = 64;
const COVER_FEATURE_SET_TILT_POSITION = 128;
const COVER_TILT_FEATURE_MASK = COVER_FEATURE_OPEN_TILT | COVER_FEATURE_CLOSE_TILT | COVER_FEATURE_STOP_TILT | COVER_FEATURE_SET_TILT_POSITION;
const HA_AUDIO_SYNC_INTERVAL_MS = 5000;
const HA_AUDIO_SYNC_AFTER_COMMAND_DELAYS = [700, 2200, 5000];
const HA_LIGHT_SYNC_INTERVAL_MS = 5000;
const HA_LIGHT_SYNC_AFTER_COMMAND_DELAYS = [700, 2200, 5000];
const HA_ROOM_SYNC_INTERVAL_MS = 5000;
const HA_ROOM_SYNC_AFTER_COMMAND_DELAYS = [700, 2200, 5000];
const INACTIVE_PAGE_SYNC_INTERVAL_MS = 5 * 60 * 1000;
const HA_ALARM_SYNC_INTERVAL_MS = 5000;
const HA_ALARM_SYNC_AFTER_COMMAND_DELAYS = [700, 2200, 5000];
const HA_DOOR_SYNC_INTERVAL_MS = 5000;
const HA_PRESENCE_SYNC_INTERVAL_MS = 5000;
const HA_PAUSE_FUNCTION_SYNC_INTERVAL_MS = 5000;
const HA_PAUSE_FUNCTION_FAST_SYNC_INTERVAL_MS = 1000;
const HA_WEATHER_SYNC_INTERVAL_MS = 60000;
const HA_TEMP_SENSOR_SYNC_INTERVAL_MS = 5000;
const LOCAL_THERMOSTAT_SYNC_INTERVAL_MS = 1000;
const VIRTUAL_TEMP_OVERRIDE_MS = 2 * 60 * 1000;
const LOCAL_THERMOSTAT_PUSH_DEBOUNCE_MS = 300;
const LIGHT_COLOR_PRESETS = [
  { name: "Warm White", color: "#ffd76f" },
  { name: "Soft White", color: "#fff2cc" },
  { name: "White", color: "#ffffff" },
  { name: "Red", color: "#ff3b3b" },
  { name: "Orange", color: "#ff8a2a" },
  { name: "Yellow", color: "#ffe04b" },
  { name: "Green", color: "#35e27a" },
  { name: "Cyan", color: "#35eaff" },
  { name: "Blue", color: "#3f7cff" },
  { name: "Purple", color: "#8b5cff" },
  { name: "Pink", color: "#ff5ec7" },
  { name: "Night", color: "#6fa8ff" },
];
const ALARM_AUTO_SUBMIT_LENGTH = 4;
const DEFAULT_USER_ACCESS_CODE = "3762";
const ROOM_CONTROL_CODE_MAX_LENGTH = 12;
const ROOM_CONTROL_MAX_ENTRIES = 12;
const ROOM_CONTROL_BED_SLIDER_HOLD_MS = 2200;
const ALARM_ARM_AWAY_DELAY_SECONDS = 60;
const ROOM_CONTROL_PICKER_DOMAINS = [
  "switch",
  "input_boolean",
  "light",
  "fan",
  "cover",
  "lock",
  "button",
  "input_button",
  "binary_sensor",
  "sensor",
  "number",
  "input_number",
  "select",
  "input_select",
  "scene",
  "script",
  "media_player",
  "climate",
  "humidifier",
  "vacuum",
  "person",
  "device_tracker",
];
const ROOM_CONTROL_READ_ONLY_DOMAINS = new Set(["binary_sensor", "sensor", "number", "input_number", "select", "input_select", "person", "device_tracker", "climate"]);
const ROOM_CONTROL_MOMENTARY_DOMAINS = new Set(["button", "input_button", "scene", "script"]);
const ROOM_CONTROL_EXCLUDED_DOMAINS = new Set(["automation"]);
const PANEL_THEMES = ["regular", "star-trek", "christmas"];
const DEFAULT_SCREEN_TIMEOUT_MINUTES = 5;
const MIN_SCREEN_TIMEOUT_MINUTES = 1;
const MAX_SCREEN_TIMEOUT_MINUTES = 120;
const SCREEN_TIMEOUT_CHECK_INTERVAL_MS = 5000;
const HARDWARE_STATUS_INTERVAL_MS = 5000;
const HISTORY_STATUS_INTERVAL_MS = 30000;
const SCHEDULE_CHECK_INTERVAL_MS = 15000;
const DEFAULT_PAUSE_FUNCTION_MINUTES = 5;
const MIN_PAUSE_FUNCTION_MINUTES = 1;
const MAX_PAUSE_FUNCTION_MINUTES = 60;
let haSyncInFlight = false;
let haSyncLastError = "";
let haAudioSyncInFlight = false;
let haAudioControlSyncInFlight = false;
let haAudioSyncLastError = "";
let haLightSyncInFlight = false;
let haLightSyncLastError = "";
let lightCommandInFlight = false;
let haRoomSyncInFlight = false;
let haRoomSyncLastError = "";
let roomControlCommandInFlight = false;
let suppressRoomControlClickUntil = 0;
let haAlarmSyncInFlight = false;
let haAlarmSyncLastError = "";
let haDoorSyncInFlight = false;
let haDoorSyncLastError = "";
let haPresenceSyncInFlight = false;
let haPresenceSyncLastError = "";
let haPauseFunctionSyncInFlight = false;
let haPauseFunctionSyncLastError = "";
let lastPauseFunctionFastSyncAt = 0;
let haWeatherSyncInFlight = false;
let haWeatherSyncLastError = "";
let haTempSensorSyncInFlight = false;
let haTempSensorSyncLastError = "";
let localThermostatSyncInFlight = false;
let localThermostatPushTimer = null;
let localThermostatPushInFlight = false;
let localThermostatLastError = "";
let hardwareStatusInFlight = false;
let hardwareLastError = "";
let historyStatusInFlight = false;
let historyLastError = "";
let lastLocalThermostatPushAt = 0;
let haAudioSyncTick = 0;
let lastInactiveBlindSyncAt = Date.now();
let lastInactiveAudioSyncAt = Date.now();
let lastInactiveLightSyncAt = Date.now();
let lastInactiveRoomSyncAt = Date.now();
let lastInactiveAlarmSyncAt = Date.now();
let lastInactiveDoorSyncAt = Date.now();
let lastScreenInteractionAt = Date.now();
let screenHasUserInteraction = false;
let startupDefaultScreenApplied = false;
let audioDefaultAutoShownForSession = false;
let audioMediaActionInFlight = false;
let audioPresetInFlight = false;
let audioVolumeDebounce = null;
let audioToneDebounces = { gain: null, bass: null, treble: null };
let audioTrackCommandLock = null;
let audioPlaybackLockedUntil = 0;
const AUDIO_TRACK_ACK_TIMEOUT_MS = 6500;
const AUDIO_PLAYBACK_LOCK_MS = 1400;
const AUDIO_SLIDER_RELEASE_MS = 900;
const AUDIO_VOLUME_SETTLE_MS = 1800;
const AUDIO_TONE_SETTLE_MS = 1500;
const SETPOINT_PREVIEW_MS = 2000;
const AUTO_CHANGEOVER_MINUTES = 120;
const MANUAL_CHANGEOVER_LOCKOUT_MINUTES = 10;
const FAN_SEQUENCE = ["off", "on", "auto"];
let lastBlindUserInteractionAt = 0;
const blindCommandHolds = new Map();
let lastAudioUserInteractionAt = 0;
let lastLightUserInteractionAt = 0;
let lastRoomControlUserInteractionAt = 0;
let audioSliderActive = { name: "", until: 0 };
let audioVolumeHoldUntil = 0;
let audioToneHoldUntil = { gain: 0, bass: 0, treble: 0 };
let audioPickerOpenedAt = 0;
let alarmPickerOpenedAt = 0;
let doorPickerOpenedAt = 0;
let lastAlarmUserInteractionAt = 0;
let lastDoorUserInteractionAt = 0;
let setpointPreviewUntil = 0;
let setpointPreviewTimeout = null;
let alarmArmAwayTimer = null;
let activeLightColorLightId = "";
let suppressLightIconClickUntil = 0;
let configSaveTimer = null;
let configSaveInFlight = false;
let configSaveQueued = false;
let configQueuedToast = false;
let virtualTempOverrideUntil = 0;
let virtualTempOverrideTimer = null;

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

const defaultLightConfig = {
  room: "living",
  rooms: {
    living: {
      label: "Living Room",
      lights: [
        { id: "ll-1", name: "Main Lights", brightness: 85, on: true, haEntityId: "", haName: "", color: "#ffd76f", colorSupported: false },
        { id: "ll-2", name: "Accent Lights", brightness: 45, on: true, haEntityId: "", haName: "", color: "#ffd76f", colorSupported: false },
        { id: "ll-3", name: "Lamp", brightness: 65, on: true, haEntityId: "", haName: "", color: "#ffd76f", colorSupported: false },
      ],
    },
    kitchen: {
      label: "Kitchen",
      lights: [
        { id: "kl-1", name: "Ceiling", brightness: 80, on: true, haEntityId: "", haName: "", color: "#ffd76f", colorSupported: false },
        { id: "kl-2", name: "Island", brightness: 55, on: true, haEntityId: "", haName: "", color: "#ffd76f", colorSupported: false },
      ],
    },
  },
};


const defaultRoomControlConfig = {
  room: "living",
  rooms: {
    living: {
      label: "Living Room",
      controls: [
        { id: "lr-control-1", name: "Entry 1", on: false, haEntityId: "", haName: "", domain: "switch", deviceClass: "", state: "off", currentPosition: null, supportedFeatures: 0, unitOfMeasurement: "", icon: "", displayType: "" },
        { id: "lr-control-2", name: "Entry 2", on: false, haEntityId: "", haName: "", domain: "input_boolean", deviceClass: "", state: "off", currentPosition: null, supportedFeatures: 0, unitOfMeasurement: "", icon: "", displayType: "" },
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
    audioControlEntities: { gain: null, bass: null, treble: null, subwoofer: null, surround: null, projector: null },
    audioAvailableEntities: { mediaPlayers: [], numbers: [], switches: [] },
    weatherEntity: { entityId: "weather.home", name: "Home" },
    weatherAvailableEntities: [],
    currentTempEntity: null,
    currentTempAvailableEntities: [],
    alarmEntity: null,
    alarmAvailableEntities: [],
    doorEntity: null,
    doorAvailableEntities: [],
    lightAvailableEntities: [],
    roomAvailableEntities: [],
    personAvailableEntities: [],
    pauseFunctionAvailableEntities: [],
  },
};

const AUDIO_PRESETS = {
  movie: {
    label: "Movie Mode",
    volume: 65,
    gain: "max",
    bass: "max",
    treble: 8,
    subwoofer: true,
    surround: true,
  },
  show: {
    label: "Show Mode",
    volume: 65,
    gain: 0,
    bass: 0,
    treble: 8,
    subwoofer: false,
    surround: true,
  },
  volume40: {
    label: "40% Volume",
    volume: 40,
    gain: "max",
    bass: "max",
    treble: 8,
    subwoofer: true,
    surround: true,
  },
  max: {
    label: "Max",
    volume: 100,
    gain: "max",
    bass: "max",
    treble: 8,
    subwoofer: true,
    surround: true,
  },
};

const state = {
  pages: ["blinds", "audio", "thermostat", "lights", "room"],
  currentPage: "thermostat",
  panelLock: { locked: false },
  userAccessCode: DEFAULT_USER_ACCESS_CODE,
  settingsAccessCode: "",
  settingsAccessTarget: "settings",
  theme: "regular",
  screenTimeoutMinutes: DEFAULT_SCREEN_TIMEOUT_MINUTES,
  thermostat: {
    name: "IHA Thermostat",
    currentTemp: 70,
    currentTempSource: "virtual",
    currentTempSourceName: "Virtual Temp",
    targetTemp: 70,
    lastComfortTarget: 70,
    mode: "cool",
    fan: "auto",
    away: false,
    awaySource: "",
    manualAwayPresenceLatch: null,
    awayHeat: 55,
    awayCool: 85,
    safetyLow: 55,
    safetyHigh: 85,
    humidity: 45,
    outdoorTemp: 78,
    outdoorWindSpeed: 0,
    outdoorWindUnit: "mph",
    autoCoolOutdoorTarget: 70,
    autoHeatOutdoorTarget: 65,
    autoChangeoverLockoutMinutes: AUTO_CHANGEOVER_MINUTES,
    manualChangeoverLockoutMinutes: MANUAL_CHANGEOVER_LOCKOUT_MINUTES,
    coolFanRemainOnMinutes: 2,
    heatLocked: false,
    coolLocked: false,
    people: [],
    schedules: [],
    autoActiveMode: "cool",
    autoPendingMode: "",
    autoLockoutUntil: 0,
    manualPendingMode: "",
    manualLockoutUntil: 0,
    lastHeatRunAt: 0,
    lastCoolRunAt: 0,
    equipmentLastHeatRunAt: 0,
    equipmentLastCoolRunAt: 0,
    coolRelayWasOn: false,
    coolFanHoldUntil: 0,
    pauseFunction: {
      durationMinutes: DEFAULT_PAUSE_FUNCTION_MINUTES,
      entries: [],
      active: false,
      pausedAt: 0,
      previousTargetTemp: null,
      previousLastComfortTarget: null,
      activeEntityIds: [],
    },
    autoSwitchNotice: { active: false, source: "", fromMode: "", toMode: "", switchTemp: 0, outdoorTemp: 0, coolTarget: 0, heatTarget: 0, createdAt: 0 },
    autoSwitchHold: { active: false, source: "", mode: "" },
    limits: {
      cool: { min: 65, max: 80 },
      heat: { min: 60, max: 78 },
      auto: { min: 60, max: 80 },
    },
  },
  scheduleEditor: { selectedId: "", draft: null },
  scheduleTimePicker: { hour12: 8, minute: 0, meridiem: "PM" },
  alarm: {
    entityId: "",
    name: "Alarm",
    state: "unassigned",
    code: "",
    disarmCode: "",
    disarming: false,
    arming: false,
    armMode: "",
    armAwayCountdown: 0,
  },
  door: {
    entityId: "",
    name: "Door",
    state: "unassigned",
    deviceClass: "door",
  },
  audio: {
    playing: false,
    trackIndex: 0,
    progress: 34,
    volume: 42,
    gain: 0,
    bass: 2,
    treble: 4,
    subwoofer: false,
    surround: false,
    projector: false,
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
  lights: JSON.parse(JSON.stringify(defaultLightConfig)),
  roomControl: JSON.parse(JSON.stringify(defaultRoomControlConfig)),
  integrations: JSON.parse(JSON.stringify(defaultIntegrations)),
  entityPicker: { roomKey: null, blindId: null },
  lightEntityPicker: { roomKey: null, lightId: null },
  roomControlEntityPicker: { roomKey: null, controlId: null },
  roomControlCodePrompt: { roomKey: null, controlId: null, action: "", code: "", busy: false },
  audioEntityPicker: { kind: null, domain: null, entities: [], search: "" },
  diagnostics: { haLogs: [] },
  systemInfo: { ipAddress: "", port: "", address: "", version: "", host: "", thermostatName: "", uptime: "", systemUptime: "", appUptime: "" },
  hardware: {
    loaded: false,
    gpio: { backend: "", available: false, error: "", source: "thermostat", activeLow: false },
    manual: { active: false, relays: { fan: false, cool: false, heat: false } },
    relays: { fan: { on: false, gpio: 7, physical: 26 }, cool: { on: false, gpio: 8, physical: 24 }, heat: { on: false, gpio: 22, physical: 15 } },
    rgb: { on: false, color: "#35eaff", backend: "", available: false, error: "", gpio: 26, physical: 37 },
    i2c: { addresses: [], devices: [], bus: 1, backend: "", scannedAt: 0, error: "", enabled: false, available: false, devicePath: "/dev/i2c-1" },
  },
  history: {
    loaded: false,
    selectedDate: "",
    today: "",
    availableDates: [],
    summary: { fan: { totalMs: 0, cycles: 0, active: false }, heat: { totalMs: 0, cycles: 0, active: false }, cool: { totalMs: 0, cycles: 0, active: false } },
    periods: [],
    updatedAtMs: 0,
    writePolicy: "",
    error: "",
  },
};

const elements = {
  app: document.getElementById("app"),
  screenTrack: document.getElementById("screenTrack"),
  clock: document.getElementById("clock"),
  toast: document.getElementById("toast"),
  currentTemp: document.getElementById("currentTemp"),
  targetTemp: document.getElementById("targetTemp"),
  primaryTempLabel: document.getElementById("primaryTempLabel"),
  secondaryTempLabel: document.getElementById("secondaryTempLabel"),
  secondaryTempRow: document.getElementById("secondaryTempRow"),
  humidityValue: document.getElementById("humidityValue"),
  thermoDial: document.getElementById("thermoDial"),
  dialTargetBadge: document.getElementById("dialTargetBadge"),
  bootOverlay: document.getElementById("bootOverlay"),
  thermostatPanelLockButton: document.getElementById("thermostatPanelLockButton"),
  thermostatPanelLockLabel: document.getElementById("thermostatPanelLockLabel"),
  changeoverBypassButton: document.getElementById("changeoverBypassButton"),
  modeBadge: document.getElementById("modeBadge"),
  runtimeState: document.getElementById("runtimeState"),
  pauseCountdownBadge: document.getElementById("pauseCountdownBadge"),
  pauseCountdownText: document.getElementById("pauseCountdownText"),
  pauseFunctionOverlay: document.getElementById("pauseFunctionOverlay"),
  pauseFunctionMessage: document.getElementById("pauseFunctionMessage"),
  safetyWarningBanner: document.getElementById("safetyWarningBanner"),
  safetyWarningTitle: document.getElementById("safetyWarningTitle"),
  safetyWarningSetpoint: document.getElementById("safetyWarningSetpoint"),
  safetyWarningCurrent: document.getElementById("safetyWarningCurrent"),
  autoSwitchNotice: document.getElementById("autoSwitchNotice"),
  autoSwitchNoticeMode: document.getElementById("autoSwitchNoticeMode"),
  autoSwitchNoticeDetail: document.getElementById("autoSwitchNoticeDetail"),
  autoSwitchOverlay: document.getElementById("autoSwitchOverlay"),
  autoSwitchClose: document.getElementById("autoSwitchClose"),
  autoSwitchTitle: document.getElementById("autoSwitchTitle"),
  autoSwitchMessage: document.getElementById("autoSwitchMessage"),
  autoSwitchDismissButton: document.getElementById("autoSwitchDismissButton"),
  autoSwitchRevertButton: document.getElementById("autoSwitchRevertButton"),
  autoConfirmOverlay: document.getElementById("autoConfirmOverlay"),
  autoConfirmClose: document.getElementById("autoConfirmClose"),
  autoConfirmCancelButton: document.getElementById("autoConfirmCancelButton"),
  autoConfirmSwitchButton: document.getElementById("autoConfirmSwitchButton"),
  awayToggle: document.getElementById("awayToggle"),
  awayModeOverlay: document.getElementById("awayModeOverlay"),
  awayHomeButton: document.getElementById("awayHomeButton"),
  awayHeatValue: document.getElementById("awayHeatValue"),
  awayCoolValue: document.getElementById("awayCoolValue"),
  safetyLowValue: document.getElementById("safetyLowValue"),
  safetyHighValue: document.getElementById("safetyHighValue"),
  fanSummary: document.getElementById("fanSummary"),
  fanChip: document.getElementById("fanChip"),
  scheduleButton: document.getElementById("scheduleButton"),
  schedulePresetBar: document.getElementById("schedulePresetBar"),
  scheduleOverlay: document.getElementById("scheduleOverlay"),
  scheduleClose: document.getElementById("scheduleClose"),
  scheduleAddButton: document.getElementById("scheduleAddButton"),
  scheduleList: document.getElementById("scheduleList"),
  scheduleNameInput: document.getElementById("scheduleNameInput"),
  scheduleTimeInput: document.getElementById("scheduleTimeInput"),
  scheduleEnabledToggle: document.getElementById("scheduleEnabledToggle"),
  scheduleCoolValue: document.getElementById("scheduleCoolValue"),
  scheduleHeatValue: document.getElementById("scheduleHeatValue"),
  scheduleConditionSummary: document.getElementById("scheduleConditionSummary"),
  schedulePersonChips: document.getElementById("schedulePersonChips"),
  scheduleDeleteButton: document.getElementById("scheduleDeleteButton"),
  scheduleSaveButton: document.getElementById("scheduleSaveButton"),
  scheduleTimePickerOverlay: document.getElementById("scheduleTimePickerOverlay"),
  scheduleTimePickerClose: document.getElementById("scheduleTimePickerClose"),
  scheduleTimePickerTitle: document.getElementById("scheduleTimePickerTitle"),
  scheduleTimePreview: document.getElementById("scheduleTimePreview"),
  scheduleTimeHourValue: document.getElementById("scheduleTimeHourValue"),
  scheduleTimeMinuteValue: document.getElementById("scheduleTimeMinuteValue"),
  scheduleMinuteShortcuts: document.getElementById("scheduleMinuteShortcuts"),
  scheduleTimePickerCancel: document.getElementById("scheduleTimePickerCancel"),
  scheduleTimePickerApply: document.getElementById("scheduleTimePickerApply"),
  relayFan: document.getElementById("relayFan"),
  relayHeat: document.getElementById("relayHeat"),
  relayCool: document.getElementById("relayCool"),
  virtualTempSlider: document.getElementById("virtualTempSlider"),
  virtualTempValue: document.getElementById("virtualTempValue"),
  virtualTempOverrideStatus: document.getElementById("virtualTempOverrideStatus"),
  outdoorTempSlider: document.getElementById("outdoorTempSlider"),
  outdoorTempValue: document.getElementById("outdoorTempValue"),
  outdoorTempTopValue: document.getElementById("outdoorTempTopValue"),
  outdoorWindTopValue: document.getElementById("outdoorWindTopValue"),
  autoCoolOutdoorTargetValue: document.getElementById("autoCoolOutdoorTargetValue"),
  autoHeatOutdoorTargetValue: document.getElementById("autoHeatOutdoorTargetValue"),
  autoLockoutValue: document.getElementById("autoLockoutValue"),
  coolFanRemainValue: document.getElementById("coolFanRemainValue"),
  alarmWidget: document.getElementById("alarmWidget"),
  alarmWidgetTitle: document.getElementById("alarmWidgetTitle"),
  alarmWidgetState: document.getElementById("alarmWidgetState"),
  doorWidget: document.getElementById("doorWidget"),
  doorWidgetTitle: document.getElementById("doorWidgetTitle"),
  doorWidgetState: document.getElementById("doorWidgetState"),
  alarmKeypadOverlay: document.getElementById("alarmKeypadOverlay"),
  alarmKeypadClose: document.getElementById("alarmKeypadClose"),
  alarmKeypadTitle: document.getElementById("alarmKeypadTitle"),
  alarmKeypadStatus: document.getElementById("alarmKeypadStatus"),
  alarmCodeDots: document.getElementById("alarmCodeDots"),
  alarmKeypadGrid: document.getElementById("alarmKeypadGrid"),
  alarmDisarmButton: document.getElementById("alarmDisarmButton"),
  alarmArmOverlay: document.getElementById("alarmArmOverlay"),
  alarmArmTitle: document.getElementById("alarmArmTitle"),
  alarmArmStatus: document.getElementById("alarmArmStatus"),
  alarmArmCountdown: document.getElementById("alarmArmCountdown"),
  alarmArmActions: document.getElementById("alarmArmActions"),
  alarmArmHomeButton: document.getElementById("alarmArmHomeButton"),
  alarmArmAwayButton: document.getElementById("alarmArmAwayButton"),
  alarmArmCancelButton: document.getElementById("alarmArmCancelButton"),
  alarmDisarmCodeInput: document.getElementById("alarmDisarmCodeInput"),
  saveAlarmCodeButton: document.getElementById("saveAlarmCodeButton"),
  userAccessCodeInput: document.getElementById("userAccessCodeInput"),
  saveUserAccessCodeButton: document.getElementById("saveUserAccessCodeButton"),
  thermostatNameInput: document.getElementById("thermostatNameInput"),
  saveThermostatNameButton: document.getElementById("saveThermostatNameButton"),
  themeChoiceButtons: Array.from(document.querySelectorAll("[data-theme-choice]")),
  screenTimeoutMinutesInput: document.getElementById("screenTimeoutMinutesInput"),
  screenTimeoutSummary: document.getElementById("screenTimeoutSummary"),
  currentTempSourceName: document.getElementById("currentTempSourceName"),
  currentTempSourceId: document.getElementById("currentTempSourceId"),
  currentTempSourceStatus: document.getElementById("currentTempSourceStatus"),
  chooseCurrentTempSensorButton: document.getElementById("chooseCurrentTempSensorButton"),
  clearCurrentTempSensorButton: document.getElementById("clearCurrentTempSensorButton"),
  addThermostatPersonButton: document.getElementById("addThermostatPersonButton"),
  thermostatPeopleList: document.getElementById("thermostatPeopleList"),
  pauseFunctionMinutesValue: document.getElementById("pauseFunctionMinutesValue"),
  addPauseFunctionEntryButton: document.getElementById("addPauseFunctionEntryButton"),
  pauseFunctionEntryList: document.getElementById("pauseFunctionEntryList"),
  pauseFunctionSummary: document.getElementById("pauseFunctionSummary"),
  heatLockToggle: document.getElementById("heatLockToggle"),
  coolLockToggle: document.getElementById("coolLockToggle"),
  thermostatInfoButton: document.getElementById("thermostatInfoButton"),
  thermostatInfoOverlay: document.getElementById("thermostatInfoOverlay"),
  thermostatInfoClose: document.getElementById("thermostatInfoClose"),
  unitNameValue: document.getElementById("unitNameValue"),
  unitIpValue: document.getElementById("unitIpValue"),
  unitVersionValue: document.getElementById("unitVersionValue"),
  unitUptimeValue: document.getElementById("unitUptimeValue"),
  unitThermalValue: document.getElementById("unitThermalValue"),
  fetchUpdateButton: document.getElementById("fetchUpdateButton"),
  restartServerButton: document.getElementById("restartServerButton"),
  fetchUpdateStatus: document.getElementById("fetchUpdateStatus"),
  dialMinLabel: document.getElementById("dialMinLabel"),
  dialMaxLabel: document.getElementById("dialMaxLabel"),
  settingsOverlay: document.getElementById("settingsOverlay"),
  settingsSheet: document.getElementById("settingsSheet"),
  settingsButton: document.getElementById("settingsButton"),
  settingsCodeOverlay: document.getElementById("settingsCodeOverlay"),
  settingsCodeClose: document.getElementById("settingsCodeClose"),
  settingsCodeTitle: document.getElementById("settingsCodeTitle"),
  settingsCodeStatus: document.getElementById("settingsCodeStatus"),
  settingsCodeDots: document.getElementById("settingsCodeDots"),
  settingsCodeGrid: document.getElementById("settingsCodeGrid"),
  tempMiniStatus: document.getElementById("tempMiniStatus"),
  headerCurrentTemp: document.getElementById("headerCurrentTemp"),
  headerSetTemp: document.getElementById("headerSetTemp"),
  headerSetPill: document.getElementById("headerSetPill"),
  settingsClose: document.getElementById("settingsClose"),
  settingsDone: document.getElementById("settingsDone"),
  settingsTitle: document.getElementById("settingsTitle"),
  settingsEyebrow: document.getElementById("settingsEyebrow"),
  settingsFooter: document.getElementById("settingsFooter"),
  thermostatSettingsView: document.getElementById("thermostatSettingsView"),
  hardwareSettingsView: document.getElementById("hardwareSettingsView"),
  openHardwareInfoButton: document.getElementById("openHardwareInfoButton"),
  backToComfortSetup: document.getElementById("backToComfortSetup"),
  refreshHardwareButton: document.getElementById("refreshHardwareButton"),
  hardwareBackendStatus: document.getElementById("hardwareBackendStatus"),
  hardwareManualStatus: document.getElementById("hardwareManualStatus"),
  releaseHardwareManualButton: document.getElementById("releaseHardwareManualButton"),
  hardwareRelayButtons: Array.from(document.querySelectorAll("[data-hardware-relay]")),
  hardwareRgbPowerButton: document.getElementById("hardwareRgbPowerButton"),
  hardwareRgbColorInput: document.getElementById("hardwareRgbColorInput"),
  hardwareRgbPresetGrid: document.getElementById("hardwareRgbPresetGrid"),
  hardwareRgbStatus: document.getElementById("hardwareRgbStatus"),
  i2cAddressList: document.getElementById("i2cAddressList"),
  i2cStatusLine: document.getElementById("i2cStatusLine"),
  historySettingsView: document.getElementById("historySettingsView"),
  openHistoryButton: document.getElementById("openHistoryButton"),
  backToComfortSetupFromHistory: document.getElementById("backToComfortSetupFromHistory"),
  refreshHistoryButton: document.getElementById("refreshHistoryButton"),
  historyDateInput: document.getElementById("historyDateInput"),
  historyPrevDayButton: document.getElementById("historyPrevDayButton"),
  historyNextDayButton: document.getElementById("historyNextDayButton"),
  historyWritePolicy: document.getElementById("historyWritePolicy"),
  historySelectedDateTitle: document.getElementById("historySelectedDateTitle"),
  historyUpdatedPill: document.getElementById("historyUpdatedPill"),
  historyCoolTotal: document.getElementById("historyCoolTotal"),
  historyHeatTotal: document.getElementById("historyHeatTotal"),
  historyFanTotal: document.getElementById("historyFanTotal"),
  historyCoolCycles: document.getElementById("historyCoolCycles"),
  historyHeatCycles: document.getElementById("historyHeatCycles"),
  historyFanCycles: document.getElementById("historyFanCycles"),
  historyTimeline: document.getElementById("historyTimeline"),
  blindSettingsView: document.getElementById("blindSettingsView"),
  audioSettingsView: document.getElementById("audioSettingsView"),
  lightsSettingsView: document.getElementById("lightsSettingsView"),
  roomControlSettingsView: document.getElementById("roomControlSettingsView"),
  blindSetupView: document.getElementById("blindSetupView"),
  blindHaView: document.getElementById("blindHaView"),
  audioHaView: document.getElementById("audioHaView"),
  lightsSetupView: document.getElementById("lightsSetupView"),
  lightsHaView: document.getElementById("lightsHaView"),
  roomTabs: document.getElementById("roomTabs"),
  roomConfigList: document.getElementById("roomConfigList"),
  blindCards: document.getElementById("blindCards"),
  blindRoomTitle: document.getElementById("blindRoomTitle"),
  lightRoomTabs: document.getElementById("lightRoomTabs"),
  lightRoomConfigList: document.getElementById("lightRoomConfigList"),
  lightCards: document.getElementById("lightCards"),
  lightRoomTitle: document.getElementById("lightRoomTitle"),
  lightRoomSummary: document.getElementById("lightRoomSummary"),
  roomControlTabs: document.getElementById("roomControlTabs"),
  roomControlConfigList: document.getElementById("roomControlConfigList"),
  roomControlCards: document.getElementById("roomControlCards"),
  roomControlRoomTitle: document.getElementById("roomControlRoomTitle"),
  roomControlSettingsView: document.getElementById("roomControlSettingsView"),
  roomControlSetupView: document.getElementById("roomControlSetupView"),
  lightColorOverlay: document.getElementById("lightColorOverlay"),
  lightColorPickerTitle: document.getElementById("lightColorPickerTitle"),
  lightColorPickerClose: document.getElementById("lightColorPickerClose"),
  lightColorPresetGrid: document.getElementById("lightColorPresetGrid"),
  lightColorPreview: document.getElementById("lightColorPreview"),
  lightColorName: document.getElementById("lightColorName"),
  haUrlInput: document.getElementById("haUrlInput"),
  haTokenInput: document.getElementById("haTokenInput"),
  audioHaUrlInput: document.getElementById("audioHaUrlInput"),
  audioHaTokenInput: document.getElementById("audioHaTokenInput"),
  lightHaUrlInput: document.getElementById("lightHaUrlInput"),
  lightHaTokenInput: document.getElementById("lightHaTokenInput"),
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
  subwooferToggle: document.getElementById("subwooferToggle"),
  surroundToggle: document.getElementById("surroundToggle"),
  projectorToggle: document.getElementById("projectorToggle"),
  audioEntityPickerOverlay: document.getElementById("audioEntityPickerOverlay"),
  audioEntityPickerTitle: document.getElementById("audioEntityPickerTitle"),
  audioEntityPickerEyebrow: document.getElementById("audioEntityPickerEyebrow"),
  audioEntityPickerHelp: document.getElementById("audioEntityPickerHelp"),
  audioEntityPickerList: document.getElementById("audioEntityPickerList"),
  audioEntityPickerClose: document.getElementById("audioEntityPickerClose"),
  audioEntitySearch: document.getElementById("audioEntitySearch"),
  roomControlCodeOverlay: document.getElementById("roomControlCodeOverlay"),
  roomControlCodeTitle: document.getElementById("roomControlCodeTitle"),
  roomControlCodeStatus: document.getElementById("roomControlCodeStatus"),
  roomControlCodeDots: document.getElementById("roomControlCodeDots"),
  roomControlCodeGrid: document.getElementById("roomControlCodeGrid"),
  roomControlCodeClose: document.getElementById("roomControlCodeClose"),
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

function normalizeHexColor(value, fallback = "#ffd76f") {
  const raw = String(value || "").trim();
  const short = /^#?([0-9a-f]{3})$/i.exec(raw);
  if (short) {
    const [r, g, b] = short[1].split("").map((part) => part + part);
    return `#${r}${g}${b}`.toLowerCase();
  }
  const full = /^#?([0-9a-f]{6})$/i.exec(raw);
  return full ? `#${full[1]}`.toLowerCase() : fallback;
}

function hexToRgb(value) {
  const hex = normalizeHexColor(value).slice(1);
  return {
    r: parseInt(hex.slice(0, 2), 16),
    g: parseInt(hex.slice(2, 4), 16),
    b: parseInt(hex.slice(4, 6), 16),
  };
}

function rgbToHex(r, g, b) {
  return `#${[r, g, b].map((part) => clamp(Math.round(Number(part) || 0), 0, 255).toString(16).padStart(2, "0")).join("")}`;
}

function lightDisplayName(light) {
  return String(light?.haName || light?.name || "Light").trim() || "Light";
}

function getLightPresetName(color) {
  const normalized = normalizeHexColor(color);
  return LIGHT_COLOR_PRESETS.find((preset) => normalizeHexColor(preset.color) === normalized)?.name || "Custom";
}

function setOverlayOpen(overlay, open) {
  if (!overlay) return;
  overlay.classList.toggle("open", Boolean(open));
  overlay.setAttribute("aria-hidden", open ? "false" : "true");
}

function lightColorModesSupportColor(modes = []) {
  return Array.isArray(modes) && modes.some((mode) => ["hs", "rgb", "rgbw", "rgbww", "xy"].includes(String(mode || "").toLowerCase()));
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

function isEditableHaFieldVisible(field) {
  if (!field) return false;
  if (field === document.activeElement) return true;
  let node = field;
  while (node && node !== document.body) {
    if (node.hidden) return false;
    node = node.parentElement;
  }
  return Boolean(field.offsetParent || field.getClientRects().length);
}

function readHaFieldsFromScreen(context = "blinds") {
  const fieldMap = {
    audio: { url: elements.audioHaUrlInput, token: elements.audioHaTokenInput },
    lights: { url: elements.lightHaUrlInput, token: elements.lightHaTokenInput },
    blinds: { url: elements.haUrlInput, token: elements.haTokenInput },
  };
  const fields = fieldMap[context] || fieldMap.blinds;

  // Only read the currently visible HA form. Hidden duplicate forms can still
  // exist in the DOM with blank values; reading them was clearing the saved API
  // URL/token when assigning entities from another page.
  if (isEditableHaFieldVisible(fields.url)) state.integrations.homeAssistant.url = fields.url.value.trim();
  if (isEditableHaFieldVisible(fields.token)) state.integrations.homeAssistant.token = fields.token.value.trim();
}


function slugify(value, rooms = state.blinds.rooms) {
  const base = String(value || "room").toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "room";
  let key = base;
  let index = 2;
  while (rooms[key]) {
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

function getLightRoomKeys() {
  return Object.keys(state.lights.rooms || {});
}

function getActiveLightRoom() {
  const keys = getLightRoomKeys();
  if (!keys.length) {
    state.lights = JSON.parse(JSON.stringify(defaultLightConfig));
    return state.lights.rooms[state.lights.room];
  }
  if (!state.lights.rooms[state.lights.room]) state.lights.room = keys[0];
  return state.lights.rooms[state.lights.room];
}

function getRoomControlKeys() {
  return Object.keys(state.roomControl.rooms || {});
}

function getActiveRoomControlRoom() {
  const keys = getRoomControlKeys();
  if (!keys.length) {
    state.roomControl = JSON.parse(JSON.stringify(defaultRoomControlConfig));
    return state.roomControl.rooms[state.roomControl.room];
  }
  if (!state.roomControl.rooms[state.roomControl.room]) state.roomControl.room = keys[0];
  return normalizeRoomControlRoom(state.roomControl.rooms[state.roomControl.room], state.roomControl.room);
}

function normalizePanelTheme(value) {
  const candidate = String(value || "").trim().toLowerCase();
  if (["star-trek", "startrek", "star_trek", "star trek", "trek"].includes(candidate)) return "star-trek";
  if (["christmas", "xmas", "holiday", "holidays", "festive"].includes(candidate)) return "christmas";
  return "regular";
}

function getPanelThemeMetaColor(theme = state.theme) {
  const normalized = normalizePanelTheme(theme);
  if (normalized === "star-trek") return "#6d5b49";
  if (normalized === "christmas") return "#143b2f";
  return "#050812";
}

function renderPanelThemePicker() {
  const activeTheme = normalizePanelTheme(state.theme);
  (elements.themeChoiceButtons || []).forEach((button) => {
    const active = normalizePanelTheme(button.dataset.themeChoice) === activeTheme;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function applyPanelTheme(theme, options = {}) {
  const nextTheme = normalizePanelTheme(theme);
  const changed = state.theme !== nextTheme;
  state.theme = nextTheme;
  document.documentElement.dataset.theme = nextTheme;
  document.body.dataset.theme = nextTheme;
  elements.app?.setAttribute("data-theme", nextTheme);
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.setAttribute("content", getPanelThemeMetaColor(nextTheme));
  renderPanelThemePicker();
  renderScreenTimeoutSettings();
  if (changed && options.save !== false) saveConfig({ toast: false });
  if (changed && options.toast) {
    const themeLabel = nextTheme === "star-trek" ? "Star Trek" : nextTheme === "christmas" ? "Christmas" : "Regular";
    showToast(`Theme set to ${themeLabel}`);
  }
  return changed;
}

function normalizeScreenTimeoutMinutes(value, fallback = DEFAULT_SCREEN_TIMEOUT_MINUTES) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return clamp(Math.round(number), MIN_SCREEN_TIMEOUT_MINUTES, MAX_SCREEN_TIMEOUT_MINUTES);
}

function isAudioSourceTv(source = state.audio.source) {
  const normalized = String(source || "").trim().toLowerCase();
  const title = String(state.audio.title || "").trim().toLowerCase();
  return normalized === "tv" || normalized === "television" || (!normalized && (title === "tv" || title === "television"));
}

function shouldDefaultToAudioScreen() {
  return Boolean(state.audio.playing) && !isAudioSourceTv(state.audio.source);
}

function getDefaultScreenPage() {
  return shouldDefaultToAudioScreen() ? "audio" : "thermostat";
}

function maybeAutoShowAudioOnPlayback(wasAudioDefault = false) {
  const isAudioDefault = shouldDefaultToAudioScreen();
  if (!isAudioDefault) {
    audioDefaultAutoShownForSession = false;
    return;
  }
  if (wasAudioDefault || audioDefaultAutoShownForSession || state.currentPage === "audio") {
    audioDefaultAutoShownForSession = state.currentPage === "audio" || audioDefaultAutoShownForSession;
    return;
  }
  if (document.visibilityState === "hidden" || isAnyOverlayOpen() || isPanelLocked()) return;
  audioDefaultAutoShownForSession = true;
  gotoPage("audio", { silent: true, autoTimeout: true });
}

function defaultScreenLabel() {
  return getDefaultScreenPage() === "audio" ? "Audio" : "Thermostat";
}

function renderScreenTimeoutSettings() {
  const minutes = normalizeScreenTimeoutMinutes(state.screenTimeoutMinutes);
  state.screenTimeoutMinutes = minutes;
  if (elements.screenTimeoutMinutesInput && document.activeElement !== elements.screenTimeoutMinutesInput) {
    elements.screenTimeoutMinutesInput.value = String(minutes);
  }
  if (elements.screenTimeoutSummary) {
    const label = defaultScreenLabel();
    elements.screenTimeoutSummary.textContent = `After ${minutes} min inactive, return to ${label}. Audio stays default only while the selected player is playing and its source is not TV.`;
  }
}

function setScreenTimeoutMinutes(value, options = {}) {
  const next = normalizeScreenTimeoutMinutes(value, state.screenTimeoutMinutes);
  const changed = next !== state.screenTimeoutMinutes;
  state.screenTimeoutMinutes = next;
  renderScreenTimeoutSettings();
  if (changed && options.save !== false) saveConfig({ toast: false });
  if (changed && options.toast) showToast(`Screen timeout set to ${next} min`);
  maybeApplyScreenTimeout();
}

function markScreenActivity() {
  lastScreenInteractionAt = Date.now();
  screenHasUserInteraction = true;
}

function isAnyOverlayOpen() {
  return Boolean(document.querySelector(".settings-overlay.open, .away-mode-overlay.open"));
}

function maybeApplyScreenTimeout(options = {}) {
  if (document.visibilityState === "hidden") return;
  if (isAnyOverlayOpen()) return;
  if (options.startup && screenHasUserInteraction) return;
  const timeoutMs = normalizeScreenTimeoutMinutes(state.screenTimeoutMinutes) * 60 * 1000;
  if (!options.force && Date.now() - lastScreenInteractionAt < timeoutMs) return;
  const targetPage = getDefaultScreenPage();
  if (targetPage === state.currentPage) return;
  gotoPage(targetPage, { silent: true, autoTimeout: true });
}

function scheduleDefaultScreenCheck(delay = 250) {
  window.setTimeout(() => maybeApplyScreenTimeout(), delay);
}

function maybeApplyStartupDefaultScreen() {
  if (startupDefaultScreenApplied) return;
  startupDefaultScreenApplied = true;
  window.setTimeout(() => maybeApplyScreenTimeout({ force: true, startup: true }), 250);
}

function serializeLightsConfig() {
  const copy = clone(state.lights || defaultLightConfig);
  Object.values(copy.rooms || {}).forEach((room) => {
    (room.lights || []).forEach((light) => {
      delete light.localHoldUntil;
    });
  });
  return copy;
}

function serializeRoomControlConfig() {
  const copy = clone(state.roomControl || defaultRoomControlConfig);
  Object.values(copy.rooms || {}).forEach((room) => {
    (room.controls || []).forEach((control) => {
      delete control.localHoldUntil;
    });
  });
  return copy;
}

function buildSavedConfig() {
  const thermostatToSave = {
    name: state.thermostat.name || "IHA Thermostat",
    currentTemp: state.thermostat.currentTemp,
    currentTempSource: state.thermostat.currentTempSource || "virtual",
    currentTempSourceName: state.thermostat.currentTempSourceName || "Virtual Temp",
    targetTemp: state.thermostat.targetTemp,
    lastComfortTarget: state.thermostat.lastComfortTarget,
    mode: state.thermostat.mode,
    fan: state.thermostat.fan,
    away: Boolean(state.thermostat.away),
    awaySource: normalizeAwaySource(state.thermostat.awaySource),
    manualAwayPresenceLatch: normalizeManualAwayPresenceLatch(state.thermostat.manualAwayPresenceLatch),
    awayHeat: state.thermostat.awayHeat,
    awayCool: state.thermostat.awayCool,
    safetyLow: state.thermostat.safetyLow,
    safetyHigh: state.thermostat.safetyHigh,
    humidity: state.thermostat.humidity,
    outdoorTemp: state.thermostat.outdoorTemp,
    outdoorWindSpeed: state.thermostat.outdoorWindSpeed,
    outdoorWindUnit: state.thermostat.outdoorWindUnit || "mph",
    autoCoolOutdoorTarget: state.thermostat.autoCoolOutdoorTarget,
    autoHeatOutdoorTarget: state.thermostat.autoHeatOutdoorTarget,
    autoChangeoverLockoutMinutes: state.thermostat.autoChangeoverLockoutMinutes,
    manualChangeoverLockoutMinutes: state.thermostat.manualChangeoverLockoutMinutes,
    coolFanRemainOnMinutes: state.thermostat.coolFanRemainOnMinutes,
    heatLocked: Boolean(state.thermostat.heatLocked),
    coolLocked: Boolean(state.thermostat.coolLocked),
    people: normalizeThermostatPeople(state.thermostat.people),
    schedules: normalizeThermostatSchedules(state.thermostat.schedules),
    pauseFunction: pauseFunctionSettingsSnapshot(),
    autoActiveMode: state.thermostat.autoActiveMode,
    autoSwitchNotice: normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice),
    autoSwitchHold: normalizeAutoSwitchHold(state.thermostat.autoSwitchHold),
    limits: state.thermostat.limits,
  };
  return {
    version: 20,
    theme: normalizePanelTheme(state.theme),
    screenTimeoutMinutes: normalizeScreenTimeoutMinutes(state.screenTimeoutMinutes),
    panelLock: { locked: Boolean(state.panelLock?.locked) },
    userAccessCode: getUserAccessCode(),
    thermostat: thermostatToSave,
    alarm: {
      disarmCode: String(state.alarm.disarmCode || "").replace(/\D/g, "").slice(0, 8),
    },
    blinds: state.blinds,
    lights: serializeLightsConfig(),
    roomControl: serializeRoomControlConfig(),
    integrations: state.integrations,
  };
}

function applySavedConfig(saved = {}) {
  state.theme = normalizePanelTheme(saved?.theme || saved?.appearance?.theme || state.theme);
  state.screenTimeoutMinutes = normalizeScreenTimeoutMinutes(saved?.screenTimeoutMinutes ?? saved?.appearance?.screenTimeoutMinutes ?? state.screenTimeoutMinutes);
  if (saved?.panelLock && typeof saved.panelLock === "object") {
    state.panelLock.locked = Boolean(saved.panelLock.locked);
  }
  if (typeof saved?.panelLocked === "boolean") state.panelLock.locked = saved.panelLocked;
  const savedUserAccessCode = String(saved?.userAccessCode || saved?.security?.userAccessCode || saved?.settingsAccessCode || "").replace(/\D/g, "").slice(0, 4);
  state.userAccessCode = savedUserAccessCode.length === 4 ? savedUserAccessCode : DEFAULT_USER_ACCESS_CODE;
  if (saved?.thermostat) {
    const defaults = clone(state.thermostat);
    const savedThermostat = saved.thermostat || {};
    const legacyTarget = Number(savedThermostat.autoOutdoorTarget);
    const legacyDifferential = Number(savedThermostat.autoOutdoorDifferential);
    state.thermostat = {
      ...defaults,
      ...savedThermostat,
      safetyLow: Number.isFinite(Number(savedThermostat.safetyLow))
        ? Number(savedThermostat.safetyLow)
        : (Number.isFinite(Number(savedThermostat.awayHeat)) ? Number(savedThermostat.awayHeat) : defaults.safetyLow),
      safetyHigh: Number.isFinite(Number(savedThermostat.safetyHigh))
        ? Number(savedThermostat.safetyHigh)
        : (Number.isFinite(Number(savedThermostat.awayCool)) ? Number(savedThermostat.awayCool) : defaults.safetyHigh),
      autoCoolOutdoorTarget: Number.isFinite(Number(savedThermostat.autoCoolOutdoorTarget))
        ? Number(savedThermostat.autoCoolOutdoorTarget)
        : (Number.isFinite(legacyTarget) ? legacyTarget : defaults.autoCoolOutdoorTarget),
      autoHeatOutdoorTarget: Number.isFinite(Number(savedThermostat.autoHeatOutdoorTarget))
        ? Number(savedThermostat.autoHeatOutdoorTarget)
        : (Number.isFinite(legacyTarget) && Number.isFinite(legacyDifferential) ? legacyTarget - legacyDifferential : defaults.autoHeatOutdoorTarget),
      manualChangeoverLockoutMinutes: Number.isFinite(Number(savedThermostat.manualChangeoverLockoutMinutes))
        ? clamp(Number(savedThermostat.manualChangeoverLockoutMinutes), 1, 60)
        : defaults.manualChangeoverLockoutMinutes,
      coolFanRemainOnMinutes: Number.isFinite(Number(savedThermostat.coolFanRemainOnMinutes))
        ? Number(savedThermostat.coolFanRemainOnMinutes)
        : defaults.coolFanRemainOnMinutes,
      outdoorWindSpeed: Number.isFinite(Number(savedThermostat.outdoorWindSpeed))
        ? Number(savedThermostat.outdoorWindSpeed)
        : defaults.outdoorWindSpeed,
      outdoorWindUnit: String(savedThermostat.outdoorWindUnit || defaults.outdoorWindUnit || "mph"),
      heatLocked: Boolean(savedThermostat.heatLocked),
      coolLocked: Boolean(savedThermostat.coolLocked),
      people: normalizeThermostatPeople(savedThermostat.people || defaults.people),
      schedules: normalizeThermostatSchedules(savedThermostat.schedules || defaults.schedules),
      pauseFunction: normalizePauseFunction({ ...(defaults.pauseFunction || {}), ...(savedThermostat.pauseFunction || {}) }),
      away: Boolean(savedThermostat.away),
      awaySource: normalizeAwaySource(savedThermostat.awaySource),
      manualAwayPresenceLatch: normalizeManualAwayPresenceLatch(savedThermostat.manualAwayPresenceLatch),
      autoSwitchNotice: normalizeAutoSwitchNotice(savedThermostat.autoSwitchNotice || defaults.autoSwitchNotice),
      autoSwitchHold: normalizeAutoSwitchHold(savedThermostat.autoSwitchHold || defaults.autoSwitchHold),
      limits: {
        ...defaults.limits,
        ...(savedThermostat.limits || {}),
      },
      autoPendingMode: "",
      autoLockoutUntil: 0,
      manualPendingMode: "",
      manualLockoutUntil: 0,
      lastHeatRunAt: 0,
      lastCoolRunAt: 0,
      equipmentLastHeatRunAt: 0,
      equipmentLastCoolRunAt: 0,
      coolRelayWasOn: false,
      coolFanHoldUntil: 0,
    };
    state.thermostat.pauseFunction.active = false;
    state.thermostat.pauseFunction.pausedAt = 0;
    state.thermostat.pauseFunction.previousTargetTemp = null;
    state.thermostat.pauseFunction.previousLastComfortTarget = null;
    state.thermostat.pauseFunction.activeEntityIds = [];
    state.thermostat.currentTempSource = String(state.thermostat.currentTempSource || "virtual");
    state.thermostat.currentTempSourceName = String(state.thermostat.currentTempSourceName || "Virtual Temp");
    state.thermostat.safetyLow = clamp(Math.round(Number(state.thermostat.safetyLow) || 55), ABS_MIN, ABS_MAX - 2);
    state.thermostat.safetyHigh = clamp(Math.round(Number(state.thermostat.safetyHigh) || 85), state.thermostat.safetyLow + 2, ABS_MAX);
    state.thermostat.autoHeatOutdoorTarget = Math.min(state.thermostat.autoHeatOutdoorTarget, state.thermostat.autoCoolOutdoorTarget - 1);
    normalizeThermostatModeForLocks();
  }
  if (saved?.alarm) {
    const savedCode = String(saved.alarm.disarmCode || "").replace(/\D/g, "").slice(0, 8);
    if (savedCode) state.alarm.disarmCode = savedCode;
  }
  if (saved?.blinds?.rooms) state.blinds = saved.blinds;
  if (saved?.lights?.rooms) {
    state.lights = { ...clone(defaultLightConfig), ...saved.lights, rooms: saved.lights.rooms };
    Object.values(state.lights.rooms || {}).forEach((room) => {
      room.lights = Array.isArray(room.lights) && room.lights.length ? room.lights : [createLight(state.lights.room || "room", 1)];
      room.lights.forEach((light, index) => {
        light.id = light.id || `light-${Date.now().toString(36)}-${index + 1}`;
        light.name = light.name || `Light ${index + 1}`;
        light.brightness = clamp(Number(light.brightness ?? 80), 0, 100);
        light.on = light.on !== false && light.brightness > 0;
        light.color = normalizeHexColor(light.color);
        light.colorSupported = Boolean(light.colorSupported);
        light.haEntityId = light.haEntityId || "";
        light.haName = light.haName || "";
      });
    });
    getActiveLightRoom();
  }
  if (saved?.roomControl?.rooms) {
    state.roomControl = { ...clone(defaultRoomControlConfig), ...saved.roomControl, rooms: saved.roomControl.rooms };
    Object.entries(state.roomControl.rooms || {}).forEach(([roomKey, room]) => {
      normalizeRoomControlRoom(room, roomKey || state.roomControl.room || "room");
    });
    getActiveRoomControlRoom();
  }
  if (saved?.integrations?.homeAssistant) {
    state.integrations.homeAssistant = {
      ...clone(defaultIntegrations.homeAssistant),
      ...saved.integrations.homeAssistant,
    };
  }
}

function readLegacySavedConfig() {
  try {
    const raw = localStorage.getItem(LEGACY_CONFIG_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (error) {
    console.warn("Unable to read legacy local config", error);
    return null;
  }
}

async function postConfigToServer(config) {
  const response = await fetch(CONFIG_API_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({ config }),
  });
  if (!response.ok) throw new Error(`Config save failed (${response.status})`);
  return response.json();
}

async function loadSavedConfig() {
  try {
    const response = await fetch(`${CONFIG_API_ENDPOINT}?_=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Config load failed (${response.status})`);
    const payload = await response.json();
    if (payload?.exists && payload?.config && typeof payload.config === "object" && Object.keys(payload.config).length) {
      applySavedConfig(payload.config);
      try { localStorage.removeItem(LEGACY_CONFIG_STORAGE_KEY); } catch (_) {}
      return;
    }

    const legacySaved = readLegacySavedConfig();
    if (legacySaved && typeof legacySaved === "object") {
      applySavedConfig(legacySaved);
      try {
        await postConfigToServer(buildSavedConfig());
        localStorage.removeItem(LEGACY_CONFIG_STORAGE_KEY);
      } catch (migrationError) {
        console.warn("Unable to migrate legacy config to panel", migrationError);
      }
    }
  } catch (error) {
    console.warn("Unable to load saved config from panel", error);
    const legacySaved = readLegacySavedConfig();
    if (legacySaved && typeof legacySaved === "object") {
      applySavedConfig(legacySaved);
    }
  }
}

async function flushConfigSave() {
  configSaveTimer = null;
  if (configSaveInFlight) {
    configSaveQueued = true;
    return;
  }
  configSaveInFlight = true;
  const shouldToast = configQueuedToast;
  configQueuedToast = false;
  try {
    await postConfigToServer(buildSavedConfig());
    try { localStorage.removeItem(LEGACY_CONFIG_STORAGE_KEY); } catch (error) { /* ignore */ }
    if (shouldToast) showToast("Config saved");
  } catch (error) {
    console.warn("Unable to save config to panel", error);
    if (shouldToast) showToast("Save failed");
  } finally {
    configSaveInFlight = false;
    if (configSaveQueued) {
      configSaveQueued = false;
      flushConfigSave();
    }
  }
}

function saveConfig(options = {}) {
  if (options.sync !== false) scheduleLocalThermostatPush();
  if (options.toast) configQueuedToast = true;
  configSaveQueued = true;
  clearTimeout(configSaveTimer);
  configSaveTimer = setTimeout(() => {
    if (!configSaveQueued) return;
    configSaveQueued = false;
    flushConfigSave();
  }, 650);
}

function localThermostatPayload() {
  const outputs = getThermostatOutputs({ recordRuntime: false });
  return {
    thermostat: {
      ...buildSavedConfig().thermostat,
      away: Boolean(state.thermostat.away),
      preset_mode: state.thermostat.away ? "away" : "home",
      presetMode: state.thermostat.away ? "away" : "home",
      heatLocked: Boolean(state.thermostat.heatLocked),
      coolLocked: Boolean(state.thermostat.coolLocked),
      people: normalizeThermostatPeople(state.thermostat.people),
      pauseFunction: pauseFunctionRuntimeSnapshot(),
      autoActiveMode: state.thermostat.autoActiveMode,
      autoPendingMode: state.thermostat.autoPendingMode,
      autoLockoutUntil: Number(state.thermostat.autoLockoutUntil || 0),
      autoSwitchNotice: normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice),
      autoSwitchHold: normalizeAutoSwitchHold(state.thermostat.autoSwitchHold),
      manualChangeoverLockoutMinutes: Number(state.thermostat.manualChangeoverLockoutMinutes || MANUAL_CHANGEOVER_LOCKOUT_MINUTES),
      manualPendingMode: state.thermostat.manualPendingMode,
      manualLockoutUntil: Number(state.thermostat.manualLockoutUntil || 0),
      lastHeatRunAt: Number(state.thermostat.lastHeatRunAt || 0),
      lastCoolRunAt: Number(state.thermostat.lastCoolRunAt || 0),
      equipmentLastHeatRunAt: Number(state.thermostat.equipmentLastHeatRunAt || 0),
      equipmentLastCoolRunAt: Number(state.thermostat.equipmentLastCoolRunAt || 0),
      coolRelayWasOn: Boolean(state.thermostat.coolRelayWasOn),
      coolFanHoldUntil: Number(state.thermostat.coolFanHoldUntil || 0),
      hvac_modes: getAvailableHvacModes(),
      hvacModes: getAvailableHvacModes(),
      relays: { fan: outputs.fan, heat: outputs.heat, cool: outputs.cool },
      hvacAction: outputs.cool ? "cooling" : outputs.heat ? "heating" : outputs.coolingFanHold || outputs.fan ? "fan" : "idle",
    },
  };
}

function getHomeAssistantWeatherEntityId() {
  const ha = state.integrations.homeAssistant || {};
  const configured = ha.weatherEntity;
  if (configured && typeof configured === "object" && configured.entityId) return String(configured.entityId).trim() || "weather.home";
  if (typeof configured === "string" && configured.trim()) return configured.trim();
  if (ha.weatherEntityId) return String(ha.weatherEntityId).trim() || "weather.home";
  return "weather.home";
}

function isHomeAssistantWeatherEnabled() {
  const ha = state.integrations.homeAssistant || {};
  return Boolean(String(ha.url || "").trim() && String(ha.token || "").trim() && getHomeAssistantWeatherEntityId());
}

function normalizeWeatherWindUnit(value) {
  const text = String(value || "mph").trim();
  return text || "mph";
}

function applyHomeAssistantWeather(weather = {}) {
  if (!weather || typeof weather !== "object") return false;
  const t = state.thermostat;
  let changed = false;

  const temperature = Number(weather.temperature);
  if (Number.isFinite(temperature)) {
    const nextTemp = clamp(Number(temperature.toFixed(1)), -40, 130);
    if (Number(t.outdoorTemp) !== nextTemp) {
      t.outdoorTemp = nextTemp;
      changed = true;
    }
  }

  const windSpeed = Number(weather.windSpeed);
  if (Number.isFinite(windSpeed)) {
    const nextWind = clamp(Number(windSpeed.toFixed(1)), 0, 250);
    if (Number(t.outdoorWindSpeed || 0) !== nextWind) {
      t.outdoorWindSpeed = nextWind;
      changed = true;
    }
  }

  const windUnit = normalizeWeatherWindUnit(weather.windSpeedUnit);
  if ((t.outdoorWindUnit || "mph") !== windUnit) {
    t.outdoorWindUnit = windUnit;
    changed = true;
  }

  if (changed) {
    renderThermostat();
    scheduleLocalThermostatPush();
  }
  return changed;
}

async function pollHomeAssistantWeather(options = {}) {
  if (!options.force && document.visibilityState === "hidden") return;
  const ha = state.integrations.homeAssistant || {};
  if (!ha.url || !ha.token) return;
  const entityId = getHomeAssistantWeatherEntityId();
  if (!entityId) return;
  if (haWeatherSyncInFlight) return;
  haWeatherSyncInFlight = true;
  try {
    const response = await fetch("/api/ha/weather/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ url: ha.url, token: ha.token, entityId }),
    });
    if (!response.ok) throw new Error(`Weather sync failed (${response.status})`);
    const payload = await response.json();
    if (payload?.weather) applyHomeAssistantWeather(payload.weather);
    if (haWeatherSyncLastError) haWeatherSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haWeatherSyncLastError) {
      haWeatherSyncLastError = message;
      console.warn("Home Assistant weather sync paused", message);
    }
  } finally {
    haWeatherSyncInFlight = false;
  }
}

function getCurrentTempEntity() {
  const ha = state.integrations.homeAssistant || {};
  const configured = ha.currentTempEntity;
  if (configured && typeof configured === "object" && configured.entityId) return configured;
  if (typeof configured === "string" && configured.trim()) return { entityId: configured.trim(), name: configured.trim(), domain: "sensor" };
  return null;
}

function isVirtualTempOverrideActive(now = Date.now()) {
  return Boolean(virtualTempOverrideUntil && virtualTempOverrideUntil > now);
}

function getVirtualTempOverrideRemainingMs(now = Date.now()) {
  return Math.max(0, Number(virtualTempOverrideUntil || 0) - now);
}

function formatShortDuration(ms) {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }
  return `${seconds}s`;
}

function scheduleVirtualTempOverrideExpiry() {
  if (virtualTempOverrideTimer) window.clearTimeout(virtualTempOverrideTimer);
  const remaining = getVirtualTempOverrideRemainingMs();
  if (!remaining) {
    virtualTempOverrideTimer = null;
    return;
  }
  virtualTempOverrideTimer = window.setTimeout(() => {
    virtualTempOverrideTimer = null;
    if (isVirtualTempOverrideActive()) {
      scheduleVirtualTempOverrideExpiry();
      return;
    }
    virtualTempOverrideUntil = 0;
    renderThermostat();
    pollHomeAssistantCurrentTempSensor({ force: true });
  }, Math.min(remaining + 100, 30 * 1000));
}

function normalizeTemperatureUnit(value) {
  return String(value || "°F").trim();
}

function isCelsiusUnit(unit) {
  const text = normalizeTemperatureUnit(unit).toLowerCase();
  return ["°c", "c", "celsius"].includes(text);
}

function isKelvinUnit(unit) {
  const text = normalizeTemperatureUnit(unit).toLowerCase();
  return ["k", "kelvin"].includes(text);
}

function sensorTemperatureToFahrenheit(value, unit) {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return NaN;
  if (isCelsiusUnit(unit)) return (raw * 9 / 5) + 32;
  if (isKelvinUnit(unit)) return ((raw - 273.15) * 9 / 5) + 32;
  return raw;
}

function readTemperatureFromEntity(entity = {}) {
  if (!entity || typeof entity !== "object") return NaN;
  const unit = normalizeTemperatureUnit(entity.unitOfMeasurement || entity.unit_of_measurement);
  return sensorTemperatureToFahrenheit(entity.state, unit);
}

function isLikelyTemperatureSensor(entity = {}) {
  if (!entity || entity.domain !== "sensor") return false;
  const deviceClass = String(entity.deviceClass || "").toLowerCase();
  const unit = normalizeTemperatureUnit(entity.unitOfMeasurement || "").toLowerCase();
  const label = `${entity.name || ""} ${entity.entityId || ""}`.toLowerCase();
  return deviceClass === "temperature" || ["°f", "f", "fahrenheit", "°c", "c", "celsius", "k", "kelvin"].includes(unit) || label.includes("temp");
}

function applyCurrentTempSensorEntity(entity = {}, options = {}) {
  const temp = readTemperatureFromEntity(entity);
  if (!Number.isFinite(temp)) return false;
  if (isVirtualTempOverrideActive() && options.ignoreOverride !== true) return false;
  const t = state.thermostat;
  const next = clamp(Number(temp.toFixed(1)), ABS_MIN, ABS_MAX);
  let changed = false;
  if (Number(t.currentTemp) !== next) {
    t.currentTemp = next;
    changed = true;
  }
  t.currentTempSource = "home-assistant";
  t.currentTempSourceName = entity.name || entity.entityId || "Home Assistant Sensor";
  const ha = state.integrations.homeAssistant || {};
  if (ha.currentTempEntity?.entityId === entity.entityId) {
    ha.currentTempEntity = { ...ha.currentTempEntity, ...entity, state: entity.state };
  }
  if (changed) {
    applyAutoSwitch({ notify: true });
    if (t.away) applyAwayTarget();
    renderThermostat();
    scheduleLocalThermostatPush();
  } else {
    renderCurrentTempSourceSettings();
  }
  return changed;
}

async function fetchCurrentTempSensorStateViaLocalBackend(entityId) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token || !entityId) throw new Error("Missing Home Assistant temperature sensor config");
  const payload = await fetchJsonWithTimeout("/api/ha/room/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds: [entityId] }),
  }, 9000);
  return (payload.controls || [])[0] || null;
}

async function pollHomeAssistantCurrentTempSensor(options = {}) {
  if (!options.force && document.visibilityState === "hidden") return;
  const entity = getCurrentTempEntity();
  if (!entity?.entityId) {
    renderCurrentTempSourceSettings();
    return;
  }
  if (isVirtualTempOverrideActive() && !options.ignoreOverride) {
    renderCurrentTempSourceSettings();
    return;
  }
  if (haTempSensorSyncInFlight) return;
  haTempSensorSyncInFlight = true;
  try {
    const update = await fetchCurrentTempSensorStateViaLocalBackend(entity.entityId);
    if (update) applyCurrentTempSensorEntity(update, { ignoreOverride: options.ignoreOverride === true });
    if (haTempSensorSyncLastError) haTempSensorSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haTempSensorSyncLastError) {
      haTempSensorSyncLastError = message;
      addHaLog("warn", "Current temperature sensor sync paused", message);
    }
  } finally {
    haTempSensorSyncInFlight = false;
  }
}

function renderCurrentTempSourceSettings() {
  const entity = getCurrentTempEntity();
  const overrideActive = isVirtualTempOverrideActive();
  const remaining = getVirtualTempOverrideRemainingMs();
  if (elements.currentTempSourceName) elements.currentTempSourceName.textContent = entity?.name || "Virtual Temp";
  if (elements.currentTempSourceId) elements.currentTempSourceId.textContent = entity?.entityId || "No Home Assistant sensor selected";
  if (elements.currentTempSourceStatus) {
    elements.currentTempSourceStatus.textContent = overrideActive
      ? `Virtual override active for ${formatShortDuration(remaining)}`
      : entity?.entityId
        ? "Using Home Assistant for current room temperature"
        : "Using the virtual temp slider until a sensor is selected";
  }
  if (elements.virtualTempOverrideStatus) {
    elements.virtualTempOverrideStatus.textContent = overrideActive
      ? `Manual override ${formatShortDuration(remaining)}`
      : entity?.entityId
        ? "Slider overrides for 2 min"
        : "Manual test source";
    elements.virtualTempOverrideStatus.classList.toggle("active", overrideActive);
  }
  if (elements.clearCurrentTempSensorButton) {
    elements.clearCurrentTempSensorButton.hidden = !entity?.entityId;
  }
}

function assignCurrentTempSensor(entity = {}) {
  if (!entity?.entityId) return;
  const ha = state.integrations.homeAssistant;
  ha.currentTempEntity = { ...entity };
  if (!Array.isArray(ha.currentTempAvailableEntities)) ha.currentTempAvailableEntities = [];
  const existing = ha.currentTempAvailableEntities.filter((item) => item.entityId !== entity.entityId);
  ha.currentTempAvailableEntities = [entity, ...existing].slice(0, 80);
  state.thermostat.currentTempSource = "home-assistant";
  state.thermostat.currentTempSourceName = entity.name || entity.entityId;
  virtualTempOverrideUntil = 0;
  scheduleVirtualTempOverrideExpiry();
  closeAudioEntityPicker();
  applyCurrentTempSensorEntity(entity, { ignoreOverride: true });
  renderThermostat();
  saveConfig({ toast: true });
  pollHomeAssistantCurrentTempSensor({ force: true, ignoreOverride: true });
  showToast("Current temp sensor selected");
}

function clearCurrentTempSensor() {
  const ha = state.integrations.homeAssistant;
  ha.currentTempEntity = null;
  state.thermostat.currentTempSource = "virtual";
  state.thermostat.currentTempSourceName = "Virtual Temp";
  virtualTempOverrideUntil = 0;
  scheduleVirtualTempOverrideExpiry();
  renderThermostat();
  saveConfig({ toast: true });
  showToast("Using virtual temp slider");
}

function applyLocalThermostatState(remote = {}) {
  const source = remote.thermostat || remote;
  if (!source || typeof source !== "object") return false;
  const t = state.thermostat;
  let changed = false;

  const setNumber = (key, min = -Infinity, max = Infinity) => {
    const raw = source[key];
    if (raw === undefined || raw === null || raw === "") return;
    const next = clamp(Number(raw), min, max);
    if (!Number.isFinite(next) || Number(t[key]) === next) return;
    t[key] = next;
    changed = true;
  };

  const setString = (key, allowed = null) => {
    const raw = source[key];
    if (raw === undefined || raw === null) return;
    const next = String(raw).trim();
    if (!next || (allowed && !allowed.includes(next)) || t[key] === next) return;
    t[key] = next;
    changed = true;
  };

  const setBoolean = (key) => {
    if (source[key] === undefined || source[key] === null) return;
    const next = Boolean(source[key]);
    if (Boolean(t[key]) === next) return;
    t[key] = next;
    changed = true;
  };

  const beforeName = t.name;
  setString("name");
  if (t.name !== beforeName) state.systemInfo.thermostatName = getThermostatName();
  setNumber("currentTemp", ABS_MIN, ABS_MAX);
  setNumber("targetTemp", ABS_MIN, ABS_MAX);
  setNumber("lastComfortTarget", ABS_MIN, ABS_MAX);
  setNumber("awayHeat", 45, 72);
  setNumber("awayCool", 72, 95);
  setNumber("safetyLow", ABS_MIN, ABS_MAX - 2);
  setNumber("safetyHigh", ABS_MIN + 2, ABS_MAX);
  setNumber("humidity", 0, 100);
  if (!isHomeAssistantWeatherEnabled()) setNumber("outdoorTemp", -40, 130);
  if (!isHomeAssistantWeatherEnabled()) setNumber("outdoorWindSpeed", 0, 250);
  if (!isHomeAssistantWeatherEnabled() && (source.outdoorWindUnit !== undefined || source.outdoor_wind_unit !== undefined)) {
    const windUnit = normalizeWeatherWindUnit(source.outdoorWindUnit || source.outdoor_wind_unit);
    if ((t.outdoorWindUnit || "mph") !== windUnit) { t.outdoorWindUnit = windUnit; changed = true; }
  }
  setNumber("autoCoolOutdoorTarget", 41, 100);
  setNumber("autoHeatOutdoorTarget", 40, 99);
  setNumber("autoChangeoverLockoutMinutes", AUTO_CHANGEOVER_MINUTES, 720);
  setNumber("manualChangeoverLockoutMinutes", MANUAL_CHANGEOVER_LOCKOUT_MINUTES, 60);
  setNumber("coolFanRemainOnMinutes", 0, 10);
  setNumber("autoLockoutUntil", 0, Number.MAX_SAFE_INTEGER);
  setNumber("manualLockoutUntil", 0, Number.MAX_SAFE_INTEGER);
  setNumber("lastHeatRunAt", 0, Number.MAX_SAFE_INTEGER);
  setNumber("lastCoolRunAt", 0, Number.MAX_SAFE_INTEGER);
  setNumber("equipmentLastHeatRunAt", 0, Number.MAX_SAFE_INTEGER);
  setNumber("equipmentLastCoolRunAt", 0, Number.MAX_SAFE_INTEGER);
  setNumber("coolFanHoldUntil", 0, Number.MAX_SAFE_INTEGER);
  if (source.autoSwitchNotice !== undefined) {
    const nextNotice = normalizeAutoSwitchNotice(source.autoSwitchNotice);
    if (JSON.stringify(t.autoSwitchNotice || {}) !== JSON.stringify(nextNotice)) {
      t.autoSwitchNotice = nextNotice;
      changed = true;
    }
  }
  if (source.autoSwitchHold !== undefined) {
    const nextHold = normalizeAutoSwitchHold(source.autoSwitchHold);
    if (JSON.stringify(t.autoSwitchHold || {}) !== JSON.stringify(nextHold)) {
      t.autoSwitchHold = nextHold;
      changed = true;
    }
  }
  setBoolean("heatLocked");
  setBoolean("coolLocked");
  if (Array.isArray(source.people)) {
    const nextPeople = normalizeThermostatPeople(source.people);
    if (JSON.stringify(normalizeThermostatPeople(t.people)) !== JSON.stringify(nextPeople)) {
      t.people = nextPeople;
      changed = true;
    }
  }
  if (source.awaySource !== undefined) {
    const nextSource = normalizeAwaySource(source.awaySource);
    if (normalizeAwaySource(t.awaySource) !== nextSource) { t.awaySource = nextSource; changed = true; }
  }
  if (source.manualAwayPresenceLatch !== undefined) {
    const nextLatch = normalizeManualAwayPresenceLatch(source.manualAwayPresenceLatch);
    if (JSON.stringify(t.manualAwayPresenceLatch || null) !== JSON.stringify(nextLatch)) {
      t.manualAwayPresenceLatch = nextLatch;
      changed = true;
    }
  }

  const incomingMode = source.hvac_mode || source.hvacMode || source.mode;
  if (incomingMode !== undefined) {
    const modeMap = { heat_cool: "auto", auto: "auto", cool: "cool", heat: "heat" };
    const next = modeMap[String(incomingMode).toLowerCase()] || "";
    const unlocked = getAllowedThermostatMode(next, t.mode);
    if (unlocked && t.mode !== unlocked) { t.mode = unlocked; changed = true; }
  }

  const incomingFan = source.fan_mode || source.fanMode || source.fan;
  if (incomingFan !== undefined) {
    const next = String(incomingFan).toLowerCase();
    if (["off", "on", "auto"].includes(next) && t.fan !== next) { t.fan = next; changed = true; }
  }

  if (source.autoPendingMode !== undefined) {
    const pending = normalizePendingMode(source.autoPendingMode);
    if (t.autoPendingMode !== pending) { t.autoPendingMode = pending; changed = true; }
  }
  if (source.manualPendingMode !== undefined) {
    const pending = normalizePendingMode(source.manualPendingMode);
    if (t.manualPendingMode !== pending) { t.manualPendingMode = pending; changed = true; }
  }

  if (source.preset_mode !== undefined || source.presetMode !== undefined || source.away !== undefined) {
    const preset = String(source.preset_mode ?? source.presetMode ?? "").toLowerCase();
    const nextAway = source.away !== undefined ? Boolean(source.away) : preset === "away";
    if (t.away !== nextAway) {
      t.away = nextAway;
      if (!nextAway) clearManualAwayPresenceLatch();
      else if (!normalizeAwaySource(t.awaySource)) t.awaySource = "manual";
      changed = true;
    }
  }

  if (source.limits && typeof source.limits === "object") {
    ["cool", "heat", "auto"].forEach((mode) => {
      const incoming = source.limits[mode] || {};
      if (!t.limits[mode]) t.limits[mode] = {};
      ["min", "max"].forEach((bound) => {
        const raw = incoming[bound];
        if (raw === undefined || raw === null || raw === "") return;
        const next = clamp(Number(raw), ABS_MIN, ABS_MAX);
        if (!Number.isFinite(next) || t.limits[mode][bound] === next) return;
        t.limits[mode][bound] = next;
        changed = true;
      });
    });
  }

  t.safetyLow = clamp(Math.round(Number(t.safetyLow) || 55), ABS_MIN, ABS_MAX - 2);
  t.safetyHigh = clamp(Math.round(Number(t.safetyHigh) || 85), t.safetyLow + 2, ABS_MAX);
  t.autoHeatOutdoorTarget = Math.min(t.autoHeatOutdoorTarget, t.autoCoolOutdoorTarget - 1);
  if (normalizeThermostatModeForLocks()) changed = true;
  if (applyAutoSwitch({ notify: true })) changed = true;
  if (t.away) applyAwayTarget();
  if (!t.away) {
    const { min, max } = getModeLimits();
    t.targetTemp = clamp(t.targetTemp, min, max);
    t.lastComfortTarget = clamp(t.lastComfortTarget, min, max);
  }
  if (t.mode === "auto") getAutoControlMode();
  return changed;
}

async function fetchLocalThermostatStatus(options = {}) {
  if (!options.force && document.visibilityState === "hidden") return;
  if (localThermostatPushTimer || Date.now() - lastLocalThermostatPushAt < 1200) return;
  if (localThermostatSyncInFlight || localThermostatPushInFlight) return;
  localThermostatSyncInFlight = true;
  try {
    const response = await fetch(`/api/thermostat/status?_=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Local thermostat returned ${response.status}`);
    const payload = await response.json();
    if (applyLocalThermostatState(payload)) {
      renderThermostat();
      saveConfig({ sync: false });
    }
    if (localThermostatLastError) localThermostatLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== localThermostatLastError) {
      localThermostatLastError = message;
      console.warn("Local thermostat sync paused", message);
    }
  } finally {
    localThermostatSyncInFlight = false;
  }
}

function systemAddressWithPort() {
  const rawAddress = String(state.systemInfo.address || "").trim();
  if (rawAddress) return rawAddress;

  const host = String(state.systemInfo.ipAddress || window.location.hostname || "").trim();
  if (!host) return "Unavailable";

  const rawPort = String(state.systemInfo.port || window.location.port || "").trim();
  return rawPort ? `${host}:${rawPort}` : host;
}

function renderSystemInfo() {
  const ip = systemAddressWithPort();
  const version = state.systemInfo.version || "Unavailable";
  const name = state.systemInfo.thermostatName || getThermostatName();
  if (elements.unitNameValue) elements.unitNameValue.textContent = name;
  const uptime = state.systemInfo.uptime || state.systemInfo.systemUptime || "Unavailable";
  const thermal = state.systemInfo.thermal || "Unavailable";
  if (elements.unitIpValue) elements.unitIpValue.textContent = ip;
  if (elements.unitVersionValue) elements.unitVersionValue.textContent = version;
  if (elements.unitUptimeValue) elements.unitUptimeValue.textContent = uptime;
  if (elements.unitThermalValue) elements.unitThermalValue.textContent = thermal;
}

async function fetchSystemInfo() {
  try {
    const response = await fetch(`/api/system/info?_=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`System info returned ${response.status}`);
    const payload = await response.json();
    state.systemInfo = {
      ipAddress: payload.ipAddress || payload.ip || window.location.hostname || "",
      port: payload.port || window.location.port || "",
      address: payload.address || payload.hostWithPort || "",
      version: payload.version || "",
      host: payload.host || "",
      thermostatName: payload.thermostatName || payload.name || getThermostatName(),
      uptime: payload.uptime || payload.systemUptime || "",
      systemUptime: payload.systemUptime || "",
      appUptime: payload.appUptime || "",
      thermal: payload.thermal || "",
      cpuTempC: payload.cpuTempC,
      cpuTempF: payload.cpuTempF,
      throttled: payload.throttled || "",
    };
  } catch (error) {
    state.systemInfo = {
      ...state.systemInfo,
      ipAddress: state.systemInfo.ipAddress || window.location.hostname || "Unavailable",
      port: state.systemInfo.port || window.location.port || "",
      address: state.systemInfo.address || "",
      version: state.systemInfo.version || "Unavailable",
      thermostatName: state.systemInfo.thermostatName || getThermostatName(),
      uptime: state.systemInfo.uptime || "Unavailable",
      systemUptime: state.systemInfo.systemUptime || "",
      appUptime: state.systemInfo.appUptime || "",
      thermal: state.systemInfo.thermal || "Unavailable",
      cpuTempC: state.systemInfo.cpuTempC,
      cpuTempF: state.systemInfo.cpuTempF,
      throttled: state.systemInfo.throttled || "",
    };
    console.warn("Unable to load system info", error);
  } finally {
    renderSystemInfo();
  }
}

function setFetchUpdateStatus(message = "", level = "info") {
  if (!elements.fetchUpdateStatus) return;
  elements.fetchUpdateStatus.textContent = message;
  elements.fetchUpdateStatus.dataset.level = level;
}

async function fetchPanelUpdate() {
  if (!elements.fetchUpdateButton) return;
  elements.fetchUpdateButton.disabled = true;
  setFetchUpdateStatus("Checking Development…", "info");
  try {
    const response = await fetch(`/api/system/fetch-update?_=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Update returned ${response.status}`);
    }
    const message = payload.message || (payload.updated ? "Update installed. Restarting…" : "Already up to date.");
    setFetchUpdateStatus(message, payload.updated ? "success" : "info");
    showToast(payload.updated ? "Update installed" : "Already up to date");
    if (payload.version) {
      state.systemInfo.version = payload.version;
      renderSystemInfo();
    }
  } catch (error) {
    const message = error.message || String(error);
    setFetchUpdateStatus(message, "error");
    showToast("Update failed");
  } finally {
    setTimeout(() => {
      if (elements.fetchUpdateButton) elements.fetchUpdateButton.disabled = false;
    }, 2000);
  }
}

async function restartPanelServer() {
  if (!elements.restartServerButton) return;
  const confirmed = window.confirm("Restart the LivingroomClimate server now?");
  if (!confirmed) return;
  elements.restartServerButton.disabled = true;
  if (elements.fetchUpdateButton) elements.fetchUpdateButton.disabled = true;
  setFetchUpdateStatus("Restarting server…", "info");
  try {
    const response = await fetch(`/api/system/reboot?_=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Restart returned ${response.status}`);
    }
    setFetchUpdateStatus(payload.message || "Restart command sent. The panel will go offline briefly.", "success");
    showToast("Restart command sent");
  } catch (error) {
    const message = error.message || String(error);
    setFetchUpdateStatus(message, "error");
    showToast("Restart failed");
    elements.restartServerButton.disabled = false;
    if (elements.fetchUpdateButton) elements.fetchUpdateButton.disabled = false;
  }
}

function openThermostatInfo() {
  state.systemInfo = { ...state.systemInfo, thermostatName: state.systemInfo.thermostatName || getThermostatName(), ipAddress: state.systemInfo.ipAddress || "Loading…", version: state.systemInfo.version || "Loading…", uptime: state.systemInfo.uptime || "Loading…" };
  renderSystemInfo();
  setOverlayOpen(elements.thermostatInfoOverlay, true);
  fetchSystemInfo();
}

function closeThermostatInfo() {
  setOverlayOpen(elements.thermostatInfoOverlay, false);
}

function applyHardwareStatusPayload(payload = {}) {
  if (!payload || typeof payload !== "object") return;
  state.hardware.loaded = true;
  state.hardware.gpio = { ...state.hardware.gpio, ...(payload.gpio || {}) };
  state.hardware.manual = { ...state.hardware.manual, ...(payload.manual || {}) };
  state.hardware.relays = { ...state.hardware.relays, ...(payload.relays || {}) };
  state.hardware.rgb = { ...state.hardware.rgb, ...(payload.rgb || {}) };
  state.hardware.i2c = { ...state.hardware.i2c, ...(payload.i2c || {}) };
}

function isHardwareViewOpen() {
  return Boolean(elements.hardwareSettingsView && !elements.hardwareSettingsView.hidden && elements.settingsOverlay?.classList.contains("open"));
}

function formatHardwareScanTime(value) {
  const numeric = Number(value || 0);
  if (!numeric) return "Not scanned yet";
  const date = new Date(numeric * 1000);
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

function renderHardwareStatus() {
  const hw = state.hardware;
  const gpio = hw.gpio || {};
  const gpioReady = Boolean(gpio.available);
  if (elements.hardwareBackendStatus) {
    elements.hardwareBackendStatus.textContent = gpioReady ? `${gpio.backend || "GPIO"} Ready` : `${gpio.backend || "GPIO"} Simulated`;
    elements.hardwareBackendStatus.dataset.ready = gpioReady ? "1" : "0";
  }
  const manualActive = Boolean(hw.manual?.active);
  if (elements.hardwareManualStatus) {
    const source = gpio.source === "manual" || manualActive ? "Manual relay testing is active." : "Thermostat automation has relay control.";
    const error = gpio.error ? ` ${gpio.error}` : "";
    elements.hardwareManualStatus.textContent = `${source}${error}`;
    elements.hardwareManualStatus.dataset.active = manualActive ? "1" : "0";
  }
  if (elements.releaseHardwareManualButton) elements.releaseHardwareManualButton.disabled = !manualActive;

  (elements.hardwareRelayButtons || []).forEach((button) => {
    const relay = button.dataset.hardwareRelay;
    const relayInfo = hw.relays?.[relay] || {};
    const isOn = Boolean(relayInfo.on);
    button.classList.toggle("active", isOn);
    button.setAttribute("aria-pressed", String(isOn));
    const label = relayInfo.label || `${titleCase(relay)} Relay`;
    const gpioPin = relayInfo.gpio ?? "?";
    const physicalPin = relayInfo.physical ?? "?";
    button.innerHTML = `<strong>${escapeHtml(label)}</strong><span>${isOn ? "ON" : "OFF"} • GPIO ${escapeHtml(gpioPin)} / Pin ${escapeHtml(physicalPin)}</span>`;
    button.disabled = false;
  });

  const rgb = hw.rgb || {};
  const rgbOn = Boolean(rgb.on);
  if (elements.hardwareRgbPowerButton) {
    elements.hardwareRgbPowerButton.textContent = rgbOn ? "On" : "Off";
    elements.hardwareRgbPowerButton.classList.toggle("active", rgbOn);
    elements.hardwareRgbPowerButton.setAttribute("aria-pressed", String(rgbOn));
  }
  if (elements.hardwareRgbColorInput && rgb.color) elements.hardwareRgbColorInput.value = normalizeHexColor(rgb.color, "#35eaff");
  if (elements.hardwareRgbStatus) {
    const backend = rgb.backend || "RGB";
    const status = rgb.available ? "ready" : "simulated";
    const note = rgb.error ? ` • ${rgb.error}` : "";
    elements.hardwareRgbStatus.textContent = `${backend} ${status} • GPIO ${rgb.gpio ?? 26} / Pin ${rgb.physical ?? 37}${note}`;
    elements.hardwareRgbStatus.dataset.ready = rgb.available ? "1" : "0";
  }

  const i2c = hw.i2c || {};
  const addresses = Array.isArray(i2c.addresses) ? i2c.addresses : [];
  const i2cDisabled = i2c.enabled === false || i2c.backend === "disabled";
  const i2cNotice = i2c.notice || (i2cDisabled ? "I2C bus is not active yet. This is OK until sensors are installed." : "");
  if (elements.i2cAddressList) {
    if (addresses.length) {
      elements.i2cAddressList.innerHTML = addresses.map((address) => `<div class="i2c-address-pill">${escapeHtml(address)}</div>`).join("");
    } else {
      let emptyText = "No I2C sensors found yet";
      if (i2c.error) emptyText = "I2C scan unavailable";
      elements.i2cAddressList.innerHTML = `<div class="empty-state compact">${emptyText}</div>`;
    }
  }
  if (elements.i2cStatusLine) {
    const backend = i2c.backend || "scanner";
    const count = addresses.length;
    const devicePath = i2c.devicePath || `/dev/i2c-${i2c.bus ?? 1}`;
    let statusText = "";
    if (i2c.error) statusText = ` • ${i2c.error}`;
    else if (i2c.action) statusText = ` • ${i2c.action}`;
    else if (i2cNotice) statusText = ` • ${i2cNotice}`;
    else if (!addresses.length) statusText = " • Scan OK; no devices detected yet.";
    elements.i2cStatusLine.textContent = `${count} address${count === 1 ? "" : "es"} • Bus ${i2c.bus ?? 1} ${devicePath} • ${backend} • Last scan ${formatHardwareScanTime(i2c.scannedAt)}${statusText}`;
    elements.i2cStatusLine.dataset.error = i2c.error ? "1" : "0";
    elements.i2cStatusLine.dataset.note = !i2c.error && (i2cNotice || i2c.action) ? "1" : "0";
  }
}


function isHistoryViewOpen() {
  return Boolean(elements.historySettingsView && !elements.historySettingsView.hidden && elements.settingsOverlay?.classList.contains("open"));
}

function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseDateKey(dateKey) {
  const raw = String(dateKey || "").trim();
  const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return new Date();
  const year = Number(match[1]);
  const month = Number(match[2]) - 1;
  const day = Number(match[3]);
  return new Date(year, month, day, 12, 0, 0, 0);
}

function shiftDateKey(dateKey, offsetDays) {
  const date = parseDateKey(dateKey || state.history.selectedDate || state.history.today || localDateKey());
  date.setDate(date.getDate() + Number(offsetDays || 0));
  return localDateKey(date);
}

function formatHistoryDateTitle(dateKey, todayKey = state.history.today) {
  if (!dateKey) return "History";
  if (dateKey === todayKey) return "Today";
  const date = parseDateKey(dateKey);
  return date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
}

function formatHistoryTime(ms) {
  const value = Number(ms || 0);
  if (!value) return "--";
  return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatHistoryDuration(ms) {
  const totalMinutes = Math.max(0, Math.round(Number(ms || 0) / 60000));
  if (totalMinutes < 1) return Number(ms || 0) > 0 ? "<1 min" : "0 min";
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (!hours) return `${minutes} min`;
  return minutes ? `${hours} hr ${minutes} min` : `${hours} hr`;
}

function formatHistoryCycles(count, active = false) {
  const cycles = Math.max(0, Number(count || 0));
  return `${cycles} cycle${cycles === 1 ? "" : "s"}${active ? " • active now" : ""}`;
}

function applyHistoryPayload(payload = {}) {
  if (!payload || typeof payload !== "object") return;
  state.history.loaded = true;
  state.history.selectedDate = payload.date || state.history.selectedDate || localDateKey();
  state.history.today = payload.today || state.history.today || localDateKey();
  state.history.availableDates = Array.isArray(payload.availableDates) ? payload.availableDates : state.history.availableDates;
  state.history.summary = { ...state.history.summary, ...(payload.summary || {}) };
  state.history.periods = Array.isArray(payload.periods) ? payload.periods : [];
  state.history.updatedAtMs = Number(payload.updatedAtMs || 0);
  state.history.writePolicy = payload.writePolicy || state.history.writePolicy || "Active-day history is held in RAM and saved once per day.";
  state.history.error = "";
}

function renderHistoryStatus() {
  const history = state.history || {};
  const selected = history.selectedDate || history.today || localDateKey();
  if (elements.historyDateInput && elements.historyDateInput.value !== selected) {
    elements.historyDateInput.value = selected;
  }
  if (elements.historySelectedDateTitle) elements.historySelectedDateTitle.textContent = formatHistoryDateTitle(selected, history.today);
  if (elements.historyWritePolicy) elements.historyWritePolicy.textContent = history.writePolicy || "Active-day history is held in RAM and saved once per day.";
  if (elements.historyUpdatedPill) {
    const updated = Number(history.updatedAtMs || 0);
    elements.historyUpdatedPill.textContent = historyStatusInFlight ? "Loading…" : updated ? `Updated ${formatHistoryTime(updated)}` : "No data";
    elements.historyUpdatedPill.dataset.ready = history.error ? "0" : "1";
  }

  const summary = history.summary || {};
  const cool = summary.cool || {};
  const heat = summary.heat || {};
  const fan = summary.fan || {};
  if (elements.historyCoolTotal) elements.historyCoolTotal.textContent = formatHistoryDuration(cool.totalMs);
  if (elements.historyHeatTotal) elements.historyHeatTotal.textContent = formatHistoryDuration(heat.totalMs);
  if (elements.historyFanTotal) elements.historyFanTotal.textContent = formatHistoryDuration(fan.totalMs);
  if (elements.historyCoolCycles) elements.historyCoolCycles.textContent = formatHistoryCycles(cool.cycles, cool.active);
  if (elements.historyHeatCycles) elements.historyHeatCycles.textContent = formatHistoryCycles(heat.cycles, heat.active);
  if (elements.historyFanCycles) elements.historyFanCycles.textContent = formatHistoryCycles(fan.cycles, fan.active);

  if (elements.historyTimeline) {
    const periods = Array.isArray(history.periods) ? history.periods : [];
    const importantPeriods = periods.filter((period) => ["cool", "heat", "fan"].includes(String(period.relay || "")));
    if (history.error) {
      elements.historyTimeline.innerHTML = `<div class="empty-state compact">${escapeHtml(history.error)}</div>`;
    } else if (!importantPeriods.length) {
      elements.historyTimeline.innerHTML = `<div class="empty-state compact">No heat, AC, or fan runtime recorded for this date yet.</div>`;
    } else {
      elements.historyTimeline.innerHTML = importantPeriods.map((period) => {
        const relay = String(period.relay || "").toLowerCase();
        const label = relay === "cool" ? "AC" : titleCase(relay);
        const start = formatHistoryTime(period.startMs);
        const end = period.ongoing ? "Now" : formatHistoryTime(period.endMs);
        const duration = formatHistoryDuration(period.durationMs);
        const source = period.source && period.source !== "thermostat" ? ` • ${escapeHtml(period.source)}` : "";
        return `<div class="history-event ${escapeHtml(relay)}"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(start)} – ${escapeHtml(end)}</span><em>${escapeHtml(duration)}${source}${period.ongoing ? " • running" : ""}</em></div>`;
      }).join("");
    }
  }
}

async function fetchHistoryStatus(options = {}) {
  if (historyStatusInFlight) return;
  if (!options.force && !isHistoryViewOpen()) return;
  historyStatusInFlight = true;
  renderHistoryStatus();
  const date = options.date || state.history.selectedDate || state.history.today || localDateKey();
  try {
    const payload = await fetchJsonWithTimeout(`${HISTORY_API_ENDPOINT}?date=${encodeURIComponent(date)}&_=${Date.now()}`, { cache: "no-store" }, 7000);
    applyHistoryPayload(payload);
    historyLastError = "";
  } catch (error) {
    historyLastError = error.message || String(error);
    state.history.error = historyLastError;
    if (elements.historyUpdatedPill) {
      elements.historyUpdatedPill.textContent = "Unavailable";
      elements.historyUpdatedPill.dataset.ready = "0";
    }
    console.warn("Unable to load HVAC history", error);
  } finally {
    historyStatusInFlight = false;
    renderHistoryStatus();
  }
}

function setHistoryDate(dateKey, options = {}) {
  state.history.selectedDate = dateKey || localDateKey();
  renderHistoryStatus();
  fetchHistoryStatus({ force: true, date: state.history.selectedDate });
  if (options.toast) showToast("History date changed");
}

function showHistoryView() {
  if (!elements.historySettingsView) return;
  hideAllSettingsViews();
  elements.historySettingsView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "history-setup");
  elements.settingsTitle.textContent = "History";
  elements.settingsEyebrow.textContent = "Daily Runtime";
  elements.settingsFooter.hidden = true;
  if (!state.history.selectedDate) state.history.selectedDate = state.history.today || localDateKey();
  renderHistoryStatus();
  fetchHistoryStatus({ force: true });
}

async function fetchHardwareStatus(options = {}) {
  if (hardwareStatusInFlight) return;
  if (!options.force && !isHardwareViewOpen()) return;
  hardwareStatusInFlight = true;
  try {
    const payload = await fetchJsonWithTimeout(`/api/hardware/status?_=${Date.now()}`, { cache: "no-store" }, 9000);
    applyHardwareStatusPayload(payload);
    hardwareLastError = "";
  } catch (error) {
    hardwareLastError = error.message || String(error);
    if (elements.hardwareBackendStatus) {
      elements.hardwareBackendStatus.textContent = "Unavailable";
      elements.hardwareBackendStatus.dataset.ready = "0";
    }
    if (elements.hardwareManualStatus) elements.hardwareManualStatus.textContent = hardwareLastError;
    console.warn("Unable to load hardware status", error);
  } finally {
    hardwareStatusInFlight = false;
    renderHardwareStatus();
  }
}

async function sendHardwareRelayCommand(relay) {
  const relayInfo = state.hardware.relays?.[relay] || {};
  const nextOn = !Boolean(relayInfo.on);
  (elements.hardwareRelayButtons || []).forEach((button) => { button.disabled = true; });
  try {
    const payload = await fetchJsonWithTimeout("/api/hardware/relay", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ relay, on: nextOn }),
    }, 7000);
    applyHardwareStatusPayload(payload);
    showToast(`${titleCase(relay)} relay ${nextOn ? "on" : "off"}`);
  } catch (error) {
    showToast("Relay command failed");
    console.warn("Relay command failed", error);
  } finally {
    renderHardwareStatus();
  }
}

async function releaseHardwareManualControl() {
  if (!elements.releaseHardwareManualButton || elements.releaseHardwareManualButton.disabled) return;
  elements.releaseHardwareManualButton.disabled = true;
  try {
    const payload = await fetchJsonWithTimeout("/api/hardware/release", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }, 7000);
    applyHardwareStatusPayload(payload);
    showToast("Thermostat control restored");
  } catch (error) {
    showToast("Release failed");
    console.warn("Release hardware control failed", error);
  } finally {
    renderHardwareStatus();
  }
}

async function sendHardwareRgbCommand(options = {}) {
  const current = state.hardware.rgb || {};
  const color = normalizeHexColor(options.color || elements.hardwareRgbColorInput?.value || current.color || "#35eaff", "#35eaff");
  const on = typeof options.on === "boolean" ? options.on : !Boolean(current.on);
  if (elements.hardwareRgbPowerButton) elements.hardwareRgbPowerButton.disabled = true;
  try {
    const payload = await fetchJsonWithTimeout("/api/hardware/rgb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on, color }),
    }, 7000);
    applyHardwareStatusPayload(payload);
    showToast(`RGB ${on ? "on" : "off"}`);
  } catch (error) {
    showToast("RGB command failed");
    console.warn("RGB command failed", error);
  } finally {
    if (elements.hardwareRgbPowerButton) elements.hardwareRgbPowerButton.disabled = false;
    renderHardwareStatus();
  }
}

function showHardwareInfoView() {
  if (!elements.hardwareSettingsView) return;
  hideAllSettingsViews();
  elements.hardwareSettingsView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "hardware-setup");
  elements.settingsTitle.textContent = "Hardware Information";
  elements.settingsEyebrow.textContent = "Pinout & Manual Testing";
  elements.settingsFooter.hidden = true;
  renderHardwareStatus();
  fetchHardwareStatus({ force: true });
}

function showComfortSetupView() {
  if (elements.hardwareSettingsView) elements.hardwareSettingsView.hidden = true;
  if (elements.historySettingsView) elements.historySettingsView.hidden = true;
  if (elements.thermostatSettingsView) elements.thermostatSettingsView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "thermostat-setup");
  elements.settingsSheet.classList.remove("hardware-setup", "history-setup");
  elements.settingsTitle.textContent = "Comfort Setup";
  elements.settingsEyebrow.textContent = "Panel Settings";
  elements.settingsFooter.hidden = false;
  if (elements.alarmDisarmCodeInput) elements.alarmDisarmCodeInput.value = getSavedAlarmCode();
  if (elements.userAccessCodeInput) elements.userAccessCodeInput.value = getUserAccessCode();
  if (elements.thermostatNameInput) elements.thermostatNameInput.value = getThermostatName();
  renderScreenTimeoutSettings();
  renderThermostatPeople();
}

function scheduleLocalThermostatPush() {
  clearTimeout(localThermostatPushTimer);
  localThermostatPushTimer = setTimeout(pushLocalThermostatStatus, LOCAL_THERMOSTAT_PUSH_DEBOUNCE_MS);
}

async function pushLocalThermostatStatus() {
  localThermostatPushTimer = null;
  if (localThermostatPushInFlight) return;
  localThermostatPushInFlight = true;
  lastLocalThermostatPushAt = Date.now();
  try {
    await fetch("/api/thermostat/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(localThermostatPayload()),
    });
  } catch (error) {
    console.warn("Unable to save thermostat state to local backend", error);
  } finally {
    localThermostatPushInFlight = false;
  }
}

function updateClock() {
  const now = new Date();
  elements.clock.textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function markPageInactiveSyncBaseline(pageName) {
  const now = Date.now();
  if (pageName === "blinds") lastInactiveBlindSyncAt = now;
  if (pageName === "audio") lastInactiveAudioSyncAt = now;
  if (pageName === "lights") lastInactiveLightSyncAt = now;
  if (pageName === "room") lastInactiveRoomSyncAt = now;
  if (pageName === "thermostat") {
    lastInactiveAlarmSyncAt = now;
    lastInactiveDoorSyncAt = now;
  }
}

function isPanelLocked() {
  return Boolean(state.panelLock?.locked);
}

function renderPanelLock() {
  const locked = isPanelLocked();
  elements.app?.classList.toggle("panel-locked", locked);
  elements.app?.setAttribute("data-panel-locked", locked ? "true" : "false");
  if (elements.thermostatPanelLockButton) {
    elements.thermostatPanelLockButton.classList.toggle("locked", locked);
    elements.thermostatPanelLockButton.classList.toggle("unlocked", !locked);
    elements.thermostatPanelLockButton.setAttribute("aria-pressed", locked ? "true" : "false");
    elements.thermostatPanelLockButton.setAttribute("aria-label", locked ? "Unlock thermostat page navigation" : "Lock thermostat page navigation");
    elements.thermostatPanelLockButton.title = locked ? "Panel locked. Enter user access code to unlock." : "Lock panel navigation";
  }
  if (elements.thermostatPanelLockLabel) elements.thermostatPanelLockLabel.textContent = locked ? "Locked" : "Unlocked";
  document.querySelectorAll(".nav-pill").forEach((button) => {
    const blocked = locked && button.dataset.goto !== "thermostat";
    button.classList.toggle("panel-lock-blocked", blocked);
    button.setAttribute("aria-disabled", blocked ? "true" : "false");
  });
}

function setPanelLocked(locked, options = {}) {
  state.panelLock.locked = Boolean(locked);
  renderPanelLock();
  if (state.panelLock.locked && state.currentPage !== "thermostat") gotoPage("thermostat", { force: true, silent: true });
  if (options.save !== false) saveConfig({ sync: false });
  if (options.toast !== false) showToast(state.panelLock.locked ? "Panel locked" : "Panel unlocked");
}

function requestPanelUnlock() {
  openSettingsCodePrompt("panelUnlock");
}

function togglePanelLock() {
  if (isPanelLocked()) {
    requestPanelUnlock();
    return;
  }
  setPanelLocked(true);
}

function guardPanelLockedNavigation(targetPage, options = {}) {
  if (!isPanelLocked() || targetPage === "thermostat" || options.force) return false;
  if (!options.silent) showToast("Panel locked. Unlock to leave thermostat.");
  gotoPage("thermostat", { force: true, silent: true });
  return true;
}

function gotoPage(pageName, options = {}) {
  if (!state.pages.includes(pageName)) return;
  if (guardPanelLockedNavigation(pageName, options)) return;
  const previousPage = state.currentPage;
  state.currentPage = pageName;
  if (previousPage !== pageName) markPageInactiveSyncBaseline(previousPage);
  elements.app.dataset.page = pageName;
  const index = state.pages.indexOf(pageName);
  const pageWidth = 100 / state.pages.length;
  elements.screenTrack.style.transform = `translateX(-${index * pageWidth}%)`;
  document.querySelectorAll(".nav-pill").forEach((button) => button.classList.toggle("active", button.dataset.goto === pageName));
  if (elements.tempMiniStatus) elements.tempMiniStatus.hidden = pageName === "thermostat";
  renderPanelLock();
  if (pageName === "blinds") pollHomeAssistantLinkedCovers({ force: true });
  if (pageName === "audio") pollHomeAssistantMediaPlayer({ force: true, controls: true });
  if (pageName === "lights") pollHomeAssistantLights({ force: true });
  if (pageName === "room") pollHomeAssistantRoomControls({ force: true });
  if (pageName === "thermostat") {
    pollHomeAssistantAlarm({ force: true });
    pollHomeAssistantDoor({ force: true });
    pollHomeAssistantThermostatPeople({ force: true });
  }
}

function goRelative(direction) {
  const currentIndex = state.pages.indexOf(state.currentPage);
  gotoPage(state.pages[clamp(currentIndex + direction, 0, state.pages.length - 1)]);
}


function isThermostatModeLocked(mode) {
  const t = state.thermostat;
  if (mode === "heat") return Boolean(t.heatLocked);
  if (mode === "cool") return Boolean(t.coolLocked);
  return false;
}

function getAvailableHvacModes() {
  const modes = [];
  if (!state.thermostat.coolLocked) modes.push("cool");
  if (!state.thermostat.heatLocked) modes.push("heat");
  if (modes.length) modes.push("heat_cool");
  return modes;
}

function getAllowedThermostatMode(mode, fallback = state.thermostat.mode) {
  const requested = String(mode || "").toLowerCase() === "heat_cool" ? "auto" : String(mode || "").toLowerCase();
  const valid = ["cool", "heat", "auto"].includes(requested) ? requested : fallback;
  const coolAvailable = !state.thermostat.coolLocked;
  const heatAvailable = !state.thermostat.heatLocked;
  if (valid === "cool" && coolAvailable) return "cool";
  if (valid === "heat" && heatAvailable) return "heat";
  if (valid === "auto" && (coolAvailable || heatAvailable)) return "auto";
  if (fallback && fallback !== valid) {
    const fallbackMode = String(fallback).toLowerCase() === "heat_cool" ? "auto" : String(fallback).toLowerCase();
    if (fallbackMode === "cool" && coolAvailable) return "cool";
    if (fallbackMode === "heat" && heatAvailable) return "heat";
    if (fallbackMode === "auto" && (coolAvailable || heatAvailable)) return "auto";
  }
  if (coolAvailable) return "cool";
  if (heatAvailable) return "heat";
  return "auto";
}

function normalizeThermostatModeForLocks() {
  const t = state.thermostat;
  const beforeMode = t.mode;
  const beforeAuto = t.autoActiveMode;
  t.mode = getAllowedThermostatMode(t.mode, t.mode);
  if (t.autoActiveMode === "heat" && t.heatLocked) t.autoActiveMode = !t.coolLocked ? "cool" : "";
  if (t.autoActiveMode === "cool" && t.coolLocked) t.autoActiveMode = !t.heatLocked ? "heat" : "";
  if (t.autoPendingMode === "heat" && t.heatLocked) t.autoPendingMode = "";
  if (t.autoPendingMode === "cool" && t.coolLocked) t.autoPendingMode = "";
  if (t.heatLocked && t.coolLocked) {
    t.autoActiveMode = "";
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
    t.coolFanHoldUntil = 0;
    t.coolRelayWasOn = false;
  }
  return beforeMode !== t.mode || beforeAuto !== t.autoActiveMode;
}

function normalizeThermostatPersonEntry(entry = {}) {
  const entityId = String(entry.entityId || entry.entity_id || "").trim();
  if (!entityId) return null;
  const domain = entityId.includes(".") ? entityId.split(".", 1)[0] : "person";
  return {
    entityId,
    domain,
    name: String(entry.name || entry.friendly_name || entityId).trim() || entityId,
    state: String(entry.state || "unknown").trim() || "unknown",
    lastChanged: entry.lastChanged || entry.last_changed || "",
    lastUpdated: entry.lastUpdated || entry.last_updated || "",
  };
}

function normalizeThermostatPeople(people = []) {
  const seen = new Set();
  return (Array.isArray(people) ? people : [])
    .map(normalizeThermostatPersonEntry)
    .filter(Boolean)
    .filter((entry) => {
      if (seen.has(entry.entityId)) return false;
      seen.add(entry.entityId);
      return true;
    });
}


function pauseFunctionDomainFromEntityId(entityId = "") {
  return String(entityId || "").split(".", 1)[0] || "";
}

function normalizePauseFunctionDuration(value, fallback = DEFAULT_PAUSE_FUNCTION_MINUTES) {
  const next = Math.round(Number(value));
  if (!Number.isFinite(next)) return clamp(Math.round(Number(fallback) || DEFAULT_PAUSE_FUNCTION_MINUTES), MIN_PAUSE_FUNCTION_MINUTES, MAX_PAUSE_FUNCTION_MINUTES);
  return clamp(next, MIN_PAUSE_FUNCTION_MINUTES, MAX_PAUSE_FUNCTION_MINUTES);
}

function normalizePauseFunctionEntry(entry = {}) {
  const entityId = String(entry?.entityId || entry?.entity_id || "").trim();
  if (!entityId || !entityId.includes(".")) return null;
  const domain = String(entry.domain || pauseFunctionDomainFromEntityId(entityId)).trim().toLowerCase();
  const rawCurrentPosition = entry.currentPosition ?? entry.current_position;
  const currentPosition = rawCurrentPosition === undefined || rawCurrentPosition === null || rawCurrentPosition === "" ? null : Number(rawCurrentPosition);
  return {
    entityId,
    name: String(entry.name || entry.friendlyName || entry.haName || entry.friendly_name || entityId).trim() || entityId,
    domain,
    deviceClass: String(entry.deviceClass || entry.device_class || "").trim().toLowerCase(),
    state: String(entry.state || "unknown").trim().toLowerCase(),
    openedAt: Math.max(0, Number(entry.openedAt || 0)),
    lastChanged: String(entry.lastChanged || entry.last_changed || "").trim(),
    lastUpdated: String(entry.lastUpdated || entry.last_updated || "").trim(),
    currentPosition: Number.isFinite(currentPosition) ? currentPosition : null,
    isClosed: typeof entry.isClosed === "boolean" ? entry.isClosed : (typeof entry.is_closed === "boolean" ? entry.is_closed : null),
  };
}

function normalizePauseFunctionEntries(entries = []) {
  const seen = new Set();
  return (Array.isArray(entries) ? entries : [])
    .map(normalizePauseFunctionEntry)
    .filter(Boolean)
    .filter((entry) => {
      if (seen.has(entry.entityId)) return false;
      seen.add(entry.entityId);
      return true;
    });
}

function normalizePauseFunction(value = {}) {
  const source = value && typeof value === "object" ? value : {};
  return {
    durationMinutes: normalizePauseFunctionDuration(source.durationMinutes ?? source.minutes ?? source.delayMinutes),
    entries: normalizePauseFunctionEntries(source.entries || source.entities || []),
    active: Boolean(source.active),
    pausedAt: Math.max(0, Number(source.pausedAt || 0)),
    previousTargetTemp: Number.isFinite(Number(source.previousTargetTemp)) ? Number(source.previousTargetTemp) : null,
    previousLastComfortTarget: Number.isFinite(Number(source.previousLastComfortTarget)) ? Number(source.previousLastComfortTarget) : null,
    activeEntityIds: normalizePresenceEntityList(source.activeEntityIds || []),
  };
}

function getPauseFunction() {
  state.thermostat.pauseFunction = normalizePauseFunction(state.thermostat.pauseFunction || {});
  return state.thermostat.pauseFunction;
}

function pauseFunctionSettingsSnapshot() {
  const pause = getPauseFunction();
  return {
    durationMinutes: normalizePauseFunctionDuration(pause.durationMinutes),
    entries: normalizePauseFunctionEntries(pause.entries).map((entry) => ({
      entityId: entry.entityId,
      name: entry.name,
      domain: entry.domain,
      deviceClass: entry.deviceClass,
      state: entry.state,
      openedAt: Number(entry.openedAt || 0),
      lastChanged: entry.lastChanged || "",
      lastUpdated: entry.lastUpdated || "",
      currentPosition: entry.currentPosition,
      isClosed: entry.isClosed,
    })),
  };
}

function pauseFunctionRuntimeSnapshot() {
  const pause = getPauseFunction();
  return {
    ...pauseFunctionSettingsSnapshot(),
    active: Boolean(pause.active),
    pausedAt: Math.max(0, Number(pause.pausedAt || 0)),
    previousTargetTemp: Number.isFinite(Number(pause.previousTargetTemp)) ? Number(pause.previousTargetTemp) : null,
    previousLastComfortTarget: Number.isFinite(Number(pause.previousLastComfortTarget)) ? Number(pause.previousLastComfortTarget) : null,
    activeEntityIds: normalizePresenceEntityList(pause.activeEntityIds || []),
  };
}

function isPauseFunctionActive() {
  return Boolean(getPauseFunction().active);
}

function pauseFunctionEntryName(entry = {}) {
  return String(entry.name || entry.entityId || "Entry").trim() || "Entry";
}

function pauseFunctionTimestampFromHa(value, now = Date.now()) {
  const timestamp = Date.parse(String(value || ""));
  return Number.isFinite(timestamp) && timestamp > 0 && timestamp <= now ? timestamp : 0;
}

function pauseFunctionOpenStartedAt(entry = {}, now = Date.now()) {
  const existing = Number(entry.openedAt || 0);
  if (Number.isFinite(existing) && existing > 0) return existing;
  return pauseFunctionTimestampFromHa(entry.lastChanged, now)
    || pauseFunctionTimestampFromHa(entry.lastUpdated, now)
    || now;
}

function pauseFunctionStateLooksOpen(entry = {}) {
  const domain = String(entry.domain || pauseFunctionDomainFromEntityId(entry.entityId)).toLowerCase();
  const value = String(entry.state || "").trim().toLowerCase();
  if (entry.isClosed === false) return true;
  if (entry.isClosed === true && (!value || ["closed", "off"].includes(value))) return false;
  if (domain === "cover" && Number.isFinite(Number(entry.currentPosition)) && Number(entry.currentPosition) > 0) return true;
  if (!value || ["unknown", "unavailable", "none", "null"].includes(value)) return false;
  if (domain === "cover") return ["open", "opening"].includes(value);
  return ["on", "open", "opening", "detected", "true", "active"].includes(value);
}

function getOpenPauseFunctionEntries(now = Date.now()) {
  const pause = getPauseFunction();
  pause.entries = pause.entries.map((entry) => {
    const next = normalizePauseFunctionEntry(entry);
    if (!next) return null;
    const open = pauseFunctionStateLooksOpen(next);
    next.openedAt = open ? pauseFunctionOpenStartedAt(next, now) : 0;
    return next;
  }).filter(Boolean);
  return pause.entries.filter(pauseFunctionStateLooksOpen);
}

function getPauseFunctionExpiredEntries(now = Date.now()) {
  const pause = getPauseFunction();
  const thresholdMs = normalizePauseFunctionDuration(pause.durationMinutes) * 60000;
  return getOpenPauseFunctionEntries(now).filter((entry) => Number(entry.openedAt || 0) && now - Number(entry.openedAt || 0) >= thresholdMs);
}

function formatPauseFunctionEntryList(entries = []) {
  const names = entries.map(pauseFunctionEntryName).filter(Boolean);
  if (!names.length) return "Selected entries";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

function formatPauseFunctionCountdown(ms) {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
function normalizeAwaySource(value) {
  const source = String(value || "").trim().toLowerCase();
  return ["manual", "presence"].includes(source) ? source : "";
}

function normalizePresenceEntityList(value = []) {
  const seen = new Set();
  return (Array.isArray(value) ? value : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .filter((entityId) => {
      if (seen.has(entityId)) return false;
      seen.add(entityId);
      return true;
    });
}

function normalizeManualAwayPresenceLatch(latch = null) {
  if (!latch || typeof latch !== "object" || latch.active === false) return null;
  const startedAt = Number(latch.startedAt || Date.now());
  return {
    active: true,
    startedAt: Number.isFinite(startedAt) ? startedAt : Date.now(),
    baselineHome: normalizePresenceEntityList(latch.baselineHome),
    seenAway: normalizePresenceEntityList(latch.seenAway),
  };
}

function createManualAwayPresenceLatch(people = getThermostatPeople()) {
  const baselineHome = [];
  const seenAway = [];
  people.forEach((person) => {
    if (!person?.entityId) return;
    if (isPersonHomeState(person.state)) baselineHome.push(person.entityId);
    else if (isKnownPresenceState(person.state)) seenAway.push(person.entityId);
  });
  return normalizeManualAwayPresenceLatch({ active: true, startedAt: Date.now(), baselineHome, seenAway });
}

function clearManualAwayPresenceLatch() {
  state.thermostat.manualAwayPresenceLatch = null;
}

function updateManualAwayPresenceLatch(people = getThermostatPeople()) {
  const t = state.thermostat;
  let latch = normalizeManualAwayPresenceLatch(t.manualAwayPresenceLatch);
  if (!latch) latch = createManualAwayPresenceLatch(people);
  const seenAway = new Set(latch.seenAway);
  let changed = JSON.stringify(t.manualAwayPresenceLatch || null) !== JSON.stringify(latch);

  people.forEach((person) => {
    if (!person?.entityId) return;
    if (isKnownPresenceState(person.state) && !isPersonHomeState(person.state) && !seenAway.has(person.entityId)) {
      seenAway.add(person.entityId);
      changed = true;
    }
  });

  latch.seenAway = Array.from(seenAway);
  t.manualAwayPresenceLatch = latch;
  const returnedPerson = people.find((person) => person?.entityId && isPersonHomeState(person.state) && seenAway.has(person.entityId));
  return { returnedPerson, changed };
}

function getThermostatPeople() {
  state.thermostat.people = normalizeThermostatPeople(state.thermostat.people);
  return state.thermostat.people;
}

function isPersonHomeState(value) {
  return String(value || "").trim().toLowerCase() === "home";
}

function isKnownPresenceState(value) {
  const stateValue = String(value || "").trim().toLowerCase();
  return Boolean(stateValue) && !["unknown", "unavailable", "none", "null"].includes(stateValue);
}

function formatPresenceState(value) {
  const stateValue = String(value || "unknown").trim().toLowerCase();
  if (stateValue === "home") return "Home";
  if (stateValue === "not_home") return "Away";
  if (stateValue === "unknown" || stateValue === "unavailable") return "Unknown";
  return titleCase(stateValue.replace(/_/g, " "));
}

function renderThermostatPeople() {
  const people = getThermostatPeople();
  if (elements.thermostatPeopleList) {
    elements.thermostatPeopleList.innerHTML = people.length ? people.map((person) => {
      const home = isPersonHomeState(person.state);
      const known = isKnownPresenceState(person.state);
      return `
        <div class="thermostat-person-pill ${home ? "home" : known ? "away" : "unknown"}" data-thermostat-person-id="${escapeHtml(person.entityId)}">
          <div>
            <strong>${escapeHtml(person.name || person.entityId)}</strong>
            <span>${escapeHtml(person.entityId)}</span>
          </div>
          <em>${escapeHtml(formatPresenceState(person.state))}</em>
          <button type="button" data-remove-thermostat-person="${escapeHtml(person.entityId)}" aria-label="Remove ${escapeHtml(person.name || person.entityId)}">×</button>
        </div>
      `;
    }).join("") : `<div class="thermostat-empty-people">No people assigned. Tap + Person to add Home Assistant person entries.</div>`;
  }
  if (elements.heatLockToggle) {
    elements.heatLockToggle.classList.toggle("active", Boolean(state.thermostat.heatLocked));
    elements.heatLockToggle.setAttribute("aria-pressed", state.thermostat.heatLocked ? "true" : "false");
  }
  if (elements.coolLockToggle) {
    elements.coolLockToggle.classList.toggle("active", Boolean(state.thermostat.coolLocked));
    elements.coolLockToggle.setAttribute("aria-pressed", state.thermostat.coolLocked ? "true" : "false");
  }
  if (elements.scheduleOverlay?.classList.contains("open")) renderScheduleOverlay();
  renderSchedulePresets();
}

function applyThermostatPresenceAutomation(options = {}) {
  const people = getThermostatPeople();
  if (!people.length) return false;
  const anyHome = people.some((person) => isPersonHomeState(person.state));
  const allKnown = people.every((person) => isKnownPresenceState(person.state));
  const allAway = allKnown && !anyHome;
  const t = state.thermostat;
  let changed = false;

  if (t.away && normalizeAwaySource(t.awaySource) !== "presence") {
    t.awaySource = "manual";
    const latchResult = updateManualAwayPresenceLatch(people);
    changed = Boolean(latchResult.changed);
    if (latchResult.returnedPerson) {
      t.away = false;
      t.awaySource = "presence";
      clearManualAwayPresenceLatch();
      const { min, max } = getModeLimits();
      t.targetTemp = clamp(t.lastComfortTarget, min, max);
      changed = true;
      if (options.toast !== false) showToast(`${latchResult.returnedPerson.name || "Person"} returned • Home mode restored`);
    }
  } else if (allAway && !t.away) {
    t.away = true;
    t.lastComfortTarget = t.targetTemp;
    t.awaySource = "presence";
    clearManualAwayPresenceLatch();
    applyAwayTarget();
    changed = true;
    if (options.toast !== false) showToast("Everyone away • Away mode active");
  } else if (anyHome && t.away) {
    t.away = false;
    t.awaySource = "presence";
    clearManualAwayPresenceLatch();
    const { min, max } = getModeLimits();
    t.targetTemp = clamp(t.lastComfortTarget, min, max);
    changed = true;
    if (options.toast !== false) showToast("Person home • Home mode restored");
  } else if (!t.away && t.manualAwayPresenceLatch) {
    clearManualAwayPresenceLatch();
    changed = true;
  }

  if (changed) {
    renderThermostat();
    saveConfig();
  }
  return changed;
}

function upsertThermostatPerson(entity = {}) {
  const normalized = normalizeThermostatPersonEntry(entity);
  if (!normalized) return;
  const people = getThermostatPeople();
  const index = people.findIndex((person) => person.entityId === normalized.entityId);
  if (index >= 0) people[index] = { ...people[index], ...normalized };
  else people.push(normalized);
  state.thermostat.people = people;
  closeAudioEntityPicker();
  renderThermostatPeople();
  saveConfig({ toast: true });
  pollHomeAssistantThermostatPeople({ force: true });
}

function removeThermostatPerson(entityId) {
  const before = getThermostatPeople().length;
  state.thermostat.people = getThermostatPeople().filter((person) => person.entityId !== entityId);
  if (state.thermostat.people.length !== before) {
    renderThermostatPeople();
    saveConfig({ toast: true });
    showToast("Person removed");
  }
}

function scheduleId() {
  return `schedule-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function normalizeScheduleTime(value, fallback = "20:00") {
  const raw = String(value || "").trim();
  let match = /^(\d{1,2}):(\d{2})$/.exec(raw);
  if (match) {
    const hour = clamp(Number(match[1]), 0, 23);
    const minute = clamp(Number(match[2]), 0, 59);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }
  match = /^(\d{1,2})(?::(\d{1,2}))?\s*([AP])\.?M\.?$/i.exec(raw);
  if (!match) return fallback;
  let hour = clamp(Number(match[1]), 1, 12);
  const minute = clamp(Number(match[2] ?? 0), 0, 59);
  const meridiem = match[3].toUpperCase();
  if (meridiem === "AM") hour = hour === 12 ? 0 : hour;
  else hour = hour === 12 ? 12 : hour + 12;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function normalizeSchedulePersonIds(value = []) {
  const seen = new Set();
  return (Array.isArray(value) ? value : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .filter((entityId) => {
      if (seen.has(entityId)) return false;
      seen.add(entityId);
      return true;
    });
}

function normalizeThermostatSchedule(entry = {}, index = 0) {
  const base = entry && typeof entry === "object" ? entry : {};
  const id = String(base.id || "").trim() || scheduleId();
  const name = String(base.name || `Schedule ${index + 1}`).trim().slice(0, 40) || `Schedule ${index + 1}`;
  return {
    id,
    name,
    enabled: base.enabled !== false,
    time: normalizeScheduleTime(base.time, "20:00"),
    coolSetpoint: clamp(Math.round(Number(base.coolSetpoint ?? base.coolTarget ?? 68)), ABS_MIN, ABS_MAX),
    heatSetpoint: clamp(Math.round(Number(base.heatSetpoint ?? base.heatTarget ?? 71)), ABS_MIN, ABS_MAX),
    personEntityIds: normalizeSchedulePersonIds(base.personEntityIds || base.people || base.persons),
    lastTriggeredDate: String(base.lastTriggeredDate || base.lastRunDate || "").trim(),
  };
}

function normalizeThermostatSchedules(schedules = []) {
  const seen = new Set();
  return (Array.isArray(schedules) ? schedules : [])
    .map((schedule, index) => normalizeThermostatSchedule(schedule, index))
    .filter((schedule) => {
      if (!schedule.id || seen.has(schedule.id)) return false;
      seen.add(schedule.id);
      return true;
    })
    .slice(0, 18);
}

function defaultThermostatSchedule() {
  const t = state.thermostat;
  const now = new Date();
  const nextHour = (now.getHours() + 1) % 24;
  return normalizeThermostatSchedule({
    id: scheduleId(),
    name: "Evening",
    enabled: true,
    time: `${String(nextHour).padStart(2, "0")}:00`,
    coolSetpoint: clamp(Math.round(Number(t.targetTemp || 68)), ABS_MIN, ABS_MAX),
    heatSetpoint: clamp(Math.round(Number(t.targetTemp || 71)), ABS_MIN, ABS_MAX),
    personEntityIds: [],
    lastTriggeredDate: "",
  }, 0);
}

function getThermostatSchedules() {
  state.thermostat.schedules = normalizeThermostatSchedules(state.thermostat.schedules);
  return state.thermostat.schedules;
}

function localDateKey(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function localTimeKey(date = new Date()) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function formatScheduleTime(value) {
  const time = normalizeScheduleTime(value, "00:00");
  const [hour, minute] = time.split(":").map(Number);
  const date = new Date();
  date.setHours(hour, minute, 0, 0);
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function scheduleTimeParts(value) {
  const time = normalizeScheduleTime(value, "20:00");
  const [hour24, minute] = time.split(":").map(Number);
  const meridiem = hour24 >= 12 ? "PM" : "AM";
  const hour12 = hour24 % 12 || 12;
  return { hour12, minute, meridiem };
}

function scheduleTimeFromParts(parts = {}) {
  let hour = clamp(Number(parts.hour12 || 8), 1, 12);
  const minute = clamp(Number(parts.minute || 0), 0, 59);
  const meridiem = String(parts.meridiem || "PM").toUpperCase() === "AM" ? "AM" : "PM";
  if (meridiem === "AM") hour = hour === 12 ? 0 : hour;
  else hour = hour === 12 ? 12 : hour + 12;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function setScheduleTimeInputValue(value) {
  if (!elements.scheduleTimeInput) return;
  const normalized = normalizeScheduleTime(value, "20:00");
  elements.scheduleTimeInput.dataset.timeValue = normalized;
  elements.scheduleTimeInput.value = formatScheduleTime(normalized);
}

function ensureScheduleTimePickerState() {
  const current = state.scheduleTimePicker || scheduleTimeParts("20:00");
  state.scheduleTimePicker = {
    hour12: clamp(Number(current.hour12 || 8), 1, 12),
    minute: clamp(Number(current.minute || 0), 0, 59),
    meridiem: String(current.meridiem || "PM").toUpperCase() === "AM" ? "AM" : "PM",
  };
  return state.scheduleTimePicker;
}

function renderScheduleTimePicker() {
  if (!elements.scheduleTimePickerOverlay) return;
  const parts = ensureScheduleTimePickerState();
  const time = scheduleTimeFromParts(parts);
  if (elements.scheduleTimePreview) elements.scheduleTimePreview.textContent = formatScheduleTime(time);
  if (elements.scheduleTimeHourValue) elements.scheduleTimeHourValue.textContent = String(parts.hour12);
  if (elements.scheduleTimeMinuteValue) elements.scheduleTimeMinuteValue.textContent = String(parts.minute).padStart(2, "0");
  elements.scheduleTimePickerOverlay.querySelectorAll("[data-schedule-time-meridiem]").forEach((button) => {
    const active = button.dataset.scheduleTimeMeridiem === parts.meridiem;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  if (elements.scheduleMinuteShortcuts) {
    const options = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
    elements.scheduleMinuteShortcuts.innerHTML = options.map((minute) => {
      const active = minute === parts.minute ? " active" : "";
      return `<button class="schedule-minute-shortcut${active}" type="button" data-schedule-minute="${minute}" aria-pressed="${active ? "true" : "false"}">:${String(minute).padStart(2, "0")}</button>`;
    }).join("");
  }
}

function openScheduleTimePicker(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const draft = updateScheduleDraftFromInputs();
  state.scheduleTimePicker = scheduleTimeParts(draft.time || "20:00");
  if (elements.scheduleTimePickerTitle) elements.scheduleTimePickerTitle.textContent = `Pick time for ${draft.name || "schedule"}`;
  renderScheduleTimePicker();
  elements.scheduleTimePickerOverlay?.classList.add("open");
  elements.scheduleTimePickerOverlay?.setAttribute("aria-hidden", "false");
}

function closeScheduleTimePicker(options = {}) {
  elements.scheduleTimePickerOverlay?.classList.remove("open");
  elements.scheduleTimePickerOverlay?.setAttribute("aria-hidden", "true");
  if (options.focusInput && elements.scheduleTimeInput) elements.scheduleTimeInput.focus({ preventScroll: true });
}

function adjustScheduleTimePickerPart(part, delta) {
  const parts = ensureScheduleTimePickerState();
  const amount = Number(delta || 0);
  if (part === "hour") {
    parts.hour12 += amount;
    while (parts.hour12 < 1) parts.hour12 += 12;
    while (parts.hour12 > 12) parts.hour12 -= 12;
  }
  if (part === "minute") {
    parts.minute += amount;
    while (parts.minute < 0) parts.minute += 60;
    while (parts.minute > 59) parts.minute -= 60;
  }
  renderScheduleTimePicker();
}

function setScheduleTimePickerMinute(minute) {
  const parts = ensureScheduleTimePickerState();
  parts.minute = clamp(Number(minute || 0), 0, 59);
  renderScheduleTimePicker();
}

function setScheduleTimePickerMeridiem(meridiem) {
  const parts = ensureScheduleTimePickerState();
  parts.meridiem = String(meridiem || "PM").toUpperCase() === "AM" ? "AM" : "PM";
  renderScheduleTimePicker();
}

function applyScheduleTimePicker() {
  const draft = ensureScheduleEditor();
  draft.time = scheduleTimeFromParts(ensureScheduleTimePickerState());
  draft.lastTriggeredDate = "";
  setScheduleTimeInputValue(draft.time);
  closeScheduleTimePicker();
  renderScheduleOverlay();
}

function getSchedulePersonLabel(entityId) {
  const person = getThermostatPeople().find((item) => item.entityId === entityId);
  return person?.name || entityId;
}

function schedulePersonState(entityId) {
  return getThermostatPeople().find((person) => person.entityId === entityId)?.state || "unknown";
}

function scheduleConditionMet(schedule) {
  const ids = normalizeSchedulePersonIds(schedule.personEntityIds);
  if (!ids.length) return true;
  return ids.every((entityId) => isPersonHomeState(schedulePersonState(entityId)));
}

function scheduleConditionSummary(schedule) {
  const ids = normalizeSchedulePersonIds(schedule.personEntityIds);
  if (!ids.length) return "Always active";
  const names = ids.map(getSchedulePersonLabel).filter(Boolean);
  if (!names.length) return "Missing person";
  return `${names.join(", ")} home`;
}

function scheduleApplyMode() {
  const t = state.thermostat;
  const mode = t.mode === "auto" ? getNormalControlMode() : t.mode;
  return ["cool", "heat"].includes(mode) ? mode : "";
}

function applyThermostatSchedule(schedule, options = {}) {
  const normalized = normalizeThermostatSchedule(schedule);
  if (!normalized.enabled) return { applied: false, reason: "disabled" };
  if (state.thermostat.away) return { applied: false, reason: "away" };
  if (!scheduleConditionMet(normalized)) return { applied: false, reason: "condition" };
  const mode = scheduleApplyMode();
  if (!mode) return { applied: false, reason: "mode" };
  if (mode === "cool" && state.thermostat.coolLocked) return { applied: false, reason: "cool-locked" };
  if (mode === "heat" && state.thermostat.heatLocked) return { applied: false, reason: "heat-locked" };
  const sourceTarget = mode === "cool" ? normalized.coolSetpoint : normalized.heatSetpoint;
  const limits = state.thermostat.limits?.[mode] || getModeLimits();
  const nextTarget = clamp(Math.round(Number(sourceTarget)), limits.min ?? ABS_MIN, limits.max ?? ABS_MAX);
  state.thermostat.targetTemp = nextTarget;
  state.thermostat.lastComfortTarget = nextTarget;
  clearAutoSwitchHold();
  if (options.render !== false) renderThermostat();
  if (options.save !== false) saveConfig();
  if (options.toast !== false) showToast(`${normalized.name}: ${titleCase(mode)} set to ${nextTarget}°`);
  return { applied: true, mode, target: nextTarget };
}

function checkThermostatSchedules() {
  const schedules = getThermostatSchedules();
  if (!schedules.length) return;
  const now = new Date();
  const dateKey = localDateKey(now);
  const timeKey = localTimeKey(now);
  let changed = false;
  let applied = null;
  schedules.forEach((schedule) => {
    if (!schedule.enabled || schedule.lastTriggeredDate === dateKey || schedule.time !== timeKey) return;
    schedule.lastTriggeredDate = dateKey;
    changed = true;
    const result = applyThermostatSchedule(schedule, { render: false, save: false, toast: false });
    if (result.applied) applied = { schedule, result };
  });
  if (changed) {
    renderThermostat();
    saveConfig();
    renderScheduleOverlay();
    if (applied) showToast(`${applied.schedule.name}: ${titleCase(applied.result.mode)} set to ${applied.result.target}°`);
  }
}

function ensureScheduleEditor() {
  let schedules = getThermostatSchedules();
  if (!schedules.length) {
    schedules = [defaultThermostatSchedule()];
    state.thermostat.schedules = schedules;
  }
  const selectedId = state.scheduleEditor.selectedId || schedules[0].id;
  const selected = schedules.find((schedule) => schedule.id === selectedId) || schedules[0];
  state.scheduleEditor.selectedId = selected.id;
  if (!state.scheduleEditor.draft || state.scheduleEditor.draft.id !== selected.id) {
    state.scheduleEditor.draft = clone(selected);
  }
  return state.scheduleEditor.draft;
}

function selectSchedule(scheduleId) {
  commitScheduleDraft({ toast: false, render: false });
  const schedule = getThermostatSchedules().find((item) => item.id === scheduleId);
  if (!schedule) return;
  state.scheduleEditor.selectedId = schedule.id;
  state.scheduleEditor.draft = clone(schedule);
  renderScheduleOverlay();
}

function addThermostatSchedule() {
  commitScheduleDraft({ toast: false, render: false });
  const schedules = getThermostatSchedules();
  const schedule = defaultThermostatSchedule();
  schedule.name = `Schedule ${schedules.length + 1}`;
  schedules.push(schedule);
  state.scheduleEditor.selectedId = schedule.id;
  state.scheduleEditor.draft = clone(schedule);
  renderScheduleOverlay();
  renderSchedulePresets();
  saveConfig();
  showToast("Schedule added");
}

function deleteSelectedSchedule() {
  const selectedId = state.scheduleEditor.selectedId;
  let schedules = getThermostatSchedules().filter((schedule) => schedule.id !== selectedId);
  if (!schedules.length) schedules = [defaultThermostatSchedule()];
  state.thermostat.schedules = schedules;
  state.scheduleEditor.selectedId = schedules[0].id;
  state.scheduleEditor.draft = clone(schedules[0]);
  renderScheduleOverlay();
  renderSchedulePresets();
  saveConfig();
  showToast("Schedule deleted");
}

function commitScheduleDraft(options = {}) {
  const draft = state.scheduleEditor.draft;
  if (!draft) return null;
  const normalized = normalizeThermostatSchedule(draft);
  const schedules = getThermostatSchedules();
  const index = schedules.findIndex((schedule) => schedule.id === normalized.id);
  if (index >= 0) {
    const existing = schedules[index];
    if (existing.time !== normalized.time || existing.enabled !== normalized.enabled) normalized.lastTriggeredDate = "";
    schedules[index] = normalized;
  }
  else schedules.push(normalized);
  state.thermostat.schedules = normalizeThermostatSchedules(schedules);
  state.scheduleEditor.selectedId = normalized.id;
  state.scheduleEditor.draft = clone(normalized);
  renderSchedulePresets();
  if (options.render !== false) renderScheduleOverlay();
  if (options.save !== false) saveConfig();
  if (options.toast) showToast("Schedule saved");
  return normalized;
}

function updateScheduleDraftFromInputs() {
  const draft = ensureScheduleEditor();
  if (elements.scheduleNameInput) draft.name = elements.scheduleNameInput.value;
  if (elements.scheduleTimeInput) {
    draft.time = normalizeScheduleTime(elements.scheduleTimeInput.dataset.timeValue || elements.scheduleTimeInput.value, draft.time || "20:00");
  }
  return draft;
}

function adjustScheduleDraftSetpoint(kind, delta) {
  const draft = updateScheduleDraftFromInputs();
  const key = kind === "heat" ? "heatSetpoint" : "coolSetpoint";
  draft[key] = clamp(Math.round(Number(draft[key] || (kind === "heat" ? 71 : 68)) + Number(delta || 0)), ABS_MIN, ABS_MAX);
  renderScheduleOverlay();
}

function toggleScheduleEnabled() {
  const draft = updateScheduleDraftFromInputs();
  draft.enabled = !draft.enabled;
  renderScheduleOverlay();
}

function toggleSchedulePerson(entityId) {
  const draft = updateScheduleDraftFromInputs();
  const ids = new Set(normalizeSchedulePersonIds(draft.personEntityIds));
  if (ids.has(entityId)) ids.delete(entityId);
  else ids.add(entityId);
  draft.personEntityIds = Array.from(ids);
  renderScheduleOverlay();
}

function renderScheduleList() {
  if (!elements.scheduleList) return;
  const schedules = getThermostatSchedules();
  const selectedId = state.scheduleEditor.selectedId || schedules[0]?.id || "";
  elements.scheduleList.innerHTML = schedules.map((schedule) => {
    const condition = scheduleConditionSummary(schedule);
    const activeClass = schedule.id === selectedId ? "active" : "";
    const disabledClass = schedule.enabled ? "" : "disabled";
    return `
      <button class="schedule-row ${activeClass} ${disabledClass}" type="button" data-schedule-id="${escapeHtml(schedule.id)}">
        <span>
          <strong>${escapeHtml(schedule.name)}</strong>
          <em>${escapeHtml(condition)}</em>
        </span>
        <b>${escapeHtml(formatScheduleTime(schedule.time))}</b>
      </button>
    `;
  }).join("");
}

function renderSchedulePeople(draft) {
  if (!elements.schedulePersonChips) return;
  const people = getThermostatPeople();
  const selected = new Set(normalizeSchedulePersonIds(draft.personEntityIds));
  if (!people.length) {
    elements.schedulePersonChips.innerHTML = `<div class="schedule-empty-people">Add people in Comfort Setup first, then select them here.</div>`;
    return;
  }
  elements.schedulePersonChips.innerHTML = people.map((person) => {
    const active = selected.has(person.entityId);
    const home = isPersonHomeState(person.state);
    const known = isKnownPresenceState(person.state);
    return `
      <button class="schedule-person-chip ${active ? "active" : ""} ${home ? "home" : known ? "away" : "unknown"}" type="button" data-schedule-person="${escapeHtml(person.entityId)}" aria-pressed="${active ? "true" : "false"}">
        <strong>${escapeHtml(person.name || person.entityId)}</strong>
        <em>${escapeHtml(formatPresenceState(person.state))}</em>
      </button>
    `;
  }).join("");
}

function scheduleApplyBlockedMessage(schedule, reason) {
  const name = schedule?.name || "Schedule";
  switch (reason) {
    case "disabled": return `${name} is disabled`;
    case "away": return `${name}: Away mode is active`;
    case "condition": return `${name}: person condition not met`;
    case "mode": return `${name}: select Cool, Heat, or Auto first`;
    case "cool-locked": return `${name}: Cool is locked`;
    case "heat-locked": return `${name}: Heat is locked`;
    default: return `${name}: could not apply`;
  }
}

function renderSchedulePresets() {
  if (!elements.schedulePresetBar) return;
  const schedules = getThermostatSchedules().filter((schedule) => schedule.enabled !== false);
  if (!schedules.length) {
    elements.schedulePresetBar.hidden = true;
    elements.schedulePresetBar.innerHTML = "";
    return;
  }
  const mode = scheduleApplyMode();
  elements.schedulePresetBar.hidden = false;
  elements.schedulePresetBar.innerHTML = schedules.slice(0, 8).map((schedule) => {
    const conditionMet = scheduleConditionMet(schedule);
    const setpoint = mode === "heat" ? schedule.heatSetpoint : schedule.coolSetpoint;
    const detail = mode ? `${Math.round(Number(setpoint))}° ${titleCase(mode)}` : formatScheduleTime(schedule.time);
    const waitingClass = conditionMet ? "" : " condition-waiting";
    const title = conditionMet
      ? `Apply ${schedule.name} now`
      : `${schedule.name}: ${scheduleConditionSummary(schedule)}`;
    return `
      <button class="schedule-preset-btn${waitingClass}" type="button" data-schedule-preset-id="${escapeHtml(schedule.id)}" title="${escapeHtml(title)}">
        <strong>${escapeHtml(schedule.name)}</strong>
        <small>${escapeHtml(detail)}</small>
      </button>
    `;
  }).join("");
}

function applySchedulePreset(scheduleId) {
  const schedule = getThermostatSchedules().find((item) => item.id === scheduleId);
  if (!schedule) return;
  const result = applyThermostatSchedule(schedule);
  if (!result.applied) showToast(scheduleApplyBlockedMessage(schedule, result.reason));
  renderSchedulePresets();
}

function renderScheduleOverlay() {
  if (!elements.scheduleOverlay) return;
  const draft = ensureScheduleEditor();
  renderScheduleList();
  if (elements.scheduleNameInput && document.activeElement !== elements.scheduleNameInput) elements.scheduleNameInput.value = draft.name || "";
  if (elements.scheduleTimeInput) setScheduleTimeInputValue(draft.time || "20:00");
  if (elements.scheduleEnabledToggle) {
    elements.scheduleEnabledToggle.classList.toggle("active", draft.enabled !== false);
    elements.scheduleEnabledToggle.textContent = draft.enabled === false ? "Disabled" : "Enabled";
    elements.scheduleEnabledToggle.setAttribute("aria-pressed", draft.enabled === false ? "false" : "true");
  }
  if (elements.scheduleCoolValue) elements.scheduleCoolValue.textContent = Math.round(Number(draft.coolSetpoint || 68));
  if (elements.scheduleHeatValue) elements.scheduleHeatValue.textContent = Math.round(Number(draft.heatSetpoint || 71));
  if (elements.scheduleConditionSummary) elements.scheduleConditionSummary.textContent = scheduleConditionSummary(draft);
  renderSchedulePeople(draft);
}

function openScheduleOverlay() {
  ensureScheduleEditor();
  renderScheduleOverlay();
  setOverlayOpen(elements.scheduleOverlay, true);
}

function closeScheduleOverlay() {
  closeScheduleTimePicker();
  commitScheduleDraft({ toast: false, render: false });
  setOverlayOpen(elements.scheduleOverlay, false);
}

function toggleThermostatEquipmentLock(kind) {
  const t = state.thermostat;
  if (kind === "heat") t.heatLocked = !t.heatLocked;
  if (kind === "cool") t.coolLocked = !t.coolLocked;
  normalizeThermostatModeForLocks();
  if (t.away) applyAwayTarget();
  renderThermostat();
  saveConfig({ toast: true });
  showToast(`${titleCase(kind)} lock ${t[`${kind}Locked`] ? "enabled" : "disabled"}`);
}

async function fetchThermostatPersonStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const payload = await fetchJsonWithTimeout("/api/ha/room/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  }, 9000);
  return payload.controls || [];
}

async function pollHomeAssistantThermostatPeople(options = {}) {
  const people = getThermostatPeople();
  if (!people.length) return;
  if (!options.force && document.visibilityState === "hidden") return;
  if (haPresenceSyncInFlight) return;
  haPresenceSyncInFlight = true;
  try {
    const updates = await fetchThermostatPersonStatesViaLocalBackend(people.map((person) => person.entityId));
    const byId = new Map((updates || []).map((entity) => [entity.entityId, entity]));
    let changed = false;
    state.thermostat.people = people.map((person) => {
      const update = byId.get(person.entityId);
      if (!update) return person;
      const merged = normalizeThermostatPersonEntry({ ...person, ...update }) || person;
      if (JSON.stringify(merged) !== JSON.stringify(person)) changed = true;
      return merged;
    });
    if (changed) {
      renderThermostatPeople();
      saveConfig({ sync: false });
    }
    applyThermostatPresenceAutomation({ toast: true });
    if (haPresenceSyncLastError) haPresenceSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haPresenceSyncLastError) {
      haPresenceSyncLastError = message;
      addHaLog("warn", "Live presence sync paused", message);
    }
  } finally {
    haPresenceSyncInFlight = false;
  }
}

async function pollHomeAssistantPauseFunction(options = {}) {
  const pause = getPauseFunction();
  if (!pause.entries.length) {
    evaluatePauseFunction({ toast: false });
    return;
  }
  if (!options.force && document.visibilityState === "hidden") return;
  if (haPauseFunctionSyncInFlight) return;
  const ha = state.integrations.homeAssistant || {};
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) return;
  haPauseFunctionSyncInFlight = true;
  try {
    const payload = await fetchJsonWithTimeout("/api/ha/room/states", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds: pause.entries.map((entry) => entry.entityId) }),
    }, 9000);
    const updates = payload.controls || [];
    const byId = new Map(updates.map((entity) => [entity.entityId, entity]));
    let changed = false;
    const now = Date.now();
    pause.entries = pause.entries.map((entry) => {
      const update = byId.get(entry.entityId);
      const merged = normalizePauseFunctionEntry({ ...entry, ...(update || {}) }) || entry;
      const open = pauseFunctionStateLooksOpen(merged);
      const nextOpenedAt = open ? pauseFunctionOpenStartedAt({ ...merged, openedAt: entry.openedAt }, now) : 0;
      if (Number(merged.openedAt || 0) !== nextOpenedAt) merged.openedAt = nextOpenedAt;
      if (JSON.stringify(merged) !== JSON.stringify(entry)) changed = true;
      return merged;
    });
    if (changed) renderPauseFunctionStatus();
    evaluatePauseFunction({ toast: true });
    if (haPauseFunctionSyncLastError) haPauseFunctionSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haPauseFunctionSyncLastError) {
      haPauseFunctionSyncLastError = message;
      addHaLog("warn", "Pause Function sync paused", message);
    }
  } finally {
    haPauseFunctionSyncInFlight = false;
  }
}

function getAutoLockoutMs() {
  const minutes = Math.max(AUTO_CHANGEOVER_MINUTES, Number(state.thermostat.autoChangeoverLockoutMinutes) || AUTO_CHANGEOVER_MINUTES);
  return minutes * 60 * 1000;
}

function getManualChangeoverLockoutMs() {
  const minutes = clamp(
    Number(state.thermostat.manualChangeoverLockoutMinutes) || MANUAL_CHANGEOVER_LOCKOUT_MINUTES,
    1,
    60
  );
  return minutes * 60 * 1000;
}

function normalizePendingMode(value) {
  const mode = String(value || "").toLowerCase();
  return ["heat", "cool"].includes(mode) ? mode : "";
}


function emptyAutoSwitchNotice() {
  return { active: false, source: "", fromMode: "", toMode: "", switchTemp: 0, outdoorTemp: 0, coolTarget: 0, heatTarget: 0, createdAt: 0 };
}

function emptyAutoSwitchHold() {
  return { active: false, source: "", mode: "" };
}

function normalizeAutoSwitchNotice(value = {}) {
  if (!value || typeof value !== "object") return emptyAutoSwitchNotice();
  const source = ["auto", "manual"].includes(String(value.source || "").toLowerCase()) ? String(value.source || "").toLowerCase() : "";
  const fromMode = normalizePendingMode(value.fromMode);
  const toMode = normalizePendingMode(value.toMode);
  const active = Boolean(value.active && source && fromMode && toMode && fromMode !== toMode);
  if (!active) return emptyAutoSwitchNotice();
  const switchTemp = Number(value.switchTemp ?? value.indoorTemp ?? value.currentTemp ?? value.outdoorTemp);
  const coolTarget = Number(value.coolTarget);
  const heatTarget = Number(value.heatTarget);
  const createdAt = Number(value.createdAt);
  return {
    active: true,
    source,
    fromMode,
    toMode,
    switchTemp: Number.isFinite(switchTemp) ? switchTemp : Number(state.thermostat?.currentTemp || 0),
    outdoorTemp: Number.isFinite(switchTemp) ? switchTemp : Number(state.thermostat?.currentTemp || 0),
    coolTarget: Number.isFinite(coolTarget) ? coolTarget : Number(state.thermostat?.autoCoolOutdoorTarget || 0),
    heatTarget: Number.isFinite(heatTarget) ? heatTarget : Number(state.thermostat?.autoHeatOutdoorTarget || 0),
    createdAt: Number.isFinite(createdAt) && createdAt > 0 ? createdAt : Date.now(),
  };
}

function normalizeAutoSwitchHold(value = {}) {
  if (!value || typeof value !== "object") return emptyAutoSwitchHold();
  const source = ["auto", "manual"].includes(String(value.source || "").toLowerCase()) ? String(value.source || "").toLowerCase() : "";
  const mode = normalizePendingMode(value.mode);
  const active = Boolean(value.active && source && mode);
  return active ? { active: true, source, mode } : emptyAutoSwitchHold();
}

function getAutoSwitchTemperature() {
  const current = Number(state.thermostat.currentTemp);
  return Number.isFinite(current) ? current : NaN;
}

function getAutoSwitchSignal() {
  const t = state.thermostat;
  const roomTemp = getAutoSwitchTemperature();
  if (!Number.isFinite(roomTemp)) return "";
  const coolTarget = Number(t.autoCoolOutdoorTarget) || 70;
  const heatTarget = Math.min(Number(t.autoHeatOutdoorTarget) || 65, coolTarget - 1);
  const coolAvailable = !t.coolLocked;
  const heatAvailable = !t.heatLocked;
  if (!coolAvailable && !heatAvailable) return "";
  if (roomTemp > coolTarget) return coolAvailable ? "cool" : (heatAvailable ? "heat" : "");
  if (roomTemp <= heatTarget) return heatAvailable ? "heat" : (coolAvailable ? "cool" : "");
  return "";
}

function clearAutoSwitchHold() {
  const current = normalizeAutoSwitchHold(state.thermostat.autoSwitchHold);
  state.thermostat.autoSwitchHold = emptyAutoSwitchHold();
  return current.active;
}

function clearAutoSwitchNotice() {
  const current = normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice);
  state.thermostat.autoSwitchNotice = emptyAutoSwitchNotice();
  return current.active;
}

function recordAutoSwitchNotice(source, fromMode, toMode) {
  const normalizedSource = ["auto", "manual"].includes(String(source || "").toLowerCase()) ? String(source || "").toLowerCase() : "manual";
  const from = normalizePendingMode(fromMode);
  const to = normalizePendingMode(toMode);
  if (!from || !to || from === to) return false;
  const t = state.thermostat;
  const next = {
    active: true,
    source: normalizedSource,
    fromMode: from,
    toMode: to,
    switchTemp: getAutoSwitchTemperature(),
    outdoorTemp: getAutoSwitchTemperature(),
    coolTarget: Number(t.autoCoolOutdoorTarget) || 70,
    heatTarget: Number(t.autoHeatOutdoorTarget) || 65,
    createdAt: Date.now(),
  };
  const before = JSON.stringify(normalizeAutoSwitchNotice(t.autoSwitchNotice));
  t.autoSwitchNotice = next;
  t.autoSwitchHold = emptyAutoSwitchHold();
  return before !== JSON.stringify(next);
}

function modeIsAvailableForAutoSwitch(mode) {
  const t = state.thermostat;
  if (mode === "heat") return !t.heatLocked;
  if (mode === "cool") return !t.coolLocked;
  return false;
}

function clampTargetToCurrentModeLimits() {
  const t = state.thermostat;
  const modeForLimits = ["heat", "cool"].includes(t.mode) ? t.mode : "auto";
  const limits = t.limits[modeForLimits] || t.limits.auto || { min: ABS_MIN, max: ABS_MAX };
  t.targetTemp = clamp(t.targetTemp, limits.min, limits.max);
  t.lastComfortTarget = clamp(t.lastComfortTarget || t.targetTemp, limits.min, limits.max);
}

function applyAutoSwitch(options = {}) {
  const t = state.thermostat;
  let changed = false;
  const signal = getAutoSwitchSignal();
  let hold = normalizeAutoSwitchHold(t.autoSwitchHold);
  if (JSON.stringify(t.autoSwitchHold || {}) !== JSON.stringify(hold)) {
    t.autoSwitchHold = hold;
    changed = true;
  }

  if (hold.active && !modeIsAvailableForAutoSwitch(hold.mode)) {
    t.autoSwitchHold = emptyAutoSwitchHold();
    hold = t.autoSwitchHold;
    changed = true;
  }

  if (hold.active && hold.source === "manual" && t.mode !== "auto") {
    if (signal === hold.mode) {
      t.autoSwitchHold = emptyAutoSwitchHold();
      changed = true;
    } else {
      if (t.mode !== hold.mode) {
        t.mode = hold.mode;
        clearManualChangeoverLockout();
        if (t.away) applyAwayTarget();
        else clampTargetToCurrentModeLimits();
        changed = true;
      }
      return changed;
    }
  }

  if (!signal) return changed;

  if (t.mode === "auto") {
    const before = normalizePendingMode(t.autoActiveMode);
    const after = getAutoControlMode(Date.now(), { notify: options.notify !== false });
    return changed || (Boolean(before) && after !== before);
  }

  const currentMode = normalizePendingMode(t.mode);
  if (!currentMode || signal === currentMode || !modeIsAvailableForAutoSwitch(signal)) return changed;

  const previousMode = currentMode;
  t.mode = signal;
  clearManualChangeoverLockout();
  if (t.away) applyAwayTarget();
  else clampTargetToCurrentModeLimits();
  if (options.notify !== false) recordAutoSwitchNotice("manual", previousMode, signal);
  return true;
}

function renderAutoSwitchNotice() {
  const notice = normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice);
  state.thermostat.autoSwitchNotice = notice;
  const active = Boolean(notice.active);
  if (elements.autoSwitchNotice) {
    elements.autoSwitchNotice.hidden = !active;
    elements.autoSwitchNotice.setAttribute("aria-hidden", active ? "false" : "true");
    elements.autoSwitchNotice.classList.toggle("open", active);
    elements.autoSwitchNotice.classList.toggle("heat", notice.toMode === "heat");
    elements.autoSwitchNotice.classList.toggle("cool", notice.toMode === "cool");
    elements.autoSwitchNotice.title = active ? `Auto-switched from ${titleCase(notice.fromMode)} to ${titleCase(notice.toMode)}. Tap for options.` : "";
  }
  if (elements.autoSwitchNoticeMode) elements.autoSwitchNoticeMode.textContent = active ? `To ${titleCase(notice.toMode)}` : "";
  if (elements.autoSwitchNoticeDetail) {
    const roomTemp = Math.round(Number(notice.switchTemp) || Number(state.thermostat.currentTemp) || 0);
    elements.autoSwitchNoticeDetail.textContent = active ? `Inside ${roomTemp}°` : "";
  }
  if (elements.autoSwitchOverlay?.classList.contains("open")) renderAutoSwitchOverlay();
}

function renderAutoSwitchOverlay() {
  const notice = normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice);
  if (!notice.active) {
    setOverlayOpen(elements.autoSwitchOverlay, false);
    return;
  }
  const from = titleCase(notice.fromMode);
  const to = titleCase(notice.toMode);
  const roomTemp = Math.round(Number(notice.switchTemp) || Number(state.thermostat.currentTemp) || 0);
  const coolTarget = Math.round(Number(notice.coolTarget) || Number(state.thermostat.autoCoolOutdoorTarget) || 0);
  const heatTarget = Math.round(Number(notice.heatTarget) || Number(state.thermostat.autoHeatOutdoorTarget) || 0);
  if (elements.autoSwitchTitle) elements.autoSwitchTitle.textContent = `Auto-switched to ${to}`;
  if (elements.autoSwitchMessage) {
    elements.autoSwitchMessage.textContent = `Inside is ${roomTemp}°. Auto-switch targets are Heat at ${heatTarget}° or below and Cool above ${coolTarget}°. It changed from ${from} to ${to}. Dismiss this warning or revert to ${from} until the next room temperature swing.`;
  }
  if (elements.autoSwitchRevertButton) elements.autoSwitchRevertButton.textContent = `Revert to ${from}`;
}

function openAutoSwitchOverlay() {
  const notice = normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice);
  if (!notice.active) return;
  renderAutoSwitchOverlay();
  setOverlayOpen(elements.autoSwitchOverlay, true);
}

function closeAutoSwitchOverlay() {
  setOverlayOpen(elements.autoSwitchOverlay, false);
}

function dismissAutoSwitchNotice() {
  if (!clearAutoSwitchNotice()) {
    closeAutoSwitchOverlay();
    return;
  }
  closeAutoSwitchOverlay();
  renderThermostat();
  saveConfig();
  showToast("Auto-switch dismissed");
}

function revertAutoSwitchNotice() {
  const notice = normalizeAutoSwitchNotice(state.thermostat.autoSwitchNotice);
  if (!notice.active) {
    closeAutoSwitchOverlay();
    return;
  }
  const t = state.thermostat;
  if (!modeIsAvailableForAutoSwitch(notice.fromMode)) {
    showToast(`${titleCase(notice.fromMode)} is locked out`);
    closeAutoSwitchOverlay();
    renderThermostat();
    return;
  }
  if (notice.source === "auto") {
    t.mode = "auto";
    t.autoActiveMode = notice.fromMode;
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
  } else {
    t.mode = notice.fromMode;
    clearManualChangeoverLockout();
    if (t.away) applyAwayTarget();
    else clampTargetToCurrentModeLimits();
  }
  t.autoSwitchHold = { active: true, source: notice.source, mode: notice.fromMode };
  t.autoSwitchNotice = emptyAutoSwitchNotice();
  closeAutoSwitchOverlay();
  renderThermostat();
  saveConfig();
  showToast(`Reverted to ${titleCase(notice.fromMode)}`);
}

function getManualChangeoverLockoutForMode(mode, now = Date.now()) {
  const requestedMode = normalizePendingMode(mode);
  if (!requestedMode || state.thermostat.mode === "auto") return null;
  const oppositeKey = requestedMode === "heat" ? "equipmentLastCoolRunAt" : "equipmentLastHeatRunAt";
  const lastOppositeRunAt = Number(state.thermostat[oppositeKey] || 0);
  if (!lastOppositeRunAt) return null;
  const until = lastOppositeRunAt + getManualChangeoverLockoutMs();
  if (until <= now) return null;
  return {
    source: "manual",
    pendingMode: requestedMode,
    until,
    remaining: until - now,
  };
}

function getActiveChangeoverBypassState(now = Date.now()) {
  const t = state.thermostat;
  const autoPendingMode = normalizePendingMode(t.autoPendingMode);
  const autoUntil = Number(t.autoLockoutUntil || 0);
  if (t.mode === "auto" && autoPendingMode && autoUntil > now) {
    return { source: "auto", pendingMode: autoPendingMode, until: autoUntil, remaining: autoUntil - now };
  }
  const manualPendingMode = normalizePendingMode(t.manualPendingMode);
  const manualUntil = Number(t.manualLockoutUntil || 0);
  if (t.mode !== "auto" && manualPendingMode && manualUntil > now) {
    return { source: "manual", pendingMode: manualPendingMode, until: manualUntil, remaining: manualUntil - now };
  }
  return null;
}

function clearManualChangeoverLockout() {
  const t = state.thermostat;
  t.manualPendingMode = "";
  t.manualLockoutUntil = 0;
}

function bypassChangeoverLockout() {
  const t = state.thermostat;
  const active = getActiveChangeoverBypassState();
  if (!active) {
    showToast("No changeover delay active");
    renderThermostat();
    return;
  }
  if (active.source === "auto") {
    t.autoActiveMode = active.pendingMode;
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
  } else {
    if (active.pendingMode === "heat") t.equipmentLastCoolRunAt = 0;
    if (active.pendingMode === "cool") t.equipmentLastHeatRunAt = 0;
    clearManualChangeoverLockout();
  }
  renderThermostat();
  saveConfig();
  showToast(`${titleCase(active.pendingMode)} changeover bypassed`);
}

function formatLockoutTime(ms) {
  if (ms <= 0) return "Ready";
  const minutes = Math.ceil(ms / 60000);
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return mins ? `${hours}h ${mins}m` : `${hours}h`;
  }
  return `${minutes}m`;
}

function getAutoControlMode(now = Date.now(), options = {}) {
  const t = state.thermostat;
  const coolAvailable = !t.coolLocked;
  const heatAvailable = !t.heatLocked;
  if (!coolAvailable && !heatAvailable) {
    t.autoActiveMode = "";
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
    t.autoSwitchHold = emptyAutoSwitchHold();
    return "locked";
  }
  const coolTarget = Number(t.autoCoolOutdoorTarget) || 70;
  const heatTarget = Math.min(Number(t.autoHeatOutdoorTarget) || 65, coolTarget - 1);
  const roomTemp = getAutoSwitchTemperature();
  let active = ["heat", "cool"].includes(t.autoActiveMode) ? t.autoActiveMode : "";
  if (active === "heat" && !heatAvailable) active = "";
  if (active === "cool" && !coolAvailable) active = "";

  let hold = normalizeAutoSwitchHold(t.autoSwitchHold);
  if (hold.active && hold.source === "auto") {
    const signal = getAutoSwitchSignal();
    if (!modeIsAvailableForAutoSwitch(hold.mode) || signal === hold.mode) {
      t.autoSwitchHold = emptyAutoSwitchHold();
      hold = t.autoSwitchHold;
    } else {
      t.autoActiveMode = hold.mode;
      t.autoPendingMode = "";
      t.autoLockoutUntil = 0;
      return hold.mode;
    }
  }

  if (!Number.isFinite(roomTemp)) {
    const fallbackMode = active || (coolAvailable ? "cool" : "heat");
    t.autoActiveMode = fallbackMode;
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
    return fallbackMode;
  }

  let desired = active;
  if (roomTemp > coolTarget) desired = coolAvailable ? "cool" : "heat";
  else if (roomTemp <= heatTarget) desired = heatAvailable ? "heat" : "cool";
  else if (!desired) desired = roomTemp >= ((coolTarget + heatTarget) / 2)
    ? (coolAvailable ? "cool" : "heat")
    : (heatAvailable ? "heat" : "cool");
  if (desired === "heat" && !heatAvailable) desired = coolAvailable ? "cool" : "locked";
  if (desired === "cool" && !coolAvailable) desired = heatAvailable ? "heat" : "locked";
  if (desired === "locked") return "locked";

  if (!active) active = desired;
  const previousActive = active;

  if (desired !== active) {
    const lastOppositeRunAt = desired === "heat" ? Number(t.lastCoolRunAt || 0) : Number(t.lastHeatRunAt || 0);
    const lockoutMs = getAutoLockoutMs();
    if (lastOppositeRunAt && now - lastOppositeRunAt < lockoutMs) {
      t.autoPendingMode = desired;
      t.autoLockoutUntil = lastOppositeRunAt + lockoutMs;
      return active;
    }
    active = desired;
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
  } else if (t.autoLockoutUntil && now >= t.autoLockoutUntil) {
    t.autoPendingMode = "";
    t.autoLockoutUntil = 0;
  }

  t.autoActiveMode = active;
  if (options.notify !== false && previousActive && active !== previousActive) {
    recordAutoSwitchNotice("auto", previousActive, active);
  }
  return active;
}

function getSafetyOverrideMode() {
  const t = state.thermostat;
  const current = Number(t.currentTemp);
  const low = Number(t.safetyLow);
  const high = Number(t.safetyHigh);
  if (!Number.isFinite(current) || !Number.isFinite(low) || !Number.isFinite(high)) return "";
  if (current < low && !t.heatLocked) return "heat";
  if (current > high && !t.coolLocked) return "cool";
  return "";
}

function getNormalControlMode(now = Date.now()) {
  const t = state.thermostat;
  const mode = t.mode === "auto" ? getAutoControlMode(now) : t.mode;
  if (mode === "heat" && t.heatLocked) return !t.coolLocked ? "cool" : "locked";
  if (mode === "cool" && t.coolLocked) return !t.heatLocked ? "heat" : "locked";
  return mode;
}

function getEffectiveControlMode(now = Date.now(), options = {}) {
  const safetyMode = options.includeSafety === false ? "" : getSafetyOverrideMode();
  if (safetyMode) return safetyMode;
  return getNormalControlMode(now);
}

function getModeLimits() {
  const t = state.thermostat;
  const normalMode = t.mode === "auto" ? getNormalControlMode() : t.mode;
  const modeForLimits = normalMode === "locked" ? "auto" : normalMode;
  const limits = t.limits[modeForLimits] || t.limits.auto || t.limits.cool;
  const range = { min: limits.min, max: limits.max };
  if (t.away || isPauseFunctionActive()) {
    const awayMode = getNormalControlMode();
    if (awayMode === "cool") range.max = Math.max(range.max, t.awayCool);
    if (awayMode === "heat") range.min = Math.min(range.min, t.awayHeat);
  }
  return range;
}

function tempToDialSweep(temp) {
  const { min, max } = getModeLimits();
  const percent = clamp((temp - min) / (max - min), 0, 1);
  return percent * DIAL_SWEEP_DEG;
}

function getDialPointForSweep(sweep, radius = 43) {
  const cssDeg = (DIAL_START_DEG + sweep) % 360;
  const radians = (cssDeg * Math.PI) / 180;
  return {
    x: 50 + radius * Math.sin(radians),
    y: 50 - radius * Math.cos(radians),
    deg: cssDeg
  };
}

function setDialVisual(targetTemp, currentTemp = targetTemp) {
  const targetSweep = tempToDialSweep(targetTemp);
  const currentSweep = tempToDialSweep(currentTemp);
  const targetPoint = getDialPointForSweep(targetSweep, 43);
  const currentPoint = getDialPointForSweep(currentSweep, 43.2);
  const targetLabelPoint = getDialPointForSweep(targetSweep, 53.2);
  const bandStart = Math.min(targetSweep, currentSweep);
  const bandEnd = Math.max(targetSweep, currentSweep);
  const bandMinimum = 4;
  const visibleBandEnd = Math.max(bandEnd, bandStart + bandMinimum);

  elements.thermoDial.style.setProperty("--angle", `${targetSweep}deg`);
  elements.thermoDial.style.setProperty("--target-angle", `${targetSweep}deg`);
  elements.thermoDial.style.setProperty("--current-angle", `${currentSweep}deg`);
  elements.thermoDial.style.setProperty("--band-start", `${bandStart}deg`);
  elements.thermoDial.style.setProperty("--band-end", `${Math.min(visibleBandEnd, DIAL_SWEEP_DEG)}deg`);
  elements.thermoDial.style.setProperty("--target-deg", `${targetPoint.deg}deg`);
  elements.thermoDial.style.setProperty("--current-deg", `${currentPoint.deg}deg`);
  elements.thermoDial.style.setProperty("--knob-x", `${targetPoint.x}%`);
  elements.thermoDial.style.setProperty("--knob-y", `${targetPoint.y}%`);
  elements.thermoDial.style.setProperty("--target-x", `${targetPoint.x}%`);
  elements.thermoDial.style.setProperty("--target-y", `${targetPoint.y}%`);
  elements.thermoDial.style.setProperty("--target-label-x", `${targetLabelPoint.x}%`);
  elements.thermoDial.style.setProperty("--target-label-y", `${targetLabelPoint.y}%`);
  elements.thermoDial.style.setProperty("--current-x", `${currentPoint.x}%`);
  elements.thermoDial.style.setProperty("--current-y", `${currentPoint.y}%`);
  elements.thermoDial.setAttribute("aria-valuemin", String(getModeLimits().min));
  elements.thermoDial.setAttribute("aria-valuemax", String(getModeLimits().max));
  elements.thermoDial.setAttribute("aria-valuenow", String(Math.round(targetTemp)));
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
  const awayMode = getNormalControlMode();
  if (awayMode === "heat") t.targetTemp = t.awayHeat;
  else if (awayMode === "cool") t.targetTemp = t.awayCool;
  else t.targetTemp = clamp(t.lastComfortTarget || t.targetTemp, ABS_MIN, ABS_MAX);
}

function applyPauseFunctionTarget() {
  const t = state.thermostat;
  const mode = getNormalControlMode();
  if (mode === "heat") t.targetTemp = t.awayHeat;
  else if (mode === "cool") t.targetTemp = t.awayCool;
  else t.targetTemp = clamp(t.targetTemp, ABS_MIN, ABS_MAX);
}

function activatePauseFunction(triggeredEntries = []) {
  const t = state.thermostat;
  const pause = getPauseFunction();
  if (!pause.active) {
    pause.active = true;
    pause.pausedAt = Date.now();
    pause.previousTargetTemp = Number.isFinite(Number(t.targetTemp)) ? Number(t.targetTemp) : null;
    pause.previousLastComfortTarget = Number.isFinite(Number(t.lastComfortTarget)) ? Number(t.lastComfortTarget) : pause.previousTargetTemp;
  }
  pause.activeEntityIds = normalizePresenceEntityList(triggeredEntries.map((entry) => entry.entityId));
  applyPauseFunctionTarget();
}

function restorePauseFunctionTarget() {
  const t = state.thermostat;
  const pause = getPauseFunction();
  const previousTarget = Number(pause.previousTargetTemp);
  const previousLast = Number(pause.previousLastComfortTarget);
  pause.active = false;
  pause.pausedAt = 0;
  pause.previousTargetTemp = null;
  pause.previousLastComfortTarget = null;
  pause.activeEntityIds = [];
  const { min, max } = getModeLimits();
  if (Number.isFinite(previousTarget)) t.targetTemp = clamp(Math.round(previousTarget), min, max);
  if (Number.isFinite(previousLast)) t.lastComfortTarget = clamp(Math.round(previousLast), min, max);
  else t.lastComfortTarget = t.targetTemp;
}

function evaluatePauseFunction(options = {}) {
  const pause = getPauseFunction();
  const now = Date.now();
  let changed = false;
  const openEntries = getOpenPauseFunctionEntries(now);
  const expiredEntries = getPauseFunctionExpiredEntries(now);

  if (!pause.entries.length) {
    if (pause.active) {
      restorePauseFunctionTarget();
      changed = true;
    }
  } else if (pause.active) {
    if (!openEntries.length) {
      restorePauseFunctionTarget();
      changed = true;
      if (options.toast !== false) showToast("Pause Function restored");
    } else {
      const activeIds = normalizePresenceEntityList(openEntries.map((entry) => entry.entityId));
      if (JSON.stringify(pause.activeEntityIds || []) !== JSON.stringify(activeIds)) {
        pause.activeEntityIds = activeIds;
        changed = true;
      }
      const beforeTarget = state.thermostat.targetTemp;
      applyPauseFunctionTarget();
      if (state.thermostat.targetTemp !== beforeTarget) changed = true;
    }
  } else if (expiredEntries.length) {
    activatePauseFunction(expiredEntries);
    changed = true;
    if (options.toast !== false) showToast("Pause Function active");
  }

  renderPauseFunctionStatus();
  if (changed && options.save !== false) {
    if (options.render !== false) renderThermostat();
    saveConfig({ toast: false });
  }
  return changed;
}

function renderPauseFunctionSettings() {
  const pause = getPauseFunction();
  if (elements.pauseFunctionMinutesValue) elements.pauseFunctionMinutesValue.textContent = String(normalizePauseFunctionDuration(pause.durationMinutes));
  if (elements.pauseFunctionEntryList) {
    elements.pauseFunctionEntryList.innerHTML = pause.entries.length ? pause.entries.map((entry) => {
      const open = pauseFunctionStateLooksOpen(entry);
      const stateLabel = entry.state && entry.state !== "unknown" ? titleCase(String(entry.state).replace(/_/g, " ")) : "Waiting";
      const sub = `${entry.entityId}${entry.deviceClass ? ` · ${titleCase(entry.deviceClass)}` : ""}`;
      return `
        <div class="pause-function-entry ${open ? "open" : ""}" data-pause-entry-id="${escapeHtml(entry.entityId)}">
          <div>
            <strong>${escapeHtml(pauseFunctionEntryName(entry))}</strong>
            <span>${escapeHtml(sub)}</span>
          </div>
          <em>${escapeHtml(stateLabel)}</em>
          <button type="button" data-remove-pause-function-entry="${escapeHtml(entry.entityId)}" aria-label="Remove ${escapeHtml(pauseFunctionEntryName(entry))}">×</button>
        </div>
      `;
    }).join("") : `<div class="pause-function-empty">No entries selected. Tap + Entry to add doors, windows, covers, or switches.</div>`;
  }
  if (elements.pauseFunctionSummary) {
    const count = pause.entries.length;
    const minutes = normalizePauseFunctionDuration(pause.durationMinutes);
    elements.pauseFunctionSummary.textContent = count
      ? `${count} entr${count === 1 ? "y" : "ies"} selected • pause after ${minutes} min open/on.`
      : `Pause Function is off until at least one entry is selected.`;
  }
}

function renderPauseFunctionStatus() {
  const pause = getPauseFunction();
  const now = Date.now();
  const openEntries = getOpenPauseFunctionEntries(now);
  const thresholdMs = normalizePauseFunctionDuration(pause.durationMinutes) * 60000;
  const soonestOpenAt = openEntries.reduce((earliest, entry) => {
    const openedAt = Number(entry.openedAt || 0);
    return openedAt && (!earliest || openedAt < earliest) ? openedAt : earliest;
  }, 0);
  const countdownRemaining = soonestOpenAt ? Math.max(0, thresholdMs - (now - soonestOpenAt)) : 0;
  const showCountdown = Boolean(openEntries.length && !pause.active && countdownRemaining > 0);

  if (elements.pauseCountdownBadge) {
    elements.pauseCountdownBadge.hidden = !showCountdown;
    elements.pauseCountdownBadge.setAttribute("aria-hidden", showCountdown ? "false" : "true");
  }
  if (elements.pauseCountdownText) {
    elements.pauseCountdownText.textContent = showCountdown
      ? `${formatPauseFunctionEntryList(openEntries)} open • ${formatPauseFunctionCountdown(countdownRemaining)} to pause`
      : "";
  }

  if (elements.pauseFunctionOverlay) {
    elements.pauseFunctionOverlay.classList.toggle("open", Boolean(pause.active));
    elements.pauseFunctionOverlay.setAttribute("aria-hidden", pause.active ? "false" : "true");
  }
  if (elements.pauseFunctionMessage) {
    const activeEntries = openEntries.length ? openEntries : pause.entries.filter((entry) => (pause.activeEntityIds || []).includes(entry.entityId));
    const openForMs = Math.max(0, now - (soonestOpenAt || Number(pause.pausedAt || now)));
    elements.pauseFunctionMessage.textContent = pause.active
      ? `${formatPauseFunctionEntryList(activeEntries)} ${activeEntries.length === 1 ? "has" : "have"} been open/on for ${formatShortDuration(openForMs)}. Comfort is paused using the Away set points until the selected entries close.`
      : "Selected entries are being monitored.";
  }
  renderPauseFunctionSettings();
}

function shouldFastSyncPauseFunction() {
  const pause = getPauseFunction();
  return Boolean(pause.entries.length);
}

function servicePauseFunctionCountdown() {
  const now = Date.now();
  if (shouldFastSyncPauseFunction() && now - lastPauseFunctionFastSyncAt >= HA_PAUSE_FUNCTION_FAST_SYNC_INTERVAL_MS) {
    lastPauseFunctionFastSyncAt = now;
    pollHomeAssistantPauseFunction({ force: true });
  }
  evaluatePauseFunction({ toast: true });
}

function setPauseFunctionDuration(value, options = {}) {
  const pause = getPauseFunction();
  const next = normalizePauseFunctionDuration(value, pause.durationMinutes);
  if (pause.durationMinutes === next) {
    renderPauseFunctionStatus();
    return;
  }
  pause.durationMinutes = next;
  renderPauseFunctionStatus();
  evaluatePauseFunction({ toast: options.toast, save: false, render: false });
  saveConfig({ toast: false });
  if (options.toast) showToast(`Pause after ${next} min`);
}

function adjustPauseFunctionDuration(delta) {
  const pause = getPauseFunction();
  setPauseFunctionDuration(normalizePauseFunctionDuration(pause.durationMinutes) + Number(delta || 0), { toast: true });
}

function upsertPauseFunctionEntry(entity = {}) {
  const normalized = normalizePauseFunctionEntry(entity);
  if (!normalized) return;
  if (pauseFunctionStateLooksOpen(normalized)) normalized.openedAt = pauseFunctionOpenStartedAt(normalized);
  const pause = getPauseFunction();
  const existing = pause.entries.filter((entry) => entry.entityId !== normalized.entityId);
  pause.entries = [...existing, normalized];
  if (state.integrations?.homeAssistant) {
    const ha = state.integrations.homeAssistant;
    if (!Array.isArray(ha.pauseFunctionAvailableEntities)) ha.pauseFunctionAvailableEntities = [];
    ha.pauseFunctionAvailableEntities = [normalized, ...ha.pauseFunctionAvailableEntities.filter((entry) => entry.entityId !== normalized.entityId)].slice(0, 100);
  }
  renderPauseFunctionStatus();
  saveConfig({ toast: false });
  pollHomeAssistantPauseFunction({ force: true });
  showToast("Pause entry added");
}

function removePauseFunctionEntry(entityId) {
  const pause = getPauseFunction();
  const target = String(entityId || "").trim();
  pause.entries = pause.entries.filter((entry) => entry.entityId !== target);
  if (pause.activeEntityIds?.includes(target)) pause.activeEntityIds = pause.activeEntityIds.filter((id) => id !== target);
  evaluatePauseFunction({ toast: false, save: false, render: false });
  renderPauseFunctionStatus();
  saveConfig({ toast: false });
}

function isSetpointPreviewActive() {
  return Date.now() < setpointPreviewUntil;
}

function holdSetpointPreview(duration = SETPOINT_PREVIEW_MS) {
  setpointPreviewUntil = Date.now() + duration;
  if (setpointPreviewTimeout) window.clearTimeout(setpointPreviewTimeout);
  setpointPreviewTimeout = window.setTimeout(() => {
    setpointPreviewUntil = 0;
    renderThermostat();
  }, duration + 35);
}

function setHomeMode() {
  const t = state.thermostat;
  if (!t.away) return;
  t.away = false;
  t.awaySource = "";
  clearManualAwayPresenceLatch();
  const { min, max } = getModeLimits();
  t.targetTemp = clamp(t.lastComfortTarget, min, max);
  renderThermostat();
  saveConfig();
  showToast("Home comfort restored");
}

function isCoolingFanForced(outputs = getThermostatOutputs()) {
  return Boolean(outputs.cool || outputs.coolingFanHold);
}

function setFanMode(mode) {
  if (!["off", "on", "auto"].includes(mode)) return;
  const outputs = getThermostatOutputs();
  const fanForced = isCoolingFanForced(outputs);
  const next = fanForced && mode === "off" ? "auto" : mode;
  state.thermostat.fan = next;
  renderThermostat();
  saveConfig();
  showToast(fanForced && mode === "off" ? "Fan remains on for cooling" : `Fan ${titleCase(next)}`);
}

function cycleFanMode() {
  const t = state.thermostat;
  const currentIndex = FAN_SEQUENCE.indexOf(t.fan);
  const outputs = getThermostatOutputs();
  let next = FAN_SEQUENCE[(currentIndex + 1) % FAN_SEQUENCE.length] || "auto";
  if (isCoolingFanForced(outputs) && next === "off") next = t.fan === "auto" ? "on" : "auto";
  setFanMode(next);
}

function setTargetTemp(temp, options = {}) {
  const t = state.thermostat;
  if (isPauseFunctionActive() && !options.forcePause) {
    applyPauseFunctionTarget();
    renderThermostat();
    showToast("Pause Function active until entries close");
    return;
  }
  if (options.preview) holdSetpointPreview();
  if (t.away && !options.keepAway) {
    t.away = false;
    t.awaySource = "";
    clearManualAwayPresenceLatch();
    showToast("Returned home");
  }
  const { min, max } = getModeLimits();
  const next = clamp(Math.round(temp), min, max);
  t.targetTemp = next;
  if (!t.away) t.lastComfortTarget = next;
  renderThermostat();
  if (options.save !== false) saveConfig();
}

function setVirtualCurrentTemp(temp) {
  const next = clamp(Number(temp), VIRTUAL_TEMP_MIN, VIRTUAL_TEMP_MAX);
  const hasHaSensor = Boolean(getCurrentTempEntity()?.entityId);
  if (hasHaSensor) {
    virtualTempOverrideUntil = Date.now() + VIRTUAL_TEMP_OVERRIDE_MS;
    scheduleVirtualTempOverrideExpiry();
    state.thermostat.currentTempSource = "virtual-override";
    state.thermostat.currentTempSourceName = "Virtual Temp Override";
  } else {
    virtualTempOverrideUntil = 0;
    state.thermostat.currentTempSource = "virtual";
    state.thermostat.currentTempSourceName = "Virtual Temp";
  }
  state.thermostat.currentTemp = next;
  applyAutoSwitch({ notify: true });
  if (state.thermostat.away) applyAwayTarget();
  renderThermostat();
  saveConfig();
}

function setVirtualOutdoorTemp(temp) {
  const next = clamp(Number(temp), 40, 100);
  state.thermostat.outdoorTemp = next;
  applyAutoSwitch({ notify: true });
  if (state.thermostat.away) applyAwayTarget();
  renderThermostat();
  saveConfig();
}

function getThermostatOutputs(options = {}) {
  const t = state.thermostat;
  const now = Date.now();
  const safetyMode = getSafetyOverrideMode();
  let controlMode = getEffectiveControlMode(now);
  const operatingTarget = safetyMode === "heat" ? Number(t.safetyLow) : safetyMode === "cool" ? Number(t.safetyHigh) : Number(t.targetTemp);
  const desiredHeat = !t.heatLocked && controlMode === "heat" && Number(t.currentTemp) < operatingTarget;
  const desiredCool = !t.coolLocked && controlMode === "cool" && Number(t.currentTemp) > operatingTarget;
  let heat = desiredHeat;
  let cool = desiredCool;
  let manualLockout = null;
  let pendingMode = "";

  if (!safetyMode && t.mode !== "auto" && (desiredHeat || desiredCool)) {
    pendingMode = desiredHeat ? "heat" : "cool";
    manualLockout = getManualChangeoverLockoutForMode(pendingMode, now);
    if (manualLockout) {
      heat = false;
      cool = false;
      controlMode = "lockout";
      if (options.recordRuntime) {
        t.manualPendingMode = pendingMode;
        t.manualLockoutUntil = manualLockout.until;
      }
    } else if (options.recordRuntime && t.manualPendingMode) {
      clearManualChangeoverLockout();
    }
  } else if (options.recordRuntime && t.manualPendingMode && Number(t.manualLockoutUntil || 0) <= now) {
    clearManualChangeoverLockout();
  }

  if (options.recordRuntime) {
    if (cool && t.fan === "off") t.fan = "auto";
    if (cool) {
      t.coolFanHoldUntil = 0;
      t.equipmentLastCoolRunAt = now;
      if (t.mode === "auto") t.lastCoolRunAt = now;
    } else if (t.coolRelayWasOn) {
      const remainMinutes = clamp(Math.round(Number(t.coolFanRemainOnMinutes) || 0), 0, 10);
      t.coolFanHoldUntil = remainMinutes > 0 ? now + remainMinutes * 60000 : 0;
    }
    if (heat) {
      t.coolFanHoldUntil = 0;
      t.equipmentLastHeatRunAt = now;
      if (t.mode === "auto") t.lastHeatRunAt = now;
    }
    t.coolRelayWasOn = cool;
  }

  const coolingFanHold = !cool && Number(t.coolFanHoldUntil || 0) > now;
  const fan = cool || coolingFanHold || t.fan === "on";
  return {
    fan,
    heat,
    cool,
    coolingFanHold,
    controlMode,
    safetyMode,
    pendingMode,
    manualLockoutRemaining: manualLockout ? manualLockout.remaining : 0,
    manualLockoutUntil: manualLockout ? manualLockout.until : 0,
  };
}

function renderRelayStatus(element, isOn) {
  if (!element) return;
  element.classList.toggle("on", Boolean(isOn));
  const status = element.querySelector("em");
  if (status) status.textContent = isOn ? "On" : "Off";
}

function renderRelayLockStatus(element, locked) {
  if (!element) return;
  element.classList.toggle("locked", Boolean(locked));
  const status = element.querySelector("em");
  if (status && locked) status.textContent = "Locked";
}


function renderTemperatureAtmosphere(currentTemp) {
  const temp = Number(currentTemp);
  const coldIntensity = Number.isFinite(temp) ? clamp((71 - temp) / 9, 0, 1) : 0;
  const heatIntensity = Number.isFinite(temp) ? clamp((temp - 71) / 9, 0, 1) : 0;
  const dominantTone = coldIntensity > heatIntensity && coldIntensity > 0.01
    ? "cold"
    : heatIntensity > 0.01
      ? "hot"
      : "neutral";

  elements.app.dataset.tempTone = dominantTone;
  elements.app.style.setProperty("--climate-cold-alpha", (coldIntensity * 0.88).toFixed(3));
  elements.app.style.setProperty("--climate-hot-alpha", (heatIntensity * 0.84).toFixed(3));
  elements.app.style.setProperty("--climate-cold-symbol", (coldIntensity * 0.42).toFixed(3));
  elements.app.style.setProperty("--climate-hot-symbol", (heatIntensity * 0.38).toFixed(3));
  elements.app.style.setProperty("--climate-cold-glow", (coldIntensity * 0.48).toFixed(3));
  elements.app.style.setProperty("--climate-hot-glow", (heatIntensity * 0.44).toFixed(3));
}

function formatCurrentTemp(value) {
  const temp = Number(value);
  return Number.isFinite(temp) ? temp.toFixed(1) : "--";
}

function renderThermostat() {
  const t = state.thermostat;
  normalizeThermostatModeForLocks();
  const autoSwitchChanged = applyAutoSwitch({ notify: true });
  if (autoSwitchChanged) saveConfig();
  if (isPauseFunctionActive()) applyPauseFunctionTarget();
  const { min, max } = getModeLimits();
  const showingSetpoint = isSetpointPreviewActive();
  const currentRounded = Number.isFinite(Number(t.currentTemp)) ? Math.round(Number(t.currentTemp)) : "--";
  const currentDisplay = formatCurrentTemp(t.currentTemp);
  const targetRounded = Math.round(t.targetTemp);
  const outdoorRounded = Math.round(t.outdoorTemp);
  const outdoorWindRounded = Math.round(Number(t.outdoorWindSpeed || 0));
  const outdoorWindUnit = normalizeWeatherWindUnit(t.outdoorWindUnit || "mph");
  const outputs = getThermostatOutputs({ recordRuntime: true });
  const controlMode = outputs.controlMode;
  const now = Date.now();

  renderTemperatureAtmosphere(t.currentTemp);
  renderPanelLock();

  const dialModeLabel = outputs.cool
    ? "Cooling"
    : outputs.heat
      ? "Heating"
      : outputs.coolingFanHold
        ? "Fan Cooldown"
        : outputs.fan
          ? "Fan On"
          : t.away
            ? "Away"
            : t.mode === "auto"
              ? `Auto ${titleCase(controlMode === "locked" ? "locked" : controlMode)}`
              : titleCase(controlMode === "locked" ? "locked" : t.mode);

  elements.currentTemp.textContent = showingSetpoint ? targetRounded : currentRounded;
  elements.targetTemp.textContent = showingSetpoint ? currentDisplay : targetRounded;
  if (elements.dialTargetBadge) elements.dialTargetBadge.textContent = String(targetRounded);
  if (elements.primaryTempLabel) elements.primaryTempLabel.textContent = showingSetpoint ? "Set Temp" : dialModeLabel;
  if (elements.secondaryTempLabel) elements.secondaryTempLabel.textContent = showingSetpoint ? "Current" : "Set Temp";
  if (elements.headerCurrentTemp) elements.headerCurrentTemp.textContent = `${currentDisplay}°`;
  if (elements.headerSetTemp) elements.headerSetTemp.textContent = `${targetRounded}°`;
  if (elements.virtualTempValue) elements.virtualTempValue.textContent = `${currentDisplay}°`;
  if (elements.virtualTempSlider && document.activeElement !== elements.virtualTempSlider) elements.virtualTempSlider.value = String(clamp(Number(t.currentTemp), VIRTUAL_TEMP_MIN, VIRTUAL_TEMP_MAX));
  renderCurrentTempSourceSettings();
  if (elements.outdoorTempValue) elements.outdoorTempValue.textContent = `${outdoorRounded}°`;
  if (elements.outdoorTempTopValue) elements.outdoorTempTopValue.textContent = `${outdoorRounded}°`;
  if (elements.outdoorWindTopValue) elements.outdoorWindTopValue.textContent = `${outdoorWindRounded} ${outdoorWindUnit}`;
  if (elements.outdoorTempSlider && document.activeElement !== elements.outdoorTempSlider) elements.outdoorTempSlider.value = String(clamp(outdoorRounded, 40, 100));
  if (elements.headerSetPill) {
    elements.headerSetPill.classList.toggle("heat", controlMode === "heat" && !t.away);
    elements.headerSetPill.classList.toggle("cool", controlMode === "cool" && !t.away);
    elements.headerSetPill.classList.toggle("auto", t.mode === "auto" && !t.away);
    elements.headerSetPill.classList.toggle("away", t.away);
  }
  elements.humidityValue.textContent = `${Math.round(t.humidity)}%`;
  elements.awayHeatValue.textContent = t.awayHeat;
  elements.awayCoolValue.textContent = t.awayCool;
  if (elements.safetyLowValue) elements.safetyLowValue.textContent = Math.round(t.safetyLow);
  if (elements.safetyHighValue) elements.safetyHighValue.textContent = Math.round(t.safetyHigh);
  elements.fanSummary.textContent = titleCase(t.fan);
  document.getElementById("coolMinValue").textContent = t.limits.cool.min;
  document.getElementById("coolMaxValue").textContent = t.limits.cool.max;
  document.getElementById("heatMinValue").textContent = t.limits.heat.min;
  document.getElementById("heatMaxValue").textContent = t.limits.heat.max;
  if (elements.autoCoolOutdoorTargetValue) elements.autoCoolOutdoorTargetValue.textContent = Math.round(t.autoCoolOutdoorTarget);
  if (elements.autoHeatOutdoorTargetValue) elements.autoHeatOutdoorTargetValue.textContent = Math.round(t.autoHeatOutdoorTarget);
  if (elements.autoLockoutValue) elements.autoLockoutValue.textContent = `${Math.round(Math.max(AUTO_CHANGEOVER_MINUTES, t.autoChangeoverLockoutMinutes) / 60)} hr`;
  if (elements.coolFanRemainValue) elements.coolFanRemainValue.textContent = String(Math.round(t.coolFanRemainOnMinutes));
  elements.dialMinLabel.textContent = `${min}°`;
  elements.dialMaxLabel.textContent = `${max}°`;

  setDialVisual(t.targetTemp, t.currentTemp);
  elements.thermoDial.classList.toggle("heat", controlMode === "heat");
  elements.thermoDial.classList.toggle("auto", t.mode === "auto");
  elements.thermoDial.classList.toggle("setpoint-preview", showingSetpoint);
  elements.app.classList.toggle("away-active", t.away);
  elements.app.classList.toggle("heat-mode", controlMode === "heat" && !t.away);
  elements.app.classList.toggle("cool-mode", controlMode === "cool" && !t.away);
  elements.app.classList.toggle("auto-mode", t.mode === "auto" && !t.away);

  renderRelayStatus(elements.relayFan, outputs.fan);
  renderRelayStatus(elements.relayHeat, outputs.heat);
  renderRelayStatus(elements.relayCool, outputs.cool);
  renderRelayLockStatus(elements.relayHeat, Boolean(t.heatLocked));
  renderRelayLockStatus(elements.relayCool, Boolean(t.coolLocked));

  if (elements.safetyWarningBanner) {
    const safetyActive = Boolean(outputs.safetyMode);
    elements.safetyWarningBanner.classList.toggle("open", safetyActive);
    elements.safetyWarningBanner.classList.toggle("heat", outputs.safetyMode === "heat");
    elements.safetyWarningBanner.classList.toggle("cool", outputs.safetyMode === "cool");
    elements.safetyWarningBanner.setAttribute("aria-hidden", safetyActive ? "false" : "true");
    if (elements.safetyWarningTitle) elements.safetyWarningTitle.textContent = `Safety ${titleCase(outputs.safetyMode || "")} Engaged`;
    if (elements.safetyWarningSetpoint) elements.safetyWarningSetpoint.textContent = `${targetRounded}°`;
    if (elements.safetyWarningCurrent) elements.safetyWarningCurrent.textContent = `${currentDisplay}°`;
  }

  renderAutoSwitchNotice();
  renderPauseFunctionStatus();

  const action = outputs.cool ? "Cooling" : outputs.heat ? "Heating" : outputs.coolingFanHold ? "Fan Cooldown" : outputs.fan ? "Fan On" : (t.heatLocked && t.coolLocked ? "Heat/Cool Locked" : "Idle");
  const lockoutRemaining = Math.max(0, Number(t.autoLockoutUntil || 0) - now);
  const manualLockoutRemaining = Math.max(0, Number(t.manualLockoutUntil || 0) - now);
  const bypassState = getActiveChangeoverBypassState(now);
  if (outputs.safetyMode) {
    elements.runtimeState.textContent = `Safety ${titleCase(outputs.safetyMode)} • ${action}`;
  } else if (isPauseFunctionActive()) {
    elements.runtimeState.textContent = `Paused • ${action}`;
  } else if (t.away) {
    elements.runtimeState.textContent = `Away • ${action}`;
  } else if (t.mode === "auto" && t.autoPendingMode && lockoutRemaining > 0) {
    elements.runtimeState.textContent = `Auto • ${action} • ${titleCase(t.autoPendingMode)} locked ${formatLockoutTime(lockoutRemaining)}`;
  } else if (t.mode !== "auto" && t.manualPendingMode && manualLockoutRemaining > 0) {
    elements.runtimeState.textContent = `${titleCase(t.mode)} • ${titleCase(t.manualPendingMode)} delayed ${formatLockoutTime(manualLockoutRemaining)}`;
  } else if (t.mode === "auto") {
    elements.runtimeState.textContent = `Auto • ${titleCase(controlMode)} • ${action}`;
  } else {
    elements.runtimeState.textContent = action;
  }

  if (elements.changeoverBypassButton) {
    const showBypass = Boolean(bypassState);
    elements.changeoverBypassButton.hidden = !showBypass;
    elements.changeoverBypassButton.setAttribute("aria-hidden", showBypass ? "false" : "true");
    elements.changeoverBypassButton.disabled = !showBypass;
    elements.changeoverBypassButton.textContent = "Bypass";
    elements.changeoverBypassButton.title = bypassState
      ? `${titleCase(bypassState.pendingMode)} available in ${formatLockoutTime(bypassState.remaining)}. Tap to bypass.`
      : "";
  }

  if (elements.modeBadge) {
    const lockedLabel = t.heatLocked && t.coolLocked ? "Locked" : controlMode === "locked" ? "Locked" : "";
    const changeoverLabel = controlMode === "lockout" && bypassState ? `${titleCase(bypassState.pendingMode)} Delay` : "";
    elements.modeBadge.textContent = lockedLabel || changeoverLabel || (outputs.safetyMode ? `Safety ${titleCase(outputs.safetyMode)}` : t.away ? `${titleCase(controlMode)} Away` : t.mode === "auto" ? `Auto ${titleCase(controlMode)}` : `${titleCase(t.mode)} Target`);
    elements.modeBadge.className = `mode-badge ${lockedLabel ? "locked" : changeoverLabel ? "locked" : outputs.safetyMode ? `${outputs.safetyMode} safety` : t.mode === "auto" ? `${controlMode} auto` : t.mode}`;
  }
  elements.awayToggle.classList.toggle("active", t.away);
  elements.awayToggle.classList.toggle("home-state", t.away);
  elements.awayToggle.textContent = t.away ? "Home" : "Away";
  if (elements.awayModeOverlay) {
    const showAwayOverlay = t.away && !isPauseFunctionActive();
    elements.awayModeOverlay.classList.toggle("open", showAwayOverlay);
    elements.awayModeOverlay.setAttribute("aria-hidden", showAwayOverlay ? "false" : "true");
  }

  const visibleModeButtons = [];
  document.querySelectorAll(".mode-button[data-mode]").forEach((button) => {
    const mode = button.dataset.mode;
    const hiddenByLock = mode === "heat" ? t.heatLocked : mode === "cool" ? t.coolLocked : mode === "auto" ? t.heatLocked && t.coolLocked : false;
    button.hidden = hiddenByLock;
    button.disabled = hiddenByLock;
    button.classList.toggle("active", button.dataset.mode === t.mode && !hiddenByLock);
    if (!hiddenByLock) visibleModeButtons.push(button);
  });
  document.querySelectorAll(".mode-deck").forEach((deck) => deck.style.setProperty("--mode-count", String(Math.max(1, visibleModeButtons.length + 1))));
  document.querySelectorAll(".segment[data-fan]").forEach((button) => {
    button.classList.toggle("active", button.dataset.fan === t.fan);
    const forcedCooling = isCoolingFanForced(outputs) && button.dataset.fan === "off";
    button.disabled = forcedCooling;
    button.title = forcedCooling ? "Fan must stay on during cooling or cool fan delay" : "";
  });
  if (elements.alarmDisarmCodeInput && document.activeElement !== elements.alarmDisarmCodeInput) {
    elements.alarmDisarmCodeInput.value = state.alarm.disarmCode || "";
  }
  if (elements.thermostatNameInput && document.activeElement !== elements.thermostatNameInput) {
    elements.thermostatNameInput.value = getThermostatName();
  }
  renderPanelThemePicker();
  elements.app.classList.toggle("heat-locked", Boolean(t.heatLocked));
  elements.app.classList.toggle("cool-locked", Boolean(t.coolLocked));
  renderThermostatPeople();
  renderDoorWidget();
  renderAlarmWidget();
}


function normalizeAlarmStateText(value) {
  const text = String(value || "unknown").replace(/^armed_/, "armed ").replace(/_/g, " ").trim();
  return text ? text.replace(/\b\w/g, (char) => char.toUpperCase()) : "Unknown";
}

function isAlarmArmed(stateValue = state.alarm.state) {
  const value = String(stateValue || "").toLowerCase();
  return value === "armed" || value.startsWith("armed_") || value === "arming" || value === "pending" || value === "triggered";
}

function isAlarmDisarmed(stateValue = state.alarm.state) {
  return String(stateValue || "").toLowerCase() === "disarmed";
}

function isSettingsAllowedByAlarm() {
  if (state.alarm.disarming || state.alarm.arming || state.alarm.armAwayCountdown > 0) return false;
  const linked = Boolean(state.alarm.entityId || getAlarmConfig()?.entityId);
  if (!linked) return true;
  return isAlarmDisarmed();
}

function updateSettingsAccess() {
  if (!elements.settingsButton) return;
  const allowed = isSettingsAllowedByAlarm();
  elements.settingsButton.hidden = !allowed;
  elements.settingsButton.disabled = !allowed;
  elements.settingsButton.setAttribute("aria-hidden", allowed ? "false" : "true");
  if (!allowed && elements.settingsOverlay?.classList.contains("open")) closeSettings();
}

function getSavedAlarmCode() {
  return String(state.alarm.disarmCode || "").replace(/\D/g, "").slice(0, 8);
}

function getUserAccessCode() {
  const code = String(state.userAccessCode || "").replace(/\D/g, "").slice(0, 4);
  return code.length === 4 ? code : DEFAULT_USER_ACCESS_CODE;
}

function saveUserAccessCode(options = {}) {
  const raw = elements.userAccessCodeInput ? elements.userAccessCodeInput.value : state.userAccessCode;
  const code = String(raw || "").replace(/\D/g, "").slice(0, 4);
  if (code.length !== 4) {
    if (elements.userAccessCodeInput) elements.userAccessCodeInput.value = getUserAccessCode();
    if (options.toast !== false) showToast("Enter a 4-digit user access code");
    return false;
  }
  state.userAccessCode = code;
  if (elements.userAccessCodeInput) elements.userAccessCodeInput.value = code;
  saveConfig({ toast: false });
  if (options.toast !== false) showToast("User access code saved");
  renderSettingsCodePrompt();
  return true;
}

function getAlarmSubmitLength() {
  return clamp(getSavedAlarmCode().length || ALARM_AUTO_SUBMIT_LENGTH, 1, 8);
}

function saveAlarmCode(options = {}) {
  const raw = elements.alarmDisarmCodeInput ? elements.alarmDisarmCodeInput.value : state.alarm.disarmCode;
  const code = String(raw || "").replace(/\D/g, "").slice(0, 8);
  if (!code) {
    if (elements.alarmDisarmCodeInput) elements.alarmDisarmCodeInput.value = getSavedAlarmCode();
    if (options.toast !== false) showToast("Enter a disarm code");
    return false;
  }
  state.alarm.disarmCode = code;
  if (elements.alarmDisarmCodeInput) elements.alarmDisarmCodeInput.value = code;
  saveConfig({ toast: false });
  if (options.toast !== false) showToast("Disarm code saved");
  renderAlarmKeypad();
  return true;
}

function normalizeThermostatName(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 80) || "IHA Thermostat";
}

function getThermostatName() {
  return normalizeThermostatName(state.thermostat?.name);
}

function saveThermostatName(options = {}) {
  const raw = elements.thermostatNameInput ? elements.thermostatNameInput.value : state.thermostat.name;
  const name = normalizeThermostatName(raw);
  const changed = state.thermostat.name !== name;
  state.thermostat.name = name;
  state.systemInfo.thermostatName = name;
  if (elements.thermostatNameInput) elements.thermostatNameInput.value = name;
  renderSystemInfo();
  if (changed || options.force) saveConfig({ toast: false });
  if (options.toast !== false) showToast("Thermostat name saved");
  return true;
}

function getAlarmArmLabel(action) {
  return action === "arm_home" ? "Arm Home" : action === "arm_away" ? "Arm Away" : "Arm";
}

function getAlarmConfig() {
  const ha = state.integrations.homeAssistant;
  if (!Object.prototype.hasOwnProperty.call(ha, "alarmEntity")) ha.alarmEntity = null;
  return ha.alarmEntity || null;
}

function applyAlarmEntityState(entity = {}) {
  const config = getAlarmConfig();
  state.alarm.entityId = entity.entityId || config?.entityId || "";
  state.alarm.name = entity.name || config?.name || state.alarm.entityId || "Alarm";
  state.alarm.state = entity.state || config?.state || (state.alarm.entityId ? "unknown" : "unassigned");
  if (state.integrations.homeAssistant.alarmEntity && entity.entityId) {
    state.integrations.homeAssistant.alarmEntity = {
      ...state.integrations.homeAssistant.alarmEntity,
      ...entity,
      entityId: entity.entityId,
      name: entity.name || entity.entityId,
    };
  }
}

function syncAlarmFromConfig() {
  const config = getAlarmConfig();
  if (!config?.entityId) {
    state.alarm.entityId = "";
    state.alarm.name = "Alarm";
    state.alarm.state = "unassigned";
    return;
  }
  applyAlarmEntityState(config);
}

function renderAlarmWidget() {
  if (!elements.alarmWidget) return;
  const config = getAlarmConfig();
  if (!config?.entityId && !state.alarm.entityId) syncAlarmFromConfig();
  const linked = Boolean(state.alarm.entityId || config?.entityId);
  const alarmState = linked ? (state.alarm.state || config?.state || "unknown") : "unassigned";
  const armed = isAlarmArmed(alarmState);
  const triggered = String(alarmState).toLowerCase() === "triggered";
  const pending = ["arming", "pending"].includes(String(alarmState).toLowerCase());
  const disarmed = String(alarmState).toLowerCase() === "disarmed";

  elements.alarmWidget.classList.toggle("linked", linked);
  elements.alarmWidget.classList.toggle("unlinked", !linked);
  elements.alarmWidget.classList.toggle("armed", armed);
  elements.alarmWidget.classList.toggle("disarmed", linked && disarmed);
  elements.alarmWidget.classList.toggle("triggered", triggered);
  elements.alarmWidget.classList.toggle("pending", pending);
  elements.alarmWidget.classList.toggle("busy", state.alarm.disarming || state.alarm.arming || state.alarm.armAwayCountdown > 0);
  elements.alarmWidget.classList.toggle("counting-down", state.alarm.armAwayCountdown > 0);

  if (elements.alarmWidgetTitle) elements.alarmWidgetTitle.textContent = linked ? (state.alarm.name || config?.name || "Alarm") : "Alarm";
  if (elements.alarmWidgetState) {
    const countdownText = state.alarm.armAwayCountdown > 0 ? `Away in ${state.alarm.armAwayCountdown}s` : "";
    elements.alarmWidgetState.textContent = linked
      ? (countdownText || (state.alarm.disarming ? "Disarming…" : state.alarm.arming ? "Arming…" : normalizeAlarmStateText(alarmState)))
      : "Hold to assign";
  }
  elements.alarmWidget.setAttribute("aria-label", linked
    ? `Alarm ${normalizeAlarmStateText(alarmState)}. Press to ${isAlarmDisarmed(alarmState) ? "arm" : "disarm"}. Hold to assign.`
    : "Alarm not assigned. Hold to assign Home Assistant alarm.");
  updateSettingsAccess();
}

function getDoorConfig() {
  const ha = state.integrations.homeAssistant;
  if (!Object.prototype.hasOwnProperty.call(ha, "doorEntity")) ha.doorEntity = null;
  return ha.doorEntity || null;
}

function applyDoorEntityState(entity = {}) {
  const config = getDoorConfig();
  state.door.entityId = entity.entityId || config?.entityId || "";
  state.door.name = entity.name || config?.name || state.door.entityId || "Door";
  state.door.state = entity.state || config?.state || (state.door.entityId ? "unknown" : "unassigned");
  state.door.deviceClass = entity.deviceClass || config?.deviceClass || state.door.deviceClass || "door";
  if (state.integrations.homeAssistant.doorEntity && entity.entityId) {
    state.integrations.homeAssistant.doorEntity = {
      ...state.integrations.homeAssistant.doorEntity,
      ...entity,
      entityId: entity.entityId,
      name: entity.name || entity.entityId,
      deviceClass: entity.deviceClass || state.door.deviceClass || "door",
    };
  }
}

function syncDoorFromConfig() {
  const config = getDoorConfig();
  if (!config?.entityId) {
    state.door.entityId = "";
    state.door.name = "Door";
    state.door.state = "unassigned";
    state.door.deviceClass = "door";
    return;
  }
  applyDoorEntityState(config);
}

function normalizeDoorStateText(value) {
  const raw = String(value || "unknown").toLowerCase();
  if (["on", "open", "opened", "opening", "detected"].includes(raw)) return "Open";
  if (["off", "closed", "closing", "clear"].includes(raw)) return "Closed";
  if (["unavailable", "unknown"].includes(raw)) return "Unknown";
  return titleCase(raw.replace(/_/g, " "));
}

function isDoorOpen(value = state.door.state) {
  return ["on", "open", "opened", "opening", "detected"].includes(String(value || "").toLowerCase());
}

function isDoorClosed(value = state.door.state) {
  return ["off", "closed", "closing", "clear"].includes(String(value || "").toLowerCase());
}

function renderDoorWidget() {
  if (!elements.doorWidget) return;
  const config = getDoorConfig();
  if (!config?.entityId && !state.door.entityId) syncDoorFromConfig();
  const linked = Boolean(state.door.entityId || config?.entityId);
  const doorState = linked ? (state.door.state || config?.state || "unknown") : "unassigned";
  const open = linked && isDoorOpen(doorState);
  const closed = linked && isDoorClosed(doorState);
  const unknown = linked && !open && !closed;

  elements.doorWidget.classList.toggle("linked", linked);
  elements.doorWidget.classList.toggle("unlinked", !linked);
  elements.doorWidget.classList.toggle("open", open);
  elements.doorWidget.classList.toggle("closed", closed);
  elements.doorWidget.classList.toggle("unknown", unknown);
  if (elements.doorWidgetTitle) elements.doorWidgetTitle.textContent = linked ? (state.door.name || config?.name || "Door") : "Door";
  if (elements.doorWidgetState) elements.doorWidgetState.textContent = linked ? normalizeDoorStateText(doorState) : "Tap to assign";
  elements.doorWidget.setAttribute("aria-label", linked
    ? `Door ${normalizeDoorStateText(doorState)}. Hold to assign a different Home Assistant binary sensor.`
    : "Door not assigned. Tap or hold to assign Home Assistant binary sensor.");
}

async function fetchDoorStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const payload = await fetchJsonWithTimeout("/api/ha/binary_sensor/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  }, 9000);
  return payload.sensors || [];
}

function scheduleDoorSync() {
  HA_ALARM_SYNC_AFTER_COMMAND_DELAYS.forEach((delay) => {
    window.setTimeout(() => pollHomeAssistantDoor({ force: true }), delay);
  });
}

async function pollHomeAssistantDoor(options = {}) {
  const config = getDoorConfig();
  const entityId = state.door.entityId || config?.entityId || "";
  if (!entityId) return;
  if (!options.force && document.visibilityState === "hidden") return;
  const now = Date.now();
  if (!options.force && state.currentPage !== "thermostat") {
    if (now - lastInactiveDoorSyncAt < INACTIVE_PAGE_SYNC_INTERVAL_MS) return;
    lastInactiveDoorSyncAt = now;
  }
  if (!options.force && state.currentPage === "thermostat" && now - lastDoorUserInteractionAt < 1200) return;
  if (haDoorSyncInFlight) return;

  haDoorSyncInFlight = true;
  try {
    const sensors = await fetchDoorStatesViaLocalBackend([entityId]);
    if (sensors[0]) {
      applyDoorEntityState(sensors[0]);
      saveConfig();
    }
    renderDoorWidget();
    if (haDoorSyncLastError) haDoorSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haDoorSyncLastError) {
      haDoorSyncLastError = message;
      addHaLog("warn", "Live door sync paused", message);
    }
  } finally {
    haDoorSyncInFlight = false;
  }
}

function openDoorPanel() {
  if (!state.door.entityId && getDoorConfig()?.entityId) syncDoorFromConfig();
  if (!state.door.entityId) {
    openAudioEntityPicker("door");
    return;
  }
  showToast(`Door ${normalizeDoorStateText(state.door.state)}`);
}

async function fetchAlarmStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const payload = await fetchJsonWithTimeout("/api/ha/alarm/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  }, 9000);
  return payload.alarms || [];
}

function scheduleAlarmSync() {
  HA_ALARM_SYNC_AFTER_COMMAND_DELAYS.forEach((delay) => {
    window.setTimeout(() => pollHomeAssistantAlarm({ force: true }), delay);
  });
}

async function pollHomeAssistantAlarm(options = {}) {
  const config = getAlarmConfig();
  const entityId = state.alarm.entityId || config?.entityId || "";
  if (!entityId) return;
  if (!options.force && document.visibilityState === "hidden") return;
  const now = Date.now();
  if (!options.force && state.currentPage !== "thermostat") {
    if (now - lastInactiveAlarmSyncAt < INACTIVE_PAGE_SYNC_INTERVAL_MS) return;
    lastInactiveAlarmSyncAt = now;
  }
  if (!options.force && state.currentPage === "thermostat" && now - lastAlarmUserInteractionAt < 1200) return;
  if (haAlarmSyncInFlight || state.alarm.disarming) return;

  haAlarmSyncInFlight = true;
  try {
    const alarms = await fetchAlarmStatesViaLocalBackend([entityId]);
    if (alarms[0]) {
      applyAlarmEntityState(alarms[0]);
      saveConfig();
    }
    renderAlarmWidget();
    if (haAlarmSyncLastError) haAlarmSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haAlarmSyncLastError) {
      haAlarmSyncLastError = message;
      addHaLog("warn", "Live alarm sync paused", message);
    }
  } finally {
    haAlarmSyncInFlight = false;
  }
}

function openAlarmPanel() {
  if (!state.alarm.entityId && getAlarmConfig()?.entityId) syncAlarmFromConfig();
  if (!state.alarm.entityId) {
    openAudioEntityPicker("alarm");
    return;
  }
  if (isAlarmDisarmed()) {
    openAlarmArmOptions();
    return;
  }
  openAlarmKeypad();
}

function openAlarmKeypad() {
  if (!state.alarm.entityId && getAlarmConfig()?.entityId) syncAlarmFromConfig();
  if (!state.alarm.entityId) {
    openAudioEntityPicker("alarm");
    return;
  }
  if (isAlarmDisarmed()) {
    openAlarmArmOptions();
    return;
  }
  closeAlarmArmOptions({ keepCountdown: true });
  state.alarm.code = "";
  elements.alarmKeypadOverlay?.classList.add("open");
  elements.alarmKeypadOverlay?.setAttribute("aria-hidden", "false");
  renderAlarmKeypad();
}

function closeAlarmKeypad() {
  if (state.alarm.disarming) return;
  state.alarm.code = "";
  elements.alarmKeypadOverlay?.classList.remove("open");
  elements.alarmKeypadOverlay?.setAttribute("aria-hidden", "true");
  renderAlarmKeypad();
}

function renderAlarmKeypad() {
  if (!elements.alarmCodeDots) return;
  const submitLength = getAlarmSubmitLength();
  const codeLength = Math.max(submitLength, state.alarm.code.length || 0);
  elements.alarmCodeDots.innerHTML = Array.from({ length: Math.min(Math.max(codeLength, submitLength), 8) }, (_, index) =>
    `<span class="${index < state.alarm.code.length ? "filled" : ""}"></span>`
  ).join("");
  if (elements.alarmKeypadTitle) elements.alarmKeypadTitle.textContent = `Disarm ${state.alarm.name || "Alarm"}`;
  if (elements.alarmKeypadStatus) {
    elements.alarmKeypadStatus.textContent = state.alarm.disarming ? "Sending disarm command…" : "Enter code to disarm.";
  }
  elements.alarmKeypadOverlay?.classList.toggle("busy", state.alarm.disarming);
  elements.alarmKeypadGrid?.querySelectorAll("button").forEach((button) => { button.disabled = state.alarm.disarming; });
  if (elements.alarmDisarmButton) elements.alarmDisarmButton.disabled = state.alarm.disarming || !state.alarm.code.length;
  renderAlarmWidget();
}

function handleAlarmKey(value) {
  if (state.alarm.disarming) return;
  if (value === "clear") state.alarm.code = "";
  else if (value === "back") state.alarm.code = state.alarm.code.slice(0, -1);
  else if (/^\d$/.test(value) && state.alarm.code.length < 8) state.alarm.code += value;
  renderAlarmKeypad();
  if (state.alarm.code.length >= getAlarmSubmitLength()) sendAlarmDisarm(state.alarm.code);
}

function renderAlarmArmOptions() {
  if (!elements.alarmArmOverlay) return;
  const counting = state.alarm.armAwayCountdown > 0;
  const busy = state.alarm.arming || counting;
  const modeLabel = getAlarmArmLabel(state.alarm.armMode);
  if (elements.alarmArmTitle) elements.alarmArmTitle.textContent = counting ? "Arming Away" : "Arm Alarm";
  if (elements.alarmArmStatus) {
    elements.alarmArmStatus.textContent = counting
      ? "Leave now. The alarm will arm away when the countdown reaches zero."
      : state.alarm.arming
        ? `Sending ${modeLabel.toLowerCase()} command…`
        : "Choose how you want to arm the system.";
  }
  if (elements.alarmArmCountdown) {
    elements.alarmArmCountdown.hidden = !counting;
    elements.alarmArmCountdown.textContent = String(state.alarm.armAwayCountdown || ALARM_ARM_AWAY_DELAY_SECONDS);
  }
  elements.alarmArmOverlay.classList.toggle("counting", counting);
  elements.alarmArmOverlay.classList.toggle("busy", busy);
  [elements.alarmArmHomeButton, elements.alarmArmAwayButton].forEach((button) => { if (button) button.disabled = busy; });
  if (elements.alarmArmCancelButton) {
    elements.alarmArmCancelButton.disabled = state.alarm.arming && !counting;
    elements.alarmArmCancelButton.textContent = counting ? "Cancel Countdown" : "Cancel";
  }
  renderAlarmWidget();
}

function openAlarmArmOptions() {
  if (!state.alarm.entityId && getAlarmConfig()?.entityId) syncAlarmFromConfig();
  if (!state.alarm.entityId) {
    openAudioEntityPicker("alarm");
    return;
  }
  if (!isAlarmDisarmed()) {
    openAlarmKeypad();
    return;
  }
  closeAlarmKeypad();
  elements.alarmArmOverlay?.classList.add("open");
  elements.alarmArmOverlay?.setAttribute("aria-hidden", "false");
  renderAlarmArmOptions();
}

function closeAlarmArmOptions(options = {}) {
  if (!options.keepCountdown) cancelAlarmAwayCountdown({ silent: true });
  elements.alarmArmOverlay?.classList.remove("open", "counting", "busy");
  elements.alarmArmOverlay?.setAttribute("aria-hidden", "true");
  renderAlarmArmOptions();
}

function cancelAlarmAwayCountdown(options = {}) {
  if (alarmArmAwayTimer) window.clearInterval(alarmArmAwayTimer);
  alarmArmAwayTimer = null;
  const hadCountdown = state.alarm.armAwayCountdown > 0;
  state.alarm.armAwayCountdown = 0;
  if (state.alarm.armMode === "arm_away" && !state.alarm.arming) state.alarm.armMode = "";
  if (hadCountdown && !options.silent) showToast("Arm away canceled");
  renderAlarmArmOptions();
}

function startAlarmAwayCountdown() {
  if (state.alarm.arming || state.alarm.armAwayCountdown > 0) return;
  state.alarm.armMode = "arm_away";
  state.alarm.armAwayCountdown = ALARM_ARM_AWAY_DELAY_SECONDS;
  elements.alarmArmOverlay?.classList.add("open");
  elements.alarmArmOverlay?.setAttribute("aria-hidden", "false");
  renderAlarmArmOptions();
  if (alarmArmAwayTimer) window.clearInterval(alarmArmAwayTimer);
  alarmArmAwayTimer = window.setInterval(() => {
    state.alarm.armAwayCountdown = Math.max(0, state.alarm.armAwayCountdown - 1);
    renderAlarmArmOptions();
    if (state.alarm.armAwayCountdown <= 0) {
      window.clearInterval(alarmArmAwayTimer);
      alarmArmAwayTimer = null;
      sendAlarmArm("arm_away");
    }
  }, 1000);
}

async function callAlarmActionViaLocalBackend(action, code = "") {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  const entityId = state.alarm.entityId || getAlarmConfig()?.entityId || "";
  if (!baseUrl || !ha.token || !entityId) throw new Error("Missing Home Assistant alarm config");
  const payload = await fetchJsonWithTimeout("/api/ha/alarm/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityId, action, code }),
  }, 12000);
  return payload.alarm || null;
}

async function sendAlarmDisarm(code = state.alarm.code) {
  if (state.alarm.disarming) return;
  if (!state.alarm.entityId && getAlarmConfig()?.entityId) syncAlarmFromConfig();
  if (!state.alarm.entityId) {
    showToast("Hold the alarm tile to assign an alarm first");
    return;
  }
  const enteredCode = String(code || "").replace(/\D/g, "");
  const savedCode = getSavedAlarmCode();
  if (savedCode && enteredCode !== savedCode) {
    state.alarm.code = "";
    renderAlarmKeypad();
    showToast("Incorrect disarm code");
    return;
  }
  const commandCode = savedCode || enteredCode;
  lastAlarmUserInteractionAt = Date.now();
  state.alarm.disarming = true;
  renderAlarmKeypad();
  renderAlarmWidget();
  try {
    const alarm = await callAlarmActionViaLocalBackend("disarm", commandCode);
    if (alarm?.entityId) applyAlarmEntityState(alarm);
    else state.alarm.state = "disarmed";
    state.alarm.code = "";
    state.alarm.disarming = false;
    closeAlarmKeypad();
    saveConfig({ toast: false });
    showToast("Alarm disarmed");
    scheduleAlarmSync();
  } catch (error) {
    state.alarm.disarming = false;
    state.alarm.code = "";
    renderAlarmKeypad();
    addHaLog("error", "Alarm disarm failed", error.message || String(error));
    showToast("Alarm disarm failed");
  } finally {
    renderAlarmWidget();
  }
}

async function sendAlarmArm(action) {
  if (state.alarm.arming || state.alarm.disarming) return;
  if (!state.alarm.entityId && getAlarmConfig()?.entityId) syncAlarmFromConfig();
  if (!state.alarm.entityId) {
    showToast("Hold the alarm tile to assign an alarm first");
    return;
  }
  if (!isAlarmDisarmed()) {
    closeAlarmArmOptions();
    openAlarmKeypad();
    return;
  }
  cancelAlarmAwayCountdown({ silent: true });
  lastAlarmUserInteractionAt = Date.now();
  state.alarm.arming = true;
  state.alarm.armMode = action;
  renderAlarmArmOptions();
  renderAlarmWidget();
  try {
    const alarm = await callAlarmActionViaLocalBackend(action, getSavedAlarmCode());
    if (alarm?.entityId) applyAlarmEntityState(alarm);
    else state.alarm.state = action === "arm_home" ? "armed_home" : "armed_away";
    state.alarm.arming = false;
    state.alarm.armMode = "";
    closeAlarmArmOptions({ keepCountdown: true });
    saveConfig({ toast: false });
    showToast(action === "arm_home" ? "Alarm armed home" : "Alarm armed away");
    scheduleAlarmSync();
  } catch (error) {
    state.alarm.arming = false;
    state.alarm.armMode = "";
    renderAlarmArmOptions();
    addHaLog("error", `${getAlarmArmLabel(action)} failed`, error.message || String(error));
    showToast(`${getAlarmArmLabel(action)} failed`);
  } finally {
    renderAlarmWidget();
  }
}

function shouldConfirmAutoMode(mode) {
  return String(mode || "").toLowerCase() === "auto" && (state.thermostat.mode !== "auto" || state.thermostat.away);
}

function openAutoConfirmOverlay() {
  setOverlayOpen(elements.autoConfirmOverlay, true);
}

function closeAutoConfirmOverlay() {
  setOverlayOpen(elements.autoConfirmOverlay, false);
}

function confirmAutoMode() {
  closeAutoConfirmOverlay();
  setMode("auto", { confirmed: true });
}

function setMode(mode, options = {}) {
  const t = state.thermostat;
  if (!["cool", "heat", "auto"].includes(mode)) return;
  if (isThermostatModeLocked(mode) || (mode === "auto" && t.heatLocked && t.coolLocked)) {
    showToast(`${titleCase(mode)} is locked out`);
    renderThermostat();
    return;
  }
  if (shouldConfirmAutoMode(mode) && !options.confirmed) {
    openAutoConfirmOverlay();
    return;
  }
  t.mode = getAllowedThermostatMode(mode, t.mode);
  clearAutoSwitchHold();
  applyAutoSwitch({ notify: true });
  if (t.away) {
    applyAwayTarget();
  } else {
    const { min, max } = getModeLimits();
    t.targetTemp = clamp(t.targetTemp, min, max);
    t.lastComfortTarget = clamp(t.lastComfortTarget, min, max);
  }
  renderThermostat();
  saveConfig();
  showToast(`${titleCase(t.mode)} mode selected`);
}

function adjustSetpoint(delta) {
  setTargetTemp(state.thermostat.targetTemp + delta, { preview: true });
}

function toggleAway() {
  const t = state.thermostat;
  if (t.away) {
    setHomeMode();
    return;
  }
  t.away = true;
  t.awaySource = "manual";
  t.manualAwayPresenceLatch = createManualAwayPresenceLatch();
  t.lastComfortTarget = t.targetTemp;
  applyAwayTarget();
  renderThermostat();
  saveConfig();
  showToast("Away mode active");
}

function setAwayTemp(kind, delta) {
  const t = state.thermostat;
  if (kind === "heat") t.awayHeat = clamp(Math.round(t.awayHeat + delta), ABS_MIN, Math.min(72, t.awayCool - 1));
  if (kind === "cool") t.awayCool = clamp(Math.round(t.awayCool + delta), Math.max(72, t.awayHeat + 1), ABS_MAX);
  if (t.away) applyAwayTarget();
  if (isPauseFunctionActive()) applyPauseFunctionTarget();
  renderThermostat();
  saveConfig();
}

function adjustSafetyRange(bound, delta) {
  const t = state.thermostat;
  if (bound === "low") t.safetyLow = clamp(Math.round(Number(t.safetyLow || 55) + delta), ABS_MIN, t.safetyHigh - 2);
  if (bound === "high") t.safetyHigh = clamp(Math.round(Number(t.safetyHigh || 85) + delta), t.safetyLow + 2, ABS_MAX);
  renderThermostat();
  saveConfig();
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
  saveConfig();
}

function adjustAutoSetting(kind, delta) {
  const t = state.thermostat;
  if (kind === "coolTarget") {
    t.autoCoolOutdoorTarget = clamp(Math.round(t.autoCoolOutdoorTarget + delta), t.autoHeatOutdoorTarget + 1, 100);
  }
  if (kind === "heatTarget") {
    t.autoHeatOutdoorTarget = clamp(Math.round(t.autoHeatOutdoorTarget + delta), 40, t.autoCoolOutdoorTarget - 1);
  }
  if (kind === "coolFanRemain") {
    t.coolFanRemainOnMinutes = clamp(Math.round(t.coolFanRemainOnMinutes + delta), 0, 10);
    if (t.coolFanRemainOnMinutes === 0) t.coolFanHoldUntil = 0;
  }
  t.autoHeatOutdoorTarget = Math.min(t.autoHeatOutdoorTarget, t.autoCoolOutdoorTarget - 1);
  applyAutoSwitch({ notify: true });
  if (t.away) applyAwayTarget();
  renderThermostat();
  saveConfig();
}


function renderSettingsCodePrompt() {
  if (!elements.settingsCodeDots) return;
  const target = state.settingsAccessTarget || "settings";
  if (elements.settingsCodeTitle) {
    elements.settingsCodeTitle.textContent = target === "panelUnlock" ? "Unlock Panel" : target === "info" ? "Panel Info" : "Enter Code";
  }
  const code = state.settingsAccessCode || "";
  const requiredLength = getUserAccessCode().length || 4;
  elements.settingsCodeDots.innerHTML = Array.from({ length: requiredLength }, (_, index) =>
    `<span class="${index < code.length ? "filled" : ""}"></span>`
  ).join("");
}

function openSettingsCodePrompt(target = "settings") {
  const accessTarget = target === "panelUnlock" ? "panelUnlock" : target === "info" ? "info" : "settings";
  if (accessTarget === "settings" && isPanelLocked()) {
    showToast("Panel locked. Enter user access code to unlock.");
    return openSettingsCodePrompt("panelUnlock");
  }
  if (accessTarget === "settings" && !isSettingsAllowedByAlarm()) {
    showToast("Disarm the alarm before opening settings");
    return;
  }
  state.settingsAccessCode = "";
  state.settingsAccessTarget = accessTarget;
  if (elements.settingsCodeStatus) {
    elements.settingsCodeStatus.dataset.error = "";
    elements.settingsCodeStatus.textContent = accessTarget === "panelUnlock" ? "Enter user access code to unlock navigation." : "";
  }
  elements.settingsCodeOverlay?.classList.add("open");
  elements.settingsCodeOverlay?.setAttribute("aria-hidden", "false");
  renderSettingsCodePrompt();
}

function closeSettingsCodePrompt() {
  state.settingsAccessCode = "";
  elements.settingsCodeOverlay?.classList.remove("open");
  elements.settingsCodeOverlay?.setAttribute("aria-hidden", "true");
  if (elements.settingsCodeStatus) {
    elements.settingsCodeStatus.dataset.error = "";
    elements.settingsCodeStatus.textContent = "";
  }
  renderSettingsCodePrompt();
}

function verifySettingsCode() {
  const requiredCode = getUserAccessCode();
  if ((state.settingsAccessCode || "").length !== requiredCode.length) return;
  if (state.settingsAccessCode === requiredCode) {
    const target = state.settingsAccessTarget || "settings";
    closeSettingsCodePrompt();
    if (target === "panelUnlock") setPanelLocked(false);
    else if (target === "info") openThermostatInfo();
    else openSettings();
    return;
  }
  state.settingsAccessCode = "";
  if (elements.settingsCodeStatus) {
    elements.settingsCodeStatus.dataset.error = "1";
    elements.settingsCodeStatus.textContent = "Incorrect code. Try again.";
  }
  renderSettingsCodePrompt();
}

function handleSettingsCodeKey(value) {
  const current = state.settingsAccessCode || "";
  if (value === "clear") state.settingsAccessCode = "";
  else if (value === "back") state.settingsAccessCode = current.slice(0, -1);
  else if (/^\d$/.test(value) && current.length < getUserAccessCode().length) state.settingsAccessCode = current + value;
  if (elements.settingsCodeStatus) {
    elements.settingsCodeStatus.dataset.error = "";
    elements.settingsCodeStatus.textContent = "";
  }
  renderSettingsCodePrompt();
  if ((state.settingsAccessCode || "").length === getUserAccessCode().length) verifySettingsCode();
}

function hideAllSettingsViews() {
  [elements.thermostatSettingsView, elements.hardwareSettingsView, elements.historySettingsView, elements.blindSettingsView, elements.audioSettingsView, elements.lightsSettingsView, elements.roomControlSettingsView].forEach((view) => { if (view) view.hidden = true; });
  elements.settingsSheet.classList.remove("full-setup", "ha-focus", "thermostat-setup", "hardware-setup", "history-setup", "room-control-setup");
  elements.settingsFooter.hidden = false;
}

function openSettings() {
  if (!isSettingsAllowedByAlarm()) {
    showToast("Disarm the alarm before opening settings");
    return;
  }
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
  } else if (state.currentPage === "lights") {
    elements.lightsSettingsView.hidden = false;
    showLightsSetupView();
  } else if (state.currentPage === "room") {
    elements.roomControlSettingsView.hidden = false;
    showRoomControlSetupView();
  } else {
    showComfortSetupView();
  }
  elements.settingsOverlay.classList.add("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "false");
}

function closeSettings() {
  if (state.currentPage === "thermostat") {
    if (elements.alarmDisarmCodeInput) saveAlarmCode({ toast: false });
    if (elements.userAccessCodeInput) saveUserAccessCode({ toast: false });
    if (elements.thermostatNameInput) saveThermostatName({ toast: false });
    if (elements.screenTimeoutMinutesInput) setScreenTimeoutMinutes(elements.screenTimeoutMinutesInput.value, { save: false });
    saveConfig();
  }
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

function showLightsSetupView() {
  if (elements.lightsSetupView) elements.lightsSetupView.hidden = false;
  if (elements.lightsHaView) elements.lightsHaView.hidden = true;
  elements.settingsSheet.classList.add("full-setup");
  elements.settingsSheet.classList.remove("ha-focus");
  elements.settingsTitle.textContent = "Lights Setup";
  elements.settingsEyebrow.textContent = "Light Setup";
  elements.settingsFooter.hidden = false;
  renderLightConfigList();
}

function showLightsHaView() {
  if (elements.lightsSetupView) elements.lightsSetupView.hidden = true;
  if (elements.lightsHaView) elements.lightsHaView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "ha-focus");
  elements.settingsTitle.textContent = "Home Assistant Config";
  elements.settingsEyebrow.textContent = "Lights";
  elements.settingsFooter.hidden = true;
  renderHaFields("lights");
}

function showRoomControlSetupView() {
  if (elements.roomControlSetupView) elements.roomControlSetupView.hidden = false;
  elements.settingsSheet.classList.add("full-setup", "room-control-setup");
  elements.settingsSheet.classList.remove("ha-focus");
  elements.settingsTitle.textContent = "Room Setup";
  elements.settingsEyebrow.textContent = "Room Control";
  elements.settingsFooter.hidden = false;
  renderRoomControlConfigList();
}

function renderHaFields(context = "blinds") {
  const ha = state.integrations.homeAssistant;
  const fieldMap = {
    audio: { url: elements.audioHaUrlInput, token: elements.audioHaTokenInput },
    lights: { url: elements.lightHaUrlInput, token: elements.lightHaTokenInput },
    blinds: { url: elements.haUrlInput, token: elements.haTokenInput },
  };
  const fields = fieldMap[context] || fieldMap.blinds;
  if (fields.url) fields.url.value = ha.url || "";
  if (fields.token) fields.token.value = ha.token || "";
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

function isAudioSliderUserLocked(name) {
  if (isAudioSliderActive(name)) return true;
  if (name === "volume" && Date.now() < audioVolumeHoldUntil) return true;
  if (["gain", "bass", "treble"].includes(name) && Date.now() < (audioToneHoldUntil[name] || 0)) return true;
  return false;
}

function updateAudioValueLabel(name) {
  const value = document.getElementById(`${name}Value`);
  if (!value) return;
  value.textContent = name === "volume"
    ? Math.round(Number(state.audio.volume || 0))
    : formatControlValue(state.audio[name]);
}

function getAudioToneControls() {
  const ha = state.integrations.homeAssistant;
  if (!ha.audioControlEntities) ha.audioControlEntities = {};
  ["gain", "bass", "treble", "subwoofer", "surround", "projector"].forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(ha.audioControlEntities, key)) ha.audioControlEntities[key] = null;
  });
  if (!ha.audioAvailableEntities) ha.audioAvailableEntities = { mediaPlayers: [], numbers: [], switches: [] };
  return ha.audioControlEntities;
}

function getAudioToneControl(kind) {
  return getAudioToneControls()[kind] || null;
}

function getAudioSwitchControl(kind) {
  return getAudioToneControls()[kind] || null;
}

function isSwitchOn(control) {
  return String(control?.state || "").toLowerCase() === "on";
}

function getAudioToneLimit(kind, bound, fallback) {
  const control = getAudioToneControl(kind);
  const number = Number(control?.[bound]);
  return Number.isFinite(number) ? number : fallback;
}

function clampAudioTonePresetValue(kind, value) {
  const min = getAudioToneLimit(kind, "min", -10);
  const max = getAudioToneLimit(kind, "max", 10);
  if (value === "max") return max;
  if (value === "min") return min;
  const numeric = Number(value);
  return clamp(Number.isFinite(numeric) ? numeric : 0, min, max);
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
  const wasAudioDefault = shouldDefaultToAudioScreen();
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
  if (!isAudioSliderUserLocked("volume")) {
    state.audio.volume = normalizeVolume(entity, state.audio.volume);
  }
  state.audio.mediaPosition = entity.mediaPosition ?? null;
  state.audio.mediaDuration = entity.mediaDuration ?? null;
  maybeResolveAudioTrackLock(entity);
  renderScreenTimeoutSettings();
  maybeAutoShowAudioOnPlayback(wasAudioDefault);
  maybeApplyStartupDefaultScreen();
  scheduleDefaultScreenCheck();
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

  const trackLocked = isAudioTrackLocked() || audioMediaActionInFlight;
  if (elements.prevTrack) elements.prevTrack.disabled = trackLocked;
  if (elements.nextTrack) elements.nextTrack.disabled = trackLocked;
  if (elements.playPause) elements.playPause.disabled = audioMediaActionInFlight || Date.now() < audioPlaybackLockedUntil;

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
      if (control && !isAudioSliderUserLocked(name)) {
        state.audio[name] = normalizeAudioControlValue(control, state.audio[name]);
      }
    }

    if (!isAudioSliderUserLocked(name)) slider.value = a[name];
    setRangeVisual(slider);
    if (value) value.textContent = name === "volume" ? Math.round(Number(a[name] || 0)) : formatControlValue(a[name]);
  });
  document.querySelectorAll("[data-audio-preset]").forEach((button) => {
    button.disabled = audioPresetInFlight;
    button.classList.toggle("busy", audioPresetInFlight);
  });
  renderAudioFeatureControls();
}

function renderAudioFeatureControls() {
  ["subwoofer", "surround", "projector"].forEach((kind) => {
    const button = elements[`${kind}Toggle`];
    if (!button) return;
    const control = getAudioSwitchControl(kind);
    const linked = Boolean(control?.entityId);
    const on = isSwitchOn(control);
    button.classList.toggle("linked", linked);
    button.classList.toggle("on", on);
    button.classList.toggle("unlinked", !linked);
    button.title = linked ? `${control.name || control.entityId}: ${on ? "On" : "Off"}` : `Hold to assign ${kind}`;
    button.setAttribute("aria-pressed", on ? "true" : "false");
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

async function fetchHaEntitiesViaLocalBackend(domains = []) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant URL or token");
  const payload = await fetchJsonWithTimeout("/api/ha/entities", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, domains }),
  }, 12000);
  return payload.entities || [];
}

async function fetchAudioSwitchStatesViaLocalBackend() {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  const controls = getAudioToneControls();
  const hasControls = ["subwoofer", "surround", "projector"].some((kind) => controls[kind]?.entityId);
  if (!baseUrl || !ha.token || !hasControls) return {};
  const payload = await fetchJsonWithTimeout("/api/ha/audio/switch_states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, controls }),
  }, 9000);
  return payload.controls || {};
}

function applyAudioSwitchControls(controls = {}) {
  const target = getAudioToneControls();
  let changed = false;
  ["subwoofer", "surround", "projector"].forEach((kind) => {
    if (!Object.prototype.hasOwnProperty.call(controls, kind)) return;
    const next = controls[kind];
    if (!next) return;
    const before = JSON.stringify(target[kind] || null);
    target[kind] = next;
    state.audio[kind] = isSwitchOn(next);
    changed = changed || before !== JSON.stringify(next);
  });
  if (changed) saveConfig();
  return changed;
}

async function sendAudioSwitchAction(kind) {
  const control = getAudioSwitchControl(kind);
  if (!control?.entityId) {
    showToast(`Hold ${kind} to assign a switch`);
    return null;
  }
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) {
    showToast("Add Home Assistant config first");
    return null;
  }
  if (audioMediaActionInFlight) {
    showToast("Audio command in progress…");
    return null;
  }
  audioMediaActionInFlight = true;
  lastAudioUserInteractionAt = Date.now();
  renderAudio();
  try {
    const payload = await fetchJsonWithTimeout("/api/ha/audio/switch/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: baseUrl, token: ha.token, entityId: control.entityId, action: "toggle" }),
    }, 10000);
    if (payload.control) {
      getAudioToneControls()[kind] = payload.control;
      state.audio[kind] = isSwitchOn(payload.control);
      saveConfig();
    }
    scheduleAudioSync();
    return payload.control;
  } catch (error) {
    addHaLog("error", `${titleCase(kind)} switch failed`, error.message || String(error));
    showToast(`${titleCase(kind)} command failed`);
    return null;
  } finally {
    audioMediaActionInFlight = false;
    renderAudio();
  }
}

async function setAudioSwitchState(kind, shouldTurnOn) {
  const control = getAudioSwitchControl(kind);
  state.audio[kind] = Boolean(shouldTurnOn);
  if (!control?.entityId) {
    saveConfig();
    return null;
  }
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) throw new Error("Missing Home Assistant config");
  const payload = await fetchJsonWithTimeout("/api/ha/audio/switch/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityId: control.entityId, action: shouldTurnOn ? "on" : "off" }),
  }, 10000);
  if (payload.control) {
    payload.control.state = shouldTurnOn ? "on" : "off";
    getAudioToneControls()[kind] = payload.control;
  }
  state.audio[kind] = Boolean(shouldTurnOn);
  return payload.control || null;
}

async function applyAudioPreset(presetKey) {
  const preset = AUDIO_PRESETS[presetKey];
  if (!preset) return;
  if (!state.integrations.homeAssistant.selectedMediaPlayerId) {
    showToast("Select an audio device first");
    return;
  }
  if (audioPresetInFlight || audioMediaActionInFlight) {
    showToast("Audio command in progress…");
    return;
  }

  const toneValues = {
    gain: clampAudioTonePresetValue("gain", preset.gain),
    bass: clampAudioTonePresetValue("bass", preset.bass),
    treble: clampAudioTonePresetValue("treble", preset.treble),
  };
  const volume = clamp(Number(preset.volume), 0, 100);

  audioPresetInFlight = true;
  audioMediaActionInFlight = true;
  lastAudioUserInteractionAt = Date.now();
  audioVolumeHoldUntil = Date.now() + AUDIO_VOLUME_SETTLE_MS;
  ["gain", "bass", "treble"].forEach((kind) => { audioToneHoldUntil[kind] = Date.now() + AUDIO_TONE_SETTLE_MS; });

  state.audio.volume = volume;
  Object.assign(state.audio, toneValues, {
    subwoofer: Boolean(preset.subwoofer),
    surround: Boolean(preset.surround),
  });
  renderAudio();

  try {
    const mediaState = await callMediaActionViaLocalBackend("volume", volume);
    if (mediaState?.entityId) {
      mediaState.volumeLevel = volume / 100;
      upsertMediaPlayerEntity(mediaState);
      applyMediaEntityState(mediaState);
      state.audio.volume = volume;
    }

    const commands = [
      ...["gain", "bass", "treble"].map((kind) => sendAudioToneAction(kind, toneValues[kind])),
      setAudioSwitchState("subwoofer", preset.subwoofer),
      setAudioSwitchState("surround", preset.surround),
    ];
    await Promise.all(commands);
    saveConfig();
    showToast(`${preset.label} applied`);
    scheduleAudioSync();
  } catch (error) {
    addHaLog("error", `${preset.label} preset failed`, error.message || String(error));
    showToast(`${preset.label} preset failed`);
  } finally {
    audioPresetInFlight = false;
    audioMediaActionInFlight = false;
    renderAudio();
    renderMediaPlayerList();
  }
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
    window.setTimeout(() => pollHomeAssistantMediaPlayer({ force: true, controls: delay > 2000 }), delay);
  });
}

async function pollHomeAssistantMediaPlayer(options = {}) {
  const entityId = state.integrations.homeAssistant.selectedMediaPlayerId;
  if (!entityId) return;
  if (!options.force && document.visibilityState === "hidden") return;
  const now = Date.now();
  if (!options.force && state.currentPage !== "audio") {
    // Keep the selected media-player state live even while another page is open
    // so the panel can jump to Audio as soon as music starts.  This only polls
    // the one selected media_player entity; tone/switch controls stay throttled.
    if (now - lastInactiveAudioSyncAt < HA_AUDIO_SYNC_INTERVAL_MS - 250) return;
    lastInactiveAudioSyncAt = now;
  }
  if (!options.force && state.currentPage === "audio" && now - lastAudioUserInteractionAt < 650) return;
  if (!options.force && audioMediaActionInFlight) return;
  if (haAudioSyncInFlight) return;

  haAudioSyncInFlight = true;
  try {
    const players = await fetchMediaStatesViaLocalBackend([entityId]);
    players.forEach(upsertMediaPlayerEntity);
    if (players[0]) applyMediaEntityState(players[0]);

    // Tone controls are much less time-sensitive than media state/volume. Do not
    // fetch three extra number entities on every poll; that keeps the Pi/HA light.
    haAudioSyncTick += 1;
    const shouldSyncToneControls = Boolean(options.controls) || (state.currentPage === "audio" && haAudioSyncTick % 5 === 0);
    if (shouldSyncToneControls && !audioMediaActionInFlight) {
      try {
        const controls = await fetchAudioControlStatesViaLocalBackend();
        if (controls) applyAudioControls(controls);
        const switches = await fetchAudioSwitchStatesViaLocalBackend();
        if (switches) applyAudioSwitchControls(switches);
      } catch (error) {
        addHaLog("warn", "Audio control sync skipped", error.message || String(error));
      }
    }

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
  if (audioMediaActionInFlight) {
    showToast("Audio command in progress…");
    return null;
  }
  lastAudioUserInteractionAt = Date.now();
  audioMediaActionInFlight = true;
  renderAudio();
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
  } finally {
    audioMediaActionInFlight = false;
    renderAudio();
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


function getBlindEntityKey(blind) {
  return blind?.haEntityId || blind?.id || "";
}

function clearBlindCommandHold(blind) {
  const key = getBlindEntityKey(blind);
  if (key) blindCommandHolds.delete(key);
}

function markBlindCommandHold(blind, targetPosition) {
  const key = getBlindEntityKey(blind);
  if (!key) return;
  const target = clamp(Math.round(Number(targetPosition)), 0, 100);
  blindCommandHolds.set(key, { target, until: Date.now() + BLIND_COMMAND_HOLD_MS });
}

function getBlindCommandHold(blind) {
  const key = getBlindEntityKey(blind);
  if (!key) return null;
  const hold = blindCommandHolds.get(key);
  if (!hold) return null;
  if (Date.now() > hold.until) {
    blindCommandHolds.delete(key);
    return null;
  }
  return hold;
}

function isBlindCommandHoldResolved(blind, entity) {
  const hold = getBlindCommandHold(blind);
  if (!hold) return true;
  const actualPosition = normalizeHaPosition(entity, hold.target);
  if (Math.abs(actualPosition - hold.target) <= BLIND_POSITION_TOLERANCE) {
    clearBlindCommandHold(blind);
    return true;
  }
  return false;
}

function updateBlindVisualCard(card, blind) {
  if (!card || !blind) return;
  const position = clamp(Math.round(Number(blind.position) || 0), 0, 100);
  applyBlindVisualVars(card, position);
  const percent = card.querySelector(".blind-percent");
  if (percent) percent.textContent = `${position}%`;
  const stage = card.querySelector("[data-blind-stage]");
  if (stage) stage.setAttribute("aria-valuenow", String(position));
  card.classList.toggle("blind-pending", Boolean(getBlindCommandHold(blind)));
}


function applyBlindVisualVars(card, position) {
  const pct = clamp(Number(position) || 0, 0, 100);
  const openRatio = pct / 100;
  const closedRatio = 1 - openRatio;
  const slatAngle = Math.round(4 + openRatio * 68);
  const slatHeight = 12.5 - (openRatio * 7.4);
  const sunlight = openRatio * openRatio;

  card.style.setProperty("--blind-open", `${pct}%`);
  card.style.setProperty("--blind-closed", `${100 - pct}%`);
  card.style.setProperty("--blind-open-ratio", openRatio.toFixed(3));
  card.style.setProperty("--blind-closed-ratio", closedRatio.toFixed(3));
  card.style.setProperty("--blind-shade-height", `100%`);
  card.style.setProperty("--blind-slat-angle", `${slatAngle}deg`);
  card.style.setProperty("--blind-light", (0.06 + sunlight * 0.94).toFixed(3));
  card.style.setProperty("--blind-shadow", (0.66 - openRatio * 0.36).toFixed(3));
  card.style.setProperty("--blind-slat-height", `${slatHeight.toFixed(1)}px`);
  card.style.setProperty("--blind-sun-opacity", (sunlight * 0.82).toFixed(3));
  card.style.setProperty("--blind-beam-opacity", (Math.max(0, openRatio - 0.12) * 0.58).toFixed(3));
  card.style.setProperty("--blind-room-glow", (0.1 + sunlight * 0.55).toFixed(3));
  card.dataset.blindState = pct >= 98 ? "open" : pct <= 2 ? "closed" : "partial";
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
    applyBlindVisualVars(card, blind.position);
    card.classList.toggle("blind-pending", Boolean(getBlindCommandHold(blind)));
    const slatCount = 18;
    const slats = Array.from({ length: slatCount }, (_, index) => `<span class="blind-slat" style="--slat-index:${index}; --slat-top:${8 + (index * (84 / (slatCount - 1)))}%"></span>`).join("");
    card.innerHTML = `
      <div class="blind-top">
        <div>
          <div class="blind-name">${blind.name}</div>
        </div>
        <div class="blind-percent">${blind.position}%</div>
      </div>
      <button class="blind-action primary" data-blind-id="${blind.id}" data-action="open">Open</button>
      <div class="shade-stage modern-shade-stage" data-blind-stage="${blind.id}" role="slider" aria-label="${blind.name} position" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${blind.position}" tabindex="0">
        <div class="blind-window smart-shade-window tilt-shade-window" aria-hidden="true">
          <div class="shade-glass"></div>
          <div class="shade-sunwash"></div>
          <div class="shade-sheet venetian-tilt-sheet">${slats}</div>
          <div class="shade-bottom-rail"></div>
        </div>
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
  blind.position = clamp(Math.round(Number(position)), 0, 100);
  const card = document.querySelector(`[data-blind-card="${blindId}"]`);
  updateBlindVisualCard(card, blind);
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

function createLight(roomKey, index) {
  return {
    id: `${roomKey}-light-${Date.now().toString(36)}-${index}`,
    name: `Light ${index}`,
    brightness: 80,
    on: true,
    color: "#ffd76f",
    colorSupported: false,
    haEntityId: "",
    haName: "",
  };
}

function setRoomLightCount(roomKey, count) {
  const room = state.lights.rooms[roomKey];
  if (!room) return;
  const target = clamp(Number(count), 1, 12);
  while (room.lights.length < target) room.lights.push(createLight(roomKey, room.lights.length + 1));
  while (room.lights.length > target) room.lights.pop();
  room.lights.forEach((light, index) => {
    if (!light.name) light.name = `Light ${index + 1}`;
    light.brightness = clamp(Number(light.brightness ?? 80), 0, 100);
    light.on = light.on !== false && light.brightness > 0;
    light.color = normalizeHexColor(light.color);
    light.colorSupported = Boolean(light.colorSupported);
  });
  saveConfig();
  renderLightConfigList();
  renderLights();
}

function addLightRoom() {
  const roomNumber = getLightRoomKeys().length + 1;
  const label = `New Room ${roomNumber}`;
  const key = slugify(label, state.lights.rooms);
  state.lights.rooms[key] = {
    label,
    lights: [createLight(key, 1)],
  };
  state.lights.room = key;
  saveConfig({ toast: true });
  renderLightConfigList();
  renderLights();
}

function deleteLightRoom(roomKey) {
  const keys = getLightRoomKeys();
  if (keys.length <= 1) {
    showToast("At least one room is required");
    return;
  }
  delete state.lights.rooms[roomKey];
  if (state.lights.room === roomKey) state.lights.room = getLightRoomKeys()[0];
  saveConfig({ toast: true });
  renderLightConfigList();
  renderLights();
}

function renameLightRoom(roomKey, label) {
  const room = state.lights.rooms[roomKey];
  if (!room) return;
  room.label = label.trim() || "Room";
  saveConfig();
  renderLights();
}

function renameLight(roomKey, lightId, label) {
  const room = state.lights.rooms[roomKey];
  const light = room?.lights.find((item) => item.id === lightId);
  if (!light) return;
  light.name = label.trim() || "Light";
  saveConfig();
  renderLights();
}

function renderLightConfigList() {
  if (!elements.lightRoomConfigList) return;
  elements.lightRoomConfigList.innerHTML = "";
  getLightRoomKeys().forEach((key) => {
    const room = state.lights.rooms[key];
    const card = document.createElement("div");
    card.className = "room-config-card light-config-card";
    card.dataset.lightRoomConfig = key;
    const countOptions = Array.from({ length: 12 }, (_, idx) => idx + 1)
      .map((count) => `<option value="${count}" ${room.lights.length === count ? "selected" : ""}>${count}</option>`)
      .join("");
    const lightInputs = room.lights.map((light, index) => `
      <label class="mini-field">Light ${index + 1}
        <input type="text" value="${escapeHtml(light.name)}" data-light-name-input data-room-key="${escapeHtml(key)}" data-light-id="${escapeHtml(light.id)}" />
      </label>
    `).join("");
    card.innerHTML = `
      <div class="room-config-main">
        <label class="form-field compact-field">Room Name
          <input type="text" value="${escapeHtml(room.label)}" data-light-room-name-input data-room-key="${escapeHtml(key)}" />
        </label>
        <label class="form-field compact-field">Lights
          <select data-light-room-count-select data-room-key="${escapeHtml(key)}">${countOptions}</select>
        </label>
        <button class="danger-button" data-delete-light-room="${escapeHtml(key)}">Delete</button>
      </div>
      <div class="blind-name-grid light-name-grid">${lightInputs}</div>
    `;
    elements.lightRoomConfigList.appendChild(card);
  });
}

function renderLightRoomTabs() {
  if (!elements.lightRoomTabs) return;
  elements.lightRoomTabs.innerHTML = "";
  getLightRoomKeys().forEach((key) => {
    const room = state.lights.rooms[key];
    const tab = document.createElement("button");
    tab.className = "room-tab";
    tab.dataset.lightRoom = key;
    tab.textContent = room.label;
    tab.classList.toggle("active", key === state.lights.room);
    elements.lightRoomTabs.appendChild(tab);
  });
}

function findLightInActiveRoom(lightId) {
  const room = getActiveLightRoom();
  return room.lights.find((item) => item.id === lightId) || null;
}

function updateLightCard(light) {
  const card = document.querySelector(`[data-light-card="${light.id}"]`);
  if (!card) return;
  const brightness = clamp(Number(light.brightness || 0), 0, 100);
  const on = light.on !== false && brightness > 0;
  const color = normalizeHexColor(light.color);
  const rgb = hexToRgb(color);
  const colorSupported = Boolean(light.colorSupported);
  const displayName = lightDisplayName(light);

  card.classList.toggle("off", !on);
  card.classList.toggle("linked", Boolean(light.haEntityId));
  card.classList.toggle("color-capable", colorSupported);
  card.style.setProperty("--light-level", `${brightness}%`);
  card.style.setProperty("--light-glow", (on ? 0.18 + brightness / 125 : 0.08).toFixed(3));
  card.style.setProperty("--light-color", color);
  card.style.setProperty("--light-color-rgb", `${rgb.r}, ${rgb.g}, ${rgb.b}`);

  const title = card.querySelector("[data-light-title]");
  if (title) {
    title.textContent = displayName;
    title.setAttribute("title", displayName);
  }

  const iconButton = card.querySelector("[data-light-icon-toggle]");
  if (iconButton) {
    const colorHint = colorSupported ? ". Hold to change color." : "";
    iconButton.setAttribute("aria-label", `${on ? "Turn off" : "Turn on"} ${displayName}${colorHint}`);
    iconButton.setAttribute("aria-pressed", on ? "true" : "false");
    iconButton.setAttribute("title", colorSupported ? "Tap to toggle. Hold to change color." : "Tap to toggle.");
  }

  const slider = card.querySelector("[data-light-slider]");
  if (slider && document.activeElement !== slider) slider.value = String(brightness);
  setRangeVisual(slider);

  const value = card.querySelector("[data-light-value]");
  if (value) value.textContent = `${Math.round(brightness)}%`;

  const colorRow = card.querySelector("[data-light-color-row]");
  if (colorRow) colorRow.hidden = !colorSupported;
  const colorButton = card.querySelector("[data-light-color-open]");
  if (colorButton) colorButton.setAttribute("aria-label", `Change ${displayName} color`);
  const colorSwatch = card.querySelector("[data-light-color-swatch]");
  if (colorSwatch) colorSwatch.style.background = color;
}

function renderLights() {
  const room = getActiveLightRoom();
  if (elements.lightRoomTitle) elements.lightRoomTitle.textContent = room.label;
  renderLightRoomTabs();
  if (!elements.lightCards) return;
  elements.lightCards.innerHTML = "";
  const lights = room.lights || [];
  elements.lightCards.style.setProperty("--light-columns", clamp(lights.length, 1, 4));
  lights.forEach((light) => {
    light.brightness = clamp(Number(light.brightness ?? 80), 0, 100);
    light.on = light.on !== false && light.brightness > 0;
    const card = document.createElement("div");
    card.className = "light-card";
    card.dataset.lightCard = light.id;
    card.dataset.roomKey = state.lights.room;
    light.color = normalizeHexColor(light.color);
    light.colorSupported = Boolean(light.colorSupported);
    const displayName = lightDisplayName(light);
    const safeLightId = escapeHtml(light.id);
    const safeLightName = escapeHtml(displayName);
    card.innerHTML = `
      <div class="light-card-top">
        <div class="light-title-block">
          <strong data-light-title title="${safeLightName}">${safeLightName}</strong>
        </div>
      </div>
      <div class="light-vertical-body">
        <button class="light-icon-button light-icon-wrap modern-light-icon-wrap" data-light-icon-toggle data-light-id="${safeLightId}" type="button" aria-label="Toggle ${safeLightName}">
          <span class="light-modern-icon" aria-hidden="true">
            <svg viewBox="0 0 72 72" focusable="false">
              <path class="fixture" d="M22 12h28c3.8 0 6.8 3 6.8 6.8v2.4H15.2v-2.4C15.2 15 18.2 12 22 12Z"></path>
              <path class="beam" d="M21 25h30l7.5 31.5c.8 3.3-1.7 6.5-5.1 6.5H18.6c-3.4 0-5.9-3.2-5.1-6.5L21 25Z"></path>
              <path class="lens" d="M24 25h24c-1.4 5.7-6.1 9.6-12 9.6S25.4 30.7 24 25Z"></path>
            </svg>
          </span>
        </button>
        <label class="light-slider-rail" for="lightSlider-${safeLightId}">
          <span class="sr-only">${safeLightName} brightness</span>
          <input id="lightSlider-${safeLightId}" class="light-slider light-slider-vertical" data-light-slider data-light-id="${safeLightId}" type="range" min="0" max="100" step="1" value="${Math.round(light.brightness)}" aria-label="${safeLightName} brightness" />
        </label>
        <div class="light-value-stack">
          <strong data-light-value>${Math.round(light.brightness)}%</strong>
          <span>Brightness</span>
        </div>
      </div>
    `;
    elements.lightCards.appendChild(card);
    updateLightCard(light);
  });
}

function setLightBrightness(lightId, brightness, options = {}) {
  lastLightUserInteractionAt = Date.now();
  const light = findLightInActiveRoom(lightId);
  if (!light) return;
  light.brightness = clamp(Number(brightness), 0, 100);
  light.on = light.brightness > 0;
  light.localHoldUntil = Date.now() + 1800;
  updateLightCard(light);
  saveConfig();
  if (options.send) sendLightToHomeAssistant(light, light.on ? "on" : "off", light.brightness);
}

function setLightColor(lightId, color, options = {}) {
  lastLightUserInteractionAt = Date.now();
  const light = findLightInActiveRoom(lightId);
  if (!light || !light.colorSupported) return;
  light.color = normalizeHexColor(color, light.color || "#ffd76f");
  if (Number(light.brightness || 0) <= 0) light.brightness = clamp(Number(light.lastBrightness || 80), 1, 100);
  light.on = true;
  light.localHoldUntil = Date.now() + 1800;
  updateLightCard(light);
  saveConfig();
  if (options.send) sendLightToHomeAssistant(light, "color", light.brightness, light.color);
}

function renderLightColorPicker() {
  const light = findLightInActiveRoom(activeLightColorLightId);
  if (!light || !elements.lightColorPresetGrid) return;
  const current = normalizeHexColor(light.color);
  const displayName = lightDisplayName(light);
  if (elements.lightColorPickerTitle) elements.lightColorPickerTitle.textContent = displayName;
  if (elements.lightColorPreview) elements.lightColorPreview.style.background = current;
  if (elements.lightColorName) elements.lightColorName.textContent = getLightPresetName(current);
  elements.lightColorPresetGrid.innerHTML = LIGHT_COLOR_PRESETS.map((preset) => {
    const color = normalizeHexColor(preset.color);
    const selected = color === current ? " selected" : "";
    return `
      <button class="light-color-preset${selected}" data-light-preset-color="${color}" type="button" style="--preset-color: ${color}">
        <span class="preset-swatch" aria-hidden="true"></span>
        <strong>${escapeHtml(preset.name)}</strong>
      </button>
    `;
  }).join("");
}

function openLightColorPicker(lightId) {
  const light = findLightInActiveRoom(lightId);
  if (!light || !light.colorSupported) return;
  activeLightColorLightId = lightId;
  renderLightColorPicker();
  setOverlayOpen(elements.lightColorOverlay, true);
}

function closeLightColorPicker() {
  activeLightColorLightId = "";
  setOverlayOpen(elements.lightColorOverlay, false);
}

function applyLightPresetColor(color) {
  if (!activeLightColorLightId) return;
  setLightColor(activeLightColorLightId, color, { send: true });
  renderLightColorPicker();
}

function setLightPowerState(light, on) {
  if (!light) return;
  light.on = Boolean(on);
  if (light.on) {
    if (Number(light.brightness || 0) <= 0) light.brightness = clamp(Number(light.lastBrightness || 80), 1, 100);
  } else {
    light.lastBrightness = clamp(Number(light.brightness || light.lastBrightness || 80), 1, 100);
    light.brightness = 0;
  }
  light.localHoldUntil = Date.now() + 1800;
}

async function applyLightAction(action) {
  const room = getActiveLightRoom();
  const lights = Array.isArray(room.lights) ? room.lights : [];
  if (!lights.length) return;
  const turnOn = String(action || "").includes("on");
  lastLightUserInteractionAt = Date.now();

  lights.forEach((light) => setLightPowerState(light, turnOn));
  saveConfig();
  renderLights();

  const linkedTargets = lights.filter((light) => light.haEntityId);
  if (!linkedTargets.length) return;
  await Promise.all(linkedTargets.map((light) => sendLightToHomeAssistant(light, turnOn ? "on" : "off", light.brightness)));
}

function toggleLight(lightId) {
  const light = findLightInActiveRoom(lightId);
  if (!light) return;
  const nextOn = !(light.on !== false && Number(light.brightness || 0) > 0);
  setLightPowerState(light, nextOn);
  updateLightCard(light);
  saveConfig();
  sendLightToHomeAssistant(light, nextOn ? "on" : "off", light.brightness);
}

function normalizeLightEntity(entity, fallback = {}) {
  const brightness = entity?.brightnessPct ?? entity?.brightness ?? fallback.brightness ?? 0;
  const pct = clamp(Math.round(Number(brightness)), 0, 100);
  const on = String(entity?.state || "").toLowerCase() === "on" && pct > 0;
  const colorSupported = Boolean(entity?.colorSupported) || lightColorModesSupportColor(entity?.supportedColorModes);
  const color = normalizeHexColor(entity?.colorHex || fallback.color);
  return {
    entityId: entity?.entityId || fallback.haEntityId || "",
    name: entity?.name || fallback.haName || entity?.entityId || "",
    state: entity?.state || (on ? "on" : "off"),
    brightness: pct,
    on,
    color,
    colorSupported,
  };
}

function applyEntityStateToLight(light, entity) {
  if (!light || !entity) return false;
  if (Date.now() < Number(light.localHoldUntil || 0)) return false;
  const normalized = normalizeLightEntity(entity, light);
  let changed = false;
  if (normalized.name && light.haName !== normalized.name) { light.haName = normalized.name; changed = true; }
  if (normalized.name && light.name === light.haEntityId) { light.name = normalized.name; changed = true; }
  if (light.brightness !== normalized.brightness) { light.brightness = normalized.brightness; changed = true; }
  if (light.on !== normalized.on) { light.on = normalized.on; changed = true; }
  if (light.color !== normalized.color) { light.color = normalized.color; changed = true; }
  if (light.colorSupported !== normalized.colorSupported) { light.colorSupported = normalized.colorSupported; changed = true; }
  return changed;
}

function syncLinkedLightsFromEntities(entities = [], options = {}) {
  if (!entities.length) return false;
  let changed = false;
  Object.values(state.lights.rooms || {}).forEach((room) => {
    (room.lights || []).forEach((light) => {
      if (!light.haEntityId) return;
      const entity = entities.find((item) => item.entityId === light.haEntityId);
      if (!entity) return;
      changed = applyEntityStateToLight(light, entity) || changed;
    });
  });
  if (changed && !options.skipSave) saveConfig();
  return changed;
}

function getLinkedLightEntityIds() {
  const ids = [];
  Object.values(state.lights.rooms || {}).forEach((room) => {
    (room.lights || []).forEach((light) => {
      if (light.haEntityId && !ids.includes(light.haEntityId)) ids.push(light.haEntityId);
    });
  });
  return ids;
}

async function fetchLinkedLightStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token || !entityIds.length) return [];
  const payload = await fetchJsonWithTimeout("/api/ha/light/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  });
  return payload.lights || [];
}

function scheduleLightSync() {
  HA_LIGHT_SYNC_AFTER_COMMAND_DELAYS.forEach((delay) => {
    window.setTimeout(() => pollHomeAssistantLights({ force: true }), delay);
  });
}

async function pollHomeAssistantLights(options = {}) {
  const linkedEntityIds = getLinkedLightEntityIds();
  if (!linkedEntityIds.length) return;
  if (!options.force && document.visibilityState === "hidden") return;
  const now = Date.now();
  if (!options.force && state.currentPage !== "lights") {
    if (now - lastInactiveLightSyncAt < INACTIVE_PAGE_SYNC_INTERVAL_MS) return;
    lastInactiveLightSyncAt = now;
  }
  if (!options.force && state.currentPage === "lights" && now - lastLightUserInteractionAt < 1200) return;
  if (haLightSyncInFlight || lightCommandInFlight) return;

  haLightSyncInFlight = true;
  try {
    const lights = await fetchLinkedLightStatesViaLocalBackend(linkedEntityIds);
    const changed = syncLinkedLightsFromEntities(lights);
    if (changed) renderLights();
    if (haLightSyncLastError) haLightSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haLightSyncLastError) {
      haLightSyncLastError = message;
      addHaLog("warn", "Live light sync paused", message);
    }
  } finally {
    haLightSyncInFlight = false;
  }
}

async function sendLightToHomeAssistant(light, action = "on", brightness = light?.brightness, color = light?.color) {
  if (!light?.haEntityId) return null;
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) return null;
  lightCommandInFlight = true;
  try {
    const payload = await fetchJsonWithTimeout("/api/ha/light/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: baseUrl, token: ha.token, entityId: light.haEntityId, action, brightness, color }),
    });
    if (payload.light) {
      applyEntityStateToLight(light, payload.light);
      saveConfig();
      renderLights();
    }
    scheduleLightSync();
    return payload.light || null;
  } catch (error) {
    addHaLog("error", "Light command failed", error.message || String(error));
    showToast("Light command failed");
    return null;
  } finally {
    lightCommandInFlight = false;
  }
}

function openLightEntityPicker(roomKey, lightId) {
  state.lightEntityPicker = { roomKey, lightId };
  openAudioEntityPicker("light");
}

function assignEntityToLight(entity) {
  const { roomKey, lightId } = state.lightEntityPicker;
  const room = state.lights.rooms[roomKey];
  const light = room?.lights.find((item) => item.id === lightId);
  if (!light || !entity) return;
  light.haEntityId = entity.entityId || "";
  light.haName = entity.name || entity.entityId || "";
  if (entity.name) light.name = entity.name;
  applyEntityStateToLight(light, entity);
  state.lightEntityPicker = { roomKey: null, lightId: null };
  closeAudioEntityPicker();
  saveConfig({ toast: true });
  renderLightConfigList();
  renderLights();
  pollHomeAssistantLights({ force: true });
}


function normalizeRoomControlDomain(value, fallback = "switch") {
  const domain = String(value || "").trim().toLowerCase();
  return domain || fallback;
}

function normalizeRoomControlDeviceClass(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, "_");
}

function normalizeRoomControlDisplayType(value) {
  const raw = String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (["bed", "bed_slider", "bedslider", "adjustable_bed", "actuator_bed"].includes(raw)) return "bed_slider";
  return "";
}

function roomControlIsBedSlider(control) {
  return normalizeRoomControlDisplayType(control?.displayType || control?.displayMode || control?.specialType || "") === "bed_slider";
}

function roomControlPositionPercent(control, fallback = 0) {
  const raw = control?.currentPosition ?? control?.current_position;
  if (raw !== null && raw !== undefined && raw !== "") {
    const value = Number(raw);
    if (Number.isFinite(value)) return clamp(Math.round(value), 0, 100);
  }
  if (normalizeRoomControlDomain(control?.domain) === "cover") {
    const stateText = String(control?.state || "").toLowerCase();
    if (["open", "opening"].includes(stateText)) return 100;
    if (["closed", "closing"].includes(stateText)) return 0;
  }
  return clamp(Math.round(Number(fallback || (control?.on ? 100 : 0))), 0, 100);
}

function normalizeRoomControlRecord(control, roomKey = "room", index = 1) {
  if (!control || typeof control !== "object") return createRoomControl(roomKey, index);
  control.id = control.id || `${roomKey}-control-${Date.now().toString(36)}-${index}`;
  control.name = control.name || `Entry ${index}`;
  control.haEntityId = control.haEntityId || "";
  control.haName = control.haName || "";
  control.domain = normalizeRoomControlDomain(control.domain || String(control.haEntityId || "").split(".", 1)[0], "switch");
  control.deviceClass = normalizeRoomControlDeviceClass(control.deviceClass || control.device_class || "");
  control.state = String(control.state ?? (control.on ? "on" : "off")).toLowerCase();
  control.on = Boolean(control.on);
  control.currentPosition = control.currentPosition ?? control.current_position ?? null;
  if (roomControlIsBedSlider(control) && control.currentPosition !== null && control.currentPosition !== undefined && control.currentPosition !== "") {
    const positionValue = Number(control.currentPosition);
    if (Number.isFinite(positionValue)) control.on = positionValue > 0;
  }
  control.supportedFeatures = Number(control.supportedFeatures ?? control.supported_features ?? 0) || 0;
  control.unitOfMeasurement = control.unitOfMeasurement || control.unit_of_measurement || "";
  control.icon = control.icon || "";
  control.displayType = normalizeRoomControlDisplayType(control.displayType || control.displayMode || control.specialType || "");
  control.defaultCode = String(control.defaultCode || control.code || "").replace(/\D/g, "").slice(0, ROOM_CONTROL_CODE_MAX_LENGTH);
  return control;
}

function createRoomControl(roomKey, index) {
  return {
    id: `${roomKey}-control-${Date.now().toString(36)}-${index}`,
    name: `Entry ${index}`,
    on: false,
    haEntityId: "",
    haName: "",
    domain: "switch",
    deviceClass: "",
    state: "off",
    currentPosition: null,
    supportedFeatures: 0,
    unitOfMeasurement: "",
    icon: "",
    displayType: "",
    defaultCode: "",
  };
}

function normalizeRoomControlRoom(room, roomKey = "room") {
  if (!room || typeof room !== "object") return room;
  room.controls = Array.isArray(room.controls) && room.controls.length ? room.controls : [createRoomControl(roomKey, 1)];
  if (room.controls.length > ROOM_CONTROL_MAX_ENTRIES) room.controls = room.controls.slice(0, ROOM_CONTROL_MAX_ENTRIES);
  room.controls.forEach((control, index) => normalizeRoomControlRecord(control, roomKey, index + 1));
  return room;
}

function setRoomControlCount(roomKey, count) {
  const room = state.roomControl.rooms[roomKey];
  if (!room) return;
  const target = clamp(Number(count), 1, ROOM_CONTROL_MAX_ENTRIES);
  room.controls = Array.isArray(room.controls) ? room.controls : [];
  while (room.controls.length < target) room.controls.push(createRoomControl(roomKey, room.controls.length + 1));
  while (room.controls.length > target) room.controls.pop();
  room.controls.forEach((control, index) => normalizeRoomControlRecord(control, roomKey, index + 1));
  saveConfig();
  renderRoomControlConfigList();
  renderRoomControls();
}

function addRoomControlRoom() {
  const roomNumber = getRoomControlKeys().length + 1;
  const label = `New Room ${roomNumber}`;
  const key = slugify(label, state.roomControl.rooms);
  state.roomControl.rooms[key] = {
    label,
    controls: [createRoomControl(key, 1)],
  };
  state.roomControl.room = key;
  saveConfig({ toast: true });
  renderRoomControlConfigList();
  renderRoomControls();
}

function deleteRoomControlRoom(roomKey) {
  const keys = getRoomControlKeys();
  if (keys.length <= 1) {
    showToast("At least one room is required");
    return;
  }
  delete state.roomControl.rooms[roomKey];
  if (state.roomControl.room === roomKey) state.roomControl.room = getRoomControlKeys()[0];
  saveConfig({ toast: true });
  renderRoomControlConfigList();
  renderRoomControls();
}

function renameRoomControlRoom(roomKey, label) {
  const room = state.roomControl.rooms[roomKey];
  if (!room) return;
  room.label = label.trim() || "Room";
  saveConfig();
  renderRoomControls();
}

function renameRoomControl(roomKey, controlId, label) {
  const room = state.roomControl.rooms[roomKey];
  const control = room?.controls?.find((item) => item.id === controlId);
  if (!control) return;
  control.name = label.trim() || "Entry";
  saveConfig();
  renderRoomControls();
}

function setRoomControlDefaultCode(roomKey, controlId, value) {
  const room = state.roomControl.rooms[roomKey];
  const control = room?.controls?.find((item) => item.id === controlId);
  if (!control) return;
  const code = String(value || "").replace(/\D/g, "").slice(0, ROOM_CONTROL_CODE_MAX_LENGTH);
  if (control.defaultCode === code) return;
  control.defaultCode = code;
  saveConfig();
  updateRoomControlCard(control);
}

function setRoomControlBedSlider(roomKey, controlId, enabled) {
  const room = state.roomControl.rooms[roomKey];
  const control = room?.controls?.find((item) => item.id === controlId);
  if (!control) return;
  const nextType = enabled ? "bed_slider" : "";
  if (normalizeRoomControlDisplayType(control.displayType) === nextType) return;
  control.displayType = nextType;
  if (enabled && control.haEntityId && normalizeRoomControlDomain(control.domain) !== "cover") {
    showToast("Bed slider is meant for a cover.* entity");
  }
  saveConfig();
  renderRoomControlConfigList();
  renderRoomControls();
}

function renderRoomControlConfigList() {
  if (!elements.roomControlConfigList) return;
  elements.roomControlConfigList.innerHTML = "";
  getRoomControlKeys().forEach((key) => {
    const room = state.roomControl.rooms[key];
    normalizeRoomControlRoom(room, key);
    const card = document.createElement("div");
    card.className = "room-config-card room-control-config-card";
    card.dataset.roomControlConfig = key;
    const countOptions = Array.from({ length: ROOM_CONTROL_MAX_ENTRIES }, (_, idx) => idx + 1)
      .map((count) => `<option value="${count}" ${room.controls.length === count ? "selected" : ""}>${count}</option>`)
      .join("");
    const controlInputs = room.controls.map((control, index) => {
      const linkedType = roomControlTypeLabel(control);
      const linkedText = linkedType ? `${linkedType} · ${control.haEntityId}` : control.haEntityId;
      const linkedMeta = control.haEntityId
        ? `<small class="room-control-config-link">${escapeHtml(linkedText)}</small>`
        : `<small class="room-control-config-link muted">Hold the room card to assign any HA entity</small>`;
      const bedSliderChecked = roomControlIsBedSlider(control) ? "checked" : "";
      return `
        <label class="mini-field room-control-entry-field">Entry ${index + 1}
          <input type="text" value="${escapeHtml(control.name)}" data-room-control-name-input data-room-key="${escapeHtml(key)}" data-room-control-id="${escapeHtml(control.id)}" />
          ${linkedMeta}
          <span class="room-control-special-toggle" role="switch" tabindex="0" aria-checked="${bedSliderChecked ? "true" : "false"}" data-room-control-bed-slider-toggle-wrap data-room-key="${escapeHtml(key)}" data-room-control-id="${escapeHtml(control.id)}">
            <input type="checkbox" ${bedSliderChecked} data-room-control-bed-slider-toggle data-room-key="${escapeHtml(key)}" data-room-control-id="${escapeHtml(control.id)}" aria-label="Show ${escapeHtml(control.name)} as a bed slider" />
            <span class="room-control-special-dot" aria-hidden="true"></span>
            <span class="room-control-special-copy"><strong>Bed slider</strong><small>Bed icon with position slider for cover.* actuators</small></span>
          </span>
          <span class="room-control-code-label">Default Code <em>optional</em></span>
          <input class="room-control-code-input" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="${ROOM_CONTROL_CODE_MAX_LENGTH}" autocomplete="off" value="${escapeHtml(control.defaultCode || "")}" data-room-control-default-code-input data-room-key="${escapeHtml(key)}" data-room-control-id="${escapeHtml(control.id)}" aria-label="Default code for ${escapeHtml(control.name)}" />
        </label>
      `;
    }).join("");
    card.innerHTML = `
      <div class="room-config-main">
        <label class="form-field compact-field">Room Name
          <input type="text" value="${escapeHtml(room.label)}" data-room-control-room-name-input data-room-key="${escapeHtml(key)}" />
        </label>
        <label class="form-field compact-field">Entries
          <select data-room-control-count-select data-room-key="${escapeHtml(key)}">${countOptions}</select>
        </label>
        <button class="danger-button" data-delete-room-control-room="${escapeHtml(key)}">Delete</button>
      </div>
      <div class="blind-name-grid room-control-name-grid">${controlInputs}</div>
    `;
    elements.roomControlConfigList.appendChild(card);
  });
}

function renderRoomControlTabs() {
  if (!elements.roomControlTabs) return;
  elements.roomControlTabs.innerHTML = "";
  getRoomControlKeys().forEach((key) => {
    const room = state.roomControl.rooms[key];
    const tab = document.createElement("button");
    tab.className = "room-tab";
    tab.dataset.roomControlRoom = key;
    tab.textContent = room.label;
    tab.classList.toggle("active", key === state.roomControl.room);
    elements.roomControlTabs.appendChild(tab);
  });
}

function findRoomControlInActiveRoom(controlId) {
  const room = getActiveRoomControlRoom();
  return (room.controls || []).find((item) => item.id === controlId) || null;
}

function roomControlDisplayName(control) {
  return String(control?.haName || control?.name || "Entry").trim() || "Entry";
}

function prettifyRoomControlName(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function normalizeRoomControlIcon(value) {
  return String(value || "").trim().toLowerCase();
}

function roomControlVisualKind(control) {
  if (roomControlIsBedSlider(control)) return "bed";
  const domain = normalizeRoomControlDomain(control?.domain, "entity");
  const deviceClass = normalizeRoomControlDeviceClass(control?.deviceClass);
  const icon = normalizeRoomControlIcon(control?.icon);
  const entityText = `${control?.haEntityId || ""} ${control?.haName || ""} ${control?.name || ""}`.toLowerCase();
  const mentionsGarage = /garage|bay|overhead/.test(`${icon} ${deviceClass} ${entityText}`);
  const mentionsDoor = /door|gate|entry/.test(`${icon} ${deviceClass} ${entityText}`);
  if (domain === "lock" && mentionsGarage) return "garage-lock";
  if (domain === "lock" && mentionsDoor) return "door-lock";
  if (domain === "cover" && (deviceClass === "garage" || deviceClass === "garage_door" || mentionsGarage)) return "garage-door";
  if (domain === "cover" && ["door", "gate", "opening"].includes(deviceClass)) return deviceClass;
  if (domain === "binary_sensor" && ["door", "garage_door", "gate", "opening"].includes(deviceClass)) return deviceClass;
  return domain === "cover" ? (deviceClass || "cover") : domain === "binary_sensor" ? (deviceClass || "binary_sensor") : domain;
}

function roomControlHasDefaultCode(control) {
  return Boolean(String(control?.defaultCode || "").replace(/\D/g, ""));
}

function roomControlRequiresCodeForAction(control, action) {
  if (!roomControlHasDefaultCode(control)) return false;
  const safeActions = new Set(["lock", "close", "off", "return_to_base", "stop"]);
  return !safeActions.has(String(action || "").toLowerCase());
}

function roomControlTypeLabel(control) {
  if (roomControlIsBedSlider(control)) return "";
  const domain = normalizeRoomControlDomain(control?.domain, "entity");
  const deviceClass = normalizeRoomControlDeviceClass(control?.deviceClass);
  const visual = roomControlVisualKind(control);
  const base = prettifyRoomControlName(domain);
  if (visual === "garage-lock") return "Lock · Garage Door";
  if (visual === "door-lock") return "Lock · Door";
  if (visual === "garage-door" && !deviceClass) return `${base} · Garage Door`;
  return deviceClass ? `${base} · ${prettifyRoomControlName(deviceClass)}` : base;
}

function roomControlEntityDomain(entity) {
  return normalizeRoomControlDomain(entity?.domain || String(entity?.entityId || "").split(".", 1)[0], "");
}

function isRoomControlEntityAllowed(entity) {
  const domain = roomControlEntityDomain(entity);
  return Boolean(domain) && !ROOM_CONTROL_EXCLUDED_DOMAINS.has(domain);
}


function isRoomControlUnavailableState(value) {
  return ["unavailable", "unknown", "none", "null"].includes(String(value ?? "").trim().toLowerCase());
}

function isRoomControlOn(value) {
  return ["on", "true", "open", "opening", "unlocked", "home", "playing", "active", "detected", "problem", "wet", "running", "cleaning"].includes(String(value ?? "").toLowerCase());
}

function roomControlCurrentPosition(entity, fallback = null) {
  const raw = entity?.currentPosition ?? entity?.current_position ?? fallback;
  if (raw === null || raw === undefined || raw === "") return null;
  const num = Number(raw);
  return Number.isFinite(num) ? clamp(Math.round(num), 0, 100) : null;
}

function roomControlOnFromState(domain, deviceClass, stateText, currentPosition = null) {
  const stateValue = String(stateText || "").toLowerCase();
  if (isRoomControlUnavailableState(stateValue)) return false;
  if (domain === "cover") {
    if (["open", "opening"].includes(stateValue)) return true;
    if (["closed", "closing"].includes(stateValue)) return false;
    if (currentPosition !== null) return Number(currentPosition) > 0;
    return false;
  }
  if (domain === "lock") return ["unlocked", "open", "opening"].includes(stateValue);
  if (domain === "media_player") return ["playing", "on"].includes(stateValue);
  if (domain === "vacuum") return !["docked", "idle", "off"].includes(stateValue);
  if (domain === "binary_sensor") return stateValue === "on";
  if (domain === "sensor" || domain === "number" || domain === "input_number" || domain === "select" || domain === "input_select" || domain === "person" || domain === "device_tracker" || domain === "climate") return false;
  return isRoomControlOn(stateValue);
}

function normalizeRoomControlEntity(entity, fallback = {}) {
  const entityId = entity?.entityId || fallback.haEntityId || "";
  const domain = normalizeRoomControlDomain(entity?.domain || String(entityId).split(".", 1)[0] || fallback.domain, "switch");
  const deviceClass = normalizeRoomControlDeviceClass(entity?.deviceClass ?? fallback.deviceClass ?? "");
  const stateText = String(entity?.state ?? fallback.state ?? (fallback.on ? "on" : "off")).toLowerCase();
  const currentPosition = roomControlCurrentPosition(entity, fallback.currentPosition ?? null);
  return {
    entityId,
    domain,
    deviceClass,
    name: entity?.name || fallback.haName || entityId || fallback.name || "Entry",
    state: stateText,
    on: roomControlOnFromState(domain, deviceClass, stateText, currentPosition),
    currentPosition,
    supportedFeatures: Number(entity?.supportedFeatures ?? fallback.supportedFeatures ?? 0) || 0,
    unitOfMeasurement: entity?.unitOfMeasurement || fallback.unitOfMeasurement || "",
    icon: entity?.icon || fallback.icon || "",
  };
}

function applyEntityStateToRoomControl(control, entity) {
  if (!control || !entity) return false;
  if (Date.now() < Number(control.localHoldUntil || 0)) return false;
  const normalized = normalizeRoomControlEntity(entity, control);
  let changed = false;
  if (normalized.name && control.haName !== normalized.name) { control.haName = normalized.name; changed = true; }
  if (normalized.name && (!control.name || control.name === control.haEntityId || /^Entry\s+\d+$/i.test(control.name))) { control.name = normalized.name; changed = true; }
  if (control.state !== normalized.state) { control.state = normalized.state; changed = true; }
  if (control.on !== normalized.on) { control.on = normalized.on; changed = true; }
  if (control.domain !== normalized.domain) { control.domain = normalized.domain; changed = true; }
  if ((control.deviceClass || "") !== normalized.deviceClass) { control.deviceClass = normalized.deviceClass; changed = true; }
  if ((control.currentPosition ?? null) !== (normalized.currentPosition ?? null)) { control.currentPosition = normalized.currentPosition; changed = true; }
  if (Number(control.supportedFeatures || 0) !== normalized.supportedFeatures) { control.supportedFeatures = normalized.supportedFeatures; changed = true; }
  if ((control.unitOfMeasurement || "") !== normalized.unitOfMeasurement) { control.unitOfMeasurement = normalized.unitOfMeasurement; changed = true; }
  if ((control.icon || "") !== normalized.icon) { control.icon = normalized.icon; changed = true; }
  return changed;
}

function roomControlIsMomentary(control) {
  return ROOM_CONTROL_MOMENTARY_DOMAINS.has(normalizeRoomControlDomain(control?.domain));
}

function roomControlIsStatusOnly(control) {
  const domain = normalizeRoomControlDomain(control?.domain);
  return ROOM_CONTROL_READ_ONLY_DOMAINS.has(domain) || normalizeRoomControlDomain(control?.domain) === "binary_sensor";
}

function roomControlActionForState(control, desiredOn = null) {
  if (!control?.haEntityId) return "";
  const domain = normalizeRoomControlDomain(control.domain);
  const currentOn = Boolean(control.on);
  const wantOn = desiredOn === null ? !currentOn : Boolean(desiredOn);
  if (roomControlIsStatusOnly(control)) return "";
  if (domain === "cover") return wantOn ? "open" : "close";
  if (domain === "lock") return wantOn ? "unlock" : "lock";
  if (domain === "button" || domain === "input_button") return "press";
  if (domain === "scene" || domain === "script") return "run";
  if (domain === "media_player") return desiredOn === null ? "play_pause" : (wantOn ? "on" : "off");
  if (domain === "vacuum") return wantOn ? "start" : "return_to_base";
  return wantOn ? "on" : "off";
}

function roomControlOptimisticOn(control, action) {
  const domain = normalizeRoomControlDomain(control?.domain);
  if (["on", "open", "unlock", "start"].includes(action)) return true;
  if (["off", "close", "lock", "return_to_base"].includes(action)) return false;
  if (action === "press" || action === "run" || action === "play_pause") return true;
  if (domain === "cover" && action === "stop") return Boolean(control?.on);
  return !Boolean(control?.on);
}

function roomControlStateLabel(control) {
  if (!control?.haEntityId) return "Hold to assign";
  const domain = normalizeRoomControlDomain(control.domain);
  const deviceClass = normalizeRoomControlDeviceClass(control.deviceClass);
  const rawState = String(control.state || (control.on ? "on" : "off")).toLowerCase();
  if (isRoomControlUnavailableState(rawState)) return prettifyRoomControlName(rawState);
  if (roomControlIsBedSlider(control)) return `${roomControlPositionPercent(control)}%`;
  if (domain === "cover") {
    const base = prettifyRoomControlName(rawState || (control.on ? "open" : "closed"));
    return control.currentPosition !== null && control.currentPosition !== undefined ? `${base} · ${control.currentPosition}%` : base;
  }
  if (domain === "lock") return control.on ? "Unlocked" : "Locked";
  if (domain === "light" || domain === "switch" || domain === "input_boolean" || domain === "fan" || domain === "humidifier") return control.on ? "On" : "Off";
  if (domain === "button" || domain === "input_button") return "Press";
  if (domain === "scene") return "Scene";
  if (domain === "script") return rawState === "on" ? "Running" : "Run";
  if (domain === "binary_sensor") {
    const on = rawState === "on";
    const map = {
      battery: ["Low", "OK"],
      battery_charging: ["Charging", "Not Charging"],
      cold: ["Cold", "Normal"],
      connectivity: ["Connected", "Disconnected"],
      door: ["Open", "Closed"],
      garage_door: ["Open", "Closed"],
      gate: ["Open", "Closed"],
      window: ["Open", "Closed"],
      opening: ["Open", "Closed"],
      lock: ["Unlocked", "Locked"],
      moisture: ["Wet", "Dry"],
      motion: ["Motion", "Clear"],
      occupancy: ["Occupied", "Clear"],
      presence: ["Present", "Away"],
      problem: ["Problem", "OK"],
      safety: ["Unsafe", "Safe"],
      smoke: ["Smoke", "Clear"],
      gas: ["Gas", "Clear"],
      sound: ["Sound", "Quiet"],
      vibration: ["Vibration", "Still"],
      power: ["On", "Off"],
      light: ["Light", "Dark"],
    };
    const pair = map[deviceClass] || ["On", "Off"];
    return on ? pair[0] : pair[1];
  }
  if (domain === "sensor" || domain === "number" || domain === "input_number") return `${control.state ?? ""}${control.unitOfMeasurement ? ` ${control.unitOfMeasurement}` : ""}`.trim() || "Status";
  if (domain === "person" || domain === "device_tracker") return prettifyRoomControlName(rawState || "Status");
  if (domain === "media_player") return prettifyRoomControlName(rawState || "Media");
  if (domain === "climate") return prettifyRoomControlName(rawState || "Climate");
  return prettifyRoomControlName(rawState || (control.on ? "on" : "off"));
}

function roomControlAriaAction(control) {
  if (roomControlIsBedSlider(control)) return `Adjust ${roomControlDisplayName(control)} position`;
  const action = roomControlActionForState(control);
  if (!action) return `View ${roomControlDisplayName(control)}`;
  const label = {
    on: "Turn on",
    off: "Turn off",
    open: "Open",
    close: "Close",
    lock: "Lock",
    unlock: "Unlock",
    press: "Press",
    run: "Run",
    play_pause: "Play or pause",
    start: "Start",
    return_to_base: "Return",
  }[action] || "Toggle";
  return `${label} ${roomControlDisplayName(control)}`;
}

function roomControlGlyphSvg(kind, innerSvg) {
  return `<svg class="room-control-svg ${kind}-svg futuristic-room-glyph" viewBox="0 0 96 96" focusable="false" aria-hidden="true">
    <path class="glyph-halo" d="M48 6 82 25v46L48 90 14 71V25z"></path>
    <path class="glyph-corner glyph-corner-a" d="M26 19h-6v13"></path>
    <path class="glyph-corner glyph-corner-b" d="M70 77h6V64"></path>
    <circle class="glyph-core" cx="48" cy="48" r="27"></circle>
    ${innerSvg}
  </svg>`;
}

function roomControlIconSvg(control) {
  const linked = Boolean(control?.haEntityId);
  if (roomControlIsBedSlider(control)) {
    const position = roomControlPositionPercent(control);
    const raised = position > 0;
    return roomControlGlyphSvg("bed", `
      <path class="glyph-soft" d="M21 73h56M27 73v7M71 73v7"></path>
      <path class="glyph-stroke" d="M23 70V39c0-4 3-7 7-7s7 3 7 7v24"></path>
      <rect class="glyph-fill" x="31" y="43" width="17" height="12" rx="4" opacity=".34"></rect>
      <path class="glyph-fill" opacity="${raised ? ".44" : ".22"}" d="M49 63l18-23c2-3 7-2 8 2l8 21z"></path>
      <path class="glyph-stroke" d="M23 63h28l16-23c2-3 7-2 8 2l8 21"></path>
      <path class="glyph-stroke" d="M23 63h52c5 0 8 3 8 8v2H23z"></path>
      <path class="glyph-soft" d="M31 63h18M56 58l11-15M76 63v9"></path>
    `);
  }
  if (!linked) {
    return roomControlGlyphSvg("assign", `
      <path class="glyph-stroke" d="M31 48h34M48 31v34"></path>
      <path class="glyph-soft" d="M28 30h14M54 30h14M28 66h14M54 66h14"></path>
    `);
  }
  const domain = normalizeRoomControlDomain(control?.domain, "switch");
  const deviceClass = normalizeRoomControlDeviceClass(control?.deviceClass);
  const open = Boolean(control?.on);
  const key = roomControlVisualKind(control);
  if (["garage-lock", "garage-door"].includes(key)) {
    return roomControlGlyphSvg("garage-lock", `
      <path class="glyph-stroke" d="M25 72V39c0-8 6-14 14-14h18c8 0 14 6 14 14v33"></path>
      <path class="glyph-soft" d="M31 72V40c0-4 3-7 7-7h20c4 0 7 3 7 7v32M32 48h32M32 58h32"></path>
      <path class="glyph-fill" opacity="${open ? ".20" : ".34"}" d="M32 48h32v24H32z"></path>
      <path class="glyph-stroke" d="M56 62v-6c0-4-3-7-8-7s-8 3-8 7v6"></path>
      <rect class="glyph-soft" x="38" y="61" width="20" height="14" rx="4"></rect>
      <circle class="glyph-dot" cx="48" cy="68" r="2.3"></circle>
    `);
  }
  if (["door-lock", "door", "garage_door", "gate", "opening"].includes(key)) {
    return roomControlGlyphSvg("door", `
      <path class="glyph-stroke" d="M33 73V24h31v49"></path>
      <path class="glyph-fill" opacity="${open ? ".44" : ".20"}" d="M39 69V30l20 5v38z"></path>
      <path class="glyph-soft" d="M29 73h40M58 36v35"></path>
      <circle class="glyph-dot" cx="53" cy="51" r="2.3"></circle>
    `);
  }
  if (["window", "shutter", "blind", "shade", "awning", "curtain", "damper", "cover"].includes(key)) {
    return roomControlGlyphSvg("cover", `
      <rect class="glyph-stroke" x="29" y="22" width="38" height="52" rx="6"></rect>
      <path class="glyph-soft" d="M48 24v48M31 46h34M35 32h26M35 39h26M35 55h26M35 62h26"></path>
      <path class="glyph-fill" opacity="${open ? ".22" : ".10"}" d="M31 24h34v21H31z"></path>
    `);
  }
  if (domain === "light" || deviceClass === "light") {
    return roomControlGlyphSvg("light", `
      <path class="glyph-stroke" d="M37 45a11 11 0 1 1 22 0c0 5-3 8-6 12-1.6 2-2.2 4-2.2 6h-5.6c0-2-.6-4-2.2-6-3-4-6-7-6-12z"></path>
      <path class="glyph-soft" d="M42 70h12M44 77h8M29 40h-7M74 40h-7M32 28l-5-5M64 28l5-5"></path>
    `);
  }
  if (domain === "fan") {
    return roomControlGlyphSvg("fan", `
      <circle class="glyph-dot" cx="48" cy="48" r="5"></circle>
      <path class="glyph-stroke" d="M51 42c8-18 26-9 17 4-5 8-13 3-17-4M41 46c-20-1-20-20-5-19 10 1 9 11 5 19M51 55c11 15-4 25-13 13-5-8 2-15 13-13"></path>
      <path class="glyph-soft" d="M48 20a28 28 0 1 1-1 0"></path>
    `);
  }
  if (domain === "lock" || deviceClass === "lock") {
    return roomControlGlyphSvg("lock", `
      <rect class="glyph-stroke" x="31" y="44" width="34" height="27" rx="6"></rect>
      <path class="glyph-stroke" d="M38 44V34a10 10 0 0 1 ${open ? "18 -7" : "20 0"}v10"></path>
      <circle class="glyph-dot" cx="48" cy="57" r="3"></circle>
      <path class="glyph-soft" d="M48 61v5"></path>
    `);
  }
  if (["motion", "occupancy", "presence"].includes(deviceClass)) {
    return roomControlGlyphSvg("motion", `
      <circle class="glyph-fill" cx="38" cy="30" r="6"></circle>
      <path class="glyph-stroke" d="M40 39l13 9-7 9 9 15M37 40l-8 15M53 48l11-8"></path>
      <path class="glyph-soft" d="M66 25c8 6 13 14 13 24s-5 18-13 24M25 32c-4 4-7 10-7 17s3 13 7 17"></path>
    `);
  }
  if (["moisture", "gas", "smoke", "safety", "problem"].includes(deviceClass)) {
    return roomControlGlyphSvg("alert", `
      <path class="glyph-stroke" d="M48 20l31 56H17z"></path>
      <path class="glyph-stroke" d="M48 38v17"></path>
      <circle class="glyph-dot" cx="48" cy="65" r="3"></circle>
    `);
  }
  if (domain === "button" || domain === "input_button") {
    return roomControlGlyphSvg("button", `
      <circle class="glyph-stroke" cx="48" cy="48" r="24"></circle>
      <circle class="glyph-fill" opacity=".28" cx="48" cy="48" r="12"></circle>
      <path class="glyph-soft" d="M48 17v9M48 70v9M17 48h9M70 48h9"></path>
    `);
  }
  if (domain === "scene" || domain === "script") {
    return roomControlGlyphSvg("scene", `
      <path class="glyph-fill" opacity=".70" d="M39 29l27 19-27 19z"></path>
      <path class="glyph-soft" d="M28 27v42M70 27v42M22 34v28"></path>
    `);
  }
  if (domain === "media_player") {
    return roomControlGlyphSvg("media", `
      <path class="glyph-stroke" d="M24 41h14l18-14v42L38 55H24z"></path>
      <path class="glyph-soft" d="M64 38c5 4 8 7 8 11s-3 8-8 11M70 29c8 6 13 12 13 20s-5 15-13 21"></path>
    `);
  }
  if (domain === "sensor" || domain === "number" || domain === "input_number" || domain === "climate") {
    return roomControlGlyphSvg("sensor", `
      <path class="glyph-stroke" d="M25 61a25 25 0 1 1 46 0"></path>
      <path class="glyph-stroke" d="M48 57l16-18"></path>
      <circle class="glyph-dot" cx="48" cy="61" r="5"></circle>
      <path class="glyph-soft" d="M30 61h36M34 41h-8M70 41h-8M48 23v-8"></path>
    `);
  }
  if (domain === "vacuum") {
    return roomControlGlyphSvg("vacuum", `
      <rect class="glyph-stroke" x="24" y="32" width="48" height="34" rx="17"></rect>
      <circle class="glyph-dot" cx="39" cy="49" r="4"></circle>
      <path class="glyph-soft" d="M55 63l10 11M31 31l6-9h22l6 9"></path>
    `);
  }
  return roomControlGlyphSvg("power", `
    <path class="glyph-stroke" d="M48 22v25"></path>
    <path class="glyph-stroke" d="M33 35a23 23 0 1 0 30 0"></path>
    <path class="glyph-soft" d="M26 27l-6-6M70 27l6-6"></path>
  `);
}

function updateRoomControlCard(control) {
  const card = document.querySelector(`[data-room-control-card="${control.id}"]`);
  if (!card) return;
  const on = Boolean(control.on);
  const linked = Boolean(control.haEntityId);
  const displayName = roomControlDisplayName(control);
  const statusOnly = linked && roomControlIsStatusOnly(control);
  const momentary = linked && roomControlIsMomentary(control);
  const unavailable = linked && isRoomControlUnavailableState(control.state);
  const bedSlider = roomControlIsBedSlider(control);
  const bedPosition = roomControlPositionPercent(control);
  card.classList.toggle("on", on);
  card.classList.toggle("off", !on);
  card.classList.toggle("linked", linked);
  card.classList.toggle("unlinked", !linked);
  card.classList.toggle("status-only", statusOnly);
  card.classList.toggle("momentary", momentary);
  card.classList.toggle("unavailable", unavailable);
  card.classList.toggle("bed-slider", bedSlider);
  card.dataset.roomControlBedSlider = bedSlider ? "1" : "";
  card.style.setProperty("--room-bed-position", `${bedPosition}%`);
  card.dataset.roomControlDomain = normalizeRoomControlDomain(control.domain || "switch");
  card.dataset.roomControlDeviceClass = normalizeRoomControlDeviceClass(control.deviceClass || "");
  card.dataset.roomControlVisual = roomControlVisualKind(control);
  card.dataset.roomControlCode = roomControlHasDefaultCode(control) ? "set" : "";
  const title = card.querySelector("[data-room-control-title]");
  if (title) {
    title.textContent = displayName;
    title.setAttribute("title", displayName);
  }
  const stateEl = card.querySelector("[data-room-control-state]");
  if (stateEl) stateEl.textContent = linked ? roomControlStateLabel(control) : "Assign";
  const domainEl = card.querySelector("[data-room-control-domain]");
  if (domainEl) domainEl.textContent = bedSlider ? "" : (linked ? roomControlTypeLabel(control) : "Unassigned");
  const icon = card.querySelector("[data-room-control-icon]");
  if (icon) icon.innerHTML = roomControlIconSvg(control);
  const hint = card.querySelector("[data-room-control-hint]");
  if (hint) {
    const nextAction = roomControlActionForState(control);
    if (!linked) hint.textContent = "Hold to assign";
    else if (bedSlider) hint.textContent = "";
    else if (statusOnly) hint.textContent = "Status only";
    else if (roomControlRequiresCodeForAction(control, nextAction)) hint.textContent = "Code required";
    else if (momentary) hint.textContent = "Tap to run";
    else hint.textContent = nextAction || "Tap";
  }
  const bedRange = card.querySelector("[data-room-control-bed-slider]");
  if (bedRange) {
    if (document.activeElement !== bedRange) bedRange.value = String(bedPosition);
    bedRange.disabled = !linked || normalizeRoomControlDomain(control.domain) !== "cover";
    bedRange.setAttribute("aria-valuetext", `${bedPosition}% raised`);
    setRangeVisual(bedRange);
  }
  const bedValue = card.querySelector("[data-room-control-bed-value]");
  if (bedValue) bedValue.textContent = `${bedPosition}%`;
  card.setAttribute("aria-label", roomControlAriaAction(control));
  card.setAttribute("aria-pressed", on ? "true" : "false");
}

function roomControlBedSliderMarkup(control) {
  const position = roomControlPositionPercent(control);
  const disabled = (!control.haEntityId || normalizeRoomControlDomain(control.domain) !== "cover") ? "disabled" : "";
  return `
    <span class="room-control-bed-panel" data-room-control-bed-panel>
      <span class="room-control-bed-slider-wrap">
        <span class="room-control-bed-rail" aria-hidden="true">
          <span class="room-control-bed-fill"></span>
          <span class="room-control-bed-tick room-control-bed-tick-100">100%</span>
          <span class="room-control-bed-tick room-control-bed-tick-50">50%</span>
          <span class="room-control-bed-tick room-control-bed-tick-0">0%</span>
        </span>
        <input class="room-control-bed-slider" type="range" min="0" max="100" step="1" value="${position}" ${disabled} data-room-control-bed-slider data-room-control-id="${escapeHtml(control.id)}" aria-label="Set ${escapeHtml(roomControlDisplayName(control))} position" aria-orientation="vertical" />
      </span>
    </span>
  `;
}

function setRoomControlPositionState(control, position, options = {}) {
  if (!control) return 0;
  const nextPosition = clamp(Math.round(Number(position || 0)), 0, 100);
  control.currentPosition = nextPosition;
  control.on = nextPosition > 0;
  control.state = nextPosition <= 0 ? "closed" : nextPosition >= 100 ? "open" : "open";
  if (options.hold !== false) control.localHoldUntil = Date.now() + ROOM_CONTROL_BED_SLIDER_HOLD_MS;
  updateRoomControlCard(control);
  if (options.save) saveConfig();
  return nextPosition;
}

function roomControlRequiresCodeForPosition(control, position) {
  return roomControlHasDefaultCode(control) && Number(position) > 0;
}

function handleRoomControlBedSliderInput(slider, options = {}) {
  if (!slider) return;
  const control = findRoomControlInActiveRoom(slider.dataset.roomControlId);
  if (!control) return;
  const position = setRoomControlPositionState(control, slider.value, { save: Boolean(options.send) });
  lastRoomControlUserInteractionAt = Date.now();
  suppressRoomControlClickUntil = Date.now() + 900;
  if (!options.send) return;
  if (!control.haEntityId || normalizeRoomControlDomain(control.domain) !== "cover") {
    showToast("Assign a cover.* entity to use the bed slider");
    return;
  }
  if (roomControlRequiresCodeForPosition(control, position)) {
    openRoomControlCodePrompt(control, "position", { position });
    return;
  }
  sendRoomControlToHomeAssistant(control, "position", "", { position });
}

function renderRoomControls() {
  const room = getActiveRoomControlRoom();
  if (elements.roomControlRoomTitle) elements.roomControlRoomTitle.textContent = room.label;
  renderRoomControlTabs();
  if (!elements.roomControlCards) return;
  elements.roomControlCards.innerHTML = "";
  const controls = Array.isArray(room.controls) ? room.controls : [];
  elements.roomControlCards.style.setProperty("--room-control-columns", "6");
  controls.forEach((control, index) => {
    normalizeRoomControlRecord(control, state.roomControl.room, index + 1);
    const isBedSlider = roomControlIsBedSlider(control);
    const card = document.createElement(isBedSlider ? "div" : "button");
    card.className = `room-control-card${isBedSlider ? " room-control-bed-card" : ""}`;
    if (isBedSlider) {
      card.tabIndex = 0;
      card.setAttribute("role", "group");
    } else {
      card.type = "button";
    }
    card.dataset.roomControlCard = control.id;
    card.dataset.roomControlToggle = "";
    card.dataset.roomControlId = control.id;
    card.dataset.roomKey = state.roomControl.room;
    const safeControlId = escapeHtml(control.id);
    const safeName = escapeHtml(roomControlDisplayName(control));
    card.innerHTML = `
      <span class="room-control-card-top">
        <span class="room-control-power-shell" aria-hidden="true">
          <span class="room-control-power-ring"></span>
          <span class="room-control-power-icon" data-room-control-icon>${roomControlIconSvg(control)}</span>
        </span>
        <em class="room-control-state-pill" data-room-control-state>${control.haEntityId ? escapeHtml(roomControlStateLabel(control)) : "Assign"}</em>
      </span>
      <span class="room-control-copy">
        <strong data-room-control-title title="${safeName}">${safeName}</strong>
        <small data-room-control-domain>${isBedSlider ? "" : (control.haEntityId ? escapeHtml(roomControlTypeLabel(control)) : "Unassigned")}</small>
        <em data-room-control-hint>${isBedSlider ? "" : (control.haEntityId ? escapeHtml(roomControlActionForState(control) || (roomControlIsStatusOnly(control) ? "Status only" : "Tap")) : "Hold to assign")}</em>
      </span>
      ${isBedSlider ? roomControlBedSliderMarkup(control) : ""}
      <span class="sr-only">${safeControlId}</span>
    `;
    elements.roomControlCards.appendChild(card);
    updateRoomControlCard(control);
  });
}

function resetRoomControlCodePrompt() {
  state.roomControlCodePrompt = { roomKey: null, controlId: null, action: "", code: "", busy: false, position: null };
}

function getPromptedRoomControl() {
  const prompt = state.roomControlCodePrompt || {};
  const room = state.roomControl.rooms?.[prompt.roomKey] || getActiveRoomControlRoom();
  return (room?.controls || []).find((item) => item.id === prompt.controlId) || null;
}

function openRoomControlCodePrompt(control, action, options = {}) {
  if (!control?.id || !action) return;
  state.roomControlCodePrompt = { roomKey: state.roomControl.room, controlId: control.id, action, code: "", busy: false, position: options.position ?? null };
  if (elements.roomControlCodeTitle) elements.roomControlCodeTitle.textContent = roomControlDisplayName(control);
  if (elements.roomControlCodeStatus) {
    elements.roomControlCodeStatus.textContent = "Enter default code.";
    elements.roomControlCodeStatus.dataset.error = "0";
  }
  renderRoomControlCodeDots();
  elements.roomControlCodeOverlay?.classList.add("open");
  elements.roomControlCodeOverlay?.setAttribute("aria-hidden", "false");
}

function closeRoomControlCodePrompt() {
  elements.roomControlCodeOverlay?.classList.remove("open");
  elements.roomControlCodeOverlay?.setAttribute("aria-hidden", "true");
  resetRoomControlCodePrompt();
}

function renderRoomControlCodeDots() {
  if (!elements.roomControlCodeDots) return;
  const control = getPromptedRoomControl();
  const code = String(state.roomControlCodePrompt?.code || "");
  const requiredLength = Math.max(4, String(control?.defaultCode || "").length || 4);
  elements.roomControlCodeDots.innerHTML = Array.from({ length: Math.min(requiredLength, ROOM_CONTROL_CODE_MAX_LENGTH) }, (_, index) =>
    `<span class="${index < code.length ? "filled" : ""}"></span>`
  ).join("");
}

function handleRoomControlCodeKey(value) {
  const prompt = state.roomControlCodePrompt || {};
  const control = getPromptedRoomControl();
  if (!control || prompt.busy) return;
  if (value === "clear") prompt.code = "";
  else if (value === "back") prompt.code = String(prompt.code || "").slice(0, -1);
  else if (/^\d$/.test(value) && String(prompt.code || "").length < ROOM_CONTROL_CODE_MAX_LENGTH) prompt.code = String(prompt.code || "") + value;
  state.roomControlCodePrompt = { ...prompt };
  if (elements.roomControlCodeStatus) {
    elements.roomControlCodeStatus.textContent = "Enter default code.";
    elements.roomControlCodeStatus.dataset.error = "0";
  }
  renderRoomControlCodeDots();
  if (String(control.defaultCode || "").length && String(prompt.code || "").length >= String(control.defaultCode || "").length) {
    submitRoomControlCode();
  }
}

function submitRoomControlCode() {
  const control = getPromptedRoomControl();
  const prompt = state.roomControlCodePrompt || {};
  if (!control || prompt.busy) return;
  const expected = String(control.defaultCode || "").replace(/\D/g, "");
  const entered = String(prompt.code || "").replace(/\D/g, "");
  if (!expected) {
    closeRoomControlCodePrompt();
    performRoomControlAction(control, prompt.action, "", { position: prompt.position });
    return;
  }
  if (entered !== expected) {
    state.roomControlCodePrompt = { ...prompt, code: "" };
    if (elements.roomControlCodeStatus) {
      elements.roomControlCodeStatus.textContent = "Incorrect code. Try again.";
      elements.roomControlCodeStatus.dataset.error = "1";
    }
    renderRoomControlCodeDots();
    return;
  }
  closeRoomControlCodePrompt();
  performRoomControlAction(control, prompt.action, entered, { position: prompt.position });
}

function setRoomControlPowerState(control, on, action = "") {
  if (!control) return;
  if (roomControlIsStatusOnly(control)) return;
  control.on = action ? roomControlOptimisticOn(control, action) : Boolean(on);
  control.state = control.on ? (normalizeRoomControlDomain(control.domain) === "cover" ? "open" : "on") : (normalizeRoomControlDomain(control.domain) === "cover" ? "closed" : "off");
  control.localHoldUntil = Date.now() + (roomControlIsMomentary(control) ? 900 : 1800);
}

async function applyRoomControlAction(action) {
  const room = getActiveRoomControlRoom();
  const controls = Array.isArray(room.controls) ? room.controls : [];
  if (!controls.length) return;
  const turnOn = String(action || "").includes("on");
  lastRoomControlUserInteractionAt = Date.now();
  const actionable = controls
    .filter((control) => control.haEntityId)
    .map((control) => ({ control, haAction: roomControlActionForState(control, turnOn) }))
    .filter((item) => item.haAction);
  const protectedTargets = actionable.filter(({ control, haAction }) => roomControlRequiresCodeForAction(control, haAction));
  const targets = actionable.filter(({ control, haAction }) => !roomControlRequiresCodeForAction(control, haAction));
  if (protectedTargets.length) showToast("Code-protected entries open one at a time");
  targets.forEach(({ control, haAction }) => setRoomControlPowerState(control, turnOn, haAction));
  saveConfig();
  renderRoomControls();
  if (!targets.length) return;
  await Promise.all(targets.map(({ control, haAction }) => sendRoomControlToHomeAssistant(control, haAction)));
}

function performRoomControlAction(control, action, code = "", options = {}) {
  if (!control || !action) return;
  lastRoomControlUserInteractionAt = Date.now();
  if (String(action).toLowerCase() === "position") {
    const position = setRoomControlPositionState(control, options.position ?? control.currentPosition ?? 0, { save: true });
    sendRoomControlToHomeAssistant(control, "position", code, { position });
    return;
  }
  setRoomControlPowerState(control, roomControlOptimisticOn(control, action), action);
  updateRoomControlCard(control);
  saveConfig();
  sendRoomControlToHomeAssistant(control, action, code);
}

function toggleRoomControl(controlId) {
  const control = findRoomControlInActiveRoom(controlId);
  if (!control) return;
  if (!control.haEntityId) {
    showToast("Hold the card to assign a Home Assistant entity");
    return;
  }
  const action = roomControlActionForState(control);
  if (!action) {
    showToast(`${roomControlDisplayName(control)} is status only`);
    return;
  }
  if (roomControlRequiresCodeForAction(control, action)) {
    openRoomControlCodePrompt(control, action);
    return;
  }
  performRoomControlAction(control, action);
}

function syncLinkedRoomControlsFromEntities(entities = [], options = {}) {
  if (!entities.length) return false;
  let changed = false;
  Object.values(state.roomControl.rooms || {}).forEach((room) => {
    (room.controls || []).forEach((control) => {
      if (!control.haEntityId) return;
      const entity = entities.find((item) => item.entityId === control.haEntityId);
      if (!entity) return;
      changed = applyEntityStateToRoomControl(control, entity) || changed;
    });
  });
  if (changed && !options.skipSave) saveConfig();
  return changed;
}

function getLinkedRoomControlEntityIds() {
  const ids = [];
  Object.values(state.roomControl.rooms || {}).forEach((room) => {
    (room.controls || []).forEach((control) => {
      if (control.haEntityId && !ids.includes(control.haEntityId)) ids.push(control.haEntityId);
    });
  });
  return ids;
}

async function fetchLinkedRoomControlStatesViaLocalBackend(entityIds) {
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token || !entityIds.length) return [];
  const payload = await fetchJsonWithTimeout("/api/ha/room/states", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: baseUrl, token: ha.token, entityIds }),
  }, 9000);
  return payload.controls || [];
}

function scheduleRoomControlSync() {
  HA_ROOM_SYNC_AFTER_COMMAND_DELAYS.forEach((delay) => {
    window.setTimeout(() => pollHomeAssistantRoomControls({ force: true }), delay);
  });
}

async function pollHomeAssistantRoomControls(options = {}) {
  const linkedEntityIds = getLinkedRoomControlEntityIds();
  if (!linkedEntityIds.length) return;
  if (!options.force && document.visibilityState === "hidden") return;
  const now = Date.now();
  if (!options.force && state.currentPage !== "room") {
    if (now - lastInactiveRoomSyncAt < INACTIVE_PAGE_SYNC_INTERVAL_MS) return;
    lastInactiveRoomSyncAt = now;
  }
  if (!options.force && state.currentPage === "room" && now - lastRoomControlUserInteractionAt < 1200) return;
  if (haRoomSyncInFlight || roomControlCommandInFlight) return;

  haRoomSyncInFlight = true;
  try {
    const controls = await fetchLinkedRoomControlStatesViaLocalBackend(linkedEntityIds);
    const changed = syncLinkedRoomControlsFromEntities(controls);
    if (changed) renderRoomControls();
    if (haRoomSyncLastError) haRoomSyncLastError = "";
  } catch (error) {
    const message = error.message || String(error);
    if (message !== haRoomSyncLastError) {
      haRoomSyncLastError = message;
      addHaLog("warn", "Live room control sync paused", message);
    }
  } finally {
    haRoomSyncInFlight = false;
  }
}

async function sendRoomControlToHomeAssistant(control, action = "toggle", code = "", options = {}) {
  if (!control?.haEntityId) return null;
  if (!action) return null;
  const ha = state.integrations.homeAssistant;
  const baseUrl = getHaBaseUrl();
  if (!baseUrl || !ha.token) return null;
  roomControlCommandInFlight = true;
  try {
    const payload = await fetchJsonWithTimeout("/api/ha/room/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: baseUrl,
        token: ha.token,
        entityId: control.haEntityId,
        action,
        code: String(code || "").replace(/\D/g, ""),
        position: Number.isFinite(Number(options.position)) ? clamp(Math.round(Number(options.position)), 0, 100) : undefined,
      }),
    }, 9000);
    if (payload.control) {
      delete control.localHoldUntil;
      applyEntityStateToRoomControl(control, payload.control);
      saveConfig();
      renderRoomControls();
    }
    scheduleRoomControlSync();
    return payload.control || null;
  } catch (error) {
    addHaLog("error", "Room control command failed", error.message || String(error));
    showToast("Room command failed");
    return null;
  } finally {
    roomControlCommandInFlight = false;
  }
}

function openRoomControlEntityPicker(roomKey, controlId) {
  state.roomControlEntityPicker = { roomKey, controlId };
  openAudioEntityPicker("roomControl");
}

function assignEntityToRoomControl(entity) {
  const { roomKey, controlId } = state.roomControlEntityPicker;
  const room = state.roomControl.rooms[roomKey];
  const control = room?.controls?.find((item) => item.id === controlId);
  if (!control || !entity) return;
  control.haEntityId = entity.entityId || "";
  control.haName = entity.name || entity.entityId || "";
  control.domain = normalizeRoomControlDomain(entity.domain || String(entity.entityId || "").split(".", 1)[0], "switch");
  control.deviceClass = normalizeRoomControlDeviceClass(entity.deviceClass || "");
  control.supportedFeatures = Number(entity.supportedFeatures || 0) || 0;
  control.unitOfMeasurement = entity.unitOfMeasurement || "";
  control.icon = entity.icon || "";
  if (entity.name) control.name = entity.name;
  applyEntityStateToRoomControl(control, entity);
  state.roomControlEntityPicker = { roomKey: null, controlId: null };
  closeAudioEntityPicker();
  saveConfig({ toast: true });
  renderRoomControlConfigList();
  renderRoomControls();
  pollHomeAssistantRoomControls({ force: true });
}

function getHaBaseUrl() {
  return String(state.integrations.homeAssistant.url || "").replace(/\/+$/, "");
}

function hasValue(value) {
  return value !== undefined && value !== null && value !== "";
}

function coverSupportsTilt(entity) {
  if (!entity) return false;
  if (hasValue(entity.currentTiltPosition)) return true;
  const supportedFeatures = Number(entity.supportedFeatures || 0);
  return Boolean(supportedFeatures & COVER_TILT_FEATURE_MASK);
}

function getBlindCoverEntity(blind) {
  if (!blind?.haEntityId) return null;
  return findCoverEntity(blind.haEntityId) || null;
}

function shouldUseTiltForBlind(blind, entity = null) {
  return coverSupportsTilt(entity || getBlindCoverEntity(blind));
}

function normalizeHaPosition(entity, fallback = 50) {
  const tiltRaw = entity?.currentTiltPosition;
  if (coverSupportsTilt(entity) && hasValue(tiltRaw)) return clamp(Math.round(Number(tiltRaw)), 0, 100);
  const raw = entity?.currentPosition;
  if (hasValue(raw)) return clamp(Math.round(Number(raw)), 0, 100);
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
  if (entity.name) {
    blind.haName = entity.name;
    blind.name = entity.name;
  }
  const hold = getBlindCommandHold(blind);
  if (hold && !isBlindCommandHoldResolved(blind, entity)) {
    // Home Assistant can report the old physical position while the motor is still moving.
    // Keep the UI at the requested target until the real cover catches up or the hold expires.
    blind.position = hold.target;
    return;
  }
  blind.position = normalizeHaPosition(entity, blind.position);
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
  if (!options.force && document.visibilityState === "hidden") return;
  const now = Date.now();
  if (!options.force && state.currentPage !== "blinds") {
    if (now - lastInactiveBlindSyncAt < INACTIVE_PAGE_SYNC_INTERVAL_MS) return;
    lastInactiveBlindSyncAt = now;
  }
  if (!options.force && state.currentPage === "blinds" && now - lastBlindUserInteractionAt < 1200) return;
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

function resolveBlindCoverCommand(blind, action, position = null) {
  const entity = getBlindCoverEntity(blind);
  if (!shouldUseTiltForBlind(blind, entity)) return { action, position };

  const supportedFeatures = Number(entity?.supportedFeatures || 0);
  const canOpenTilt = Boolean(supportedFeatures & COVER_FEATURE_OPEN_TILT);
  const canCloseTilt = Boolean(supportedFeatures & COVER_FEATURE_CLOSE_TILT);

  if (action === "position") return { action: "tilt", position };
  if (action === "open") return canOpenTilt ? { action: "open_tilt", position: null } : { action: "tilt", position: 100 };
  if (action === "close") return canCloseTilt ? { action: "close_tilt", position: null } : { action: "tilt", position: 0 };
  return { action, position };
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
  const command = resolveBlindCoverCommand(blind, action, position);

  if (optimistic !== null && optimistic !== undefined) {
    blind.position = clamp(Math.round(Number(optimistic)), 0, 100);
    markBlindCommandHold(blind, blind.position);
  }
  renderBlinds();

  try {
    const entityState = await callCoverActionViaLocalBackend(blind.haEntityId, command.action, command.position);
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
    clearBlindCommandHold(blind);
    addHaLog("error", `Cover ${command.action} failed`, `${blind.haEntityId}: ${error.message || error}`);
    showToast("Home Assistant cover command failed");
    renderBlinds();
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
    if (isPanelLocked()) return;
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
  const updateFromEvent = (event) => setTargetTemp(pointerToTemp(event.clientX, event.clientY), { preview: true });
  elements.thermoDial.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault(); event.stopPropagation(); dragging = true;
    elements.thermoDial.classList.add("dial-adjusting");
    elements.thermoDial.setPointerCapture(event.pointerId); updateFromEvent(event);
  });
  elements.thermoDial.addEventListener("pointermove", (event) => { if (dragging) { event.preventDefault(); updateFromEvent(event); } });
  const finishDial = (event) => {
    if (!dragging) return;
    dragging = false;
    elements.thermoDial.classList.remove("dial-adjusting");
    holdSetpointPreview();
    try { elements.thermoDial.releasePointerCapture(event.pointerId); } catch (_) {}
  };
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

function bindLightInteractions() {
  if (!elements.lightCards) return;
  let timer = null;
  let press = null;
  let iconTimer = null;
  let iconPress = null;

  const cancel = () => {
    clearTimeout(timer);
    timer = null;
    press = null;
  };

  const cancelIconHold = () => {
    clearTimeout(iconTimer);
    iconTimer = null;
    iconPress = null;
  };

  elements.lightCards.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    const iconButton = event.target.closest("[data-light-icon-toggle]");
    if (iconButton) {
      const light = findLightInActiveRoom(iconButton.dataset.lightId);
      if (!light?.colorSupported) return;
      iconPress = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        lightId: iconButton.dataset.lightId,
      };
      try { iconButton.setPointerCapture(event.pointerId); } catch (_) {}
      iconTimer = window.setTimeout(() => {
        const pending = iconPress;
        cancelIconHold();
        if (!pending) return;
        suppressLightIconClickUntil = Date.now() + 700;
        openLightColorPicker(pending.lightId);
      }, 650);
      return;
    }

    if (event.target.closest("[data-light-slider], [data-light-color-open], .light-color-wheel")) return;
    const card = event.target.closest("[data-light-card]");
    if (!card) return;
    press = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      roomKey: card.dataset.roomKey,
      lightId: card.dataset.lightCard,
    };
    timer = window.setTimeout(() => {
      const pending = press;
      cancel();
      if (pending) openLightEntityPicker(pending.roomKey, pending.lightId);
    }, 900);
  });

  elements.lightCards.addEventListener("pointermove", (event) => {
    if (press && press.pointerId === event.pointerId) {
      const dx = Math.abs(event.clientX - press.x);
      const dy = Math.abs(event.clientY - press.y);
      if (dx > 12 || dy > 12) cancel();
    }
    if (iconPress && iconPress.pointerId === event.pointerId) {
      const dx = Math.abs(event.clientX - iconPress.x);
      const dy = Math.abs(event.clientY - iconPress.y);
      if (dx > 12 || dy > 12) cancelIconHold();
    }
  });

  ["pointerup", "pointercancel", "pointerleave"].forEach((name) => {
    elements.lightCards.addEventListener(name, () => {
      cancel();
      cancelIconHold();
    });
  });
}

function getAudioPickerMeta(kind) {
  if (kind === "media") return { domain: "media_player", title: "Choose Media Device", help: "Select the media_player this card should control." };
  if (["gain", "bass", "treble"].includes(kind)) return { domain: "number", title: `Assign ${titleCase(kind)}`, help: "Select the Home Assistant number entry for this control." };
  if (["subwoofer", "surround", "projector"].includes(kind)) return { domain: "switch", title: `Assign ${titleCase(kind)}`, help: "Select the Home Assistant switch entry for this button." };
  if (kind === "alarm") return { domain: "alarm_control_panel", title: "Assign Alarm", help: "Select the Home Assistant alarm_control_panel entry for this thermostat page." };
  if (kind === "door") return { domain: "binary_sensor", title: "Assign Door Sensor", help: "Select the Home Assistant binary_sensor that reports this door open or closed." };
  if (kind === "roomControl") return {
    domain: "entity",
    domains: ROOM_CONTROL_PICKER_DOMAINS,
    allDomains: true,
    title: "Assign Room Device",
    excludeDomains: ROOM_CONTROL_EXCLUDED_DOMAINS,
    help: "Select any useful Home Assistant entity. Automations are hidden, and the card will choose the icon, status, and action from its domain and device class."
  };
  if (kind === "thermostatPerson") return { domain: "person", title: "Add Person", help: "Select the Home Assistant person entry that should control Home/Away mode." };
  if (kind === "thermostatTemp") return { domain: "sensor", title: "Choose Current Temp Sensor", help: "Select the Home Assistant sensor used for the thermostat current room temperature. The virtual slider will temporarily override it for 2 minutes." };
  if (kind === "pauseFunction") return { domain: "entity", domains: ["binary_sensor", "cover", "switch", "input_boolean"], title: "Add Pause Entry", help: "Select doors, windows, covers, switches, or input_booleans. Any selected entry that stays open/on past the delay will pause comfort." };
  if (kind === "light") return { domain: "light", title: "Assign Light", help: "Select the Home Assistant light entry for this slider." };
  return { domain: "", title: "Assign Entity", help: "Select the Home Assistant entity for this control." };
}

function scoreEntityForSearch(entity, search) {
  const q = String(search || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  if (!q) return 1;
  const hay = `${entity.name || ""} ${entity.entityId || ""} ${entity.domain || ""} ${entity.deviceClass || ""} ${entity.state || ""}`.toLowerCase();
  const compact = hay.replace(/[^a-z0-9]+/g, "");
  if (compact.includes(q)) return 10;
  const words = q.match(/[a-z0-9]+/g) || [];
  return words.every((w) => compact.includes(w)) ? 5 : 0;
}

function renderAudioEntityPicker() {
  const picker = state.audioEntityPicker;
  if (!elements.audioEntityPickerList) return;
  const search = elements.audioEntitySearch?.value || picker.search || "";
  const entities = (picker.entities || [])
    .filter((entity) => picker.kind !== "roomControl" || isRoomControlEntityAllowed(entity))
    .filter((entity) => picker.kind !== "thermostatTemp" || isLikelyTemperatureSensor(entity))
    .filter((entity) => scoreEntityForSearch(entity, search) > 0);
  if (elements.audioEntityPickerTitle) elements.audioEntityPickerTitle.textContent = getAudioPickerMeta(picker.kind).title;
  if (elements.audioEntityPickerHelp) elements.audioEntityPickerHelp.textContent = getAudioPickerMeta(picker.kind).help;
  const selectedPauseIds = picker.kind === "pauseFunction" ? new Set(getPauseFunction().entries.map((entry) => entry.entityId)) : new Set();
  elements.audioEntityPickerList.innerHTML = entities.length ? entities.map((entity) => {
    const stateText = entity.state !== undefined && entity.state !== null ? String(entity.state) : "";
    let valueText = entity.domain === "number" && entity.value !== null && entity.value !== undefined ? `Value ${formatControlValue(entity.value)}` : stateText;
    if (picker.kind === "roomControl") {
      const domainLabel = prettifyRoomControlName(entity.domain || "entity");
      const classLabel = entity.deviceClass ? ` · ${prettifyRoomControlName(entity.deviceClass)}` : "";
      const stateLabel = stateText ? ` · ${prettifyRoomControlName(stateText)}` : "";
      valueText = `${domainLabel}${classLabel}${stateLabel}`;
    }
    const selected = selectedPauseIds.has(entity.entityId);
    return `
      <button class="audio-entity-row ${selected ? "selected" : ""}" data-audio-entity-id="${escapeHtml(entity.entityId)}">
        <strong>${escapeHtml(entity.name || entity.entityId)}${selected ? " ✓" : ""}</strong>
        <span>${escapeHtml(entity.entityId)}</span>
        <em>${escapeHtml(selected ? "Selected" : (valueText || entity.domain || ""))}</em>
      </button>
    `;
  }).join("") : `<div class="empty-state compact">No matching entities.</div>`;
}

async function openAudioEntityPicker(kind) {
  audioPickerOpenedAt = Date.now();
  if (kind === "alarm") alarmPickerOpenedAt = Date.now();
  if (kind === "door") doorPickerOpenedAt = Date.now();
  const meta = getAudioPickerMeta(kind);
  if (!meta.domain) return;
  if (kind === "light") readHaFieldsFromScreen("lights");
  else if (!["alarm", "door", "roomControl", "thermostatPerson", "pauseFunction"].includes(kind)) readHaFieldsFromScreen("audio");
  if (!getHaBaseUrl() || !state.integrations.homeAssistant.token) {
    showToast(["alarm", "door", "roomControl", "thermostatPerson", "thermostatTemp", "pauseFunction"].includes(kind) ? "Add Home Assistant config from Blinds, Audio, or Lights settings first" : "Add Home Assistant config first");
    if (kind === "light") { openSettings(); showLightsHaView(); }
    else if (!["alarm", "door", "roomControl", "thermostatPerson", "thermostatTemp", "pauseFunction"].includes(kind)) { openSettings(); showAudioHaView(); }
    return;
  }
  state.audioEntityPicker = { kind, domain: meta.domain, entities: [], search: "" };
  elements.audioEntityPickerOverlay?.classList.add("open");
  elements.audioEntityPickerOverlay?.setAttribute("aria-hidden", "false");
  if (elements.audioEntitySearch) {
    elements.audioEntitySearch.value = "";
    window.setTimeout(() => elements.audioEntitySearch.focus(), 80);
  }
  renderAudioEntityPicker();
  try {
    const domains = meta.allDomains ? [] : (Array.isArray(meta.domains) && meta.domains.length ? meta.domains : [meta.domain]);
    let entities = await fetchHaEntitiesViaLocalBackend(domains);
    if (kind === "roomControl") entities = entities.filter(isRoomControlEntityAllowed);
    state.audioEntityPicker.entities = entities;
    const ha = state.integrations.homeAssistant;
    if (!ha.audioAvailableEntities) ha.audioAvailableEntities = { mediaPlayers: [], numbers: [], switches: [] };
    if (meta.domain === "media_player") ha.audioAvailableEntities.mediaPlayers = entities;
    if (meta.domain === "number") ha.audioAvailableEntities.numbers = entities;
    if (meta.domain === "switch") ha.audioAvailableEntities.switches = entities;
    if (meta.domain === "alarm_control_panel") ha.alarmAvailableEntities = entities;
    if (meta.domain === "binary_sensor") ha.doorAvailableEntities = entities;
    if (meta.domain === "light") ha.lightAvailableEntities = entities;
    if (meta.domain === "person") ha.personAvailableEntities = entities;
    if (kind === "pauseFunction") ha.pauseFunctionAvailableEntities = entities;
    if (kind === "thermostatTemp") ha.currentTempAvailableEntities = entities;
    if (kind === "roomControl") ha.roomAvailableEntities = entities;
    renderAudioEntityPicker();
  } catch (error) {
    addHaLog("error", kind === "roomControl" ? "Room entity load failed" : "Audio entity load failed", error.message || String(error));
    showToast("Could not load Home Assistant entities");
  }
}

function closeAudioEntityPicker() {
  elements.audioEntityPickerOverlay?.classList.remove("open");
  elements.audioEntityPickerOverlay?.setAttribute("aria-hidden", "true");
  state.audioEntityPicker = { kind: null, domain: null, entities: [], search: "" };
}

function selectAudioEntity(entityId) {
  const picker = state.audioEntityPicker;
  const entity = (picker.entities || []).find((item) => item.entityId === entityId);
  if (!entity) return;
  const kind = picker.kind;
  if (kind === "media") {
    upsertMediaPlayerEntity(entity);
    state.integrations.homeAssistant.selectedMediaPlayerId = entity.entityId;
    applyMediaEntityState(entity);
    closeAudioEntityPicker();
    saveConfig({ toast: true });
    loadAudioControlsForSelected({ quiet: true });
    scheduleAudioSync();
    renderAudio();
    return;
  }
  if (["gain", "bass", "treble"].includes(kind)) {
    const control = { ...entity, kind };
    getAudioToneControls()[kind] = control;
    state.audio[kind] = normalizeAudioControlValue(control, state.audio[kind]);
    closeAudioEntityPicker();
    saveConfig({ toast: true });
    renderAudio();
    return;
  }
  if (["subwoofer", "surround", "projector"].includes(kind)) {
    getAudioToneControls()[kind] = entity;
    state.audio[kind] = isSwitchOn(entity);
    closeAudioEntityPicker();
    saveConfig({ toast: true });
    renderAudio();
    return;
  }
  if (kind === "alarm") {
    state.integrations.homeAssistant.alarmEntity = { ...entity };
    applyAlarmEntityState(entity);
    closeAudioEntityPicker();
    saveConfig({ toast: true });
    renderAlarmWidget();
    pollHomeAssistantAlarm({ force: true });
    return;
  }
  if (kind === "door") {
    state.integrations.homeAssistant.doorEntity = { ...entity };
    applyDoorEntityState(entity);
    closeAudioEntityPicker();
    saveConfig({ toast: true });
    renderDoorWidget();
    scheduleDoorSync();
    return;
  }
  if (kind === "thermostatPerson") {
    upsertThermostatPerson(entity);
    return;
  }
  if (kind === "thermostatTemp") {
    assignCurrentTempSensor(entity);
    return;
  }
  if (kind === "pauseFunction") {
    upsertPauseFunctionEntry(entity);
    renderAudioEntityPicker();
    return;
  }
  if (kind === "roomControl") {
    assignEntityToRoomControl(entity);
    return;
  }
  if (kind === "light") {
    assignEntityToLight(entity);
  }
}

function bindLongPress(target, callback, options = {}) {
  if (!target) return;
  let timer = null;
  let startX = 0;
  let startY = 0;
  const delay = options.delay || 3000;
  const cancel = () => { if (timer) window.clearTimeout(timer); timer = null; };
  target.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    if (!target.matches("[data-audio-picker]") && event.target.closest("[data-audio-picker]")) return;
    if (!options.allowInteractive && event.target.closest("input, select, textarea, button") && !event.target.closest("[data-audio-picker]")) return;
    startX = event.clientX; startY = event.clientY;
    timer = window.setTimeout(() => { timer = null; callback(event); }, delay);
  });
  target.addEventListener("pointermove", (event) => {
    if (!timer) return;
    if (Math.abs(event.clientX - startX) > 12 || Math.abs(event.clientY - startY) > 12) cancel();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach((name) => target.addEventListener(name, cancel));
}

function bindEvents() {
  document.querySelectorAll(".nav-pill").forEach((button) => button.addEventListener("click", () => gotoPage(button.dataset.goto)));
  elements.thermostatPanelLockButton?.addEventListener("click", togglePanelLock);
  elements.changeoverBypassButton?.addEventListener("click", bypassChangeoverLockout);
  elements.autoSwitchNotice?.addEventListener("click", openAutoSwitchOverlay);
  elements.autoSwitchClose?.addEventListener("click", closeAutoSwitchOverlay);
  elements.autoSwitchDismissButton?.addEventListener("click", dismissAutoSwitchNotice);
  elements.autoSwitchRevertButton?.addEventListener("click", revertAutoSwitchNotice);
  elements.autoSwitchOverlay?.querySelector("[data-close-auto-switch]")?.addEventListener("click", closeAutoSwitchOverlay);
  elements.autoConfirmClose?.addEventListener("click", closeAutoConfirmOverlay);
  elements.autoConfirmCancelButton?.addEventListener("click", closeAutoConfirmOverlay);
  elements.autoConfirmSwitchButton?.addEventListener("click", confirmAutoMode);
  elements.autoConfirmOverlay?.querySelector("[data-close-auto-confirm]")?.addEventListener("click", closeAutoConfirmOverlay);
  document.getElementById("tempDown").addEventListener("click", () => adjustSetpoint(-1));
  document.getElementById("tempUp").addEventListener("click", () => adjustSetpoint(1));
  elements.awayToggle.addEventListener("click", toggleAway);
  elements.awayHomeButton?.addEventListener("click", setHomeMode);
  elements.fanChip?.addEventListener("click", cycleFanMode);
  elements.scheduleButton?.addEventListener("click", openScheduleOverlay);
  elements.schedulePresetBar?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-schedule-preset-id]");
    if (button) applySchedulePreset(button.dataset.schedulePresetId);
  });
  elements.scheduleClose?.addEventListener("click", closeScheduleOverlay);
  document.querySelectorAll("[data-close-schedule]").forEach((el) => el.addEventListener("click", closeScheduleOverlay));
  elements.scheduleAddButton?.addEventListener("click", addThermostatSchedule);
  elements.scheduleSaveButton?.addEventListener("click", () => commitScheduleDraft({ toast: true }));
  elements.scheduleDeleteButton?.addEventListener("click", deleteSelectedSchedule);
  elements.scheduleEnabledToggle?.addEventListener("click", toggleScheduleEnabled);
  elements.scheduleNameInput?.addEventListener("input", updateScheduleDraftFromInputs);
  elements.scheduleTimeInput?.addEventListener("pointerdown", openScheduleTimePicker);
  elements.scheduleTimeInput?.addEventListener("click", openScheduleTimePicker);
  elements.scheduleTimeInput?.addEventListener("keydown", (event) => {
    if (["Enter", " "].includes(event.key)) openScheduleTimePicker(event);
  });
  elements.scheduleTimePickerClose?.addEventListener("click", closeScheduleTimePicker);
  elements.scheduleTimePickerCancel?.addEventListener("click", closeScheduleTimePicker);
  elements.scheduleTimePickerApply?.addEventListener("click", applyScheduleTimePicker);
  elements.scheduleTimePickerOverlay?.addEventListener("click", (event) => {
    if (event.target.closest("[data-close-schedule-time]")) { closeScheduleTimePicker(); return; }
    const adjust = event.target.closest("[data-schedule-time-adjust]");
    if (adjust) { adjustScheduleTimePickerPart(adjust.dataset.scheduleTimeAdjust, Number(adjust.dataset.delta || 0)); return; }
    const meridiem = event.target.closest("[data-schedule-time-meridiem]");
    if (meridiem) { setScheduleTimePickerMeridiem(meridiem.dataset.scheduleTimeMeridiem); return; }
    const minute = event.target.closest("[data-schedule-minute]");
    if (minute) setScheduleTimePickerMinute(Number(minute.dataset.scheduleMinute || 0));
  });
  elements.scheduleList?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-schedule-id]");
    if (row) selectSchedule(row.dataset.scheduleId);
  });
  elements.schedulePersonChips?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-schedule-person]");
    if (chip) toggleSchedulePerson(chip.dataset.schedulePerson);
  });
  elements.scheduleOverlay?.addEventListener("click", (event) => {
    const step = event.target.closest("[data-schedule-step]");
    if (step) adjustScheduleDraftSetpoint(step.dataset.scheduleStep, Number(step.dataset.delta || 0));
  });
  elements.virtualTempSlider?.addEventListener("input", (event) => setVirtualCurrentTemp(event.target.value));
  elements.outdoorTempSlider?.addEventListener("input", (event) => setVirtualOutdoorTemp(event.target.value));
  elements.doorWidget?.addEventListener("click", () => {
    if (Date.now() - doorPickerOpenedAt < 900) return;
    lastDoorUserInteractionAt = Date.now();
    openDoorPanel();
  });
  bindLongPress(elements.doorWidget, (event) => {
    event.preventDefault();
    event.stopPropagation();
    openAudioEntityPicker("door");
  }, { delay: 1200, allowInteractive: true });
  elements.alarmWidget?.addEventListener("click", () => {
    if (Date.now() - alarmPickerOpenedAt < 900) return;
    openAlarmPanel();
  });
  bindLongPress(elements.alarmWidget, (event) => {
    event.preventDefault();
    event.stopPropagation();
    openAudioEntityPicker("alarm");
  }, { delay: 1200, allowInteractive: true });
  elements.alarmKeypadClose?.addEventListener("click", closeAlarmKeypad);
  document.querySelectorAll("[data-close-alarm-keypad]").forEach((el) => el.addEventListener("click", closeAlarmKeypad));
  elements.alarmKeypadGrid?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-alarm-key]");
    if (button) handleAlarmKey(button.dataset.alarmKey);
  });
  elements.alarmDisarmButton?.addEventListener("click", () => sendAlarmDisarm());
  elements.alarmArmHomeButton?.addEventListener("click", () => sendAlarmArm("arm_home"));
  elements.alarmArmAwayButton?.addEventListener("click", startAlarmAwayCountdown);
  elements.alarmArmCancelButton?.addEventListener("click", () => closeAlarmArmOptions());
  document.querySelectorAll("[data-cancel-alarm-arm]").forEach((el) => el.addEventListener("click", () => closeAlarmArmOptions()));
  elements.saveAlarmCodeButton?.addEventListener("click", () => saveAlarmCode({ toast: true }));
  elements.alarmDisarmCodeInput?.addEventListener("input", (event) => {
    event.target.value = String(event.target.value || "").replace(/\D/g, "").slice(0, 8);
  });
  elements.saveUserAccessCodeButton?.addEventListener("click", () => saveUserAccessCode({ toast: true }));
  elements.userAccessCodeInput?.addEventListener("input", (event) => {
    event.target.value = String(event.target.value || "").replace(/\D/g, "").slice(0, 4);
  });
  elements.saveThermostatNameButton?.addEventListener("click", () => saveThermostatName({ toast: true, force: true }));
  elements.thermostatNameInput?.addEventListener("change", () => saveThermostatName({ toast: false }));

  (elements.themeChoiceButtons || []).forEach((button) => button.addEventListener("click", () => applyPanelTheme(button.dataset.themeChoice, { toast: true })));
  elements.screenTimeoutMinutesInput?.addEventListener("input", (event) => {
    event.target.value = String(event.target.value || "").replace(/\D/g, "").slice(0, 3);
    if (event.target.value) {
      state.screenTimeoutMinutes = normalizeScreenTimeoutMinutes(event.target.value);
      renderScreenTimeoutSettings();
    }
  });
  elements.screenTimeoutMinutesInput?.addEventListener("change", (event) => setScreenTimeoutMinutes(event.target.value, { toast: true }));

  ["pointerdown", "touchstart", "wheel"].forEach((eventName) => {
    window.addEventListener(eventName, markScreenActivity, { capture: true, passive: true });
  });
  window.addEventListener("keydown", markScreenActivity, { capture: true });

  elements.settingsCodeClose?.addEventListener("click", closeSettingsCodePrompt);
  document.querySelectorAll("[data-close-settings-code]").forEach((el) => el.addEventListener("click", closeSettingsCodePrompt));
  elements.settingsCodeGrid?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-settings-key]");
    if (button) handleSettingsCodeKey(button.dataset.settingsKey);
  });
  elements.roomControlCodeClose?.addEventListener("click", closeRoomControlCodePrompt);
  document.querySelectorAll("[data-close-room-control-code]").forEach((el) => el.addEventListener("click", closeRoomControlCodePrompt));
  elements.roomControlCodeGrid?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-room-control-code-key]");
    if (button) handleRoomControlCodeKey(button.dataset.roomControlCodeKey);
  });
  elements.settingsButton.addEventListener("click", () => isPanelLocked() ? requestPanelUnlock() : openSettingsCodePrompt("settings"));
  elements.openHardwareInfoButton?.addEventListener("click", showHardwareInfoView);
  elements.openHistoryButton?.addEventListener("click", showHistoryView);
  elements.backToComfortSetup?.addEventListener("click", showComfortSetupView);
  elements.backToComfortSetupFromHistory?.addEventListener("click", showComfortSetupView);
  elements.refreshHardwareButton?.addEventListener("click", () => fetchHardwareStatus({ force: true }));
  elements.refreshHistoryButton?.addEventListener("click", () => fetchHistoryStatus({ force: true }));
  elements.historyDateInput?.addEventListener("change", (event) => setHistoryDate(event.target.value, { toast: false }));
  elements.historyPrevDayButton?.addEventListener("click", () => setHistoryDate(shiftDateKey(state.history.selectedDate, -1)));
  elements.historyNextDayButton?.addEventListener("click", () => setHistoryDate(shiftDateKey(state.history.selectedDate, 1)));
  elements.releaseHardwareManualButton?.addEventListener("click", releaseHardwareManualControl);
  (elements.hardwareRelayButtons || []).forEach((button) => button.addEventListener("click", () => sendHardwareRelayCommand(button.dataset.hardwareRelay)));
  elements.hardwareRgbPowerButton?.addEventListener("click", () => sendHardwareRgbCommand());
  elements.hardwareRgbColorInput?.addEventListener("change", (event) => sendHardwareRgbCommand({ on: true, color: event.target.value }));
  elements.hardwareRgbPresetGrid?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-hardware-rgb-color]");
    if (!button) return;
    sendHardwareRgbCommand({ on: true, color: button.dataset.hardwareRgbColor });
  });
  elements.settingsClose.addEventListener("click", closeSettings);
  elements.settingsDone.addEventListener("click", closeSettings);
  document.querySelectorAll("[data-close-settings]").forEach((el) => el.addEventListener("click", closeSettings));
  elements.thermostatInfoButton?.addEventListener("click", () => isPanelLocked() ? requestPanelUnlock() : openSettingsCodePrompt("info"));
  elements.thermostatInfoClose?.addEventListener("click", closeThermostatInfo);
  elements.fetchUpdateButton?.addEventListener("click", fetchPanelUpdate);
  elements.restartServerButton?.addEventListener("click", restartPanelServer);
  document.querySelectorAll("[data-close-thermostat-info]").forEach((el) => el.addEventListener("click", closeThermostatInfo));
  elements.lightColorPickerClose?.addEventListener("click", closeLightColorPicker);
  document.querySelectorAll("[data-close-light-color]").forEach((el) => el.addEventListener("click", closeLightColorPicker));
  elements.lightColorPresetGrid?.addEventListener("click", (event) => {
    const preset = event.target.closest("[data-light-preset-color]");
    if (!preset) return;
    applyLightPresetColor(preset.dataset.lightPresetColor);
  });
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
  document.getElementById("addLightRoomButton")?.addEventListener("click", addLightRoom);
  document.getElementById("addRoomControlRoomButton")?.addEventListener("click", addRoomControlRoom);
  document.getElementById("openLightHaConfig")?.addEventListener("click", showLightsHaView);
  document.getElementById("backToLightsSetup")?.addEventListener("click", showLightsSetupView);
  document.getElementById("saveLightHaConfig")?.addEventListener("click", () => { saveHaFields("lights"); showLightsSetupView(); });
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

  elements.lightRoomConfigList?.addEventListener("input", (event) => {
    const roomNameInput = event.target.closest("[data-light-room-name-input]");
    const lightNameInput = event.target.closest("[data-light-name-input]");
    if (roomNameInput) renameLightRoom(roomNameInput.dataset.roomKey, roomNameInput.value);
    if (lightNameInput) renameLight(lightNameInput.dataset.roomKey, lightNameInput.dataset.lightId, lightNameInput.value);
  });
  elements.lightRoomConfigList?.addEventListener("change", (event) => {
    const countSelect = event.target.closest("[data-light-room-count-select]");
    if (countSelect) setRoomLightCount(countSelect.dataset.roomKey, countSelect.value);
  });
  elements.lightRoomConfigList?.addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-delete-light-room]");
    if (deleteButton) deleteLightRoom(deleteButton.dataset.deleteLightRoom);
  });

  elements.roomControlConfigList?.addEventListener("input", (event) => {
    const roomNameInput = event.target.closest("[data-room-control-room-name-input]");
    const controlNameInput = event.target.closest("[data-room-control-name-input]");
    const codeInput = event.target.closest("[data-room-control-default-code-input]");
    if (roomNameInput) renameRoomControlRoom(roomNameInput.dataset.roomKey, roomNameInput.value);
    if (controlNameInput) renameRoomControl(controlNameInput.dataset.roomKey, controlNameInput.dataset.roomControlId, controlNameInput.value);
    if (codeInput) {
      const clean = String(codeInput.value || "").replace(/\D/g, "").slice(0, ROOM_CONTROL_CODE_MAX_LENGTH);
      if (codeInput.value !== clean) codeInput.value = clean;
      setRoomControlDefaultCode(codeInput.dataset.roomKey, codeInput.dataset.roomControlId, clean);
    }
  });
  elements.roomControlConfigList?.addEventListener("change", (event) => {
    const countSelect = event.target.closest("[data-room-control-count-select]");
    const bedToggle = event.target.closest("[data-room-control-bed-slider-toggle]");
    if (countSelect) setRoomControlCount(countSelect.dataset.roomKey, countSelect.value);
    if (bedToggle) setRoomControlBedSlider(bedToggle.dataset.roomKey, bedToggle.dataset.roomControlId, bedToggle.checked);
  });
  elements.roomControlConfigList?.addEventListener("click", (event) => {
    const bedToggleWrap = event.target.closest("[data-room-control-bed-slider-toggle-wrap]");
    if (bedToggleWrap) {
      event.preventDefault();
      event.stopPropagation();
      const checkbox = bedToggleWrap.querySelector("[data-room-control-bed-slider-toggle]");
      const currentlyChecked = checkbox ? checkbox.checked : bedToggleWrap.getAttribute("aria-checked") === "true";
      setRoomControlBedSlider(bedToggleWrap.dataset.roomKey, bedToggleWrap.dataset.roomControlId, !currentlyChecked);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-room-control-room]");
    if (deleteButton) deleteRoomControlRoom(deleteButton.dataset.deleteRoomControlRoom);
  });
  elements.roomControlConfigList?.addEventListener("keydown", (event) => {
    const bedToggleWrap = event.target.closest("[data-room-control-bed-slider-toggle-wrap]");
    if (!bedToggleWrap || ![" ", "Enter"].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const checkbox = bedToggleWrap.querySelector("[data-room-control-bed-slider-toggle]");
    const currentlyChecked = checkbox ? checkbox.checked : bedToggleWrap.getAttribute("aria-checked") === "true";
    setRoomControlBedSlider(bedToggleWrap.dataset.roomKey, bedToggleWrap.dataset.roomControlId, !currentlyChecked);
  });

  document.querySelectorAll("[data-away-adjust]").forEach((button) => button.addEventListener("click", () => {
    const [kind, delta] = button.dataset.awayAdjust.split(":");
    setAwayTemp(kind, Number(delta));
  }));
  document.querySelectorAll("[data-safety-adjust]").forEach((button) => button.addEventListener("click", () => {
    const [bound, delta] = button.dataset.safetyAdjust.split(":");
    adjustSafetyRange(bound, Number(delta));
  }));
  document.querySelectorAll("[data-limit-adjust]").forEach((button) => button.addEventListener("click", () => {
    const [mode, bound, delta] = button.dataset.limitAdjust.split(":");
    adjustLimit(mode, bound, Number(delta));
  }));
  document.querySelectorAll("[data-auto-adjust]").forEach((button) => button.addEventListener("click", () => {
    const [kind, delta] = button.dataset.autoAdjust.split(":");
    adjustAutoSetting(kind, Number(delta));
  }));
  elements.addThermostatPersonButton?.addEventListener("click", () => openAudioEntityPicker("thermostatPerson"));
  elements.addPauseFunctionEntryButton?.addEventListener("click", () => openAudioEntityPicker("pauseFunction"));
  elements.pauseFunctionEntryList?.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-remove-pause-function-entry]");
    if (removeButton) removePauseFunctionEntry(removeButton.dataset.removePauseFunctionEntry);
  });
  document.querySelectorAll("[data-pause-duration-adjust]").forEach((button) => button.addEventListener("click", () => adjustPauseFunctionDuration(Number(button.dataset.pauseDurationAdjust || 0))));
  elements.heatLockToggle?.addEventListener("click", () => toggleThermostatEquipmentLock("heat"));
  elements.coolLockToggle?.addEventListener("click", () => toggleThermostatEquipmentLock("cool"));
  elements.chooseCurrentTempSensorButton?.addEventListener("click", () => openAudioEntityPicker("thermostatTemp"));
  elements.clearCurrentTempSensorButton?.addEventListener("click", clearCurrentTempSensor);
  elements.thermostatPeopleList?.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-remove-thermostat-person]");
    if (removeButton) removeThermostatPerson(removeButton.dataset.removeThermostatPerson);
  });
  document.querySelectorAll(".mode-button[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  document.querySelectorAll(".segment[data-fan]").forEach((button) => button.addEventListener("click", () => setFanMode(button.dataset.fan)));

  document.getElementById("prevTrack").addEventListener("click", () => changeTrack(-1));
  document.getElementById("nextTrack").addEventListener("click", () => changeTrack(1));
  elements.playPause.addEventListener("click", togglePlayback);
  document.querySelectorAll("[data-audio-preset]").forEach((button) => {
    button.addEventListener("click", () => applyAudioPreset(button.dataset.audioPreset));
  });
  bindLongPress(document.querySelector(".audio-side-card"), () => openAudioEntityPicker("media"));
  document.querySelectorAll("[data-audio-picker]").forEach((control) => {
    const kind = control.dataset.audioPicker;
    bindLongPress(control, (event) => { event.preventDefault(); event.stopPropagation(); openAudioEntityPicker(kind); });
  });
  ["subwoofer", "surround", "projector"].forEach((kind) => {
    elements[`${kind}Toggle`]?.addEventListener("click", () => {
      if (Date.now() - audioPickerOpenedAt < 900) return;
      sendAudioSwitchAction(kind);
    });
  });
  elements.audioEntityPickerClose?.addEventListener("click", closeAudioEntityPicker);
  document.querySelectorAll("[data-close-audio-entity-picker]").forEach((el) => el.addEventListener("click", closeAudioEntityPicker));
  elements.audioEntitySearch?.addEventListener("input", () => { state.audioEntityPicker.search = elements.audioEntitySearch.value; renderAudioEntityPicker(); });
  elements.audioEntityPickerList?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-audio-entity-id]");
    if (!row) return;
    selectAudioEntity(row.dataset.audioEntityId);
  });
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
    slider.addEventListener("pointerdown", () => markAudioSliderActive(name, 8000));
    slider.addEventListener("pointerup", () => { releaseAudioSlider(name); scheduleSliderCommand(0); });
    slider.addEventListener("pointercancel", () => releaseAudioSlider(name));
    slider.addEventListener("lostpointercapture", () => releaseAudioSlider(name));
    slider.addEventListener("blur", () => releaseAudioSlider(name));
    slider.addEventListener("input", (event) => {
      state.audio[name] = Number(event.target.value);
      lastAudioUserInteractionAt = Date.now();
      markAudioSliderActive(name, 8000);
      if (name === "volume") audioVolumeHoldUntil = Date.now() + AUDIO_VOLUME_SETTLE_MS;
      if (["gain", "bass", "treble"].includes(name)) audioToneHoldUntil[name] = Date.now() + AUDIO_TONE_SETTLE_MS;
      setRangeVisual(event.target);
      updateAudioValueLabel(name);
      // Do not send HA commands while dragging. This keeps the Pi/HA light and
      // separates received state updates from actual user commands.
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
  elements.lightRoomTabs?.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-light-room]");
    if (!tab) return;
    state.lights.room = tab.dataset.lightRoom;
    saveConfig();
    renderLights();
    pollHomeAssistantLights({ force: true });
  });
  elements.roomControlTabs?.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-room-control-room]");
    if (!tab) return;
    state.roomControl.room = tab.dataset.roomControlRoom;
    saveConfig();
    renderRoomControls();
    pollHomeAssistantRoomControls({ force: true });
  });
  document.querySelectorAll("[data-blind-action]").forEach((button) => button.addEventListener("click", () => applyBlindAction(button.dataset.blindAction)));
  document.querySelectorAll("[data-light-action]").forEach((button) => button.addEventListener("click", () => applyLightAction(button.dataset.lightAction)));
  document.querySelectorAll("[data-room-control-action]").forEach((button) => button.addEventListener("click", () => applyRoomControlAction(button.dataset.roomControlAction)));
  elements.blindCards.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-blind-id]");
    if (!button) return;
    applyBlindAction(button.dataset.action, button.dataset.blindId);
  });
  elements.lightCards?.addEventListener("click", (event) => {
    const colorButton = event.target.closest("[data-light-color-open]");
    if (colorButton) {
      event.preventDefault();
      openLightColorPicker(colorButton.dataset.lightId);
      return;
    }
    const iconButton = event.target.closest("[data-light-icon-toggle]");
    if (!iconButton) return;
    event.preventDefault();
    if (Date.now() < suppressLightIconClickUntil) return;
    toggleLight(iconButton.dataset.lightId);
  });
  elements.lightCards?.addEventListener("input", (event) => {
    const slider = event.target.closest("[data-light-slider]");
    if (!slider) return;
    setLightBrightness(slider.dataset.lightId, slider.value);
  });
  elements.lightCards?.addEventListener("change", (event) => {
    const slider = event.target.closest("[data-light-slider]");
    if (!slider) return;
    setLightBrightness(slider.dataset.lightId, slider.value, { send: true });
  });

  elements.roomControlCards?.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("[data-room-control-bed-slider]")) return;
    suppressRoomControlClickUntil = Date.now() + 1200;
    event.stopImmediatePropagation();
  });
  elements.roomControlCards?.addEventListener("input", (event) => {
    const slider = event.target.closest("[data-room-control-bed-slider]");
    if (!slider) return;
    event.stopPropagation();
    handleRoomControlBedSliderInput(slider, { send: false });
  });
  elements.roomControlCards?.addEventListener("change", (event) => {
    const slider = event.target.closest("[data-room-control-bed-slider]");
    if (!slider) return;
    event.stopPropagation();
    handleRoomControlBedSliderInput(slider, { send: true });
  });
  elements.roomControlCards?.addEventListener("click", (event) => {
    if (event.target.closest("[data-room-control-bed-slider]")) return;
    const card = event.target.closest("[data-room-control-card]");
    if (!card) return;
    event.preventDefault();
    if (Date.now() < suppressRoomControlClickUntil) return;
    const control = findRoomControlInActiveRoom(card.dataset.roomControlCard);
    if (roomControlIsBedSlider(control)) {
      if (!control?.haEntityId) showToast("Hold the card to assign a cover entity");
      return;
    }
    toggleRoomControl(card.dataset.roomControlCard);
  });
  bindLongPress(elements.roomControlCards, (event) => {
    if (event.target.closest("[data-room-control-bed-slider]")) return;
    const card = event.target.closest("[data-room-control-card]");
    if (!card) return;
    event.preventDefault();
    event.stopPropagation();
    suppressRoomControlClickUntil = Date.now() + 700;
    openRoomControlEntityPicker(card.dataset.roomKey, card.dataset.roomControlCard);
  }, { delay: 900, allowInteractive: true });

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
  bindLightInteractions();
  window.addEventListener("keydown", (event) => {
    const settingsCodeOpen = elements.settingsCodeOverlay?.classList.contains("open");
    if (settingsCodeOpen) {
      if (/^\d$/.test(event.key)) { event.preventDefault(); handleSettingsCodeKey(event.key); return; }
      if (event.key === "Backspace") { event.preventDefault(); handleSettingsCodeKey("back"); return; }
      if (event.key === "Enter") { event.preventDefault(); verifySettingsCode(); return; }
      if (event.key === "Escape") { event.preventDefault(); closeSettingsCodePrompt(); return; }
    }
    const roomCodeOpen = elements.roomControlCodeOverlay?.classList.contains("open");
    if (roomCodeOpen) {
      if (/^\d$/.test(event.key)) { event.preventDefault(); handleRoomControlCodeKey(event.key); return; }
      if (event.key === "Backspace") { event.preventDefault(); handleRoomControlCodeKey("back"); return; }
      if (event.key === "Enter") { event.preventDefault(); submitRoomControlCode(); return; }
      if (event.key === "Escape") { event.preventDefault(); closeRoomControlCodePrompt(); return; }
    }
    const alarmKeypadOpen = elements.alarmKeypadOverlay?.classList.contains("open");
    if (alarmKeypadOpen) {
      if (/^\d$/.test(event.key)) { event.preventDefault(); handleAlarmKey(event.key); return; }
      if (event.key === "Backspace") { event.preventDefault(); handleAlarmKey("back"); return; }
      if (event.key === "Enter") { event.preventDefault(); sendAlarmDisarm(); return; }
    }
    if (event.key === "ArrowRight") goRelative(1);
    if (event.key === "ArrowLeft") goRelative(-1);
    if (event.key === "+" || event.key === "=") adjustSetpoint(1);
    if (event.key === "-" || event.key === "_") adjustSetpoint(-1);
    if (event.key === "Escape") {
      if (elements.scheduleTimePickerOverlay?.classList.contains("open")) {
        closeScheduleTimePicker();
        return;
      }
      closeSettingsCodePrompt(); closeRoomControlCodePrompt(); closeSettings(); closeEntityPicker(); closeAudioEntityPicker(); closeAlarmKeypad(); closeAlarmArmOptions(); closeLightColorPicker(); closeThermostatInfo(); closeAutoConfirmOverlay(); closeScheduleOverlay();
    }
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

function dismissBootOverlay(delay = 1800) {
  window.setTimeout(() => {
    document.body.classList.remove("booting");
    document.body.classList.add("booted");
    if (elements.bootOverlay) elements.bootOverlay.setAttribute("aria-hidden", "true");
  }, delay);
}

async function init() {
  await loadSavedConfig();
  applyPanelTheme(state.theme, { save: false });
  dismissBootOverlay(normalizePanelTheme(state.theme) === "star-trek" ? 2600 : 1200);
  renderScreenTimeoutSettings();
  syncAlarmFromConfig();
  syncDoorFromConfig();
  bindEvents();
  updateClock();
  renderThermostat();
  renderCurrentTempSourceSettings();
  renderThermostatPeople();
  renderPauseFunctionStatus();
  renderPanelLock();
  fetchLocalThermostatStatus({ force: true });
  pollHomeAssistantWeather({ force: true });
  renderDoorWidget();
  renderAlarmWidget();
  renderAudio();
  renderBlinds();
  renderLights();
  renderRoomControls();
  gotoPage("thermostat");
  setInterval(updateClock, 1000);
  setInterval(() => {
    if (state.currentPage === "thermostat") renderThermostat();
  }, 5000);
  setInterval(() => checkThermostatSchedules(), SCHEDULE_CHECK_INTERVAL_MS);
  checkThermostatSchedules();
  setInterval(() => fetchLocalThermostatStatus(), LOCAL_THERMOSTAT_SYNC_INTERVAL_MS);
  // The virtual temperature slider is now the temporary sensor input.
  // setInterval(mockSensorDrift, 4500);
  setInterval(mockTrackProgress, 1200);
  setInterval(() => maybeApplyScreenTimeout(), SCREEN_TIMEOUT_CHECK_INTERVAL_MS);
  setInterval(() => fetchHardwareStatus(), HARDWARE_STATUS_INTERVAL_MS);
  setInterval(() => fetchHistoryStatus(), HISTORY_STATUS_INTERVAL_MS);
  setInterval(() => pollHomeAssistantLinkedCovers(), HA_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantMediaPlayer(), HA_AUDIO_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantLights(), HA_LIGHT_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantRoomControls(), HA_ROOM_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantAlarm(), HA_ALARM_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantDoor(), HA_DOOR_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantThermostatPeople(), HA_PRESENCE_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantPauseFunction(), HA_PAUSE_FUNCTION_SYNC_INTERVAL_MS);
  setInterval(servicePauseFunctionCountdown, 1000);
  setInterval(() => pollHomeAssistantWeather(), HA_WEATHER_SYNC_INTERVAL_MS);
  setInterval(() => pollHomeAssistantCurrentTempSensor(), HA_TEMP_SENSOR_SYNC_INTERVAL_MS);
  pollHomeAssistantCurrentTempSensor({ force: true });
  pollHomeAssistantMediaPlayer({ force: true, controls: true }).then(() => maybeApplyStartupDefaultScreen()).catch(() => maybeApplyStartupDefaultScreen());
  pollHomeAssistantAlarm({ force: true });
  pollHomeAssistantDoor({ force: true });
  pollHomeAssistantThermostatPeople({ force: true });
  pollHomeAssistantPauseFunction({ force: true });
}

init().catch((error) => {
  console.error("Smart thermostat failed to start", error);
  showToast("Startup error");
});
