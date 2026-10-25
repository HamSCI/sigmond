# Release v3.82 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the seven v3.82 rows and the RTCM3 candidate: bring-up installs from disk, the console panel gains colour, the console gets a root menu, psk-recorder's jt9 gets a writable /tmp, the links-record failure names its remedy, a restore drops lock-file rewrites, and `smd update` runs installers the way restore does.

**Architecture:** Each row stays in the repo that owns the code. Rows 1, 5, 6 and 7 change `bin/smd` and `lib/sigmond/` in sigmond. Row 3 adds two heredocs to the sigmond wizard, which `pm-align` already knows how to carry to stations in the field. Row 2 changes the `sigmond-issue` heredoc in the appliance's `firstboot-v3.sh`. Row 4 changes one unit file in psk-recorder. The RTCM3 candidate adds regression tests to hf-timestd. Rows 6 and 7 share one installer runner, `_align_run_install`, which already exists.

**Tech Stack:** Python 3.11 stdlib (sigmond core, pm-align), bash (wizard heredocs, appliance scripts and their bash tests), systemd units, pytest.

**Spec:** `sigmond/tasks/release-v382.md` (rows 1 to 7), plus Michael's rulings of 2026-10-25 carried in this plan's task headers. Evidence files: `sigmond/.superpowers/sdd/plan-sink-2b-part2/phase-g-rootcause.md` (row 1), `review-t28fix-report.md` finding N-1 (row 5), `task-28-v381-report.md` (row 6); Rob's bus message `/srv/hamsci/claude-bus/20261024T220812Z-rob.md` (RTCM3).

## Global Constraints

- The sigmond test suite never elevates. The conftest guard stays on; never set `SIGMOND_TEST_ALLOW_SUDO`. A test that would reach `_as_root` patches it (`mock.patch.object(smd, "_as_root", lambda argv: argv)`) instead.
- `smd` refuses sudo. Never write `sudo smd` in code, messages, docs or tests.
- Every image gets `INSTALL.md` and `docs/install-page.html` updated in sigmond-appliance. That happens in the last checklist item of Task 9, after the rig passes, not before.
- Commit messages end with exactly:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3
  ```
- No repo has an exclusive owner. The controller tells Rob about every change on the claude-bus; tasks do not post.
- Test commands, per repo (run from the repo root, `/home/mjh/hamsci/repos/<repo>`):
  - sigmond: `.venv/bin/pytest <path> -v` (the dev venv; never a bare `pytest`). Full suite: `.venv/bin/pytest -q`.
  - sigmond-appliance: `bash test-<name>.sh` (each prints ✓/✗ lines and exits non-zero on a ✗).
  - psk-recorder: `.venv/bin/python -m pytest <path> -v`.
  - hf-timestd: `.venv/bin/python -m pytest --override-ini addopts="-ra" <path> -v` (its `addopts` already holds `-q`; a second `-q` hides the summary).
- The rig test `sigmond-appliance/test-update-v3.sh` carries two exceptions that exist for v3.81 alone: `U2_ALLOW_ROLLBACK_KINDS=dirty` (row 6's superdarn-sounder lock file) and the G-ter nest limit for hf-timestd's setup wizard (appliance commit 14b3204, row 7). This plan removes neither. The update baseline stays v3.78 until v3.81 becomes a baseline; drop both once v3.82 serves as one.
- Restore must import no sigmond module after its first checkout. Any new module code that restore reaches goes into a module already listed in `_PRELOAD_MODULES['manifest_restore']` (bin/smd ~4947), or gets listed there; `tests/test_preload_modules.py` enforces it.
- Nothing in this plan restarts radiod or touches a station, the rig or B4.

## Review Focus

1. **A component missing from disk during a no-pull bring-up.** `--no-pull` must still clone a checkout that does not exist yet; only an existing checkout stays untouched. Pinned in Task 1 (`test_no_fetch_still_clones_when_absent`).
2. **A lock file already dirty before the restore's install.sh ran.** The restore must never discard it, even though it now matches the "lock file" pattern. Pinned in Task 4 (`test_a_lock_file_dirty_before_install_is_kept`).
3. **The split console.** On a tuned station the keyboard sits in the VM, so every console login reaches the host over SSH from 10.99.0.2. A rule of "tty only, never SSH" would hide the menu on every real station. LAN SSH and the RAC must still get the plain shell. Pinned in Task 2 (`test_split_console_is_console`, `test_lan_ssh_is_not_console`, `test_rac_ssh_is_not_console`).
4. **A menu that cannot draw or cannot learn the state.** No whiptail, a `sigmond-rac-switch status` that hangs, or a failing item must leave the operator at a shell or back at the menu, never stuck. Pinned in Task 2 (`test_no_whiptail_falls_through_to_the_shell`, `test_a_hung_rac_status_is_bounded`, `test_a_failing_item_returns_to_the_menu`).
5. **Colour codes that break a text match.** `/etc/issue` holds sed range markers and lines the rig greps for. A code placed before a marker, or inside a grepped phrase, silently breaks the panel refresh or a rig verdict. Pinned in Task 6 (`markers carry no escape`, `RAC block carries no escape`).

---

### Task 1: `smd install --no-pull`, passed by bring-up (row 1)

Repo: **sigmond**. Bring-up runs `smd install --components X --yes` (`lib/sigmond/bringup.py:197-205, 318-320`). Both install paths hard-code `pull=True` (`bin/smd:10175` in `_install_single_client`, `bin/smd:10411` in `_install_full_suite`), and `installer.clone_repo(pull_if_exists=True)` then runs `git fetch origin` and `git checkout -B main origin/main`. The three native installers also pull: `_install_radiod_native` (`bin/smd:~10627`, pinned to ka9q-python's compat commit, but it still fetches), `_install_gmag_webui_native` (`~10776`) and `_install_ka9q_web_native` (`~10840`), both unpinned and so drifting to origin/main. No catalog entry carries a `pin` today (`etc/catalog.toml`), so `install_client(pull=False)` already leaves a catalog client's checkout alone.

**Files:**
- Modify: `lib/sigmond/installer.py:181-263` (`clone_repo`)
- Modify: `bin/smd` (`_add_install_args` ~25475; `_install_single_client` ~10175; `_install_full_suite` ~10411 and its native calls; `_install_radiod_native` ~10586; `_install_gmag_webui_native` ~10765; `_install_ka9q_web_native` ~10825; `_BASH_COMPLETION` install flag lists ~16061 and ~16168)
- Modify: `lib/sigmond/bringup.py:197-205, 318-320`
- Test: `tests/test_installer.py`, `tests/test_bringup.py`, new `tests/test_install_no_pull.py`

**Interfaces:**
- Produces: `clone_repo(entry, *, base=GIT_BASE, pull_if_exists=False, ref=None, fetch=True) -> Path`. With `fetch=False` and an existing checkout: no git network command at all; `ref=None` returns the checkout as it stands; a `ref` that resolves locally gets `_checkout_ref`; a `ref` that does not resolve raises `RuntimeError` naming the ref and saying no fetch ran. A missing checkout still clones.
- Produces: `smd install ... --no-pull` (argparse `dest='no_pull'`), meaning "install what sits on disk". It sets `pull=False` for `_do_install_client` and passes `fetch=False, pull_if_exists=False` to the natives' `_clone_repo` calls. The natives grow a keyword `pull: bool = True`.
- Produces: every bring-up step of kind `'install'` carries `'--no-pull'` in its argv.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_installer.py  (real git repos under tmp_path, as the neighbours do)
def test_no_fetch_leaves_an_existing_checkout_on_its_commit(self, tmp_path):
    # origin gains a commit after the clone; fetch=False must not see it
    repo_dir = clone_repo(entry, base=tmp_path, pull_if_exists=True, fetch=False)
    assert _head(repo_dir) == old_sha
    assert not any('fetch' in c for c in git_calls)      # spy on installer._git

def test_no_fetch_checks_out_a_local_pin_without_fetching(self, tmp_path): ...
    # ref present locally -> HEAD == ref, no 'fetch' call
def test_no_fetch_refuses_a_pin_it_does_not_have(self, tmp_path):
    with pytest.raises(RuntimeError, match='no fetch'): ...
def test_no_fetch_still_clones_when_absent(self, tmp_path): ...     # Review Focus 1

# tests/test_bringup.py
def test_every_bringup_install_passes_no_pull():
    p = build_plan(_dasi2(), local_radiod=True)
    installs = [s for s in p.steps if s.kind == 'install']
    assert installs and all('--no-pull' in s.argv for s in installs)
# repeat for build_plan(..., local_radiod=False, remote_status_dns='x') and the no-SDR deferred path

# tests/test_install_no_pull.py  (load bin/smd via SourceFileLoader like test_update_executor.py;
# patch _need_root -> False, _netdiag_auto_refresh, _build_decoders -> True, _post_install_fixups,
# _enable_after_install, and capture _do_install_client's kwargs)
def test_single_client_install_with_no_pull_passes_pull_false(): ...   # kwargs['pull'] is False
def test_single_client_install_without_the_flag_still_pulls(): ...    # kwargs['pull'] is True
def test_components_install_with_no_pull_passes_pull_false(): ...
def test_native_ka9q_web_with_no_pull_never_fetches(): ...
    # patch smd._clone_repo; assert called with pull_if_exists=False, fetch=False
def test_native_radiod_with_no_pull_keeps_its_pin_without_fetching(): ...
    # _clone_repo called with ref=<pin>, fetch=False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_installer.py tests/test_bringup.py tests/test_install_no_pull.py -v`
