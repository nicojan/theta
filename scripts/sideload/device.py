"""devicectl wrapper.

Everything goes through `--json-output` into a temp file and is then parsed.
devicectl's table output is for humans and its columns shift between Xcode
releases; the JSON is the contract.
"""

import json
import os
import tempfile
from dataclasses import dataclass

from .errors import DeviceError
from .run import redact, run

# devicectl reports these. Only `connected` can receive an install, but a
# `disconnected` reading is not an answer on its own -- see wake().
CONNECTED = "connected"
PAIRED = "paired"


@dataclass
class Device:
    name: str
    udid: str
    identifier: str
    model: str
    platform: str
    os_version: str
    tunnel_state: str
    pairing_state: str

    @property
    def is_ios(self):
        return (self.platform or "").lower() == "ios"

    @property
    def is_ready(self):
        return self.tunnel_state == CONNECTED and self.pairing_state == PAIRED

    @property
    def is_wakeable(self):
        """Paired, so a dropped tunnel can be brought back up. See wake()."""
        return self.pairing_state == PAIRED

    @property
    def status(self):
        if self.pairing_state != PAIRED:
            return "not paired"
        if self.tunnel_state != CONNECTED:
            return self.tunnel_state or "disconnected"
        return "ready"


def _devicectl(args, timeout=300):
    """Run a devicectl subcommand and return its parsed JSON result."""
    handle, out_path = tempfile.mkstemp(suffix=".json", prefix="devicectl-")
    os.close(handle)
    try:
        proc = run(["xcrun", "devicectl", *args, "--json-output", out_path],
                   check=False, timeout=timeout)
        try:
            with open(out_path) as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            payload = None

        if proc.returncode != 0:
            raise DeviceError(
                _describe_failure(payload, proc),
                remedy=_remedy_for(payload, proc),
                detail=redact(proc.stderr.decode("utf-8", "replace")),
            )
        return (payload or {}).get("result", {})
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _describe_failure(payload, proc):
    error = (payload or {}).get("error") or {}
    message = error.get("userInfo", {}).get("NSLocalizedDescription", {}).get("string")
    if message:
        return message
    stderr = redact(proc.stderr.decode("utf-8", "replace")).strip()
    return stderr.splitlines()[-1] if stderr else f"devicectl exited {proc.returncode}"


def _remedy_for(payload, proc):
    blob = json.dumps(payload or {}).lower() + proc.stderr.decode("utf-8", "replace").lower()
    if "passcode" in blob or "locked" in blob:
        return "Unlock the device and keep it unlocked for the whole install."
    if "not found" in blob or "unavailable" in blob or "no device" in blob:
        return "Reconnect the device over USB, or confirm Wi-Fi pairing is active."
    if "trust" in blob:
        return "Tap Trust on the device, then retry."
    if "valid provisioning profile" in blob or "entitlement" in blob:
        return "The profile does not cover this device or bundle ID; re-mint it."
    return None


def list_devices():
    """All devices devicectl knows about, iOS and otherwise."""
    result = _devicectl(["list", "devices"], timeout=60)
    devices = []
    for raw in result.get("devices", []):
        hardware = raw.get("hardwareProperties", {})
        props = raw.get("deviceProperties", {})
        connection = raw.get("connectionProperties", {})
        devices.append(Device(
            name=props.get("name", "?"),
            udid=hardware.get("udid", ""),
            identifier=raw.get("identifier", ""),
            model=hardware.get("productType", ""),
            platform=hardware.get("platform", ""),
            os_version=props.get("osVersionNumber", ""),
            tunnel_state=connection.get("tunnelState", ""),
            pairing_state=connection.get("pairingState", ""),
        ))
    return devices


def ios_devices():
    return [d for d in list_devices() if d.is_ios]


def find_device(needle):
    """Resolve a name, UDID or devicectl identifier to a single Device."""
    candidates = list_devices()
    exact = [d for d in candidates
             if needle in (d.udid, d.identifier) or d.name == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise DeviceError(f"{needle!r} matches {len(exact)} devices",
                          remedy="Use the UDID instead of the name.")

    loose = [d for d in candidates if needle.lower() in d.name.lower()]
    if len(loose) == 1:
        return loose[0]
    if not loose:
        known = ", ".join(d.name for d in candidates) or "none"
        raise DeviceError(f"No device matching {needle!r}",
                          remedy=f"Known devices: {known}")
    raise DeviceError(f"{needle!r} is ambiguous",
                      remedy="Use the UDID instead of the name.")


def wake(device, timeout=120):
    """Bring the on-demand tunnel back up, and return the device re-read.

    `list devices` reports the tunnel state devicectl last saw, and an idle
    phone drops its tunnel within a minute or two -- so a cold read says
    `disconnected` about a device that is reachable. Any real operation
    re-establishes the tunnel; `device info details` is the cheapest one.
    A device that is not paired cannot be woken, and is returned unchanged.
    """
    if not device.is_wakeable:
        return device
    try:
        _devicectl(["device", "info", "details", "--device", device.udid],
                   timeout=timeout)
    except DeviceError:
        # Leave the caller to report the original status; a failure to wake is
        # not more informative than the state it was already in.
        return device
    for entry in list_devices():
        if entry.udid == device.udid:
            return entry
    return device


def installed_apps(device):
    """Installed apps keyed by bundle identifier."""
    result = _devicectl(["device", "info", "apps", "--device", device.udid], timeout=120)
    apps = {}
    for app in result.get("apps", []):
        bundle_id = app.get("bundleIdentifier")
        if bundle_id:
            apps[bundle_id] = app
    return apps


def install_app(device, app_path, timeout=900):
    """Install a .app bundle. devicectl does not accept an .ipa."""
    result = _devicectl(
        ["device", "install", "app", "--device", device.udid, str(app_path)],
        timeout=timeout,
    )
    return result.get("installedApplications", [])


def uninstall_app(device, bundle_id, timeout=300):
    return _devicectl(
        ["device", "uninstall", "app", "--device", device.udid, bundle_id],
        timeout=timeout,
    )


def launch_app(device, bundle_id, timeout=120):
    return _devicectl(
        ["device", "process", "launch", "--device", device.udid, bundle_id],
        timeout=timeout,
    )
