# NOTES-local — Theta build on this machine

Local build notes for this fork. Not upstream documentation — `README.md` is the author's; this file records what it took to build on **macOS 26 / Darwin 25.6 with Xcode 26.6**, where the README's assumptions no longer hold.

Fork: `nicojan/theta` (`origin`) ← `objcmsgSend/theta` (`upstream`). Clone at `~/dev/theta`.

## Status

`./build.sh sideload` works. Produced `output/Instagram_patched.ipa` (310 MB) against Instagram **441.0.0** on 2026-08-15, injection verified (see [Verification](#verification)). **Not yet installed or run on a device** — nothing below is confirmed at runtime.

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

## Known issues

- **Navigation settings are broken on sideload** — tab icon order, swipe between tabs, launch tab, hide feed/explore/reels/messages tab, Messenger mode. Upstream issue, documented in `README.md`; not caused by anything here. Jailbreak builds are unaffected.
- **Runtime unverified.** The IPA has never been installed. A launch crash or odd AVFoundation behavior points at the two deviations above before it points at tweak logic.

## Disk

`input/Payload` and `output/Payload` are ~566 MB each and both gitignored. Safe to delete once an IPA is confirmed installed; `build.sh` recreates `output/` and `input/` is re-staged from the source IPA.

## Scope note

Several Theta features act on other users' expectations rather than the local client: `StoryGhost` and `StorySeenLocalOnly` (view stories without appearing in the viewer list), `KeepDeletedMessages` (retain DMs the sender deleted), `HideTypingIndicator`, `ScreenshotSuppression` (defeats the screenshot notice on disappearing media), and `ProfileAnalyzer` (tracks follower/following changes on watched accounts). Recorded here so the contents of the build are not a surprise later — they are individually toggleable in Theta's settings.