Expected: FAIL (`clone_repo() got an unexpected keyword argument 'fetch'`; `'--no-pull'` absent from argv; `unrecognized arguments: --no-pull`).

- [ ] **Step 3: Implement**

`clone_repo(..., fetch=True)` per Interfaces; keep the offline-warning branches unchanged for `fetch=True`. Add `--no-pull` to `_add_install_args` with help text "install what sits on disk: no fetch, no checkout of origin (bring-up passes it; the image ships each checkout at its manifest pin)". Thread `pull = not args.no_pull` through both install paths and the three natives. Add `--no-pull` to both install completion word lists. In `bringup.py`, append `'--no-pull'` to the three install argv lists. Leave `_install_profile` and the TUI install paths pulling: neither belongs to bring-up.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_installer.py tests/test_bringup.py tests/test_install_no_pull.py tests/test_bash_completion.py tests/test_install_target_classifier.py -v`
Expected: PASS. Then the full suite: `.venv/bin/pytest -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/installer.py lib/sigmond/bringup.py bin/smd tests/test_installer.py tests/test_bringup.py tests/test_install_no_pull.py
git commit -m "install: --no-pull installs from disk; bring-up passes it (v3.82 row 1)"
```

---

### Task 2: Console menu after root login (row 3)

Repos: **sigmond** (wizard heredocs, pm-align, tests). The appliance needs no code change; Task 9 checks whiptail on the nested PM.

#### Design

*Where the operator logs in.* The Proxmox host (PM) has no keyboard: both USB controllers belong to the VM through vfio-pci. A console login therefore runs like this. The operator presses a key on the VM's tty1. The VM's `sigmond-console-bridge` (firstboot-v3.sh ~1870-1970) runs `ssh -tt root@10.99.0.1`. The PM's `sigmond-console-relay` paints the session onto the PM's VT1. So on a tuned station the root shell arrives over SSH, from 10.99.0.2 to 10.99.0.1. A real `/dev/tty[0-9]*` login happens only on an untuned host, or after the GRUB escape hatch (`modprobe.blacklist=vfio-pci`). The ruling's "tty only, not SSH" would never show the menu on a real station. This plan therefore counts two cases as the console:

- the login's terminal matches `/dev/tty[0-9]*`, or
- `SSH_CONNECTION` reads `10.99.0.2 <port> 10.99.0.1 <port>`: the split-console bridge on the private host-only /30.

Every other SSH session keeps the plain shell: LAN, the RAC (frp delivers it from 127.0.0.1), and `smd remote`'s forced command (no login shell, no tty). **Open for Michael:** confirm this reading of "console only". The alternative marks the bridge explicitly (`SendEnv LC_SIGMOND_CONSOLE`, which Debian's default `AcceptEnv LC_*` accepts), but that changes the VM half and needs a re-push to every VM.

*What runs.* `/etc/profile.d/sigmond-console-menu.sh` runs in login shells only. It calls `/usr/local/sbin/sigmond-console-menu --login` when the user is root, stdin is a terminal, and `SIGMOND_MENU_SHOWN` is unset; it exports `SIGMOND_MENU_SHOWN=1` first, so `bash -l`, `su -` and tmux inside the session do not show it again. The menu script decides "console or not" itself and returns at once when not. The root password stays in front: the menu runs only after login.

*The menu.* whiptail, size `20 72 8` (fits the 80x24 the bridge's pty may report). Title: `Sigmond station <name>`, where the name comes from the first word of `/etc/sigmond-appliance/.configured` (the panel reads the same file), falling back to `hostname -s`. Items, by tag:

| tag | label | runs |
|---|---|---|
| `check` | Check the station | `sigmond-check` |
| `settings` | Change station settings | `sigmond-setup --reconfigure` |
| `remote` | Remote access off / Remote access on | `sigmond-setup --rac-off` / `--rac-on` |
| `net` | Test gateway and DNS (IPv4 and IPv6) | built-in `net_test` |
| `shell` | Shell | leave the menu |

The `remote` label names the action that applies now. The menu reads the first line of `timeout ${SIGMOND_MENU_RAC_TIMEOUT:-10} sigmond-rac-switch status`: `Remote access: on` and `Remote access: wanted on, not up` offer "Remote access off"; `Remote access: off` offers "Remote access on". A missing switch, a timeout or any other line offers both items, `remote-on` and `remote-off`, with "(state unknown)" in each label.

After any item the script prints `Press Enter to return to the menu`, waits, and shows the menu again. Choosing Shell, or Esc/Cancel (whiptail exit 1 or 255), leaves the loop with exit 0, and the login shell continues. Without whiptail, the script prints one line (`sigmond-console-menu: whiptail not installed; plain shell`) and returns 0.

*The network test.* Four lines, each a word verdict (colour never carries it):
- `IPv4 gateway <addr>: answers` / `does NOT answer` / `none (no IPv4 default route)`, from `ip -4 route show default` and `ping -4 -c 2 -W 2 <addr>`;
- `IPv6 gateway <addr>%<dev>: ...`, from `ip -6 route show default` and `ping -6 -c 2 -W 2 <addr>%<dev>`;
- `DNS (IPv4 answer) github.com: <first addr>` / `FAILED`, from `getent ahostsv4 github.com`;
- `DNS (IPv6 answer) github.com: <first addr>` / `FAILED`, from `getent ahostsv6 github.com`.

The menu text stays ASCII: the bridge paints raw bytes onto a `TERM=linux` VT.

*Delivery.* The wizard installs both files at the point where it installs `sigmond-check` (`scripts/proxmox/sigmond-wizard.sh` ~3349-3518), as top-level quoted heredocs. `pm-align` carries them to stations already in the field through a new optional set, `WIZARD_HEREDOCS_SINCE_V382`, the way `WIZARD_HEREDOCS_SINCE_V372` carries `sigmond-check`.

**Files:**
- Modify: `scripts/proxmox/sigmond-wizard.sh` (two new heredocs after `chmod +x /usr/local/bin/sigmond-check`, ~3518)
- Modify: `scripts/proxmox/pm-align.py` (`WIZARD_HEREDOCS_SINCE_V382`, used in `desired_files` like `..._SINCE_V372`, ~300 and ~405)
- Test: new `tests/test_console_menu.py`; `tests/test_pm_align.py`

**Interfaces:**
- Produces: `/usr/local/sbin/sigmond-console-menu [--login]`, mode 0755. Functions inside, callable when the test sources the script with `SIGMOND_MENU_LIB=1` (the script then defines its functions and returns before `main`):
  - `console_kind` → prints `console` or `other`; reads `$(tty)` (overridable as `SIGMOND_MENU_TTY`) and `SSH_CONNECTION`.
  - `rac_actions` → prints `off`, `on`, or `on off`.
  - `net_test` → prints the four lines above.
  - `menu_loop` → runs until Shell/Esc; returns 0.
- Env knobs for tests only: `SIGMOND_MENU_TTY`, `SIGMOND_MENU_CONF` (default `/etc/sigmond-appliance/.configured`), `SIGMOND_MENU_RAC_TIMEOUT` (default 10). Every command runs by name from `PATH`, so tests stub `whiptail`, `sigmond-check`, `sigmond-setup`, `sigmond-rac-switch`, `ip`, `ping`, `getent`, `timeout` and `hostname` with scripts in a temp `bin/`.
- Produces: `/etc/profile.d/sigmond-console-menu.sh`, mode 0644.
- Produces: `pm_align.WIZARD_HEREDOCS_SINCE_V382 = {"/usr/local/sbin/sigmond-console-menu": 0o755, "/etc/profile.d/sigmond-console-menu.sh": 0o644}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_console_menu.py` extracts both heredocs with `pm_align.extract_heredocs(WIZARD.read_text())` and writes them to `tmp_path`. A fake `whiptail` pops one answer per call from `$FAKE_ANSWERS` (a tag, or `ESC` for exit 255), writes the tag to stderr as whiptail does, and appends its argv to `$FAKE_LOG`. Each stub command appends its name and arguments to the same log.

```python
def test_tty1_is_console(): assert kind(tty='/dev/tty1', ssh='') == 'console'
def test_split_console_is_console():                      # Review Focus 3
    assert kind(tty='/dev/pts/0', ssh='10.99.0.2 50422 10.99.0.1 22') == 'console'
