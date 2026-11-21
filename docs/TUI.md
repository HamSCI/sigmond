# The sigmond TUI: an operator's guide

> **Audience:** operator, administrator
> **Status:** draft (written from the code; not yet checked on a live station)
> **Verified against:** sigmond 2318ac9 on 2026-11-21 — code
> **Canonical for:** operating sigmond's terminal interface (`smd tui`): its screens, keys, and the `smd` commands behind them

`smd tui` opens a full-screen terminal interface to the station.  It
shows what `smd status`, `smd timing`, `smd watch` and a dozen other
commands show, and it can run many of the commands that change the
station.  Each change still goes through `smd` or `systemctl`.  The TUI
gathers your choices, asks you to confirm, and then runs the command.

[COMMANDS.md](COMMANDS.md) covers the `smd` commands themselves, and
the generated [smd reference](commands/smd-reference.md) on the docs
site lists every option.  This page names the command behind each TUI
action, so you can run the same thing from a shell.

## 1. Starting it

Run it as yourself, not under `sudo`:

```
smd tui
```

The shell aliases `smdt` and `smdtui` do the same.  `smd config edit`
with no client name also still opens the TUI; smd prints a warning that
this form will go away.

smd refuses to run under `sudo` (`don't run smd under sudo — it
elevates itself`).  When a TUI action needs root, smd or `sudo` asks
for your password at that moment (section 4).

The TUI needs the Textual library.  When smd cannot import it, smd
switches to sigmond's own Python environment under
`/opt/git/sigmond/sigmond/venv`.  If that environment lacks the TUI
libraries, smd says so and suggests `smd install` to rebuild it.

On start the TUI:

- loads `topology.toml`, the catalog and `coordination.toml`;
- opens **Guided bring-up** if no component has been enabled yet, and
  **Overview** otherwise;
- runs `git fetch origin` on the sigmond checkout in the background.
  When the checkout lags `origin/main`, the header shows
  `⚠ N update(s) available` and a dialog offers **Update & Exit** or
  **Continue**.  **Update & Exit** runs `git pull --ff-only` on the
  sigmond checkout and closes the TUI.  On a fleet station, choose
  **Continue**: [operator/do-not-touch.md](operator/do-not-touch.md)
  rules out `git pull` by hand.

## 2. The layout

The window has three panels between a header and a footer.

- **Left: the navigation tree.**  Arrow keys move through it; Enter or a
  click opens a screen.
- **Centre: the active screen.**  Only one screen shows at a time.
  Opening another replaces it.
- **Right: the context panel.**  It shows a short help text for the
  current screen.

A thin splitter sits between each pair of panels.  Drag it to widen or
narrow the side panels.  The footer lists the keys that work at that
moment.

The tree groups the screens this way:

| Group | Screens |
|---|---|
| (top) | Overview |
| Installation | Guided bring-up · ① Download & install · ② Configure · ③ Enable / start / stop · PSWS enrolment |
| Monitoring | Environment · Timing & Authority · Annotation Quality · Activity · GPSDO live · ka9q-radio live · Receiver channels · RAC tunnel · Resources |
| Maintenance | Apply · Backup · Restore |
| Debugging | Logs · Verifier · Validate · Diag: net · ka9q-watch |
| Advanced (starts collapsed) | Software versions · SDR inventory · CPU affinity · CPU frequency |

## 3. Keys

### Everywhere

These keys work from any screen, unless a text field or a screen with
its own use of the key has the focus.

| Key | Opens |
|---|---|
| `o` | Overview |
| `c` | CPU affinity |
| `r` | ka9q-radio live (radiod) |
| `a` | RAC tunnel |
| `v` | Validate |
| `b` | Backup |
| `R` (shift-r) | Restore |
| `K` (shift-k) | ka9q-watch |
| `q` | Quit |

Only these nine screens have a key.  Reach the others through the tree.

Tab and shift-Tab move the focus between panels, buttons and fields.
The Textual framework supplies these and a few more keys of its own,
such as ctrl-q to quit and ctrl-p for its command palette.

While the cursor sits in a text field, letters go into the field.  So
`q` types a "q" there; it does not quit.

### On particular screens

A screen's own keys work only while the focus sits inside that screen,
for instance on its table.  With the focus in the tree, the global key
wins.  On Environment, for example, `r` reloads the manifest when the
focus sits in a table, but opens the radiod screen when the focus sits
in the tree.

