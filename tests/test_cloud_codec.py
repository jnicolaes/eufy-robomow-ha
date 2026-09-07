"""Regression tests for independently testable protocol codecs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_cloud_module():
    module_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "eufy_robomow"
        / "cloud.py"
    )
    spec = importlib.util.spec_from_file_location("eufy_robomow_cloud", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cloud = _load_cloud_module()


@pytest.mark.parametrize("value", [0, 1, 127, 128, 359, 2**32, 2**64 - 1])
def test_varint_round_trip(value: int) -> None:
    encoded = cloud._varint_encode(value)

    decoded, position = cloud._varint_decode(encoded, 0)

    assert decoded == value
    assert position == len(encoded)


def test_varint_decode_rejects_truncated_input() -> None:
    with pytest.raises(ValueError, match="Truncated"):
        cloud._varint_decode(b"\x80", 0)


def test_varint_decode_rejects_oversized_input() -> None:
    with pytest.raises(ValueError, match="exceeds 10 bytes"):
        cloud._varint_decode(b"\x80" * 11, 0)


@pytest.mark.parametrize(
    ("edge_mm", "path_mm", "travel_speed", "blade_speed", "pad_direction"),
    [
        (0, 80, "slow", "slow", 0),
        (70, 100, "normal", "fast", 90),
        (-150, 120, "fast", "normal", 359),
    ],
)
def test_dp155_round_trip(
    edge_mm: int,
    path_mm: int,
    travel_speed: str,
    blade_speed: str,
    pad_direction: int,
) -> None:
    blob = cloud._encode_dp155(
        edge_mm=edge_mm,
        path_mm=path_mm,
        travel_speed=travel_speed,
        blade_speed=blade_speed,
        pad_direction=pad_direction,
    )

    assert cloud._decode_dp155(blob) == {
        "edge_mm": edge_mm,
        "path_mm": path_mm,
        "travel_speed": travel_speed,
        "blade_speed": blade_speed,
        "pad_direction": pad_direction,
    }


def test_dp155_decode_rejects_invalid_base64() -> None:
    with pytest.raises(ValueError, match="valid base64"):
        cloud._decode_dp155("not-base64!")
