#!/usr/bin/env python3
"""Route verification copilot — cross-checks routes/act<N>.json against
cached poewiki pages with an LLM, on top of deterministic schema checks.

Usage:
    python tools/verify_routes_llm.py <act-number|all>
        [--route-dir routes] [--cache-dir data/wiki_cache] [--out-dir .]
        [--no-llm] [--refresh]

For each act the tool:
  1. fetches + caches the act's poewiki pages (act overview page, the
     Trial of Ascendancy page for trial acts, and the act's quest pages)
     into data/wiki_cache/<slug>.html — a cache hit skips the network;
  2. strips the HTML to text (stdlib html.parser) and prompts the LLM
     (standard tier, json_schema) with the act JSON + page texts;
  3. prints the findings as a table and writes verify_act<N>.json.

ADVISORY ONLY: this tool never edits route files. A human reviews the
findings and fixes routes/act<N>.json by hand (feed confirmed findings
into REVIEW.md per the plan addendum, section 5A).

Degrade (INTERFACES.md invariant 4): when the LLM is unavailable
(LLMDisabled — kill switch, missing SDK, or missing key) only the
deterministic checks run (JSON validity, schema fields, kind enum,
duplicate consecutive zones), the wiki fetch is skipped too, and the
output says the LLM layer was skipped.

Network rules (INTERFACES.md invariant 3): User-Agent
"poe-league-tools/1.0 (contact: cyrus@hadavi.net)", one request at a
time, hard floor of 1 request / 2 s, honors 429/Retry-After, and retries
transient 5xx (poewiki was observed to emit a one-off 500 live).

Stdlib only; import-safe (no side effects at import time).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
WIKI_BASE = "https://www.poewiki.net/wiki/"
MIN_INTERVAL_S = 2.0        # hard floor: 1 request / 2 s (invariant 3)
FETCH_ATTEMPTS = 3
PAGE_CHAR_CAP = 9000        # per-page text budget in the prompt
FEATURE = "route_verify"    # usage-meter tag
KIND_ENUM = ("travel", "kill", "town", "trial")

DEFAULT_ROUTE_DIR = os.path.join(ROOT, "routes")
DEFAULT_CACHE_DIR = os.path.join(ROOT, "data", "wiki_cache")

# Pages fetched per act. Act 1 titles were live-verified 2026-07-07: every
# one is linked from https://www.poewiki.net/wiki/Act_1 and returns 200
# at WIKI_BASE + slug (page pattern confirmed, incl. "The_Siren%27s_Cadence").
# VERIFY: acts 2-10 quest titles are from game knowledge and not yet checked
# against the wiki — a wrong title just fails to fetch and is skipped with a
# warning, so the cost of an error here is one missing context page.
ACT_QUEST_PAGES = {
    1: ["Enemy at the Gate", "Mercy Mission", "Breaking Some Eggs",
        "The Caged Brute", "The Marooned Mariner", "The Dweller of the Deep",
        "A Dirty Job", "The Way Forward", "The Siren's Cadence"],
    2: ["The Great White Beast", "Intruders in Black", "Sharp and Cruel",
        "The Root of the Problem", "Deal with the Bandits",
        "Through Sacred Ground", "Shadow of the Vaal"],
    3: ["Lost in Love", "Victario's Secrets", "The Ribbon Spool",
        "A Fixture of Fate", "Sever the Right Hand", "Piety's Pets",
        "The Gemling Queen", "A Swig of Hope", "The Sceptre of God"],
    4: ["Breaking the Seal", "An Indomitable Spirit", "The King of Fury",
        "The King of Desire", "Corpus Malachus", "The Eternal Nightmare"],
    5: ["Return to Oriath", "The Key to Freedom", "In Service to Science",
        "Kitava's Torments", "Death to Purity"],
    6: ["Fallen from Grace", "Bestel's Epic", "The Father of War",
        "Essence of Umbra", "The Cloven One", "The Puppet Mistress",
        "The Brine King"],
    7: ["The Silver Locket", "Essence of the Artist", "Kishara's Star",
        "In Memory of Greust", "The Master of a Million Faces",
        "Queen of Despair", "Lighting the Way", "Web of Secrets"],
    8: ["Essence of the Hag", "Love is Dead", "The Gemling Legion",
        "Reflection of Terror", "The Wings of Vastiri"],
    9: ["The Storm Blade", "Fastis Fortuna", "Queen of the Sands",
        "The Ruler of Highgate"],
    10: ["Safe Passage", "Death and Rebirth", "No Love for Old Ghosts",
         "Vilenta's Vengeance", "An End to Hunger"],
}
TRIAL_PAGE = "Trial of Ascendancy"   # lists every labyrinth trial location
TRIAL_ACTS = {1, 2, 3, 6, 7}


def pages_for_act(act):
    """Wiki page titles to feed the LLM for one act."""
    titles = [f"Act {act}"]
    if act in TRIAL_ACTS:
        titles.append(TRIAL_PAGE)
    titles.extend(ACT_QUEST_PAGES.get(act, ()))
    return titles


# ------------------------------------------------------------ wiki fetching

def slugify(title):
    """Filesystem-safe cache name: 'The Siren's Cadence' -> The_Siren_s_Cadence."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", title.replace(" ", "_"))


