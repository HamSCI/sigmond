# hf-timestd offset-judge integration (2026-08-05)

Baseline: 31 failed / 1064 passed / 46 skipped (textual-missing environmental).

- [x] `[timing] honor_radiod_restart_request` in topology.py + example toml + tests (38fd239)
- [x] lib/sigmond/timing_judge.py — artifact load/validate, restart-request
      processing (stamp, never-twice), status renderer; tests (34d3183)
- [x] bin/sigmond-radiod-watchdog — honor step wired into main() (d2b947b)
- [x] bin/smd cmd_status — timing judge section (5ba2165)
- [x] Full suite after: 31 failed / 1095 passed / 46 skipped — failed set
      byte-identical to baseline (zero new failures; +31 new tests pass)
