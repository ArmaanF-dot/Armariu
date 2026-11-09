# Model rules (9 Nov 2026)

Rules for choosing and changing the scores, so that a model is never picked on data it was tested on.

1. **Locked window.** Test months from 1 Nov 2026 are held back (`radar/locked.py`). Model comparisons
   refuse to score them. The earlier locked windows (90 days: Jan to Jul 2026; 12 months: Jul to Oct 2025)
   have been used and are ordinary test months now.
2. **Swap rule.** A new model replaces the live one only if it beats it on the locked window with a 95%
   range for the gain that excludes zero, in the group it is meant for (ordinary firms, serial, property).
   `--use-locked` is for that one final check of a model already chosen. Write down the date it was used,
   because the window is then spent.
3. **Quote random-sample rates**, not the radar's own data (about 3x higher). Ranges resample companies.
4. **Drift.** `python -m radar.drift` (also section 6 of the weekly pack) flags the score when the top-10%
   lift for ordinary firms is under 2.5x for 3 months in a row. Then refit and say so openly.
5. **Refit each quarter** and record the monthly lift in `output/refi_watch/sample/accuracy_365d.json`.
6. **Do not retest:** methods and datasets in docs/refi_methods.md, docs/refi_datasets.md and
   docs/refi_features.md came back flat on ordinary firms.
