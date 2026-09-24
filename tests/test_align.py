"""Tests for sigmond.align — no test touches the network."""
import io
import json
import unittest

from sigmond import align

MANIFEST = """image_version: v3.53
appliance_commit: 4a92fbdbcce497035994e76636689d84f7ffe967
appliance_tag: v3.53

components (live):
    sigmond          daba1f6
    hf-timestd       5c8196d
    ka9q-radio       401992c
    ka9q-python      72ce2a2
    hs-uploader      1111111
    psk-recorder     2222222
    wspr-recorder    3333333
    mag-recorder     4444444
    hamsci-dsp       5555555
    station-web      6666666
    gpsdo-monitor    7777777
"""


def release_json(tag="v3.53", with_manifest=True):
    assets = [{"name": f"sigmond-appliance-{tag}-20260924.sha256",
               "browser_download_url": "https://example/sha"}]
    if with_manifest:
        assets.append({"name": f"sigmond-appliance-{tag}-20260924.manifest.txt",
                       "browser_download_url": "https://example/manifest"})
    return json.dumps({"tag_name": tag, "assets": assets}).encode()


class FakeUrlopen:
    """Answers by URL; records every URL asked for."""
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, url, timeout=None):
        self.seen.append(url)
        body = self.routes.get(url)
        if body is None:
            raise OSError(f"no route for {url}")
        return io.BytesIO(body)


class FetchReleaseTests(unittest.TestCase):
    def test_latest_release_parses_manifest(self):
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": MANIFEST.encode()})
        rel = align.fetch_release(urlopen=fake)
        self.assertEqual(rel.tag, "v3.53")
        self.assertEqual(rel.appliance_commit, "4a92fbdbcce497035994e76636689d84f7ffe967")
        self.assertEqual(rel.components["hf-timestd"], "5c8196d")
        self.assertEqual(len(rel.components), 11)

    def test_named_release_uses_the_tags_endpoint(self):
        fake = FakeUrlopen({align.RELEASES_API + "/tags/v3.51": release_json("v3.51"),
                            "https://example/manifest": MANIFEST.encode()})
        align.fetch_release("v3.51", urlopen=fake)
        self.assertIn(align.RELEASES_API + "/tags/v3.51", fake.seen)

    def test_release_without_manifest_asset_refuses(self):
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(with_manifest=False)})
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=fake)
        self.assertIn("manifest", str(cm.exception))

    def test_untrustworthy_manifest_refuses(self):
        thin = "components (live):\n    sigmond daba1f6\n"
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": thin.encode()})
        with self.assertRaises(align.LookupError_):
            align.fetch_release(urlopen=fake)

    def test_network_failure_refuses(self):
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=FakeUrlopen({}))
        self.assertIn("could not reach", str(cm.exception))


def rel(**over):
    comps = {"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"}
    comps.update(over)
    return align.Release(tag="v3.53", manifest_text="", appliance_commit=None, components=comps)


class PlanTests(unittest.TestCase):
    def test_all_current(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6aa", "hf-timestd": "5c8196d",
                                        "ka9q-radio": "401992cdecaa"}, {})
        self.assertEqual({i.status for i in plan}, {"current"})

    def test_move_and_order_sigmond_first(self):
        plan = align.plan_align(rel(), {"sigmond": "459bee6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"}, {})
        self.assertEqual(plan[0].component, "sigmond")
        self.assertEqual([i.status for i in plan[:2]], ["move", "move"])

    def test_dirty_checkout_that_would_move_refuses(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"}, {"hf-timestd": True})
        item = next(i for i in plan if i.component == "hf-timestd")
        self.assertEqual(item.status, "refuse")
        self.assertIn("uncommitted", item.note)

    def test_dirty_but_current_is_not_refused(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                        "ka9q-radio": "401992c"}, {"hf-timestd": True})
        self.assertEqual(next(i for i in plan if i.component == "hf-timestd").status, "current")

    def test_radiod_move_is_flagged(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                        "ka9q-radio": "deb7bdd"}, {})
        item = next(i for i in plan if i.component == "ka9q-radio")
        self.assertEqual(item.status, "move")
        self.assertIn("RESTARTS radiod", item.note)

    def test_missing_and_stray(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "ka9q-radio": "401992c",
                                        "codar-sounder": "eeeeeee"}, {})
        by = {i.component: i.status for i in plan}
        self.assertEqual(by["hf-timestd"], "missing")
        self.assertEqual(by["codar-sounder"], "stray")
        self.assertEqual(plan[-1].component, "codar-sounder")

    def test_unreadable_head_is_a_refusal_not_a_move(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": None,
                                        "ka9q-radio": "401992c"}, {})
        self.assertEqual(next(i for i in plan if i.component == "hf-timestd").status, "refuse")


import hashlib


class ProbeTests(unittest.TestCase):
    def test_github_slug(self):
        self.assertEqual(align.github_slug("https://github.com/ka9q/ka9q-radio"), "ka9q/ka9q-radio")
        self.assertEqual(align.github_slug("https://github.com/HamSCI/sigmond.git/"), "HamSCI/sigmond")
        self.assertIsNone(align.github_slug("https://gitlab.com/x/y"))

    def test_commit_distance(self):
        url = "https://api.github.com/repos/HamSCI/hf-timestd/compare/4595c00...5c8196d"
        body = json.dumps({"ahead_by": 20, "behind_by": 0,
                           "files": [{}] * 22}).encode()
        got = align.commit_distance("HamSCI/hf-timestd", "4595c00", "5c8196d",
                                    urlopen=FakeUrlopen({url: body}))
        self.assertEqual(got, {"ahead": 20, "behind": 0, "files": 22})

    def test_commit_distance_failure_is_reported_not_raised(self):
        got = align.commit_distance("HamSCI/x", "a", "b", urlopen=FakeUrlopen({}))
        self.assertIn("error", got)

    def test_image_file_drift(self):
        good, stale = b"#!/bin/bash\nnew\n", b"#!/bin/bash\nold\n"
        routes = {f"{align.RAW_BASE}/v3.53/sigmond-site-timing": good,
                  f"{align.RAW_BASE}/v3.53/sigmond-location-check": good}
        local = {"/usr/local/sbin/sigmond-site-timing": stale,
                 "/usr/local/sbin/sigmond-location-check": None}
        got = {d["path"]: d["status"] for d in align.image_file_drift(
            "v3.53", read_local=local.get, urlopen=FakeUrlopen(routes))}
        self.assertEqual(got["/usr/local/sbin/sigmond-site-timing"], "differs")
        self.assertEqual(got["/usr/local/sbin/sigmond-location-check"], "absent")

    def test_image_file_unreachable_is_unknown(self):
        got = align.image_file_drift("v3.53", read_local=lambda p: b"x", urlopen=FakeUrlopen({}))
        self.assertEqual({d["status"] for d in got}, {"unknown"})

    def test_recorded_release(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("v3.36\n")
        try:
            self.assertEqual(align.recorded_release(f.name), "v3.36")
        finally:
            os.unlink(f.name)
        self.assertIsNone(align.recorded_release("/nonexistent/version"))


if __name__ == "__main__":
    unittest.main()