| Screen | Key | Action |
|---|---|---|
| Environment | `p` | Probe all sources |
| | `m` | mDNS only |
| | `n` | NTP only |
| | `k` | KiwiSDR only |
| | `w` | ka9q-web only |
| | `v` | GNSS-VTEC only |
| | `s` | Network (SNMP) only |
| | `u` | USB SDR only |
| | `r` | Reload the manifest (`/etc/sigmond/environment.toml`) |
| Software versions | Space | Select or clear the row under the cursor |
| | `a` | Select every visible row |
| | `n` | Clear the selection |
| SDR inventory | `r` | Rescan |
| | `e` | Edit the selected device |
| | `d` | Remove the selected device from the label list |
| Enable / start / stop | Enter | Tick or untick the row under the cursor (with the cursor on the checkbox column) |

### In dialogs

| Dialog | Key | Action |
|---|---|---|
| Any confirmation | Escape | Cancel.  The focus starts on the cancel button, so a stray Enter also cancels |
| Live-output window (installs and updates) | Escape | Close, but only after the command has finished |
| Component detail, device editor, add-remote-SDR, config editor | Escape | Close without saving |

### Double-select

Several tables treat two quick selections of the same row (a
double-click, or Enter twice within about half a second) as a request
to open it:

- **Software versions**: opens the component's detail window.
- **SDR inventory**: opens the device editor.
- **GPSDO live**: opens the device's detail view.
- **ka9q-radio live**: suspends the TUI and runs ka9q-python's own
  `ka9q tui <status> --ssrc <N>` on that channel.

## 4. How the TUI runs a change

Most changes follow one pattern:

1. You press a button.  The TUI shows a confirmation dialog with the
   exact command it will run.
2. You confirm.  The TUI suspends itself and hands the terminal back.
3. The command runs in the plain terminal, so you see its own output
   and can type a password.  For an `smd` command, smd raises itself to
   root when it needs to.  For anything else, such as `systemctl`, the
   TUI puts `sudo` in front.
4. When the command exits, the TUI returns and shows the exit code.

Some actions run differently:

- **Software versions** install and update in a live-output window
  inside the TUI instead of suspending.  The window keeps its
  **Dismiss** button disabled until the command ends.
- **Guided bring-up** also streams into a window.  Before it starts, it
  checks `sudo -n true`; if sudo wants a password, the TUI suspends
  once, runs `sudo -v`, and you type it there.
- **ka9q-radio live**, **SDR inventory**, the in-TUI config editor,
  **RAC tunnel** and **Software versions → Toggle enable** change
  things without the suspend step.  Section 6 lists which of these skip
  the confirmation dialog too.

## 5. Before you change anything on a fleet station

On a station that a fleet administrator looks after, several TUI
buttons run commands that [operator/do-not-touch.md](operator/do-not-touch.md)
asks operators not to run.  The same reasons hold when the TUI runs
them for you:

- **Apply → Apply now** runs `smd apply`.
- **Software versions → Update** runs `smd component update`, and
  **Install** on `ka9q-radio` runs `smd install ka9q-radio`.
- **Configure → Edit selected…** rewrites a client's configuration.
- **SDR inventory → Save** on an RX-888 rewrites
  `/etc/radio/radiod@<name>.conf`.
- **Download & install → Refresh** runs `git pull` on the sigmond
  catalog checkout (section 7.2).
- The update dialog at start-up runs `git pull` on sigmond (section 1).

The read-only screens carry no such risk.  Use them freely.

## 6. What reads and what writes

