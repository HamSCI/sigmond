"""The suite never reads the host's site-profile.toml (final review R17.2)."""
from pathlib import Path

import sigmond.site_profile as sp
from sigmond import receivers
from sigmond.coordination import Coordination, render_env


def test_the_profile_path_points_into_a_tmp_dir_that_does_not_exist():
    assert sp.SITE_PROFILE_PATH != Path('/etc/sigmond/site-profile.toml')
    assert not sp.SITE_PROFILE_PATH.exists()


def test_load_receivers_reads_the_patched_path_by_default():
    sp.SITE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    sp.SITE_PROFILE_PATH.write_text(
        '[[receiver]]\nreporter_id = "AC0G/B1A"\n'
        'radiod = "ac0g-b1-a-status.local"\n')
    (r,) = receivers.load_receivers()
    assert r.reporter_id == 'AC0G/B1A'
    assert sp.load_site_profile() is not None
    assert 'RECEIVER_COUNT=1' in render_env(Coordination())


def test_render_env_sees_no_receivers_without_a_profile():
    assert 'RECEIVER_' not in render_env(Coordination())
