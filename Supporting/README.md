# Supporting

Place supporting documents, notes, screenshots, reference files, and other repo-only materials here.

The tablet self-update path intentionally excludes this top-level folder from the deployed wall-panel checkout, so files stored here can live in GitHub without being copied onto the thermostat unit.

## Home Assistant IHA integration update

`iha.zip` contains the changed files that must be merged into Home Assistant's existing `/config/custom_components/iha/` directory. Do not delete the other existing IHA files that are not included in the archive.

Version **10.10.0** keeps one Home Assistant-owned JARVIS record for every updated IHA panel:

```text
.storage/iha.jarvis_knowledge
```

That record now contains both:

- exact shared home-temperature source mappings; and
- the shared JARVIS routing, local/Piper speech policy, and personality profile.

The new services are:

```text
iha.get_jarvis_profile
iha.set_jarvis_profile
```

After copying the files, restart Home Assistant. Then install/configure Piper through the Wyoming integration when local speech is desired and save `tts.piper` under **Backup Config → JARVIS Voice → Shared Routing & Personality**.
