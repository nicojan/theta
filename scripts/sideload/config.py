"""Persisted settings.

Lives in ~/.config/theta-sideload/, never in the repository. The App Store
Connect private key is referenced by path and is never copied here.
"""

import os
import tomllib

CONFIG_DIR = os.path.expanduser("~/.config/theta-sideload")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")

DEFAULTS = {
    "device": "",
    "identity": "",
    "profile": "",
    "asc_key_path": "",
    "asc_key_id": "",
    "asc_issuer_id": "",
    "remove_extensions": True,
    "get_task_allow": True,
    "display_name": "",
}


def load():
    settings = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as fh:
            settings.update(tomllib.load(fh))
    return settings


def save(settings):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    lines = ["# theta-sideload settings. Written by the tool; safe to edit by hand.", ""]
    for key in sorted(settings):
        value = settings[key]
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        else:
            lines.append(f'{key} = "{str(value)}"')
    with open(CONFIG_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH
