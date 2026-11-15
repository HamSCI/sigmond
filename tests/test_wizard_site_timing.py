"""The wizard installs sigmond-site-timing into the VM from the VM's own
sigmond checkout (2026-11-15), and never writes through the sbin link."""
import re
from pathlib import Path

WIZ = (Path(__file__).resolve().parent.parent / 'scripts' / 'proxmox'
       / 'sigmond-wizard.sh').read_text()


def _block():
    start = WIZ.index('say "installing the site-timing auto-wiring helper in the VM"')
    return WIZ[start:WIZ.index('# ⛔ Tell the VM which network the SITE is on', start)]


def test_links_from_the_vms_sigmond_first():
    b = _block()
    assert 'ST_LINK=/opt/git/sigmond/sigmond/scripts/install/site-timing-link.sh' in b
    assert b.index('bash $ST_LINK') < b.index('/root/sigmond-appliance/sigmond-site-timing')


def test_fallback_never_redirects_into_the_link():
    # `> /usr/local/sbin/sigmond-site-timing` would follow the link and
    # overwrite sigmond's checkout with the stick's copy.
    assert not re.search(r'>\s*/usr/local/sbin/sigmond-site-timing\b', _block())
    assert 'mv -f /usr/local/sbin/.sigmond-site-timing.new /usr/local/sbin/sigmond-site-timing' in _block()


def test_nowhere_else_in_the_wizard_writes_it():
    assert not re.search(r'>\s*/usr/local/sbin/sigmond-site-timing\b', WIZ)
