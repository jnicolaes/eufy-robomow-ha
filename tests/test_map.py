"""Tests for the confirmed E15 map decoder."""

from __future__ import annotations

import pytest

from custom_components.eufy_robomow.map import (
    MapDecodeError,
    MapSnapshot,
    Point,
    merge_live_snapshots,
    parse_map_snapshot,
)

from .map_fixtures import (
    clean_path_payload,
    integer,
    map_payload,
    message,
    point,
    varint,
)


def test_parse_map_snapshot_builds_confirmed_geometry() -> None:
    snapshot = parse_map_snapshot(
        map_payload(),
        clean_path_payload(),
        point(11, 12, x_field=2, y_field=3),
    )

    assert snapshot.map_id == 539
    assert snapshot.boundary == (
        Point(-20, 0),
        Point(100, 0),
        Point(100, 80),
        Point(0, 80),
    )
    assert len(snapshot.base_areas) == 1
    assert len(snapshot.no_go_areas) == 1
    assert len(snapshot.pathways) == 1
    assert tuple(map(len, snapshot.cleaned_paths)) == (2,)
    assert snapshot.mower_position == Point(9, 10)
    assert snapshot.tracking_position == Point(200, 200)


def test_parse_map_snapshot_falls_back_to_navigation_position() -> None:
    snapshot = parse_map_snapshot(
        map_payload(include_embedded_position=False),
        clean_path_payload(),
        point(11, 12, x_field=2, y_field=3),
    )

    assert snapshot.mower_position == Point(11, 12)


def test_parse_map_snapshot_keeps_sparse_straight_mowing_pass_connected() -> None:
    clean_path = b"".join(
        message(7, message(1, point(x, y))) for x, y in ((100, -5000), (100, 1000))
    )

    snapshot = parse_map_snapshot(
        map_payload(),
        clean_path,
        point(11, 12, x_field=2, y_field=3),
    )

    assert snapshot.cleaned_paths == (
        (
            Point(100, -5000),
            Point(100, 1000),
        ),
    )
    assert snapshot.tracking_position == Point(100, 1000)


def test_merge_live_snapshots_accumulates_unique_coverage_and_latest_position() -> None:
    shared = Point(20, 20)
    previous = MapSnapshot(
        map_id=539,
        boundary=(Point(0, 0), Point(100, 0), Point(0, 100)),
        base_areas=(),
        no_go_areas=(),
        pathways=(),
        cleaned_paths=((Point(10, 10), shared),),
        mower_position=Point(5, 5),
        tracking_position=shared,
    )
    current = MapSnapshot(
        map_id=539,
        boundary=previous.boundary,
        base_areas=(),
        no_go_areas=(),
        pathways=(),
        cleaned_paths=(
            (shared, Point(10, 10)),
            (shared, Point(30, 30)),
        ),
        mower_position=Point(5, 5),
        tracking_position=Point(30, 30),
    )

    merged = merge_live_snapshots(previous, current)

    assert merged.cleaned_paths == (
        (
            Point(10, 10),
            shared,
            Point(30, 30),
        ),
    )
    assert merged.tracking_position == Point(30, 30)


def test_merge_live_snapshots_resets_when_map_changes() -> None:
    previous = MapSnapshot(
        map_id=539,
        boundary=(Point(0, 0), Point(100, 0), Point(0, 100)),
        base_areas=(),
        no_go_areas=(),
        pathways=(),
        cleaned_paths=((Point(10, 10), Point(20, 20)),),
        mower_position=None,
    )
    current = MapSnapshot(
        map_id=540,
        boundary=previous.boundary,
        base_areas=(),
        no_go_areas=(),
        pathways=(),
        cleaned_paths=(),
        mower_position=None,
    )

    assert merge_live_snapshots(previous, current) is current


def test_parse_map_snapshot_rejects_missing_boundary() -> None:
    payload = message(2, message(1, integer(16, 1)))

    with pytest.raises(MapDecodeError, match="boundary"):
        parse_map_snapshot(payload, b"", b"")


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"\x00", "field number 0"),
        (b"\x08", "Truncated"),
        (b"\x12\x04abc", "exceeds payload"),
        (varint((1 << 3) | 5) + b"\x00\x00\x00\x00", "wire type 5"),
        (b"\x08" + (b"\x80" * 10), "exceeds 10 bytes"),
    ],
)
def test_parse_map_snapshot_rejects_malformed_protobuf(
    payload: bytes,
    error: str,
) -> None:
    with pytest.raises(MapDecodeError, match=error):
        parse_map_snapshot(payload, b"", b"")


def test_parse_map_snapshot_rejects_coordinate_outside_sint32() -> None:
    oversized_coordinate = integer(1, 0x1_0000_0000)
    boundary = b"".join(message(1, oversized_coordinate) for _ in range(3))
    record = integer(16, 1) + message(10, message(3, boundary))

    with pytest.raises(MapDecodeError, match="sint32"):
        parse_map_snapshot(message(2, message(1, record)), b"", b"")
