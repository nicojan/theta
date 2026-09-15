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
            os.makedirs(os.path.join(self.app, relative), exist_ok=True)
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
