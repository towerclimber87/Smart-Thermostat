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

## Typed JARVIS-style Home Assistant console

Version 11.60 adds an original full-screen AI-core animation and a typed command loop. The Raspberry Pi does not run a language model or speech recognizer. It only displays the animation and forwards text to the Home Assistant conversation API, keeping the panel workload small.

The assistant reuses the Home Assistant URL and long-lived token already stored in the thermostat configuration. Replies can be sent to the Sonos/media player already selected on the Audio page, or to a separate media player chosen in the temporary Backup Config web portal. The portal also allows an explicit `conversation.*` agent and `tts.*` entity to be entered. Leaving the conversation agent blank uses Home Assistant's default agent; leaving TTS blank now prefers an available OpenAI TTS entity before Home Assistant Cloud or Piper. For safety, the stored-token assistant endpoint accepts commands only from the thermostat itself unless the temporary Backup Config portal is open.

### Original cinematic voice over Sonos

Version 12.20 adds a direct OpenAI speech mode to the **JARVIS Voice** tab. It does not install ElevenLabs, Piper, HACS, or another voice provider. The thermostat uses the same OpenAI API account you already use with Home Assistant, generates a temporary MP3, and asks Home Assistant to play it as a Sonos announcement. The previous Sonos program and volume are restored by the announcement path when the player supports it.

One-time setup:

1. Press **Backup Config** on the thermostat and open the temporary configuration address.
2. Open **JARVIS Voice**.
3. Select **OpenAI cinematic voice — recommended**.
4. Paste an OpenAI API key from the same OpenAI account, keep `gpt-4o-mini-tts`, select a voice, and save.
5. Run the typed test. The default is **Onyx**, speed **1.08**, with a prefilled original refined British-style household-assistant prompt.

The API key is stored with the panel configuration but is never returned by the JARVIS configuration/status endpoints. Generated MP3 files use random names, are served only long enough for the LAN speaker to retrieve them, and are removed after approximately ten minutes. The Sonos/media player must be able to reach the thermostat's LAN address on port `8080`.

The existing **Home Assistant TTS entity** mode remains available. In that mode, voice instructions and speed stay configured in the Home Assistant OpenAI TTS subentry, while the thermostat can pass a supported voice choice to the selected TTS entity.

Open the continuous typed loop on the Pi:

```bash
cd ~/Smart-Thermostat-Development
python3 scripts/jarvis-console.py
```

Useful console commands:

- `/new` starts a fresh Home Assistant conversation with the next command.
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
