# Supporting

Place supporting documents, notes, screenshots, reference files, and other repo-only materials here.

The tablet self-update path intentionally excludes this top-level folder from the deployed wall-panel checkout, so files stored here can live in GitHub without being copied onto the thermostat unit.

## Home Assistant IHA integration update

`iha.zip` contains the files that must be merged into Home Assistant's existing `/config/custom_components/iha/` directory. Version 10.9.0 adds the single Home Assistant-owned JARVIS knowledge store used by every thermostat screen.

After copying the files, restart Home Assistant. Do not delete the other existing IHA integration files that are not present in this update archive.
