import datetime
import unittest

from sideload.profile import (
    Profile,
    compute_entitlements,
    derive_bundle_id,
    dropped_entitlements,
)

FUTURE = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)


def make_profile(app_id, entitlements=None, team="3CY4DX3K45"):
    base = {
        "application-identifier": app_id,
        "com.apple.developer.team-identifier": team,
        "keychain-access-groups": [f"{team}.*"],
        "get-task-allow": True,
    }
    base.update(entitlements or {})
    return Profile(path="/tmp/p.mobileprovision", uuid="u", name="n", team_id=team,
                   app_id=app_id, entitlements=base, expires=FUTURE)


class WildcardProfiles(unittest.TestCase):
    def test_preserves_original_bundle_id(self):
        prof = make_profile("3CY4DX3K45.*")
        self.assertEqual(derive_bundle_id(prof, "com.burbn.instagram"),
                         ("com.burbn.instagram", False))

    def test_signs_any_bundle_id(self):
        prof = make_profile("3CY4DX3K45.*")
        self.assertTrue(prof.is_wildcard)
        self.assertTrue(prof.covers_bundle_id("com.burbn.instagram"))
        self.assertTrue(prof.covers_bundle_id("anything.at.all"))


class ExplicitProfiles(unittest.TestCase):
    def test_forces_rewrite_to_its_own_identifier(self):
        prof = make_profile("3CY4DX3K45.ca.forhuman.theta")
        self.assertEqual(derive_bundle_id(prof, "com.burbn.instagram"),
                         ("ca.forhuman.theta", True))

    def test_reports_no_rewrite_when_already_matching(self):
        prof = make_profile("3CY4DX3K45.ca.forhuman.theta")
        self.assertEqual(derive_bundle_id(prof, "ca.forhuman.theta"),
                         ("ca.forhuman.theta", False))

    def test_covers_only_its_own_identifier(self):
        prof = make_profile("3CY4DX3K45.ca.forhuman.theta")
        self.assertFalse(prof.is_wildcard)
        self.assertFalse(prof.covers_bundle_id("com.burbn.instagram"))
        self.assertTrue(prof.covers_bundle_id("ca.forhuman.theta"))


class PrefixWildcards(unittest.TestCase):
    def test_keeps_matching_identifier(self):
        prof = make_profile("3CY4DX3K45.ca.forhuman.*")
        self.assertEqual(derive_bundle_id(prof, "ca.forhuman.theta"),
                         ("ca.forhuman.theta", False))

    def test_rewrites_non_matching_identifier_under_the_prefix(self):
        prof = make_profile("3CY4DX3K45.ca.forhuman.*")
        self.assertEqual(derive_bundle_id(prof, "com.burbn.instagram"),
                         ("ca.forhuman.instagram", True))


# The entitlements Instagram actually ships, trimmed to the interesting ones.
INSTAGRAM_ENTITLEMENTS = {
    "application-identifier": "MH9GU9K5PX.com.burbn.instagram",
    "aps-environment": "production",
    "com.apple.developer.associated-domains": ["applinks:instagram.com"],
    "com.apple.developer.icloud-container-identifiers": ["iCloud.com.burbn.instagram"],
    "com.apple.security.application-groups": ["group.com.burbn.instagram"],
    "com.apple.developer.team-identifier": "777W53UFB2",
    "keychain-access-groups": ["MH9GU9K5PX.com.burbn.instagram"],
}


class EntitlementPolicy(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile("3CY4DX3K45.*")

    def test_rewrites_application_identifier_to_our_team(self):
        computed = compute_entitlements(INSTAGRAM_ENTITLEMENTS, self.profile, "com.burbn.instagram")
        self.assertEqual(computed["application-identifier"], "3CY4DX3K45.com.burbn.instagram")

    def test_takes_team_identifier_from_the_profile_not_the_app(self):
        computed = compute_entitlements(INSTAGRAM_ENTITLEMENTS, self.profile, "com.burbn.instagram")
        self.assertEqual(computed["com.apple.developer.team-identifier"], "3CY4DX3K45")

    def test_drops_everything_the_profile_cannot_grant(self):
        computed = compute_entitlements(INSTAGRAM_ENTITLEMENTS, self.profile, "com.burbn.instagram")
        for key in ("aps-environment",
                    "com.apple.developer.associated-domains",
                    "com.apple.developer.icloud-container-identifiers",
                    "com.apple.security.application-groups"):
            self.assertNotIn(key, computed, f"{key} should have been dropped")

    def test_reports_what_it_dropped(self):
        computed = compute_entitlements(INSTAGRAM_ENTITLEMENTS, self.profile, "com.burbn.instagram")
        dropped = dropped_entitlements(INSTAGRAM_ENTITLEMENTS, computed)
        self.assertIn("aps-environment", dropped)
        self.assertIn("com.apple.security.application-groups", dropped)

    def test_keeps_a_capability_the_profile_actually_grants(self):
        prof = make_profile("3CY4DX3K45.*", {"aps-environment": "development"})
        computed = compute_entitlements(INSTAGRAM_ENTITLEMENTS, prof, "com.burbn.instagram")
        # Granted, so it survives -- but with the profile's development value,
        # not the production value the App Store build shipped with.
        self.assertEqual(computed["aps-environment"], "development")

    def test_get_task_allow_can_be_withheld(self):
        computed = compute_entitlements(INSTAGRAM_ENTITLEMENTS, self.profile,
                                        "com.burbn.instagram", get_task_allow=False)
        self.assertIs(computed["get-task-allow"], False)

    def test_handles_an_unsigned_app_with_no_entitlements(self):
        computed = compute_entitlements({}, self.profile, "com.burbn.instagram")
        self.assertEqual(computed["application-identifier"], "3CY4DX3K45.com.burbn.instagram")


class Expiry(unittest.TestCase):
    def test_past_expiry_is_expired(self):
        prof = make_profile("3CY4DX3K45.*")
        prof.expires = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        self.assertTrue(prof.is_expired)

    def test_future_expiry_is_not(self):
        self.assertFalse(make_profile("3CY4DX3K45.*").is_expired)


if __name__ == "__main__":
    unittest.main()
