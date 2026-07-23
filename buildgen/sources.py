#!/usr/bin/env python3
"""Turn build-share links into raw PoB codes.

Mirrors the site list Path of Building itself imports from
(src/Modules/BuildSiteTools.lua): pobb.in, pastebin.com, pastebinp.com,
poe.ninja, maxroll.gg, rentry.co, poedb.tw. YouTube/Google redirect
wrappers (what you actually copy out of a video description) are
unwrapped first, so a guide link can be pasted as-is.

`resolve()` is the one entry point: URLs get downloaded and return the
code; anything else passes through untouched. Tests inject `fetch` so
nothing here needs the network (see tests/test_pob_sources.py).
"""
import re
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
TIMEOUT_S = 20
# pobb.in intermittently answers 500 "error code: 1101" (its Cloudflare
# Worker throwing) — observed live 2026-07-18, including 3 failures then
# a 200 on the 4th identical request. Retry 5xx/429 patiently and keep
# a polite gap between requests; 404s (dead paste) fail immediately.
MIN_INTERVAL_S = 2.0
MAX_ATTEMPTS = 5
_last_request = [0.0]
SUPPORTED = ("pobb.in", "pastebin.com", "poe.ninja", "maxroll.gg",
             "rentry.co", "poedb.tw", "pastebinp.com")


class SourceError(Exception):
    """A build link we could not turn into a PoB code."""


# (pattern on the normalized URL, raw-endpoint template). Endpoints are
# the ones PoB's own importer hits, so if a site changes its API, the
# fix is whatever PoB ships in BuildSiteTools.lua.
_SITES = (
    # PoB's importer uses pobb.in/pob/{id}; /{id}/raw serves the same
    # code (verified live 2026-07-18) and matches what browsers see
    (r"^https://pobb\.in/(?:pob/)?([\w-]+?)(?:/raw)?$",
     "https://pobb.in/{}/raw"),
    (r"^https://pastebin\.com/(?:raw/)?(\w+)$",
     "https://pastebin.com/raw/{}"),
    (r"^https://pastebinp\.com/(?:raw/)?(\w+)$",
     "https://pastebinp.com/raw/{}"),
    (r"^https://poe\.ninja/(?:poe1/)?pob/(?:raw/)?(\w+)$",
     "https://poe.ninja/poe1/pob/raw/{}"),
    (r"^https://maxroll\.gg/poe/(?:api/)?pob/([\w-]+)$",
     "https://maxroll.gg/poe/api/pob/{}"),
    (r"^https://rentry\.co/(?:paste/)?([\w-]+?)(?:/raw)?$",
     "https://rentry.co/paste/{}/raw"),
    (r"^https://poedb\.tw/pob/([\w-]+?)(?:/raw)?$",
     "https://poedb.tw/pob/{}/raw"),
)

_REDIRECT_HOSTS = ("youtube.com", "google.com")


def is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _unwrap(url: str) -> str:
    """Pull the target out of youtube.com/redirect?q=... (and google.com/url)
    wrapper links; anything else comes back unchanged."""
    for _ in range(3):  # wrappers can nest, but never legitimately deep
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc.lower().removeprefix("www.")
        if not any(host == h or host.endswith("." + h)
                   for h in _REDIRECT_HOSTS):
            return url
        qs = urllib.parse.parse_qs(parts.query)
        target = (qs.get("q") or qs.get("url") or [None])[0]
        if not target or not is_url(target):
            return url
        url = target
    return url


def raw_url(url: str) -> str | None:
    """Fetchable raw-code URL for a recognized build link, else None.

    Normalizes scheme/host case, http->https, leading www., trailing
    slash, and query/fragment junk before matching.
    """
    parts = urllib.parse.urlsplit(_unwrap(url.strip()))
    host = parts.netloc.lower().removeprefix("www.")
    normalized = "https://" + host + parts.path.rstrip("/")
    for pattern, template in _SITES:
        m = re.match(pattern, normalized)
        if m:
            return template.format(m.group(1))
    return None


def _get(url: str, timeout: float = TIMEOUT_S) -> str:
    last_code = None
    for attempt in range(MAX_ATTEMPTS):
        wait = _last_request[0] + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last_code = e.code
            if e.code == 429 or e.code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            raise SourceError(f"HTTP {e.code} fetching {url} — link dead "
                              "or paste expired") from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise SourceError(f"network error fetching {url}: {e}") from e
    raise SourceError(f"HTTP {last_code} from {url} after {MAX_ATTEMPTS} "
                      "tries — site overloaded or rate-limiting; wait a "
                      "minute and retry")


def resolve(text: str, fetch=None) -> str:
    """URL -> download and return the PoB code; non-URL -> unchanged.

    Raises SourceError for URLs on hosts we don't know (rather than
    letting a URL fall through to base64-decoding, whose error would
    point the user the wrong way).
    """
    text = text.strip()
    if not is_url(text):
        return text
    target = raw_url(text)
    if target is None:
        raise SourceError(
            f"don't know how to get a PoB code from {text}\n"
            f"  supported link sites: {', '.join(SUPPORTED)} — or paste "
            "the code itself (PoB → Import/Export Build → Generate)")
    code = (fetch or _get)(target).strip()
    if not code:
        raise SourceError(f"empty response from {target}")
    return code
