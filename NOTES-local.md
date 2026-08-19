# NOTES-local — Theta build on this machine

Local build notes for this fork. Not upstream documentation — `README.md` is the author's; this file records what it took to build on **macOS 26 / Darwin 25.6 with Xcode 26.6**, where the README's assumptions no longer hold.

Fork: `nicojan/theta` (`origin`) ← `objcmsgSend/theta` (`upstream`). Clone at `~/dev/theta`.

## Status

`./build.sh sideload` works. Current `output/Instagram_patched.ipa` (307 MB) is built against Instagram **442.0.0** (2026-08-15), injection verified (see [Verification](#verification)). An earlier 441.0.0 build was produced the same day; `build.sh` wipes `output/` on each run, so only the most recent IPA survives.

Runtime status: installed and run on device (iPhone 16 Pro Max, iOS 26.6). Theta loads and hooks install cleanly on 442. Fixed **and confirmed on device**: the repost freeze (see [Repost freeze](#repost-freeze-infinite-layout-loop-in-toastdismiss)), the story overlay (see [Story overlay](#story-overlay-buttons-vanished-on-442)), the story-download crash and the **whole VP9 → FFmpeg transcode path** including the `@loader_path` rewrite (see [Story video download](#story-video-download-vp9-source-three-separate-faults)), photo *and* video story saves, and both manual mark-as-seen **and Skip On Seen** (see [Story seen state](#story-seen-state-mark-and-skip-on-442)). H.264 (`avc1`) story video saves successfully.

**Unverified in the current IPA** (dylib `AAF59D9E`, built 2026-08-19): the `ENABLED()` value cache, the download-button repositioning, the Messages-tab long-press, and the `prepareForReuse` hook meant to stop the overlay going missing on a recycled cell. Also untested: the eye button's long-press menu, and which of the three skip routes actually fires — see [Story seen state](#story-seen-state-mark-and-skip-on-442). [Testing the current IPA](#testing-the-current-ipa) is the checklist; `.claude/HANDOFF.md` carries the same list with the next action.

dSYMs are kept in `symbols/` (gitignored), named by dylib UUID. `.theos` is overwritten on every build, so a shipped IPA is undiagnosable without its copy there.

## Testing the current IPA

Install `output/Instagram_patched_noplugins.ipa` with Sideloadly, ticking **Remove app extensions** (see [Install](#install-with-remove-app-extensions)). Then start a capture *before* reproducing, because the interesting lines are gone by the time a toast appears:

```sh
./scripts/theta-log.sh --all      # Ctrl-C when done; writes logs/theta-<timestamp>.log
```

Each check below names the log line that decides it. Grep the capture with `grep -a` — the file has binary stretches and plain `grep` will call it binary and print nothing.

| # | Do this | Pass looks like | Fail looks like |
| --- | --- | --- | --- |
| 1 | Download a **video** story | `SaveVideo: source codec=vp09 ffmpeg=1`, then no `AV1Transcoder: dlopen` line at all, then a file in the camera roll | any `AV1Transcoder: dlopen … failed:` line — read the reason, it is now public. **Passed 2026-08-19** |
| 2 | Download a **photo** story | `StoryDownload: media=… mediaType=1 image=1 …` followed by `StorySave: downloading …` and either `StorySave: Photos import success=1` or `StorySave: local-folder save … moved=1` | a `StoryDownload:` line whose branch flags are all 0, or a `StorySave:` failure reason. **Passed 2026-08-19** |
| 3 | Download an H.264 story or reel | `source codec=avc1 ffmpeg=0` and a saved file | regression — this path already worked on 2026-08-18 |
| 4 | Repeat 1–3 a few times | no `Corpse allowed` and no `Terminating app due to uncaught exception` anywhere in the capture | the crash is back; symbolicate against `symbols/<UUID>.dSYM` |
| 5 | Open the first story of a tray, back out, reopen | `StoryOverlay: building overlay — buttons=4` each time | buttons missing on the recycled cell — the `prepareForReuse` hook |
| 6 | Long-press the Messages tab | Theta settings open | nothing happens |
| 7 | Tap the **eye** button on a story | `StorySeen: mark-current item=IGStoryItem via=item-context` then `mark-current ok=1` | `via=(none)` — no route found the current item; `ok=0` — the viewer rejected the mark. **Passed 2026-08-19** |
| 8 | With **Skip On Seen** on, tap the eye | `StorySkip: advanced via …` naming a route, and the story advances | `StorySkip: no advance route — section=…` — the class it names is the one to add a route for. **Passed 2026-08-19** (the story advanced; no capture was running, so which route fired is unrecorded) |
| 9 | Long-press the **eye** button | `StorySeen: long-press fired, presenting menu`, then the menu appears | no line at all = the gesture never fired; line but no menu = the alert failed to present |

If a save reports **"Saved to local folder"** rather than the camera roll, that is not a bug: **Settings → Save Method** is set to `Folder`. Those files land in `AudioNotes` inside Instagram's own Documents container, reachable only from the pink **folder icon in the top bar of Theta's settings** — not from the Files app. Switch Save Method to `Camera Roll` for camera-roll saves.

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

## Packaging for install

`build.sh sideload` produces `output/Instagram_patched.ipa` with all 7 app extensions. What actually gets installed on this machine is a copy with the extensions stripped, because a free Apple ID caps at 3 (see [Install](#install-with-remove-app-extensions)). There is no script for it; it is one command, run from `output/` after a build:

```sh
cd output && zip -qry Instagram_patched_noplugins.ipa Payload -x 'Payload/Instagram.app/PlugIns/*'
```

`-y` matters — it stores symlinks as symlinks, which the embedded frameworks rely on.

Copy the dSYM out on every build you intend to install, or a crash report from that IPA cannot be symbolicated. `.theos` is overwritten by the next build:

```sh
U=$(dwarfdump --uuid output/Payload/Instagram.app/Theta.dylib | awk '{print $2}')
mkdir -p "symbols/$U.dSYM" && cp -R .theos/obj/arm64/Theta.dylib.dSYM/ "symbols/$U.dSYM/"
```

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

## Story video download: VP9 source, three separate faults

Downloading a video story ended in "Could not prepare video for saving", and then killed the app. It was three independent bugs stacked, found over four device captures on 2026-08-17/18. Two are fixed and confirmed; the third is fixed but unverified.

### 1. The source is VP9, not AV1 (fixed, confirmed)

`[Theta] SaveVideo: source codec=vp09` — Instagram serves story video as VP9. `SavePosts.m` had a pre-check that diverted AV1 to FFmpeg before the AVFoundation merge, but it tested only `'av01'`, so VP9 went down the merge path. No `AVAssetExportSession` preset can write VP9, which is why the export and then all nine presets failed with `AVFoundationErrorDomain -11838` (`NSOSStatusErrorDomain -16976` underneath) in well under a millisecond each. Failing that fast and that uniformly was the tell: AVFoundation was refusing the source, not disagreeing about a preset.

`ThetaCodecRequiresFFmpegTranscode()` in `ThetaDashManifest.m` now answers for `av01`, `vp08` and `vp09`, and the three codec-detection sites (`SavePosts.m` ×2, `MediaSelectionViewController.m`) all route through it. The FourCC is logged at detection — it used to be computed and thrown away, which is why this went unseen for so long.

### 2. The crash was `setOutputFileType:`, not the completion handler (fixed, confirmed)

The app died ~70 ms after the last preset failed. The exception, caught in an unfiltered capture 1.5 ms before the corpse:

```
*** Terminating app due to uncaught exception 'NSInvalidArgumentException',
    reason: '*** -[AVAssetExportSession setOutputFileType:] Invalid output file type'
```

`exportPresetsCompatibleWithAsset:` returns presets compatible with the *asset*, and some of those cannot write MP4 at all (`AVAssetExportPresetAppleM4A` is audio-only). `-setOutputFileType:` **raises** for those rather than failing softly, so the tenth preset in the list terminated the app. `ThetaExportSessionSupportsMPEG4()` now asks `determineCompatibleFileTypesWithCompletionHandler:` (falling back to `supportedFileTypes`) and skips any preset that cannot write MP4.

Confirmed on device: six download attempts across two builds, 16 presets skipped per attempt, no corpse. Note that the earlier theory — that the crash came from nesting the recovery inside the `AVAssetExportSession` completion handler — was **wrong**. The crash was one frame earlier, inside `ThetaExportPhotosCompatibleMP4`. The move to a background queue is still in the tree and still worth having, but it fixed nothing on its own.

### 3. ffmpeg bound to Instagram's libavutil (fixed, confirmed 2026-08-19)

With VP9 correctly routed to FFmpeg, the transcode still failed instantly:

```
[Theta] AV1Transcoder: dlopen libavcodec failed: dlopen(…/ffmpeg.framework/libavcodec.framework/libavcodec, 0x0009):
    Symbol not found: _av_image_copy_plane
```

Instagram ships **its own** `libavutil.framework` and `libavcodec.framework` under `Instagram.app/Frameworks`, with the *same* `@rpath` install names as the ffmpeg build Theta embeds at the bundle root. Our `libavcodec` asked dyld for `@rpath/libavutil.framework/libavutil` and got Instagram's copy, which does not export `av_image_copy_plane`:

```sh
nm -gU output/Payload/Instagram.app/Frameworks/libavutil.framework/libavutil | grep -c av_image_copy_plane   # 0
nm -gU output/Payload/Instagram.app/ffmpeg.framework/libavutil.framework/libavutil | grep -c av_image_copy_plane   # 2
```

Load order could not fix a name collision, so `build.sh` now rewrites our copy after embedding it: `rewrite_ffmpeg_install_names` sets each library's id and each inter-library dependency to `@loader_path/../<lib>.framework/<lib>`, which resolves relative to the library's own directory inside `ffmpeg.framework` and cannot reach Instagram's. The build fails loudly if any `@rpath` ffmpeg reference survives. Verify on a fresh build with:

```sh
otool -L output/Payload/Instagram.app/ffmpeg.framework/libavcodec.framework/libavcodec | grep -E 'loader_path|@rpath'
```

Two smaller faults were fixed alongside it. `AV1Transcoder` never loaded **libswresample**, which `libavcodec` links against and which Instagram's `Frameworks` dir does not contain — it is now in the load chain, right after libavutil. And every loader error called `dlerror()` twice, once for the log and once for the `NSError`; the second call returns `NULL`, so the error text was always `(null)`. All five loads now go through one helper that reads it once and logs `%{public}s`.

### Resolved: photo stories were saving all along (2026-08-19)

A photo story logged `StoryOverlay: tap reached download` and then nothing, which read as a dead button. It was in fact saving correctly — into `Documents/AudioNotes`, because **Save Method** was set to `Folder`. Two things hid it. The local-folder branch of `thetaStorySaveURL` logged nothing whatsoever, and its only confirmation toast sits behind `Show Banners`, which was off; `showCompletionToast` in `SavePosts.m` is *not* gated, which is why video saves showed a toast and photo saves did not. That branch now logs the branch taken, the filename and the move result.

It also had a real latent bug: the move error was passed as `nil`, so a *failed* move still showed the success toast. Fixed.

Diagnostic lesson worth keeping: an unlogged success and a silent failure are indistinguishable from the outside, and here the log's *silence* was the whole clue — `StorySave: downloading` followed 75 ms later by a `Save Method_SegmentIndex` read proved the completion handler ran to the branch, without a single line from the branch itself.

Sideloaded-app crash reports are **not** reachable via `idevicecrashreport`; corpse reports land in the osanalytics `DiagnosticReports` directory. Get them from the device: Settings → Privacy & Security → Analytics & Improvements → Analytics Data. In the end none were needed here — an unfiltered `theta-log.sh --all` capture carried the `Terminating app due to uncaught exception` line with the full reason, which was faster than chasing the `.ips`.

## Story seen state: mark and skip on 442

Two separate faults behind one symptom — the eye button "did nothing". Both were found by capturing a tap, not by reading code: every Theta overlay button logs `StoryOverlay: tap reached <name>` unconditionally, so `tap reached seen` with nothing after it localised the bug to the handler immediately.

### 1. Manual mark resolved the story item through a dead delegate path (fixed, confirmed)

`seenButtonPressedCurrent` reached the current story item via `thetaStorySectionControllerFromCell` → `currentStoryItem`, then bailed if the section controller was nil. On 442 `containerView.delegate` is an `IGStoryGestureNuxDismissHandler`, which answers neither `viewModel` nor `currentStoryItem`, so that resolution returned nil and the function returned without logging a word. The download button had already been fixed for this by routing through `currentStoryItemContext`; the seen buttons never got the same treatment.

The mark now goes through `thetaStoryCurrentItemFromCell` first — the item-context route already proven on 442 — falling back to section, then viewer, and logs which one won:

```
[Theta] StorySeen: mark-current item=IGStoryItem via=item-context section=0
[Theta] StorySeen: mark-current ok=1
```

The section controller is only needed for Skip On Seen, so it no longer gates the mark itself.

### 2. `Skip On Seen` called a selector that no longer exists (fixed, confirmed 2026-08-19)

`thetaStorySkipIfEnabled` guarded on `-fullscreenOverlayDidTapNextStoryButton:` and returned when it was absent. It is absent from **all** of 442 — zero implementors across 43,736 classes and 312,772 methods:

```sh
python3 scripts/objcdump.py input/Payload/Instagram.app/Instagram /tmp/ig442.json
python3 -c "import json; c=json.load(open('/tmp/ig442.json')); \
  print([k for k,v in c.items() if 'fullscreenOverlayDidTapNextStoryButton:' in v['methods']])"
# []
```

So Skip On Seen has been dead since the 442 upgrade, independently of the mark bug. `scripts/compat.py` is built to catch exactly this class of breakage and would have flagged it against a 441 bundle — worth running on every IG bump.

What 442 offers instead:

| Route | Owner | Notes |
| --- | --- | --- |
| `_nextStoryButtonTapped` | `IGStoryFullscreenOverlayView` | No argument — IG's own next-story handler, reachable via the cell's `overlayView` |
| `advanceToNextItemWithNavigationAction:` | `IGStoryFullscreenSectionController` | Takes a navigation-action enum whose values are **not** recoverable from the binary's metadata |

`thetaStorySkipIfEnabledForCell` tries the legacy selector, then `_nextStoryButtonTapped`, then `advanceToNextItemWithNavigationAction:` with `0`, and logs which fired. `_nextStoryButtonTapped` is preferred over the section-controller call precisely because it takes no argument and so involves no guessed enum value. If every route misses, the log names the class that was actually there:

```
[Theta] StorySkip: no advance route — section=IGStoryGestureNuxDismissHandler overlay=1
```

The helper also resolves the section controller itself when its caller passes nil — without that, fix 1 (which deliberately stopped gating on the section controller) would leave skip with no target.

Confirmed working on device 2026-08-19: tapping the eye with Skip On Seen enabled marks the story and advances. **Which route fired is not recorded** — no capture was running for that test. This still matters, because route 3 passes `0` for a navigation-action enum whose values are not in 442's metadata: if routes 1 and 2 are what fire, that guess has never actually executed. One `theta-log.sh --all` capture of a single eye tap closes the question, and the `StorySkip: advanced via …` line names the route outright.

### 3. `shouldBeSeen` latched on, silently defeating Story Ghost (fixed, confirmed live)

`shouldBeSeen` is a global in `THGlobalsAndHooking.m` that tells `hook_storyGhost2` to let one real seen receipt through. `seenButtonPressedCurrent` and `seenButtonPressedAll` set it `true` and relied on the hook to reset it — but while fault 1 was live no mark ever reached the hook, so it stayed `true` from the first tap onward and **every passively-viewed story sent a real receipt**, which is the exact opposite of what Story Ghost promises.

Both handlers now save and restore it around the call, as `thetaLocalSeenMarkCurrent` always did. One missing hook produced both a dead-looking button and a privacy leak; the dead button is what got reported.

## Reading device logs

Anything you need to read back from a device capture must use `os_log` with `%{public}s`. `NSLog`'s `%@` and plain `%s` are redacted to `<private>`, which silently hides exactly the class names and error descriptions worth capturing — it cost two build-install-capture cycles here before it was spotted.

Do not pipe `./build.sh` into `head`: the early pipe close kills the build with SIGPIPE partway through, leaving `output/` empty with no error message. Redirect to a file and grep that.

`build.sh` clears `output/` at the start of a sideload build, and that step loses a race with Finder recreating `.DS_Store` — it fails with `rm: cannot remove '…/output': Directory not empty` after having already deleted most of the tree. It happened twice on 2026-08-18. Re-running the build is enough; `find output -name .DS_Store -delete` first makes it less likely.

Grep captures with `grep -a`. The syslog files contain binary stretches, and without `-a` grep reports "binary file matches" and prints nothing — which reads exactly like the log not containing the line.

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
- **Photo stories save nothing.** Instrumented on 2026-08-18 but not yet diagnosed — see [Story video download](#story-video-download-vp9-source-three-separate-faults).
- **Toolchain deviations are still unproven.** Builds since 2026-08-15 install and run, so the Xcode-14 symlink and the grafted libc++ headers are not obviously harmful — but a launch crash or odd AVFoundation behaviour still points at [those two deviations](#deviations-from-readme) before it points at tweak logic.

## Disk

`input/Payload` and `output/Payload` are ~566 MB each and both gitignored. Safe to delete once an IPA is confirmed installed; `build.sh` recreates `output/` and `input/` is re-staged from the source IPA.

## Scope note

Several Theta features act on other users' expectations rather than the local client: `StoryGhost` and `StorySeenLocalOnly` (view stories without appearing in the viewer list), `KeepDeletedMessages` (retain DMs the sender deleted), `HideTypingIndicator`, `ScreenshotSuppression` (defeats the screenshot notice on disappearing media), and `ProfileAnalyzer` (tracks follower/following changes on watched accounts). Recorded here so the contents of the build are not a surprise later — they are individually toggleable in Theta's settings.
