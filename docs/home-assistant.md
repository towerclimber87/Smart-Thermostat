# Home Assistant auto-discovery

This project exposes the wall panel as a local Home Assistant `climate` entity without MQTT.

## What gets discovered

The Raspberry Pi server advertises this mDNS/Zeroconf service:

```text
_iha-thermostat._tcp.local.
```

Home Assistant uses the included custom integration at:

```text
custom_components/iha
```

Once accepted in Home Assistant, it creates one thermostat climate entity. The device name comes from the thermostat name entered on the wall panel settings screen.

The climate entity includes:

- Current temperature
- Target temperature
- HVAC modes: Off, Heat, Cool, Heat/Cool Auto
- HVAC action: Idle, Heating, Cooling, Fan, Off
- Fan modes: Auto, On, Off
- Presets: Home, Away
- Humidity
- Extra attributes for outdoor temperature and relay states

## Raspberry Pi install/update

Run the install script from the project folder:

```bash
./scripts/install-pi.sh
```

The service runs `server.py`. That is required for:

- `/api/thermostat/status`
- `/api/thermostat/control`
- `/api/discovery`
- mDNS discovery

## Home Assistant install

Copy the integration folder into Home Assistant:

```text
/config/custom_components/iha
```

Restart Home Assistant.

Then go to:

```text
Settings > Devices & services
```

The thermostat should appear as a discovered IHA thermostat. Accept it and Home Assistant will create the climate entity.

## Manual add fallback

If discovery is blocked by VLANs, multicast, or mDNS settings:

1. Go to Settings > Devices & services.
2. Add Integration.
3. Search for `IHA`.
4. Enter the Pi host/IP and port `8080`.

## Important rename note

If you already copied the older `fns_smart_thermostat` folder into Home Assistant, remove it from `/config/custom_components/` before installing this renamed `iha` folder. Then restart Home Assistant.

## Thermostat name

The thermostat name is set inside the wall panel settings screen. That name is saved on the Raspberry Pi, returned by `/api/discovery`, advertised through Zeroconf, and sent to Home Assistant as the climate/device name.

If Home Assistant already had the device configured before a rename, reload the integration or restart Home Assistant. The integration also updates the config entry title when the coordinator receives the new name.

## Live updates

The wall panel now polls the local thermostat API every second, cache-busts those requests, and retries after temporary failures. Changes made from Home Assistant should appear on the panel without refreshing the browser.

## Current compatibility note

Version `0.2.4` keeps `ATTR_TEMPERATURE` imported from `homeassistant.const`, polls the thermostat faster, and syncs the thermostat name through discovery, device info, and the Home Assistant config entry title.

## Network note

Auto-discovery requires Home Assistant and the Raspberry Pi to see each other over mDNS/Multicast. If they are on different VLANs, enable an mDNS reflector/repeater on the network.
