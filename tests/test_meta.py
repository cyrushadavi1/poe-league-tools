"""Headless tests for tools/meta.py (poe.ninja meta ranker). Offline: runs
entirely against trimmed real fixtures captured 2026-07-07 under
tests/fixtures_market/ninja_builds*.json."""
import base64
import contextlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools")]

import meta                                    # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures_market")


def load_fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


IDX = load_fixture("ninja_builds_index_state.json")
CUR = base64.b64decode(load_fixture("ninja_builds_search_current.json")
                       ["protobuf_b64"])
WK1 = base64.b64decode(load_fixture("ninja_builds_search_week1.json")
                       ["protobuf_b64"])
DICTS = {h: base64.b64decode(b)
         for h, b in load_fixture("ninja_builds_dictionaries.json")
         ["by_hash"].items()}


def fake_get(url, timeout=30.0):
    """Offline stand-in for meta._get, serving the fixtures by URL."""
    if url == meta.INDEX_STATE_URL:
        return json.dumps(IDX).encode()
    if "/poe1/api/builds/" in url and "/search?" in url:
        assert "overview=ancestors" in url and "type=exp" in url
        return WK1 if "timemachine=week-1" in url else CUR
    if "/poe1/api/builds/dictionary/" in url:
        return DICTS[url.rsplit("/", 1)[1]]
    raise AssertionError(f"unexpected URL fetched: {url}")


# ------------------------------------------------------- protobuf parsing
sr = meta.parse_search(CUR)
assert sr["total"] == 41408
assert set(sr["dimensions"]) == {"class", "skills", "weaponmode"}
assert sr["dimensions"]["class"]["dictionary_id"] == "class"
assert sr["dimensions"]["skills"]["dictionary_id"] == "gem"
assert len(sr["dimensions"]["class"]["counts"]) == 26
assert sr["dictionary_hashes"]["class"] == \
    "5b7b99177de4de3c47658faa7bbb992e5d5ecbff"

cd = meta.parse_dictionary(DICTS[sr["dictionary_hashes"]["class"]])
assert cd["id"] == "class" and len(cd["values"]) == 26
assert "Daughter of Oshabi" in cd["values"]

# ------------------------------------------------- shares + exact ranking
class_counts = meta.shares(sr["dimensions"]["class"], cd["values"],
                           sr["total"])
ranked = meta.rank(class_counts, sr["total"], top=5)
assert [(r["name"], r["count"], r["pct"]) for r in ranked] == [
    ("Daughter of Oshabi", 7358, 17.77),
    ("Bog Shaman", 4237, 10.23),
    ("Wildspeaker", 4187, 10.11),
    ("Ancestral Commander", 4007, 9.68),
    ("Whisperer", 3799, 9.17),
]

# tie-break: equal counts -> alphabetical
tied = meta.rank({"Zeal": 5, "Arc": 5, "Mid": 9}, 100, top=3)
assert [r["name"] for r in tied] == ["Mid", "Arc", "Zeal"]
assert tied[0]["pct"] == 9.0

# delta: percentage-point change vs prev; missing name -> None ("new")
d = meta.rank({"A": 20, "B": 10}, 100, top=2,
              prev={"A": 10}, prev_total=100)
assert d[0]["delta_pp"] == 10.0 and d[1]["delta_pp"] is None

# ------------------------------------------------------------ full pipeline
m = meta.fetch_meta(get=fake_get, top=5)
assert m["league"] == "Ancestors" and m["slug"] == "ancestors"
assert m["version"] == "2002-20260707-55341"
assert m["total"] == 41408 and m["week1_total"] == 38095
assert [(r["name"], r["count"], r["pct"], r["delta_pp"])
        for r in m["ascendancies"]] == [
    ("Daughter of Oshabi", 7358, 17.77, -0.28),
    ("Bog Shaman", 4237, 10.23, 0.42),
    ("Wildspeaker", 4187, 10.11, 0.25),
    ("Ancestral Commander", 4007, 9.68, -0.12),
    ("Whisperer", 3799, 9.17, -0.56),
]
assert [(r["name"], r["count"], r["pct"], r["delta_pp"])
        for r in m["skills"]] == [
    ("Kinetic Fusillade", 3648, 8.81, -0.52),
    ("Elemental Hit", 2392, 5.78, -0.34),
    ("Righteous Fire", 2313, 5.59, 0.02),
    ("Void Sphere", 2063, 4.98, 0.68),
    ("Flicker Strike", 1933, 4.67, -0.14),
]

