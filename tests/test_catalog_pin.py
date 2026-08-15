"""A catalog entry must be able to pin a component to an exact commit.

`installer.clone_repo` already takes a `ref`, `_checkout_ref` already
deepens a shallow clone to reach an older pin, and its docstring is
emphatic: "Building the EXACT pin matters: ka9q-radio's wire-protocol
headers are what ka9q-web and ka9q-python adapt to, so we must never
silently settle for the shallow HEAD."

Nothing ever passed a ref.  Both call sites -- the source-dep clone and
the main install -- called clone_repo without one, and CatalogEntry had
no field to carry a pin, so every install built whatever each
component's `main` happened to be AT INSTALL TIME, with no record of
which.  Two DASI2 images built a month apart are different software.

Sharpened by the 165-commit ka9q-radio jump of 2026-08-15, which carried
a `struct channel` -> `chan_t` rename and a filter rework.
"""
from sigmond.catalog import CatalogEntry, _entry_from_toml_block


def test_a_pin_is_parsed_from_the_catalog():
    e = _entry_from_toml_block('ka9q-radio', {
        'repo': 'https://github.com/ka9q/ka9q-radio',
        'pin': 'cd44bbdd',
    })

    assert e.pin == 'cd44bbdd'


def test_no_pin_means_track_the_default_branch():
    """Unpinned entries must keep working exactly as before."""
    e = _entry_from_toml_block('wspr-recorder', {
        'repo': 'https://github.com/HamSCI/wspr-recorder',
    })

    assert e.pin is None


def test_ka9q_radio_is_NOT_catalog_pinned():
    """ka9q-radio is pinned by ka9q-python's compat file, not here.

    That pin means something this one cannot: "the commit ka9q-python's
    types.py was validated against".  It is DERIVED from a real check —
    the 118 status tags and the encoding enum — and bumping it requires
    that check to pass.  A hand-edited catalog pin can drift from the
    wire contract silently; the compat pin cannot.

    Both existed briefly on 2026-08-15 and agreed only by coincidence,
    which is the setup that bites later.  `_install_radiod_native()`
    reads the compat pin, so that is the one that decides what gets
    built, and this file must not appear to be a second authority.
    """
    from pathlib import Path
    import tomllib
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / 'etc' / 'catalog.toml').read_text())

    assert not cfg['client']['ka9q-radio'].get('pin'), (
        "ka9q-radio must NOT be pinned here — ka9q-python's "
        "ka9q_radio_compat owns that pin; two authorities will diverge")
