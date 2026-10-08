# Standalone model copy verification

The standalone `open_crop_model/` package contains the selected `management_refit` engine and effective parameters. All 143 frozen scientific Python files and all 164 resolved case parameter sets match the current product. Thirty included process tests pass. Four representative wheat and maize seasons, run from outside the repository, reproduce the saved daily and seasonal outputs.

Each replay preserves exact inputs, weather, observations, unchanged source snapshots and SHA-256 records. This is a software-copy equivalence check, not new calibration or independent model validation. Verification details are in `verification/copy_checks.json`.
