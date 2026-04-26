"""Cursor-on-Target XML builder.

CoT is an XML format used by TAK products. The minimal useful event looks
like:

    <event version="2.0" uid="..." type="a-f-G-U-C"
           how="m-g" time="..." start="..." stale="...">
        <point lat="..." lon="..." hae="..." ce="..." le="..."/>
        <detail>
            <contact callsign="..."/>
            <track speed="..." course="..."/>
            <remarks>...</remarks>
        </detail>
    </event>

Field meanings used here:

  uid    — globally unique per tracked entity. We use `meshcore.<id>`
           to namespace ours and avoid collisions with other CoT sources
           on the same TAK Server.
  type   — CoT taxonomy. `a-f-G-U-C` is friendly/ground/unit/combat.
           See https://wiki.tak.gov/info/cot.
  how    — `m-g` means "machine, GPS-derived"; `h-e` means "human entered".
           For our reports, `m-g` is correct: a node sent us its own GPS.
  time   — when the event was generated (ISO-8601 Z).
  start  — when the entity reached this state (same as `time` in our case).
  stale  — when the receiver should consider the data unreliable.
  hae    — height above ellipsoid in meters.
  ce, le — circular & linear error in meters. Default 9999999.0 = unknown.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from xml.etree import ElementTree as ET


# CoT requires Z-suffixed ISO-8601 with millisecond precision.
def _isoz(dt: datetime) -> str:
    s = dt.strftime("%Y-%m-%dT%H:%M:%S.")
    return s + f"{dt.microsecond // 1000:03d}Z"


def build_cot(
        *,
        uid: str,
        cot_type: str,
        lat: float,
        lon: float,
        time_dt: datetime,
        stale_after_s: float,
        callsign: Optional[str] = None,
        alt_m: Optional[float] = None,
        speed_mps: Optional[float] = None,
        course_deg: Optional[float] = None,
        accuracy_m: Optional[float] = None,
        remarks: Optional[str] = None,
        how: str = "m-g",
) -> bytes:
    """Build a CoT event as a UTF-8 XML byte string with a trailing newline.

    TAK servers/clients typically expect newline-delimited CoT on a TCP
    socket; we add the newline for caller convenience.
    """
    # Use UTC for everything. CoT explicitly requires Z-time.
    if time_dt.tzinfo is None:
        time_dt = time_dt.replace(tzinfo=timezone.utc)
    else:
        time_dt = time_dt.astimezone(timezone.utc)

    stale_dt = time_dt + timedelta(seconds=stale_after_s)

    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": uid,
            "type": cot_type,
            "how": how,
            "time": _isoz(time_dt),
            "start": _isoz(time_dt),
            "stale": _isoz(stale_dt),
        },
    )

    point_attrs = {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "hae": f"{alt_m:.1f}" if alt_m is not None else "9999999.0",
        "ce": f"{accuracy_m:.1f}" if accuracy_m is not None else "9999999.0",
        # Linear (vertical) error: not provided by GPSD's epx/epy. Leave unknown.
        "le": "9999999.0",
    }
    ET.SubElement(event, "point", point_attrs)

    detail = ET.SubElement(event, "detail")
    if callsign:
        ET.SubElement(detail, "contact", {"callsign": callsign})
    if speed_mps is not None or course_deg is not None:
        track_attrs = {}
        if speed_mps is not None:
            track_attrs["speed"] = f"{speed_mps:.2f}"
        if course_deg is not None:
            track_attrs["course"] = f"{course_deg:.1f}"
        ET.SubElement(detail, "track", track_attrs)
    if remarks:
        rem = ET.SubElement(detail, "remarks")
        rem.text = remarks

    xml = ET.tostring(event, encoding="utf-8", xml_declaration=False)
    return xml + b"\n"
