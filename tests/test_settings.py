from tbssa.settings import parse_admin_ids


def test_parse_admin_ids_empty():
    assert parse_admin_ids("") == set()


def test_parse_admin_ids_trims_and_filters():
    assert parse_admin_ids(" 1, 2, x, 3 ") == {1, 2, 3}