def test_lan_ssh_is_not_console():
    assert kind(tty='/dev/pts/0', ssh='192.168.1.20 50422 192.168.1.50 22') == 'other'
def test_rac_ssh_is_not_console():
    assert kind(tty='/dev/pts/0', ssh='127.0.0.1 40000 127.0.0.1 22') == 'other'
def test_login_mode_off_console_draws_nothing():          # --login over LAN ssh: no whiptail call, rc 0
def test_title_names_the_station():                       # conf 'AC0G-B4 ...' -> '--title' arg holds 'Sigmond station AC0G-B4'
def test_title_falls_back_to_hostname():
def test_check_runs_sigmond_check_then_returns():         # answers check,shell -> log has sigmond-check; whiptail called twice
def test_settings_runs_reconfigure():                     # log has 'sigmond-setup --reconfigure'
def test_remote_offers_off_when_on():                     # status 'Remote access: on' -> menu argv holds 'Remote access off', not '... on'
def test_remote_offers_off_when_wanted_but_not_up():
def test_remote_offers_on_when_off():                     # choosing it runs 'sigmond-setup --rac-on'
def test_remote_offers_both_when_state_unknown():         # no switch on PATH -> 'remote-on' and 'remote-off' tags
def test_a_hung_rac_status_is_bounded():                  # stub sleeps 30, SIGMOND_MENU_RAC_TIMEOUT=1 -> whole run < 5 s, both offered
def test_net_test_reports_all_four_lines():               # stubbed routes/ping/getent -> 'IPv4 gateway 192.168.1.1: answers',
                                                          # 'IPv6 gateway fe80::1%vmbr0: answers', 'DNS (IPv4 answer) github.com: 140.82.', 'DNS (IPv6 answer) github.com: FAILED'
