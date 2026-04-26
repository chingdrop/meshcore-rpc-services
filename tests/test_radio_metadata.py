"""Tests for _extract_radio_metadata in the service module.

The helper has to handle three realistic shapes:
  1. Nested gateway payload (Event __dict__ — the real wire shape)
  2. Flat payload (legacy/test injection)
  3. Garbage / non-JSON

It's a pure function so we test it directly without spinning anything up.
"""
from __future__ import annotations

import json

from meshcore_rpc_services.transport.service import _extract_radio_metadata


def test_nested_payload_extracts_uppercase_fields():
    payload = json.dumps({
        "type": "EventType.CONTACT_MSG_RECV",
        "payload": {
            "type": "PRIV",
            "text": "{}",
            "pubkey_prefix": "a1b2c3d4e5f6",
            "RSSI": -88,
            "SNR": 7.5,
        },
        "attributes": {},
    }).encode("utf-8")
    rssi, snr = _extract_radio_metadata(payload)
    assert rssi == -88
    assert snr == 7.5


def test_nested_payload_snr_only():
    """v3 PRIV packets typically have SNR but not RSSI."""
    payload = json.dumps({
        "type": "EventType.CONTACT_MSG_RECV",
        "payload": {"text": "x", "SNR": 4.25},
    }).encode("utf-8")
    rssi, snr = _extract_radio_metadata(payload)
    assert rssi is None
    assert snr == 4.25


def test_flat_payload_extracts_lowercase_fields():
    payload = json.dumps({
        "text": "x", "rssi": -100, "snr": 2.0,
    }).encode("utf-8")
    rssi, snr = _extract_radio_metadata(payload)
    assert rssi == -100
    assert snr == 2.0


def test_garbage_returns_none_none():
    rssi, snr = _extract_radio_metadata(b"not json at all")
    assert rssi is None
    assert snr is None


def test_missing_fields_returns_none_none():
    payload = json.dumps({"payload": {"text": "x"}}).encode("utf-8")
    rssi, snr = _extract_radio_metadata(payload)
    assert rssi is None
    assert snr is None


def test_string_values_are_ignored_not_crashed():
    """Defensive: weird payloads with stringy 'RSSI' shouldn't blow up."""
    payload = json.dumps({"payload": {"RSSI": "low", "SNR": "good"}}).encode("utf-8")
    rssi, snr = _extract_radio_metadata(payload)
    assert rssi is None
    assert snr is None


def test_int_snr_is_coerced_to_float():
    payload = json.dumps({"payload": {"SNR": 7}}).encode("utf-8")
    rssi, snr = _extract_radio_metadata(payload)
    assert isinstance(snr, float)
    assert snr == 7.0
