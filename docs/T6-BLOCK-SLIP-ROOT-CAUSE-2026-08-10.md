
## Layer-1 mitigations DEPLOYED on B4 (2026-08-10 ~21:55 UTC)

1. **xhci IRQ pin** — irq51 (RX888's controller, was on CPU 6 inside the
   decoder range) → CPUs 0-1 (radiod's reserved pair, ~90 % headroom).
   Live + persistent (`rx888-irq-affinity.service`, verified re-applies).
   This closes the actual starvation path: hard-IRQ service is immune to
   decoder nice levels and unit fences.
2. **Decode-wave spreading** — `WSPR_DECODE_LONG_SLOTS=2` drop-in
   (`wspr-recorder@.service.d/20-decode-spread.conf`): the F15/F30 wall
   at :00/:30 becomes a 2-wide queue draining into the idle valleys
   between cycle ends (operator observation: dramatic spikes at cycle
   end, idle between). Long-mode results are not latency-critical.
3. Existing unit fences (radiod 0-1; everything else 2-13, decoders
   Nice=5) confirmed intact — they were necessary but not sufficient
   without (1).

**Verification:** diff-CSV slip rate after 2026-08-10 21:55 vs baseline
6.6/h (132 slips / 20 h). Expect ≈0. The Layer-2 calibrator fix is still
required — gaps can recur under any future load pattern (loss is logged
by radiod's measured-rate line + the fork's step logging; Layer 2 makes
labels correct through them).

**Fleet/image follow-ups (image-completeness):** IRQ pinning belongs in
sigmond's cpu-affinity applier; the LONG_SLOTS cap belongs in
wspr-recorder's shipped defaults/config; both must land in the DASI image,
not remain B4 hand-applies.
