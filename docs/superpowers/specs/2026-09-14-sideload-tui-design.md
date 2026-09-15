# Sideloading IPAs without Sideloadly — design

_2026-09-14 · supersedes the Sideloadly dependency for this repo_

## Problem

Sideloadly is the only thing standing between a built IPA and the device. It is a GUI, it is not Apple-Silicon-native, and it cannot be driven from a script — so every install is a manual step, and the agent-side half of an investigation (build, install, capture, compare) has a human-shaped hole in the middle of it.

Every function Sideloadly performs has a native, scriptable equivalent already present on this machine.

## What Sideloadly actually does, and what replaces it

| Step | Replacement | Verified |
|---|---|---|
| Discover device, resolve UDID | `xcrun devicectl list devices --json-output` → `hardwareProperties.udid` | `nPhone` = `00008140-001478AE3C08801C`, iOS 27.0 |
| Signing certificate | Keychain identity | `Apple Development: Nico Jan`, `OU=3CY4DX3K45`, expires 2026-12-11 |
| Provisioning profile | ASC API / Xcode stub / BYO | none on disk — the only real gap |
| Re-sign | `codesign`, inside-out | main binary `cryptid 0`, arm64, re-signable |
| Install | `xcrun devicectl device install app <Payload/X.app>` | Xcode 26.6, devicectl 518.33 |

Team `3CY4DX3K45` is a paid Individual membership (`isFreeProvisioningTeam => false`). Profiles therefore last a year, not seven days, and the AltStore/AltServer/anisette machinery — which exists to work around free-account limits — is not needed.

## Architecture

A Python package `scripts/sideload/` with two front doors onto one pipeline: a Textual TUI for manual use, and a headless CLI for automation. Both call the same `pipeline.run()`; there is no path a human can take that a script cannot.

```
tui.py ─┐
        ├─→ pipeline.py ─→ profile_source → resign → device install → verify
cli.py ─┘                       │             │            │
                    asc.py / bootstrap /   resign.py    device.py
                    BYO profile
```

### Modules

- **`bootstrap_profile.py`** — generates a throwaway Xcode project and runs `xcodebuild -allowProvisioningUpdates` so Xcode mints a profile through the account already logged in. Requires no API key. For a paid team it returns the team wildcard profile, which is what makes the App Store Connect path optional rather than necessary.
- **`profile.py`** — decodes `.mobileprovision` (CMS, via `security cms -D`), and owns the entitlement policy.
- **`keychain.py`** — enumerates codesigning identities, parses subject/OU for team.
- **`device.py`** — `devicectl` wrapper. Always `--json-output` to a temp file and parsed; never scraped from table output.
- **`resign.py`** — unpack, strip, rewrite, sign, verify.
- **`pipeline.py`** — orchestration, emitting typed step events consumed by either front end.
- **`config.py`** — `~/.config/theta-sideload/config.toml`. Never in the repo; the `.p8` stays wherever the user put it.

## The entitlement problem

Instagram declares more than twenty entitlements: iCloud containers, three app groups, fifteen associated domains, App Clips, Sign in with Apple, hotspot configuration, production push. None are grantable to another team.

A wildcard App ID grants exactly four:

- `application-identifier` — `<TEAM>.<bundle-id>`
- `com.apple.developer.team-identifier`
- `get-task-allow` — true, for development
- `keychain-access-groups`

The policy is therefore an allowlist, computed as the intersection of what the app declares and what the profile grants, unioned with those four. Everything else is dropped. This is what Sideloadly does; the difference is that here it is written down.

**Consequences, which the TUI states before running:** push notifications, universal links, iCloud and the app groups stop working, and because the keychain access group changes, the login session resets.

## Bundle identifier: derived, not chosen

The profile decides, and the tool reads it rather than asking:

- **Wildcard App ID** (`<TEAM>.*`) — any bundle ID is signable, so `com.burbn.instagram` is preserved. A new install replaces the old one, which is the existing workflow.
- **Explicit App ID** — only that identifier is signable, so the app is rewritten to it. Patched and stock builds can then be installed side by side, which is better for A/B capture, at the cost of a second 315 MB app.

Offering this as a free checkbox would let the user pick an option the profile cannot honour.

## App extensions are removed by default

The IPA carries seven (`InstagramWidgetExtension`, `…LiveActivities`, `…LockScreenCameraControl`, `InstagramShareExtension`, `InstagramNotificationExtension`, `…ContentExtension`, `InstagramBroadcastSampleHandlerExtension`). They communicate through app groups that no longer exist, and under an explicit App ID each would need its own. Removing them also resolves the five widget-extension crash corpses seen in the first three seconds of the current build.

## Signing

