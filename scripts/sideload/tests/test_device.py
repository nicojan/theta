import json
import os
import tempfile
import unittest
from unittest import mock

from sideload import device as device_mod
from sideload.errors import DeviceError

# Captured verbatim from `xcrun devicectl list devices --json-output` on this
# machine, trimmed to the fields the wrapper reads.
FIXTURE = {
    "result": {
        "devices": [
            {
                "identifier": "0C709925-F62F-50ED-9EC2-A187009593A3",
                "deviceProperties": {"name": "nPhone", "osVersionNumber": "27.0"},
                "hardwareProperties": {"udid": "00008140-001478AE3C08801C",
                                       "productType": "iPhone17,2", "platform": "iOS"},
                "connectionProperties": {"tunnelState": "connected", "pairingState": "paired"},
            },
            {
                "identifier": "3DA7699C-B95C-57B5-955F-70D813D4A5F2",
                "deviceProperties": {"name": "nPad", "osVersionNumber": "26.2.1"},
                "hardwareProperties": {"udid": "00008110-001E11292E41801E",
                                       "productType": "iPad14,1", "platform": "iOS"},
                "connectionProperties": {"tunnelState": "disconnected", "pairingState": "paired"},
            },
            {
                "identifier": "7083AFB2-44A3-5E99-9FF3-B146267B9646",
                "deviceProperties": {"name": "nWatch", "osVersionNumber": "10.6.2"},
                "hardwareProperties": {"udid": "00008006-0008E4CA1147002E",
                                       "productType": "Watch5,2", "platform": "watchOS"},
                "connectionProperties": {"tunnelState": "disconnected", "pairingState": "paired"},
            },
        ]
    }
}


def fake_devicectl(result):
    return mock.patch.object(device_mod, "_devicectl", return_value=result["result"])


class DeviceParsing(unittest.TestCase):
    def test_reads_the_real_udid_not_the_coredevice_identifier(self):
        with fake_devicectl(FIXTURE):
            phone = device_mod.find_device("nPhone")
        # devicectl's `identifier` is a CoreDevice UUID and is NOT the UDID the
        # developer portal wants. Confusing the two registers a device that then
        # never matches a profile.
        self.assertEqual(phone.udid, "00008140-001478AE3C08801C")
        self.assertNotEqual(phone.udid, phone.identifier)

    def test_filters_non_ios_devices(self):
        with fake_devicectl(FIXTURE):
            names = [d.name for d in device_mod.ios_devices()]
        self.assertEqual(names, ["nPhone", "nPad"])

    def test_readiness_requires_a_connected_tunnel(self):
        with fake_devicectl(FIXTURE):
            phone = device_mod.find_device("nPhone")
            pad = device_mod.find_device("nPad")
        self.assertTrue(phone.is_ready)
        self.assertFalse(pad.is_ready)
        self.assertEqual(pad.status, "disconnected")

    def test_finds_by_udid(self):
        with fake_devicectl(FIXTURE):
            found = device_mod.find_device("00008140-001478AE3C08801C")
        self.assertEqual(found.name, "nPhone")

    def test_finds_by_case_insensitive_fragment(self):
        with fake_devicectl(FIXTURE):
            self.assertEqual(device_mod.find_device("nphone").name, "nPhone")

    def test_unknown_device_lists_what_is_known(self):
        with fake_devicectl(FIXTURE):
            with self.assertRaises(DeviceError) as caught:
                device_mod.find_device("nonesuch")
        self.assertIn("nPhone", caught.exception.remedy)


class InstalledApps(unittest.TestCase):
    def test_keys_apps_by_bundle_identifier(self):
        result = {"result": {"apps": [
            {"bundleIdentifier": "com.burbn.instagram", "version": "442.0.0"},
            {"bundleIdentifier": "com.apple.Maps", "version": "1.0"},
        ]}}
        with fake_devicectl(result):
            apps = device_mod.installed_apps(mock.Mock(udid="x"))
        self.assertEqual(apps["com.burbn.instagram"]["version"], "442.0.0")


class FailureMapping(unittest.TestCase):
    def _error(self, message):
        proc = mock.Mock(returncode=1, stderr=message.encode())
        payload = {"error": {"userInfo": {"NSLocalizedDescription": {"string": message}}}}
        return payload, proc

    def test_locked_device_suggests_unlocking(self):
        payload, proc = self._error("The device is locked with a passcode.")
        self.assertIn("Unlock", device_mod._remedy_for(payload, proc))

    def test_missing_device_suggests_reconnecting(self):
        payload, proc = self._error("Device not found.")
        self.assertIn("Reconnect", device_mod._remedy_for(payload, proc))

    def test_entitlement_failure_points_at_the_profile(self):
        payload, proc = self._error("no valid provisioning profile found")
        self.assertIn("profile", device_mod._remedy_for(payload, proc))

    def test_uses_the_localized_description_over_raw_stderr(self):
        payload, proc = self._error("The device is locked with a passcode.")
        self.assertEqual(device_mod._describe_failure(payload, proc),
                         "The device is locked with a passcode.")


if __name__ == "__main__":
    unittest.main()
