"""Named rear-trim presets (e.g. one per wheel or trainer), stored per user.

File: $DI2_PRESETS, else $XDG_CONFIG_HOME/di2/presets.json (~/.config/di2/presets.json).
Nothing is shipped with the project; users save their own from the web UI.

Format: {"<name>": {"rear_trim": int, "trim_range": int, "rear_derailleur": str,
                    "serial": str, "saved": iso time, "note": str}, ...}
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Optional


def path() -> Path:
    if os.environ.get('DI2_PRESETS'):
        return Path(os.environ['DI2_PRESETS'])
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'di2' / 'presets.json'


def load() -> Dict[str, dict]:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get('rear_trim'), int)}


def _write(data: Dict[str, dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)


def save(name: str, rear_trim: int, rear_derailleur: Optional[dict] = None, note: str = '') -> Dict[str, dict]:
    name = name.strip()[:40]
    if not name:
        raise ValueError('a preset needs a name')
    rd = rear_derailleur or {}
    data = load()
    data[name] = {'rear_trim': int(rear_trim), 'trim_range': (rd.get('settings') or {}).get('trim_range'),
                  'rear_derailleur': rd.get('model'), 'serial': rd.get('serial'),
                  'saved': time.strftime('%Y-%m-%dT%H:%M:%S'), 'note': note[:200]}
    _write(data)
    return data


def delete(name: str) -> Dict[str, dict]:
    data = load()
    data.pop(name, None)
    _write(data)
    return data
