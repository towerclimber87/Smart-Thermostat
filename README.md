# Smart Thermostat Native

This package removes Chromium from the touchscreen UI and replaces it with a native PyQt5 appliance client. The local Python backend is still used for thermostat safety logic, relay control, Home Assistant proxy calls, config import/export, hardware info, history, reboot, and update handling.

The package intentionally does not include the old `public/` web UI or the old Tkinter native file.

## Important security note

`data/panel-config.json` may contain your Home Assistant URL, long-lived access token, and an OpenAI API key when the direct cinematic voice is enabled. Do not push this repository to a public GitHub repo with real panel data inside `data/`.

## Install or update on the Pi

From the repo folder:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh scripts/jarvis-console.py
sudo ./scripts/install-native.sh
```

The installer:

- installs the native Qt/X dependencies,
- writes `/etc/X11/Xwrapper.config`,
- disables old Chromium/web kiosk services if present,
- disables `getty@tty1.service` so the native UI owns the touchscreen terminal,
- installs `smart-thermostat-backend.service`,
- installs `smart-thermostat-native.service`,
- starts the backend and native UI.

The native UI service launches X directly with `xinit` on `tty1`. This avoids the earlier issue where `startx` stayed running but the screen remained on the terminal.

## Services

- `smart-thermostat-backend.service` runs `server.py` on `0.0.0.0:8080` so Home Assistant can reach it from the LAN.
- `smart-thermostat-native.service` launches the Qt full-screen UI through X on `tty1`.
- `avahi-daemon.service` advertises `_iha-thermostat._tcp.local` for Home Assistant discovery.
- Discovery IDs now prefer the physical Raspberry Pi serial or a non-loopback MAC address before `/etc/machine-id`, so cloned SD cards do not show up as the same Home Assistant device.

## Home Assistant discovery/control

The native app still uses the local backend API, but the backend now listens on the LAN at port `8080`. The installer also writes `/etc/avahi/services/iha-thermostat.service`, so Home Assistant can auto-discover the panel as `_iha-thermostat._tcp.local`.

Expected checks on the Pi:

```bash
curl http://127.0.0.1:8080/api/discovery
systemctl status avahi-daemon.service smart-thermostat-backend.service --no-pager -l
cat /etc/avahi/services/iha-thermostat.service
```

If auto-discovery does not appear in Home Assistant, add the custom `iha` integration manually and enter the thermostat IP address with port `8080`. The integration accepts either `192.168.x.x` plus port `8080`, or a full URL such as `http://192.168.x.x:8080`.

## Useful checks

```bash
systemctl status smart-thermostat-backend.service smart-thermostat-native.service --no-pager -l
pgrep -a -f 'Xorg|xinit|native_app/main.py'
tail -200 /dev/shm/smart-thermostat-native/logs/native-ui.log
tail -200 /dev/shm/smart-thermostat-native/logs/native-ui.previous.log
tail -200 /dev/shm/smart-thermostat-native/logs/native-crash.log
journalctl -u smart-thermostat-native.service -b -n 120 --no-pager
```

## Manual test without installing services

Backend:

```bash
cd ~/Smart-Thermostat-Development
python3 server.py --host 0.0.0.0 --port 8080
```

Native UI from an existing X session:

```bash
cd ~/Smart-Thermostat-Development
python3 native_app/main.py
```

## Raspberry Pi thermal emergency protection

Version 14.00 adds a latched CPU-overheat mode for wall-cavity installations:

- protection enters immediately when the Raspberry Pi CPU reaches **70°C**;
- all normal thermostat comfort calls, schedules, presence handling, door-pause work, manual relay tests, JARVIS processing, page polling, animations, and ordinary touchscreen interaction are suspended;
- the autonomous backend and selected room-temperature source stay active so the configured **Safety Low** and **Safety High** heat/cool protections can still operate;
- the display wakes if necessary and shows a black screen containing only **UNIT OVERHEATING** in red;
- normal operation returns only after the CPU remains at or below **65°C for two minutes**, preventing rapid cycling near the threshold.

The defaults can be overridden through `SMART_THERMOSTAT_THERMAL_TRIGGER_C`, `SMART_THERMOSTAT_THERMAL_CLEAR_C`, and `SMART_THERMOSTAT_THERMAL_CLEAR_HOLD_SECONDS`.

## Typed JARVIS-style Home Assistant console

The native panel displays the AI-core animation and sends typed commands to Home Assistant. The Raspberry Pi does not run an LLM. Home Assistant handles local intents and forwards only broader requests to the configured conversation agent.