| Screen | Changes the station? | Command or file behind it |
|---|---|---|
| Overview | No | `smd status`, `smd timing` |
| Guided bring-up | Yes | `smd bringup <profile> --non-interactive …`; `smd config edit radiod` |
| ① Download & install | Yes | `smd install <name>`; `smd install --yes`; `smd install --profile <p> --yes` |
| ② Configure | Yes | `smd admin instance add/remove/migrate`; `smd config edit <client> <reporter>`; `<client> config apply --json` |
| ③ Enable / start / stop | Yes | `sudo systemctl start/stop/restart <units>`; `smd start/stop/restart --components <names>` |
| PSWS enrolment | Yes | `smd psws enroll`; `smd psws verify`; `smd config upload <client> <instance> --on/--off` |
| Environment | No (sends network probes) | in-process code shared with `smd admin environment probe` |
| Timing & Authority | No | reads `/run/hf-timestd/authority.json` and chrony |
| Annotation Quality | No | reads `authority.json` and the running recorder units |
| Activity | No | `smd watch <target> [-v]` |
| GPSDO live | No | reads `/run/gpsdo/*.json` (`smd watch gpsdo` shows the same) |
| ka9q-radio live | Yes, without a confirmation dialog | ka9q-python, direct to radiod |
| Receiver channels | No | ka9q-python channel discovery |
| RAC tunnel | Yes | writes `/etc/sigmond/frpc.toml`; `systemctl enable/disable wd-rac` |
| Resources | No | `/proc`, file sizes, `sink.db` |
| Apply | Yes | `smd apply --dry-run`; `smd apply` |
| Backup | Writes a file in your home directory | `smd config backup` |
| Restore | Yes | `smd config restore --input <file>` |
| Logs | Only **Set level** | `journalctl --follow`; `tail -f`; `smd admin log set-level` |
| Verifier | Only **Rehabilitate** | `smd admin verifier report …`; `smd admin verifier rehabilitate` |
| Validate | No | in-process rules shared with `smd admin validate` |
| Diag: net | No | `smd admin diag net --json` |
| ka9q-watch | No | `smd watch ka9q --json` |
| Software versions | Yes; **Toggle enable** and the policy buttons skip the dialog | `smd install --components …`; `smd component update --components …`; writes `topology.toml` |
| SDR inventory | Yes; **Save** and **Remove** skip the dialog | writes `sdr-labels.toml` and `/etc/radio/radiod@<name>.conf`; `smd config register-radiod` |
| CPU affinity | Only **Apply plan** | `smd admin diag cpu-affinity --apply` |
| CPU frequency | Only **Apply policy** | `smd admin diag cpu-freq --apply` |

## 7. The screens

### 7.1 Overview

Shows a summary line, the timing state, a service-health table (one
row per component), the client inventory and the CPU-affinity summary.
It gathers the same facts as `smd status`.  **Refresh** gathers them
again.  Read-only.

### 7.2 Installation

**Guided bring-up.**  Takes a bare host to a running station without a
shell.  You:

1. pick a station profile (the catalog supplies the list, for example
   `dasi2`, `base` or `client`);
2. read the **Equipment** panel, which runs the same hardware checks
   `smd bringup` runs.  The panel only advises; a missing device never
   blocks the run.  Components for a missing SDR, GPSDO or
   magnetometer install dormant and start once you attach the device;
3. enter the station identity: reporter id and grid square (required),
   callsign, PSWS station id and PSWS instrument ids (optional), and
   for a `client` profile the remote radiod's DNS name;
4. optionally tick **Also install this profile's optional clients**.

The **Station readiness** panel runs the check behind
`smd admin readiness`.  **Preview plan** runs
`smd bringup <profile> --non-interactive … --dry-run`, which changes
nothing and needs no password.  **Begin bring-up** asks for
confirmation and then runs the same command without `--dry-run`, as
root, streaming its output into a window.  It can take minutes, or
hours on the first FFT-wisdom build.

Afterwards three buttons appear.  **Edit antenna** suspends the TUI and
runs `smd config edit radiod`, because bring-up configures radiod with
antenna defaults.  **Open Validate** opens Validate.  **Re-run
bring-up** clears the verdict so you can start again.

**① Download & install.**  Lists every catalog entry with its kind,
name, description and a progress bar through *download → install →
configure → enable → run*.

- **Install selected** runs `smd install <name>` for the highlighted
  row.  It refuses a row that has already been built.
- **Install enabled** runs `smd install --yes`, which installs every
  component `topology.toml` enables.
- **Install <profile> station** (one button per profile) runs
  `smd install --profile <profile> --yes`.  It enables and installs the
  profile's components; it does not configure or start them.
- **Refresh** runs `git pull --ff-only` in
  `/opt/git/sigmond/sigmond` before reloading the catalog.

**② Configure.**  One row per configured `<client>@<reporter-id>`
instance, with columns Client, Reporter ID, Sources, Cfg/Env/Srcs
(which of the three instance files exist) and Unit (the systemd
state).  A line above the table summarises `coordination.toml`.

