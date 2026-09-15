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


class Waking(unittest.TestCase):
    """A cold tunnel is a stale cache, not an absent device.

    `list devices` reports the tunnel state devicectl last saw. An idle phone
    drops its tunnel in a minute or two, so a cold read called a reachable
    device `disconnected` and aborted the install before it started.
    """

    def _warm_fixture(self):
        import copy
        warm = copy.deepcopy(FIXTURE)
        for entry in warm["result"]["devices"]:
            if entry["deviceProperties"]["name"] == "nPad":
                entry["connectionProperties"]["tunnelState"] = "connected"
        return warm

    def test_paired_device_is_wakeable_even_when_disconnected(self):
        with fake_devicectl(FIXTURE):
            pad = device_mod.find_device("nPad")
        self.assertFalse(pad.is_ready)
        self.assertTrue(pad.is_wakeable)

    def test_wake_reestablishes_the_tunnel_and_rereads_the_device(self):
        with fake_devicectl(FIXTURE):
            pad = device_mod.find_device("nPad")
        with mock.patch.object(device_mod, "_devicectl",
                               return_value=self._warm_fixture()["result"]) as called:
            woken = device_mod.wake(pad)
        # The wake call itself, then the re-read.
        self.assertEqual(called.call_args_list[0][0][0][:3],
                         ["device", "info", "details"])
        self.assertTrue(woken.is_ready)
        self.assertEqual(woken.udid, pad.udid)

    def test_wake_is_a_noop_for_an_unpaired_device(self):
        unpaired = device_mod.Device(
            name="x", udid="u", identifier="i", model="m", platform="iOS",
            os_version="27.0", tunnel_state="disconnected", pairing_state="unpaired")
        with mock.patch.object(device_mod, "_devicectl") as called:
            self.assertIs(device_mod.wake(unpaired), unpaired)
        called.assert_not_called()

    def test_wake_returns_the_original_when_it_cannot_connect(self):
        with fake_devicectl(FIXTURE):
            pad = device_mod.find_device("nPad")
        with mock.patch.object(device_mod, "_devicectl",
                               side_effect=DeviceError("no route")):
            self.assertIs(device_mod.wake(pad), pad)


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
