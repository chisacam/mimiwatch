"""Resolving a chzzk recording: the address shape, the two playback shapes, the cheapest rendition.

Nothing here reaches the network. Everything `chzzk.py` fetches goes through
`_get_json` or the single `urlopen` in `smallest_hls`, and a test stubs whichever
of the two it needs; the autouse `no_network` fixture turns every other call into
a failure on the spot, so a test that quietly starts talking to
api.chzzk.naver.com fails instead of passing slowly.
"""
from __future__ import annotations

import json
import os
import urllib.error

import pytest

import chzzk
import transcribe_vod as vod

PAGE = "https://chzzk.naver.com/video/15186552"
NO = "15186552"
# Signed and short-lived, like the real one -- the tests below never store it.
MASTER = "https://cdn.example/rewind/abc/master.m3u8?hdntl=exp=1758000000"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A fetch the test did not stub itself is a test bug, not a slow test."""
    def boom(*a, **k):
        raise AssertionError("tests do not reach the network")

    monkeypatch.setattr(chzzk.urllib.request, "urlopen", boom)


def _content(**over) -> dict:
    """The `content` block of the video endpoint, carrying the fields resolve reads."""
    c = {"videoTitle": "7시간 방송 다시보기", "duration": 25234,
         "thumbnailImageUrl": "https://img.example/thumb.jpg",
         "channel": {"channelName": "채널 이름", "channelId": "abc123"}}
    c.update(over)
    return c


def _rewind(path: str = MASTER) -> str:
    """`liveRewindPlaybackJson` -- JSON inside a JSON field, the way the site sends it."""
    return json.dumps({"media": [{"mediaId": "HLS", "protocol": "hls", "path": path}]})


# One period, one adaptation set, four renditions. The tallest `PD_` is the one to
# play and the cheapest `PD_` is the one to pull sound out of; the segmented entry
# is taller *and* cheaper than all of them, so if the `PD_` filter ever goes both
# URLs change and the two tests below say so. That entry is also the half yt-dlp
# dies on -- the reason this module exists (chzzk.py).
_MANIFEST = {"period": [{"adaptationSet": [{"representation": [
    {"id": "PD_720", "bandwidth": 1_200_000, "height": 720,
     "baseURL": [{"value": "https://cdn.example/720.mp4?_lsu_sa_=t7"}]},
    {"id": "PD_1080", "bandwidth": 8_384_000, "height": 1080,
     "baseURL": [{"value": "https://cdn.example/1080.mp4?_lsu_sa_=t10"}]},
    {"id": "PD_144", "bandwidth": 192_000, "height": 144,
     "baseURL": [{"value": "https://cdn.example/144.mp4?_lsu_sa_=t1"}]},
    {"id": "video_avc1_2160", "bandwidth": 1_000, "height": 2160,
     "baseURL": [{"value": "https://cdn.example/segmented/2160.mp4"}]},
]}]}]}


def _stub_json(monkeypatch, body: dict, manifest: dict | None = None) -> list[str]:
    """Answer chzzk's two endpoints out of a dict. Returns the addresses that were asked for."""
    asked: list[str] = []

    def get(url, timeout):
        asked.append(url)
        if url.startswith(chzzk._VIDEO_API):
            return body
        assert url.startswith(chzzk._PLAYBACK), url
        assert manifest is not None, "the manifest endpoint should not have been asked"
        return manifest

    monkeypatch.setattr(chzzk, "_get_json", get)
    return asked


def _stub_fetch(monkeypatch, text: str | None = None, exc: Exception | None = None):
    """Stand in for the one urlopen in smallest_hls. Returns the addresses fetched."""
    got: list[str] = []

    class Body:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return text.encode("utf-8")

    def urlopen(req, timeout=None):
        got.append(req.full_url if hasattr(req, "full_url") else req)
        if exc is not None:
            raise exc
        return Body()

    monkeypatch.setattr(chzzk.urllib.request, "urlopen", urlopen)
    return got