def test_net_test_names_a_missing_default_route():        # 'none (no IPv4 default route)'
def test_shell_returns_zero_without_running_anything():
def test_esc_returns_zero():
def test_a_failing_item_returns_to_the_menu():            # sigmond-check exits 3 -> menu shown again, rc 0 at the end
def test_no_whiptail_falls_through_to_the_shell():        # rc 0, one line naming whiptail
def test_menu_text_is_ascii():                            # every byte of the menu script < 0x80
def test_profile_hook_runs_only_for_root_login_shells():  # bash -c 'source hook' (not login) -> menu never called;
                                                          # bash -l with SIGMOND_MENU_SHOWN=1 -> never called
```

```python
# tests/test_pm_align.py
def test_desired_files_carries_the_console_menu_when_the_wizard_has_it():
    paths = {f.path: f.mode for f in pm_align.desired_files(_src(), 100)}
    assert paths["/usr/local/sbin/sigmond-console-menu"] == 0o755
    assert paths["/etc/profile.d/sigmond-console-menu.sh"] == 0o644
def test_desired_files_tolerates_a_wizard_without_the_menu(): ...   # older release: absent, no error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_console_menu.py tests/test_pm_align.py -v`
Expected: FAIL (`KeyError: '/usr/local/sbin/sigmond-console-menu'` from the heredoc lookup).

- [ ] **Step 3: Implement the two heredocs and `WIZARD_HEREDOCS_SINCE_V382`**

Write the script per the Design table. The whiptail call uses `3>&1 1>&2 2>&3` to capture the tag. Precede the heredocs with `say "installing the console menu (root logins at this machine's console)"` and a comment that pm-align carries them (`WIZARD_HEREDOCS_SINCE_V382`). In pm-align, add the dict next to `WIZARD_HEREDOCS_SINCE_V372` and append each path the wizard carries, like the v3.72 loop.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_console_menu.py tests/test_pm_align.py tests/test_wizard_dispatch.py -v`
Expected: PASS. Mutation check: delete the `10.99.0.2` clause from `console_kind`; `test_split_console_is_console` must fail. Restore it.

- [ ] **Step 5: Commit**

```bash
git add scripts/proxmox/sigmond-wizard.sh scripts/proxmox/pm-align.py tests/test_console_menu.py tests/test_pm_align.py
git commit -m "console: a root menu at the station console, split console included (v3.82 row 3)"
```

---

### Task 3: `smd update` runs installers as restore does (row 7)

Repo: **sigmond**. The update's install step (`bin/smd:4416-4436`) runs `subprocess.run(_as_root(['bash', str(sh)]), capture_output=True, text=True)`: no `--yes`, stdin open. Restore and align already share `_align_run_install(repo, log_target=None)` (`bin/smd:6632`). It picks `scripts/install.sh` else `install.sh`, passes `--yes` unless `installer_args.pass_yes` says the parser rejects it, closes stdin and writes the update log. The update's step should call it.

**Files:**
- Modify: `bin/smd:6632-6670` (`_align_run_install`), `bin/smd:4408-4436` (update's install step)
- Test: `tests/test_update_executor.py`, `tests/test_installer_args.py`

**Interfaces:**
- Produces: `_align_run_install(repo: Path, log_target: Optional[str] = None, *, tail_lines: int = 5) -> int`. Same behaviour as today, with `tail_lines` setting how many output lines it prints on a non-zero exit. Returns 0 when no installer exists.
- Consumes: `sigmond.installer_args.pass_yes(text) -> bool` (already in `_PRELOAD_MODULES['update']`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_update_executor.py — a class that needs no sudo:
# patch smd._as_root to identity and subprocess.run with _run_without_runuser
class InstallerInvocationTests(_Rig):
    def test_update_install_passes_yes_and_closes_stdin(self):
        self._set_install_sh('#!/bin/bash\nprintf "%s\\n" "$@" > "$(dirname "$0")/../.args"\n'
                             'if read -r _; then echo open; else echo closed; fi > "$(dirname "$0")/../.stdin"\n')
        # run with real stdin attached to a pipe that holds a line
        ...
        assert (self.host / ".args").read_text().split() == ["--yes"]
        assert (self.host / ".stdin").read_text().strip() == "closed"
    def test_an_installer_that_rejects_yes_runs_without_it(self):
        # body: case "$1" in --help) ;; *) exit 2 ;; esac  -> no --yes, exit 0 overall
    def test_update_install_goes_through_align_run_install(self):
        with mock.patch.object(smd, "_align_run_install", return_value=0) as m: ...
        m.assert_called_once_with(self.base / "fakeclient", log_target="fakeclient", tail_lines=15)

# tests/test_installer_args.py
def test_align_run_install_prints_tail_lines_on_failure(): ...   # 20 lines out, exit 1, tail_lines=15 -> 15 '  | ' lines
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_update_executor.py tests/test_installer_args.py -v`
Expected: FAIL (`.args` empty; `.stdin` reads `open`; `_align_run_install` never called).

