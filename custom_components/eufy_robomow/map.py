"""Strict decoding and normalization for Eufy E15 map snapshots."""

from __future__ import annotations

from dataclasses import dataclass, replace

_MAX_VARINT_BYTES = 10


class MapDecodeError(ValueError):
    """Raised when an E15 map payload is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class Point:
    """One point in the mower's local map coordinate system."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class MapSnapshot:
    """Confirmed read-only E15 geometry from one coherent map snapshot."""

    map_id: int
    boundary: tuple[Point, ...]
    base_areas: tuple[tuple[Point, ...], ...]
    no_go_areas: tuple[tuple[Point, ...], ...]
    pathways: tuple[tuple[Point, ...], ...]
    cleaned_paths: tuple[tuple[Point, ...], ...]
    mower_position: Point | None
    tracking_position: Point | None = None


ProtoValue = int | bytes


@dataclass(frozen=True, slots=True)
class _ProtoMessage:
    fields: dict[int, tuple[ProtoValue, ...]]

    def integer(self, field_number: int, *, default: int | None = None) -> int | None:
        values = tuple(
            value
            for value in self.fields.get(field_number, ())
            if isinstance(value, int)
        )
        return values[-1] if values else default

    def byte_strings(self, field_number: int) -> tuple[bytes, ...]:
        return tuple(
            value
            for value in self.fields.get(field_number, ())
            if isinstance(value, bytes)
        )

    def messages(self, field_number: int) -> tuple[_ProtoMessage, ...]:
        return tuple(
            _decode_message(value) for value in self.byte_strings(field_number)
        )


def parse_map_snapshot(
    map_payload: bytes,
    clean_path_payload: bytes,
    navigation_path_payload: bytes,
) -> MapSnapshot:
    """Decode the three E15 P2P stream files into one typed snapshot."""
    try:
        map_root = _decode_message(map_payload)
        map_envelope = _only_message(map_root, 2, "map envelope")
        map_record = _only_message(map_envelope, 1, "map record")
        clean_path = _decode_message(clean_path_payload)
        navigation_path = _decode_message(navigation_path_payload)

        map_id = _required_integer(map_record, 16, "map identifier")
        boundary = _decode_boundary(map_record)
        cleaned_paths, tracking_position = _decode_cleaned_paths(clean_path)

        mower_messages = map_record.messages(8)
        mower_position = (
            _decode_position(
                mower_messages[-1],
                x_field=2,
                y_field=3,
                allow_omitted_zero=True,
            )
            if mower_messages
            else None
        )
        if mower_position is None:
            mower_position = _decode_position(
                navigation_path,
                x_field=2,
                y_field=3,
            )

        base_areas = tuple(
            polygon
            for area in map_record.messages(26)
            for polygon in (_decode_nested_polygon(area, polygon_field=2),)
            if polygon
        )
        no_go_areas = tuple(
            polygon
            for restriction in map_record.messages(11)
            for polygon in (_decode_repeated_points(restriction, field_number=1),)
            if polygon
        )
        pathways = tuple(
            path
            for pathway in map_record.messages(18)
            for path in (_decode_repeated_points(pathway, field_number=1),)
            if path
        )
    except MapDecodeError:
        raise
    except (OverflowError, ValueError) as exc:
        raise MapDecodeError(f"Malformed E15 map payload: {exc}") from exc

    return MapSnapshot(
        map_id=map_id,
        boundary=boundary,
        base_areas=base_areas,
        no_go_areas=no_go_areas,
        pathways=pathways,
        cleaned_paths=cleaned_paths,
        mower_position=mower_position,
        tracking_position=tracking_position,
    )


def merge_live_snapshots(
    previous: MapSnapshot,
    current: MapSnapshot,
) -> MapSnapshot:
    """Accumulate deduplicated mowing coverage from consecutive live deltas."""
    if previous.map_id != current.map_id:
        return current

    cleaned_paths = _unique_coverage_segments(
        previous.cleaned_paths,
        current.cleaned_paths,
    )
    return replace(
        current,
        cleaned_paths=cleaned_paths,
        tracking_position=current.tracking_position or previous.tracking_position,
    )


def _decode_message(payload: bytes) -> _ProtoMessage:
    fields: dict[int, list[ProtoValue]] = {}
    position = 0

    while position < len(payload):
        tag, position = _read_varint(payload, position)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if field_number == 0:
            raise MapDecodeError("Protobuf field number 0 is invalid")

        value: ProtoValue
        if wire_type == 0:
            value, position = _read_varint(payload, position)
        elif wire_type == 2:
            length, position = _read_varint(payload, position)
            end = position + length
            if end > len(payload):
                raise MapDecodeError("Length-delimited protobuf field exceeds payload")
            value = payload[position:end]
            position = end
        else:
            raise MapDecodeError(f"Unsupported protobuf wire type {wire_type}")

        fields.setdefault(field_number, []).append(value)

    return _ProtoMessage(
        fields={field_number: tuple(values) for field_number, values in fields.items()}
    )


