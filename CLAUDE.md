# JobTracker developer guide

Read [AGENTS.md](AGENTS.md) and the canonical
[engineering rules](docs/engineering-rules.md) before making changes.

The [documentation index](docs/index.md) links feature owners, architecture,
contracts and testing/build/install/deployment/incident runbooks.
Run `python3 scripts/dev.py context [words-or-feature-id]` to locate the relevant owners
and focused checks. There are no separate Claude-specific rules.

After verification, commit completed work automatically in small, cohesive local
commits: one feature, fix or refactor per commit, with its tests and documentation.
Use descriptive commit messages and stage only the relevant files/hunks. Preserve
unrelated changes. Never push, open a PR, amend or rewrite history unless asked.
