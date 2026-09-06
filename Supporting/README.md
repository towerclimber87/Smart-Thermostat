## Current bundled IHA integration (10.18.0)

The included `Supporting/iha.zip` currently contains IHA integration version **10.18.0**. Install/merge this package into Home Assistant's existing `/config/custom_components/iha/` directory and restart Home Assistant after updating.


## Home Assistant Screen Lock (10.18.0)

Version **10.18.0** adds a real Home Assistant **Screen Lock** lock entity for each thermostat panel. Lock/unlock controls the normal tap/guest lock shown at the top-left of the thermostat screen. The long-press Security Lock remains local-only and cannot be remotely unlocked or downgraded from Home Assistant.

## Home Assistant Backup Portal switch (10.17.8)

Version **10.17.8** keeps the **Backup Portal** switch as the single Home Assistant control for the temporary Backup / Settings web portal and removes the redundant **Open Backup** button, including cleaning up the retired button entity from existing installs. Turn the switch on to open the portal and turn it off to close the portal immediately. The switch follows the actual panel state, so it turns off in Home Assistant when the portal times out or is closed from the thermostat screen. **Backup Portal**, **Check for updates**, **Force software update**, and the **Software** update entity are categorized as **Diagnostic** entities so these maintenance controls stay out of the thermostat's normal control section.

## Home Assistant Open Backup button (10.17.6)

Version **10.17.6** adds an **Open Backup** button entity to the thermostat device in Home Assistant. Pressing it enables the thermostat's temporary Backup / Settings web portal using the same backend activation route as the panel's **Backup Config** action. The entity exposes the returned `backup_url`, portal `active` state, and `timeout_seconds` as attributes.

The temporary browser portal has also been expanded to mirror the current wall-panel Settings organization more closely, using two-column section cards that open and collapse like the panel. It includes Device Internet, Alexa Lockout, Auto Away / Home, HVAC range and safety settings, onboard temperature sensors, Thermostat Sync and one-time House Sync, Doors / Comfort Pause, differential/runtime settings, Intimacy Hold, person tracking, outside temperature, unit/security settings, Screen Settings, Audio Settings, and JARVIS controls.


## JARVIS playback completion synchronization (10.17.2)

Version **10.17.2** prevents Sonos announcement volume and the panel's JARVIS overlay from resetting during speech. Some Sonos players briefly return to their baseline media state before the announcement audio has finished. The integration now treats its conservative text-derived speech duration as a minimum hold: observable playback may keep the announcement active longer, but an early state transition cannot shorten it. The previous Sonos volume is restored and the panel receives the finished callback only after that completion window.

The existing 400 millisecond volume-settling pause remains in place before `tts.speak`, so the first words begin at the panel's configured announcement volume.

## Home Assistant-owned speech (10.14.0)

Version **10.14.0** moves JARVIS audio generation and Sonos playback out of the thermostat backend. `iha.ask_jarvis` and `iha.jarvis_announcement` now ask the panel to process/display the text with `externalSpeech`, then Home Assistant calls its native `tts.speak` service directly. The panel still shows the JARVIS icon, command, response text, speaking/complete state, and saved screen hold time.

The panel's **Announcement Volume** remains authoritative. Home Assistant temporarily applies that level to the selected media player, calls the configured TTS entity with `cache: false`, monitors the playback cycle, restores the previous media-player volume, and tells the panel when playback finishes. For Home Assistant Cloud, the configured voice is passed in `options.voice` (for example, `JennyNeural`).

This design is also ready for an external wireless microphone array: the microphone/Assist pipeline stays in Home Assistant, while the IHA panel remains a visual status and volume-control surface.

# Supporting

Place supporting documents, notes, screenshots, reference files, and other repo-only materials here.

The tablet self-update path intentionally excludes this top-level folder from the deployed wall-panel checkout, so files stored here can live in GitHub without being copied onto the thermostat unit.

## Home Assistant IHA integration update

`iha.zip` contains the changed files that must be merged into Home Assistant's existing `/config/custom_components/iha/` directory. Do not delete the other existing IHA files that are not included in the archive.

Version **10.13.0** adds a persistent Home Assistant JARVIS volume sensor for every IHA panel and keeps one Home Assistant-owned JARVIS record for every updated IHA panel:

```text
.storage/iha.jarvis_knowledge
```

That record now contains both:

- exact shared home-temperature source mappings; and
- the shared JARVIS routing, local/Piper speech policy, and personality profile.


The new per-panel sensor uses the panel name, for example:

```text
sensor.office_thermostat_jarvis_volume
```

Its state is a media-player-ready volume level from `0.01` through `1.00`. The attributes also include `volume_percent`, `source_available`, and `using_cached_value`. Home Assistant stores the last valid value in `.storage/iha.jarvis_volume`; if the panel is offline, the sensor remains usable with that cached value. Before the first successful panel poll, it safely defaults to `0.45`.

The new services are:

```text
iha.get_jarvis_profile
iha.set_jarvis_profile
```

After copying the files, restart Home Assistant. Then install/configure Piper through the Wyoming integration when local speech is desired and save `tts.piper` under **Backup Config → JARVIS Voice → Shared Routing & Personality**.

Version 10.13.0 also changes `iha.ask_jarvis` so **Start new conversation** defaults to off. Home Assistant automations now continue the thermostat's saved cloud conversation unless they explicitly request a fresh one.

Version 10.13.0 also registers `iha.jarvis_announcement`. This service sends an already-written automation message to the thermostat's speech pipeline without asking Home Assistant Assist or OpenAI to interpret or rewrite it.
