import pytest

from sigmond.storage.model import (
    Demand, Levels, Store, allot_days, allottable_bytes, apply_pool_limit,
    headroom_bytes, pool_room_bytes,
)

GiB = 1 << 30


def test_headroom_is_space_above_final_with_a_floor():
    assert headroom_bytes(Store("root", "/", "guest", 100 * GiB, 0), Levels()) == 5 * GiB
    assert headroom_bytes(Store("root", "/", "guest", 20 * GiB, 0), Levels()) == 2 * GiB
    assert headroom_bytes(Store("pool", "pve/data", "pool", 100 * GiB, 0), Levels()) == 10 * GiB


def test_unregistered_consumers_shrink_the_allottable_space():
    s = Store("root", "/", "guest", 100 * GiB, 60 * GiB)
    # 60 used, 40 of it registered -> 20 unregistered; 100 - 1 - 5 - 20 = 74
    assert allottable_bytes(s, Levels(), registered_bytes=40 * GiB, state_reserve=GiB) == 74 * GiB
    assert allottable_bytes(Store("r", "/", "guest", 10 * GiB, 10 * GiB), Levels(), 0, GiB) == 0


def test_pool_room_is_this_vms_share_of_space_below_final():
    pool = Store("pool", "pve/data", "pool", 200 * GiB, 150 * GiB)
    assert pool_room_bytes(pool, Levels(), share=0.5) == 20 * GiB   # (190 - 150) * 0.5
    assert pool_room_bytes(Store("pool", "p", "pool", 100 * GiB, 99 * GiB), Levels(), 1.0) == 0


def test_a_filling_pool_caps_growth_not_holdings():
    assert apply_pool_limit(allottable=74 * GiB, registered_bytes=40 * GiB, pool_room=10 * GiB) == 50 * GiB
    assert apply_pool_limit(74 * GiB, 40 * GiB, 100 * GiB) == 74 * GiB


# --- days of coverage (Task 13, Michael 2026-11-04) ---------------------------

def test_equal_days_not_equal_bytes():
    # I/Q writes 80 GiB/day, the magnetometer 0.01 GiB/day, 100 GiB to share, no floors
    a = allot_days([Demand("hf-timestd", 0, 80 * GiB), Demand("mag", 0, 0.01 * GiB)], 100 * GiB)
    assert a.days == pytest.approx(100 / 80.01, rel=1e-6)
    assert a.per_client["mag"] < 0.02 * GiB and sum(a.per_client.values()) == 100 * GiB


def test_retention_cap_gives_days_to_others():
    a = allot_days([Demand("iq", 0, 10 * GiB), Demand("spots", 0, 10 * GiB, max_days=1.0)], 100 * GiB)
    assert a.per_client["spots"] == 10 * GiB and a.per_client["iq"] == 90 * GiB


def test_floors_first_then_days():
    a = allot_days([Demand("a", 5 * GiB, 10 * GiB), Demand("b", 0, 10 * GiB)], 25 * GiB)
    assert a.days == pytest.approx(1.0) and a.per_client["a"] == 15 * GiB


def test_operator_weight_doubles_days():
    a = allot_days([Demand("a", 0, 10 * GiB, weight=2.0), Demand("b", 0, 10 * GiB)], 30 * GiB)
    assert a.per_client["a"] == 20 * GiB


def test_a_client_with_no_rate_gets_its_floor_only():
    a = allot_days([Demand("a", 3 * GiB, 0.0), Demand("b", 0, 10 * GiB)], 30 * GiB)
    assert a.per_client == {"a": 3 * GiB, "b": 27 * GiB} and a.days == pytest.approx(2.7)


def test_no_rates_means_no_days_and_floors_only():
    a = allot_days([Demand("a", 3 * GiB, 0.0), Demand("b", 0, 0.0)], 30 * GiB)
    assert a.days is None and a.per_client == {"a": 3 * GiB, "b": 0} and a.floors_fit


def test_every_client_capped_leaves_the_rest_unallotted():
    a = allot_days([Demand("a", 0, GiB, max_days=2.0), Demand("b", 0, GiB, max_days=3.0)], 100 * GiB)
    assert a.per_client == {"a": 2 * GiB, "b": 3 * GiB}
    assert a.days == pytest.approx(3.0) and a.allottable == 100 * GiB


def test_caps_fill_in_order():
    # c caps at 1 day, b at 2; a takes the rest: 1 + 2 + D = 10 -> D = 7
    a = allot_days([Demand("a", 0, GiB), Demand("b", 0, GiB, max_days=2.0),
                    Demand("c", 0, GiB, max_days=1.0)], 10 * GiB)
    assert a.per_client == {"a": 7 * GiB, "b": 2 * GiB, "c": GiB}
    assert a.days == pytest.approx(7.0)


def test_floors_that_no_longer_fit_say_so():
    a = allot_days([Demand("a", 30 * GiB, GiB), Demand("b", 30 * GiB, GiB)], 50 * GiB)
    assert not a.floors_fit and a.per_client == {"a": 30 * GiB, "b": 30 * GiB}


def test_one_client_with_several_demands_gets_one_share():
    a = allot_days([Demand("a", GiB, GiB), Demand("a", GiB, GiB), Demand("b", 0, 2 * GiB)], 22 * GiB)
    # a: floor 2 GiB, rate 2 GiB/day; b: rate 2 GiB/day; 20 GiB above floors -> D = 5
    assert a.per_client == {"a": 12 * GiB, "b": 10 * GiB}


def test_rounding_remainder_goes_to_first_sorted_client():
    a = allot_days([Demand(n, 0, 1.0) for n in ("c", "a", "b")], 10)
    assert a.per_client == {"a": 4, "b": 3, "c": 3}
    assert sum(a.per_client.values()) == 10


def test_days_equal_the_largest_cap_when_every_client_caps_with_odd_weights():
    a = allot_days([Demand("a", 0, 3 * GiB, weight=0.7, max_days=1.5),
                    Demand("b", 0, 5 * GiB, weight=1.5, max_days=2.5),
                    Demand("c", 0, 0.3 * GiB, weight=0.7, max_days=0.9)], 1000 * GiB)
    assert a.days == pytest.approx(2.5)


def test_days_never_exceed_the_largest_cap_and_allotments_fit_property():
    import random
    rnd = random.Random(20261104)
    for _ in range(5000):
        n = rnd.randint(1, 6)
        dem = [Demand(f"c{i}", rnd.randint(0, 3) * GiB, rnd.uniform(1e3, 50 * GiB),
                      weight=rnd.choice([0.3, 0.7, 1.0, 1.5, 2.0, 3.3]),
                      max_days=rnd.choice([None, rnd.uniform(0.1, 30.0)]))
               for i in range(n)]
        room = rnd.randint(0, 2000) * GiB
        a = allot_days(dem, room)
        assert sum(a.per_client.values()) <= room or not a.floors_fit
        caps = [d.max_days for d in dem]
        if a.floors_fit and all(c is not None for c in caps):
            assert a.days <= max(caps) * (1 + 1e-9)
