"""Tests for sigmond.align — no test touches the network."""
import http.client
import io
import json
import unittest
import urllib.error

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
                            "https://example/manifest": MANIFEST.replace("v3.53", "v3.51").encode()})
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

    def test_malformed_component_name_refuses(self):
        bad = MANIFEST.replace("sigmond ", "sig!mond ", 1)
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": bad.encode()})
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=fake)
        self.assertIn("malformed", str(cm.exception))

    def test_malformed_sha_refuses(self):
        bad = MANIFEST.replace("daba1f6", "ZZZZZZZ")
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": bad.encode()})
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=fake)
        self.assertIn("malformed", str(cm.exception))

    def test_invalid_release_tag_refuses_before_building_a_url(self):
        fake = FakeUrlopen({})
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release("not-a-tag", urlopen=fake)
        self.assertIn("not a release tag", str(cm.exception))
        self.assertEqual(fake.seen, [])

    def test_latest_release_with_a_malformed_tag_name_refuses(self):
        for bad in ("v3.53/../x", "latest", "", "v3"):
            fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(bad),
                                "https://example/manifest": MANIFEST.encode()})
            with self.assertRaises(align.LookupError_) as cm:
                align.fetch_release(urlopen=fake)
            self.assertIn("not a release tag", str(cm.exception))

    def test_preamble_tag_mismatch_refuses(self):
        mismatched = MANIFEST.replace("appliance_tag: v3.53", "appliance_tag: v9.99")
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": mismatched.encode()})
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=fake)
        self.assertIn("v9.99", str(cm.exception))


