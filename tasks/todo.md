# hf-timestd offset-judge integration (2026-08-05)

Baseline: 31 failed / 1064 passed / 46 skipped (textual-missing environmental).

- [ ] `[timing] honor_radiod_restart_request` in topology.py + example toml + tests
- [ ] lib/sigmond/timing_judge.py — artifact load/validate, restart-request
      processing (stamp, never-twice), status renderer; tests
- [ ] bin/sigmond-radiod-watchdog — honor step wired into main()
- [ ] bin/smd cmd_status — timing judge section
- [ ] Full suite: zero NEW failures