- [ ] **Step 3: Implement**

Add `tail_lines` to `_align_run_install`. Replace the update step's `subprocess.run` and its own tail printing with `rc = _align_run_install(base / step.target, log_target=step.target, tail_lines=15)`. Keep the update's `_ok`/`_err` lines, `applied`, `_record_update_step` and `failed` accounting unchanged. Keep the `pull_failed` skip ahead of the call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_update_executor.py tests/test_installer_args.py tests/test_installer_yes_contract.py tests/test_preload_modules.py tests/test_align_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/smd tests/test_update_executor.py tests/test_installer_args.py
git commit -m "update: run each install.sh as restore does, --yes and stdin closed (v3.82 row 7)"
```

---

### Task 4: Restore discards lock-file rewrites its installers made (row 6)

Repo: **sigmond**. `cmd_manifest_restore` (`bin/smd:5159`) refuses a dirty tree before it moves anything (`~5360-5383`). It then checks out the older SHAs and runs each moved component's install.sh through `_align_run_install` (`~5455-5477`). An older install.sh may run `uv sync` without `--frozen` and rewrite `uv.lock` as root (superdarn-sounder before 36fa026). After each install, the restore should restore any tracked lock file that the install changed, and say so. It must never touch a change that existed before the install. The existing comment "NEVER stash, NEVER discard" (CONTRIBUTING.md §8) stays true for everything else; add one sentence there naming this exception.

**Files:**
- Modify: `lib/sigmond/manifest_restore.py` (pure helper; already preloaded for restore)
- Modify: `bin/smd` (`cmd_manifest_restore` install loop ~5455-5477; new `_restore_discard_lock_rewrites`)
- Test: `tests/test_manifest_restore.py`

**Interfaces:**
- Produces: `manifest_restore.LOCK_FILE_NAMES = frozenset({'uv.lock'})`.
- Produces: `manifest_restore.lock_rewrites(before: set[str], after: set[str]) -> list[str]`: sorted paths in `after - before` whose basename sits in `LOCK_FILE_NAMES`.
- Produces: `_restore_tracked_dirty(d: Path) -> set[str]`: the same `git --no-optional-locks -C d status --porcelain --untracked-files=no` the dirty check runs, as a set of paths.
- Produces: `_restore_discard_lock_rewrites(name: str, d: Path, owner: str, before: set[str]) -> bool`: for each path from `lock_rewrites`, runs `_runuser(owner or 'root', ['git', '-C', str(d), 'checkout', '--', path])`; prints `_info(f'{name}: discarded {path}: the older install.sh rewrote it, and a restore keeps no installer\'s lock-file rewrite')`; on a failed checkout prints `_warn` with the command to run by hand and returns False.

- [ ] **Step 1: Write the failing tests**

Extend `_FakeGit` with `dirty_after_install: dict[name, list[str]]`. After the fake sees that component's install.sh run, `status --porcelain` answers with those lines added.

```python
def test_lock_rewrites_picks_new_lock_files_only():
    assert lock_rewrites({'a.py'}, {'a.py', 'uv.lock', 'sub/uv.lock', 'b.py'}) == ['sub/uv.lock', 'uv.lock']
def test_lock_rewrites_ignores_a_lock_file_dirty_before():
    assert lock_rewrites({'uv.lock'}, {'uv.lock'}) == []
def test_a_lock_file_the_installer_rewrote_is_discarded_and_named(self):
    fake = _FakeGit(dirty_after_install={'superdarn-sounder': ['uv.lock']})
    rc, out, fake = self._run(...)
    assert rc == 0
    assert any(c[-3:] == ['checkout', '--', 'uv.lock'] for c in fake.calls)
    assert 'discarded uv.lock' in out
def test_a_lock_file_dirty_before_install_is_kept(self):                   # Review Focus 2
    # status answers ['uv.lock'] both before and after install -> no 'checkout --'
def test_a_non_lock_file_the_installer_changed_is_left_alone(self):
    # dirty_after_install ['src/x.py'] -> no 'checkout --'