def page_url(title):
    return WIKI_BASE + urllib.parse.quote(title.replace(" ", "_"))


_last_request = 0.0


def _retry_after_s(headers):
    """Parse Retry-After (seconds or HTTP-date), uncapped — invariant 3
    says the server-requested wait is honored in full. Mirrors
    market/sources.py::_retry_after_seconds."""
    value = headers.get("Retry-After") if headers else None
    if value is None or str(value).strip() == "":
        return MIN_INTERVAL_S
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(value))
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return MIN_INTERVAL_S


def _http_get(url, _sleep=time.sleep):
    """GET with the mandatory UA, >= MIN_INTERVAL_S between request starts,
    honoring 429/Retry-After and retrying transient 5xx."""
    global _last_request
    last_exc = None
    for attempt in range(FETCH_ATTEMPTS):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
        if wait > 0:
            _sleep(wait)
        _last_request = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            retriable = exc.code == 429 or 500 <= exc.code < 600
            if retriable and attempt + 1 < FETCH_ATTEMPTS:
                pause = _retry_after_s(exc.headers) if exc.code == 429 else 0.0
                _sleep(max(pause, MIN_INTERVAL_S))
                continue
            raise
    raise last_exc  # unreachable in practice; keeps the control flow obvious


def get_page(title, cache_dir, fetch=None, refresh=False):
    """Cached fetch of one wiki page -> (html, url, from_cache).

    Cache file: <cache_dir>/<slug>.html. A cache hit never touches the
    network. ``fetch`` is injectable for tests (defaults to the
    rate-limited HTTP client).
    """
    url = page_url(title)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, slugify(title) + ".html")
    if not refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read(), url, True
    html_text = (fetch or _http_get)(url)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return html_text, url, False


# ------------------------------------------------------------ HTML -> text

