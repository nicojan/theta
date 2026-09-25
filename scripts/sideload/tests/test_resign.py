import os
import plistlib
import shutil
import tempfile
import unittest

from sideload import resign
from sideload.run import redact


class SigningOrder(unittest.TestCase):
    """Nested code must be signed before whatever seals it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = os.path.join(self.root, "Payload", "Instagram.app")
        for relative in (
            "Frameworks/FBSharedFramework.framework",
            "Frameworks/libavcodec.framework",
            "PlugIns/InstagramWidgetExtension.appex",
            "PlugIns/InstagramWidgetExtension.appex/Frameworks/Nested.framework",
        ):
            bundle = os.path.join(self.app, relative)
            os.makedirs(bundle, exist_ok=True)
            # Real bundles always carry an Info.plist. Without one codesign
            # rejects them, so a fixture lacking it tests an impossible input.
            open(os.path.join(bundle, "Info.plist"), "w").close()
        os.makedirs(os.path.join(self.app, "Frameworks"), exist_ok=True)
        open(os.path.join(self.app, "Frameworks", "Theta.dylib"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _relative(self):
        return [os.path.relpath(p, self.app) for p in resign.signable_paths(self.app)]

    def test_never_returns_the_app_itself(self):
        # The app is signed last, by apply(), with entitlements. If it appeared
        # here it would be signed early and without them.
        self.assertNotIn(".", self._relative())
        for path in resign.signable_paths(self.app):
            self.assertNotEqual(os.path.abspath(path), os.path.abspath(self.app))

    def test_finds_the_injected_dylib(self):
        self.assertIn("Frameworks/Theta.dylib", self._relative())

    def test_finds_frameworks_and_extensions(self):
        found = self._relative()
        self.assertIn("Frameworks/FBSharedFramework.framework", found)
        self.assertIn("PlugIns/InstagramWidgetExtension.appex", found)

    def test_signs_a_nested_framework_before_its_extension(self):
        found = self._relative()
        nested = found.index("PlugIns/InstagramWidgetExtension.appex/Frameworks/Nested.framework")
        appex = found.index("PlugIns/InstagramWidgetExtension.appex")
        self.assertLess(nested, appex)

    def test_returns_no_duplicates(self):
        found = self._relative()
        self.assertEqual(len(found), len(set(found)))


class FakeBundles(unittest.TestCase):
    """Theta's ffmpeg.framework is a container, not a framework.

    It ends in .framework but holds only nested .framework bundles -- no
    Info.plist, no binary. Signing it aborted the whole run on codesign's
    "bundle format unrecognized"; it must be descended into instead.
    """

    def _tree(self):
        root = tempfile.mkdtemp()
        app = os.path.join(root, "Instagram.app")
        # A real framework, nested inside a container that only looks like one.
        inner = os.path.join(app, "ffmpeg.framework", "libavfilter.framework")
        os.makedirs(inner)
        open(os.path.join(inner, "Info.plist"), "w").close()
        open(os.path.join(inner, "libavfilter"), "w").close()
        # A real, ordinary framework alongside it.
        real = os.path.join(app, "Frameworks", "Real.framework")
        os.makedirs(real)
        open(os.path.join(real, "Info.plist"), "w").close()
        self.addCleanup(shutil.rmtree, root, True)
        return app

    def test_container_is_skipped_but_its_frameworks_are_signed(self):
        app = self._tree()
        found = resign.signable_paths(app)
        names = [os.path.relpath(p, app) for p in found]
        self.assertNotIn("ffmpeg.framework", names)
        self.assertIn(os.path.join("ffmpeg.framework", "libavfilter.framework"), names)
        self.assertIn(os.path.join("Frameworks", "Real.framework"), names)

    def test_a_framework_with_only_a_binary_still_counts(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        app = os.path.join(root, "Instagram.app")
        fw = os.path.join(app, "Bare.framework")
        os.makedirs(fw)
        open(os.path.join(fw, "Bare"), "w").close()
        self.assertIn(fw, resign.signable_paths(app))


class ExtensionStripping(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = os.path.join(self.root, "Instagram.app")
        for name in ("InstagramWidgetExtension.appex", "InstagramShareExtension.appex"):
            os.makedirs(os.path.join(self.app, "PlugIns", name))
        os.makedirs(os.path.join(self.app, "Watch", "Watch.app"))
        os.makedirs(os.path.join(self.app, "Frameworks"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_removes_plugins_and_watch_but_not_frameworks(self):
        removed = resign.strip_extensions(self.app)
        self.assertFalse(os.path.exists(os.path.join(self.app, "PlugIns")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "Watch")))
        self.assertTrue(os.path.isdir(os.path.join(self.app, "Frameworks")))
        self.assertEqual(len(removed), 3)

    def test_prunes_removed_bundles_from_the_sinf_manifest(self):
        # installd replicates sinfs to every listed path on an upgrade, and aborts with
        # "Info.plist missing at …/PlugIns/X.appex" when the bundle there is gone.
        os.makedirs(os.path.join(self.app, "SC_Info"))
        manifest = os.path.join(self.app, "SC_Info", "Manifest.plist")
        with open(manifest, "wb") as fh:
            plistlib.dump({
                "SinfPaths": ["SC_Info/Instagram.sinf"],
                "SinfReplicationPaths": [
                    "Frameworks/A.framework/SC_Info/A.sinf",
                    "PlugIns/InstagramWidgetExtension.appex/SC_Info/InstagramWidgetExtension.sinf",
                    "Watch/Watch.app/SC_Info/Watch.sinf",
                    "SC_Info/Instagram.sinf",
                ],
            }, fh)
        resign.strip_extensions(self.app)
        with open(manifest, "rb") as fh:
            pruned = plistlib.load(fh)
        self.assertEqual(pruned["SinfPaths"], ["SC_Info/Instagram.sinf"])
        self.assertEqual(pruned["SinfReplicationPaths"],
                         ["Frameworks/A.framework/SC_Info/A.sinf", "SC_Info/Instagram.sinf"])

    def test_no_manifest_is_fine(self):
        resign.strip_extensions(self.app)
        self.assertFalse(os.path.exists(os.path.join(self.app, "SC_Info")))


class StaleSignatures(unittest.TestCase):
    def test_removes_every_inherited_code_signature(self):
        root = tempfile.mkdtemp()
        app = os.path.join(root, "Instagram.app")
        for relative in ("_CodeSignature",
                         "Frameworks/A.framework/_CodeSignature",
                         "PlugIns/B.appex/_CodeSignature"):
            os.makedirs(os.path.join(app, relative))
        try:
            resign._remove_stale_signatures(app)
            leftovers = [d for _r, dirs, _f in os.walk(app) for d in dirs if d == "_CodeSignature"]
            self.assertEqual(leftovers, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class Redaction(unittest.TestCase):
    def test_strips_a_jwt(self):
        token = ("eyJhbGciOiJFUzI1NiIsImtpZCI6IkFCQ0RFRiJ9"
                 ".eyJpc3MiOiJpc3N1ZXIiLCJhdWQiOiJhcHBzdG9yZSJ9"
                 ".c2lnbmF0dXJlLWJ5dGVzLWhlcmU")
        self.assertNotIn(token, redact(f"Authorization: Bearer {token}"))

    def test_strips_a_private_key_block(self):
        blob = "-----BEGIN PRIVATE KEY-----\nMIGkAgEBBDA\n-----END PRIVATE KEY-----"
        self.assertNotIn("MIGkAgEBBDA", redact(blob))

    def test_leaves_ordinary_output_alone(self):
        self.assertEqual(redact("codesign: signing Instagram.app"),
                         "codesign: signing Instagram.app")


if __name__ == "__main__":
    unittest.main()
