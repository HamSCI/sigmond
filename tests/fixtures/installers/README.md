# Real component installers, copied as test fixtures

`tests/test_installer_args.py` reads these to check `sigmond.installer_args`
against installers that exist, not hand-written snippets. They are copies;
nothing runs them.

| File | Origin |
|---|---|
| `hf-timestd-install.sh` | hf-timestd `scripts/install.sh` at ff9cec9 (unquoted `case $1 in` loop with `--yes\|-y`) |
| `hf-tec-613408c-install.sh` | hf-tec `scripts/install.sh` at 613408c, the v3.72 release; it exits 2 on any argument but `--build-only` |
| `hf-tec-a3a0f51-install.sh` | hf-tec `scripts/install.sh` at a3a0f51, which accepts `--yes` (added in e54fb17) |

Refresh a copy with `git -C ../<repo> show <sha>:scripts/install.sh`, and update this table.
