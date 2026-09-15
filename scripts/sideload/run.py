"""Subprocess helpers with redaction, used by every module that shells out."""

import re
import shlex
import subprocess

# Anything matching these is never written to a log or surfaced in the TUI.
_REDACTIONS = [
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "<jwt>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<private-key>"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1<redacted>"),
]


def redact(text):
    """Strip credentials from text bound for a log or the screen."""
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def run(argv, check=True, cwd=None, timeout=None, input_bytes=None):
    """Run argv, returning CompletedProcess. Output is captured, never inherited."""
    proc = subprocess.run(
        argv,
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        stderr = redact(proc.stderr.decode("utf-8", "replace")).strip()
        raise RuntimeError(f"{shlex.join(argv)} exited {proc.returncode}\n{stderr}")
    return proc


def run_text(argv, **kwargs):
    """Run argv and return its stdout as text."""
    return run(argv, **kwargs).stdout.decode("utf-8", "replace")
