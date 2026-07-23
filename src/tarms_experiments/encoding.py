"""Deterministic byte encoding used by signatures and Merkle leaves."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


class EncodingError(ValueError):
    """Raised when an event cannot be encoded under the TARMS profile."""


def _normalise(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise EncodingError(
                "integers must remain within the cross-language safe integer range"
            )
        return value
    if isinstance(value, float):
        raise EncodingError("float values are not permitted; use scaled integers")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalised: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise EncodingError("object keys must be strings")
            normalised_key = unicodedata.normalize("NFC", key)
            if normalised_key in normalised:
                raise EncodingError(
                    f"object-key normalization collision for {normalised_key!r}"
                )
            normalised[normalised_key] = _normalise(nested)
        return normalised
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_normalise(item) for item in value]
    raise EncodingError(f"unsupported value type: {type(value).__name__}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping):
        raise EncodingError("canonical event must be a mapping")
    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