class _TextExtractor(HTMLParser):
    """Collects visible text; drops script/style; block tags break lines."""

    SKIP = {"script", "style", "noscript", "template"}
    BREAK = {"p", "div", "li", "ul", "ol", "table", "tr", "br", "hr",
             "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "blockquote", "pre", "dd", "dt"}
    SPACE = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAK:
            self.chunks.append("\n")
        elif tag in self.SPACE:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BREAK:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.chunks.append(data)


def strip_html(html_text):
    """Visible text of an HTML page: entities decoded, script/style gone,
    whitespace collapsed, blank lines removed."""
    parser = _TextExtractor()
    parser.feed(html_text)
    parser.close()
    text = "".join(parser.chunks).replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


# ----------------------------------------------------- deterministic checks

def step_id(step, i):
    """Step id used everywhere: '<index>:<zone>'."""
    zone = step.get("zone") if isinstance(step, dict) else None
    return f"{i}:{zone if isinstance(zone, str) and zone.strip() else '?'}"


def _finding(sid, severity, issue, evidence="", source_url="",
             layer="deterministic"):
    return {"step_id": sid, "severity": severity, "issue": issue,
            "evidence": evidence, "source_url": source_url, "layer": layer}


def deterministic_findings(raw, act_num):
    """Schema-level checks that need no LLM and no network: JSON validity,
    required fields + types, kind enum, duplicate consecutive zones."""
    out = []
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return [_finding(f"act{act_num}", "error",
                         f"route file is not valid JSON: {exc}")]
    if not isinstance(data, dict):
        return [_finding(f"act{act_num}", "error",
                         "top level must be a JSON object")]
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return [_finding(f"act{act_num}", "error",
                         "'steps' must be a non-empty list")]
    if data.get("act") != act_num:
        out.append(_finding(f"act{act_num}", "warn",
                            f"top-level 'act' is {data.get('act')!r}, "
                            f"expected {act_num}"))
    prev_zone = None
    for i, step in enumerate(steps):
        sid = step_id(step, i)
        if not isinstance(step, dict):
            out.append(_finding(sid, "error", "step must be a JSON object"))
            prev_zone = None
            continue
        zone = step.get("zone")
        if not isinstance(zone, str) or not zone.strip():
            out.append(_finding(sid, "error",
                                "missing or empty 'zone' (must match the "
                                "Client.txt 'You have entered X.' name)"))
            zone = None
        kind = step.get("kind")
        if kind not in KIND_ENUM:
            out.append(_finding(sid, "error",
                                f"'kind' must be one of {'|'.join(KIND_ENUM)},"
                                f" got {kind!r}"))
        do = step.get("do")
        if (not isinstance(do, list) or not do
                or not all(isinstance(d, str) and d.strip() for d in do)):
            out.append(_finding(sid, "error",
                                "'do' must be a non-empty list of non-empty "
                                "strings"))
        for key in ("layout", "tip"):
            if key in step and not isinstance(step[key], str):
                out.append(_finding(sid, "error", f"'{key}' must be a string"))
        if "arealvl" in step and (isinstance(step["arealvl"], bool)
                                  or not isinstance(step["arealvl"], int)):
            out.append(_finding(sid, "error", "'arealvl' must be an integer"))
        if zone and prev_zone and zone.lower() == prev_zone.lower():
            out.append(_finding(sid, "error",
                                f"duplicate consecutive zone {zone!r} — "
                                "auto-advance ignores re-entering the current "
                                "zone, so this step is unreachable"))
        prev_zone = zone
    return out


# ------------------------------------------------------------ LLM layer

SYSTEM_PROMPT = """\
You are a Path of Exile campaign route fact-checker. You are given one act's
route JSON and the text of poewiki pages, each preceded by a 'SOURCE: <url>'
line. Cross-check the route against the wiki and report discrepancies.

Report only what the wiki text supports: wrong NPC or reward attribution,
rewards the wiki contradicts (e.g. a claimed passive skill point that does
not exist), missing mandatory objectives (Trial of Ascendancy, skill-point
quests, required boss kills), and wrong zone connections or ordering.

Rules:
- severity "error" = the wiki contradicts the route, or a mandatory
  objective is missing; "warn" = plausible problem you cannot fully confirm
  from the given text.
- evidence: a SHORT verbatim quote (under 25 words) from one wiki page;
  source_url: that page's SOURCE url.
- step_id: copy an id from the STEP IDS list ("<index>:<zone>"); for
  something missing from the route entirely use "missing:<name>".
- Findings are advisory only — a human edits the route file by hand.
  Never propose JSON edits.
- A fully consistent route yields {"findings": []}. Do not pad with trivia.
"""

FINDINGS_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step_id", "severity", "issue", "evidence",
                             "source_url"],
                "properties": {
                    "step_id": {"type": "string"},
                    "severity": {"type": "string",
                                 "enum": ["error", "warn"]},
                    "issue": {"type": "string"},
                    "evidence": {"type": "string"},
                    "source_url": {"type": "string"},
                },
            },
        },
    },
}


