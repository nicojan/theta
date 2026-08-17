# NOTES-local — Theta build on this machine

Local build notes for this fork. Not upstream documentation — `README.md` is the author's; this file records what it took to build on **macOS 26 / Darwin 25.6 with Xcode 26.6**, where the README's assumptions no longer hold.

Fork: `nicojan/theta` (`origin`) ← `objcmsgSend/theta` (`upstream`). Clone at `~/dev/theta`.

## Status

`./build.sh sideload` works. Current `output/Instagram_patched.ipa` (307 MB) is built against Instagram **442.0.0** (2026-08-15), injection verified (see [Verification](#verification)). An earlier 441.0.0 build was produced the same day; `build.sh` wipes `output/` on each run, so only the most recent IPA survives.

Runtime status: installed and run on device (iPhone 16 Pro Max, iOS 26.6). Theta loads and hooks install cleanly on 442; the repost freeze is fixed and confirmed on device (see [Repost freeze](#repost-freeze-infinite-layout-loop-in-toastdismiss)), as is the story overlay (see [Story overlay](#story-overlay-buttons-vanished-on-442)).

**Unverified in the current IPA** (dylib `FC670962`): the `ENABLED()` value cache, the download-button repositioning, the Messages-tab long-press, and the story *actions* behind the restored buttons — saving a story and marking one seen both ran through the same broken delegate path and have not been exercised since the fix. `.claude/HANDOFF.md` lists what would verify each.

dSYMs are kept in `symbols/` (gitignored), named by dylib UUID. `.theos` is overwritten on every build, so a shipped IPA is undiagnosable without its copy there.

## Environment

| Component | Value |
|---|---|
| macOS | Darwin 25.6.0 |
| Xcode | 26.6 (build 17F113) |
| Toolchain clang | Apple clang 21.0.0 |
| Theos | `~/theos` — `export THEOS=/Users/nicojan/theos` |
| SDK | `$THEOS/sdks/iPhoneOS14.5.sdk` (from `sdks/iPhoneOS14.5.sdk.tar.xz` in this repo) |
| Python | 3.14.7 |

`THEOS` is not set in the shell profile. Export it per-session, or the build fails immediately.

## Setup performed

```sh
git clone --recursive https://github.com/theos/theos.git ~/theos
export THEOS=/Users/nicojan/theos
mkdir -p "$THEOS/sdks"
tar -xJf sdks/iPhoneOS14.5.sdk.tar.xz -C "$THEOS/sdks"
```

Then the two deviations below, then `cc -O2 -o tools/insert_dylib tools/insert_dylib.c` (build.sh does this itself; it compiles clean).

`dpkg-deb` is **not** installed and is not needed — Theos falls back to its bundled `dm.pl`, which builds the `.deb` fine.

`third_party/CydiaSubstrate.framework` is already tracked in this repo with a working arm64 binary. `scripts/extract-substrate-from-deb.py` does **not** need to be run; it downloads `mobilesubstrate_0.9.6301` from apt.saurik.com and writes byte-identical content. The build's Substrate search finds the committed copy on its own.

## Deviations from README

Both were required to compile at all. Both are untested by the author, and both are the first place to look if the tweak misbehaves at runtime.

### 1. Toolchain is not Xcode 14

`Makefile:9` hard-codes `PREFIX = $(THEOS)/toolchain/Xcode14.xctoolchain/usr/bin/`. Xcode 14 cannot be installed on Darwin 25. Symlinked the current toolchain into that path:

```sh
ln -sfn /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain \
        "$THEOS/toolchain/Xcode14.xctoolchain"
```

So the build runs on **Apple clang 21**, not the Xcode 14 clang the author tested against.

### 2. libc++ headers grafted into the 14.5 SDK

With clang 21, the build failed at `simd/math.h:1403: 'cmath' file not found`. The patched 14.5 SDK ships no C++ headers; Xcode 14's clang tolerated that, clang 21 does not. Copied them in from the current iOS SDK:

```sh
cp -R /Applications/Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS26.5.sdk/usr/include/c++ \
      "$THEOS/sdks/iPhoneOS14.5.sdk/usr/include/c++"
```

Modern libc++ headers against a 14.5 SDK. Only reached via AVFoundation's `simd` module, so the blast radius looks small — but it is a graft, not a fix.

Note this lives in `$THEOS/sdks/`, outside the repo. **Re-unpacking the SDK tarball wipes it** and the build breaks again with the same `cmath` error.

## Build procedure

```sh
export THEOS=/Users/nicojan/theos
# decrypted IPA unpacked so the binary is at input/Payload/Instagram.app/Instagram
./build.sh sideload      # → output/Instagram_patched.ipa
```

The decrypted IPA is supplied by the operator; obtaining one is out of scope for these notes. Verify before building — the app must be genuinely decrypted or the injected tweak will not load:

```sh
otool -l input/Payload/Instagram.app/Instagram | grep -A4 LC_ENCRYPTION_INFO   # cryptid must be 0
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' input/Payload/Instagram.app/Info.plist
```

Source used: `com.burbn.instagram` 441.0.0, arm64, `cryptid 0` — matches the README's tested version.

Install the output with Sideloadly / AltStore / SideStore (re-signs with an Apple ID), or TrollStore directly.

## Verification

A clean exit from `build.sh` is not evidence the injection landed. Checks that were actually run against `output/Payload/Instagram.app`:

```sh
otool -l Instagram | grep -A2 'Theta.dylib'          # → @executable_path/Theta.dylib load command
otool -D Theta.dylib                                  # → @executable_path/Theta.dylib
otool -L Theta.dylib | grep -i substrate              # → @executable_path/CydiaSubstrate.framework/... (weak)
otool -D CydiaSubstrate.framework/CydiaSubstrate      # → @executable_path/... , arm64 present
codesign -dv Instagram                                # → adhoc (original signature stripped)
ls -d ffmpeg.framework ThetaResources.bundle          # → both embedded
```

All passed. The `LC_LOAD_DYLIB` check is the one that matters — everything else can look right while the dylib is never loaded.

## Instagram 442.0.0

Built against **442.0.0** on 2026-08-15 (`com.burbn.instagram-442.0.0-Decrypted.ipa`). **No source changes were needed.**

The ObjC surface Theta touches was diffed 441 → 442 by parsing `__objc_classlist` in every Mach-O in both bundles (main binary + 8 appex + frameworks; ~45,000 classes, ~217,000 ivars, ~312,000 methods each) and resolving Theta's plain class names against Swift-mangled ObjC names (`_TtC<n><module><n><class>`). Of the 362 classes / selectors / ivars Theta references and that exist in 441, exactly one is gone in 442:

| Symbol | Used at | Impact |
| --- | --- | --- |
| `IGSundialViewerNavigationBarOld` | `Source/Hooks/UI/HideCreateButton.m:93` | None. It is the *second* entry in a `ThetaFirstClass` fallback list; the primary `_TtC33IGSundialViewerNavigationBarSwift28IGSundialViewerNavigationBar` is present in both versions. Instagram simply deleted the legacy class. The dead fallback can be removed whenever. |

Two caveats on the method. Selector and ivar presence was checked **globally** (does this name exist on any class), not per-class, so a member that *moved* between classes would not be flagged. And a class resolving does not prove its internals are unchanged — layout and behaviour can drift without renaming. So this rules out the loud failure mode (hooks silently no-op because a name vanished); it does not substitute for running the thing.

Also worth recording: `_repostView`, `_lazyRepostCountButton`, `IGMedia`, `IGVideo`, `IGBadgeButton` and ~150 other identifiers Theta references **do not exist in 441 either**. Those code paths are already dead and have nothing to do with 442.

To repeat this for the next version bump:

```sh
./scripts/compat.py <old-Instagram.app> <new-Instagram.app>
# e.g. ./scripts/compat.py /tmp/ig442/Payload/Instagram.app input/Payload/Instagram.app
```

It scrapes the identifiers out of `Source/` and `Include/` itself, so it needs no arguments beyond the two bundles, and exits non-zero if anything broke. `scripts/objcdump.py` is the underlying Mach-O parser — it decodes `DYLD_CHAINED_PTR_64` rebases, which is why a naive pointer read returns zero classes on these binaries.

Note that `strings(1)` is **not** a substitute: on this binary its `-a` and whole-file modes disagree with each other, and both miss Swift class names entirely. That route reports `IGSundialViewerVerticalUFI` as a plain class when it is really `_TtC26IGSundialViewerVerticalUFI26IGSundialViewerVerticalUFI`.

## Repost freeze: infinite layout loop in ToastDismiss

Tapping repost froze the entire UI while the reel kept playing. Root cause is `Source/Hooks/Behavior/ToastDismiss.m`, and it is **not** version-specific — 441 is affected identically.

`hook_toastView_layoutSubviews` hooks `IGActionableConfirmationToastView` (present in both 441 and 442) — that class is the *"Reposted · Undo"* confirmation toast. Inside `layoutSubviews` it set `v.transform` from a position measured with `convertPoint:toView:`. That measurement already includes the transform applied on the previous pass, so the value never converges; it oscillates between `50 − T` and `0`, and every assignment dirties layout again:

| Pass | measured top | translation applied |
| --- | --- | --- |
| 1 | `T` | `50 − T` |
| 2 | `50` | `0` |
| 3 | `T` | `50 − T` |

The fix backs the applied offset out of the measurement so the target is stable, and skips the assignment when it moves less than 0.5pt.

Two aggravating factors worth remembering. The hook has **no `ENABLED()` gate**, so it ran for every user on every actionable toast — any confirm-with-Undo action could trigger it, not just repost. And the symptom is easy to misread: the main thread is *spinning*, not blocked, but the loop logs nothing, so syslog goes completely silent and looks like a deadlock.

### How it was found

The device log pinned the onset to 14:27:22.53 (last line, then nothing but `AudioQueueGetCurrentTime` at a metronomic ~82 lines/sec) but could not explain it. What actually cracked it was `crashes_and_spins/Instagram.cpu_resource-*.ips` inside a sysdiagnose — a CPU-limit report carrying microstackshot backtraces, with `Footprint: 9680 KB -> 1969.61 MB`. That memory growth is why the process disappeared mid-investigation: it was jetsammed, not force-quit.

The stack contained an unnamed image whose UUID matched `Theta.dylib` exactly. Symbolicating against the build's own dSYM named the frame:

```sh
# UUID must match between the report, the shipped dylib, and the dSYM
dwarfdump --uuid output/Payload/Instagram.app/Theta.dylib
xcrun atos -o .theos/obj/arm64/Theta.dylib.dSYM/Contents/Resources/DWARF/Theta.dylib \
           -arch arm64 -l <load-address> <frame-address>
# -> hook_toastView_layoutSubviews (ToastDismiss.m)
```

`.theos/obj/arm64/Theta.dylib.dSYM` is generated on every build and the shipped dylib is stripped, so **symbolication only works against the dSYM from the same build**. Keep it if an IPA is handed to someone else to test.

## Related: download button was mispositioned on 442

Separate defect, found while investigating, **fixed but not yet verified on device**. Theta's download button is added with `addSubview:` (so it is topmost and wins hit-testing) and was constrained a fixed distance above the like button. On 442 the slot directly above like is the repost control, so the button overlapped it and, with `showsMenuAsPrimaryAction = YES`, swallowed taps meant for repost.

Anchoring to a different hard-coded neighbour would break again on the next IG release, so `theta_repositionDownloadButtonClearOfSiblings` now detects an actual frame collision after layout and lifts the button clear. It settles in one step (clearing the collision ends the intersection) and carries the same epsilon guard as the toast fix, for the same reason.

## Story overlay buttons vanished on 442

Every Theta control in the story viewer — download, seen, local-seen, mentions — was missing, and every story feature behind them (mark-as-seen, download-all, mentions) was silently dead. **Fixed and confirmed on device** (dylib `FC670962`).

`StoryGhost.m` reached the story's view model positionally: `cell.containerView.delegate` was assumed to *be* the `IGStoryFullscreenSectionController`, and everything hung off that — `viewModel` → `owner` → the buttons, plus `currentStoryItem` for the actions. On 442 that delegate is `IGStoryGestureNuxDismissHandler`, a Swift class with no properties at all, so `setupButtons` returned before reading a single toggle. Both buttons disappearing at once was the tell: they are gated on separate settings, so a shared bail-out was the only explanation.

The fix stops trusting position. `thetaStoryValidatedSectionController` accepts a candidate only if it answers `viewModel` or `currentStoryItem`; `thetaStoryViewModelFromCell` and `thetaStoryCurrentItemFromCell` try 442's `IGStoryItemContext` on the cell first (it carries `storyItem`, `viewModel` and `sectionContext` directly), then the validated section controller, then the viewer's `currentViewModel`. The old delegate paths stay ahead of the new one so older IG builds resolve unchanged, and the four hand-rolled copies of the walk now share these resolvers.

Diagnostics stayed in: `[Theta] StoryOverlay: …` logs one line per reason per 5s, including `building overlay — buttons=N save=… ghost=…` on success. Use `%{public}s` for anything you need to read back — os_log redacts `%@` as `<private>`, which cost a build round-trip here.

## Install with "Remove app extensions"

The IPA ships 7 app extensions. Sideloadly re-signs them, and on device they fail signature validation and crash-loop: `EXC_BAD_ACCESS`, `"namespace":"CODESIGNING","indicator":"Invalid Page"` — 18 crashes of `InstagramWidgetExtensionLockScreenCameraControl` alone in one afternoon, often in pairs seconds apart.

**Theta is injected into the main binary only** — `otool -L` shows zero Theta references in all 7 appex — so nothing in Theta needs them. Tick Sideloadly's "Remove app extensions" at install time. It stops the crash-relaunch churn (a small but real battery/thermal cost) and is required anyway on a free Apple ID, which caps at 3.

## Performance baseline (442, post-toast-fix)

Measured 2026-08-15 after ~4 minutes of normal use, via `spindump-nosymbols.txt` in a sysdiagnose:

| Metric | Value | Read |
| --- | --- | --- |
| CPU time | 0.427s over a 2s window, 77 threads | ~21% of one core — normal for video playback |
| Main thread | 0.179s, blocked in `mach_msg` in all 8 samples | idle in the runloop, healthy |
| Footprint | 517 MB | normal (cf. 1969 MB during the layout loop) |
| Theta frames in spindump | **zero** | not on any thread's stack |
| New `cpu_resource` reports | none | no sustained CPU abuse |

So Theta is not a meaningful CPU cost in steady state. A hot phone here is Instagram's own video decode, networking and `com.facebook.analytics` queues.

One inefficiency was found and since fixed, on principle rather than for heat: `ENABLED()` built a key with `stringWithFormat:` and hit `NSUserDefaults` on **every** call, from render-path predicates. An unfiltered capture logged 997 Theta preference reads in 35 seconds — 42% of all CFPrefs traffic in the process — with `Enable Liquid Glass Surfaces_Enabled` read 637 times (~18/sec). CFPrefs caches, so that was a floor on the real call count. It never showed up in CPU samples, so it was never the heat. `ThetaSettingEnabled()` in `THGlobalsAndHooking.m` now caches the value and flushes on defaults change and on app foreground.

Note when re-measuring: the numbers above came from an **unfiltered** capture. `theta-log.sh` without `--all` filters to Instagram/Theta process matches and drops most of the CoreFoundation debug lines, so preference volume is not comparable between the two modes. A filtered capture showing few reads proves nothing.

## Capturing device logs

`./scripts/theta-log.sh` captures iPhone syslog to `logs/theta-<timestamp>.log` while a bug is reproduced. `--all` for freezes (unfiltered), `--list` to check what the Mac can see.

Requires `brew install libimobiledevice` (installed on this machine 2026-08-15) and the iPhone connected **over USB and unlocked**. Network pairing is not enough — `idevicesyslog -n` fails with `Could not connect to lockdownd: -8` even when `idevice_id -n` lists the device.

Two dead ends, so they are not retried: `log stream --device` was **removed in macOS 26** (the flag is unrecognised), and `xcrun devicectl` has no console/syslog subcommand. Console.app remains the zero-install fallback and the script prints those steps when it cannot find a device.

## Known issues

- **Navigation settings are broken on sideload** — tab icon order, swipe between tabs, launch tab, hide feed/explore/reels/messages tab, Messenger mode. Upstream issue, documented in `README.md`; not caused by anything here. Jailbreak builds are unaffected.
- **Runtime unverified.** The IPA has never been installed. A launch crash or odd AVFoundation behavior points at the two deviations above before it points at tweak logic.

## Disk

`input/Payload` and `output/Payload` are ~566 MB each and both gitignored. Safe to delete once an IPA is confirmed installed; `build.sh` recreates `output/` and `input/` is re-staged from the source IPA.

## Scope note

Several Theta features act on other users' expectations rather than the local client: `StoryGhost` and `StorySeenLocalOnly` (view stories without appearing in the viewer list), `KeepDeletedMessages` (retain DMs the sender deleted), `HideTypingIndicator`, `ScreenshotSuppression` (defeats the screenshot notice on disappearing media), and `ProfileAnalyzer` (tracks follower/following changes on watched accounts). Recorded here so the contents of the build are not a surprise later — they are individually toggleable in Theta's settings.
