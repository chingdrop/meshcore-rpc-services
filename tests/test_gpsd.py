"""Tests for gpsd._parse_tpv. The TCP client is exercised separately in
integration; the parser is pure and worth unit-testing thoroughly because
GPSD's reported field shapes vary across versions and devices.
"""
from __future__ import annotations

from meshcore_rpc_services.gpsd import _parse_tpv


def test_parse_tpv_3d_fix():
    msg = {
        "class": "TPV",
        "mode": 3,
        "lat": 27.94, "lon": -82.29,
        "alt": 14.5,
        "epx": 3.2, "epy": 4.1,
        "speed": 1.4, "track": 280.0,
        "time": "2026-04-26T12:00:00.000Z",
    }
    fix = _parse_tpv(msg)
    assert fix is not None
    assert fix.lat == 27.94
    assert fix.lon == -82.29
    assert fix.alt == 14.5
    assert fix.acc == 4.1  # max(epx, epy)
    assert fix.spd == 1.4
    assert fix.hdg == 280.0
    assert fix.fix == 3


def test_parse_tpv_2d_fix_no_alt():
    msg = {"class": "TPV", "mode": 2, "lat": 1.0, "lon": 2.0}
    fix = _parse_tpv(msg)
    assert fix is not None
    assert fix.fix == 2
    assert fix.alt is None


def test_parse_tpv_no_fix_returns_none():
    msg = {"class": "TPV", "mode": 1}  # NO_FIX
    assert _parse_tpv(msg) is None


def test_parse_tpv_zero_mode_returns_none():
    msg = {"class": "TPV", "mode": 0, "lat": 1.0, "lon": 2.0}
    assert _parse_tpv(msg) is None


def test_parse_tpv_missing_lat_returns_none():
    msg = {"class": "TPV", "mode": 3, "lon": 2.0}
    assert _parse_tpv(msg) is None


def test_parse_tpv_string_lat_returns_none():
    msg = {"class": "TPV", "mode": 3, "lat": "twenty seven", "lon": -82.29}
    assert _parse_tpv(msg) is None


def test_parse_tpv_only_one_eph_axis():
    """Some GPS chips report only epx OR epy; we should still pick a value."""
    msg = {"class": "TPV", "mode": 3, "lat": 1.0, "lon": 2.0, "epx": 7.0}
    fix = _parse_tpv(msg)
    assert fix is not None
    assert fix.acc == 7.0


def test_parse_tpv_no_eph_axes():
    """No accuracy reported; that's allowed."""
    msg = {"class": "TPV", "mode": 3, "lat": 1.0, "lon": 2.0}
    fix = _parse_tpv(msg)
    assert fix is not None
    assert fix.acc is None
