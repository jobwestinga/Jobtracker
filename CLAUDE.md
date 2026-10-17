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

Stay in this project for local work. If cross-project context is needed or helpful,
use the focused destinations in README's Personal Tech Stack section, or the
optional parent ownership table (`../README.md#where-to-look`) and shared rules
(`../AGENTS.md`). Read only the relevant sibling's entry/rules before editing it.
Standalone clones do not require the parent. Do not search all sibling/history
folders by default; keep each fact and implementation with its owner.

The parent coordinates plans, ideas and connections. Implement this project's
tasks inside this child; it keeps ownership of its domain work.
