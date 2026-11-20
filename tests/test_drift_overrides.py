from sigmond.drift import overrides

MS = ('[paths]\n{line}\n[site]\ncallsign = "AC0G"\ngrid = "EM38"\n'
      '[[receivers]]\nname = "rx"\n[channels.a]\nfreq = 7.0\n')
J = 'decoder_jt9 = "/usr/local/libexec/sigmond/decoders/ubersdr-msk144/jt9"'


def _run(tmp_path, cfgs, **kw):
    kw.setdefault("catalog_override", tmp_path / "c.toml")
    kw.setdefault("pins_file", tmp_path / "p.toml")
    kw.setdefault("coordination", tmp_path / "co.toml")
    kw.setdefault("repo_catalog", tmp_path / "repo-catalog.toml")
    return overrides.override_items(client_configs=cfgs, **kw)


def _cfg(tmp_path, text):
    p = tmp_path / "AC0G-ND.toml"
    p.write_text(text)
    return {"meteor-scatter": [p]}


def test_decoder_jt9_override_is_fault(tmp_path):
    got = _run(tmp_path, _cfg(tmp_path, MS.format(line=J)))
    assert [(i.kind, i.severity, i.subject, i.fix) for i in got] == [
        ("override", "fault", "AC0G-ND.toml:paths.decoder_jt9",
         "remove paths.decoder_jt9 from AC0G-ND.toml")]
    assert "ubersdr-msk144/jt9" in got[0].detail


def test_commented_override_ignored(tmp_path):
    assert _run(tmp_path, _cfg(tmp_path, MS.format(line="# " + J))) == []


def test_site_keys_never_count(tmp_path):
    assert _run(tmp_path, _cfg(tmp_path, MS.format(line=""))) == []


def test_catalog_repo_override_is_drift(tmp_path):
    # Every field of a catalog entry counts (v3.94 Task 9); a table that
    # is no catalog section never does.
    (tmp_path / "c.toml").write_text(
        '[client.x]\nrepo = "https://e/x.git"\n[server.y]\nbranch = "d"\n'
        '[client.z]\nenabled = true\n')
    got = _run(tmp_path, {})
    assert [(i.severity, i.subject) for i in got] == [
        ("drift", "catalog:x.repo"), ("drift", "catalog:z.enabled")]
    assert "catalog-prune" in got[0].fix


def test_any_station_pin_file_is_one_drift_item(tmp_path):
    (tmp_path / "p.toml").write_text(
        '[pins]\na = "' + "a" * 40 + '"\nb = "abc"\n')
    got = _run(tmp_path, {})
    assert [i.subject for i in got] == ["pins:station-file"]
    assert "2 station pin(s)" in got[0].detail


def test_override_inside_array_of_tables(tmp_path):
    got = _run(tmp_path, _cfg(
        tmp_path, '[[receivers]]\nname = "a"\n[[receivers]]\n'
                  'decoder_path = "/x/jt9"\n'))
    assert [i.subject for i in got] == [
        "AC0G-ND.toml:receivers[1].decoder_path"]


def test_operator_override_is_drift(tmp_path):
    (tmp_path / "co.toml").write_text(
        '[decoders]\nimplementation = "ubersdr"\noperator_override = true\n')
    got = _run(tmp_path, {})
    assert len(got) == 1 and "ubersdr" in got[0].detail
    assert got[0].fix.startswith("set operator_override = false")


def test_unreadable_config_is_one_fault(tmp_path):
    cfgs = _cfg(tmp_path, "this is = = not toml")
    (tmp_path / "c.toml").write_text('[client.x]\nrepo = "u"\n')
    got = _run(tmp_path, cfgs)
    assert [(i.severity, i.subject) for i in got] == [
        ("fault", str(cfgs["meteor-scatter"][0])), ("drift", "catalog:x.repo")]
    assert got[0].detail.startswith("could not read ")


# Final review C3: the shipped templates set decoder_jt9 and mag_usb_binary
# themselves; a config holding the release's own value is no override.
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

REPOS = Path(__file__).resolve().parents[2]
TEMPLATES = {
    "psk-recorder": REPOS / "psk-recorder/config/psk-recorder-config.toml.template",
    "mag-recorder": REPOS / "mag-recorder/config/mag-recorder-config.toml.template",
}


def _base_with_templates(tmp_path):
    base = tmp_path / "base"
    for comp, tpl in TEMPLATES.items():
        if not tpl.is_file():
            pytest.skip(f"no sibling checkout holds {tpl}")
        (base / comp / "config").mkdir(parents=True)
        shutil.copy(tpl, base / comp / "config" / tpl.name)
    return base


def _configs_from_templates(tmp_path, edit=None):
    cfgs = {}
    for comp, tpl in TEMPLATES.items():
        text = tpl.read_text()
        if edit:
            text = edit(comp, text)
        p = tmp_path / "etc" / comp / f"{comp}-config.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        cfgs[comp] = [p]
    return cfgs


def test_shipped_templates_raise_no_override(tmp_path):
    base = _base_with_templates(tmp_path)
    cfgs = _configs_from_templates(tmp_path)
    assert _run(tmp_path, cfgs, base=base) == []


def test_shipped_template_values_pass_without_a_checkout(tmp_path):
    # No template at the checkout: RELEASE_DEFAULTS holds the same values.
    _base_with_templates(tmp_path)          # skips without the siblings
    cfgs = _configs_from_templates(tmp_path)
    assert _run(tmp_path, cfgs) == []
    assert _run(tmp_path, cfgs, base=tmp_path / "empty-base") == []


def test_a_value_differing_from_the_template_is_a_fault(tmp_path):
    base = _base_with_templates(tmp_path)

    def edit(comp, text):
        if comp == "psk-recorder":
            return text.replace('"/usr/local/bin/jt9_decode"',
                                '"/home/op/wsjtx/jt9"')
        return text
    got = _run(tmp_path, _configs_from_templates(tmp_path, edit), base=base)
    assert [(i.severity, i.subject) for i in got] == [
        ("fault", "psk-recorder-config.toml:paths.decoder_jt9")]
    assert "/home/op/wsjtx/jt9" in got[0].detail


def test_a_key_the_template_lacks_is_still_a_fault(tmp_path):
    # meteor-scatter's template only comments decoder_jt9 out: ND's
    # hand-built jt9 stays visible.
    base = tmp_path / "base"
    (base / "meteor-scatter" / "config").mkdir(parents=True)
    (base / "meteor-scatter" / "config" / "m.toml.template").write_text(
        '[paths]\n# decoder_jt9 = "/x"\n')
    got = _run(tmp_path, _cfg(tmp_path, MS.format(line=J)), base=base)
    assert [i.subject for i in got] == ["AC0G-ND.toml:paths.decoder_jt9"]
