# Subjects and colour suggestions

Subjects are timed entities, separate from Goals. The native New Subject panel
shows four suggested colours above the eight presets and the custom picker.
The first suggestion is selected initially; editing a subject preserves its
saved colour exactly.

[`suggest_colors`](../../jobtracker/core/colors.py) uses active subject colours
and a bounded palette of bright, colourful candidates. It chooses the candidate
with the greatest distance to its nearest existing colour, adds the pick to the
occupied set, and repeats. Distance uses
[Oklab](https://bottosson.github.io/posts/oklab/) to better reflect visible
lightness and hue differences than raw RGB distance. This does not guarantee
distinction for every form of colour vision; names remain visible in cards,
chart blocks and hover details.

Archived colours are excluded. Invalid colours, duplicate candidates, exhausted
palettes and empty input are handled deterministically. Existing saved colours,
the presets and subject ordering are unchanged.

Native owners: `SubjectsStore.openEditor`, `SubjectEditorModel`,
`SubjectEditorPanel`, and the engine's `subjects.list` response. Regression
coverage lives in [test_colors.py](../../tests/test_colors.py).