- **Edit selected…** first asks the client whether it supports the
  JSON configuration exchange (`<client> config show --json`).  If it
  does, the TUI opens its own form for the client's settings; **Save**
  sends the changed values to `<client> config apply --json`, as root.
  If not, the TUI confirms and then runs
  `smd config edit <client> <reporter>`, which opens the client's own
  wizard or your `$EDITOR` in the suspended terminal.
- **Remove selected…** runs `smd admin instance remove <client>
  <reporter> --yes`.  It removes the instance's files but leaves the
  unit running; stopping it stays a separate step.
- **Add (dry-run)** and **Add** run `smd admin instance add <client>
  <reporter>`, with and without `--dry-run`.  Type the reporter id as
  `AC0G/B1` or `AC0G-B1`.  Add creates the files but neither enables
  nor starts the unit.
- **Migrate** runs `smd admin instance migrate` and shows its output.
  The interactive migration itself runs only from a shell:
  `smd admin instance migrate --yes`.
- **Refresh** reloads the table.

**③ Enable / start / stop.**  One row per managed target.  A templated
recorder instance, such as `psk-recorder@AC0G-B1`, gets its own row.  A
component with several units, such as `hf-timestd`, gets one row that
shows how many of its units run (for example `partial 21/24`).

Tick rows in the checkbox column: click a cell once to highlight it and
again to tick it, or move the cursor there and press Enter.  Clicking
the column header ticks or clears every row.  The **Start (n)**,
**Stop (n)** and **Restart (n)** buttons show how many rows you have
ticked.  The TUI offers no reload, because no sigmond client implements
one.

For instance rows the TUI runs `sudo systemctl <verb> <units>`.  For
component rows it runs `smd <verb> --components <names>`, so smd starts
or stops the units in the right order.  A mixed selection asks once and
runs the instance batch first.

**PSWS enrolment.**  The top half handles the station's enrolment with
the PSWS portal.  **Enroll** runs `smd psws enroll`: it makes sure the
station's SSH key exists and prints the public key to register.
**Verify** runs `smd psws verify`, a live login test; success lands in
`/etc/sigmond/.psws-verified`.  Recorders keep their data queued
locally whether or not the station has enrolled.

The bottom half lists each wspr-recorder, psk-recorder and
meteor-scatter instance with its upload flag.  **Enable** and
**Disable** run `smd config upload <client> <instance> --on` or
`--off`; for psk-recorder the **Via** list adds `--via`.  These flip
the flag only.  Whether spots actually arrive, the Verifier screen
tells you.

### 7.3 Monitoring

**Environment.**  Compares the site you declared in
`/etc/sigmond/environment.toml` with what discovery finds.  The left
pane lists declared peers, the middle pane the latest observations, and
the right pane each peer's verdict: healthy, degraded, missing, or an
unexpected extra.  The keys in section 3 run probes.  mDNS, ka9q-radio
status and the local GPSDO listen passively; NTP and KiwiSDR probes
send queries.  With `discovery.passive_only = true` in the manifest,
the active probes do not run.

**Timing & Authority.**  Two views, stacked, both updated every second.
The top shows hf-timestd's timing authority from
`/run/hf-timestd/authority.json`: the active tier, its offset and σ,
how old the snapshot has grown (red beyond 60 s), the governing radiod,
the available tiers and witnesses, and any disagreement flags.  The
bottom shows chrony's sources measured against HPPS rather than against
the system clock, with reach, sample age, σ and a 60-second sparkline.
Read the top first: it says what hf-timestd believes, and the bottom
says what chrony does with it.  `smd timing` gives the command-line
view.

**Annotation Quality.**  One row per running science recorder
(timestd-metrology, wspr-recorder, psk-recorder, hfdl-recorder,
codar-sounder, mag-recorder instances) with the timing tier and σ its
data currently carries, coloured GREEN (σ < 100 µs), YELLOW
(σ < 10 ms) or RED.  A panel below explains the verdict.  Updates every
second.

**Activity.**  Streams `smd watch <target>` into the screen.  Pick a
target (each recorder that declares one, plus `ka9q`, `uploads` and
`verifier`) and, where offered, an instance.  **Start** begins the
stream, **Stop** ends it, **Clear** empties the pane.  The `-v` switch
adds `-v` for targets that support it.  Leaving the screen stops the
stream.

