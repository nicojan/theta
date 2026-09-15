"""Unpack, strip, rewrite and re-sign an IPA.

Signing is inside-out and never uses `--deep`. `--deep` re-signs nested code with
the *outer* bundle's entitlements, which is wrong for app extensions and silently
produces a bundle that installs and then refuses to launch.
"""

import os
import plistlib
import shutil
from dataclasses import dataclass, field

from . import profile as profile_mod
from .errors import SigningError
from .run import run, run_text

# Ordinary bundle wrappers whose contents are signed as a unit.
_SIGNABLE_BUNDLES = (".framework", ".appex", ".app", ".xctest")


@dataclass
class ResignPlan:
    """What a re-sign will do, computed before anything is written."""

    app_path: str
    original_bundle_id: str
    bundle_id: str
    bundle_id_rewritten: bool
    display_name: str = None
    removed_extensions: list = field(default_factory=list)
    entitlements: dict = field(default_factory=dict)
    dropped_entitlements: list = field(default_factory=list)


def unpack(ipa_path, dest_dir):
    """Extract an IPA.

    `ditto -x -k` rather than `unzip`: unzip does not reliably preserve the
    symlinks inside .framework bundles, and a framework whose Versions/Current
    link has become a real directory fails to sign.
    """
    os.makedirs(dest_dir, exist_ok=True)
    try:
        run(["ditto", "-x", "-k", str(ipa_path), str(dest_dir)], timeout=1800)
    except RuntimeError as exc:
        raise SigningError(f"Could not unpack {ipa_path}",
                           remedy="Confirm the file is a complete IPA.",
                           detail=str(exc)) from exc
    return find_app_bundle(dest_dir)


def find_app_bundle(unpacked_dir):
    payload = os.path.join(unpacked_dir, "Payload")
    if not os.path.isdir(payload):
        raise SigningError("No Payload directory in the IPA",
                           remedy="This does not look like an iOS application archive.")
    apps = [e for e in sorted(os.listdir(payload)) if e.endswith(".app")]
    if not apps:
        raise SigningError("No .app bundle inside Payload")
    if len(apps) > 1:
        raise SigningError(f"Payload holds {len(apps)} app bundles",
                           remedy="Expected exactly one; the archive may be malformed.")
    return os.path.join(payload, apps[0])


def read_plist(path):
    with open(path, "rb") as fh:
        return plistlib.load(fh)


def write_plist(path, data):
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)


def read_entitlements(binary_or_bundle):
    """Entitlements currently embedded in a signed bundle, or {} if unsigned."""
    proc = run(["codesign", "-d", "--entitlements", "-", "--xml", str(binary_or_bundle)],
               check=False)
    if proc.returncode != 0 or not proc.stdout:
        return {}
    try:
        return plistlib.loads(proc.stdout)
    except Exception:
        return {}


def strip_extensions(app_path):
    """Remove app extensions and any bundled watch app.

    They communicate through app groups that a re-signed build no longer holds,
    and under an explicit App ID each would need its own registered identifier.
    """
    removed = []
    for subdir in ("PlugIns", "Watch", "com.apple.WatchPlaceholder"):
        target = os.path.join(app_path, subdir)
        if not os.path.isdir(target):
            continue
        for entry in sorted(os.listdir(target)):
            removed.append(f"{subdir}/{entry}")
        shutil.rmtree(target)
    return removed


def plan(app_path, prof, display_name=None, remove_extensions=True, get_task_allow=True):
    """Compute every change before making any of them."""
    info_path = os.path.join(app_path, "Info.plist")
    info = read_plist(info_path)
    original_bundle_id = info.get("CFBundleIdentifier", "")

    bundle_id, rewritten = profile_mod.derive_bundle_id(prof, original_bundle_id)
    app_entitlements = read_entitlements(app_path)
    entitlements = profile_mod.compute_entitlements(
        app_entitlements, prof, bundle_id, get_task_allow=get_task_allow
    )

    extensions = []
    if remove_extensions:
        plugins = os.path.join(app_path, "PlugIns")
        if os.path.isdir(plugins):
            extensions = [f"PlugIns/{e}" for e in sorted(os.listdir(plugins))]
        watch = os.path.join(app_path, "Watch")
        if os.path.isdir(watch):
            extensions += [f"Watch/{e}" for e in sorted(os.listdir(watch))]

    return ResignPlan(
        app_path=app_path,
        original_bundle_id=original_bundle_id,
        bundle_id=bundle_id,
        bundle_id_rewritten=rewritten,
        display_name=display_name,
        removed_extensions=extensions,
        entitlements=entitlements,
        dropped_entitlements=profile_mod.dropped_entitlements(app_entitlements, entitlements),
    )


