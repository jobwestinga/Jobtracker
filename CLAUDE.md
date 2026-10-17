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

## Personal Tech Stack context

This project also belongs to a local Personal Tech Stack workspace. When that
parent is available, read its overview (`../README.md`),
connection map (`../MAP.md`) and working instructions (`../AGENTS.md`) for a task
that spans projects. For normal work, start in this repository and follow its
own instructions. These optional parent links are not required for a standalone
clone; application code, history and setup remain owned here.