# --no-delta path must not fetch the week-1 snapshot
def no_timemachine_get(url, timeout=30.0):
    assert "timemachine" not in url, "week-1 fetched despite want_delta=False"
    return fake_get(url, timeout)


m2 = meta.fetch_meta(get=no_timemachine_get, top=3, want_delta=False)
assert m2["week1_total"] is None
assert "delta_pp" not in m2["ascendancies"][0]
assert len(m2["ascendancies"]) == 3 and len(m2["skills"]) == 3

report2 = meta.format_report(m2)
assert "week-over-week deltas: not available" in report2
assert "Δpp/wk" not in report2

# ---------------------------------------------------------- snapshot picker
assert meta.pick_snapshot(IDX, None)["url"] == "ancestors"
assert meta.pick_snapshot(IDX, "ANCESTORS")["url"] == "ancestors"
assert meta.pick_snapshot(IDX, "mirage")["name"] == "Mirage"
try:
    meta.pick_snapshot(IDX, "nosuchleague")
    assert False, "unknown league must raise"
except meta.MetaError as e:
    assert "available" in str(e) and "ancestors" in str(e)

# ------------------------------------------------------- malformed responses
for bad in [b"", b"\x00", b"\xff\xff\xff", CUR[:40],
            bytes(range(255, 191, -1))]:
    try:
        meta.parse_search(bad)
        assert False, f"parse_search must reject {bad[:8]!r}"
    except meta.MetaError:
        pass

try:
    meta.parse_dictionary(b"\x12\x03abc")     # values but no id
    assert False, "dictionary without id must raise"
except meta.MetaError:
    pass

# dictionary key out of range -> clean error
try:
    meta.shares({"dictionary_id": "class", "counts": [(999, 1)]},
                ["OnlyValue"], 10)
    assert False, "out-of-range key must raise"
except meta.MetaError as e:
    assert "out of range" in str(e)

# search response missing the skills dimension -> clean error
def enc_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_str(fno, s):
    b = s.encode()
    return enc_varint(fno << 3 | 2) + enc_varint(len(b)) + b


def enc_msg(fno, payload):
    return enc_varint(fno << 3 | 2) + enc_varint(len(payload)) + payload


classless = enc_msg(1, enc_varint(1 << 3) + enc_varint(100) +
                    enc_msg(2, enc_str(1, "class") + enc_str(2, "class")))
try:
    meta.fetch_meta(get=lambda url, timeout=30.0:
                    json.dumps(IDX).encode()
                    if url == meta.INDEX_STATE_URL else classless)
    assert False, "missing skills dimension must raise"
except meta.MetaError as e:
    assert "skills" in str(e)

# index-state that isn't JSON -> clean error
try:
    meta.fetch_meta(get=lambda url, timeout=30.0: b"<html>oops</html>")
    assert False, "non-JSON index-state must raise"
except meta.MetaError:
    pass

# ------------------------------------------------------------------- CLI
real_get = meta._get
meta._get = fake_get
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = meta.main(["--top", "5"])
    assert rc == 0
    text = out.getvalue()
    assert "poe.ninja build meta — Ancestors" in text
    assert "41,408 ladder characters" in text
    assert "Daughter of Oshabi" in text and "17.77%" in text
    assert "Kinetic Fusillade" in text and "8.81%" in text
    assert "Δpp/wk" in text and "-0.28" in text
    assert text.index("Daughter of Oshabi") < text.index("Bog Shaman")
    assert text.index("Kinetic Fusillade") < text.index("Elemental Hit")

    # unknown league via CLI -> clear message, exit 1
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = meta.main(["--league", "nosuchleague"])
    assert rc == 1 and "available" in err.getvalue()
finally:
    meta._get = real_get

# network failure -> clear message, exit 1
def down_get(url, timeout=30.0):
    raise meta.MetaError("network error fetching x: connection refused")


meta._get = down_get
try:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = meta.main([])
    assert rc == 1
    assert "could not fetch poe.ninja build data" in err.getvalue()
    assert "connection refused" in err.getvalue()
finally:
    meta._get = real_get

print("ALL TESTS PASSED")
print("  top ascendancy:", m["ascendancies"][0]["name"],
      f'{m["ascendancies"][0]["pct"]}%')
print("  top skill:     ", m["skills"][0]["name"],
      f'{m["skills"][0]["pct"]}%')
