"""Mint a development provisioning profile through Xcode, with no API key.

Xcode's automatic signing already knows how to talk to the developer portal
using the account signed in under Settings > Accounts. This builds a throwaway
single-file app for the device, lets Xcode register the device and mint the
profile, then lifts the profile straight out of the built product rather than
guessing which file in ~/Library/Developer/Xcode/UserData landed last.

For a paid team this returns the team wildcard profile (App ID `<TEAM>.*`),
which signs any bundle identifier and so preserves the app's original one. A
free personal team gets an explicit App ID instead, and the pipeline then
rewrites the bundle identifier to match -- see profile.derive_bundle_id.
"""

import os
import shutil
import tempfile

from . import profile as profile_mod
from .errors import SideloadError
from .run import redact, run

DEFAULT_BUNDLE_ID = "ca.forhuman.theta.instagram"

_MAIN_SWIFT = "import UIKit\n"

_PBXPROJ = """// !$*UTF8*$!
{
\tarchiveVersion = 1;
\tclasses = {
\t};
\tobjectVersion = 56;
\tobjects = {
\t\tAA00000000000000000001 = {isa = PBXBuildFile; fileRef = AA00000000000000000002; };
\t\tAA00000000000000000002 = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = main.swift; sourceTree = "<group>"; };
\t\tAA00000000000000000003 = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = Stub.app; sourceTree = BUILT_PRODUCTS_DIR; };
\t\tAA00000000000000000004 = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
\t\tAA00000000000000000005 = {isa = PBXGroup; children = (AA00000000000000000002, AA00000000000000000006); sourceTree = "<group>"; };
\t\tAA00000000000000000006 = {isa = PBXGroup; children = (AA00000000000000000003); name = Products; sourceTree = "<group>"; };
\t\tAA00000000000000000007 = {isa = PBXNativeTarget; buildConfigurationList = AA00000000000000000008; buildPhases = (AA00000000000000000009, AA00000000000000000004); buildRules = (); dependencies = (); name = Stub; productName = Stub; productReference = AA00000000000000000003; productType = "com.apple.product-type.application"; };
\t\tAA0000000000000000000A = {isa = PBXProject; attributes = {BuildIndependentTargetsInParallel = 1; LastUpgradeCheck = 2600; TargetAttributes = {AA00000000000000000007 = {CreatedOnToolsVersion = 26.0; }; }; }; buildConfigurationList = AA0000000000000000000B; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base); mainGroup = AA00000000000000000005; productRefGroup = AA00000000000000000006; projectDirPath = ""; projectRoot = ""; targets = (AA00000000000000000007); };
\t\tAA00000000000000000009 = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (AA00000000000000000001); runOnlyForDeploymentPostprocessing = 0; };
\t\tAA0000000000000000000C = {isa = XCBuildConfiguration; buildSettings = {ALWAYS_SEARCH_USER_PATHS = NO; CLANG_ENABLE_OBJC_ARC = YES; COPY_PHASE_STRIP = NO; IPHONEOS_DEPLOYMENT_TARGET = 15.0; ONLY_ACTIVE_ARCH = YES; SDKROOT = iphoneos; SWIFT_VERSION = 5.0; }; name = Debug; };
\t\tAA0000000000000000000D = {isa = XCBuildConfiguration; buildSettings = {CODE_SIGN_STYLE = Automatic; DEVELOPMENT_TEAM = "__TEAM__"; GENERATE_INFOPLIST_FILE = YES; PRODUCT_BUNDLE_IDENTIFIER = "__BUNDLE_ID__"; PRODUCT_NAME = "$(TARGET_NAME)"; SWIFT_VERSION = 5.0; TARGETED_DEVICE_FAMILY = "1,2"; }; name = Debug; };
\t\tAA0000000000000000000B = {isa = XCConfigurationList; buildConfigurations = (AA0000000000000000000C); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
\t\tAA00000000000000000008 = {isa = XCConfigurationList; buildConfigurations = (AA0000000000000000000D); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
\t};
\trootObject = AA0000000000000000000A;
}
"""


