# scanner/ — frozen reference

This is the original standalone Python scanner (`pro_scan.py` + `scan.py`,
`step1_finviz.py`…`step4_enrich.py`), run from a local venv. It predates
`services/data-engine`, which is now the pipeline actually wired to the
dashboard's "Pro Scanner" tab.

**Frozen.** Kept only as a reference for the original filter logic and
sequencing — do not build new features on it, and a fix here does not
propagate to `services/data-engine`. See `CLAUDE.md` and
`docs/overview.md` for which implementation is live.

`results.json` and `status.json` are generated output from past manual
runs, not source.
