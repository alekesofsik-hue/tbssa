from tbssa.ssh import _normalize_md5_fingerprint


def test_normalize_md5_fingerprint_formats():
    assert _normalize_md5_fingerprint("MD5:aa:bb") == "aabb"
    assert _normalize_md5_fingerprint("aa:bb") == "aabb"
    assert _normalize_md5_fingerprint("AABB") == "aabb"

