# The minimal client skeleton

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond 55b9e68 on 2026-08-23 — walk-through fixes (live DASI002 + code/docs)
> **Canonical for:** the copyable Tier-1 scaffold (nothing else — the narrative is [becoming-a-client.md](../becoming-a-client.md))

Six files that sigmond will accept as a client. They are documentation
scaffolding: nothing in sigmond installs them, nothing imports them, and no
test collects them. MIT-licensed — copy them and delete this README.

Read [becoming-a-client.md](../becoming-a-client.md) first; this directory is
its worked scaffold.

| File | What it is | Contract |
|---|---|---|
| [`deploy.toml`](deploy.toml) | the manifest sigmond discovers you by — name, build, install steps, units | §5 |
| [`my-recorder@.service`](my-recorder@.service) | the templated unit; `%i` is the reporter id | §4 |
| [`my_recorder/cli.py`](my_recorder/cli.py) | `version` / `inventory` / `validate` / `daemon` / `config show` | §3, §14 |
| [`config/help.toml`](config/help.toml) | the wizard sidecar, with the three-tier audit as comments | ADD-A-CLIENT §8 |
| [`pyproject.toml`](pyproject.toml) | ordinary packaging — but `[project.scripts]` is what makes `deploy.toml`'s `produces` a real console script | — |
| `README.md` | this file |

