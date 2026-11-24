"""sigmond.release_list: the release list's format, owners and comparisons."""
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sigmond import release_list as rl
from sigmond.release_list import (
    Mismatch, ReleaseList, ReleaseListError, ROWS, compare_live,
    compare_manifest, format_list, ft8lib_from_install_sh,
    ka9q_radio_from_compat, load_list, main, native_constants, parse_list,
)

REPO = Path(__file__).resolve().parent.parent


def sha(i):
    return ("%040x" % (0xabc0000 + i))


def rows(**over):
    d = {n: sha(i) for i, n in enumerate(ROWS)}
    d.update(over)
    return d


def text_for(comps, version="v3.94"):
    body = "".join(f'"{n}" = "{s}"\n' if "/" in n else f'{n} = "{s}"\n'
                   for n, s in sorted(comps.items()))
    return (rl.HEADER + f'version = "{version}"\n\n[components]\n' + body)


GOOD = text_for(rows())


def test_parse_round_trip():
    parsed = parse_list(GOOD)
    assert parsed.version == "v3.94"
    assert set(parsed.components) == set(ROWS)
    assert format_list(parsed) == GOOD


def test_abbreviated_sha_refuses():
    t = text_for(rows(callhash="adc9d9d"))
    with pytest.raises(ReleaseListError, match="callhash"):
        parse_list(t)


def test_uppercase_sha_refuses():
    with pytest.raises(ReleaseListError, match="callhash"):
        parse_list(text_for(rows(callhash=sha(1).upper().replace("0", "A"))))


def test_sigmond_row_refuses():
    with pytest.raises(ReleaseListError, match="sigmond has no row"):
        parse_list(text_for(rows(sigmond=sha(99))))


def test_missing_proxmox_row_refuses():
    c = rows()
    del c["sigmond-proxmox"]
    with pytest.raises(ReleaseListError, match="sigmond-proxmox"):
        parse_list(text_for(c))


def test_bad_name_refuses():
    with pytest.raises(ReleaseListError, match="bad/name"):
        parse_list(text_for(rows(**{"bad/name": sha(98)})))


def test_missing_components_table_refuses():
    with pytest.raises(ReleaseListError, match="components"):
        parse_list('version = "v3.94"\n')


def test_invalid_toml_refuses():
    with pytest.raises(ReleaseListError):
        parse_list("version = \n")


@pytest.mark.parametrize("v", ["3.94", "v3.94-rc1"])
def test_bad_version_refuses(v):
    with pytest.raises(ReleaseListError, match="version"):
        parse_list(text_for(rows(), version=v))


def _const(name):
    m = re.search(r"^%s\s*=\s*'([0-9a-f]{40})'" % name,
                  (REPO / "bin" / "smd").read_text(), re.M)
    return m.group(1)


def test_native_constants_reads_bin_smd():
    text = (REPO / "bin" / "smd").read_text()
    assert native_constants(text) == {
        "onion": _const("_ONION_COMMIT"),
        "wsjtx": _const("_WSJTX_COMMIT"),
        "ubersdr_wsprd": _const("_UBERSDR_COMMIT")}


def test_native_constants_missing_refuses():
    with pytest.raises(ReleaseListError, match="wsjtx"):
        native_constants("_ONION_COMMIT = '%s'\n" % sha(1))


def test_compat_skips_comments():
    s = "aa52" + "0" * 28 + "deadbeef"
    assert ka9q_radio_from_compat("# x\n# y\n\n%s\n" % s) == s


def test_compat_rejects_short():
    with pytest.raises(ReleaseListError):
        ka9q_radio_from_compat("# x\naa52\n")


def test_ft8lib_pin():
    s = "0dfd" + "1" * 33 + "dec"
    assert ft8lib_from_install_sh('x=1\nFT8LIB_PIN="%s"   # note\n' % s) == s


def test_ft8lib_pin_missing():
    with pytest.raises(ReleaseListError):
        ft8lib_from_install_sh("nothing\n")


def test_compare_live_equal_by_prefix():
    rlist = parse_list(GOOD)
    live = {n: s[:9] for n, s in rlist.components.items()}
    live["sigmond"] = "1234567"
    assert compare_live(rlist, live) == []


def test_compare_live_skips_proxmox_and_reports_differ():
    rlist = parse_list(GOOD)
    live = {"callhash": "1111111", "sigmond-proxmox": "2222222"}
    out = compare_live(rlist, live)
    assert [m.component for m in out] == ["callhash"]
    assert out[0].want == sha(ROWS.index("callhash"))
    assert out[0].have == "1111111"


def test_compare_live_absent_only_with_require_all():
    rlist = parse_list(GOOD)
    live = {"callhash": rlist.components["callhash"]}
    assert compare_live(rlist, live) == []
    out = compare_live(rlist, live, require_all=True)
    names = {m.component for m in out}
    assert names == set(ROWS) - {"callhash", "sigmond-proxmox"}
    assert all(m.have is None and m.want for m in out)