**GPSDO live.**  One row per Leo Bodnar GPSDO that gpsdo-monitor
reports in `/run/gpsdo/`: PLL lock, GPS fix, satellites, antenna, the
A-level hint and the radiod it governs, plus a table of output
frequencies.  **Deep dive**, or a double-select, shows one device's
full report.  **Refresh** re-reads.  Read-only: set the GPSDO with Leo
Bodnar's own tool.

**ka9q-radio live.**  The top list shows every radiod that announces
itself on the LAN; **Refresh radiods** looks again.  For the chosen
radiod the screen shows a front-end table (sample rate, edges, gain,
attenuator, A/D overrange count and so on) and a table of active
channels (SSRC, frequency, preset, sample rate, encoding, SNR).

Highlight a channel and press **Deep dive** to see that one channel in
full.  The deep-dive view also lets you change the channel: tuning
(frequency, preset, sample rate), filter edges, gain, AGC and squelch,
and output (encoding, lifetime, description).  Each **Apply** sends
only the values you changed, straight to radiod through ka9q-python.
No confirmation dialog appears, and no `smd` command lies behind it.
**Back** returns to the channel list.

**Receiver channels.**  Pick a `<client>@<reporter>` instance.  The
screen finds the radiod that instance reads from and lists every live
channel whose frequency matches the instance's configuration.  Use it
to confirm that each band the client expects has a channel.  Read-only.

**RAC tunnel.**  Sets up the frpc reverse tunnel that lets the fleet
administrator reach the station.  Enter the user, token, SSH remote
port and, if assigned, web remote port that the administrator gave
you.  **Activate** installs `sigmond-rac` if its template has not yet
appeared (`smd install sigmond-rac --yes`), writes
`/etc/sigmond/frpc.toml` from the template, and runs
`systemctl enable --now wd-rac`.  **Disable** runs
`systemctl disable --now wd-rac` and keeps the file.  The status pane
updates every two seconds.  On a Proxmox appliance the tunnel runs on
the host, not in the station VM; see
[operator/remote-access.md](operator/remote-access.md) before using
this screen there.

**Resources.**  Memory, load and uptime; free space on `/var`, `/tmp`
and `/dev/shm`; per-client data and log sizes, with daily growth and
"days left" where the files allow an estimate; and the upload queue in
`sink.db`, with oldest and newest entries and the lag.  Read-only, no
password needed.

### 7.4 Maintenance

**Apply.**  On opening, the screen runs `smd apply --dry-run` and lists
only the lines that announce a change, so you can see whether edits
wait unapplied.  **Refresh pending** runs it again.  **Dry-run (full)**
shows the whole dry-run output.  **Apply now** confirms and runs
`smd apply`, which may restart services.  Read section 5 first.

**Backup.**  **Create backup now** runs `smd config backup`.  It saves
`~/sigmond-config-<hostname>-<date>.tar.gz` with sigmond's topology,
radiod's configuration, the client configurations, systemd units,
sudoers, cron and logrotate files.

**Restore.**  Lists the `sigmond-config-*.tar.gz` files in your home
directory.  Select one, then **Restore selected**, which runs
`smd config restore --input <file>`.  It overwrites files under
`/etc/sigmond`, `/etc/radio`, `/etc/wsprdaemon`, `/etc/hf-timestd`,
`/etc/psk-recorder` and more, but restarts nothing.  Run `smd apply`
afterwards.

### 7.5 Debugging

**Logs.**  Pick a component and, for a recorder, an instance.
**Follow journal** runs `journalctl --follow` on its units; **Tail
files** runs `tail -f` on its log files.  **Stop** ends either.  The
pane keeps the last 2000 lines.  **Set level** runs
`smd admin log set-level <component> <level>`, which records the level
in `coordination.env` and signals the units to pick it up.

**Verifier.**  The report half runs `smd admin verifier report
--target <t> --window <w>`.  Window accepts an `s`, `m`, `h` or `d`
suffix (default `1h`).  For spot targets, the RX call field and the
**Lost**, **In-flight**, **Delivered** and **Cadence** boxes add
`--rx-call`, `--lost`, `--in-flight`, `--delivered` and `--cadence`.
The rehabilitate half takes an RX call and a TX call and runs
`smd admin verifier rehabilitate <rx> <call>`, which clears wsprnet's
suppression of that pair so the decoders see the call again.