def build_prompt(act, raw, pages):
    """User prompt: the act JSON verbatim (so the model sees the exact
    route lines), the step-id anchors, then each page as SOURCE + text."""
    parts = [f"ACT {act} ROUTE JSON (verbatim from routes/act{act}.json):",
             raw.strip(), ""]
    try:
        steps = json.loads(raw).get("steps", [])
        ids = [step_id(s, i) for i, s in enumerate(steps)]
    except (ValueError, AttributeError):
        ids = []
    if ids:
        parts += ["STEP IDS (use these exact step_id values):"] + ids + [""]
    parts.append("WIKI PAGES:")
    if not pages:
        parts.append("(no wiki pages available — flag only issues visible "
                     "in the route JSON itself, as warns)")
    for p in pages:
        parts += ["", f"SOURCE: {p['url']}", p["text"][:PAGE_CHAR_CAP]]
    return "\n".join(parts)


def _sanitize_llm_findings(items):
    """Normalize model output defensively: coerce fields to str, clamp the
    severity enum, clip evidence, tag the layer."""
    out = []
    for f in items if isinstance(items, list) else []:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "warn")).lower()
        out.append(_finding(
            str(f.get("step_id", "?")),
            sev if sev in ("error", "warn") else "warn",
            str(f.get("issue", "")).strip(),
            evidence=str(f.get("evidence", "")).strip()[:300],
            source_url=str(f.get("source_url", "")).strip(),
            layer="llm"))
    return out


def _make_llm():
    """-> (llm, client_module, why_not). llm is None when unavailable; the
    caller then also skips wiki fetching (no point downloading pages)."""
    if ROOT not in sys.path:  # so `llm.client` resolves when run from tools/
        sys.path.insert(0, ROOT)
    try:
        import llm.client as lc
    except ImportError as exc:
        return None, None, f"llm.client unavailable: {exc}"
    try:
        return lc.LLM("standard"), lc, ""
    except lc.LLMDisabled as exc:
        return None, lc, f"disabled: {exc}"


def _collect_pages(act, cache_dir, fetch=None, refresh=False):
    """Fetch-or-load every wiki page for the act; failures are non-fatal
    (the page is skipped with a stderr warning)."""
    pages = []
    for title in pages_for_act(act):
        try:
            html_text, url, _ = get_page(title, cache_dir, fetch=fetch,
                                         refresh=refresh)
        except Exception as exc:  # noqa: BLE001 — fetch trouble skips the page
            print(f"  warn: wiki page {title!r} unavailable ({exc}) — skipped",
                  file=sys.stderr)
            continue
        pages.append({"title": title, "url": url,
                      "text": strip_html(html_text)[:PAGE_CHAR_CAP]})
    return pages


# ------------------------------------------------------------ verification

