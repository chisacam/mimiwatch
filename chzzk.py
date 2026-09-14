"""Resolving a chzzk recording, without yt-dlp.

yt-dlp knows the site -- `CHZZKVideoIE` matches `chzzk.naver.com/video/<n>` --
but on 2026-09-14 it could not be relied on for recordings, and the reason is
structural rather than a passing breakage. A chzzk recording comes in two
shapes, and which one a video has is in `vodStatus`:

  * `liveRewindPlaybackJson` is filled in -- the rewind of a broadcast. A
    normal HLS master playlist with five renditions, `EXT-X-ENDLIST` at the end
    so a player treats it as a recording and not a live edge. yt-dlp reads it.

  * `vodStatus == "ABR_HLS"` -- despite the name, what the player fetches is a
    DASH manifest. yt-dlp walks it with the generic MPD parser and dies:

        ERROR: 15186552: An extractor error has occurred.
               (caused by KeyError('sourceURL'))

    chzzk writes `<Initialization range="0-76870"/>` with no `sourceURL`, and
    `common.py` reads that attribute unconditionally. Making it tolerant only
    moves the failure one line down to `KeyError('media')`, because the
    `<SegmentURL>` entries are byte ranges too. Supporting that is real work on
    yt-dlp's DASH side, not a patch we can carry, and master had neither fix
    when this was measured.

The measurement that settles it: of three recordings sampled, the one the owner
handed over to test with was exactly the shape yt-dlp cannot open.

So recordings are resolved here instead, from the same two endpoints the web
player uses. What comes back is better than what yt-dlp's `-j` gave us anyway:
a real thumbnail and the channel id, neither of which `vod.probe` could carry.

**The URL expires.** The rewind playlist is signed with `hdntl=exp=<unix>`, 17
hours out when measured, and the progressive file carries its own `_lsu_sa_`
token. Nothing may store one and hand it back later -- the live side learnt
this already (`live.play_url`), and recordings work the same way: the page asks
for a URL when it is about to play, and gets one made a moment ago.

**Two URLs, not one.** The browser wants the best picture; transcription wants
the smallest file that still has the sound, because the audio is the same in
every rendition and the video is not. For a seven-hour broadcast that is 565 MB
against 26 GB.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

import paths

# A browser's. The endpoints answer a bare urllib with 200 as well, but the
# player's own headers are the ones chzzk is guaranteed to keep working.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

_VIDEO_API = "https://api.chzzk.naver.com/service/v3/videos/"
_PLAYBACK = "https://apis.naver.com/neonplayer/vodplay/v1/playback/"

# Only the numeric recording id. A live address is `/live/<hex>` and belongs to
# live.py; anything else is not ours to claim.
_VIDEO_URL = re.compile(r"^https?://(?:www\.)?chzzk\.naver\.com/video/(\d+)")

TIMEOUT_S = 20.0
# The manifest of a long broadcast is over a megabyte of JSON, and it is fetched
# from a Naver API rather than a CDN, so it is the slow one of the two.
MANIFEST_TIMEOUT_S = 45.0


class ChzzkError(RuntimeError):
    """The site would not give us this recording."""


def video_no(url: str) -> str:
    """The recording number in a chzzk address, or "" when it is not one."""
    m = _VIDEO_URL.match((url or "").strip())
    return m.group(1) if m else ""


def _cookie_header() -> str:
    """The pasted chzzk login, as one `Cookie:` line.

    yt-dlp is handed a Netscape file (`stream.merged_cookie_file`), which is no
    use here -- this module speaks HTTP itself. It reads the one site's file
    rather than the merged one so that a YouTube cookie is never sent to Naver.
    A subscriber-only rewind needs this; a public one does not, so a missing
    file is not an error.
    """
    try:
        with open(paths.cookies_path("chzzk"), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""
    pairs = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        # domain  flag  path  secure  expiry  name  value
        parts = ln.split("\t")
        if len(parts) >= 7 and parts[5]:
            pairs.append(f"{parts[5]}={parts[6]}")
    return "; ".join(pairs)


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json",
        # Without it the playback endpoint answers with a DASH XML document
        # instead of the JSON shape this module reads.
        "Referer": "https://chzzk.naver.com/",
    })
    cookie = _cookie_header()
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as exc:
        raise ChzzkError(f"chzzk answered {exc.code} for {url.split('?', 1)[0]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ChzzkError(f"could not reach chzzk: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChzzkError("chzzk answered with something that is not JSON") from exc


def _meta(no: str) -> dict:
    d = _get_json(_VIDEO_API + no, TIMEOUT_S)
    if d.get("code") != 200 or not isinstance(d.get("content"), dict):
        raise ChzzkError(d.get("message") or f"chzzk has no recording {no}")
    return d["content"]


def _progressive(video_id: str, in_key: str) -> list[tuple[int, int, str]]:
    """The plain files in the DASH manifest, as (bandwidth, height, url).

    Only the `PD_` representations are taken. The rest are segmented, which is
    the half yt-dlp chokes on and the half nothing here needs: a `PD_` entry is
    one mp4 behind a plain `<BaseURL>`, served with `accept-ranges: bytes` and
    `access-control-allow-origin: *`, so the browser plays it with a bare
    `<video src>` and ffmpeg seeks into it over HTTP without downloading the
    front of the file.
    """
    url = f"{_PLAYBACK}{video_id}?key={in_key}&env=real&lc=en_US&cpl=en_US"
    mpd = _get_json(url, MANIFEST_TIMEOUT_S)
    out = []
    for period in mpd.get("period") or []:
        for aset in period.get("adaptationSet") or []:
            for rep in aset.get("representation") or []:
                base = rep.get("baseURL") or []
                if not str(rep.get("id") or "").startswith("PD_") or not base:
                    continue
                href = (base[0] or {}).get("value") or ""
                if href:
                    out.append((int(rep.get("bandwidth") or 0),
                                int(rep.get("height") or 0), href))
    return sorted(out)


def smallest_hls(master_url: str) -> str:
    """The cheapest rendition in an HLS master, for pulling sound out of.

    ffmpeg handed a master picks the best video stream, which for a rewind is
    1080p60 -- 8384 kbps where the 144p variant is 192 kbps, for sound that is
    identical in both. The master is a dozen lines, so reading it is free
    against what it saves.

    Falls back to the master itself: a playlist we cannot parse should cost
    bandwidth, not the whole job.
    """
    req = urllib.request.Request(master_url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            text = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError):
        return master_url
    best = None
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if not ln.startswith("#EXT-X-STREAM-INF"):
            continue
        m = re.search(r"BANDWIDTH=(\d+)", ln)
        href = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if m and href and not href.startswith("#"):
            if best is None or int(m.group(1)) < best[0]:
                best = (int(m.group(1)), href)
    if not best:
        return master_url
    return urllib.parse.urljoin(master_url, best[1])


def resolve(no: str, audio: bool = False) -> dict:
    """Everything about one recording, with a URL that plays right now.

    `audio=True` adds `audio_url`, the cheapest rendition. It costs one more
    request for a rewind (reading the master), so the page asking for something
    to play does not pay it.
    """
    c = _meta(no)
    ch = c.get("channel") or {}
    out = {
        "id": no,
        "title": c.get("videoTitle") or "",
        "duration": c.get("duration"),
        "uploader": ch.get("channelName") or "",
        "channel": ch.get("channelId") or "",
        "thumbnail": c.get("thumbnailImageUrl") or "",
        "is_live": False,
        "site": "chzzk",
        "play_url": "",
        "play_kind": "",
    }

    rewind = c.get("liveRewindPlaybackJson")
    if rewind:
        try:
            media = (json.loads(rewind).get("media") or [{}])[0] or {}
        except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
            media = {}
        master = media.get("path") or ""
        if master:
            out["play_url"], out["play_kind"] = master, "hls"
            if audio:
                out["audio_url"] = smallest_hls(master)
            return out

    video_id, in_key = c.get("videoId"), c.get("inKey")
    if video_id and in_key:
        reps = _progressive(video_id, in_key)
        if reps:
            out["play_url"] = max(reps, key=lambda r: r[1])[2]
            out["play_kind"] = "mp4"
            if audio:
                out["audio_url"] = reps[0][2]
            return out

    # No rewind playlist and no manifest we can read. Almost always a login:
    # a subscriber-only or an adult recording answers with the metadata and
    # withholds `inKey`.
    raise ChzzkError(
        f"chzzk gave no playable file for recording {no}. "
        "A subscribers-only or age-rated recording needs the chzzk login "
        "(Settings → Cookies).")
