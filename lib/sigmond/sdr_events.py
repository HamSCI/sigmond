"""The station's record of losing its RX888: one line per episode.

Three things lose the RX888 for a station, and each ends the same way: radiod
loses its samples, the station recovers by itself, and until v3.71 nobody
heard about it.

* The card leaves the USB bus (udev: usb-remove), and maybe returns (usb-add).
* sigmond-sdr-recover resets the card's hub port to bring a vanished or wedged
  card back (port-reset).  A hub's ppps claim is not proof that it cuts VBUS
  (AC0G-B4's two hubs never did), so the record says "port reset", never
  "power cycle".  Old lines that say power-cycle still read.
* The reset did not help: the card came back on the USB2 half only, with
  -71/-110 in the kernel log, or never (needs-replug).  A person must replug
  it by hand.
* The stream stalls with the card still on the bus: radiod logs "No rx888
  data for 5 seconds, quitting" and dies on a libusb assertion
  (radiod-exit).  AC0G-ND did this three times between 2026-10-10 10:13Z and
  2026-10-11 08:49Z, and its card never left the bus.
* radiod says "quitting" yet hangs, or falls silent, and
  sigmond-radiod-watchdog restarts it (radiod-wedge).  systemd reads that
  restart as asked for, so radiod's own hook writes nothing; the watchdog
  writes the event itself.

Writers call record(); `smd doctor` calls read_episodes() and assess().  The
file lives in /var/lib, not /run: a lost card clusters around reboots and
cold starts, and /run forgets across exactly those.

One line per EPISODE, never per event.  A loss of any kind within
EPISODE_GAP_S of the open episode's last event joins it, unless the card
came back more than SETTLE_S before.  ND's loop of 2026-10-05 (1,708 radiod
restarts in 9.5 h) makes one line with count 1708; one vanish -- removal,
radiod's failing restarts, the power cut -- makes one episode; a card that
drops, returns and drops again minutes later makes two.

An episode that stayed open longer than LONG_EPISODE_S reads INVALID
(sdr-down-long), even after the card came back (Michael's rule,
2026-10-11).  Shorter, a lone episode reads sdr-recovered.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

EVENTS_PATH = Path("/var/lib/sigmond/sdr-events.jsonl")
SYSFS_USB = Path("/sys/bus/usb/devices")

EPISODE_GAP_S = 600          # a loss this soon after the last one joins its episode
SETTLE_S = 120               # ... and so does one this soon after the card came back
KEEP_S = 7 * 86400           # episodes older than this drop on the next write
MAX_LINES = 200              # the file never holds more
WINDOW_S = 86400             # what `smd doctor` reads
FLAP_EPISODES = 3            # this many episodes in WINDOW_S read INVALID
LONG_EPISODE_S = 1800        # an episode open longer than this reads INVALID
DETAIL_MAX = 120
LOCK_WAIT_S = 5.0            # the writer waits this long for the lock, then raises OSError

LOSS_KINDS = ("usb-remove", "power-cycle", "port-reset", "needs-replug",
              "radiod-exit", "radiod-wedge")
ARRIVAL_KIND = "usb-add"
KINDS = LOSS_KINDS + (ARRIVAL_KIND,)

# What systemd sets $SERVICE_RESULT to when radiod died on its own.  A stop
# that someone asked for reads "success": radiod answers SIGTERM with EX_OK
# (ka9q-radio src/main.c, closedown).  "timeout" means an asked-for stop that
# ran long and ended in SIGKILL; it loses nothing.
RADIOD_LOSS_RESULTS = frozenset({"exit-code", "signal", "core-dump", "watchdog"})

# At power-up rx888_boot loads the firmware and the card leaves the bus as
# 00f3 and returns as 00f1 about a second later.  radiod can start inside that
# second: it finds no card, exits 66 (EX_NOINPUT, "device could not be
# found"), and systemd starts it again 10 s later.  K3LR did this on every
# boot on record.  An exit 66 this soon after boot is that race, not a loss; a
# card that stays missing keeps radiod failing past the grace and counts then.
NO_CARD_STATUS = "66"
BOOT_GRACE_S = 180
UPTIME_PATH = Path("/proc/uptime")

# The running card (00f1) and its FX3 bootloader (00f3): either one sits on the
# bus.  The same pair sigmond-sdr-recover looks for.
RX888_IDS = (("04b4", "00f1"), ("04b4", "00f3"))

_FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class Episode:
    opened: float
    last: float
    kinds: List[str]
    count: int
    detail: str = ""
    back: Optional[float] = None


def clean_text(s, limit: int = DETAIL_MAX) -> str:
    """Printable ASCII only, at most `limit` characters."""
    return "".join(c if " " <= c <= "~" else "?" for c in str(s or ""))[:limit]


def radiod_exit_is_loss(service_result: str) -> bool:
    return service_result in RADIOD_LOSS_RESULTS


def radiod_exit_is_boot_race(exit_status: str, uptime: Optional[float]) -> bool:
    return (exit_status == NO_CARD_STATUS and uptime is not None
            and uptime < BOOT_GRACE_S)


def uptime_s(path: Path = UPTIME_PATH) -> Optional[float]:
    """Seconds since boot, or None when the kernel's answer cannot be read."""
    try:
        return float(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime(_FMT)


def _epoch(s) -> float:
    return datetime.strptime(str(s), _FMT).replace(tzinfo=timezone.utc).timestamp()


def _parse(line: str) -> Optional[Episode]:
    try:
        d = json.loads(line)
        kinds, count, back = d["kinds"], d["count"], d.get("back")
        if (not isinstance(kinds, list) or not kinds
                or any(k not in LOSS_KINDS for k in kinds)):
            return None
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return None
        return Episode(opened=_epoch(d["opened"]), last=_epoch(d["last"]),
                       kinds=sorted(set(kinds)), count=count,
                       detail=clean_text(d.get("detail", "")),
                       back=_epoch(back) if back else None)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _dump(ep: Episode) -> str:
    return json.dumps({"opened": _iso(ep.opened), "last": _iso(ep.last),
                       "kinds": ep.kinds, "count": ep.count, "detail": ep.detail,
                       "back": _iso(ep.back) if ep.back is not None else None},
                      sort_keys=True)


# O_NONBLOCK: opening a FIFO planted at the path must not block.  A regular
# file ignores the flag, so its reads and writes behave as before.
_NOFOLLOW = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _open_regular(path: Path, flags: int, mode: int = 0o644) -> int:
    """Open `path` without following a symlink, and refuse anything but a
    regular file.  /var/lib/sigmond admits every sigmond-group member while
    the writer runs as root, so a planted link must not steer a root write.
    Raises OSError on a link (ELOOP) or on a device, FIFO or directory."""
    fd = os.open(str(path), flags | _NOFOLLOW, mode)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _load(path: Path) -> List[Episode]:
    try:
        fd = _open_regular(path, os.O_RDONLY)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "r", errors="replace") as fh:
        text = fh.read()
    return [ep for ep in map(_parse, text.splitlines()) if ep is not None]