def test_compare_live_unlisted_checkout_reported():
    rlist = parse_list(GOOD)
    out = compare_live(rlist, {"future-client": "abcdef1"})
    assert out == [Mismatch("future-client", None, "abcdef1", out[0].note)]
    assert out[0].note


def manifest_for(rlist, **over):
    comps = {n: s[:8] for n, s in rlist.components.items()
             if n != "sigmond-proxmox"}
    comps["sigmond"] = "daba1f6"
    comps.update(over)
    lines = "".join(f"    {n:<16} {s}\n" for n, s in comps.items())
    return ("image_version: v3.94\nappliance_commit: %s\nappliance_tag: v3.94\n"
            "\ncomponents (live):\n%s" %
            (rlist.components["sigmond-proxmox"], lines))


def test_compare_manifest_equal():
    rlist = parse_list(GOOD)
    assert compare_manifest(rlist, manifest_for(rlist)) == []


def test_compare_manifest_maps_appliance_commit():
    rlist = parse_list(GOOD)
    m = manifest_for(rlist).replace(rlist.components["sigmond-proxmox"], "f" * 40)
    out = compare_manifest(rlist, m)
    assert [x.component for x in out] == ["sigmond-proxmox"]


def test_compare_manifest_extra_row():
    rlist = parse_list(GOOD)
    out = compare_manifest(rlist, manifest_for(rlist, **{"new-thing": "abcdef1"}))
    assert [(x.component, x.want) for x in out] == [("new-thing", None)]


def test_compare_manifest_missing_row():
    rlist = parse_list(GOOD)
    m = "".join(l for l in manifest_for(rlist).splitlines(True)
                if "callhash" not in l)
    out = compare_manifest(rlist, m)
    assert [(x.component, x.have) for x in out] == [("callhash", None)]


def test_compare_manifest_unreadable_refuses():
    rlist = parse_list(GOOD)
    with pytest.raises(ReleaseListError):
        compare_manifest(rlist, "no block here\n")


def test_main_check_exit_codes(tmp_path, capsys):
    lst = tmp_path / "components.toml"
    lst.write_text(GOOD)
    rlist = parse_list(GOOD)
    man = tmp_path / "m.txt"
    man.write_text(manifest_for(rlist))
    assert main(["check", "--list", str(lst), "--manifest", str(man)]) == 0
    man.write_text(manifest_for(rlist, callhash="1111111"))
    assert main(["check", "--list", str(lst), "--manifest", str(man)]) == 1
    out = capsys.readouterr().out
    assert "callhash" in out and out.isascii()
    lst.write_text("version = \n")
    assert main(["check", "--list", str(lst), "--manifest", str(man)]) == 2
    assert main(["check", "--list", str(tmp_path / "none"),
                 "--manifest", str(man)]) == 2


def test_main_version_and_stdin(tmp_path, capsys, monkeypatch):
    lst = tmp_path / "components.toml"
    lst.write_text(GOOD)
    assert main(["version", "--list", str(lst)]) == 0
    assert capsys.readouterr().out == "v3.94\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(GOOD))
    assert main(["version", "--list", "-"]) == 0
    assert capsys.readouterr().out == "v3.94\n"


def test_module_runs_as_script(tmp_path):
    lst = tmp_path / "components.toml"
    lst.write_text(GOOD)
    r = subprocess.run([sys.executable, "-m", "sigmond.release_list",
                        "version", "--list", str(lst)],
                       capture_output=True, text=True,
                       env={"PYTHONPATH": str(REPO / "lib")})
    assert r.returncode == 0 and r.stdout == "v3.94\n"


def _git_show(repo, rev, path):
    r = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"{repo.name} has no {rev}:{path} locally")
    return r.stdout


def test_real_list_owners_agree():
    path = REPO / "release" / "components.toml"
    if not path.exists():
        pytest.skip("release/components.toml does not exist yet")
    rlist = load_list(path)
    nat = native_constants((REPO / "bin" / "smd").read_text())
    for name, want in nat.items():
        assert rlist.components[name] == want, name
    kp = REPO.parent / "ka9q-python"
    if kp.is_dir():
        got = ka9q_radio_from_compat(_git_show(
            kp, rlist.components["ka9q-python"],
            "ka9q_radio_compat"))
        assert rlist.components["ka9q-radio"] == got
    psk = REPO.parent / "psk-recorder"
    if psk.is_dir():
        got = ft8lib_from_install_sh(_git_show(
            psk, rlist.components["psk-recorder"], "scripts/install.sh"))
        assert rlist.components["ft8_lib"] == got


def test_every_catalog_repo_has_a_row():
    # install.sh clones every catalog repo, and the golden VM's freshness gate
    # refuses a checkout the list does not name (v3.95 candidate 2 stopped on
    # chirp-recorder).  So a repo added to the catalog needs a row here too.
    import tomllib
    catalog = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "etc" / "catalog.toml").read_text())
    repos = {name for section in catalog.values() if isinstance(section, dict)
             for name, entry in section.items()
             if isinstance(entry, dict) and entry.get("repo")}
    assert repos - set(ROWS) == set()
