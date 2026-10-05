"""Bring-up orchestration engine (install-orchestration design, Phase B).

Turns a catalog ``Profile`` into an ordered, conditional sequence of bring-up
``Step``s — install -> per-client config interview -> background FFT wisdom ->
start, with checkpoints between.  Plan-building (:func:`build_plan`) is pure and
unit-testable; execution lives in ``smd`` (``cmd_bringup``) so this module stays
free of subprocess/TTY/root concerns and the TUI can render a plan before running
it.

See docs/install-orchestration-design.md for the staged model this implements.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# FFT wisdom artifact radiod imports at startup; Stage 4 waits on it (local).
WISDOM_FILE = Path('/etc/fftw/wisdomf')

# Settle inserted after each radiod-bound client starts, so it finishes
# provisioning its channels before the next client contends for radiod's
# control plane.  Simultaneous provisioning starves radiod and yields 0
# channels (see docs/install-orchestration-design.md / greenfield notes).
CLIENT_STAGGER_S = 20

# Stage labels (also the progress-group headers the executor prints).
STAGE1 = 'Stage 1 — radiod + host tuning'
STAGE2 = 'Stage 2 — hf-timestd (timing authority)'
STAGE3A = 'Stage 3a — radiod-bound clients'
STAGE3B = 'Stage 3b — independent clients'
STAGE4 = 'Stage 4 — start + verify'

# hf-timestd gets its own stage (timing authority, before consumers); the
# independent set runs on the 3b track.  Both are therefore skipped by the
# radiod-bound 3a loop.
#
# ⛔ INDEPENDENT MEANS "DOES NOT USE RADIOD", AND THAT DECIDES WHEN IT STARTS.
# rob, 2026-09-30: "the ones independent of radiod should start as soon as the
# VM is up ... only things that use the radio services" wait for it.
#
#   mag-recorder    talks to an RM3100 over a USB I2C adapter
#   station-web     serves reports and documentation off the filesystem
#   hamsci-physics  post-processes files already on disk (timer-driven)
#   gmag-webui      the magnetometer dashboard; reads mag-recorder's feed
#
# ⛔ gmag-webui was found still staggered by RUNNING the new plan on a live
# station, not by reading it: it is not in the profile's `clients` tuple (it
# arrives via the catalog), so a static read of the plan missed it.  It is the
# magnetometer page -- the exact thing the operator is waiting on -- and it
# touches radiod not at all.
#
# None of them opens a radiod channel, so none of them can starve radiod's
# control plane -- which is the ONLY thing CLIENT_STAGGER_S protects.  They
# were nevertheless queued behind the whole staggered set, because the start
# phase was one flat ordered list.  On a dasi2 install that is four staggered
# clients plus a wait-for-streaming ahead of them: rob watched a healthy
# station show no station-web and no magnetometer for about fifteen minutes
# after radiod was up, with nothing wrong (v3.63, AI6VN-PM, 2026-09-30).
#
# ⚠ gpsdo-monitor and igmp-querier are NOT here on purpose.  They look
# independent and are not: radiod's transport is multicast (igmp-querier) and
# its sample clock is disciplined against the GPSDO.  They belong to the
# radiod stack and start with it.
_TIMING_AUTHORITY = 'hf-timestd'
_INDEPENDENT = frozenset({'mag-recorder', 'station-web', 'hamsci-physics',
                          'gmag-webui'})

# Clients that take a per-reporter instance (`<client>@<reporter-id>`).  When a
# reporter id is supplied, bring-up creates + enables the instance instead of
# starting a base-config unit.  wspr-recorder, psk-recorder, and
# meteor-scatter all seed a complete per-instance config from their shared
# config (the [[radiod]] block + bands), so `<client>@<reporter>` runs
# directly (sigmond#16; meteor-scatter field-validated on AC0G-B4
# 2026-07-29/30).  (Multi-radiod *source selection* for psk via `sources
# apply` is still deferred, but irrelevant to the single-radiod
# reporter-keyed path bring-up uses.)
_REPORTER_KEYED = frozenset({'wspr-recorder', 'psk-recorder', 'meteor-scatter'})


@dataclass
class Step:
    """One bring-up action.  ``argv`` is what the executor runs (an ``smd`` or
    ``systemctl`` invocation); empty for ``note``/``wait-wisdom`` steps that the
    executor handles specially."""
    stage: str
    label: str
    kind: str                       # install|config|tune|wisdom|start|checkpoint|note|wait-wisdom|wait-streaming
    argv: list = field(default_factory=list)
    hard: bool = False              # checkpoint: abort the run on failure
    background: bool = False         # fire-and-don't-wait
    check: str = ''                 # checkpoint probe id (see executor._probe)
    settle_s: int = 0               # executor sleeps this long after the step
    may_fail: bool = False          # non-fatal BY DESIGN: a non-zero exit is not
                                    # a failed bring-up (e.g. sdr-recover on a
                                    # hub that cannot switch power)


@dataclass
class Plan:
    profile: str
    local_radiod: bool
    remote_status_dns: Optional[str]
    steps: list = field(default_factory=list)
    # True when the plan defers the radio half because no SDR was on the bus.
    # The executor then exits PARTIAL (rc 3), never "done" -- see build_plan.
    sdr_absent: bool = False


def build_plan(profile, *, local_radiod: bool,
               remote_status_dns: Optional[str] = None,
               smd: str = 'smd', with_optional: bool = False,
               non_interactive: bool = False, skip=frozenset(),
               dormant=frozenset(), no_config=frozenset(),
               reporter: Optional[str] = None,
               sdr_absent: bool = False) -> Plan:
    """Pure: a profile + radiod locality -> the ordered Step list.

    ``local_radiod`` gates the entire radiod stack (infra, ka9q-radio, tuning,
    radiod config, FFT wisdom).  When False the host binds a remote radiod and
    none of that — nor the wisdom wait — applies.  ``mag-recorder`` is emitted
    on the independent 3b track regardless of locality.

    ``dormant`` names hardware-gated components whose device is absent: they
    still install + configure + enable (so they light up when the hardware is
    later attached and `smd start` — which skips dormant components — runs
    again), but their "configured" checkpoint is SOFT, so a config step that
    can't fully complete without the device doesn't abort the whole bring-up
    (docs/install-redesign.md §3).  Distinct from ``skip`` (excluded entirely).

    ``no_config`` names components with NO config contract — a Deno app or a
    static server, not a contract-conformant client.  They install, enable and
    start like anything else, but get no config interview and no ``configured:``
    checkpoint, because neither can succeed: there is nothing to interview and
    no config for the probe to find.  ``ka9q-web`` has always had this
    treatment implicitly, by sitting in ``local_radiod_infra`` rather than in
    ``clients``; ``gmag-webui`` sits in ``clients`` and needs it named.

    ``sdr_absent`` (local radiod only) means no SDR was on the USB bus when
    bring-up began.  mjh, 2026-10-04, from Fargo: never write "done" when not
    done; offer an abort, but by default carry on without the radio.  So the
    plan still INSTALLS everything, and runs the independent track whole, but
    defers every step that needs radiod's configuration: radiod's own config
    and its hard checkpoint, host tuning (``smd apply`` starts radiod), FFT
    wisdom, the ka9q-web unit, every radiod-bound client's config, instance
    and upload flag, the streaming waits and the radiod-bound starts.  The
    radio half is installed WITHOUT enabling it in topology, so Stage 4's
    catch-all ``smd start`` cannot start a recorder that has no config.  The
    plan is marked partial; re-running bring-up once the card is present
    (firstrun does that on its arrival) completes it.
    """
    deferring = bool(sdr_absent and local_radiod)
    steps: list = []

    # Pre-create the shared SQLite sink with group-writable perms BEFORE any
    # component install: hs-uploader bootstraps during the install steps and,
    # if it wins the creation race, leaves a 0-byte 0644 sink.db that the
    # recorder users (group sigmond) can't write — every decode then drops
    # silently while all health checks stay green (sigmond#42; hit live on
    # the B4 appliance bring-up 2026-07-27).  chmod/chgrp run unconditionally
    # so a previously-raced file is repaired too.  Idempotent.
    steps.append(Step(STAGE1, 'provision shared sink.db (group-writable, '
                              'pre-empts writer race — sigmond#42)', 'tune',
                      argv=['bash', '-c',
                            'install -d -m 2775 -o root -g sigmond /var/lib/sigmond'
                            ' && { [ -f /var/lib/sigmond/sink.db ] || '
                            'install -m 664 -o root -g sigmond /dev/null '
                            '/var/lib/sigmond/sink.db; }'
                            ' && chgrp sigmond /var/lib/sigmond/sink.db'
                            ' && chmod 664 /var/lib/sigmond/sink.db']))

    activated: list = []    # components this plan enables (start-eligible)
    deferred: list = []     # radio half installed --no-enable (no SDR)

    def install(stage: str, comp: str, enable: bool = True) -> None:
        # Enable in topology BEFORE installing.  `smd install --components` builds
        # the component but does NOT flip topology `enabled=true` (only
        # `smd install --profile` does, via set_component_enabled).  Without this
        # the whole station stays `enabled=false`: `smd status`/`validate` see
        # nothing "declared" and the Stage-4 `smd start` steps have nothing to
        # start (radiod is left disabled/inactive).  `smd enable` is idempotent,
        # so re-running bring-up is a no-op here.
        if not enable:
            # Deferred radio half (no SDR): build it, leave it disabled, so no
            # catch-all start can launch it before it is configured.
            deferred.append(comp)
            steps.append(Step(stage, f'install {comp} (not enabled — radio deferred)',
                              'install',
                              argv=[smd, 'install', '--components', comp, '--yes',
                                    '--no-enable']))
            return
        activated.append(comp)
        steps.append(Step(stage, f'enable {comp}', 'enable',
                          argv=[smd, 'enable', comp]))
        steps.append(Step(stage, f'install {comp}', 'install',
                          argv=[smd, 'install', '--components', comp, '--yes']))

    def configure(stage: str, client: str) -> None:
        argv = [smd, 'config', 'init', client]
        label = f'configure {client}'
        # Client config interviews default to --non-interactive: each client's
        # own wizard (e.g. psk-recorder's whiptail) can't render/read input
        # inside bring-up's nested terminal, and sigmond already supplies the
        # essentials (callsign / grid / radiod) via the env — the operator
        # fine-tunes later with `smd config edit <client>`.  radiod is the
        # exception: it's sigmond's own inline text wizard (works here, and the
        # operator sets the antenna), so it stays interactive unless the whole
        # bring-up was invoked with --non-interactive.
        # ...and also when there is no terminal to interview on.  Without
        # this the radiod step exits 2 in any non-TTY bring-up and the station
        # ends up with NO radiod instance at all — which is strictly worse
        # than defaults the operator refines later with `smd config edit
        # radiod` (AI6VN, v3.40, 2026-09-17: fresh install, bring-up run over
        # ssh, "step exited 2: smd config init radiod", zero radiod@ units).
        if non_interactive or client != 'radiod' or not sys.stdin.isatty():
            argv.append('--non-interactive')
        if non_interactive:
            label += ' (non-interactive)'
        steps.append(Step(stage, label, 'config', argv=argv))

    def checkpoint(stage: str, label: str, check: str, hard: bool = False) -> None:
        steps.append(Step(stage, f'checkpoint: {label}', 'checkpoint',
                          check=check, hard=hard))

    # --- Stage 1: radiod stack (local only) ---
    if deferring:
        steps.append(Step(STAGE1, 'NO SDR on the USB bus — installing the radiod '
                                  'stack but deferring its configuration, host '
                                  'tuning, FFT wisdom and every radiod-bound '
                                  'client until the SDR appears', 'note'))
        for infra in profile.local_radiod_infra:
            if infra in skip:
                continue
            install(STAGE1, infra, enable=False)
        install(STAGE1, 'ka9q-radio', enable=False)
        if with_optional:
            for opt in profile.optional:
                install(STAGE1, opt, enable=False)
    elif local_radiod:
        for infra in profile.local_radiod_infra:
            if infra in skip:           # hardware-gated infra absent (e.g. no GPSDO)
                continue
            install(STAGE1, infra)
        install(STAGE1, 'ka9q-radio')
        if with_optional:
            for opt in profile.optional:
                install(STAGE1, opt)
        # The RX-888 must be on the bus before `configure radiod`, not merely
        # before radiod STARTS: config init probes the USB bus to detect the
        # SDR, and a miss there fails the hard 'radiod configured' checkpoint
        # below and aborts the whole bring-up.  Placing this in stage 4 (as it
        # first was) is useless -- bring-up never gets that far.
        #
        # AI6VN, v3.42, 2026-09-17: the card was NOT latched, it was not ready
        # yet.  An RX-888 enumerates first as its FX3 bootloader and only
        # becomes 04b4:00f1 once firmware is loaded, and bring-up installs
        # that firmware and reloads udev EARLIER IN THIS SAME RUN -- so the
        # card appeared moments after config init had already given up.  The
        # gate therefore waits for the card before resorting to a power cycle.
        #
        # Non-fatal by design: a station with no local card, or a hub that
        # cannot switch power, still brings up everything else.
        steps.append(Step(STAGE1, 'ensure the RX-888 is on the bus (wait for a '
                                  'slow FX3, recover a latched card)', 'tune',
                          argv=['/usr/local/sbin/sigmond-sdr-recover',
                                '--ensure-present'], may_fail=True))
        configure(STAGE1, 'radiod')
        # Tune AFTER `configure radiod`, never before: `smd apply` writes
        # per-INSTANCE affinity drop-ins and enables + starts any radiod
        # instance it finds, so running it pre-config acts on a stale or
        # pre-existing instance (a golden-image station carries one) before
        # the operator has even named the station — rob hit this live on the
        # first USB-image bring-up, 2026-07-25.  Configuration and naming
        # first; affinity/tuning applied to the configured result.
        steps.append(Step(STAGE1, 'apply host tuning (affinity / governor / rmem)',
                          'tune', argv=[smd, 'apply']))
        steps.append(Step(STAGE1, 'launch FFT wisdom planner', 'wisdom',
                          argv=['systemctl', 'start', '--no-block',
                                'sigmond-wisdom.service'], background=True))
        checkpoint(STAGE1, 'radiod configured', check='radiod-configured', hard=True)
        if 'ka9q-web' in profile.local_radiod_infra and 'ka9q-web' not in skip:
            # ka9q-web builds with the Stage-1 infra above, but its unit-writer
            # refuses to install ka9q-web.service until coordination.env carries
            # SIGMOND_RADIOD_STATUS — which `configure radiod` only just wrote.
            # Re-run the (idempotent, sha-compared) install so the unit exists
            # before Stage 4 tries to start it; without this every greenfield
            # bring-up ends with "Unit ka9q-web.service not found".
            steps.append(Step(STAGE1, 'install ka9q-web unit (post-radiod-config)',
                              'install',
                              argv=[smd, 'install', '--components', 'ka9q-web',
                                    '--yes']))
    else:
        dns = remote_status_dns or 'auto-discover'
        steps.append(Step(STAGE1, f'using remote radiod ({dns}) — skipping radiod '
                          'stack, gpsdo-monitor, and FFT wisdom', 'note'))

    # --- Stage 2: hf-timestd (timing authority; radiod-bound) ---
    if _TIMING_AUTHORITY in profile.clients and _TIMING_AUTHORITY not in skip:
        install(STAGE2, _TIMING_AUTHORITY, enable=not deferring)
        if _TIMING_AUTHORITY not in no_config and not deferring:
            configure(STAGE2, _TIMING_AUTHORITY)
            checkpoint(STAGE2, 'hf-timestd configured',
                       check=f'configured:{_TIMING_AUTHORITY}',
                       hard=_TIMING_AUTHORITY not in dormant)

    # --- Stage 3a: radiod-bound spot clients ---
    for client in profile.clients:
        if (client == _TIMING_AUTHORITY or client in _INDEPENDENT
                or client in skip):
            continue
        install(STAGE3A, client, enable=not deferring)
        if deferring:
            continue    # config, instance and upload flag need radiod's status
        # ⛔ Both of these guards were missing here while the Stage-3b loop
        # below carried them, and the two loops disagreeing IS the defect:
        # gmag-webui rides this track, so a Fargo station with no
        # magnetometer hard-aborted its whole bring-up on a dashboard it was
        # never going to use (AC0G-ND, 2026-09-02).  cmd_bringup had already
        # put it in `dormant`; this loop threw that away.
        if client not in no_config:
            configure(STAGE3A, client)
            checkpoint(STAGE3A, f'{client} configured',
                       check=f'configured:{client}',
                       hard=client not in dormant)
        # Create the per-reporter instance from the base config (status + bands
        # the config step just wrote).  `instance add` only scaffolds the files;
        # Stage 4 enables/starts it (staggered).  Without a reporter id the
        # client falls back to its base-config start.
        if reporter and client in _REPORTER_KEYED:
            # --force makes re-add idempotent (create only missing files, leave
            # existing ones) so re-running bring-up doesn't error on an instance
            # that's already scaffolded.
            steps.append(Step(STAGE3A,
                              f'create reporter instance {client}@{reporter}',
                              'enable',
                              argv=[smd, 'admin', 'instance', 'add', '--force',
                                    client, reporter]))
            # Enable the instance's upstream upload — the flag itself
            # (WSPR/PSK_USE_HS_UPLOADER) plus, via DELIVERY_ON_ENABLE, the
            # standalone-correct PSK_DELIVERY_PIPELINES=direct.  This was a
            # manual image-builder step; when skipped, psk-recorder falls to
            # the server-merge runtime default and PSKReporter silently gets
            # nothing while every local health check stays green (B4
            # appliance bring-up, 2026-07-27).  Idempotent.
            steps.append(Step(STAGE3A,
                              f'enable upload {client}@{reporter}',
                              'config',
                              argv=[smd, 'config', 'upload', client,
                                    reporter, '--on']))

    # Provision the shared hs-uploader watermark dir.  Recorder units list
    # /var/lib/hs-uploader in ReadWritePaths under ProtectSystem=strict, so it
    # MUST exist before they start or systemd aborts the sandbox with
    # 226/NAMESPACE.  It's normally created by hs-uploader/install.sh, which
    # bring-up doesn't invoke (hs-uploader is a source-only sibling), so create
    # it here — root:sigmond, setgid + group-writable like /var/lib/sigmond, so
    # every HamSCI recorder user (in the sigmond group) can write.  `install -d`
    # is idempotent.
    #
    # ⛔ AHEAD OF STAGE 3b, because that stage now STARTS clients as it installs
    # them.  While every start lived in Stage 4 this sat comfortably above them;
    # moving the independents earlier moved them above this, and mag-recorder
    # would have hit 226/NAMESPACE on a directory nothing had created yet.
    # Cheap enough to be unconditional: it is one idempotent `install -d`.
    steps.append(Step(STAGE3B, 'provision shared hs-uploader watermark dir', 'tune',
                      argv=['install', '-d', '-m', '2775', '-o', 'root',
                            '-g', 'sigmond', '/var/lib/hs-uploader']))

    # --- Stage 3b: independent clients (no radiod, no wisdom wait) ---
    for client in profile.clients:
        if client in _INDEPENDENT and client not in skip:
            install(STAGE3B, client)
            if client not in no_config:
                configure(STAGE3B, client)
                # Soft checkpoint for a dormant (hardware-absent) client: its
                # config step may not fully complete without the device, and
                # that must not abort the bring-up — it lights up when the
                # hardware is attached.
                checkpoint(STAGE3B, f'{client} configured',
                           check=f'configured:{client}',
                           hard=client not in dormant)
            # ⛔ START IT HERE, NOT IN STAGE 4.  Installed-and-configured is
            # everything this client needs; it uses no radiod, so there is
            # nothing left to wait for.  Deferring the start to Stage 4 made it
            # wait for EVERY remaining component to install first.
            #
            # Measured on a v3.64 first install (AI6VN-PM, 2026-10-01):
            #   station-web installed at log line 929, started at line 1196 --
            #   267 lines and ~5 minutes apart, idle the whole time.
            #   VM booted 00:23:43, station-web active 00:31:04 (+7m21).
            #
            # That matters because an operator reaching a station through its
            # RAC channels sees the tunnel come up long before the service
            # behind it does, so a working station reads as a broken one.
            # rob, 2026-10-01: "all my access is through the RAC channels ...
            # we want to optimise for the RAC channels to be available as soon
            # as possible."
            #
            # ⚠ Stage 4 still runs `smd start` over everything enabled, so a
            # client that fails here is retried there -- this is an EARLY start,
            # not the only one.
            steps.append(Step(STAGE3B, f'start {client} (independent — no radiod)',
                              'start',
                              argv=[smd, 'start', '--components', client]))

    # Re-render the site profile now that stages 1-3 created every client's
    # config: this pushes the PSWS station/instrument ids from
    # site-profile.toml THROUGH into each recorder's own config file — the
    # earlier pre-config render could only seed coordination (the client
    # files didn't exist yet).  Quiet no-op on hosts without a site profile
    # (legacy prompt-driven identity) and idempotent otherwise.  MUST come
    # before the manifest step, which resolves {station_id}/{instrument_id}
    # from those client configs.
    steps.append(Step(STAGE4, 'render site profile (PSWS ids into client '
                              'configs)', 'tune',
                      argv=[smd, 'config', 'render', '--if-present']))

    # Generate the single-host uploader manifest from each enabled client's
    # deploy.toml [[hs_uploader.pipeline]] declarations (identity substituted
    # from coordination + per-client configs), then enable + start the daemon.
    # Runs after clients are installed+configured (stages 1-3), so PSWS ids /
    # reporter ids exist; idempotent (a no-op restart when nothing changed).
    steps.append(Step(STAGE4, 'generate hs-uploader manifest + enable daemon',
                      'tune',
                      argv=[smd, 'admin', 'uploader', 'manifest',
                            '--write', '--enable']))

    # Heal any leftover legacy config before starting: a stale client config
    # from a prior install (e.g. the legacy `status_address` field) that
    # `config init` refused to overwrite would otherwise fail to load.  This
    # rewrites it to the canonical `status` schema and canonicalizes the radiod
    # identity in coordination.toml.  Idempotent — a no-op when nothing is
    # legacy, so it's harmless on a truly-clean host.
    steps.append(Step(STAGE4, 'migrate any legacy config to the canonical radiod '
                              'schema', 'tune',
                      argv=[smd, 'admin', 'radiod', 'migrate', '--yes']))

    # --- Stage 4: start, ORDERED so clients never provision against a cold
    # radiod.  radiod reaches systemd 'active' (forked) ~10 s — and on a cold
    # start up to ~3 min — before it logs 'rx888 running' (actually streaming);
    # a client started in that window provisions 0 channels.  So on a local
    # radiod: start the radiod stack, WAIT for streaming + a settle margin,
    # THEN start the radiod-bound clients staggered.  Independent clients (mag)
    # aren't gated on radiod.  A final idempotent `smd start` sweeps up anything
    # enabled-but-unstarted (e.g. inert rac stays inert). ---
    radiod_bound = [c for c in profile.clients
                    if c not in skip and c not in _INDEPENDENT]
    independent = [c for c in profile.clients
                   if c not in skip and c in _INDEPENDENT]

    # Independent clients already started in Stage 3b, the moment each was
    # installed and configured -- see the note there.  Stage 4's catch-all
    # `smd start` below still covers any that did not come up.

    if deferring:
        radiod_bound = []           # installed, not enabled, nothing to start
    elif local_radiod:
        steps.append(Step(STAGE4, 'wait for FFT wisdom before starting radiod',
                          'wait-wisdom'))
        radiod_stack = ['ka9q-radio'] + [i for i in profile.local_radiod_infra
                                         if i not in skip]
        steps.append(Step(STAGE4, 'start radiod + local-radiod infra', 'start',
                          argv=[smd, 'start', '--components', ','.join(radiod_stack)]))
        steps.append(Step(STAGE4,
                          'wait for radiod streaming (rx888 running) + settle',
                          'wait-streaming'))

    for client in radiod_bound:
        if reporter and client in _REPORTER_KEYED:
            # `instance enable` does systemctl enable --now on the per-reporter
            # unit (wspr-recorder@<reporter>), so it both declares and starts it.
            steps.append(Step(STAGE4, f'start {client}@{reporter} (staggered)', 'start',
                              argv=[smd, 'admin', 'instance', 'enable', client, reporter],
                              settle_s=CLIENT_STAGGER_S))
        else:
            steps.append(Step(STAGE4, f'start {client} (staggered)', 'start',
                              argv=[smd, 'start', '--components', client],
                              settle_s=CLIENT_STAGGER_S))
    if deferring:
        # ⛔ NOT a bare `smd start`.  --no-enable only declines to ENABLE the
        # radio half; it cannot disable what the image's topology already
        # enables, and the DASI golden VM ships hf-timestd, psk, meteor,
        # igmp-querier and ka9q-web enabled=true.  A bare catch-all then
        # started igmp-querier and failed on "Unit ka9q-web.service not found"
        # (v3.67 nested test, 2026-10-05 01:07Z) -- a red step in a partial
        # bring-up that had done exactly what it should.  So name what this
        # plan activated, and nothing it deferred.
        startable = [c for c in activated if c not in deferred]
        if startable:
            steps.append(Step(STAGE4, 'start the components this plan activated '
                                      '(radio half deferred)', 'start',
                              argv=[smd, 'start', '--components', ','.join(startable)]))
    else:
        steps.append(Step(STAGE4, 'start any remaining enabled components', 'start',
                          argv=[smd, 'start']))

    # hs-uploader burns its systemd start-limit during Stages 1-3: the
    # component install bootstraps the daemon before any client pipelines
    # exist, so it exits ("no pipelines built — nothing to do") and
    # crash-loops until systemd gives up.  Every later start then fails
    # with "Start request repeated too quickly" and the uploader stays
    # dead after an otherwise-green bring-up.  The manifest step above has
    # written the real pipelines by now — clear the failed state and
    # restart onto the final manifest.  Idempotent: reset-failed is a
    # no-op when healthy, restart just reloads.
    steps.append(Step(STAGE4, 'clear hs-uploader start-limit (pre-manifest '
                              'crash-loop)', 'start',
                      argv=['systemctl', 'reset-failed', 'hs-uploader.service']))
    steps.append(Step(STAGE4, 'restart hs-uploader on the final manifest',
                      'start',
                      argv=['systemctl', 'restart', 'hs-uploader.service']))

    # Now that every client is running, pin them OFF radiod's cache-pair (cores
    # 0-1).  Stage 1's `smd apply` writes the exclusion files but runs before the
    # clients exist and doesn't daemon-reexec the manager CPUAffinity, so without
    # this the recorders/web-api start on all cores, contend with radiod's USB
    # reader, and the RX888 FX3 overruns → 'rx888 has aborted' → radiod restart
    # cascade (cycle gaps, missing bands, out-of-order uploads, low output).
    # `cpu-affinity --apply` daemon-reexecs the exclusion + sets AllowedCPUs on
    # every running service; idempotent.
    if local_radiod and not deferring:
        steps.append(Step(STAGE4, 'reserve radiod cores (apply cache-pair CPU exclusion)',
                          'tune',
                          argv=[smd, 'admin', 'diag', 'cpu-affinity', '--apply']))
    checkpoint(STAGE4, 'final validate', check='validate')
    if deferring:
        steps.append(Step(STAGE4, 'PARTIAL — the radio half waits for the SDR; '
                                  'bring-up completes when it is plugged in '
                                  '(or run `smd bringup` again)', 'note'))

    return Plan(profile=profile.name, local_radiod=local_radiod,
                remote_status_dns=remote_status_dns, steps=steps,
                sdr_absent=deferring)
