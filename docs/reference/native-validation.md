# Native app validation

Use the [testing runbook](../runbooks/testing.md) for current commands and
[build/install runbook](../runbooks/build-install.md) for signed artifacts,
backups and guarded installation. Test stores and screenshots must be synthetic;
private device information and deployment values stay in ignored local storage.

## Automated coverage and runtime limits

The native executable checks cover chart layouts, pointer dispatch, keyboard
routing, civil timestamp parsing, bridge fixtures, journal recovery and live
engine feature flows. Live tests include lost acknowledgements, crash restart,
single store ownership, delayed edits, two-store synchronization and explicit
conflict resolution. Python checks exercise receipt/outbox rollback, minimum
durations, logical days, foreign timers, database ownership and compatible Qt
startup. Phone Node/browser harnesses separately cover the web clients.

These checks do not replace real-window input and layout review. Verify the
half-screen window, drag/drop/Escape, exact session selection, autosave feedback,
invalid field correction, fullscreen Spaces, nested panels, custom colours,
reduced motion and file-dialog cancellation with a disposable store. Check both
ordinary clicks and drags over controls; a drag must never also click.

Command Line Tools compile the app and run the executable suite without full
Xcode. XCUIAutomation needs full Xcode and has not been run for this update.
Physical trackpad gestures, additional display scaling, overnight energy use and
long-term daily use need separate hands-on evidence. Do not claim accessibility
certification or a physical-device check from synthetic tests. VoiceOver testing
is outside the current validation scope.

The native production identity is `com.jobtracker.app` on the established store;
the pilot identity is separate. A staged signed build proves packaging, not
installation. Installer results and pending status determine whether production
has changed. App rollback preserves the current database and pending work.

## Long-history weight fitting

Measured on 25 October 2026; retained here as a performance record, independent
of the normal feature and release documentation.


The native `weight.view` request uses
the shared [Python fit](../../jobtracker/core/weight_plot.py). The initial
2,000-reading baseline had a 67.9 ms median over 11 warm requests. Profiling
identified LOESS as the largest cost: its tricube kernel repeatedly called
general powers and `min` for every neighbour. Direct cubic multiplication and
an equivalent bounded-distance branch remove that repeated work. No points,
neighbours, smoothing settings or history detail are removed.

The fixed synthetic store contains 2,000 daily scale readings from 20 April
2021 through 10 October 2026, with microsecond timestamps and sinusoidal weights.
Measurements used macOS 27.0.1 arm64 and Python 3.12.10, `range=all`,
`smoothing=loess`, `span=0.04`, Historic off. The observation starts at command
entry and ends after the existing in-process engine adapter encodes and decodes
the JSON response. Store setup, process launch, pipe scheduling and drawing are
excluded. Each comparison used one disposable store, three warmups and 11
interleaved before/after pairs, alternating which version ran first. The old fit
was loaded from a source snapshot; all other code and inputs were identical.

| Comparison | Before median | After median | Reduction |
| --- | --- | --- | --- |
| First 11 pairs | 72.3 ms | 54.7 ms | 24.3% |
| Independent repeat, 11 pairs | 75.1 ms | 58.4 ms | 22.2% |

All non-trend response fields compare exactly, and all 2,000 fitted coordinates
remain present in the same order. The maximum fitted-weight difference is
`4.27e-14` kg, below display precision. Profiled function calls fall from
476,239 to 316,239; traced Python peak allocation stays about 5.45 MiB.
Existing scipy/statsmodels reference fixtures and new 1,000-point irregular
linear-history cases at four spans check mathematical results independently.

These are warm measurements on one Mac, not UI/launch or tail-latency claims.
The independent repeat includes a 183.9 ms after-sample outlier. An extra run
overlapping focused tests is excluded from the table because of CPU contention.
No benchmark framework or runtime dependency was added; the one-off script,
fixed source snapshot, profiles and raw samples are in ignored `.local/ai10-*`.
This measurement does not establish which build is installed or deployed.
