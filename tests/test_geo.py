"""Tests for geo.py pure functions."""

import math

import pytest

from meshcore_rpc_services.geo import haversine_m, initial_bearing_deg


class TestHaversine:
    def test_known_distance_tampa_to_st_pete(self):
        # These coords compute ~26.7 km straight-line.
        d = haversine_m(27.95, -82.46, 27.77, -82.64)
        assert 24_000 < d < 30_000

    def test_zero_distance(self):
        assert haversine_m(27.94, -82.29, 27.94, -82.29) == pytest.approx(0.0)

    def test_symmetry(self):
        a = haversine_m(10.0, 20.0, 30.0, 40.0)
        b = haversine_m(30.0, 40.0, 10.0, 20.0)
        assert a == pytest.approx(b)

    def test_roughly_one_degree_latitude(self):
        # 1° latitude ≈ 111 km.
        d = haversine_m(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < d < 112_000

    def test_antipodal_points(self):
        # Opposite ends of the Earth ≈ half circumference ≈ 20,015 km.
        d = haversine_m(0.0, 0.0, 0.0, 180.0)
        assert 20_000_000 < d < 20_100_000


class TestInitialBearing:
    def test_due_north(self):
        b = initial_bearing_deg(0.0, 0.0, 1.0, 0.0)
        assert abs(b - 0.0) < 0.5

    def test_due_south(self):
        b = initial_bearing_deg(1.0, 0.0, 0.0, 0.0)
        assert abs(b - 180.0) < 0.5

    def test_due_east(self):
        b = initial_bearing_deg(0.0, 0.0, 0.0, 1.0)
        assert abs(b - 90.0) < 0.5

    def test_due_west(self):
        b = initial_bearing_deg(0.0, 1.0, 0.0, 0.0)
        assert abs(b - 270.0) < 0.5

    def test_result_in_range(self):
        for lat1, lon1, lat2, lon2 in [
            (10, 20, 30, 40), (-10, -20, 30, 40), (0, 0, -1, -1),
        ]:
            b = initial_bearing_deg(lat1, lon1, lat2, lon2)
            assert 0.0 <= b < 360.0

    def test_northeast_quadrant(self):
        b = initial_bearing_deg(0.0, 0.0, 1.0, 1.0)
        assert 0.0 < b < 90.0
