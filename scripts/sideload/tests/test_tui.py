import os
import shutil
import tempfile
import unittest
from unittest import mock

from sideload import device as device_mod
from sideload import pipeline
from sideload.errors import SideloadError

try:
    from sideload.tui import SideloadApp
    HAVE_TEXTUAL = True
except ImportError:  # textual is only needed for the interface
    HAVE_TEXTUAL = False

FIXTURE_DEVICES = [
    device_mod.Device(name="nPhone", udid="00008140-001478AE3C08801C",
                      identifier="0C709925", model="iPhone17,2", platform="iOS",
                      os_version="27.0", tunnel_state="connected", pairing_state="paired"),
    device_mod.Device(name="nPad", udid="00008110-001E11292E41801E",
                      identifier="3DA7699C", model="iPad14,1", platform="iOS",
                      os_version="26.2.1", tunnel_state="disconnected", pairing_state="paired"),
]


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class TuiSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_mounts_and_lists_devices(self):
        app = SideloadApp(root=os.path.dirname(__file__))
        with mock.patch.object(device_mod, "ios_devices", return_value=FIXTURE_DEVICES):
            async with app.run_test() as pilot:
                await pilot.pause()
                listing = app.query_one("#devices")
                self.assertEqual(len(listing), 2)
                # The ready device is preselected; a sleeping phone is not.
                self.assertEqual(app.selected_device.name, "nPhone")

    async def test_refuses_to_run_without_a_profile(self):
        app = SideloadApp(root=os.path.dirname(__file__))
        with mock.patch.object(device_mod, "ios_devices", return_value=FIXTURE_DEVICES):
            async with app.run_test() as pilot:
                await pilot.pause()
                app.profile = None
                app.action_sideload()
                await pilot.pause()
                self.assertFalse(app.busy)

    async def test_refuses_a_disconnected_device(self):
        app = SideloadApp(root=os.path.dirname(__file__))
        with mock.patch.object(device_mod, "ios_devices", return_value=[FIXTURE_DEVICES[1]]):
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_sideload()
                await pilot.pause()
                self.assertFalse(app.busy)


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class WorkerPlumbing(unittest.IsolatedAsyncioTestCase):
    """The pipeline runs on a thread; its narration must reach the log widget."""

    def setUp(self):
        # The interface refuses to run without a device, an IPA and a profile.
        # Two of those are real objects; the IPA only has to exist on disk.
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "output"))
        open(os.path.join(self.root, "output", "Instagram_patched.ipa"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    async def _ready_app(self, pilot_body):
        app = SideloadApp(root=self.root)
        seen = []
        with mock.patch.object(device_mod, "ios_devices", return_value=[FIXTURE_DEVICES[0]]):
            async with app.run_test() as pilot:
                await pilot.pause()
                # Set after mounting: on_mount reloads the profile from config.
                app.profile = mock.Mock(path="/tmp/p.mobileprovision", is_expired=False)
                app.query_one("#log").write = lambda text, *a, **k: seen.append(str(text))
                await pilot_body(app, pilot)
                for _ in range(80):
                    await pilot.pause()
                    if not app.busy:
                        break
        return app, "\n".join(seen)

    async def test_streams_progress_and_reports_success(self):
        def fake_run(options, progress=None, confirm=None):
            progress("Unpacking")
            progress("Signing 7 nested binaries")
            return pipeline.Result(bundle_id="com.burbn.instagram",
                                   device_name="nPhone", installed=True)

        async def body(app, _pilot):
            with mock.patch.object(pipeline, "run", side_effect=fake_run):
                app.action_sideload()
                for _ in range(80):
                    await _pilot.pause()
                    if not app.busy:
                        break

        app, log = await self._ready_app(body)
        self.assertFalse(app.busy)
        self.assertIn("Unpacking", log)
        self.assertIn("Signing 7 nested binaries", log)
        self.assertIn("installed on nPhone", log)

    async def test_surfaces_the_remedy_when_a_step_fails(self):
        failure = SideloadError("The device is locked", remedy="Unlock the device.")

        async def body(app, _pilot):
            with mock.patch.object(pipeline, "run", side_effect=failure):
                app.action_sideload()
                for _ in range(80):
                    await _pilot.pause()
                    if not app.busy:
                        break

        _app, log = await self._ready_app(body)
        self.assertIn("The device is locked", log)
        # A failure without its remedy just tells the user they lost.
        self.assertIn("Unlock the device.", log)


if __name__ == "__main__":
    unittest.main()
