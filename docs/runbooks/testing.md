# Testing

Run from the repository root. Tests redirect the eager compatibility database
before app imports and use temporary SQLite fixtures. Native tests use offscreen
Qt; browser tests run a local mock API with isolated browser storage. Keep all
checks independent of live credentials, servers and user databases.

## Choose an environment

Use a Python interpreter with the runtime and test dependencies. A project virtual
environment is convenient; an existing suitable interpreter works too.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

The development requirements include pytest, PySide6, server dependencies and
Starlette's `httpx` test client. Check the selected environment with
`python -m pip show pytest PySide6 fastapi httpx`.

The developer tool accepts `--python /path/to/python` for test/build commands and
as a global option. Select it explicitly if the shell's Python differs from the
one containing the dependencies. Do not silently substitute another interpreter
after a missing-dependency failure.

## During a change

```sh
python scripts/dev.py context weight.daily-entry
python scripts/dev.py test weight.daily-entry --python /path/to/python
python scripts/dev.py check
```

Use the feature ID relevant to the change. The context output and
[feature index](../generated/feature-index.md) explain the selected tests.
When a public owner moves, update [feature metadata](../architecture/features.json)
and compatibility contracts in the same change. Shared fixtures belong under
[tests/support](../../tests/support/__init__.py); native regressions are grouped
under `tests/ui/`.

Add regressions for behavior that could lose data or break rules: timer ownership,
captured timestamps, minimum duration, day boundaries, stale responses,
transaction failures, dropped acknowledgements, outbox ordering and import/restore
identity. Avoid tests that only repeat a low-impact implementation detail.

## Before delivering code

```sh
python scripts/dev.py test --python /path/to/python
python scripts/dev.py check
python scripts/dev.py mobile
```

The full test command runs pytest. `check` also runs the repository privacy
guard; enable its staged-content hook with `git config core.hooksPath .githooks`.
`mobile` runs the Node regressions; Node must
be installed. The pytest mobile wrapper also invokes Node when available, so
report any skip instead of calling it a pass. After checks pass, repeat or broaden
them only for new changes, failures or unresolved concerns.

The browser command runs four harnesses sequentially, using Playwright and
Chromium: the full app, feature sheets, Subject Quick Add and accessibility.

```sh
python scripts/dev.py browser
```

It honors `PLAYWRIGHT_MODULE` for an existing Playwright installation and
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` for an existing Chromium executable. Browser
tooling is a development dependency, not a phone runtime dependency; do not add
a bundler or package manager to the app to run these checks.

For icon-only work, `JOBTRACKER_BROWSER_SCOPE=icons node tests/mobile/browser.cjs`
checks the installation guide, both apps' icons and manifests, the root default icon,
reflow and absence of writes. It saves an installation-guide preview using only public artwork.

[Feature-sheet checks](../../tests/mobile/feature-sheets.cjs) inject isolated
write callbacks into the real goal, template and settings factories. They cover
durable duplicate guards, draft retention, toggle races, recurrence validation
and failed flush/disconnect handling independently of the full app mock API.

[Subject Quick Add](../../tests/mobile/subject-quick-add.cjs) uses the real
outbox with isolated storage to check presets, custom totals, captured endpoints,
double clicks, offline reload, storage failures and lost acknowledgements.
[Accessibility checks](../../tests/mobile/accessibility.cjs) exercise keyboard
focus, nested fullscreen/sheet dismissal, 320px layouts, 200% text, visible focus,
contrast, reduced motion, chart data tables, Subject Quick Add and named colour
swatches; reflow covers every tab. The full-app harness also checks persistent
sync feedback through offline saves, reload and replay, and that the status line
opens its details.

These checks follow the [WAI modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)
and [reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html).
They do not replace a manual pass in iPhone Safari with VoiceOver: check control
names, swipe order, save/error announcements, modal dismissal and fullscreen
rotation with an unfinished draft. Do not claim WCAG certification from these
synthetic browser checks.

Direct equivalents are useful when diagnosing a wrapper failure:

```sh
/path/to/python -m pytest
node --test tests/mobile/*.test.mjs
node tests/mobile/browser.cjs
```

Document the interpreter, checks run and material limitations. Local integration
tests prove the source contract, not the version running on a deployed server.
Documentation-only changes require reference/link checks; they do not require
an application rebuild.
