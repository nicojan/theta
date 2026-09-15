"""Codesigning identities available in the login keychain.

codesign is given the SHA-1 hash rather than the common name: names collide
across teams and across renewals, hashes do not.
"""

import datetime
import re
from dataclasses import dataclass

from .errors import SigningError
from .run import run, run_text

_IDENTITY_LINE = re.compile(r'^\s*\d+\)\s+([0-9A-F]{40})\s+"(.+)"\s*$')
_SUBJECT_FIELD = re.compile(r"(?:^|,\s*)([A-Za-z]+)\s*=\s*([^,]+)")


@dataclass
class Identity:
    sha1: str
    common_name: str
    team_id: str = ""
    member_id: str = ""
    expires: datetime.datetime = None

    @property
    def is_development(self):
        return self.common_name.startswith(("Apple Development", "iPhone Developer"))

    @property
    def is_expired(self):
        if self.expires is None:
            return False
        return self.expires < datetime.datetime.now(datetime.timezone.utc)

    @property
    def label(self):
        team = f" · {self.team_id}" if self.team_id else ""
        return f"{self.common_name}{team}"


def list_identities():
    """Valid codesigning identities, enriched with team and expiry."""
    try:
        output = run_text(["security", "find-identity", "-v", "-p", "codesigning"])
    except RuntimeError as exc:
        raise SigningError("Could not read codesigning identities",
                           remedy="Unlock the login keychain and retry.",
                           detail=str(exc)) from exc

    identities = []
    for line in output.splitlines():
        match = _IDENTITY_LINE.match(line)
        if match:
            identities.append(Identity(sha1=match.group(1), common_name=match.group(2)))

    for identity in identities:
        _enrich(identity)
    return identities


def development_identities():
    return [i for i in list_identities() if i.is_development and not i.is_expired]


def find_identity(needle):
    """Resolve a SHA-1, team ID or name fragment to one Identity."""
    candidates = list_identities()
    for identity in candidates:
        if identity.sha1.lower() == needle.lower():
            return identity

    matches = [i for i in candidates
               if needle.lower() in i.common_name.lower() or i.team_id == needle]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(i.label for i in candidates) or "none"
        raise SigningError(f"No codesigning identity matching {needle!r}",
                           remedy=f"Available: {known}")
    raise SigningError(f"{needle!r} matches {len(matches)} identities",
                       remedy="Use the 40-character SHA-1 hash instead.")


def _enrich(identity):
    """Fill in team, member ID and expiry from the certificate itself."""
    pem = _certificate_pem(identity)
    if not pem:
        return
    try:
        subject = run_text(["openssl", "x509", "-noout", "-subject"],
                           input_bytes=pem.encode())
        enddate = run_text(["openssl", "x509", "-noout", "-enddate"],
                           input_bytes=pem.encode())
    except RuntimeError:
        return

    fields = dict(_SUBJECT_FIELD.findall(subject.split("=", 1)[-1] if "subject" in subject else subject))
    # The team is the organisational unit. The UID is the member, not the team --
    # a distinction that is easy to misread, since the common name carries a
    # third identifier that looks just like a team ID but is not one.
    identity.team_id = (fields.get("OU") or "").strip()
    identity.member_id = (fields.get("UID") or "").strip()

    raw = enddate.strip().split("=", 1)[-1]
    try:
        identity.expires = datetime.datetime.strptime(
            raw, "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        pass


def _certificate_pem(identity):
    """The PEM for this identity, matched by SHA-1 rather than by name."""
    try:
        blob = run_text(["security", "find-certificate", "-a", "-Z", "-p",
                         "-c", identity.common_name])
    except RuntimeError:
        return None

    current_hash = None
    buffer = []
    for line in blob.splitlines():
        if line.startswith("SHA-1 hash:"):
            current_hash = line.split(":", 1)[1].strip().upper()
            buffer = []
        elif line.startswith("-----BEGIN CERTIFICATE-----"):
            buffer = [line]
        elif buffer:
            buffer.append(line)
            if line.startswith("-----END CERTIFICATE-----"):
                if current_hash == identity.sha1.upper():
                    return "\n".join(buffer) + "\n"
                buffer = []
    return None
