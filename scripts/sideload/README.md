# sideload

Re-signs an IPA with your own Apple Development certificate and installs it on a connected device, replacing Sideloadly. Everything it does is native arm64 and scriptable — there is no path through the interface that the command line cannot take.

## Setup, once

```sh
uv venv .venv-sideload
uv pip install --python .venv-sideload/bin/python textual
./sideload bootstrap
```

`bootstrap` builds a throwaway Xcode project so Xcode mints a provisioning profile through the account already signed in under Xcode → Settings → Accounts. On a paid team it returns the team wildcard profile, valid for a year and covering every registered device. There is no App Store Connect API key to generate and no `.p8` to keep anywhere.

Settings live in `~/.config/theta-sideload/config.toml`. Nothing is written into the repository.

## Use

```sh
./sideload                                   # the interface
./sideload devices                           # what devicectl can see
./sideload identities                        # codesigning identities and their teams
./sideload profile <path.mobileprovision>    # describe a profile
./sideload install --ipa output/Instagram_patched.ipa --launch
./sideload resign --ipa <path> --output <out.ipa>   # sign without installing
```

`install` with no `--ipa` takes the newest IPA in `output/` or `artifacts/`. Add `--remember` to save the device, profile and identity as defaults.

## What re-signing costs

The app is signed against your team, not Meta's, so every entitlement belonging to the original team is dropped — nineteen of them for Instagram. In practice:

- push notifications, universal links, iCloud and the app groups stop working
- the keychain access group changes, so the login session resets
- the seven app extensions are removed by default; they depend on app groups that no longer exist

The app itself runs normally. `install` prints exactly what it dropped.

## Bundle identifier

The profile decides this, not a setting. A wildcard App ID signs anything, so `com.burbn.instagram` survives and a new install replaces the old one. An explicit App ID can only sign itself, so the app is rewritten to match — which lets a patched and a stock build sit side by side.

## Troubleshooting

Every failure carries a remedy. The common ones:

| Symptom | Cause |
|---|---|
| `nPhone is disconnected` | A sleeping phone reports as disconnected even while plugged in. Unlock it. |
| `is not in the provisioning profile` | The device was registered after the profile was minted. Run `bootstrap` again. |
| `installed under a different team` | iOS will not replace an app signed by another team. Pass `--uninstall-conflicting`. |
| `codesign failed` | Usually a stale extended attribute. `xattr -cr` the bundle. |

Set `SIDELOAD_DEBUG=1` for the underlying tool output.

## Tests

```sh
cd scripts && ../.venv-sideload/bin/python -m unittest discover -s sideload/tests -t . -p 'test_*.py'
```
