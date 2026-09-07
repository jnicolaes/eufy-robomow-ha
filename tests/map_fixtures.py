"""Synthetic E15 protobuf fixtures without private lawn geometry."""

from __future__ import annotations


def varint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        current = value & 0x7F
        value >>= 7
        encoded.append(current | (0x80 if value else 0))
        if not value:
            return bytes(encoded)


def integer(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def sint32(value: int) -> int:
    return (value << 1) ^ (value >> 31)


def message(field: int, payload: bytes) -> bytes:
    return varint((field << 3) | 2) + varint(len(payload)) + payload


def point(
    x: int,
    y: int,
    *,
    x_field: int = 1,
    y_field: int = 2,
) -> bytes:
    encoded = b""
    if x:
        encoded += integer(x_field, sint32(x))
    if y:
        encoded += integer(y_field, sint32(y))
    return encoded


def map_payload(*, include_embedded_position: bool = True) -> bytes:
    boundary = b"".join(
        message(1, point(x, y)) for x, y in ((-20, 0), (100, 0), (100, 80), (0, 80))
    )
    restriction = b"".join(
        message(1, point(x, y)) for x, y in ((20, 20), (30, 20), (30, 30), (20, 30))
    )
    pathway = b"".join(message(1, point(x, y)) for x, y in ((-30, 10), (-10, 10)))
    base_area = b"".join(
        message(1, point(x, y)) for x, y in ((40, 70), (70, 70), (70, 85), (40, 85))
    )
    embedded_position = (
        message(8, point(9, 10, x_field=2, y_field=3))
        if include_embedded_position
        else b""
    )
    record = b"".join(
        (
            embedded_position,
            message(10, message(3, boundary)),
            message(11, restriction),
            integer(16, 539),
            message(18, pathway),
            message(26, integer(1, 1) + message(2, base_area)),
        )
    )
    return message(2, message(1, record))


def clean_path_payload() -> bytes:
    items = (
        ((0, 0), 0),
        ((10, 0), 0),
        ((20, 0), 1),
        ((30, 0), 1),
        ((200, 200), 1),
    )
    return b"".join(
        message(7, message(1, point(x, y)) + integer(2, event))
        for (x, y), event in items
    )