class GetErrorTests(unittest.TestCase):
    """align._get narrows what it catches, and names rate limits and HTTP
    status codes instead of flattening everything to 'could not reach'."""

    def test_rate_limit_names_the_limit_and_reset(self):
        import email.message

        hdrs = email.message.Message()
        hdrs["X-RateLimit-Remaining"] = "0"
        hdrs["X-RateLimit-Reset"] = "1790306100"  # 2026-09-25 03:15:00 UTC

        def fake(url, timeout=None):
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs, io.BytesIO(b"rate limit"))

        with self.assertRaises(align.LookupError_) as cm:
            align._get("https://api.github.com/x", fake, 5.0)
        msg = str(cm.exception)
        self.assertIn("rate limit", msg.lower())
        self.assertIn("03:15Z", msg)

    def test_404_on_tags_reports_no_such_release(self):
        def fake(url, timeout=None):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))

        with self.assertRaises(align.LookupError_) as cm:
            align._get(align.RELEASES_API + "/tags/v9.99", fake, 5.0)
        self.assertIn("no such release: v9.99", str(cm.exception))

    def test_other_http_error_names_the_status(self):
        def fake(url, timeout=None):
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b""))

        with self.assertRaises(align.LookupError_) as cm:
            align._get("https://api.github.com/x", fake, 5.0)
        self.assertIn("HTTP 500", str(cm.exception))

    def test_network_error_is_could_not_reach(self):
        def fake(url, timeout=None):
            raise urllib.error.URLError("no route")

        with self.assertRaises(align.LookupError_) as cm:
            align._get("https://api.github.com/x", fake, 5.0)
        self.assertIn("could not reach", str(cm.exception))

    def test_programming_error_propagates(self):
        def fake(url, timeout=None):
            raise TypeError("boom")

        with self.assertRaises(TypeError):
            align._get("https://api.github.com/x", fake, 5.0)


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
        self.assertEqual(item.note, "radiod rebuild is Plan 2b — --apply will not move it")

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

    # --- Final review ---

    def test_libraries_move_before_their_consumers(self):
        r = align.Release(tag="v3.53", manifest_text="", appliance_commit=None,
                          components={"codar-sounder": "aaaaaaa", "hf-tec": "bbbbbbb",
                                      "ka9q-python": "ccccccc", "sigmond": "ddddddd"})
        plan = align.plan_align(r, {"codar-sounder": "1111111", "hf-tec": "2222222",
                                    "ka9q-python": "3333333", "sigmond": "4444444"}, {})
        self.assertEqual([i.component for i in plan],
                         ["sigmond", "ka9q-python", "codar-sounder", "hf-tec"])

    def test_libraries_first_keeps_its_own_order(self):
        comps = {n: "aaaaaaa" for n in ("hs-uploader", "callhash", "hamsci-dsp",
                                        "ka9q-python", "aardvark", "sigmond")}
        r = align.Release(tag="v3.53", manifest_text="", appliance_commit=None, components=comps)
        plan = align.plan_align(r, {n: "1111111" for n in comps}, {})
        self.assertEqual([i.component for i in plan],
                         ["sigmond", "ka9q-python", "hamsci-dsp", "callhash", "hs-uploader",
                          "aardvark"])

    def test_uvlock_only_dirt_is_planned_as_a_move_with_a_note(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"}, {"hf-timestd": ["uv.lock"]})
        item = next(i for i in plan if i.component == "hf-timestd")
        self.assertEqual(item.status, "move")
        self.assertIn("uv.lock will be reset", item.note)

    def test_uvlock_plus_pin_dirt_is_still_uvlock_only(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"},
                                {"hf-timestd": [".pin", "uv.lock"]})
        self.assertEqual(next(i for i in plan if i.component == "hf-timestd").status, "move")

    def test_other_listed_dirt_refuses(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"},
                                {"hf-timestd": ["uv.lock", "src/x.py"]})
        item = next(i for i in plan if i.component == "hf-timestd")
        self.assertEqual(item.status, "refuse")
        self.assertIn("uncommitted", item.note)

    def test_an_empty_dirt_list_is_clean(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"}, {"hf-timestd": []})
        item = next(i for i in plan if i.component == "hf-timestd")
        self.assertEqual((item.status, item.note), ("move", ""))


import hashlib


class ProbeTests(unittest.TestCase):
    def test_github_slug(self):
        self.assertEqual(align.github_slug("https://github.com/ka9q/ka9q-radio"), "ka9q/ka9q-radio")
        self.assertEqual(align.github_slug("https://github.com/HamSCI/sigmond.git/"), "HamSCI/sigmond")
        self.assertIsNone(align.github_slug("https://gitlab.com/x/y"))

    def test_commit_distance(self):
        url = "https://api.github.com/repos/HamSCI/hf-timestd/compare/4595c00...5c8196d?per_page=1"
        body = json.dumps({"ahead_by": 20, "behind_by": 0,
                           "files": [{}] * 22}).encode()
        got = align.commit_distance("HamSCI/hf-timestd", "4595c00", "5c8196d",
                                    urlopen=FakeUrlopen({url: body}))
        self.assertEqual(got, {"ahead": 20, "behind": 0, "files": 22})

    def test_commit_distance_files_absent_is_unknown_not_zero(self):
        url = "https://api.github.com/repos/HamSCI/hf-timestd/compare/4595c00...5c8196d?per_page=1"
        body = json.dumps({"ahead_by": 20, "behind_by": 0}).encode()  # no 'files' key
        got = align.commit_distance("HamSCI/hf-timestd", "4595c00", "5c8196d",
                                    urlopen=FakeUrlopen({url: body}))
        self.assertIsNone(got["files"])

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


class DraftTests(unittest.TestCase):
    def test_draft_or_prerelease_refuses(self):
        for flag in ("draft", "prerelease"):
            meta = json.loads(release_json())
            meta[flag] = True
            fake = FakeUrlopen({align.RELEASES_API + "/latest": json.dumps(meta).encode(),
                                "https://example/manifest": MANIFEST.encode()})
            with self.assertRaises(align.LookupError_) as cm:
                align.fetch_release(urlopen=fake)
            self.assertIn("not blessed", str(cm.exception))


def mv(name, live, target, note=""):
    return align.Item(name, "move", live, target, note)


class ClassifyTests(unittest.TestCase):
    def anc(self, table):
        return lambda name, a, b: table.get((name, a, b), False)

    def test_forward(self):
        out = align.classify([mv("hf-tec", "a1", "b2")], self.anc({("hf-tec", "a1", "b2"): True}))
        self.assertEqual(out[0].status, "forward")

    def test_ahead_is_left(self):
        out = align.classify([mv("sigmond", "c3", "b2")], self.anc({("sigmond", "b2", "c3"): True}))
        self.assertEqual(out[0].status, "ahead")
        self.assertIn("--allow-rollback", out[0].note)

    def test_diverged(self):
        out = align.classify([mv("x", "a1", "b2")], self.anc({}))
        self.assertEqual(out[0].status, "diverged")

    def test_unknown_ancestry_refuses(self):
        out = align.classify([mv("x", "a1", "b2")], lambda *a: None)
        self.assertEqual(out[0].status, "refuse")

    def test_radiod_forward_keeps_restart_note(self):
        note = "radiod rebuild is Plan 2b — --apply will not move it"
        out = align.classify([mv(align.RADIOD, "a1", "b2", note)],
                             self.anc({(align.RADIOD, "a1", "b2"): True}))
        self.assertEqual(out[0].note, note)

    def test_non_move_items_pass_through(self):
        cur = align.Item("hf-timestd", "current", "5c8196d", "5c8196d")
        self.assertEqual(align.classify([cur], lambda *a: True), [cur])


if __name__ == "__main__":
    unittest.main()
