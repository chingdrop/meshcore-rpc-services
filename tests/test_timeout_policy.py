from meshcore_rpc_services.timeouts import TimeoutPolicy, clamp_ttl


def test_defaults_when_ttl_missing():
    p = TimeoutPolicy(default_s=30, max_s=300)
    assert p.resolve(request_type="ping", requested_ttl=None) == 30


def test_per_type_default_overrides_global_when_ttl_missing():
    p = TimeoutPolicy(default_s=30, max_s=300, per_type_default_s={"slow.thing": 90})
    assert p.resolve(request_type="slow.thing", requested_ttl=None) == 90
    assert p.resolve(request_type="other", requested_ttl=None) == 30


def test_request_ttl_takes_precedence_over_per_type_default():
    p = TimeoutPolicy(default_s=30, max_s=300, per_type_default_s={"x": 90})
    assert p.resolve(request_type="x", requested_ttl=5) == 5


def test_clamps_to_min():
    p = TimeoutPolicy(default_s=30, min_s=5, max_s=300)
    assert p.resolve(request_type="x", requested_ttl=1) == 5


def test_clamps_to_max():
    p = TimeoutPolicy(default_s=30, min_s=1, max_s=60)
    assert p.resolve(request_type="x", requested_ttl=9999) == 60


def test_clamp_ttl_shim_still_works():
    assert clamp_ttl(None, 30, 300) == 30
    assert clamp_ttl(10, 30, 300) == 10
    assert clamp_ttl(99999, 30, 300) == 300
    assert clamp_ttl(0, 30, 300) == 1
