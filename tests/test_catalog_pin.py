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


def test_the_shipped_catalog_pins_ka9q_radio():
    """The component whose wire-protocol headers everything adapts to is
    the one that must never float."""
    from pathlib import Path
    import tomllib
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / 'etc' / 'catalog.toml').read_text())

    entry = cfg['client']['ka9q-radio']
    assert entry.get('pin'), "ka9q-radio must be pinned in etc/catalog.toml"