def signable_paths(app_path):
    """Everything needing its own signature, deepest first.

    Depth ordering is what makes this inside-out: a framework nested inside an
    extension must be signed before the extension that seals it.
    """
    targets = []
    for root, dirs, files in os.walk(app_path):
        for name in list(dirs):
            if name.endswith(_SIGNABLE_BUNDLES):
                targets.append(os.path.join(root, name))
                dirs.remove(name)
                for sub_root, sub_dirs, sub_files in os.walk(os.path.join(root, name)):
                    for sub_name in list(sub_dirs):
                        if sub_name.endswith(_SIGNABLE_BUNDLES):
                            targets.append(os.path.join(sub_root, sub_name))
                    for sub_name in sub_files:
                        if sub_name.endswith(".dylib"):
                            targets.append(os.path.join(sub_root, sub_name))
        for name in files:
            if name.endswith(".dylib"):
                targets.append(os.path.join(root, name))

    unique = sorted(set(targets), key=lambda p: (p.count(os.sep), p), reverse=True)
    return [p for p in unique if os.path.abspath(p) != os.path.abspath(app_path)]


def apply(resign_plan, prof, identity_sha1, progress=None):
    """Carry out the plan: strip, rewrite, embed the profile, sign, verify."""
    app_path = resign_plan.app_path
    say = progress or (lambda _message: None)

    if resign_plan.removed_extensions:
        say(f"Removing {len(resign_plan.removed_extensions)} app extensions")
        strip_extensions(app_path)

    info_path = os.path.join(app_path, "Info.plist")
    info = read_plist(info_path)
    if resign_plan.bundle_id_rewritten:
        say(f"Rewriting bundle ID to {resign_plan.bundle_id}")
        info["CFBundleIdentifier"] = resign_plan.bundle_id
    if resign_plan.display_name:
        info["CFBundleDisplayName"] = resign_plan.display_name
    write_plist(info_path, info)

    say("Embedding provisioning profile")
    shutil.copyfile(prof.path, os.path.join(app_path, "embedded.mobileprovision"))
    _remove_stale_signatures(app_path)

    targets = signable_paths(app_path)
    say(f"Signing {len(targets)} nested binaries, then the app")
    for index, target in enumerate(targets, 1):
        _codesign(target, identity_sha1, entitlements=None)
        if index % 10 == 0 or index == len(targets):
            say(f"  signed {index}/{len(targets)}")

    _codesign(app_path, identity_sha1, entitlements=resign_plan.entitlements)

    say("Verifying signature")
    verify(app_path)
    return app_path


def _remove_stale_signatures(app_path):
    """Drop every inherited _CodeSignature before re-signing.

    A stale seal left behind in a nested bundle verifies against the old
    certificate and produces a failure a long way from its cause.
    """
    for root, dirs, _files in os.walk(app_path):
        for name in list(dirs):
            if name == "_CodeSignature":
                shutil.rmtree(os.path.join(root, name))
                dirs.remove(name)


def _codesign(target, identity_sha1, entitlements=None):
    argv = ["codesign", "--force", "--sign", identity_sha1,
            "--generate-entitlement-der", "--timestamp=none"]

    entitlements_path = None
    if entitlements is not None:
        entitlements_path = os.path.join(
            os.path.dirname(os.path.abspath(target)), ".entitlements.plist"
        )
        write_plist(entitlements_path, entitlements)
        argv += ["--entitlements", entitlements_path]

    try:
        proc = run([*argv, str(target)], check=False, timeout=1800)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            raise SigningError(
                f"codesign failed on {os.path.basename(target)}",
                remedy=_signing_remedy(stderr),
                detail=stderr,
            )
    finally:
        if entitlements_path and os.path.exists(entitlements_path):
            os.unlink(entitlements_path)


def _signing_remedy(stderr):
    lowered = stderr.lower()
    if "no identity found" in lowered or "unknown identity" in lowered:
        return "The signing identity is not in the login keychain."
    if "user interaction is not allowed" in lowered:
        return "Unlock the login keychain, or allow codesign to use the key."
    if "resource fork" in lowered or "finder information" in lowered:
        return "Run `xattr -cr` on the app bundle and retry."
    if "bundle format" in lowered:
        return "A nested bundle is malformed, often a mangled framework symlink."
    return None


def verify(app_path):
    """Verify the outer bundle and every nested signature individually."""
    proc = run(["codesign", "--verify", "--strict", "--verbose=2", str(app_path)],
               check=False, timeout=900)
    if proc.returncode != 0:
        raise SigningError(
            "The re-signed bundle does not verify",
            detail=proc.stderr.decode("utf-8", "replace").strip(),
        )

    for target in signable_paths(app_path):
        nested = run(["codesign", "--verify", "--strict", str(target)],
                     check=False, timeout=300)
        if nested.returncode != 0:
            raise SigningError(
                f"Nested signature invalid: {os.path.relpath(target, app_path)}",
                detail=nested.stderr.decode("utf-8", "replace").strip(),
            )
    return True


def describe_signature(app_path):
    """Team and identifier actually sealed into the bundle, for verification."""
    proc = run(["codesign", "-dv", str(app_path)], check=False)
    fields = {}
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def repack(unpacked_dir, output_ipa):
    """Zip an unpacked Payload back into an IPA."""
    if os.path.exists(output_ipa):
        os.unlink(output_ipa)
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
         os.path.join(unpacked_dir, "Payload"), str(output_ipa)], timeout=1800)
    return output_ipa
