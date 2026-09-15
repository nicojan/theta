"""Orchestration tests.

This layer had no tests, which is how two defects reached a real device: a
paired-but-idle phone was reported `disconnected`, and
`--uninstall-conflicting` asked for a second confirmation that auto-denied
itself whenever stdin was not a TTY.
"""

import unittest
from unittest import mock

from sideload import device as device_mod
from sideload import pipeline
from sideload.errors import DeviceError, SideloadError


def make_device(name="nPhone", tunnel="connected", pairing="paired", udid="UDID-1"):
    return device_mod.Device(
        name=name, udid=udid, identifier=f"ID-{name}", model="iPhone17,2",
        platform="iOS", os_version="27.0",
        tunnel_state=tunnel, pairing_state=pairing)


class Identity:
    def __init__(self, team_id="3CY4DX3K45"):
        self.team_id = team_id


class ResolveDevice(unittest.TestCase):
    def test_wakes_a_named_device_whose_tunnel_has_idled_down(self):
        cold = make_device(tunnel="disconnected")
        warm = make_device(tunnel="connected")
        with mock.patch.object(device_mod, "find_device", return_value=cold), \
             mock.patch.object(device_mod, "wake", return_value=warm) as woken:
            resolved = pipeline._resolve_device("nPhone")
        woken.assert_called_once_with(cold)
        self.assertTrue(resolved.is_ready)

    def test_does_not_wake_a_device_that_is_already_ready(self):
        warm = make_device(tunnel="connected")
        with mock.patch.object(device_mod, "find_device", return_value=warm), \
             mock.patch.object(device_mod, "wake") as woken:
            pipeline._resolve_device("nPhone")
        woken.assert_not_called()

    def test_reports_the_device_when_waking_fails(self):
        cold = make_device(tunnel="disconnected")
        with mock.patch.object(device_mod, "find_device", return_value=cold), \
             mock.patch.object(device_mod, "wake", return_value=cold):
            with self.assertRaises(DeviceError) as caught:
                pipeline._resolve_device("nPhone")
        self.assertIn("disconnected", caught.exception.message)
        # The old remedy insisted on USB at a device that had never been on USB.
        self.assertNotIn("USB", caught.exception.remedy)

    def test_autoselects_a_cold_but_paired_device_when_none_are_warm(self):
        cold = make_device(tunnel="disconnected")
        warm = make_device(tunnel="connected")
        with mock.patch.object(device_mod, "ios_devices", return_value=[cold]), \
             mock.patch.object(device_mod, "wake", return_value=warm):
            self.assertEqual(pipeline._resolve_device("").name, "nPhone")

    def test_prefers_a_warm_device_over_a_cold_one(self):
        cold = make_device(name="nPad", tunnel="disconnected", udid="UDID-2")
        warm = make_device(name="nPhone", tunnel="connected", udid="UDID-1")
        with mock.patch.object(device_mod, "ios_devices", return_value=[cold, warm]), \
             mock.patch.object(device_mod, "wake") as woken:
            self.assertEqual(pipeline._resolve_device("").name, "nPhone")
        woken.assert_not_called()

    def test_an_unpaired_device_is_not_a_candidate(self):
        unpaired = make_device(tunnel="disconnected", pairing="unpaired")
        with mock.patch.object(device_mod, "ios_devices", return_value=[unpaired]):
            with self.assertRaises(DeviceError) as caught:
                pipeline._resolve_device("")
        self.assertIn("No connected iOS device", caught.exception.message)


class ConflictingInstall(unittest.TestCase):
    BUNDLE = "com.burbn.instagram"

    def _run(self, installed_team, uninstall_conflicting):
        return self._run_record({"teamIdentifier": installed_team},
                                uninstall_conflicting)

    def _run_record(self, record, uninstall_conflicting):
        target = make_device()
        existing = {self.BUNDLE: record}
        options = pipeline.Options(ipa="x.ipa",
                                   uninstall_conflicting=uninstall_conflicting)
        with mock.patch.object(device_mod, "installed_apps", return_value=existing), \
             mock.patch.object(device_mod, "uninstall_app") as removed:
            pipeline._handle_conflicting_install(
                target, self.BUNDLE, Identity(), options, lambda _m: None)
        return removed

    def test_the_flag_is_the_confirmation(self):
        # Regression: this previously called a confirm() callback that returned
        # False off a TTY, so the flag silently cancelled the run it enabled.
        removed = self._run("OTHERTEAM", uninstall_conflicting=True)
        removed.assert_called_once()

    def test_without_the_flag_it_refuses_rather_than_deleting(self):
        with self.assertRaises(SideloadError) as caught:
            self._run("OTHERTEAM", uninstall_conflicting=False)
        self.assertIn("--uninstall-conflicting", caught.exception.remedy)

    def test_same_team_is_replaced_in_place_without_uninstalling(self):
        removed = self._run("3CY4DX3K45", uninstall_conflicting=False)
        removed.assert_not_called()

    def test_an_unreported_team_is_replaced_rather_than_deleted(self):
        # devicectl returns no teamIdentifier key at all for a developer-signed
        # Instagram on iOS 27 -- this is the real record shape, minus the key the
        # other fixtures invent. Unreadable was being treated as different, which
        # demanded an uninstall and threw away the container (and the login).
        record = {"bundleIdentifier": self.BUNDLE, "version": "442.0.0",
                  "builtByDeveloper": True}
        removed = self._run_record(record, uninstall_conflicting=False)
        removed.assert_not_called()

    def test_an_unreported_team_does_not_delete_even_with_the_flag(self):
        record = {"bundleIdentifier": self.BUNDLE, "version": "442.0.0"}
        removed = self._run_record(record, uninstall_conflicting=True)
        removed.assert_not_called()

    def test_uninstalling_is_off_by_default(self):
        # A destructive option must never be on for a caller that did not ask.
        self.assertFalse(pipeline.Options(ipa="x.ipa").uninstall_conflicting)


if __name__ == "__main__":
    unittest.main()
