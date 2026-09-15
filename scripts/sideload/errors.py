"""Typed pipeline failures that carry a remedy the user can act on."""


class SideloadError(Exception):
    """A step failed. `remedy` is shown to the user verbatim."""

    def __init__(self, message, remedy=None, detail=None):
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.detail = detail

    def __str__(self):
        parts = [self.message]
        if self.remedy:
            parts.append(f"Try: {self.remedy}")
        return "\n".join(parts)


class DeviceError(SideloadError):
    pass


class ProfileError(SideloadError):
    pass


class SigningError(SideloadError):
    pass


class ConfigError(SideloadError):
    pass