The assistant reuses the Home Assistant URL and long-lived token already stored in the thermostat configuration. The stored-token assistant endpoint accepts commands only from the thermostat itself unless the temporary Backup Config portal is open.

### JARVIS Sonos completion synchronization

Version 14.71 includes IHA 10.17.2. Sonos may briefly return to its baseline media state while an announcement is still audible. JARVIS now keeps the configured announcement volume and on-screen speaking state active for at least a conservative duration derived from the spoken text, while real media-player playback can extend that window. Only then does Home Assistant restore the prior volume and tell the panel to clear the JARVIS overlay. The existing 400 millisecond pre-speech volume settling pause is retained.

### Home Assistant-owned speech playback

Version 13.90 removes audio generation and Sonos playback from the thermostat backend. The matching IHA 10.14.0 integration calls Home Assistant's native `tts.speak` action using the TTS entity, voice, language, media player, and announcement volume saved on the panel. The thermostat continues to show the JARVIS icon, command, response text, speaking status, and configured screen hold time. Home Assistant restores the prior media-player volume after playback and reports completion back to the panel. This is the intended path for automations and for a future Home Assistant wireless microphone array.

### Persistent JARVIS automation volume

Version 13.50 exposes each panel's saved JARVIS voice volume through the normal thermostat status poll. The matching IHA 10.14.0 integration publishes a media-player-ready sensor such as `sensor.office_thermostat_jarvis_volume`, whose state is `0.45` for 45 percent. Home Assistant retains the last valid value in `.storage/iha.jarvis_volume`, so existing automations can keep using it while the wall panel is offline or rebooting. The sensor attributes report the percentage and whether the current value is live or cached.

### Local automation execution and controlled OpenAI fallback

Version 13.10 adds the missing Home Assistant automation announcement service:

- `iha.jarvis_announcement` is now registered by the IHA custom integration.
- Automation messages bypass Home Assistant Assist and OpenAI, so the supplied text is spoken exactly as written.
- Announcements use Home Assistant native TTS and Sonos playback while retaining the thermostat's screen animation, saved announcement volume, and optional media-player override.
- Running an announcement does not replace or reset the saved JARVIS conversation.

Version 13.00 fixes JARVIS requests started from Home Assistant automations and adds a reliable local-first path for running household automations:

- explicit requests such as “Run the Welcome Home automation,” “Trigger the bedtime routine,” and “Activate Movie Night scene” first match exact `automation.*`, `script.*`, and `scene.*` entities and call the corresponding Home Assistant service directly;
- if local matching and built-in Assist cannot resolve an explicitly named automation, script, or scene, hybrid mode may send that same request to the configured OpenAI conversation agent;
- unrelated device-control commands remain local-only so OpenAI cannot guess a different target;
- when no OpenAI conversation agent is configured, JARVIS now reports that configuration problem instead of silently sending the request back to Home Assistant's default local agent;
- `iha.ask_jarvis` now continues the saved cloud conversation by default. Set **Start new conversation** only when an automation intentionally needs a clean session.

This preserves the intended order: deterministic local execution first, Home Assistant Assist second, and OpenAI only for an unresolved eligible request.

### Misspelling-resistant weather fallback

Version 12.80 ensures that a weather question always reaches the OpenAI conversation agent when Home Assistant cannot supply a usable local forecast. The router recognizes common speech-to-text variants such as `forecast`, `forcast`, `forcat`, and `forcest`. Because the exact Home Assistant weather handler runs first, a valid local forecast remains free and fast; only an unresolved weather request uses OpenAI and its enabled web-search tool.

A request such as:

```text
What is the weather forcat for tomorrow?
```

now follows this order:

1. try the configured Home Assistant `weather.*` entity;
2. if no usable forecast is returned, send the original request to `conversation.openai_conversation`;
3. speak the final answer through local Piper.

The same cloud fallback is also enforced when Home Assistant returns `no_valid_targets` for a read-only weather request. Device-control commands remain local-only and never use this fallback.

### Direct-title spoken responses

Version 12.70 removes the extra spoken ceremony before answers. When the shared title is enabled, replies begin directly with the configured form of address:

```text
Sir, tomorrow's forecast is partly cloudy...
Sir, the living room light is off.
```

The panel strips leading phrases such as `Done`, `Certainly`, `Of course`, `Right away`, and `As requested`, including cases where Home Assistant or the cloud agent already placed `sir` after the phrase. Errors are also normalized to begin with the title instead of an apology. The answer's factual content is otherwise preserved.