**Validate.**  Runs sigmond's cross-client rules (radiod resolution,
frequency coverage, CPU isolation, timing chain, disk budget, channel
count) and lists each result.  **Re-run** runs them again.
`smd admin validate` gives the same check from a shell.

**Diag: net.**  Classifies the network's IGMP behaviour so radiod's
multicast stays safe.  **Fast scan** runs `smd admin diag net --json
--listen 0` without root.  **Full listen (sudo)** listens on the wire
for IGMP queries for the number of seconds you enter.  It needs root;
if it fails, run `smd admin diag net --listen <s>` in a terminal.

**ka9q-watch.**  Runs `smd watch ka9q --json`.  It compares the
ka9q-radio commit that ka9q-python expects with upstream `main`, lists
the commits in between, and flags header changes that would break
stream delivery (green, yellow, red).  It also compares the installed
radiod with that pin.  **Refresh (no fetch)** uses cached refs;
**Refresh + git fetch** fetches upstream first.  Read-only.

### 7.6 Advanced

**Software versions.**  One row per catalog component, with columns
for selection, name, enabled, status, commit index, commits behind and
ahead of upstream, uncommitted changes, date and version policy.  The
screen fetches from each remote on opening.  Filter buttons (**All**,
**Missing**, **Behind**, **Dirty**, **Ahead**) limit the rows.

Select rows with Space, `a` and `n` (section 3), then:

- **+ Install** runs `smd install --components <names> --yes` for the
  selected rows not yet installed;
- **↑ Update** runs `smd component update --components <names>` for
  the selected rows that lag upstream and carry no local changes;
- **⇄ Toggle enable** flips each selected component's `enabled` flag
  in `topology.toml` straight away, without a dialog;
- **⟳ Refresh** fetches and reloads;
- **Detail…** opens the highlighted component.

The detail window shows recent and incoming commits and offers
**Install** or **Update this**, and three policy buttons: **Set:
latest**, **Pin to current** and **Set: ignore**.  The policy buttons
write `topology.toml` at once.  A component on `ignore` cannot update
until you set it back to `latest`.

**SDR inventory.**  Lists every receiver the host can see: USB devices
from `lsusb`, KiwiSDRs found on the LAN (port 8073), and front ends
that ka9q-radio serves.  **Rescan** (`r`) looks again.  **Edit** (`e`)
opens a form for the device's configuration name, reporter id, grid
and, for some devices, channels and TTL.  **Save** stores these in
`/var/lib/sigmond/sdr-labels.toml`.  For an RX-888 with a name, Save
also writes `/etc/radio/radiod@<name>.conf`, reads it back, and runs
`smd config register-radiod <name>` so clients can find that radiod.
**Remove** (`d`) drops the device from the label list; an attached USB
device reappears on the next scan.  **+ Add remote SDR** adds one by
address.

**CPU affinity.**  Shows the CPU layout, the plan (one physical core
per radiod, everything else on the remaining cores), what the system
actually does, and any contention.  **Re-run** refreshes.  **Apply
plan** runs `smd admin diag cpu-affinity --apply`.  On a fleet station
the administrator owns CPU placement; leave it alone.

**CPU frequency.**  Shows each CPU's maximum clock against the
`[cpu_freq]` policy in `topology.toml`.  **Refresh** re-reads.
**Apply policy** runs `smd admin diag cpu-freq --apply`.

## 8. Where the help panel disagrees with the screen

The right-hand help text and the screen sometimes describe different
behaviour.  Trust the screen, and this page:

- **ka9q-radio live** and **GPSDO live**: the help says **Deep dive**
  launches an external program.  It opens a view inside the TUI.  On
  ka9q-radio live, a double-select launches `ka9q tui`.
- **RAC tunnel**: the help describes an older scheme (a RAC number,
  `vpn.wsprdaemon.org`, `frpc.ini`, an **Apply & enable** button).  The
  screen asks for user, token and ports, writes `frpc.toml`, and labels
  its button **Activate**.
- **Restore**: the help describes a folder tree.  The screen lists the
  backups in your home directory.
- **Logs**: the help says to change the log level from the command
  line.  The screen has a **Set level** button.
- **Download & install**: the help names an **Install all missing**
  button.  The screen calls it **Install enabled**.
- **Software versions**: the hint says Enter opens details.  A single
  Enter does not; press it twice quickly, double-click, or use
  **Detail…**.
