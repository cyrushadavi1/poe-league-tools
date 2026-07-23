"""Headless tests: build links -> raw PoB codes, and the party wizard.

No network anywhere: sources.resolve/party.wizard take an injected
`fetch`, per the dev-on-Mac rule (simulate every integration).
"""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "buildgen")]

import pob                                # noqa: E402
import party                              # noqa: E402
import sources                            # noqa: E402

# ------------------------------------------------- raw_url link mapping
CASES = {
    # pobb.in, incl. trailing slash / query junk / already-raw form
    "https://pobb.in/AbC-123": "https://pobb.in/AbC-123/raw",
    "https://pobb.in/AbC-123/": "https://pobb.in/AbC-123/raw",
    "http://www.pobb.in/AbC-123?utm=x#y": "https://pobb.in/AbC-123/raw",
    "https://pobb.in/pob/AbC-123": "https://pobb.in/AbC-123/raw",
    "https://pobb.in/AbC-123/raw": "https://pobb.in/AbC-123/raw",
    # pastebin + pastebinp
    "https://pastebin.com/dQw4w9Wg": "https://pastebin.com/raw/dQw4w9Wg",
    "https://pastebin.com/raw/dQw4w9Wg": "https://pastebin.com/raw/dQw4w9Wg",
    "https://pastebinp.com/abc123": "https://pastebinp.com/raw/abc123",
    # poe.ninja old and new paths
    "https://poe.ninja/pob/4abc": "https://poe.ninja/poe1/pob/raw/4abc",
    "https://poe.ninja/poe1/pob/4abc": "https://poe.ninja/poe1/pob/raw/4abc",
    "https://poe.ninja/poe1/pob/raw/4abc": "https://poe.ninja/poe1/pob/raw/4abc",
    # maxroll / rentry / poedb
    "https://maxroll.gg/poe/pob/abc-def": "https://maxroll.gg/poe/api/pob/abc-def",
    "https://rentry.co/mypaste": "https://rentry.co/paste/mypaste/raw",
    "https://rentry.co/mypaste/raw": "https://rentry.co/paste/mypaste/raw",
    "https://poedb.tw/pob/xyz9": "https://poedb.tw/pob/xyz9/raw",
    # youtube redirect wrapper (what you copy from a video description)
    "https://www.youtube.com/redirect?event=video_description&q="
    "https%3A%2F%2Fpobb.in%2FAbC-123": "https://pobb.in/AbC-123/raw",
    # unknown hosts -> None
    "https://example.com/AbC-123": None,
    "https://pobb.in.evil.com/AbC-123": None,
}
for url, want in CASES.items():
    got = sources.raw_url(url)
    assert got == want, f"raw_url({url!r}) = {got!r}, want {want!r}"

# unknown-host URL raises with the supported-site list; codes pass through
try:
    sources.resolve("https://example.com/notapob")
    raise AssertionError("unknown host should raise SourceError")
except sources.SourceError as e:
    assert "pobb.in" in str(e)
assert sources.resolve("  someRawCode==  ") == "someRawCode=="

# ------------------------------------------------- resolve + read_code
root = ET.Element("PathOfBuilding")
ET.SubElement(root, "Build", className="Witch", ascendClassName="Necromancer",
              level="90")
CODE = pob.encode(root)

fetched = []


def fake_fetch(url):
    fetched.append(url)
    return CODE + "\n"


got = sources.resolve("https://pobb.in/AbC-123", fetch=fake_fetch)
assert got == CODE
assert fetched == ["https://pobb.in/AbC-123/raw"]

tmp = tempfile.mkdtemp(prefix="poe_sources_test_")
try:
    # a file containing a LINK resolves too (friends drop links in .txt)
    link_file = os.path.join(tmp, "friend.txt")
    with open(link_file, "w", encoding="utf-8") as f:
        f.write("https://pastebin.com/dQw4w9Wg\n")
    assert pob.read_code(link_file, fetch=fake_fetch) == CODE
    assert fetched[-1] == "https://pastebin.com/raw/dQw4w9Wg"
    # and a file containing a code still works unchanged
    code_file = os.path.join(tmp, "me.txt")
    with open(code_file, "w", encoding="utf-8") as f:
        f.write(CODE + "\n")
    assert pob.read_code(code_file) == CODE

    # ------------------------------------------------------ party wizard
    def scripted(answers):
        it = iter(answers)
        return lambda prompt: next(it)

    said = []
    manifest_path = os.path.join(tmp, "party.json")
    manifest = party.wizard(
        manifest_path,
        ask=scripted([
            "CyrusChar",                       # player 1
            "https://example.com/nope",        # bad host -> retried
            "https://pobb.in/AbC-123",         # good link
            "FriendChar",                      # player 2
            CODE,                              # raw code paste
            "",                                # blank name = done
            "friendchar",                      # "me" (case-insensitive)
        ]),
        say=said.append, fetch=fake_fetch)

    assert [m["player"] for m in manifest["members"]] == ["CyrusChar",
                                                          "FriendChar"]
    m1, m2 = manifest["members"]
    assert m1["pob"] == CODE and m1["source"] == "https://pobb.in/AbC-123"
    assert m2["pob"] == CODE and "source" not in m2
    assert not m1.get("me") and m2["me"] is True
    assert any("!!" in s for s in said), "bad link should be reported"
    assert any("Witch (Necromancer)" in s for s in said), \
        "decoded build should be confirmed to the user"
    with open(manifest_path, encoding="utf-8") as f:
        assert json.load(f) == manifest

    # wizard refuses to clobber without a yes
    try:
        party.wizard(manifest_path, ask=scripted(["n"]), say=said.append)
        raise AssertionError("overwrite without consent")
    except SystemExit:
        pass
finally:
    shutil.rmtree(tmp)

print("ok - test_pob_sources")
