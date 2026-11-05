"""The golden VM's build writes the marker is_appliance_install() reads.

Final review, 2026-11-05: on a fresh image sigmond-wisdom.service runs at the
first boot, before sigmond-wizard.sh stamps /etc/sigmond-appliance/version.
The image's baked wisdom then read as a repo install's.  sigmond-appliance's
provision-components.sh writes a marker at build time; this test ties the two
repos to one path.  It skips when no sigmond-appliance checkout sits beside
this one.
"""
from pathlib import Path

import pytest

from sigmond import provenance

REPO = Path(__file__).resolve().parent.parent
PROVISION = REPO.parent / 'sigmond-appliance' / 'provision-components.sh'


@pytest.mark.skipif(not PROVISION.is_file(), reason='no sigmond-appliance checkout')
def test_provision_writes_the_marker_after_capture_prep():
    text = PROVISION.read_text()
    marker = provenance.GOLDEN_IMAGE_MARKER
    assert marker in text
    # capture-prep runs first; the marker must survive it.
    assert text.index('smd admin capture-prep') < text.index(marker)