### Cost-aware hybrid routing

Version 12.60 makes hybrid mode conversational without turning every home request into a paid AI call:

- current weather and forecasts are read directly from the configured Home Assistant `weather.*` entity through `weather.get_forecasts`;
- common multi-entity questions such as “Is the garage open?” and “What lights are on?” are summarized locally from exact Home Assistant states;
- a read-only local question can fall through to the OpenAI agent when built-in Assist returns an unresolved or ambiguous-target error;
- device-control commands never fall through to OpenAI, so a vague command cannot be reinterpreted against a different device;
- the test result includes `fallbackReason` when a read-only question uses the cloud fallback.

If the Home Assistant weather entity cannot supply a forecast, hybrid routing sends the question to the OpenAI conversation agent. To permit a real internet lookup, enable **Web search** and **Include home location** in the OpenAI Conversation subentry. Web search is a paid OpenAI tool, so use a small search-context setting when cost matters.

Version 12.50 fixes local Piper playback from JARVIS test automations and thermostat requests. Local TTS now uses the language and voice already configured on the Home Assistant TTS entity instead of forcing the panel's generic language value. It first requests a Sonos-compatible announcement URL with provider defaults and then falls back to the same minimal `tts.speak` payload verified in Home Assistant Developer Tools.

Version 12.40 adds one shared routing and personality profile to the same Home Assistant-owned record used by JARVIS knowledge:

```text
.storage/iha.jarvis_knowledge
```

Every updated IHA panel reads the same profile. The default behavior is:

- **Conversation routing: Hybrid**
  - saved multi-room temperature questions are answered directly from the shared IHA knowledge store;
  - obvious device, area, climate, media, timer, scene, script, and status requests go directly to `conversation.home_assistant`;
  - explanations, writing, summaries, comparisons, general knowledge, and other broad requests go directly to the configured OpenAI conversation agent;
  - common read-only home summaries and weather forecasts are answered locally from exact Home Assistant data;
  - unresolved read-only questions may fall through to OpenAI for a more natural summary;
  - device-control commands and ambiguous actions stay local errors so the cloud agent cannot guess a different target.
- **Speech cost policy: Piper/local for every answer**
  - Piper speaks both local and OpenAI-generated answers;
  - OpenAI TTS is not called during normal operation;
  - if the policy is `local_only` and Piper is missing, the thermostat reports the missing local TTS configuration rather than silently creating a paid OpenAI speech request.
- **Personality**
  - the default form of address is `sir`;
  - humor defaults to restrained;
  - the old goofy spoken prefixes and screen jokes are removed;
  - spoken replies begin directly with `Sir, ...` without an extra acknowledgement.

The router decides the destination before making the conversation request. A clear home-control command therefore does not wait for an OpenAI probe. A two-step request occurs only for a read-only question that local Assist cannot resolve; the cloud agent then receives the question and can summarize the relevant exposed entities.

Recommended shared settings under **Backup Config → JARVIS Voice → Shared Routing & Personality**:

```text
Conversation routing: Hybrid
Local Home Assistant agent: conversation.home_assistant
OpenAI / broad-question agent: conversation.<your OpenAI agent>
Speech cost policy: Piper/local speech for every answer
Local TTS entity: tts.piper
Form of address: sir
Humor level: Restrained
Begin spoken replies with “sir”: enabled
```

The **Run Test** result now shows the selected conversation route and speech path. Useful checks:

```text
Turn on the living room light.
Is the garage open?
What is tomorrow's weather forecast?
Tell me the outside temperature and then all inside temperatures.
Explain how a heat pump works.
```

The light command should show `home_assistant_local`. Garage and light-status summaries should show `home_assistant_status_summary`. A Home Assistant forecast should show `home_assistant_weather`; if no usable local forecast exists, it should show a cloud route. The saved temperature request should show `iha_shared_temperature`, and the general explanation should show `cloud_conversation`. With the lowest-cost speech policy, every answer should still show the local Piper speech path.

### Install Piper for free local speech

Piper runs inside Home Assistant and does not add a per-request OpenAI charge.

1. In Home Assistant, install the **Piper** app and start it.
2. Open **Settings → Devices & services**.
3. Add the discovered Piper service through the **Wyoming** integration.
4. Choose the desired English language/voice variant in the Piper configuration.
5. Return to the thermostat's **JARVIS Voice** page.
6. Set **Speech cost policy** to **Piper/local speech for every answer**.
7. Enter the discovered Piper TTS entity, normally `tts.piper`, or leave it blank for Piper-first auto-detection.
8. Save **Shared Behavior** and run a test.

