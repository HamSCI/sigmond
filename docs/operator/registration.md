# Getting your data accepted — registration

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 0fd90dd on 2026-08-23 — live b4 + dasi002 (smd psws status, smd status "PSWS upload not finished" block, smd config uploads status, smd admin instance list) + code/docs
> **Canonical for:** getting a station's uploads accepted (PSWS, wsprnet, pskreporter, wsprdaemon)

Your station starts hearing signals the moment the install finishes. This page
is about the other half: making the four places your data goes **accept** it and
file it under your name. Unfamiliar words are in the [glossary](glossary.md).

The good news first, because it saves most people an afternoon of worry:

- **wsprnet.org** — nothing to register. Ever.
- **pskreporter.info** — nothing to register. Ever.
- **wsprdaemon.org** — nothing to register; it registers itself on your first upload.
- **PSWS (HamSCI)** — **the only place you fill in a form.** One account, one
  site, one instrument per product, then paste one key.

So: read the first three sections to learn *where to look* for your data, and
budget your real effort for PSWS.

Every command below is tagged `[VM]` or `[host]` on the line above it, so a
whole block is safe to copy. Nearly all of them are `[VM]` — the decoder VM,
which you reach with `ssh hamsci@<VM address>` (see
[the operator front page](README.md#two-machines-in-one-box)).

---

## 1. What the wizard already did

The setup wizard you answered during the install
([INSTALL.md §7](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#7-answer-the-setup-questions))
collected your whole identity and wrote it to one file,
`/etc/sigmond/site-profile.toml`, from which every recorder is configured. You
do not edit that file by hand and you do not repeat yourself per program.

| The wizard asked | What it becomes | Who uses it |
|---|---|---|
| **Reporter ID** (e.g. `AC0G/B4`) | `[reporters] reporter_id` | wsprnet, wsprdaemon — this *is* your identity there |
| **Grid square** (e.g. `EM38ww`) | `[station] grid_square` | all four; also the station's own coordinates |
| **Station designator** | `site-profile.toml` `[host].hostname` | names the VM and the radio's channels |
| **PSWS station + instrument IDs** *(optional, skippable)* | `[psws]` and `[psws.instruments]` | PSWS only |
| **Remote access** *(optional)* | the RAC tunnel | your fleet admin, not a data path |

Note what is **not** on that list: the wizard never asks for your callsign. It
takes the part of the reporter ID before the `/` — `AC0G/B4` gives `AC0G`
(source: `scripts/proxmox/sigmond-wizard.sh`, `CALLSIGN="${REPORTER%%/*}"`) —
and stores it as `[station] callsign`. That derived, unsuffixed call is exactly
what pskreporter wants (§3), which is why you never typed it twice.

If you gave it PSWS IDs, it also **generated your PSWS upload key** —
`/etc/hs-uploader/keys/id_ed25519_host` — and armed the login banner that keeps
reminding you about it, but it deliberately did **not** try to register the key:
that step needs copy-and-paste, and the install console has none (source:
`lib/sigmond/commands/config.py`, `_report_station_key()`). §5 is where you
finish it.

To see what it recorded — `[VM]`:

```bash
smd psws status
```

On a station that is fully enrolled this looks like AC0G/B4 does today:

```
PSWS enrollment — site
  ✓ station id         S000170
  ✓ hf-timestd id      171
  ✓ mag-recorder id    372
  ✓ station key        /etc/hs-uploader/keys/id_ed25519_host
  ✓ key verified       2026-08-17T22:33:29Z
```

On a station that skipped PSWS at the wizard it says so plainly, and nothing is
wrong — that is DASI002 today:

```
PSWS enrollment — site
  [psws] disabled in site-profile.toml — nothing to do
```

> **If you skipped the PSWS questions and now have IDs**, don't re-run the whole
> wizard from scratch: from the **host**, `sigmond-setup --reconfigure` re-asks
> everything with your old answers pre-filled — press Enter through the ones
> that are already right ([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)).

---

## 2. wsprnet.org — nothing to register

There is no account, no key, no form, and no waiting on anyone. WSPR reporting
works on the honour system: your **reporter ID** is your identity, and the
first spot you upload creates you in the database.

That is exactly why the reporter ID matters. It is one per receiver *and*
antenna — `AC0G/B4`, `AC0G/B1`, `AC0G/S` are three different reporters at one
callsign — so that a site with several receivers doesn't blur them together
(source: `hs-uploader/docs/PER-SITE-SETUP.md` §2).

**How to confirm:** go to [wsprnet.org](https://wsprnet.org), open the
**Database** tab, and search for your reporter ID in the *Reporter* field. Rows
should appear within about fifteen minutes of the station being up
([INSTALL.md §9](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#9-fifteen-minutes-later--check-its-alive)).

You can also watch it happen from the station, which is faster than reloading a
web page and tells you *why* if nothing is shipping — `[VM]`:

```bash
smd watch uploads
```

That prints one line per two-minute WSPR cycle, e.g.
`cycle=21:48 shipped wsprdaemon=199 wsprnet=posted:75/added:74 ft8=312 ft4=0`.
Press Ctrl-C to stop it; it changes nothing.

---

## 3. pskreporter.info — nothing to register

Same story: no account, no key. Your FT8/FT4 spots are sent over an open
connection tagged with your **bare callsign and grid** — no `/B4` suffix here
(source: `hs-uploader/docs/PER-SITE-SETUP.md` §1 and §2).

**How to confirm:** at [pskreporter.info](https://pskreporter.info), look
yourself up as the **receiver** (sometimes labelled *monitor*) — "signals
received by `AC0G`" — **not** as a sender. This trips up nearly everyone: a
station that is working perfectly shows nothing at all if you search it as a
transmitter. It cost one of our sites an afternoon of debugging a system that
was never broken.

Or, again from the station — `[VM]`:

```bash
smd watch uploads
```

Lines from the PSK uploader carry `ft8=` and `ft4=` counters — that is what
pskreporter is being sent. Not every line has them: WSPR cycle lines and PSK
lines are two different shapes sharing one screen.

> **Don't poll pskreporter's query API.** It is rate-limited, and a script that
> checks it in a loop will get you blocked. Look at the web page, or use
> `smd watch uploads` locally as often as you like.

---

## 4. wsprdaemon.org — it registers itself

wsprdaemon.org gets a second, richer copy of your WSPR data. You do **not**
email anyone and you do **not** pre-register a key. The first time your station
tries to upload, this happens by itself (source:
`hs-uploader/docs/PER-SITE-SETUP.md` §4):

1. The station tries a secure (SFTP) login to the gateway as your reporter ID
   with `/` turned into `_` — `AC0G/B4` logs in as `AC0G_B4`. On a brand-new
   station this is refused, because the gateway has never seen you.
2. It automatically falls back to a plain FTP drop that carries your reporter
   ID **and your public key** alongside the data.
3. The gateway registers that key for you and the station switches to the
   secure path on its own. That provisioning is **gateway-paced and can take a
   while** — it is not something your station controls or can hurry
   (`hs-uploader/docs/PER-SITE-SETUP.md` §4.3).

**Your data flows the whole time** — during step 2 as much as after step 3.
There is nothing for you to do at any point, and nothing to check up on unless
data stops.

**How to confirm:** the `wsprdaemon=` counter in `smd watch uploads` — `[VM]`:

```bash
smd watch uploads
```

A number bigger than zero means the cycle shipped. The gateway side of it (has
wsprdaemon.org actually got your rows?) is not visible from your station; ask
your fleet admin, who can query the collector directly.

---

## 5. PSWS — the one portal step

[PSWS](https://pswsnetwork.eng.ua.edu/) is HamSCI's Personal Space Weather
Station network. Your station's **daily** products go here: the GRAPE
time-standard dataset from `hf-timestd`, and the magnetometer day-file from
`mag-recorder` if you have an RM3100. Unlike the three spot networks, PSWS
files data under IDs it issues to you, and it accepts uploads only from a key
you have registered. Hence a form.

This is the only part of registration with an outside dependency — a portal you
do not control — so start it early rather than last.

### 5a. Get the IDs (in a browser, once)

1. Go to **<https://pswsnetwork.eng.ua.edu/>** and create an account.
2. Create a **Site** — one per physical location. PSWS assigns it a **station
   ID** that looks like `S000170`.
3. Add an **Instrument** for each product you will send. Each gets its own
   **instrument ID**, a plain number. AC0G/B4 has two: `171` for GRAPE and
   `372` for the magnetometer.

> **Two addresses are in circulation, and the HamSCI docs do not agree on which
> is the portal.** Everything on the station says
> `https://pswsnetwork.eng.ua.edu/` — that is what `smd psws enroll` prints
> (source: `lib/sigmond/psws.py`, `PSWS_PORTAL`). But
> `hf-timestd/docs/PSWS_SETUP_GUIDE.md` presents that as the *server* and gives
> `pswsnetwork.caps.ua.edu` as a separate *registration portal*, and a third
> HamSCI page gives `pswsnetwork.org`. If one does not load, try the others —
> and please tell your fleet admin which one worked, so this paragraph can be
> deleted (docs-gap row 9).

If you already gave these to the wizard, skip to 5b. If not, put them in from
the **host** with `sigmond-setup --reconfigure` (§1) — that is easier and safer
than editing files.

### 5b. Register the key

Your station already has a key; PSWS needs the public half. Log into the VM
over ssh and **the login banner shows it to you**, along with what to do with
it — that is what the banner is for, and it keeps nagging at every login until
the job is done.

To print the same thing on demand — `[VM]`:

```bash
smd psws enroll
```

It creates the key if it is somehow missing, then prints your station ID, the
public key (one long `ssh-ed25519 AAAA…` line), and the portal steps. Copy that
whole line — no line breaks, nothing trimmed — and paste it into the SSH-key
field for your **site** on the PSWS portal.

`enroll` and `verify` write files that belong to root, so `smd` re-runs itself
under `sudo` and **you should expect a password prompt** (source: `bin/smd`,
`_need_root()`). That is normal, not a failure. `smd psws status` and the login
banner never ask.

> **If you find hf-timestd's `PSWS_SETUP_GUIDE.md`, it will tell you to do
> something else** — generate a per-recorder RSA key, then push it with
> `ssh-copy-id` and a TOKEN from the portal. That is the **older** per-recorder
> procedure. On an appliance station it is superseded: one station key
> (`/etc/hs-uploader/keys/id_ed25519_host`) serves every PSWS product, and
> `smd psws enroll` / `smd psws verify` are the whole flow. Follow this page.
> (The two documents contradicting each other is docs-gap row 10.)

Nothing is lost while you wait to do this. The recorders keep recording and the
day's files queue up locally; they upload once the key is accepted.

### 5c. Prove it works

Back on the VM — `[VM]`:

```bash
smd psws verify
```

This makes one real login to PSWS as your station ID. On success it prints
`✓ SFTP login OK as S000NNN@pswsnetwork.eng.ua.edu`, records the fact in
`/etc/sigmond/.psws-verified`, and the login banner stops nagging. If it fails
it tells you which of the two ways it failed:

| What it says | What it means | What to do |
|---|---|---|
| `cannot reach pswsnetwork.eng.ua.edu:22` / `timed out` | network or firewall, not registration | check the station's internet; retry later |
| `SFTP login FAILED … public key not registered at the portal yet?` | the key isn't on the portal (or is a different one) | re-run `smd psws enroll`, paste the key again — carefully, whole line |

Then check the recorded state — `[VM]`:

```bash
smd psws status
```

`✓ key verified <timestamp>` is the finish line for registration.

> **One warning to expect and ignore.** `smd status` may still print a
> `━━━ PSWS upload not finished ━━━` block naming an SSH key under
> `/home/timestd/.ssh/` or `/etc/mag-recorder/keys/` even after
> `smd psws status` says verified. Those are the paths of an older
> per-recorder key scheme; today one station key serves everything. If
> `smd psws status` shows `✓ key verified`, your uploads are enrolled. This is
> tracked as a docs-gap (row 7 in
> [`../contributor/docs-gap-ledger.md`](../contributor/docs-gap-ledger.md)).

### 5d. If you have a magnetometer

Nothing extra to do. It is worth knowing *why* there used to be, in case a
future problem looks like this one: PSWS only ingests a magnetometer day if the
daily zip contains exactly one file named `<site>-<YYYYMMDD>-runmag.log` in the
sensor's native line format. For several days in August 2026 this station's
zips arrived, were stored, and were never ingested for exactly that reason.
`mag-recorder` has produced the right file automatically since 2026-08-22
(commit `d0a37b9`), so a current station gets this for free.

**What this means for you:** nothing — unless the PSWS page shows GRAPE data
but no magnetometer data for the same days. If you see that, tell your fleet
admin and quote this paragraph; it is a known failure shape, not a mystery.

---

## 6. Confirming everything flows

Registration is done when all of these show data. The waits are typical, not
guarantees — propagation may simply be poor, and a band with nothing on it
produces no spots no matter how healthy your station is.

| Product | Where to look | Typically appears |
|---|---|---|
| **WSPR spots** → wsprnet.org | [wsprnet.org](https://wsprnet.org) → Database → *Reporter* = your reporter ID | within ~15 minutes (2-minute cycles, then the upload batch) |
| **FT8 / FT4 spots** → pskreporter.info | [pskreporter.info](https://pskreporter.info) → your **callsign as receiver** | within a few minutes (the station ships every 30 s) |
| **WSPR copies** → wsprdaemon.org | `smd watch uploads`, the `wsprdaemon=` counter | same cycle as wsprnet; the very first ever may go the slower FTP route until the gateway registers you (§4) |
| **GRAPE daily dataset** → PSWS | your site's page on the PSWS portal | next day — packaged and uploaded at **01:00 UTC** for the previous UTC day |
| **Magnetometer daily file** → PSWS | your site's page on the PSWS portal | next day — uploaded at **03:00 UTC** |

The two PSWS rows are vaguer than the rest on purpose: **nobody has yet written
down what the portal's data view actually looks like** or how to navigate to
your site's files (docs-gap row 8). If you get an account and work it out, tell
your fleet admin so those two cells can name the page properly.

The two daily timers are real and checkable — `[VM]`:

```bash
systemctl list-timers grape-daily.timer mag-recorder-upload.timer
```

So a station finished on Tuesday afternoon shows spots the same afternoon and
its first PSWS products on Wednesday. Don't judge PSWS on day one.

> **One switch that stops everything at once.** A station can have all its
> outbound uploads turned off by policy — a testbed with no antenna, for
> instance, should not pollute the databases. Check it with `smd config uploads
> status` — `[VM]`:
>
> ```bash
> smd config uploads status
> ```
>
> `✓ uploads: enabled` is normal. `⚠ uploads: DISABLED BY POLICY` with a reason
> means someone turned them off deliberately (DASI002 reads
> `no HF antenna; no PSWS station/instrument ids`). Ask your fleet admin before
> changing it. The station's 5-minute [heartbeat](glossary.md) is never subject
> to this switch, so a station with uploads off still shows up on the fleet
> board.

---

## 7. Returning station, or moving a station

**Replacing an old station with new hardware?** Save the old machine's keys
before you wipe it and put them on the install stick — the installer restores
them and **your PSWS registration carries over**, so you never touch the portal
again. Do this at install time; it is
[INSTALL.md §4](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#4-returning-station-put-your-old-keys-on-the-stick-optional),
and it is optional only in the sense that the alternative is re-registering.

If you did not save them, that is recoverable: register the new station's key
at the portal exactly as in §5b, under the same site. Your station ID and
instrument IDs do not change.

**Moving a station that was built somewhere else?** Very common — stations are
staged at a workbench and shipped to their real home, which means the grid
square is wrong. After it is physically installed, from the **host**:

```bash
# [host] — on the Proxmox host, not the VM
sigmond-setup --reconfigure
```

Type the new grid square; press Enter through everything else. Your reporter
ID, remote-access number and **PSWS registration all stick**, the recorders
restart themselves, and the next spots upload with the new grid — check
wsprnet after about fifteen minutes
([INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another)).

---

## 8. What can go wrong here

Four failures account for nearly all of it. Each has a first move you can make
yourself; the full diagnosis for each lives in
[troubleshooting.md](troubleshooting.md), under the heading named below.

| Symptom | First move | Then see |
|---|---|---|
| **No spots on wsprnet** after 30 minutes | `smd watch uploads` — if it prints nothing at all, the problem is decoding, not registration; if it prints `wsprnet=posted:0`, check that you are searching the right identity with `smd admin instance list` (its REPORTER ID column, e.g. `AC0G/B4`) or in `/etc/sigmond/site-profile.toml` under `[reporters] reporter_id` | [troubleshooting.md → *No spots on wsprnet*](troubleshooting.md#no-spots-on-wsprnet) |
| **Nothing on pskreporter** | Search your callsign **as receiver**, not sender (§3) — that is the answer more often than not | [troubleshooting.md → *Nothing on pskreporter*](troubleshooting.md#nothing-on-pskreporter) |
| **Uploads pending, and the number keeps growing** | `smd config uploads status` first (§6) — uploads may be off by policy | [troubleshooting.md → *Uploads pending and growing*](troubleshooting.md#uploads-pending-and-growing) |
| **PSWS not verified** | `smd psws verify` and read which of the two failures it reports (§5c) | [troubleshooting.md → *PSWS not verified*](troubleshooting.md#psws-not-verified) |
| **PSWS has GRAPE data but no magnetometer data** | Nothing to fix locally — tell your fleet admin, and quote §5d | [troubleshooting.md → *PSWS not verified*](troubleshooting.md#psws-not-verified) |

When you ask for help, send the four outputs listed on
[the operator front page](README.md#getting-help--what-to-send), plus
`smd psws status`.