def test_video_no_matches_only_a_recording():
    """A recording number, and nothing else -- a live address is live.py's."""
    assert chzzk.video_no(PAGE) == NO
    assert chzzk.video_no("http://chzzk.naver.com/video/7") == "7"
    assert chzzk.video_no("https://www.chzzk.naver.com/video/123?tab=chat") == "123"
    assert chzzk.video_no("  " + PAGE + "  ") == NO
    # /live/<hex> is a broadcast. It goes to live.py, and claiming it here would
    # send a running stream down the recording flow.
    assert chzzk.video_no("https://chzzk.naver.com/live/abcdef0123456789") == ""
    assert chzzk.video_no("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == ""
    assert chzzk.video_no("https://chzzk.naver.com/video/abc") == ""
    assert chzzk.video_no("chzzk.naver.com/video/1") == ""        # No scheme, no match
    assert chzzk.video_no("https://chzzk.naver.com.evil.example/video/1") == ""
    assert chzzk.video_no("") == ""
    assert chzzk.video_no(None) == ""


def test_resolve_rewind_plays_the_master_playlist(monkeypatch):
    """The rewind of a broadcast: one HLS master, and every metadata field mapped."""
    asked = _stub_json(monkeypatch, {"code": 200, "content": _content(
        liveRewindPlaybackJson=_rewind())})
    d = chzzk.resolve(NO)
    assert d["play_kind"] == "hls" and d["play_url"] == MASTER
    assert d["id"] == NO and d["title"] == "7시간 방송 다시보기"
    assert d["duration"] == 25234 and d["uploader"] == "채널 이름"
    assert d["channel"] == "abc123" and d["thumbnail"] == "https://img.example/thumb.jpg"
    assert d["is_live"] is False and d["site"] == "chzzk"
    # A rewind is one request, and `audio_url` is not made unless it is asked
    # for -- reading the master costs a second round trip (no_network would
    # have failed the test if it had been read).
    assert len(asked) == 1 and asked[0] == chzzk._VIDEO_API + NO
    assert "audio_url" not in d


def test_resolve_rewind_audio_is_the_cheapest_variant(monkeypatch):
    """`audio=True` reads the master and picks the 144p rendition -- same sound, 43x less of it."""
    _stub_json(monkeypatch, {"code": 200, "content": _content(
        liveRewindPlaybackJson=_rewind())})
    got = _stub_fetch(monkeypatch, "\n".join([
        "#EXTM3U",
        "#EXT-X-STREAM-INF:BANDWIDTH=8384000,RESOLUTION=1920x1080",
        "1080p/index.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=192000,RESOLUTION=256x144",
        "144p/index.m3u8",
    ]))
    d = chzzk.resolve(NO, audio=True)
    assert d["play_url"] == MASTER                       # The picture is still the good one
    assert d["audio_url"] == "https://cdn.example/rewind/abc/144p/index.m3u8"
    assert got == [MASTER]


def test_resolve_manifest_picks_the_tallest_and_ignores_the_segmented(monkeypatch):
    """No rewind: the DASH manifest, of which only the `PD_` files are usable."""
    asked = _stub_json(monkeypatch, {"code": 200, "content": _content(
        videoId="vid-1", inKey="key-1")}, _MANIFEST)
    d = chzzk.resolve(NO)
    assert d["play_kind"] == "mp4"
    assert d["play_url"] == "https://cdn.example/1080.mp4?_lsu_sa_=t10"
    assert d["site"] == "chzzk" and d["is_live"] is False
    assert "audio_url" not in d
    # Both endpoints, and the manifest is asked for with the key the metadata carried.
    assert len(asked) == 2 and asked[1].startswith(chzzk._PLAYBACK + "vid-1")
    assert "key=key-1" in asked[1]


def test_resolve_manifest_audio_is_the_lowest_bandwidth(monkeypatch):
    """Transcription wants the smallest file that still has the sound."""
    _stub_json(monkeypatch, {"code": 200, "content": _content(
        videoId="vid-1", inKey="key-1")}, _MANIFEST)
    d = chzzk.resolve(NO, audio=True)
    assert d["audio_url"] == "https://cdn.example/144.mp4?_lsu_sa_=t1"
    assert d["play_url"] == "https://cdn.example/1080.mp4?_lsu_sa_=t10"


def test_resolve_without_a_playable_file_raises(monkeypatch):
    """A subscribers-only or age-rated recording: the metadata arrives, `inKey` does not.

    It must not come back as a dict with an empty URL -- that reads as success
    all the way to the player, which then sits there with nothing to play.
    """
    for content in (_content(),                                   # neither
                    _content(videoId="vid-1"),                    # no inKey
                    _content(inKey="key-1"),                      # no videoId
                    _content(liveRewindPlaybackJson=_rewind("")),  # rewind with no path
                    _content(liveRewindPlaybackJson="not json")):
        _stub_json(monkeypatch, {"code": 200, "content": content})
        with pytest.raises(chzzk.ChzzkError) as e:
            chzzk.resolve(NO)
        assert NO in str(e.value)
    # A manifest that has nothing but segmented renditions is the same case.
    _stub_json(monkeypatch, {"code": 200, "content": _content(videoId="v", inKey="k")},
               {"period": [{"adaptationSet": [{"representation": [
                   {"id": "video_avc1_1080", "bandwidth": 1, "height": 1080,
                    "baseURL": [{"value": "https://cdn.example/seg.mp4"}]}]}]}]})
    with pytest.raises(chzzk.ChzzkError):
        chzzk.resolve(NO)


def test_resolve_carries_the_sites_own_refusal(monkeypatch):
    """A non-200 `code` in the body is an error, and the message the site wrote is kept."""
    _stub_json(monkeypatch, {"code": 404, "message": "존재하지 않는 영상입니다."})
    with pytest.raises(chzzk.ChzzkError) as e:
        chzzk.resolve(NO)
    assert "존재하지 않는 영상입니다." in str(e.value)
    # A 200 with no content block is a refusal too, and there is no message to quote.
    _stub_json(monkeypatch, {"code": 200, "content": None})
    with pytest.raises(chzzk.ChzzkError) as e:
        chzzk.resolve(NO)
    assert NO in str(e.value)


def test_smallest_hls_picks_the_lowest_bandwidth(monkeypatch):
    """ffmpeg handed a master takes 1080p60 for sound that is identical in the 144p variant."""
    got = _stub_fetch(monkeypatch, "\n".join([
        "#EXTM3U",
        "#EXT-X-STREAM-INF:BANDWIDTH=8384000,RESOLUTION=1920x1080,CODECS=\"avc1\"",
        "1080p/index.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720",
        "720p/index.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=192000,RESOLUTION=256x144",
        "144p/index.m3u8",
        "",
    ]))
    # The href is relative in the real playlist, so it is resolved against the master.
    assert chzzk.smallest_hls(MASTER) == "https://cdn.example/rewind/abc/144p/index.m3u8"
    assert got == [MASTER]


def test_smallest_hls_falls_back_to_the_master(monkeypatch):
    """A playlist we cannot read should cost bandwidth, not the whole job."""
    _stub_fetch(monkeypatch, "#EXTM3U\n#EXT-X-ENDLIST\n")
    assert chzzk.smallest_hls(MASTER) == MASTER
    # A variant line with no URL under it is not a variant.
    _stub_fetch(monkeypatch, "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n#EXT-X-ENDLIST\n")
    assert chzzk.smallest_hls(MASTER) == MASTER
    # And neither is one with no BANDWIDTH to compare.
    _stub_fetch(monkeypatch, "#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=256x144\n144p/index.m3u8\n")
    assert chzzk.smallest_hls(MASTER) == MASTER


def test_smallest_hls_survives_a_failed_fetch(monkeypatch):
    for exc in (urllib.error.URLError("no route"), TimeoutError(), OSError("closed")):
        _stub_fetch(monkeypatch, exc=exc)
        assert chzzk.smallest_hls(MASTER) == MASTER


def _resolved(**over) -> dict:
    d = {"id": NO, "title": "7시간 방송 다시보기", "duration": 25234,
         "uploader": "채널 이름", "channel": "abc123",
         "thumbnail": "https://img.example/thumb.jpg", "is_live": False,
         "site": "chzzk", "play_url": MASTER, "play_kind": "hls"}
    d.update(over)
    return d


def test_probe_chzzk_answers_without_yt_dlp(monkeypatch):
    """probe routes a recording here, and the doc carries what `-j` never could."""
    def boom(*a, **k):
        raise AssertionError("a chzzk recording does not go through yt-dlp")

    monkeypatch.setattr(vod.subprocess, "run", boom)
    monkeypatch.setattr(chzzk, "resolve", lambda no, audio=False: _resolved(id=no))
    meta = vod.probe(PAGE)
    # The id says where it came from; a bare number is a doc the page cannot
    # pick a player for.
    assert meta["id"] == "chzzk-" + NO
    assert meta["title"] == "7시간 방송 다시보기" and meta["duration"] == 25234
    assert meta["uploader"] == "채널 이름" and meta["is_live"] is False
    assert meta["site"] == "chzzk" and meta["channel"] == "abc123"
    assert meta["thumbnail"] == "https://img.example/thumb.jpg"
    # The page address, not the media address: the media one expires within the day.
    assert meta["url"] == PAGE
    assert "play_url" not in meta and "audio_url" not in meta


def test_probe_chzzk_turns_a_refusal_into_a_vod_error(monkeypatch):
    """Whatever chzzk said has to reach the "Add" dialog, which only knows VodError."""
    def refuse(no, audio=False):
        raise chzzk.ChzzkError("needs the chzzk login")

    monkeypatch.setattr(chzzk, "resolve", refuse)
    with pytest.raises(vod.VodError) as e:
        vod.probe_chzzk(NO, PAGE)
    assert "needs the chzzk login" in str(e.value)
    with pytest.raises(vod.VodError):
        vod.probe(PAGE)


def test_cookie_header_sends_one_site_only(tmp_path, monkeypatch):
    """The Naver login goes to Naver. A YouTube cookie must never ride along."""
    monkeypatch.setenv("MIMIWATCH_HOME", str(tmp_path))
    d = tmp_path / "cookies"
    d.mkdir()
    assert chzzk._cookie_header() == ""            # No file is not an error; a public recording needs none
    (d / "youtube.txt").write_text(
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tyoutube-secret\n", encoding="utf-8")
    (d / "chzzk.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        "\n"
        ".naver.com\tTRUE\t/\tTRUE\t0\tNID_AUT\tnaver-a\n"
        "#HttpOnly_.naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tnaver-b\n"
        "short\tline\n", encoding="utf-8")
    got = chzzk._cookie_header()
    assert got == "NID_AUT=naver-a"
    assert "youtube-secret" not in got
    assert os.path.exists(d / "youtube.txt")