Piper's available voice names and quality depend on the voice model installed in Home Assistant. Select a calm British English male voice where one is available. This creates the intended refined household-assistant style without attempting to duplicate an actor's exact voice.

### Refined cloud personality

The thermostat places the shared form of address at the beginning of both local and cloud spoken responses without adding a preliminary acknowledgement. For broad OpenAI answers to stay in character as well, configure the OpenAI conversation agent's prompt in Home Assistant with wording similar to:

```text
You are JARVIS, an original refined household computer assistant. Begin spoken answers directly with “Sir,” and then the useful answer. Do not begin with acknowledgements such as “Of course,” “Certainly,” “Done,” or “Right away.” Be concise, composed, precise, and helpful. Use a polished British manner of speaking, but do not imitate any actor or copyrighted character. Use dry humor only rarely and never become goofy, theatrical, overly chatty, or excessively enthusiastic. For home facts and device state, do not invent values; use only information supplied by Home Assistant and say clearly when information is unavailable. When a read-only request matches several exposed entities, inspect all relevant matches and summarize each state instead of asking the user to choose one. For current external information such as weather, use web search when it is enabled and Home Assistant does not provide the answer.
```

This prompt controls the content and manner of the OpenAI answer. Piper controls how the final text sounds when spoken.

### Optional premium speech fallback

The existing direct OpenAI speech path remains available under **Premium Speech Fallback**. It can use a selected OpenAI voice, delivery instructions, model, and speed. The API key is stored with the panel configuration but is never returned by the JARVIS configuration or status endpoints. Generated MP3 files use random names and are removed after approximately ten minutes.

Set **Speech cost policy** to one of the following only when premium speech is desired:

- **Hybrid speech**: Piper/local for Home Assistant routes and premium speech for broad OpenAI routes.
- **Premium only**: premium speech for every answer.

The Home Assistant premium TTS option is also preserved for an existing `tts.*` provider. Sonos playback uses the announcement path where supported so the previous program and volume can resume afterward.

### Console and endpoint tests

Open the continuous typed loop on the Pi:

```bash
cd ~/Smart-Thermostat-Development
python3 scripts/jarvis-console.py
```

Useful console commands:

- `/new` starts a fresh cloud conversation with the next cloud-routed command.
- `/status` shows the current assistant stage and selected output entities.
- `/quit` closes the loop.

Run one command without opening the loop:

```bash
python3 scripts/jarvis-console.py "What is the living room temperature?"
```

Test the local endpoint directly:

```bash
curl -sS -X POST \
  -H 'Content-Type: application/json' \
  --data '{"text":"Turn on the living room light"}' \
  http://127.0.0.1:8080/api/assistant/process | python3 -m json.tool
```

To configure it from another computer, press **Backup Config** on the thermostat, open the temporary web address shown on the panel, and use the **JARVIS Voice** tab. This page does not display or transmit the saved Home Assistant token to the browser.

### Shared JARVIS home knowledge

Version 12.30 moved learned temperature-source mappings into the IHA Home Assistant integration instead of saving a separate copy on each wall panel. Version 12.40 adds the shared routing and personality profile to that same record. Every IHA thermostat reads the same live data before answering multi-room temperature questions.

Install the matching `Supporting/iha.zip` files into Home Assistant's `/config/custom_components/iha/` directory and restart Home Assistant before testing. The matching integration version is **10.18.0**.

JARVIS can be taught by speech or by **Backup Config → JARVIS Voice → Shared Home Knowledge**:

```text
JARVIS, remember that outside temperature comes from sensor.back_porch_temperature.
JARVIS, use climate.bedroom_thermostat for the bedroom temperature.
JARVIS, forget the office temperature.
JARVIS, what temperature sources do you remember?
```

When a friendly name matches more than one temperature entity, JARVIS does not guess. It lists the possible entities and asks for the exact entity ID. Climate entities automatically use `current_temperature`, weather entities use `temperature`, and normal temperature sensors use their state unless an attribute override is explicitly saved.

Requests such as these are answered from exact stored mappings rather than asking a conversation model to discover entities:

```text
JARVIS, tell me the outside temperature and then all of the inside temperatures.
JARVIS, what are the bedroom, living room, and office temperatures?
```

The IHA integration converts mapped temperature values to Home Assistant's configured temperature unit, reports unavailable sources honestly, and never substitutes a different entity when a requested mapping is missing.
