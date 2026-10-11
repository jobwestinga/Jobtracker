"""Synthetic tests of the optional bridge to the sibling plotting project."""

import importlib.util
import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
matplotlib = pytest.importorskip("matplotlib")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")
pytest.importorskip("openpyxl")
matplotlib.use("Agg", force=True)


@pytest.fixture
def graph():
    path = Path(__file__).resolve().parents[2] / "Weight_graph" / "main.py"
    if not path.exists():
        pytest.skip("optional sibling Weight_graph project is not present")
    spec = importlib.util.spec_from_file_location("weight_graph_bridge_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    module.plt.close("all")


def _xlsx(path, rows):
    pd.DataFrame(rows, columns=["Date", "Weight"]).to_excel(path, index=False)


def _json(path, rows):
    path.write_text(json.dumps(rows), encoding="utf-8")


def _scale():
    return pd.DataFrame({
        "DateTime": pd.to_datetime(["2023-02-25T09:00:00", "2023-03-20T09:00:00"]),
        "Weight": [80.0, 84.0], "Body_Fat": [22.0, 25.0],
    })


def _row(day, weight, source="manual", uid=None):
    row = {"measured_at": day, "weight_kg": weight, "source": source}
    if uid is not None:
        row["uid"] = uid
    return row


def test_absent_json_keeps_csv_precedence_and_historic_source_classification(graph, tmp_path):
    manual, historic, app = [tmp_path / name for name in ("manual.xlsx", "historic.xlsx", "app.json")]
    # A historic timestamp can also occur in the scale export. Source identity,
    # rather than timestamp membership, must keep that scale point dense.
    _xlsx(manual, [(pd.Timestamp("2023-02-25"), 70), (pd.Timestamp("2023-02-26"), 79)])
    _xlsx(historic, [(pd.Timestamp("2019-01-01"), 90), (pd.Timestamp("2023-02-25T09:00:00"), 100)])
    regular, combined = graph.load_weight_history(_scale(), manual, historic, app)
    assert regular["Weight"].tolist() == [80, 79, 84]
    assert combined["Weight"].tolist() == [90, 80, 79, 84]
    assert combined["Historic"].tolist() == [True, False, False, False]


def test_json_is_complete_authority_for_edits_deletions_and_same_day_readings(graph, tmp_path):
    manual, historic, app = [tmp_path / name for name in ("manual.xlsx", "historic.xlsx", "app.json")]
    _xlsx(manual, [(pd.Timestamp("2023-02-25"), 100)])
    _xlsx(historic, [(pd.Timestamp("2019-01-01"), 120)])
    _json(app, [
        _row("2023-02-25T09:00:00", 78, "scale"),
        _row("2023-02-25T12:00:00", 77.5),
        _row("2018-01-01T00:00:00", 95, "historic"),
    ])
    inputs_before = {path: path.read_bytes() for path in (manual, historic, app)}
    regular, combined = graph.load_weight_history(_scale(), manual, historic, app)
    assert regular["Weight"].tolist() == [78, 77.5]
    assert combined["Weight"].tolist() == [95, 78, 77.5]
    # The deleted March scale reading and old workbook historical weight stay
    # deleted; the current JSON is not merely an extra merge layer.
    assert len(combined) == 3
    assert all(path.read_bytes() == before for path, before in inputs_before.items())


def test_json_loading_does_not_require_manual_or_historic_workbooks(graph, tmp_path):
    app = tmp_path / "app.json"
    _json(app, [_row("2026-10-10T09:35:00", 80)])
    regular, combined = graph.load_weight_history(_scale(), tmp_path / "missing.xlsx", tmp_path / "also-missing.xlsx", app)
    assert regular["Weight"].tolist() == [80]
    assert combined is None


@pytest.mark.parametrize("payload", [
    {}, [None], [{}], [_row("bad", 80)], [_row("2026-10-10T09:00:00+02:00", 80)],
    [_row("2026-10-10", True)], [_row("2026-10-10", float("nan"))],
    [_row("2026-10-10", 0)], [_row("2026-10-10", 80, "unknown")],
    [_row("2026-10-10", 80, uid="same"), _row("2026-10-11", 81, uid="same")],
])
def test_malformed_export_reports_error_without_using_old_inputs(graph, tmp_path, payload, monkeypatch):
    app = tmp_path / "JobTrackerWeights.json"
    _json(app, payload)
    monkeypatch.setattr(graph, "load_weight_xlsx", lambda path: pytest.fail("must not silently fall back"))
    with pytest.raises(ValueError, match="JobTrackerWeights.json"):
        graph.load_weight_history(_scale(), tmp_path / "manual.xlsx", tmp_path / "historic.xlsx", app)


def test_broken_json_has_clear_error(graph, tmp_path):
    app = tmp_path / "JobTrackerWeights.json"
    app.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read JobTracker weight export JobTrackerWeights.json"):
        graph.load_jobtracker_weights(app)


def test_empty_app_history_skips_weight_figure_without_overwriting_existing_image(graph, tmp_path, monkeypatch):
    app = tmp_path / "app.json"
    _json(app, [])
    regular, combined = graph.load_weight_history(_scale(), tmp_path / "manual.xlsx", tmp_path / "historic.xlsx", app)
    assert regular.empty and combined is None
    monkeypatch.setattr(graph, "OUTPUT_DIR", tmp_path)
    existing = tmp_path / "weight.png"
    existing.write_bytes(b"previous image")
    graph.plot_timeseries(regular, "Weight", "Weight", "kg", existing.name)
    assert existing.read_bytes() == b"previous image"


@pytest.mark.parametrize("rows", [
    [_row("2018-01-01", 95, "historic")],
    [_row("2018-01-01", 95, "historic"), _row("2019-01-01", 90, "historic")],
    [_row("2018-01-01", 95, "historic"), _row("2018-01-01", 93, "historic"), _row("2019-01-01", 90, "historic")],
])
def test_historic_only_and_duplicate_timestamp_curves_stay_finite(graph, tmp_path, rows):
    app = tmp_path / "app.json"
    _json(app, rows)
    history = graph.load_jobtracker_weights(app)
    x, y = graph.trend_with_historic(history, "Weight")
    assert len(x) == len(y) and len(x) > 0
    assert np.isfinite(x).all() and np.isfinite(y).all()


def test_main_weight_bridge_leaves_body_fat_and_fat_free_mass_unchanged(graph, tmp_path, monkeypatch):
    app = tmp_path / "app.json"
    _json(app, [_row("2023-02-25T09:00:00", 78, "scale")])
    scale = _scale()
    monkeypatch.setattr(graph, "JOBTRACKER_JSON", app)
    monkeypatch.setattr(graph, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(graph, "find_newest_export", lambda _: tmp_path / "original.csv")
    monkeypatch.setattr(graph, "load_export", lambda _: scale.copy())
    calls = {}
    monkeypatch.setattr(graph, "plot_timeseries", lambda data, column, *args, **kwargs: calls.update({column: data.copy()}))
    graph.main()
    assert calls["Weight"]["Weight"].tolist() == [78]
    expected = graph.correct_body_fat(scale)
    pd.testing.assert_frame_equal(calls["Body_Fat"], expected)
    pd.testing.assert_frame_equal(calls["Fat_Free_Mass"], expected)


def test_historic_source_is_independent_of_shared_timestamp(graph, tmp_path):
    app = tmp_path / "app.json"
    _json(app, [
        _row("2026-10-10T09:00:00", 78, "scale"),
        _row("2026-10-10T09:00:00", 95, "historic"),
    ])
    regular, combined = graph.load_weight_history(_scale(), tmp_path / "manual.xlsx", tmp_path / "historic.xlsx", app)
    assert regular["Weight"].tolist() == [78]
    assert sorted(combined["Historic"].tolist()) == [False, True]
    x, y = graph.trend_with_historic(combined, "Weight")
    assert len(x) == 1 and y.tolist() == [78]


def test_normal_historic_curve_retains_original_lowess_and_pchip_values(graph):
    from scipy.interpolate import PchipInterpolator

    dense = pd.DataFrame({
        "DateTime": pd.date_range("2026-01-01", periods=60),
        "Weight": np.linspace(80, 78, 60), "Historic": False,
    })
    historic = pd.DataFrame({
        "DateTime": pd.to_datetime(["2018-01-01", "2019-01-01"]),
        "Weight": [95, 90], "Historic": True,
    })
    combined = pd.concat([historic, dense]).reset_index(drop=True)
    dx, dy = graph.trend(dense, "Weight")
    anchors = min(graph.HISTORIC_JOIN_ANCHORS, len(dx))
    knot_x = np.concatenate([graph.mdates.date2num(historic["DateTime"]), dx[:anchors]])
    knot_y = np.concatenate([historic["Weight"].to_numpy(float), dy[:anchors]])
    hx = np.linspace(knot_x[0], dx[0], graph.HISTORIC_CURVE_POINTS)
    expected_x = np.concatenate([hx, dx])
    expected_y = np.concatenate([PchipInterpolator(knot_x, knot_y)(hx), dy])
    actual_x, actual_y = graph.trend_with_historic(combined, "Weight")
    np.testing.assert_array_equal(actual_x, expected_x)
    np.testing.assert_array_equal(actual_y, expected_y)


def test_historic_only_figure_renders_to_isolated_output(graph, tmp_path, monkeypatch):
    app = tmp_path / "app.json"
    _json(app, [_row("2018-01-01", 95, "historic"), _row("2019-01-01", 90, "historic")])
    history = graph.load_jobtracker_weights(app)
    monkeypatch.setattr(graph, "OUTPUT_DIR", tmp_path)
    graph.plot_timeseries(history, "Weight", "Synthetic historic weight", "kg", "synthetic.png", trend_fn=graph.trend_with_historic)
    assert (tmp_path / "synthetic.png").read_bytes().startswith(b"\x89PNG")