Inside-out, never `codesign --deep`:

1. Nested frameworks and dylibs, deepest first — including the injected `Theta.dylib`
2. Remaining `.appex` bundles, each with its own `application-identifier`
3. The `.app` last, with the computed entitlements

Unpack with `ditto -x -k`, not `unzip`, which mangles framework symlinks. Each nested binary is verified individually; `--deep` is a verification convenience, not a signing mode.

## Failure modes handled explicitly

- **Device disconnected or locked.** `nPhone` moved from `available` to `disconnected` during research. Retry with a stated reason, never a silent hang.
- **Installed app signed by a different team.** iOS refuses the replacement. Detect, and uninstall first on confirmation.
- **Profile does not include the device.** Re-register and re-mint rather than failing at `codesign`.
- **Post-install verification.** Confirm the bundle ID and version actually on the device; do not infer success from a zero exit code.

## Security

The `.p8`, key ID and issuer ID live in `~/.config/theta-sideload/`, never in the repo. Generated JWTs and key material are redacted from all log output and from the TUI's log pane. A pre-commit gitleaks gate is live in this repo.

## Build order

1. **Phase 1** — `profile.py`, `keychain.py`, `device.py`, `resign.py`, `pipeline.py`, `cli.py`, with unit tests on entitlement computation, bundle-ID derivation, profile decoding and devicectl JSON parsing.
2. **Phase 2** — `bootstrap_profile.py`, then a real re-sign of `artifacts/Instagram_stock442_control.ipa`.
3. **Phase 3** — the Textual TUI over the finished pipeline.

## Revised during implementation

Two things in the plan above turned out to be wrong, both in the same direction.

**Xcode automatic signing produces a wildcard, not an explicit App ID.** The design assumed `bootstrap_profile.py` could only ever mint an explicit App ID, and that a wildcard — the thing that preserves `com.burbn.instagram` — required the App Store Connect API. In practice, a paid team's automatic signing returns `iOS Team Provisioning Profile: *`, App ID `3CY4DX3K45.*`, covering all 16 registered devices and valid for a year.

**So `asc.py` was not built.** Its entire value was minting wildcard profiles headlessly, and the bootstrap already does that with no API key, no `.p8` on disk, and no JWT to keep out of logs. Building it anyway would have added a network client and a credential-handling surface to do something already working. It remains straightforward to add if Xcode is ever unavailable, or if profiles are ever needed on a machine without it.

The consequence is that the one manual prerequisite in the original plan — generating an App Store Connect API key — is not needed at all.

The entitlement analysis held exactly: the minted profile grants precisely the four keys the design predicted, and the re-signed control IPA drops nineteen.

## Revised after the first device run (2026-09-14, evening)

The design was never exercised against a device — the phone read `disconnected` throughout the session that built it, and `devicectl` install/uninstall/verify were unit-tested against captured JSON only. The first real run found three defects, all in code the tests had covered, and disproved one stated consequence.

**A cold `tunnelState` is not an answer.** The design treated `tunnelState == "connected"` as the readiness test. `devicectl list devices` reports the tunnel state it last saw, and an idle phone drops its tunnel within a minute or two, so the tool refused a device that was fully reachable. Any real operation re-establishes it; `device.wake()` now does so before the readiness check is believed. The remedy text also insisted on USB at a device that had only ever been on the local network — wireless install works fine.

**A flag that asks for confirmation is not a flag.** `--uninstall-conflicting` still called a `confirm()` callback that returned `False` off a TTY, so it cancelled the very run it was meant to enable. Passing it is now the consent, as the TUI switch already was, and the callback is gone rather than left unused. `Options.uninstall_conflicting` also defaults to `False`, so a programmatic caller that does not ask can never delete an app.

**Not every directory ending in `.framework` is a bundle.** Theta's `ffmpeg.framework` is a bare container of nested `.framework` bundles with no Info.plist and no binary of its own. `signable_paths` tried to sign it and codesign aborted the run. Real bundles are signed; containers are descended into. A `SigningOrder` fixture had encoded the wrong behaviour by building bundles with no Info.plist — inputs that cannot exist on a device, which is exactly why the suite stayed green while a real IPA failed.

**The entitlement consequence was understated.** The design predicted that dropping the keychain access group would reset the login session. It does — but for stock Instagram the session cannot be re-established at all: the app aborts on Direct's account-session sync, reproducibly, under two independent signers. The entitlement *analysis* held exactly; the prediction of what losing those entitlements would cost did not. See `NOTES-local.md` → "The stock control is unusable".

The common thread: every one of these passed a test suite that never touched a device or a real IPA. The unit tests are still worth having, but they cannot stand in for one real install.
