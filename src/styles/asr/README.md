# ASR CSS maintenance

The 11 numbered modules are imported from `src/index.css` in cascade order.
Their ordering is intentional. The late `locked` responsive modules preserve
previously approved mobile and tablet layouts; do not sort or merge them
without comparing screenshots and testing controls.

## Qualification

Run `ASR_CSS_VISUAL_BASELINE=/tmp/asr-css-visual-baseline-beta84 bash scripts/asr-css-qualification.sh`.
The baseline path must point to an independently reviewed capture, not a
fresh capture from a failing candidate. Reports are saved under `/tmp`.

The gate includes build, lint, functional UI and browser checks, and 20
screenshots covering five themes and four viewports. Visual checks allow
0.25% pixel variation; they do not guarantee all UI states are identical.

## Optimization findings

The parsed stylesheet contains 1,810 CSS rules and 751 `!important`
declarations. No identical declaration repeated within the same rule was
found. Many apparent selector repetitions are intentional theme, media,
or locked-layout overrides. Removing `!important` or collapsing repeated
selectors without analyzing specificity and context can silently change
approved layouts; do not automate those transformations indiscriminately.

The monolithic file was divided by feature while preserving the exact
original rule sequence, and confirmed with screenshot regression tests.
Source-level regression tests now read the numbered modules in order.

Do not deploy, publish, merge, or restart Asterisk as part of CSS cleanup.
