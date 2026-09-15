"""Provisioning profile decoding and the entitlement policy.

A .mobileprovision is a CMS (PKCS#7) envelope around an XML plist. macOS can
unwrap it without any third-party dependency, so this module shells out to
`security cms -D` rather than pulling in an ASN.1 parser.
"""

import datetime
import plistlib
from dataclasses import dataclass, field

from .errors import ProfileError
from .run import run

# Granted by every development profile, including wildcard App IDs, and required
# for the app to launch and be debuggable. `application-identifier` is rewritten
# per-binary, so it is handled separately rather than copied.
_ALWAYS_KEYS = (
    "com.apple.developer.team-identifier",
    "keychain-access-groups",
)


@dataclass
class Profile:
    """A decoded .mobileprovision."""

    path: str
    uuid: str
    name: str
    team_id: str
    app_id: str
    entitlements: dict
    device_udids: tuple = ()
    expires: datetime.datetime = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_wildcard(self):
        """True when the App ID ends in `*` and so signs any bundle identifier."""
        return self.app_id.endswith("*")

    @property
    def app_id_prefix(self):
        """The team/App ID prefix, i.e. everything before the first dot."""
        return self.app_id.split(".", 1)[0]

    @property
    def is_expired(self):
        if self.expires is None:
            return False
        return self.expires < datetime.datetime.now(datetime.timezone.utc)

    def covers_device(self, udid):
        return udid in self.device_udids

    def covers_bundle_id(self, bundle_id):
        """Whether this profile can sign `bundle_id`."""
        suffix = self.app_id.split(".", 1)[1] if "." in self.app_id else ""
        if suffix == "*":
            return True
        if suffix.endswith(".*"):
            return bundle_id.startswith(suffix[:-1])
        return bundle_id == suffix


def decode(path):
    """Decode a .mobileprovision into a Profile."""
    try:
        der = run(["security", "cms", "-D", "-i", str(path)]).stdout
    except RuntimeError as exc:
        raise ProfileError(
            f"Could not decode {path}",
            remedy="Confirm the file is a .mobileprovision and not truncated.",
            detail=str(exc),
        ) from exc

    try:
        raw = plistlib.loads(der)
    except Exception as exc:
        raise ProfileError(f"{path} is not a property list once unwrapped", detail=str(exc)) from exc

    entitlements = raw.get("Entitlements", {})
    app_id = entitlements.get("application-identifier")
    if not app_id:
        raise ProfileError(
            f"{path} grants no application-identifier",
            remedy="Regenerate the profile; it is not an iOS app profile.",
        )

    expires = raw.get("ExpirationDate")
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=datetime.timezone.utc)

    return Profile(
        path=str(path),
        uuid=raw.get("UUID", ""),
        name=raw.get("Name", ""),
        team_id=(raw.get("TeamIdentifier") or [entitlements.get("com.apple.developer.team-identifier", "")])[0],
        app_id=app_id,
        entitlements=entitlements,
        device_udids=tuple(raw.get("ProvisionedDevices", ()) or ()),
        expires=expires,
        raw=raw,
    )


def derive_bundle_id(profile, original_bundle_id):
    """Return (bundle_id, rewritten) for signing `original_bundle_id` under `profile`.

    The profile dictates this. A wildcard App ID signs anything, so the original
    identifier survives; an explicit App ID can only sign itself, so the app is
    rewritten to match. Offering this as a user choice would let the user pick
    something the profile cannot honour.
    """
    suffix = profile.app_id.split(".", 1)[1] if "." in profile.app_id else ""
    if suffix == "*":
        return original_bundle_id, False
    if suffix.endswith(".*"):
        prefix = suffix[:-1]
        if original_bundle_id.startswith(prefix):
            return original_bundle_id, False
        return prefix + original_bundle_id.rsplit(".", 1)[-1], True
    return suffix, suffix != original_bundle_id


def compute_entitlements(app_entitlements, profile, bundle_id, get_task_allow=True):
    """Intersect what the app declares with what the profile grants.

    Instagram declares twenty-odd entitlements — iCloud containers, app groups,
    associated domains, App Clips, production push. None are grantable to another
    team, so the result is an allowlist rather than a merge: the four keys any
    development profile carries, plus any capability the profile genuinely grants
    and the app actually asked for. Values come from the profile, so a production
    push entitlement becomes a development one rather than being carried over.
    """
    computed = {
        "application-identifier": f"{profile.app_id_prefix}.{bundle_id}",
        "get-task-allow": bool(get_task_allow),
    }

    for key in _ALWAYS_KEYS:
        if key in profile.entitlements:
            computed[key] = profile.entitlements[key]

    granted = set(profile.entitlements) & set(app_entitlements or {})
    for key in granted:
        if key in computed or key == "application-identifier":
            continue
        computed[key] = profile.entitlements[key]

    return computed


def dropped_entitlements(app_entitlements, computed):
    """Keys the app declared that the profile could not grant, for reporting."""
    return sorted(set(app_entitlements or {}) - set(computed))