def verify_act(act, route_dir=DEFAULT_ROUTE_DIR, cache_dir=DEFAULT_CACHE_DIR,
               out_dir=".", fetch=None, use_llm=True, refresh=False):
    """Verify one act. Returns the report dict (also written to
    verify_act<N>.json in out_dir), or None when the route file is absent.

    Read-only with respect to the route file — findings are advisory.
    """
    route_path = os.path.join(route_dir, f"act{act}.json")
    if not os.path.exists(route_path):
        return None
    with open(route_path, encoding="utf-8") as f:
        raw = f.read()

    findings = deterministic_findings(raw, act)
    llm_ran, note = False, ""
    if not use_llm:
        note = "LLM layer skipped (--no-llm); deterministic checks only"
    else:
        llm_obj, lc, why = _make_llm()
        if llm_obj is None:
            note = f"LLM layer skipped ({why}); deterministic checks only"
        else:
            pages = _collect_pages(act, cache_dir, fetch=fetch,
                                   refresh=refresh)
            prompt = build_prompt(act, raw, pages)
            try:
                data = llm_obj.complete(system=SYSTEM_PROMPT, messages=prompt,
                                        max_tokens=2000, feature=FEATURE,
                                        json_schema=FINDINGS_SCHEMA)
                findings.extend(_sanitize_llm_findings(data.get("findings")))
                llm_ran = True
            except lc.LLMDisabled as exc:
                note = (f"LLM layer skipped (disabled: {exc}); "
                        "deterministic checks only")
            except lc.LLMError as exc:
                note = (f"LLM layer failed ({exc}); "
                        "deterministic findings only")

    report = {"act": act,
              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "route_file": route_path,
              "llm_ran": llm_ran,
              "note": note,
              "findings": findings}
    os.makedirs(out_dir or ".", exist_ok=True)
    out_path = os.path.join(out_dir or ".", f"verify_act{act}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    report["report_file"] = out_path
    return report


# ------------------------------------------------------------ presentation

def _clip(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def render_table(findings):
    """Readable findings table, errors first; evidence/source as sub-lines."""
    if not findings:
        return "  no findings"
    ordered = sorted(findings, key=lambda f: f.get("severity") != "error")
    w = max(len("STEP"), *(len(_clip(f.get("step_id", ""), 28))
                           for f in ordered))
    lines = [f"  {'STEP':<{w}}  {'SEV':<5}  {'LAYER':<13}  ISSUE",
             "  " + "-" * (w + 88)]
    for f in ordered:
        lines.append(f"  {_clip(f.get('step_id', ''), 28):<{w}}  "
                     f"{_clip(f.get('severity', ''), 5):<5}  "
                     f"{_clip(f.get('layer', ''), 13):<13}  "
                     f"{_clip(f.get('issue', ''), 76)}")
        if f.get("evidence"):
            lines.append(f"      evidence: \"{_clip(f['evidence'], 88)}\"")
        if f.get("source_url"):
            lines.append(f"      source:   {f['source_url']}")
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI

def main(argv=None, fetch=None):
    # Windows: redirected stdout defaults to the ANSI codepage, which cannot
    # encode arbitrary wiki evidence quotes; force UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="Advisory route verifier: deterministic schema checks "
                    "plus an LLM cross-check against cached poewiki pages. "
                    "Never edits route files.")
    ap.add_argument("act", help='act number 1-10, or "all"')
    ap.add_argument("--route-dir", default=DEFAULT_ROUTE_DIR)
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--out-dir", default=".",
                    help="where verify_act<N>.json is written")
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic checks only (no wiki, no LLM)")
    ap.add_argument("--refresh", action="store_true",
                    help="refetch wiki pages even when cached")
    args = ap.parse_args(argv)

    if args.act.lower() == "all":
        acts, required = list(range(1, 11)), False
    else:
        try:
            n = int(args.act)
        except ValueError:
            n = -1
        if not 1 <= n <= 10:
            print(f'invalid act {args.act!r}: expected 1-10 or "all"',
                  file=sys.stderr)
            return 2
        acts, required = [n], True

    ran = 0
    for act in acts:
        report = verify_act(act, route_dir=args.route_dir,
                            cache_dir=args.cache_dir, out_dir=args.out_dir,
                            fetch=fetch, use_llm=not args.no_llm,
                            refresh=args.refresh)
        if report is None:
            path = os.path.join(args.route_dir, f"act{act}.json")
            msg = f"act {act}: no route file at {path} — skipped"
            if required:
                print(msg, file=sys.stderr)
                return 2
            print(msg)
            continue
        ran += 1
        errors = sum(1 for f in report["findings"]
                     if f["severity"] == "error")
        warns = len(report["findings"]) - errors
        print(f"=== act {act} — {report['route_file']} ===")
        if report["note"]:
            print(f"  note: {report['note']}")
        print(render_table(report["findings"]))
        print(f"  {len(report['findings'])} finding(s): {errors} error(s), "
              f"{warns} warn(s) — advisory only, fix the route by hand")
        print(f"  wrote {report['report_file']}")
    if not ran and not required:
        print("no route files found", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
