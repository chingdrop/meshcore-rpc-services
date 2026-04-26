"""Tests for the CoT XML builder."""
from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from meshcore_rpc_services.tak.cot import build_cot


def _parse(xml: bytes) -> ET.Element:
    # Strip the trailing newline our builder adds.
    return ET.fromstring(xml.rstrip(b"\n"))


def test_build_cot_minimal_fields_present():
    t = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    xml = build_cot(
        uid="meshcore.alice",
        cot_type="a-f-G-U-C",
        lat=27.94,
        lon=-82.29,
        time_dt=t,
        stale_after_s=300,
    )
    root = _parse(xml)
    assert root.tag == "event"
    assert root.attrib["uid"] == "meshcore.alice"
    assert root.attrib["type"] == "a-f-G-U-C"
    assert root.attrib["how"] == "m-g"
    assert root.attrib["time"] == "2026-04-26T12:00:00.000Z"
    assert root.attrib["start"] == "2026-04-26T12:00:00.000Z"
    assert root.attrib["stale"] == "2026-04-26T12:05:00.000Z"

    point = root.find("point")
    assert point is not None
    assert point.attrib["lat"] == "27.9400000"
    assert point.attrib["lon"] == "-82.2900000"


def test_build_cot_unknown_alt_uses_sentinel():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    xml = build_cot(
        uid="x", cot_type="a-f-G-U-C",
        lat=0, lon=0, time_dt=t, stale_after_s=60,
    )
    root = _parse(xml)
    point = root.find("point")
    assert point.attrib["hae"] == "9999999.0"
    assert point.attrib["ce"] == "9999999.0"
    assert point.attrib["le"] == "9999999.0"


def test_build_cot_with_track_and_remarks():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    xml = build_cot(
        uid="x", cot_type="a-f-G-U-C",
        lat=1, lon=2, time_dt=t, stale_after_s=60,
        callsign="MC-alice", speed_mps=2.4, course_deg=120.0,
        accuracy_m=5.5, alt_m=14.0,
        remarks="battery=78%; rssi=-92",
    )
    root = _parse(xml)
    contact = root.find("./detail/contact")
    assert contact is not None and contact.attrib["callsign"] == "MC-alice"

    track = root.find("./detail/track")
    assert track is not None
    assert track.attrib["speed"] == "2.40"
    assert track.attrib["course"] == "120.0"

    remarks = root.find("./detail/remarks")
    assert remarks is not None
    assert remarks.text == "battery=78%; rssi=-92"

    point = root.find("point")
    assert point.attrib["hae"] == "14.0"
    assert point.attrib["ce"] == "5.5"


def test_build_cot_naive_datetime_is_treated_as_utc():
    """Defensive: a naive datetime shouldn't crash, just assume UTC."""
    naive = datetime(2026, 1, 1, 12, 0, 0)
    xml = build_cot(
        uid="x", cot_type="a-f-G-U-C",
        lat=0, lon=0, time_dt=naive, stale_after_s=60,
    )
    root = _parse(xml)
    assert root.attrib["time"].endswith("Z")


def test_build_cot_terminator_newline_present():
    """TAK Servers parse newline-delimited CoT on TCP."""
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    xml = build_cot(
        uid="x", cot_type="a-f-G-U-C",
        lat=0, lon=0, time_dt=t, stale_after_s=60,
    )
    assert xml.endswith(b"\n")
    assert xml.count(b"\n") == 1  # exactly one terminator