def _write_stub(root, team_id, bundle_id):
    project_dir = os.path.join(root, "Stub.xcodeproj")
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(root, "main.swift"), "w") as fh:
        fh.write(_MAIN_SWIFT)
    pbxproj = _PBXPROJ.replace("__TEAM__", team_id).replace("__BUNDLE_ID__", bundle_id)
    with open(os.path.join(project_dir, "project.pbxproj"), "w") as fh:
        fh.write(pbxproj)
    return project_dir


def _build(root, destination, products_dir):
    # Build output is redirected with build settings rather than
    # -derivedDataPath, which xcodebuild rejects unless a scheme is also given,
    # and a bare -target project has no scheme.
    return run([
        "xcodebuild",
        "-project", os.path.join(root, "Stub.xcodeproj"),
        "-target", "Stub",
        "-configuration", "Debug",
        "-destination", destination,
        "-allowProvisioningUpdates",
        f"SYMROOT={os.path.join(root, 'build')}",
        f"OBJROOT={os.path.join(root, 'obj')}",
        f"CONFIGURATION_BUILD_DIR={products_dir}",
        "build",
    ], check=False, timeout=900)


def _built_profile(products_dir):
    for root, _dirs, files in os.walk(products_dir):
        if root.endswith(".app") and "embedded.mobileprovision" in files:
            return os.path.join(root, "embedded.mobileprovision")
    return None


def mint(team_id, bundle_id=DEFAULT_BUNDLE_ID, device=None, destination=None,
         output_path=None, progress=None):
    """Build the stub and return the path to the minted .mobileprovision."""
    say = progress or (lambda _message: None)
    root = tempfile.mkdtemp(prefix="theta-sideload-stub-")
    products_dir = os.path.join(root, "products")

    try:
        say(f"Generating a stub project for {bundle_id} under team {team_id}")
        _write_stub(root, team_id, bundle_id)

        # Building for the specific device is what makes Xcode register it and
        # include it in the profile. A generic destination can mint a profile
        # that omits the very phone we are installing to.
        destinations = []
        if destination:
            destinations.append(destination)
        elif device is not None:
            destinations.append(f"platform=iOS,id={device.udid}")
        destinations.append("generic/platform=iOS")

        last_output = ""
        for attempt in destinations:
            say(f"Asking Xcode to provision ({attempt})")
            proc = _build(root, attempt, products_dir)
            last_output = redact(
                (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace")
            )
            minted = _built_profile(products_dir)
            if proc.returncode == 0 and minted:
                say("Xcode minted a profile")
                return _deliver(minted, output_path, device, say)
            shutil.rmtree(products_dir, ignore_errors=True)

        raise SideloadError(
            "Xcode would not mint a provisioning profile",
            remedy=_diagnose(last_output),
            detail=_tail(last_output),
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _deliver(minted, output_path, device, say):
    destination = output_path or os.path.join(
        os.path.expanduser("~/.config/theta-sideload"), "bootstrap.mobileprovision"
    )
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copyfile(minted, destination)

    prof = profile_mod.decode(destination)
    say(f"Profile {prof.app_id} covering {len(prof.device_udids)} devices, "
        f"expires {prof.expires:%Y-%m-%d}")
    if device is not None and not prof.covers_device(device.udid):
        raise SideloadError(
            f"The minted profile does not include {device.name}",
            remedy=f"Register {device.udid} with team {prof.team_id} in the "
                   "developer portal, then run bootstrap again.",
        )
    return destination


def _diagnose(output):
    lowered = output.lower()
    if "no account for team" in lowered or "no signing certificate" in lowered:
        return ("Xcode has no account for this team. Add it under "
                "Xcode > Settings > Accounts, then retry.")
    if "unable to log in" in lowered or "authentication" in lowered:
        return ("Xcode could not authenticate. Open Xcode, sign in again "
                "(two-factor may need confirming), then retry.")
    if "is not available" in lowered and "destination" in lowered:
        return "The device was not reachable. Connect and unlock it, then retry."
    if "bundle identifier" in lowered and ("not available" in lowered or "already" in lowered):
        return ("That bundle identifier is registered to another team. "
                "Pass --bundle-id with one you own.")
    return "Run with SIDELOAD_DEBUG=1 to see xcodebuild's own output."


def _tail(output, lines=40):
    return "\n".join(output.strip().splitlines()[-lines:])
