"""The wizard must not push the host's install-time state over newer state.

pm-align refreshes itself on every --apply, and the VM carries its own
appliance version and manifest.  The wizard's copies come from a tree that is
never refreshed, so overwriting on every --reconfigure downgrades pm-align
and makes a newer VM claim the host's old release (spec 4.1, 7.1).

Static checks over executable lines; backslash continuations are joined.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def logical_lines() -> list[str]:
    out, cur = [], ""
    for ln in WIZARD.read_text().splitlines():
        s = ln.strip()
        if not cur and (not s or s.startswith("#")):
            continue
        if s.endswith("\\"):
            cur += s[:-1] + " "
            continue
        out.append(cur + s)
        cur = ""
    return out


def test_wizard_never_overwrites_an_installed_pm_align():
    hits = [l for l in logical_lines()
            if "install " in l and l.split("install ", 1)[1].count("/usr/local/sbin/pm-align")]
    assert hits, "no pm-align install line found"
    for l in hits:
        guard = ("[ -e /usr/local/sbin/pm-align ] ||",
                 "[ ! -e /usr/local/sbin/pm-align ] &&")
        assert any(g in l and l.index(g) < l.index("install ") for g in guard), l


def _guarded(target: str):
    hits = [l for l in logical_lines()
            if "gexec" in l and f"> {target}" in l]
    assert hits, f"no gexec writes {target}"
    for l in hits:
        g = f"[ -s {target} ] ||"
        assert g in l and l.index(g) < l.index(f"> {target}"), l
        cmd = l.split('"')[1]  # the guest-bound string; the say fallback stays host-side
        assert cmd.isascii(), cmd


def test_vm_version_stamp_only_when_absent():
    _guarded("/etc/sigmond-appliance/version")


def test_vm_manifest_copy_only_when_absent():
    _guarded("/etc/sigmond-appliance/manifest.txt")
