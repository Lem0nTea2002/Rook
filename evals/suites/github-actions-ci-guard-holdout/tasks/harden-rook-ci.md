Harden `.github/workflows/offline-tests.yml` in place.

The repository requires least-privilege checkout and bounded jobs. Disable
credential persistence for every checkout step and add a finite timeout to
each job. Preserve the existing permissions, triggers, concurrency, operating
system and Python matrix, action versions, offline model-cost controls, CLI
demo, test command, quality checks, and all other files.