def _read_varint(payload: bytes, position: int) -> tuple[int, int]:
    value = 0
    for byte_index in range(_MAX_VARINT_BYTES):
        if position >= len(payload):
            raise MapDecodeError("Truncated protobuf varint")
        current = payload[position]
        position += 1
        value |= (current & 0x7F) << (7 * byte_index)
        if current & 0x80 == 0:
            return value, position
    raise MapDecodeError("Protobuf varint exceeds 10 bytes")


def _decode_boundary(map_record: _ProtoMessage) -> tuple[Point, ...]:
    boundary_containers = map_record.messages(10)
    if not boundary_containers:
        raise MapDecodeError("E15 map has no boundary container")

    boundary_layers = boundary_containers[-1].messages(3)
    if not boundary_layers:
        raise MapDecodeError("E15 map has no active boundary")

    boundary = _decode_repeated_points(boundary_layers[-1], field_number=1)
    if len(boundary) < 3:
        raise MapDecodeError("E15 map boundary has fewer than three points")
    return boundary


def _decode_cleaned_paths(
    clean_path: _ProtoMessage,
) -> tuple[tuple[tuple[Point, ...], ...], Point | None]:
    """Separate mowing coverage from transport and pose events."""
    segments: list[tuple[Point, ...]] = []
    current: list[Point] = []
    tracking_position: Point | None = None

    for path_item in clean_path.messages(7):
        point_messages = path_item.messages(1)
        if not point_messages:
            continue
        point = _decode_position(point_messages[-1], allow_omitted_zero=True)
        if point is None:
            continue

        tracking_position = point
        path_event = path_item.integer(2, default=0) or 0
        if path_event != 0:
            if current:
                segments.append(tuple(current))
                current = []
            continue

        current.append(point)

    if current:
        segments.append(tuple(current))
    return tuple(segments), tracking_position


def _unique_coverage_segments(
    *path_groups: tuple[tuple[Point, ...], ...],
) -> tuple[tuple[Point, ...], ...]:
    """Return stable continuous paths without duplicate coverage."""
    merged: list[list[Point]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    for paths in path_groups:
        for path in paths:
            for start, end in zip(path, path[1:], strict=False):
                if start == end:
                    continue
                start_key = (start.x, start.y)
                end_key = (end.x, end.y)
                key = (
                    (start_key, end_key)
                    if start_key <= end_key
                    else (end_key, start_key)
                )
                if key in seen:
                    continue
                seen.add(key)
                if merged and merged[-1][-1] == start:
                    merged[-1].append(end)
                else:
                    merged.append([start, end])

    return tuple(tuple(path) for path in merged)


def _decode_nested_polygon(
    message: _ProtoMessage,
    *,
    polygon_field: int,
) -> tuple[Point, ...]:
    polygon_messages = message.messages(polygon_field)
    if not polygon_messages:
        return ()
    return _decode_repeated_points(polygon_messages[-1], field_number=1)


def _decode_repeated_points(
    message: _ProtoMessage,
    *,
    field_number: int,
) -> tuple[Point, ...]:
    return tuple(
        point
        for point in (
            _decode_position(candidate, allow_omitted_zero=True)
            for candidate in message.messages(field_number)
        )
        if point is not None
    )


def _decode_position(
    message: _ProtoMessage,
    *,
    x_field: int = 1,
    y_field: int = 2,
    allow_omitted_zero: bool = False,
) -> Point | None:
    encoded_x = message.integer(x_field)
    encoded_y = message.integer(y_field)
    if not allow_omitted_zero and encoded_x is None and encoded_y is None:
        return None
    return Point(
        x=_decode_sint32(encoded_x or 0),
        y=_decode_sint32(encoded_y or 0),
    )


def _decode_sint32(value: int) -> int:
    if value < 0 or value > 0xFFFFFFFF:
        raise MapDecodeError("E15 coordinate is outside sint32 range")
    return (value >> 1) ^ -(value & 1)


def _only_message(
    message: _ProtoMessage,
    field_number: int,
    label: str,
) -> _ProtoMessage:
    values = message.messages(field_number)
    if len(values) != 1:
        raise MapDecodeError(f"E15 {label} must occur exactly once")
    return values[0]


def _required_integer(
    message: _ProtoMessage,
    field_number: int,
    label: str,
) -> int:
    value = message.integer(field_number)
    if value is None:
        raise MapDecodeError(f"E15 map has no {label}")
    return value