def test_a_failed_discard_warns_with_the_command(self): ...
def test_the_discard_runs_as_the_checkout_owner(self): ...                 # argv starts with runuser -u <owner>
```

`test_restore_imports_no_sigmond_module_after_the_first_checkout` must still pass unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_manifest_restore.py -v`
Expected: FAIL (`ImportError: cannot import name 'lock_rewrites'`).

- [ ] **Step 3: Implement**

In the install loop: `before = _restore_tracked_dirty(d)` just before `_align_run_install(d, log_target=name)`; after it, whatever its exit code, call `_restore_discard_lock_rewrites(name, d, _owner_of(d), before)`. A False return adds `name` to `install_failed`. Reference `manifest_restore.lock_rewrites` through the module object bound before the first checkout, never through a fresh import.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_manifest_restore.py tests/test_preload_modules.py -v`
Expected: PASS. Mutation check: make `lock_rewrites` return `sorted(after)` filtered by name only; `test_a_lock_file_dirty_before_install_is_kept` must fail.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/manifest_restore.py bin/smd tests/test_manifest_restore.py CONTRIBUTING.md
git commit -m "restore: discard lock-file rewrites an older install.sh made, and say so (v3.82 row 6)"
```

---

### Task 5: The links-record failure names its remedy (row 5)

Repo: **sigmond**. `sink_levers.restore_uploader_links` (`lib/sigmond/sink_levers.py:701-771`) keeps the record `/etc/sigmond/sink-levers/wspr-uploader.links` and reports a failure when the operator has disabled the uploader (`now is None`) or linked it elsewhere (`now != target`). `commands/uploader.py:653-663` then sets `rc = 1`. Each upload step prints FAILED and exits 1, with no way out named.

