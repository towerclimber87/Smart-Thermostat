#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data
if [[ ! -s public/config/default-config.json ]]; then
  echo "Missing public/config/default-config.json" >&2
  exit 1
fi
python3 - <<'PY'
import json
import time
from pathlib import Path
root = Path.cwd()
default = json.loads((root / "public/config/default-config.json").read_text(encoding="utf-8"))
config = default.get("config") if isinstance(default, dict) and isinstance(default.get("config"), dict) else default
if not isinstance(config, dict) or "integrations" not in config:
    raise SystemExit("Default config does not look like a Smart Thermostat config")
record = {
    "version": 608,
    "updatedAt": int(time.time()),
    "config": config,
}
(root / "data").mkdir(exist_ok=True)
(root / "data/panel-config.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("Restored data/panel-config.json from bundled known-good config")
PY
sudo systemctl restart smart-thermostat-web.service smart-thermostat-native.service 2>/dev/null || true
