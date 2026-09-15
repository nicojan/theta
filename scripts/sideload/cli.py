"""Headless front end. Everything the TUI can do, scriptably."""

import argparse
import os
import sys

from . import bootstrap_profile, config, device as device_mod, keychain
from . import profile as profile_mod
from . import pipeline
from .errors import SideloadError

# Where a Theta build lands, searched in order when --ipa is omitted.
DEFAULT_IPA_DIRS = ("output", "artifacts")


def _say(message):
    print(message, flush=True)


def _confirm(question):
    if not sys.stdin.isatty():
        return False
    reply = input(f"{question} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def find_ipas(root="."):
    """IPAs in the conventional build directories, newest first."""
    found = []
    for name in DEFAULT_IPA_DIRS:
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        for entry in os.listdir(directory):
            if entry.endswith(".ipa"):
                path = os.path.join(directory, entry)
                found.append((os.path.getmtime(path), path))
    return [path for _mtime, path in sorted(found, reverse=True)]


def cmd_devices(_args):
    devices = device_mod.list_devices()
    if not devices:
        _say("No devices known to devicectl.")
        return 0
    width = max(len(d.name) for d in devices)
    for entry in devices:
        _say(f"{entry.name:<{width}}  {entry.udid}  {entry.platform} {entry.os_version:<7} {entry.status}")
    return 0


def cmd_identities(_args):
    identities = keychain.list_identities()
    if not identities:
        _say("No codesigning identities in the login keychain.")
        return 0
    for identity in identities:
        expiry = f"expires {identity.expires:%Y-%m-%d}" if identity.expires else "expiry unknown"
        flag = " (EXPIRED)" if identity.is_expired else ""
        _say(f"{identity.sha1}  {identity.common_name}  team {identity.team_id or '?'}  {expiry}{flag}")
    return 0


def cmd_profile(args):
    prof = profile_mod.decode(args.path)
    _say(f"Name          {prof.name}")
    _say(f"UUID          {prof.uuid}")
    _say(f"Team          {prof.team_id}")
    _say(f"App ID        {prof.app_id}  ({'wildcard' if prof.is_wildcard else 'explicit'})")
    _say(f"Expires       {prof.expires:%Y-%m-%d}" if prof.expires else "Expires       unknown")
    _say(f"Devices       {len(prof.device_udids)}")
    _say("Entitlements  " + ", ".join(sorted(prof.entitlements)) or "none")
    return 0


def cmd_install(args):
    settings = config.load()
    ipa = args.ipa
    if not ipa:
        candidates = find_ipas()
        if not candidates:
            raise SideloadError("No IPA given and none found in output/ or artifacts/",
                                remedy="Pass --ipa explicitly.")
        ipa = candidates[0]
        _say(f"Using {ipa}")

    profile_path = args.profile or settings.get("profile")
    if not profile_path:
        raise SideloadError(
            "No provisioning profile",
            remedy="Pass --profile, or run `sideload bootstrap` to mint one through Xcode.",
        )

    options = pipeline.Options(
        ipa=ipa,
        device=args.device or settings.get("device", ""),
        identity=args.identity or settings.get("identity", ""),
        profile=profile_path,
        display_name=args.display_name or settings.get("display_name", ""),
        remove_extensions=not args.keep_extensions,
        get_task_allow=not args.no_debug,
        uninstall_conflicting=args.uninstall_conflicting,
        launch=args.launch,
        keep_unpacked=args.keep_unpacked,
        output_ipa=args.output or "",
    )

    result = pipeline.run(options, progress=_say, confirm=_confirm)

    _say("")
    _say(f"Installed {result.bundle_id} on {result.device_name}")
    if result.removed_extensions:
        _say(f"Removed {len(result.removed_extensions)} extensions")
    if result.dropped_entitlements:
        _say(f"Dropped {len(result.dropped_entitlements)} entitlements -- push, universal "
             "links, iCloud and app groups will not work, and the login session resets")

    if args.remember:
        settings.update({
            "device": result.device_name,
            "profile": profile_path,
            "identity": options.identity,
        })
        config.save(settings)
    return 0



def cmd_bootstrap(args):
    """Mint a profile through Xcode, for when there is no ASC API key yet."""
    settings = config.load()
    identity = keychain.find_identity(args.identity or settings.get("identity") or "Apple Development")
    if not identity.team_id:
        raise SideloadError("Could not read a team from the signing certificate",
                            remedy="Pass --team explicitly.")

    target = None
    if args.device or settings.get("device"):
        target = device_mod.find_device(args.device or settings["device"])
    else:
        ready = [d for d in device_mod.ios_devices() if d.is_ready]
        if len(ready) == 1:
            target = ready[0]

    path = bootstrap_profile.mint(
        team_id=args.team or identity.team_id,
        bundle_id=args.bundle_id,
        device=target,
        output_path=args.output or "",
        progress=_say,
    )
    _say(f"\nProfile written to {path}")

    settings["profile"] = path
    settings["identity"] = identity.sha1
    if target is not None:
        settings["device"] = target.name
    config.save(settings)
    _say(f"Saved as the default in {config.CONFIG_PATH}")
    return 0


def cmd_resign(args):
    """Re-sign without installing. Useful when no device is attached."""
    settings = config.load()
    ipa = args.ipa or (find_ipas() or [None])[0]
    if not ipa:
        raise SideloadError("No IPA given and none found in output/ or artifacts/",
                            remedy="Pass --ipa explicitly.")
    profile_path = args.profile or settings.get("profile")
    if not profile_path:
        raise SideloadError("No provisioning profile",
                            remedy="Run `sideload bootstrap` first.")

    options = pipeline.Options(
        ipa=ipa,
        identity=args.identity or settings.get("identity", ""),
        profile=profile_path,
        display_name=args.display_name or "",
        remove_extensions=not args.keep_extensions,
        get_task_allow=not args.no_debug,
        output_ipa=args.output or "",
        keep_unpacked=args.keep_unpacked,
        install=False,
    )
    result = pipeline.run(options, progress=_say)
    _say("")
    _say(f"Re-signed {result.bundle_id}")
    if result.output_ipa:
        _say(f"Wrote {result.output_ipa}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sideload",
        description="Sideload an IPA to a connected iOS device.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("devices", help="list devices devicectl can see").set_defaults(func=cmd_devices)
    sub.add_parser("identities", help="list codesigning identities").set_defaults(func=cmd_identities)

    profile_parser = sub.add_parser("profile", help="describe a .mobileprovision")
    profile_parser.add_argument("path")
    profile_parser.set_defaults(func=cmd_profile)

    install = sub.add_parser("install", help="re-sign and install an IPA")
    install.add_argument("--ipa", help="path to the IPA (defaults to the newest in output/ or artifacts/)")
    install.add_argument("--device", default="", help="device name, UDID or identifier")
    install.add_argument("--identity", default="", help="signing identity SHA-1, team or name fragment")
    install.add_argument("--profile", default="", help="path to a .mobileprovision")
    install.add_argument("--display-name", default="", help="override CFBundleDisplayName")
    install.add_argument("--keep-extensions", action="store_true",
                         help="keep app extensions (they need their own App IDs and app groups)")
    install.add_argument("--no-debug", action="store_true",
                         help="omit get-task-allow, preventing the debugger from attaching")
    install.add_argument("--uninstall-conflicting", action="store_true",
                         help="uninstall an existing install signed by another team")
    install.add_argument("--launch", action="store_true", help="launch the app after installing")
    install.add_argument("--keep-unpacked", action="store_true",
                         help="leave the unpacked bundle on disk for inspection")
    install.add_argument("--output", default="", help="also write the re-signed IPA here")
    install.add_argument("--remember", action="store_true",
                         help="save device, profile and identity as defaults")
    install.set_defaults(func=cmd_install)

    resign_parser = sub.add_parser("resign", help="re-sign an IPA without installing it")
    resign_parser.add_argument("--ipa")
    resign_parser.add_argument("--identity", default="")
    resign_parser.add_argument("--profile", default="")
    resign_parser.add_argument("--display-name", default="")
    resign_parser.add_argument("--keep-extensions", action="store_true")
    resign_parser.add_argument("--no-debug", action="store_true")
    resign_parser.add_argument("--keep-unpacked", action="store_true")
    resign_parser.add_argument("--output", default="", help="write the re-signed IPA here")
    resign_parser.set_defaults(func=cmd_resign)

    bootstrap = sub.add_parser(
        "bootstrap",
        help="mint a provisioning profile through Xcode (no API key needed)",
    )
    bootstrap.add_argument("--bundle-id", default=bootstrap_profile.DEFAULT_BUNDLE_ID,
                           help="identifier to register; must be one you own")
    bootstrap.add_argument("--team", default="", help="team ID (defaults to the certificate's)")
    bootstrap.add_argument("--device", default="", help="device to include in the profile")
    bootstrap.add_argument("--identity", default="", help="signing identity to read the team from")
    bootstrap.add_argument("--output", default="", help="where to write the .mobileprovision")
    bootstrap.set_defaults(func=cmd_bootstrap)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except SideloadError as exc:
        print(f"\nerror: {exc.message}", file=sys.stderr)
        if exc.remedy:
            print(f"       {exc.remedy}", file=sys.stderr)
        if exc.detail and os.environ.get("SIDELOAD_DEBUG"):
            print(f"\n{exc.detail}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