def _write(path: Path, episodes: Sequence[Episode]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("".join(_dump(ep) + "\n" for ep in episodes))
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock_or_raise(lock) -> None:
    """flock(LOCK_EX), waiting at most LOCK_WAIT_S.  Any reader of the 0644
    lock file can hold the lock, so a plain blocking flock could hang the
    writer, and radiod's stop with it."""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise OSError(f"could not take the events lock within {LOCK_WAIT_S:g} s")
            time.sleep(0.05)


def record(kind: str, detail: str = "", *, now: float,
           path: Path = EVENTS_PATH) -> Optional[str]:
    """Note one event.  Returns "opened" when it began an episode, "grown"
    when it joined the open one, "back" when the card returned to an open
    episode, and None when it changed nothing (an arrival with no loss
    before it, as at every boot)."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _open_regular(path.parent / (path.name + ".lock"),
                            os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    with os.fdopen(lock_fd, "a") as lock:
        _lock_or_raise(lock)
        episodes = [ep for ep in _load(path) if ep.last >= now - KEEP_S]
        cur = episodes[-1] if episodes else None
        what = None
        if kind == ARRIVAL_KIND:
            # A hand replug can come hours after needs-replug: it closes that
            # episode whatever the gap, so the doctor alarm clears.
            if (cur is not None and cur.back is None
                    and (abs(now - cur.last) <= EPISODE_GAP_S
                         or "needs-replug" in cur.kinds)):
                cur.back = now
                what = "back"
        elif (cur is not None and abs(now - cur.last) <= EPISODE_GAP_S
              and (cur.back is None or now - cur.back <= SETTLE_S)):
            cur.last = max(cur.last, now)
            cur.count += 1
            cur.kinds = sorted(set(cur.kinds) | {kind})
            if kind in ("usb-remove", "needs-replug"):
                cur.back = None
            if detail:
                cur.detail = clean_text(detail)
            what = "grown"
        else:
            episodes.append(Episode(opened=now, last=now, kinds=[kind], count=1,
                                    detail=clean_text(detail)))
            what = "opened"
        if what is not None:
            _write(path, episodes[-MAX_LINES:])
    return what


def read_episodes(path: Path = EVENTS_PATH, *, now: float,
                  window_s: float = WINDOW_S) -> List[Episode]:
    """The episodes whose last event falls inside the window, oldest first."""
    return [ep for ep in _load(Path(path)) if ep.last >= now - window_s]


def card_present(root: Path = SYSFS_USB) -> bool:
    """True when a running RX888 or its bootloader sits on the USB bus."""
    try:
        entries = list(Path(root).iterdir())
    except OSError:
        return False
    for d in entries:
        try:
            vid = (d / "idVendor").read_text().strip().lower()
            pid = (d / "idProduct").read_text().strip().lower()
        except OSError:
            continue
        if (vid, pid) in RX888_IDS:
            return True
    return False


def _hm(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def episode_span(ep: Episode) -> float:
    """How long the episode stayed open, in seconds: from its first event to
    the card's return when one came, else to its last event.  A stall with
    the card on the bus brings no return, but radiod restarts every 10 s while
    it lasts, so the last event tracks the outage."""
    end = ep.last if ep.back is None else max(ep.last, ep.back)
    return end - ep.opened


def _tail(ep: Episode) -> str:
    tail = f"{'+'.join(ep.kinds)}, {ep.count} event(s)"
    if ep.detail:
        tail += f", {ep.detail}"
    return tail


def assess(episodes: Sequence[Episode], present: bool) -> Optional[Tuple[str, str]]:
    """`smd doctor`'s verdict on the window: (kind, detail), or None.

    A reset that did not help comes first: an open episode that holds
    needs-replug reads sdr-needs-replug, card present or not, until the card
    returns (usb-add) or a later episode opens.  Then the present tense: a
    card off the bus now reads sdr-lost.  Then an
    episode open longer than LONG_EPISODE_S reads sdr-down-long, then three
    episodes read sdr-flapping.  Only what remains reads sdr-recovered."""
    if not episodes:
        return None
    n = len(episodes)
    last = episodes[-1]
    tail = _tail(last)
    if "needs-replug" in last.kinds and last.back is None:
        # sdr-recover asks at once, with no reset, on a port no reset can
        # clear (a hub whose SuperSpeed half uhubctl cannot switch, or one
        # measured to hold VBUS through a cut).
        tried = "port-reset" in last.kinds or "power-cycle" in last.kinds
        why = ("a port reset did not clear it" if tried
               else "a port reset on this hub cannot clear it")
        return ("sdr-needs-replug",
                f"RX888 needs a hand replug at the card since {_hm(last.opened)}; "
                f"{why} ({tail})")
    if not present:
        return ("sdr-lost",
                f"RX888 off the bus now; lost at {_hm(last.opened)} ({tail}); "
                f"{n} loss episode(s) in 24 h")
    long = [ep for ep in episodes if episode_span(ep) > LONG_EPISODE_S]
    if long:
        worst = max(long, key=episode_span)
        return ("sdr-down-long",
                f"RX888 lost for {int(episode_span(worst) // 60)} min from "
                f"{_hm(worst.opened)} ({_tail(worst)}); {n} loss episode(s) in 24 h")
    if n >= FLAP_EPISODES:
        return ("sdr-flapping",
                f"RX888 lost {n} times in 24 h, last at {_hm(last.opened)} ({tail}); "
                f"back each time, but something keeps taking it")
    return ("sdr-recovered",
            f"RX888 lost {n} time(s) in 24 h, last at {_hm(last.opened)} ({tail}); recovered")
