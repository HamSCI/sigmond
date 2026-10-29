"""`smd install --no-pull` installs what sits on disk (v3.82 row 1).

Bring-up runs `smd install --components X --yes`.  Both install paths used to
hard-code `pull=True`, and the three native installers pulled on their own, so
a fresh appliance install fetched every component and checked out origin/main
instead of building the checkouts the image shipped at its manifest pins.

Bring-up now passes `--no-pull` (tests/test_bringup.py holds that half).  These
tests hold the other half: the flag reaches `install_client()` as `pull=False`
and each native `clone_repo()` call as `fetch=False, pull_if_exists=False`,
and leaving the flag off still pulls.  Every privileged or networked step is
patched; nothing here elevates.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from sigmond.catalog import CatalogEntry

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_no_pull", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_no_pull", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

_PIN = "0123456789abcdef0123456789abcdef01234567"


def _client_entry(name="fake-client"):
    return CatalogEntry(
        name=name, kind="client", description="test",
        repo=f"https://github.com/test/{name}",
        install_script=f"/opt/git/sigmond/{name}/install.sh",
    )


def _native_entry(name):
    return CatalogEntry(name=name, kind="client", description="test",
                        repo=f"https://github.com/test/{name}")


def _args(**kw):
    base = dict(client=None, components=None, profile=None, client_dir=None,
                dry_run=False, yes=True, force=False, no_enable=False,
                topology=None, no_pull=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _single_client_install(no_pull):
    """Run `_install_single_client` for a catalog client; return the kwargs
    `_do_install_client` received."""
    entry = _client_entry()
    install = mock.Mock(return_value=True)
    with mock.patch.object(smd, "load_catalog", return_value=[entry]), \
         mock.patch.object(smd, "get_entry", return_value=entry), \
         mock.patch("sigmond.preflight.check_requires", return_value=True), \
         mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_build_decoders", return_value=True), \
         mock.patch.object(smd, "_post_install_fixups"), \
         mock.patch.object(smd, "_enable_after_install"), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.object(smd, "_do_install_client", install):
        rc = smd._install_single_client(_args(client=entry.name, no_pull=no_pull))
    assert rc == 0
    install.assert_called_once()
    return install.call_args.kwargs


def test_single_client_install_with_no_pull_passes_pull_false():
    assert _single_client_install(no_pull=True)["pull"] is False


def test_single_client_install_without_the_flag_still_pulls():
    assert _single_client_install(no_pull=False)["pull"] is True


def _components_install(no_pull):
    entry = _client_entry()
    targets = smd._InstallTargets()
    targets.catalog_queue.append(entry)
    install = mock.Mock(return_value=True)
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_load_topology",
                           return_value={"smd_bin": Path("/nonexistent/smd")}), \
         mock.patch.object(smd, "load_catalog", return_value=[entry]), \
         mock.patch.object(smd, "_classify_install_targets", return_value=targets), \
         mock.patch.object(smd, "_ensure_sigmond_dirs"), \
         mock.patch.object(smd, "_install_smd_itself"), \
         mock.patch.object(smd, "_install_timing_watchdog"), \
         mock.patch.object(smd, "_install_radiod_watchdog"), \
         mock.patch.object(smd, "_install_loopback_multicast_route"), \
         mock.patch.object(smd, "_host_has_local_radiod", return_value=False), \
         mock.patch.object(smd, "_ensure_sigmond_venv", return_value=True), \
         mock.patch.object(smd, "_post_install_fixups"), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.object(smd, "_do_install_client", install):
        rc = smd._install_full_suite(_args(components=entry.name, no_pull=no_pull))
    assert rc == 0
    install.assert_called_once()
    return install.call_args.kwargs


def test_components_install_with_no_pull_passes_pull_false():
    assert _components_install(no_pull=True)["pull"] is False


def test_components_install_without_the_flag_still_pulls():
    assert _components_install(no_pull=False)["pull"] is True


def test_full_suite_threads_no_pull_to_every_native():
    targets = smd._InstallTargets()
    targets.natives.update({"ka9q-radio", "ka9q-web", "gmag-webui"})
    natives = {n: mock.Mock(return_value=True) for n in (
        "_install_radiod_native", "_install_ka9q_web_native",
        "_install_gmag_webui_native")}
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_load_topology",
                           return_value={"smd_bin": Path("/nonexistent/smd")}), \
         mock.patch.object(smd, "load_catalog", return_value=[]), \
         mock.patch.object(smd, "_classify_install_targets", return_value=targets), \
         mock.patch.object(smd, "_ensure_sigmond_dirs"), \
         mock.patch.object(smd, "_install_smd_itself"), \
         mock.patch.object(smd, "_install_timing_watchdog"), \
         mock.patch.object(smd, "_install_radiod_watchdog"), \
         mock.patch.object(smd, "_install_loopback_multicast_route"), \
         mock.patch.object(smd, "_host_has_local_radiod", return_value=True), \
         mock.patch.object(smd, "_ensure_sigmond_venv", return_value=True), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.multiple(smd, **natives):
        rc = smd._install_full_suite(_args(
            components="ka9q-radio,ka9q-web,gmag-webui", no_pull=True))
    assert rc == 0
    for name, m in natives.items():
        m.assert_called_once()
        assert m.call_args.kwargs.get("pull") is False, name


# --- the natives: what each hands clone_repo ---------------------------------

def _native_clone_kwargs(installer, name, no_pull, extra_patches=()):
    """Run one native installer far enough to call `_clone_repo`, then stop it
    there (the clone raises).  Return the kwargs of that call."""
    entry = _native_entry(name)
    clone = mock.Mock(side_effect=RuntimeError("stop after clone"))
    patches = [mock.patch.object(smd, "get_entry", return_value=entry),
               mock.patch.object(smd, "_clone_repo", clone),
               *extra_patches]
    for p in patches:
        p.start()
    try:
        ok = getattr(smd, installer)([entry], pull=not no_pull)
    finally:
        for p in reversed(patches):
            p.stop()
    assert ok is False
    clone.assert_called_once()
    return clone.call_args.kwargs


def _radiod_patches(pin):
    return (mock.patch.object(smd, "_install_radiod_deps", return_value=True),
            mock.patch.object(smd, "_ensure_radio_user", return_value=True),
            mock.patch.object(smd, "_ensure_fftw_dir", return_value=True),
            mock.patch.object(smd, "_ka9q_radio_pin", return_value=pin))


def test_native_ka9q_web_with_no_pull_never_fetches():
    kw = _native_clone_kwargs("_install_ka9q_web_native", "ka9q-web", no_pull=True)
    assert kw["pull_if_exists"] is False and kw["fetch"] is False


def test_native_ka9q_web_without_the_flag_still_pulls():
    kw = _native_clone_kwargs("_install_ka9q_web_native", "ka9q-web", no_pull=False)
    assert kw["pull_if_exists"] is True and kw.get("fetch", True) is True


def test_native_gmag_webui_with_no_pull_never_fetches():
    kw = _native_clone_kwargs("_install_gmag_webui_native", "gmag-webui",
                              no_pull=True)
    assert kw["pull_if_exists"] is False and kw["fetch"] is False


def test_native_radiod_with_no_pull_keeps_its_pin_without_fetching():
    kw = _native_clone_kwargs("_install_radiod_native", "ka9q-radio",
                              no_pull=True, extra_patches=_radiod_patches(_PIN))
    assert kw["ref"] == _PIN
    assert kw["fetch"] is False and kw["pull_if_exists"] is False


def test_native_radiod_unpinned_with_no_pull_never_fetches():
    kw = _native_clone_kwargs("_install_radiod_native", "ka9q-radio",
                              no_pull=True, extra_patches=_radiod_patches(None))
    assert kw.get("ref") is None
    assert kw["fetch"] is False and kw["pull_if_exists"] is False


def test_native_radiod_without_the_flag_still_fetches_its_pin():
    kw = _native_clone_kwargs("_install_radiod_native", "ka9q-radio",
                              no_pull=False, extra_patches=_radiod_patches(_PIN))
    assert kw["ref"] == _PIN
    assert kw["pull_if_exists"] is True and kw.get("fetch", True) is True


def test_single_client_native_ka9q_web_threads_no_pull():
    entry = _native_entry("ka9q-web")
    native = mock.Mock(return_value=True)
    with mock.patch.object(smd, "load_catalog", return_value=[entry]), \
         mock.patch.object(smd, "get_entry", return_value=entry), \
         mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_enable_after_install"), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.object(smd, "_install_ka9q_web_native", native):
        rc = smd._install_single_client(_args(client="ka9q-web", no_pull=True))
    assert rc == 0
    assert native.call_args.kwargs.get("pull") is False


# --- the CLI surface ---------------------------------------------------------

def _run_smd(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"),
               SIGMOND_NO_VENV_REEXEC="1")
    return subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("verb", [("install",), ("component", "install")])
def test_install_help_offers_no_pull(verb):
    out = _run_smd(*verb, "--help").stdout
    assert "--no-pull" in out


def test_completion_offers_no_pull_wherever_it_offers_no_enable():
    script = _run_smd("admin", "completion", "bash").stdout
    lines = [ln for ln in script.splitlines() if "--no-enable" in ln]
    assert len(lines) >= 2
    assert all("--no-pull" in ln for ln in lines)
