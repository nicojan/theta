"""Orchestration shared by the TUI and the CLI.

Both front ends call `run()`. There is deliberately no path a human can take
through the TUI that a script cannot take through the CLI.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass, field

from . import device as device_mod
from . import keychain, resign
from . import profile as profile_mod
from .errors import DeviceError, SideloadError


@dataclass
class Options:
    ipa: str
    device: str = ""
    identity: str = ""
    profile: str = ""
    display_name: str = ""
    remove_extensions: bool = True
    get_task_allow: bool = True
    uninstall_conflicting: bool = True
    launch: bool = False
    keep_unpacked: bool = False
    output_ipa: str = ""
    install: bool = True


@dataclass
class Result:
    bundle_id: str = ""
    app_path: str = ""
    device_name: str = ""
    dropped_entitlements: list = field(default_factory=list)
    removed_extensions: list = field(default_factory=list)
    installed: bool = False
    output_ipa: str = ""


def run(options, progress=None, confirm=None):
    """Sideload `options.ipa`. Returns a Result, raises SideloadError on failure.

    `progress(message)` receives step narration. `confirm(question)` is asked
    before anything destructive and must return True to proceed.
    """
    say = progress or (lambda _message: None)
    ask = confirm or (lambda _question: True)
    result = Result()

    target = None
    if options.install:
        say("Resolving device")
        target = _resolve_device(options.device)
        result.device_name = target.name
        say(f"Device: {target.name} · {target.model} · iOS {target.os_version} · {target.status}")

    say("Resolving signing identity")
    identity = _resolve_identity(options.identity)
    say(f"Identity: {identity.label}")

    say("Reading provisioning profile")
    prof = profile_mod.decode(options.profile)
    _check_profile(prof, identity, target)
    kind = "wildcard" if prof.is_wildcard else "explicit"
    say(f"Profile: {prof.name} · {prof.app_id} ({kind}) · expires {prof.expires:%Y-%m-%d}")

    workdir = tempfile.mkdtemp(prefix="theta-sideload-")
    try:
        say(f"Unpacking {os.path.basename(options.ipa)}")
        app_path = resign.unpack(options.ipa, workdir)

        plan = resign.plan(
            app_path, prof,
            display_name=options.display_name or None,
            remove_extensions=options.remove_extensions,
            get_task_allow=options.get_task_allow,
        )
        result.bundle_id = plan.bundle_id
        result.dropped_entitlements = plan.dropped_entitlements
        result.removed_extensions = plan.removed_extensions

        if plan.bundle_id_rewritten:
            say(f"Bundle ID {plan.original_bundle_id} -> {plan.bundle_id} "
                "(the profile's App ID is explicit, so it cannot sign the original)")
        if plan.dropped_entitlements:
            say(f"Dropping {len(plan.dropped_entitlements)} ungrantable entitlements: "
                + ", ".join(plan.dropped_entitlements[:4])
                + (" ..." if len(plan.dropped_entitlements) > 4 else ""))

        resign.apply(plan, prof, identity.sha1, progress=say)
        result.app_path = app_path

        sealed = resign.describe_signature(app_path)
        say(f"Sealed as {sealed.get('Identifier', '?')} · team {sealed.get('TeamIdentifier', '?')}")

        if options.output_ipa:
            say(f"Repacking to {options.output_ipa}")
            result.output_ipa = resign.repack(workdir, options.output_ipa)

        if not options.install:
            return result

        _handle_conflicting_install(target, plan.bundle_id, identity, options, say, ask)

        say("Installing")
        device_mod.install_app(target, app_path)
        result.installed = True

        say("Verifying install")
        _verify_installed(target, plan.bundle_id, say)

        if options.launch:
            say("Launching")
            device_mod.launch_app(target, plan.bundle_id)

        return result
    finally:
        if options.keep_unpacked:
            say(f"Unpacked bundle left at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _resolve_device(needle):
    if needle:
        target = device_mod.find_device(needle)
    else:
        candidates = [d for d in device_mod.ios_devices() if d.is_ready]
        if not candidates:
            known = device_mod.ios_devices()
            detail = ", ".join(f"{d.name} ({d.status})" for d in known) or "none"
            raise DeviceError(
                "No connected iOS device",
                remedy="Connect and unlock the device, then retry. "
                       f"Known devices: {detail}",
            )
        if len(candidates) > 1:
            raise DeviceError(
                f"{len(candidates)} devices are connected",
                remedy="Name one with --device: "
                       + ", ".join(d.name for d in candidates),
            )
        target = candidates[0]

    if not target.is_ready:
        raise DeviceError(
            f"{target.name} is {target.status}",
            remedy="Reconnect over USB and unlock the device. A sleeping phone "
                   "reports as disconnected even while plugged in.",
        )
    return target


def _resolve_identity(needle):
    if needle:
        return keychain.find_identity(needle)
    candidates = keychain.development_identities()
    if not candidates:
        raise SideloadError(
            "No valid Apple Development identity in the keychain",
            remedy="Sign in to Xcode under Settings > Accounts and download the "
                   "manual signing certificates.",
        )
    if len(candidates) > 1:
        raise SideloadError(
            f"{len(candidates)} development identities available",
            remedy="Choose one with --identity: "
                   + ", ".join(i.label for i in candidates),
        )
    return candidates[0]


def _check_profile(prof, identity, target):
    if prof.is_expired:
        raise SideloadError(f"Profile expired on {prof.expires:%Y-%m-%d}",
                            remedy="Mint a new profile.")
    if identity.team_id and prof.team_id and identity.team_id != prof.team_id:
        raise SideloadError(
            f"Profile is for team {prof.team_id} but the certificate is team {identity.team_id}",
            remedy="Use a profile and certificate from the same team.",
        )
    if target is not None and prof.device_udids and not prof.covers_device(target.udid):
        raise SideloadError(
            f"{target.name} is not in the provisioning profile",
            remedy=f"Register {target.udid} with the team and re-mint the profile.",
        )


def _handle_conflicting_install(target, bundle_id, identity, options, say, ask):
    """iOS refuses to replace an app signed by a different team."""
    try:
        existing = device_mod.installed_apps(target).get(bundle_id)
    except DeviceError:
        return
    if not existing:
        return

    installed_team = existing.get("teamIdentifier") or ""
    if installed_team and identity.team_id and installed_team == identity.team_id:
        say(f"Replacing existing install of {bundle_id}")
        return

    say(f"{bundle_id} is already installed, signed by team {installed_team or 'unknown'}")
    if not options.uninstall_conflicting:
        raise SideloadError(
            f"{bundle_id} is installed under a different team",
            remedy="Delete the app from the device, or pass --uninstall-conflicting.",
        )
    if not ask(f"Uninstall the existing {bundle_id} (its data will be lost)?"):
        raise SideloadError("Cancelled: the existing install blocks this one")
    say("Uninstalling the conflicting build")
    device_mod.uninstall_app(target, bundle_id)


def _verify_installed(target, bundle_id, say):
    """Confirm from the device, rather than inferring from an exit code."""
    try:
        apps = device_mod.installed_apps(target)
    except DeviceError:
        say("Installed, but the device would not list apps to confirm it")
        return
    app = apps.get(bundle_id)
    if not app:
        raise SideloadError(
            f"devicectl reported success but {bundle_id} is not on the device",
            remedy="Check for an install error on the device itself.",
        )
    version = app.get("version") or app.get("bundleVersion") or "?"
    say(f"Confirmed on device: {bundle_id} version {version}")
