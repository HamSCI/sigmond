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


# --- LAN offers (security review, 2026-11-15) --------------------------------
# site-timing in the VM wires a LAN NTP server or GNSS stream only from its
# trust record now.  The wizard, with the operator present, offers what it
# finds and records each yes in the VM.

def _fn(name):
    start = WIZ.index(f'{name}() {{')
    return WIZ[start:WIZ.index('\n}\n', start)]


def test_lan_offers_ask_only_on_a_tty():
    body = _fn('ask_lan_extras')
    assert body.index('[ -t 0 ] || return 0') < body.index('rd -r -p')


def test_ask_ntp_ends_with_the_lan_offers():
    assert 'ask_lan_extras "$scan"' in _fn('ask_ntp')


def test_trust_is_recorded_only_into_a_site_timing_that_knows_it():
    body = _fn('lan_trust_record')
    assert body.index('grep -q -- --trust-ntp') < body.index('--trust-ntp $ip --no-run')
    assert '--trust-gnss $GNSS_TRUST --no-run' in body


def test_trust_is_recorded_after_site_timing_is_installed():
    assert WIZ.index('\nlan_trust_record\n') > WIZ.index(
        'say "installing the site-timing auto-wiring helper in the VM"')
