from sigmond.storage.model import (
    Levels, Registration, Store, allot, allottable_bytes, apply_pool_limit,
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


def test_even_split_above_floors():
    regs = [Registration("a", "root", ("/a",), 10 * GiB, None, True),
            Registration("b", "root", ("/b",), 0, None, False)]
    a = allot(regs, 50 * GiB, {})
    assert a.per_client == {"a": 30 * GiB, "b": 20 * GiB} and a.floors_fit


def test_operator_shares_weight_the_split():
    regs = [Registration("a", "root", (), 0, None, True), Registration("b", "root", (), 0, None, True)]
    assert allot(regs, 30 * GiB, {"a": 2.0}).per_client == {"a": 20 * GiB, "b": 10 * GiB}


def test_floors_that_no_longer_fit_say_so():
    regs = [Registration("a", "root", (), 30 * GiB, None, True), Registration("b", "root", (), 30 * GiB, None, True)]
    a = allot(regs, 50 * GiB, {})
    assert not a.floors_fit and a.per_client == {"a": 30 * GiB, "b": 30 * GiB}


def test_one_client_with_several_registrations_gets_one_share():
    regs = [Registration("a", "root", ("/x",), GiB, None, True), Registration("a", "root", ("/y",), GiB, None, True),
            Registration("b", "root", (), 0, None, True)]
    assert set(allot(regs, 20 * GiB, {}).per_client) == {"a", "b"}


def test_rounding_remainder_goes_to_first_sorted_client():
    regs = [Registration(n, "root", (), 0, None, True) for n in ("c", "a", "b")]
    a = allot(regs, 10, {})
    assert a.per_client == {"a": 4, "b": 3, "c": 3}
    assert sum(a.per_client.values()) == 10
