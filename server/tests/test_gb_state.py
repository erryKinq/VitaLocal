"""Which snapshot is parsed how: full or incremental, from where, or not at all."""

from datetime import UTC, datetime, timedelta

from vitalocal.ingest import gb_state

UNITS = {"GARMIN_ACTIVITY_SAMPLE": "s", "GARMIN_STRESS_SAMPLE": "ms", "GARMIN_NAP_SAMPLE": "ms"}
T = 1_790_000_000  # seconds
H = 3600
AS_OF = datetime(2026, 9, 21, 12, tzinfo=UTC)


def state(**kw) -> gb_state.State:
    base = dict(
        batch_id=1,
        artifact_sha256="a" * 64,
        as_of=AS_OF,
        parser_version="1.4.0",
        schema_version=140,
        table_hwm={"GARMIN_ACTIVITY_SAMPLE": T, "GARMIN_STRESS_SAMPLE": T * 1000},
        last_full_at=None,
    )
    return gb_state.State(**{**base, **kw})


def plan(st, bounds, **kw):
    args = dict(parser_version="1.4.0", schema_version=140, table_units=UNITS, window=timedelta(hours=72))
    return gb_state.plan(st, bounds=bounds, **{**args, **kw})


BOUNDS = {
    "GARMIN_ACTIVITY_SAMPLE": (T - 30 * 24 * H, T + 12 * 60),
    "GARMIN_STRESS_SAMPLE": ((T - 30 * 24 * H) * 1000, (T + 12 * 60) * 1000),
}


def test_incremental_windows_per_table_in_their_units():
    p = plan(state(), BOUNDS)
    assert p.mode == "incremental"
    assert p.windows == {"GARMIN_ACTIVITY_SAMPLE": T - 72 * H, "GARMIN_STRESS_SAMPLE": (T - 72 * H) * 1000}


def test_window_never_starts_before_the_table():
    short = {
        "GARMIN_ACTIVITY_SAMPLE": (T - H, T + 60),
        "GARMIN_STRESS_SAMPLE": ((T - H) * 1000, (T + 60) * 1000),
    }
    assert plan(state(), short).windows["GARMIN_ACTIVITY_SAMPLE"] == T - H


def test_a_table_with_its_first_rows_is_read_whole():
    bounds = {**BOUNDS, "GARMIN_NAP_SAMPLE": (T * 1000, T * 1000)}
    p = plan(state(), bounds)
    assert p.mode == "incremental" and p.windows["GARMIN_NAP_SAMPLE"] == T * 1000
    assert "GARMIN_NAP_SAMPLE" in p.reason


def test_full_parse_reasons():
    assert plan(None, BOUNDS).mode == "full"
    assert plan(state(parser_version="1.3.0"), BOUNDS).reason == "parser 1.3.0 -> 1.4.0"
    assert plan(state(schema_version=139), BOUNDS).reason == "database schema 139 -> 140"
    assert plan(state(), BOUNDS, force_full=True).reason == "forced"
    assert plan(state(), BOUNDS, incremental=False).mode == "full"
    # Gadgetbridge was reset: the snapshot ends long before the applied one
    reset = {"GARMIN_ACTIVITY_SAMPLE": (T - 200 * H, T - 100 * H)}
    assert plan(state(), reset).mode == "full"


def test_an_older_snapshot_is_skipped_unless_it_reaches_further():
    st = state()
    older = AS_OF - timedelta(hours=6)
    assert gb_state.is_older(st, older, BOUNDS) is False  # its data reaches further (clock jumped)
    behind = {"GARMIN_ACTIVITY_SAMPLE": (T - 30 * 24 * H, T - 600)}
    assert gb_state.is_older(st, older, behind) is True
    assert gb_state.is_older(st, AS_OF, behind) is False  # same moment: the same snapshot again
    assert gb_state.is_older(None, older, behind) is False
    assert gb_state.is_older(st, None, behind) is False
