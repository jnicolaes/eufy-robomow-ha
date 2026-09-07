"""Tests for deterministic, script-free E15 map rendering."""

from __future__ import annotations

import re

from custom_components.eufy_robomow.map import MapSnapshot, Point
from custom_components.eufy_robomow.map_renderer import (
    MAP_CONTENT_TYPE,
    render_map_svg,
)


def _snapshot() -> MapSnapshot:
    return MapSnapshot(
        map_id=539,
        boundary=(
            Point(0, 0),
            Point(100, 0),
            Point(100, 100),
            Point(0, 100),
        ),
        base_areas=(
            (
                Point(40, 90),
                Point(70, 90),
                Point(70, 110),
                Point(40, 110),
            ),
        ),
        no_go_areas=(
            (
                Point(20, 20),
                Point(30, 20),
                Point(30, 30),
                Point(20, 30),
            ),
        ),
        pathways=(
            (Point(-20, 50), Point(10, 50)),
            (Point(40, 40), Point(60, 60)),
        ),
        cleaned_paths=((Point(10, 10), Point(20, 10)),),
        mower_position=Point(45, 50),
        tracking_position=Point(20, 10),
    )


def test_render_map_svg_is_deterministic_and_script_free() -> None:
    rendered = render_map_svg(_snapshot())

    assert rendered == render_map_svg(_snapshot())
    assert MAP_CONTENT_TYPE == "image/svg+xml"
    assert rendered.startswith(b'<svg xmlns="http://www.w3.org/2000/svg"')
    assert b"<title>Eufy mower map 539</title>" in rendered
    assert b"<script" not in rendered.lower()
    assert b'class="boundary"' in rendered
    assert b'class="base-area"' in rendered
    assert b'class="no-go-area"' in rendered
    assert rendered.count(b'class="pathway"') == 2
    assert b'class="mower-marker"' in rendered


def test_render_map_svg_hides_historical_cleaned_path_by_default() -> None:
    assert b'class="cleaned-path"' not in render_map_svg(_snapshot())


def test_render_map_svg_can_show_current_cleaned_path() -> None:
    rendered = render_map_svg(
        _snapshot(),
        include_cleaned_paths=True,
    )

    assert b'class="cleaned-area" clip-path="url(#mowing-boundary-clip)"' in rendered
    assert b'class="cleaned-path"' in rendered
    assert b'class="charging-station"' in rendered
    assert b"scale(.75)" in rendered
    assert b'class="mower-lightning"' not in rendered


def test_render_map_svg_keeps_offset_markers_inside_canvas() -> None:
    snapshot = MapSnapshot(
        map_id=539,
        boundary=(Point(0, 0), Point(100, 0), Point(100, 100), Point(0, 100)),
        base_areas=(),
        no_go_areas=(),
        pathways=(),
        cleaned_paths=((Point(0, 0), Point(100, 100)),),
        mower_position=Point(1000, 1000),
        tracking_position=Point(0, 0),
    )

    rendered = render_map_svg(snapshot, include_cleaned_paths=True)
    positions = re.findall(
        rb'class="(?:charging-station|mower-marker)" '
        rb'transform="translate\(([\d.]+) ([\d.]+)\)',
        rendered,
    )

    assert len(positions) == 2
    assert all(20 <= float(x) <= 700 for x, _ in positions)
    assert all(28 <= float(y) <= 872 for _, y in positions)
