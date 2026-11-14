"""``systemctl show --timestamp=unix`` on a systemd that predates it.

sigmond reads unit start times as ``@<seconds>`` from
``systemctl show --timestamp=unix``.  systemd 249 (Ubuntu 22.04) has
``--timestamp=`` but not its ``unix`` value: it prints
``Invalid value: unix.`` and exits 1, so every reader failed there.  On
VK5ARG (2026-11-14) that failed every upload-lever step of bring-up, held the
uploads, and left hs-uploader with no pipelines.

The fallback keeps the ``unix`` call exactly as it is wherever systemd takes
it.  Only when systemd refuses that value does the caller re-run with
``--timestamp=utc`` (systemd 246+), and :func:`utc_to_unix_output` rewrites
each ``... YYYY-MM-DD HH:MM:SS[.ffffff] UTC`` value into the ``@<seconds>``
the callers already parse.
"""
from __future__ import annotations

import calendar
import re

UNIX = "--timestamp=unix"
UTC = "--timestamp=utc"

# "Sat 2026-11-14 19:05:22 UTC" (utc) or "... 19:05:22.398306 UTC" (us+utc).
# The weekday is skipped, so the locale it is printed in does not matter.
_UTC_VALUE = re.compile(
    r"^(?:[A-Za-z]+ )?(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})(\.\d+)? UTC$")


def unix_unsupported(result) -> bool:
    """True when ``systemctl`` refused ``--timestamp=unix`` itself."""
    if getattr(result, "returncode", 0) == 0:
        return False
    err = (getattr(result, "stderr", "") or "")
    return "Invalid value" in err and "unix" in err


def with_utc(argv: list) -> list:
    """``argv`` with ``--timestamp=unix`` swapped for ``--timestamp=utc``."""
    return [UTC if a == UNIX else a for a in argv]


def utc_value_to_unix(value: str) -> str:
    """``Sat 2026-11-14 19:05:22 UTC`` -> ``@1763147122``; anything that is not
    a UTC timestamp (another property, ``n/a``, empty) comes back unchanged."""
    m = _UTC_VALUE.match(value.strip())
    if not m:
        return value
    y, mo, d, h, mi, s, frac = m.groups()
    secs = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s)))
    return f"@{secs}{frac or ''}"


def utc_to_unix_output(stdout: str) -> str:
    """Rewrite ``systemctl show --timestamp=utc`` output (``KEY=VALUE`` lines,
    or bare values under ``--value``) as ``--timestamp=unix`` would print it."""
    out = []
    for line in (stdout or "").splitlines():
        # A timestamp value never holds "=", so a line with one is KEY=VALUE.
        key, sep, val = line.partition("=")
        out.append(f"{key}={utc_value_to_unix(val)}" if sep
                   else utc_value_to_unix(line))
    return "\n".join(out) + ("\n" if (stdout or "").endswith("\n") else "")
