from sigmond.clients.base import ClientView, DataSink, DiskWrite, InstanceView
from sigmond.clients.contract import _instance_from_contract
from sigmond.storage.model import Store
from sigmond.storage.registry import registrations_from_views, store_for_path


def _view(name, version, sinks=(), disk_writes=()):
    iv = InstanceView(instance="default", data_sinks=list(sinks),
                      disk_writes=list(disk_writes))
    return ClientView(client_type=name, installed=True,
                      contract_version=version, instances=[iv])


ROOT = Store("root", "/", "guest", 1, 0)


def test_store_for_path_takes_the_longest_component_prefix():
    s = [ROOT, Store("archive", "/var/lib/sigmond/archive", "archive", 1, 0)]
    assert store_for_path("/var/lib/sigmond/archive/iq", s).name == "archive"
    assert store_for_path("/var/lib/sigmond/archived", s).name == "root"


def test_v09_client_with_classes_is_managed():
    view = _view("hf-timestd", "0.9", sinks=[DataSink(
        "file", "/var/lib/timestd/raw", floor_bytes=3 << 30,
        final_policy="give-up-oldest:raw", classes=("raw", "sent"))])
    (r,) = registrations_from_views({"hf-timestd": view}, [ROOT])
    assert r.managed and r.floor_bytes == 3 << 30 and r.policy == "give-up-oldest:raw"


def test_disk_writes_promote_as_unmanaged():
    view = _view("codar-sounder", "0.8", disk_writes=[
        DiskWrite(path="/var/lib/codar", mb_per_day=1.0, retention_days=7)])
    (r,) = registrations_from_views({"codar-sounder": view}, [ROOT])
    assert not r.managed and r.floor_bytes == 0 and r.policy is None


def test_two_sinks_on_one_store_merge():
    view = _view("x", "0.9", sinks=[DataSink("file", "/a", floor_bytes=1, classes=("raw",)),
                                    DataSink("file", "/b", floor_bytes=2, classes=("raw",))])
    (r,) = registrations_from_views({"x": view}, [ROOT])
    assert r.paths == ("/a", "/b") and r.floor_bytes == 3


def test_inventory_json_parses_data_sinks():
    iv = _instance_from_contract({"instance": "default", "data_sinks": [
        {"kind": "file", "path": "/a", "floor_bytes": 5,
         "final_policy": "stop-writing", "classes": ["raw"]}]})
    assert iv.data_sinks[0].floor_bytes == 5 and iv.data_sinks[0].classes == ("raw",)


def test_old_contract_version_is_never_managed():
    view = _view("x", "0.8", sinks=[DataSink("file", "/a", classes=("raw",))])
    (r,) = registrations_from_views({"x": view}, [ROOT])
    assert not r.managed


def test_sink_without_a_store_drops_with_an_issue():
    view = _view("x", "0.9", sinks=[DataSink("file", "/a", classes=("raw",))])
    out = registrations_from_views({"x": view}, [Store("m", "/mnt", "pool", 1, 0)])
    assert out == [] and any("/a" in i for i in view.issues)


def test_bad_final_policy_is_not_registered():
    view = _view("x", "0.9", sinks=[DataSink("file", "/a", final_policy="rm-rf",
                                             classes=("raw",))])
    (r,) = registrations_from_views({"x": view}, [ROOT])
    assert r.policy is None and any("rm-rf" in i for i in view.issues)


def test_target_key_serves_as_path_and_service_sinks_skip():
    iv = _instance_from_contract({"instance": "d", "data_sinks": [
        {"kind": "file", "target": "/t"}, {"kind": "service", "target": "db.t"}]})
    assert [s.path for s in iv.data_sinks] == ["/t", "db.t"]
    view = ClientView("x", True, contract_version="0.9", instances=[iv])
    (r,) = registrations_from_views({"x": view}, [ROOT])
    assert r.paths == ("/t",)


def test_declared_rates_add_and_the_tightest_retention_caps_days():
    view = _view("x", "0.9", sinks=[
        DataSink("file", "/a", mb_per_day=10.0, retention_days=7, classes=("raw",)),
        DataSink("file", "/b", mb_per_day=5.0, retention_days=0, classes=("raw",))],
        disk_writes=[DiskWrite(path="/c", mb_per_day=1.0, retention_days=3)])
    (r,) = registrations_from_views({"x": view}, [ROOT])
    assert r.mb_per_day == 16.0 and r.max_days == 3.0


def test_no_retention_means_no_cap():
    view = _view("x", "0.9", sinks=[DataSink("file", "/a", mb_per_day=2.0, classes=("raw",))])
    (r,) = registrations_from_views({"x": view}, [ROOT])
    assert r.mb_per_day == 2.0 and r.max_days is None
