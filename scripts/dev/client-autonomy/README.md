# client-autonomy

Installs each HamSCI client into a clean Debian 13 container with systemd as
init and no sigmond, then reports what works. It answers one question: does a
client run without the orchestrator?

## Run

```
scripts/dev/client-autonomy/check.sh [--keep] <client> [<client> ...]
REPORT=/path/report.md scripts/dev/client-autonomy/check.sh gpsdo-monitor
UV_PYTHON_INSTALL_DIR=/opt/uv/python scripts/dev/client-autonomy/check.sh psk-recorder   # second pass
```

Run as mjh on the devbox with rootless podman. Clients: wspr-recorder,
psk-recorder, meteor-scatter, mag-recorder, hf-timestd, codar-sounder, hf-tec,
superdarn-sounder, hamsci-physics, gpsdo-monitor, hs-uploader.

Environment: `REPOS` (default `~/hamsci/repos`), `REPORT` (default
`~/hamsci/tasks/2026-10-29-client-autonomy-report.md`), `SETTLE` (seconds to wait
before judging units, default 30), `INSTALL_TIMEOUT`.

The script prints one markdown row per client and appends it to `REPORT`.
Install tails, journals and `enable` output go in `<REPORT minus .md>-logs/`.
It exits 0 when it ran to the end, whatever the clients did.

## What it measures

For each client: it copies the client and the siblings its `pyproject.toml`
names under `[tool.uv.sources]` from the LOCAL checkouts (unpushed commits
included; no `.git`, no venvs) into `/opt/git/sigmond/<name>`. It copies no
sigmond. It runs the installer, then records:

- `install`: the installer's exit code.
- `sigmond in venv`: whether `import sigmond` works in the client's venv (expected: no).
- `writer`: whether `hs_uploader` imports, and `hs_uploader.sink.sink_path()` and
  `Writer.from_env(...).is_noop` (a property), run as root in the venv.
- `units`: every unit in `deploy.toml` (`units`, `optional_units`, `templated_units`
  with instance `autonomy`), enabled and started, judged after `SETTLE` seconds with
  `is-active`, `Result=` and the exit status. Status 226 reads as a missing path; 203
  as a binary that cannot exec; 217 as a failed `User=` switch.

hamsci-physics has no installer; the harness runs its `deploy.toml` `[build].steps`
and says so in the row. hf-timestd needs its setup wizard answered; the harness
passes the wizard's env variables and re-runs the installer.

## What it does not test

No radiod and no RTP stream: a client that exits for want of radiod or config shows
as such. Feeding a recorded stream comes later (spec section 9). No hardware (mag,
GPSDO). No upgrade path, no second install. The writer probe runs as root, not as the
service user.

## How systemd runs as PID 1 rootless

Three things were needed on this host (podman 5.4.2, cgroup v2, login shell without
a delegated cgroup):

1. Run every podman call as `systemd-run --user --scope podman ...`. A login shell's
   cgroup belongs to root, so crun cannot create the container cgroup inside it
   (`init` exits 255 without output; `exec` says "Permission denied"). The user
   manager's cgroup (`user@UID.service`) is delegated to the user, and the scope
   lives there.
2. `--cgroup-manager=cgroupfs`. With the systemd manager, `podman build` RUN steps
   fail with "sd-bus call: Interactive authentication required".
3. `--cap-add=SYS_ADMIN` on `podman run --systemd=always`. Hardened units
   (ProtectSystem and the like) make systemd keep CAP_SYS_ADMIN across the `User=`
   switch; without the capability they die at step USER (217). The capability holds
   only inside the container's user namespace.

The harness filters podman's "no systemd user session" warnings; they do no harm.

## Shims in the image (container limits)

The Containerfile adds `udev` and `cron` to the brief's package list (installers
write udev rules and `/etc/cron.d` files; real hosts have both), keeps the apt lists
so the installers' `apt-get install` works, and wraps `udevadm control|trigger|settle`
to succeed (no udevd without `/sys` write access).

## Two passes

The first pass runs the installers as shipped. A defect in the installers' inline
uv fallback (the interpreter lands under `/root`) makes most services unable to start,
which hides everything behind it. The second pass sets `UV_PYTHON_INSTALL_DIR=/opt/uv/python`
and shows what is left.

## Cleanup

Containers are removed on exit unless `--keep` (a kept container is named
`autonomy-<client>-<pid>`; remove it with `systemd-run --user --scope podman
--cgroup-manager=cgroupfs rm -f <name>`). The image `hamsci-autonomy:trixie` takes
644 MB, of which 124 MB is the Debian base.
