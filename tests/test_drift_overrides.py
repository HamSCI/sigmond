from sigmond.drift import overrides

MS = ('[paths]\n{line}\n[site]\ncallsign = "AC0G"\ngrid = "EM38"\n'
      '[[receivers]]\nname = "rx"\n[channels.a]\nfreq = 7.0\n')
J = 'decoder_jt9 = "/usr/local/libexec/sigmond/decoders/ubersdr-msk144/jt9"'


def _run(tmp_path, cfgs, **kw):
    kw.setdefault("catalog_override", tmp_path / "c.toml")
    kw.setdefault("pins_file", tmp_path / "p.toml")
    kw.setdefault("coordination", tmp_path / "co.toml")
    kw.setdefault("manifest", {})
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
    (tmp_path / "c.toml").write_text(
        '[client.x]\nrepo = "https://e/x.git"\n[server.y]\nbranch = "d"\n'
        '[client.z]\nenabled = true\n')
    got = _run(tmp_path, {})
    assert [(i.severity, i.subject) for i in got] == [
        ("drift", "catalog:x"), ("drift", "catalog:y")]
    assert "catalog-prune" in got[0].fix


def test_pin_differing_from_manifest_is_drift(tmp_path):
    (tmp_path / "p.toml").write_text(
        '[pins]\na = "' + "a" * 40 + '"\nb = "' + "b" * 40 + '"\n'
        'c = "' + "c" * 40 + '"\n')
    got = _run(tmp_path, {}, manifest={"a": "aaaaaaa", "b": "1234567"})
    assert [i.subject for i in got] == ["pin:b", "pin:c"]


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
        ("fault", str(cfgs["meteor-scatter"][0])), ("drift", "catalog:x")]
    assert got[0].detail.startswith("could not read ")
