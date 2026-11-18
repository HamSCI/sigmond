from sigmond.drift import strays
from sigmond.lifecycle import UnitRef


def _ref(inst, orphaned=False, comp="meteor-scatter"):
    return UnitRef(comp, f"{comp}@{inst}.service", f"{comp}@.service",
                   inst, "service", "test", orphaned)


def _run(tmp_path, refs=(), env=None, names=frozenset({"psk-recorder"})):
    dd = tmp_path / "decoders"
    dd.mkdir(exist_ok=True)
    base = tmp_path / "git"
    base.mkdir(exist_ok=True)
    return strays.stray_items(decoder_dir=dd, base=base, catalog_names=set(names),
                              unit_refs=list(refs),
                              unit_env=env or (lambda u: {}))


def test_extra_decoder_folder_is_drift(tmp_path):
    (tmp_path / "decoders" / "ubersdr-msk144").mkdir(parents=True)
    items = _run(tmp_path)
    assert [(i.kind, i.severity) for i in items] == [("stray", "drift")]
    assert items[0].subject.endswith("ubersdr-msk144")
    assert items[0].fix.startswith("remove ")


def test_escaped_instance_is_stray(tmp_path):
    items = _run(tmp_path, [_ref("AC0G\\x3dB4")])
    assert len(items) == 1 and items[0].severity == "drift"
    assert items[0].fix == "smd admin instance remove meteor-scatter AC0G\\x3dB4"
    env = lambda u: {"METEOR_SCATTER_DELIVERY_MODE": "x"}
    items = _run(tmp_path, [_ref("AC0G\\x3dB4")], env)
    assert items[0].severity == "fault"
    assert "METEOR_SCATTER_DELIVERY_MODE" in items[0].detail


def test_orphaned_instance_is_stray(tmp_path):
    items = _run(tmp_path, [_ref("old", orphaned=True), _ref("ok")])
    assert [i.subject for i in items] == ["meteor-scatter@old.service"]


def test_unknown_checkout_is_drift(tmp_path):
    (tmp_path / "git" / "mystery" / ".git").mkdir(parents=True)
    (tmp_path / "git" / "plain").mkdir(parents=True)
    items = _run(tmp_path)
    assert [i.subject.rsplit("/", 1)[1] for i in items] == ["mystery"]
    assert items[0].fix.startswith("remove ")


def test_known_things_give_no_items(tmp_path):
    for d in ("ubersdr", "wsjtx", ".hidden"):
        (tmp_path / "decoders" / d).mkdir(parents=True)
    for d in ("psk-recorder", "onion", "sigmond"):
        (tmp_path / "git" / d / ".git").mkdir(parents=True)
    assert _run(tmp_path, [_ref("default")]) == []