What is **not** here: `scripts/install.sh`, tests, and a config template.
The first of those is **required** — CLIENT-CONTRACT §5's *standalone-safe
requirement* says a client must also ship an equivalent `./install.sh` (or
`make install`) driven by `deploy.toml`, so that someone installing without
sigmond gets the same layout, and [ADD-A-CLIENT §6](../../ADD-A-CLIENT.md#6-catalog-entry-often-optional)
puts that path in the catalog as `install_script`. The skeleton cannot guess
it for you; write it before you hand the repo to a station.

## Copy it

From a sigmond checkout, on your laptop or the devbox:

```bash
cp -r docs/scientist/skeleton ~/my-recorder
cd ~/my-recorder
rm README.md
git init && git add -A && git commit -m "initial client skeleton"
```

Then rename. `my-recorder` is the client name, and one name is reused as the
systemd unit stem, the `/etc/` directory, the `/usr/local/bin` symlink, the
`/var/lib/` directory and the catalog key — so a single careful rename does
the whole job:

```bash
grep -rl my-recorder . | xargs -r sed -i 's/my-recorder/your-client/g'
grep -rl my_recorder . | xargs -r sed -i 's/my_recorder/your_client/g'
mv my-recorder@.service your-client@.service
mv my_recorder your_client
```

Use a **hyphenated** name for the client and the matching **underscored**
name for the Python package — that is the convention every client in the
fleet follows. One thing to get right that nothing will tell you: sigmond
resolves a client by its **install-directory** name (`/opt/git/sigmond/<dir>/`),
or by `[client] name` if you declare one — *not* by `[package] name`
(`lib/sigmond/discover.py`). Since `smd component add` derives the directory
from your git URL, the repo name, the `/usr/local/bin` symlink and the unit
stem all have to be the same word.

Three values in `my_recorder/cli.py` are marked `CHANGE ME` and are wrong for
your station until you change them: the reporter id, the radiod status name,
and the callsign.

## Run the contract verbs by hand

You do not need a station, a venv, or sigmond — this is stdlib Python 3.11+.
Every block below is real output from `python3 docs/scientist/skeleton/…` in a
sigmond checkout on 2026-08-23. (`deploy_toml_path` reports the checkout it
was run from; installed at `/opt/git/sigmond/my-recorder/` it reports that
instead.)

```console
$ python3 docs/scientist/skeleton/my_recorder/cli.py version
{
  "client": "my-recorder",
  "version": "0.1.0",
  "contract_version": "0.8",
  "python": "3.13.5",
  "deploy_toml_path": "/home/…/sigmond/docs/scientist/skeleton/deploy.toml"
}
```

`inventory --json` is the payload sigmond builds its whole view of you from —
the instance list, the frequencies, the disk budget, and the timing claim:

```console
$ python3 docs/scientist/skeleton/my_recorder/cli.py inventory --json | python3 -m json.tool
{
    "client": "my-recorder",
    "version": "0.1.0",
    "contract_version": "0.8",
    "config_path": null,
    "instances": [
        {
            "instance": "MY-RECORDER-1",
            "radiod_id": "DASI002-status.local",
            "radiod_status_dns": "DASI002-status.local",
            "host": "localhost",
            "required_cores": [],
            "preferred_cores": "worker",
            "frequencies_hz": [
                14110000
            ],
            "ka9q_channels": 1,
            "data_destination": null,
            "disk_writes": [
                {
                    "path": "/var/lib/my-recorder",
                    "mb_per_day": 8294.4,
                    "retention_days": 0
                }
            ],
            "uses_timing_calibration": false,
            "provides_timing_calibration": false,
            "timing_authority_applied": null,
            "data_path": {
                "kind": "radiod-ka9q-python",
                "radiod_id": "DASI002-status.local"
            },
            "deploy_toml_path": "/home/…/sigmond/docs/scientist/skeleton/deploy.toml"
        }
    ],
    "deps": {
        "pypi": [
            {
                "name": "ka9q-python",
                "version": "3.25.2"
            }
        ]
    },
    "issues": []
}
```

`validate --json` answers "is this client fit to run right now?" and is the
one verb allowed to exit nonzero:

```console
$ python3 docs/scientist/skeleton/my_recorder/cli.py validate --json ; echo "exit=$?"
{
  "ok": true,
  "issues": []
}
exit=0
```

Break the envelope and it says so, by instance, with a nonzero exit — this is
what an operator will read out of `smd status` when your client is unhappy.
Note the severity: sigmond prints an issue as an **error** only when the
string is exactly `fail`, and as a warning for anything else — so `"error"`
would reach the operator looking like a warning (`bin/smd`):

```console
$ printf '[capture]\nsample_rate = 12345\nfrequency_hz = 250000000\n' > /tmp/bad.toml
$ python3 docs/scientist/skeleton/my_recorder/cli.py --config /tmp/bad.toml validate --json ; echo "exit=$?"
{
  "ok": false,
  "issues": [
    {
      "severity": "fail",
      "instance": "MY-RECORDER-1",
      "message": "frequency 250000000 Hz is outside the RX888 front end's 10000-64000000 Hz span"
    },
    {
      "severity": "fail",
      "instance": "MY-RECORDER-1",
      "message": "sample_rate 12345 is not a positive multiple of 200 Hz; radiod will not serve it"
    }
  ]
}
exit=1
```

It checks four things, chosen because each one silently produces nothing:
the frequency is inside the front end's span, the sample rate is a multiple of
200 Hz, the callsign is set, and the reporter id matches §19.1's
`[A-Z0-9][A-Z0-9-]*[A-Z0-9]` — that last one because the same string becomes
the systemd instance, the config stem, the env-file stem and two directories,
so a bad id is wrong in five places at once.

⚠ **The frequency check uses the wider of the two spans.** `RX888_LOW_HZ` /
`RX888_HIGH_HZ` are the receiver's 10 kHz – 64 MHz
(`ka9q-radio/docs/SDR/rx888.md`), while radiod's *live* front-end filter is
narrower — 15 kHz – 60.912 MHz on AC0G/B4, 2026-08-23
([station-capabilities.md §Frequency and bandwidth](../station-capabilities.md#frequency-and-bandwidth--what-radiod-will-hand-you)).
So a config can validate green on a frequency your station's front end will not
serve. Tighten the two constants to your own station's `fe filt low`/`fe filt
high` when you copy the skeleton; the comment above them says the same thing.

`inventory`, by contrast, **must never** exit nonzero — sigmond's `installed`
flag depends on it. Point it at a config that is not there and it degrades
instead of failing:

```console
$ python3 docs/scientist/skeleton/my_recorder/cli.py --config /nope/nope.toml inventory --json ; echo "exit=$?"
…
  "issues": [
    {
      "severity": "fail",
      "instance": null,
      "message": "config /nope/nope.toml does not exist; reporting built-in defaults"
    }
  ]
}
exit=0
```

The wizard surface, which `smd config` and the TUI drive:

```console
$ python3 docs/scientist/skeleton/my_recorder/cli.py config show --json --defaults
{
  "instance": {
    "reporter_id": "MY-RECORDER-1"
  },
  "radiod": {
    "status_dns": "DASI002-status.local"
  },
  "capture": {
    "frequency_hz": 14110000,
    "preset": "iq",
    "sample_rate": 12000,
    "encoding": "f32le",
    "out_dir": "/var/lib/my-recorder"
  },
  "station": {
    "callsign": "N0CALL"
  }
}
```

And the daemon — a stub loop that logs to **stderr** and exits cleanly on
SIGTERM, which is the contract-relevant part of it:

```console
$ timeout 3 python3 docs/scientist/skeleton/my_recorder/cli.py daemon MY-RECORDER-1
2026-08-23 16:28:40,088 INFO    my-recorder: starting: instance=MY-RECORDER-1 14.110000 MHz preset=iq rate=12000 encoding=f32le out=/var/lib/my-recorder
2026-08-23 16:28:40,088 INFO    my-recorder: tick 1 — replace this with your capture loop (out_dir=/var/lib/my-recorder, present=False)
2026-08-23 16:28:43,058 INFO    my-recorder: stopped cleanly after 1 tick(s)
```

Note where those lines went. Ask for verbose logging on a contract verb and
stdout is still nothing but JSON — that is the §3 stdout-cleanliness rule, and
it is the single most common way a new client fails to appear in `smd status`:

```console
$ python3 docs/scientist/skeleton/my_recorder/cli.py -v inventory --json 2>/dev/null | python3 -m json.tool > /dev/null && echo "stdout is clean JSON"
stdout is clean JSON
```

## Or install it the way a station does

`deploy.toml`'s build step is an editable install into a venv; the console
script it declares under `produces` comes from `pyproject.toml`. Proven on the
devbox, 2026-08-23, in a throwaway venv (since deleted):

```console
$ cp -r docs/scientist/skeleton/. /tmp/…/pkg/
$ python3 -m venv /tmp/…/venv && /tmp/…/venv/bin/pip install -e /tmp/…/pkg
$ /tmp/…/venv/bin/my-recorder version
{
  "client": "my-recorder",
  "version": "0.1.0",
  "contract_version": "0.8",
  "python": "3.13.5",
  "deploy_toml_path": "/tmp/…/pkg/deploy.toml"
}
$ /tmp/…/venv/bin/my-recorder validate --json ; echo "exit=$?"
{
  "ok": true,
  "issues": []
}
exit=0
```

On a station the venv is `/opt/git/sigmond/<client>/venv/` and the symlink to
`/usr/local/bin/` is an install step — `smd install` does both.

## What is stubbed

| Stub | Replace it with |
|---|---|
| `run_daemon()` — a 10-second tick loop | your capture loop; the Tier-0 recipe in [capture-quickstart.md](../capture-quickstart.md) drops straight in |
| `DEFAULT_CONFIG` in `cli.py` | a config template rendered to `/etc/<client>/` by a `kind = "render"` install step ([ADD-A-CLIENT.md §2](../../ADD-A-CLIENT.md#2-deploytoml--the-sigmond-manifest)) |
| `config init` / `config edit` | the interactive halves, then uncomment `[contract.config]` in `deploy.toml` |
| `deps.pypi` version, and `pyproject.toml`'s empty `dependencies` | whatever ka9q-python your client is tested against — both lists, they serve different readers |
| no `install.sh` | the standalone installer §5 requires (see above) — it must also create the `myrec` service user/group and `/etc/my-recorder/env/`, the same setup `deploy.toml`'s `mkdir`/`owner` steps do for a fleet install |
| `[[hs_uploader.pipeline]]` — absent | a pipeline declaration, if your rows should leave the station ([becoming-a-client.md](../becoming-a-client.md#shipping-it-upstream)) |
| `[client_features]` — absent | one block per TUI screen you want to appear on ([ADD-A-CLIENT.md §5](../../ADD-A-CLIENT.md#5-client_features--tui-registration-drop-in)) |
| `[[deps.pypi]] ka9q-python = "3.25.2"` in `deploy.toml` | a version actually on PyPI (**3.22.0** is; 3.25.2 is not) — or the `git+https://github.com/HamSCI/ka9q-python@v3.25.2` checkout install [capture-quickstart.md](../capture-quickstart.md) uses instead |

## License

MIT (`SPDX-License-Identifier: MIT`, stated in every file). Copy, modify, and
relicense your derived client however you like.