Decision (Michael preferred this): the step **keeps failing**, so the change stays visible, but the output names both ways out. Also, compare link targets after resolving relative paths, so a relative re-link to the same file no longer reads as a change (N-1's second point).

**Files:**
- Modify: `lib/sigmond/sink_levers.py:690-771` (`RestoreResult`, `restore_uploader_links`)
- Test: `tests/test_sink_levers.py:946-980`, `tests/test_uploader_manifest_part2.py`

**Interfaces:**
- Produces: `RestoreResult.remedy: Optional[str] = None`. `lines()` appends it last as `lever: <remedy>` when set.
- `restore_uploader_links` sets `remedy` once when any record line failed because the operator changed the unit link:
  `f"the operator changed {WSPR_UPLOADER}'s link after the fill, so the record no longer applies. To keep that change and clear this failure: sudo rm {etc / UPLOADER_LINKS_RECORD}. To return to the fill's state instead: smd apply"`
  (for `now is None`, the second sentence reads "To re-enable the uploader instead: smd apply").
- Target comparison: `os.path.normpath(os.path.join(unit_dir, x))` for both `now` and the recorded target.

- [ ] **Step 1: Write the failing tests**

```python
def test_restore_skips_when_the_unit_link_is_gone(self):          # existing; add:
    self.assertIn(f"sudo rm {self.record()}", res.remedy)
    self.assertIn(f"lever: {res.remedy}", res.lines())
def test_restore_skips_a_target_the_unit_link_no_longer_names(self):   # existing; add the same two asserts
def test_a_relative_relink_to_the_same_file_restores(self):
    # unit link re-made as a relative path to the recorded target -> restored, record removed, no failure
def test_a_bad_record_line_names_no_operator_remedy(self):        # outside .wants -> remedy is None
# tests/test_uploader_manifest_part2.py
def test_upload_step_after_an_operator_disable_fails_and_names_the_record(self):
    # rc == 1; output holds 'sudo rm /etc/sigmond/sink-levers/wspr-uploader.links' (under the test etc root)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sink_levers.py tests/test_uploader_manifest_part2.py -v`
Expected: FAIL (`AttributeError: 'RestoreResult' object has no attribute 'remedy'`).

- [ ] **Step 3: Implement** per Interfaces. `uploader.py` needs no change: it prints `back.lines()` and keeps `rc = 1 if back.failures`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sink_levers.py tests/test_uploader_manifest_part2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/sink_levers.py tests/test_sink_levers.py tests/test_uploader_manifest_part2.py
git commit -m "sink levers: a stale links record names its remedy; relative re-links compare equal (v3.82 row 5)"
```

---

### Task 6: Colour on the console status panel (row 2)

Repo: **sigmond-appliance**. `sigmond-issue` lives as the heredoc `cat > /usr/local/sbin/sigmond-issue <<'ISSEOF'` in `firstboot-v3.sh` (~2975-3880). It composes `PANEL` (~3815-3836), writes it into `/etc/issue` and `/etc/motd` between the markers `════ Sigmond appliance ` and `════ end Sigmond panel`, and paints it onto VT1 (`_paint_vt1`, ~3129). Chris's mockup does not sit in any repo; this task proposes a palette (**open for Michael:** match it to the mockup).

Decisions:
- Colour goes into the VT1 paint and `/etc/issue` (the console's own file). `/etc/motd` gets the plain text: SSH, the RAC and pam_motd read it.
- Colour wraps only these tokens in the main panel: addresses (`HOSTIP`, `VMIP`, the web UI URL) bold cyan `\e[1;36m`; logins (`root`, `sigmond`, `hamsci`) bold green `\e[1;32m`; the default password `hamsci-sigmond` bold yellow `\e[1;33m`. Every span ends with `\e[0m`. The words around them ("<-- image default, CHANGE IT") keep the meaning.
- The markers, `RACBLOCK`, `NETWARN`, `BRINGUP`, `RXWARN`, `VMNOTE` and `VMBEHIND` carry no codes. So the sed ranges and every rig grep of `/etc/issue` (test-nested-v3.sh ~220-300, 1821, 2963) keep working as they stand.
- No fit test exists today. The new test proves the colour changes nothing visible: the panel with codes stripped equals the panel built with colour off, byte for byte. So width and height stay what they were.

**Files:**
- Modify: `firstboot-v3.sh` (inside the `ISSEOF` heredoc: a new marked block `# >>> panel_colour` … `# <<< panel_colour` near the top; the `PANEL` heredoc ~3820-3836; the write loop ~3837-3852)
- Test: new `test-panel-colour.sh`; `test-panel-remote.sh` (one added assertion)

**Interfaces:**
- Produces, in the `panel_colour` block: `_hl <addr|login|pw> <text>` prints the text wrapped in its SGR pair, or bare when `SIGMOND_PANEL_COLOUR=0`; `_panel_plain` (stdin → stdout) strips `\x1b\[[0-9;]*m`.
- `/etc/issue` and VT1 receive `$PANEL`; `/etc/motd` receives `$(printf '%s' "$PANEL" | _panel_plain)`.

- [ ] **Step 1: Write the failing test** `test-panel-colour.sh`, in the style of `test-panel-startup.sh` (extract `issue.sh`, sed `/dev/tty1`, `/etc/issue`, `/etc/motd` to temp files, stub `qm`, `systemctl`, `who`):

```
✓ the panel_colour block loads; _hl addr 10.0.0.5 prints ESC[1;36m10.0.0.5ESC[0m
✓ SIGMOND_PANEL_COLOUR=0: _hl prints the bare text
✓ a full run writes escape codes into /etc/issue and the VT1 capture
✓ /etc/motd holds no escape byte
✓ /etc/issue with codes stripped == /etc/issue from a SIGMOND_PANEL_COLOUR=0 run (same text, so same fit)
✓ markers carry no escape: every '════ Sigmond appliance' and '════ end Sigmond panel' line starts at column 0 with no ESC   (Review Focus 5)
✓ the default-password branch wraps hamsci-sigmond in _hl pw and keeps '<-- image default, CHANGE IT' outside it
```

and in `test-panel-remote.sh`, after it renders each RAC scene: `✓ RAC block carries no escape` (no `\x1b` in `$RACBLOCK`).

- [ ] **Step 2: Run to verify it fails**

Run: `bash test-panel-colour.sh; bash test-panel-remote.sh`
Expected: `✗ could not extract the panel_colour block` and a non-zero exit; test-panel-remote passes (its new assertion already holds, which pins the boundary).

- [ ] **Step 3: Implement** the block, wrap the tokens in `PANEL`, and split the write loop so `/etc/motd` gets the stripped text. Keep `_scrub_panel_file` and both sed range deletes unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `bash test-panel-colour.sh && bash test-panel-startup.sh && bash test-panel-scrub.sh && bash test-panel-remote.sh && bash test-panel-agent.sh && bash test-console-storm.sh`
Expected: all ✓, exit 0. Mutation check: wrap the opening marker line in `_hl addr`; the marker assertion must fail.

- [ ] **Step 5: Commit**

```bash
git add firstboot-v3.sh test-panel-colour.sh test-panel-remote.sh
git commit -m "panel: colour for addresses, logins and the default password on the console (v3.82 row 2)"
```

---

### Task 7: psk-recorder gets a writable private /tmp (row 4)

Repo: **psk-recorder**. `systemd/psk-recorder@.service` sets `PrivateTmp=false` with `ProtectSystem=strict`, so /tmp stays read-only and Qt5's QSharedMemory (jt9 in `-s` mode, driven by `jt9_decode`) cannot make its key file. Each instance's jt9 children share one private /tmp under `PrivateTmp=true`, which is what QSharedMemory needs.

Other units checked: `wspr-recorder@.service` and `wspr-uploader.service` already set `PrivateTmp=true`. `meteor-scatter@.service` keeps `PrivateTmp=false`, but it runs `jt9 --msk144` on files, not in shared-memory mode, and it has decoded live spots under that unit (B4, 2026-07-24). No other unit needs the change.

**Files:**
- Modify: `systemd/psk-recorder@.service` (`PrivateTmp=false` → `PrivateTmp=true`, with a comment naming QSharedMemory's key file)
- Test: new `tests/test_unit_sandbox.py`

- [ ] **Step 1: Write the failing test**

```python
UNIT = Path(__file__).resolve().parents[1] / "systemd" / "psk-recorder@.service"
def _settings():   # [(key, value)] for non-comment 'Key=Value' lines in [Service]
def test_jt9_gets_a_writable_private_tmp():
    s = _settings()
    assert ("PrivateTmp", "true") in s and ("PrivateTmp", "false") not in s
def test_the_sandbox_stays_strict():
    assert ("ProtectSystem", "strict") in _settings()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_unit_sandbox.py -v`
Expected: FAIL on `test_jt9_gets_a_writable_private_tmp`.

- [ ] **Step 3: Implement** the one-line change and its comment.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (whole suite, ~222 tests).

- [ ] **Step 5: Commit**

```bash
git add systemd/psk-recorder@.service tests/test_unit_sandbox.py
git commit -m "unit: PrivateTmp=true, so jt9's Qt shared memory can make its key file (v3.82 row 4)"
```

---

### Task 8: RTCM3 stray-preamble regression tests (candidate)

Repo: **hf-timestd**. Rob's bus message (2026-10-24 22:08Z) describes a backgrounded one-liner that never returned. It imported a module `probe` from a scratchpad (session dbde85d2, 2026-10-05; the scratchpad now holds only an empty `mut/` directory) and fed it noise holding one stray `\xd3\x00\x10`. Its socket sat in CLOSE-WAIT.

Neither shipped candidate loops. `sigmond/lib/sigmond/discovery/http_gnss.py` reads once (`_TCP_PEEK_BYTES = 256`) and only looks at the first byte. In `hf_timestd/core/rtcm3_adapter.py`, `looks_like_rtcm3` and `RTCM3Adapter._frames` both resync at preamble+1 on a CRC failure (since 19972d4). Measured 2026-10-25 with Rob's exact input: both return in under 0.1 ms. `scripts/live_vtec.py` breaks out on an empty `recv` and caps its sniff at 4096 bytes. The `probe` module that hung appears to have been a prototype or a mutant. This task therefore pins the behaviour with Rob's input and proves the tests can see a hang. It changes production code only if Step 2 fails.

**Files:**
- Test: `tests/unit/test_rtcm3_adapter.py`
- Modify (only if Step 2 fails): `src/hf_timestd/core/rtcm3_adapter.py:80-149`

- [ ] **Step 1: Write the tests**

```python
def _noise_with_one_stray_preamble():
    r = random.Random(500)
    b = bytearray(x for x in (r.randrange(256) for _ in range(4000)) if x != 0xD3)[:1500]
    b[500:503] = b'\xd3\x00\x10'
    return bytes(b)

NMEA = (b'$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n' * 30)
NMEA_STRAY = NMEA[:500] + b'\xd3\x00\x10' + NMEA[500:]

def _returns_within(fn, seconds=2.0):    # run fn in a daemon thread; assert it finished
def test_stray_preamble_in_noise_returns_promptly():
    assert _returns_within(lambda: looks_like_rtcm3(_noise_with_one_stray_preamble())) is False
def test_stray_preamble_in_nmea_returns_promptly():
    assert _returns_within(lambda: looks_like_rtcm3(NMEA_STRAY)) is False
def test_adapter_drops_a_stray_preamble_in_noise():
    ad = RTCM3Adapter(BroadcastEphemeris())
    assert _returns_within(lambda: list(ad.process_data(_noise_with_one_stray_preamble()))) == []
    assert ad.buffer == b''
def test_adapter_drops_a_stray_preamble_in_nmea(): ...    # same with NMEA_STRAY
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest --override-ini addopts="-ra" tests/unit/test_rtcm3_adapter.py -v`
Expected on current main: PASS. If any test fails, go to Step 3; otherwise skip to Step 4.

- [ ] **Step 3 (only if Step 2 failed): Fix the resync.** On a frame that fails its length or CRC check, advance one byte past the preamble; give `looks_like_rtcm3` a `max_scan: int = 8192` byte budget and return False beyond it.

- [ ] **Step 4: Prove the tests can see a hang (mutation)**

Change `i += 1` in `looks_like_rtcm3` to `pass`, and `self.buffer = self.buffer[1:]` in `_frames` to `pass`. Run Step 2's command: each related test must fail by timeout, within about 2 s each. Revert both mutations and run again: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_rtcm3_adapter.py   # plus rtcm3_adapter.py if Step 3 ran
git commit -m "rtcm3: pin the resync on a stray preamble in noise and in NMEA"
```

---

### Task 9: Build, rig and docs (controller checklist)

Run by the controller after Tasks 1-8 land and the bus says the rig stands free. Announce on the bus before the rig takes the image; post the rig claim and release. All rig commands run on `root@192.168.1.182` in the rig's staging directory, after `sync-rig.sh`.

- [ ] Confirm rows 1-7 landed: open `sigmond/tasks/release-v382.md` and mark each row with its commit. A row that cannot land goes to Michael, never quietly to v3.83.
- [ ] Full suites green: sigmond `.venv/bin/pytest -q`; appliance `for t in test-*.sh; do bash "$t" || echo "FAIL $t"; done` (skip the rig-only `test-nested-*`/`test-update-*`); psk-recorder and hf-timestd suites as in Global Constraints.
- [ ] Push sigmond, sigmond-appliance, psk-recorder, hf-timestd (fast-forward to main). The controller tells Rob on the bus what changed.
- [ ] `sync-rig.sh`, then `build-golden-vm.sh` (golden VM from pushed main; its manifest now carries the new pins).
- [ ] Tag the appliance `v3.82` on a clean HEAD, then `build-usb-v3.sh --release`. The image lands in `pending/`.
- [ ] `test-nested-v3.sh` on the new image → `PHASE D PASS — NESTED TEST COMPLETE` for this exact filename. Also check on the nested PM: `command -v whiptail` (if absent, add `whiptail`, `libnewt0.52`, `libslang2` to `offline-debs/` and rebuild), and that `/usr/local/sbin/sigmond-console-menu` and `/etc/profile.d/sigmond-console-menu.sh` exist.
- [ ] On the nested station after bring-up: each client checkout matches the image manifest. The appliance 9fc247c drift check in test-update-v3.sh should now print no WARN line.
- [ ] `test-update-v3.sh --image <v3.81 image> --target <v3.82 sigmond sha>`. Keep `U2_ALLOW_ROLLBACK_KINDS=dirty` and the G-ter nest limit in place; record whether the run needed them. `smd update` does not re-exec itself after it pulls sigmond, so the rig's E and G-ter updates run the baseline image's smd, not v3.82's. Row 7 gets no rig evidence, and the G-ter nest limit stays necessary. Only row 6 runs in the target smd (restore, step G), so `U2_ALLOW_ROLLBACK_KINDS=dirty` may go unused.
- [ ] On the nested v3.82 station with no hf-timestd config, run one `smd update --apply` and expect `hf-timestd: install.sh exit 0` with "setup wizard deferred". This is the only live check of row 7.
- [ ] Hardware check of rows 2 and 3 (needs a real monitor; the nest cannot show it): the panel colours read well on VT1, and a console login over the split console reaches the menu, while `ssh root@<PM>` from the LAN gets the plain shell.
- [ ] Update `sigmond-appliance/INSTALL.md` and `docs/install-page.html` for v3.82 (the menu and the colour change what an operator sees at the console). Commit and push.
- [ ] `bless-release.sh v3.82`, then `--apply` once every gate passes.
