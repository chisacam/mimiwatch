"""오디오를 통째로 올리지 않는 wav 판독기.

예전에는 `read_wav`가 파일 전체를 float32 배열로 만들었습니다. 116분짜리
방송에서 봉우리 RSS 1,116MB 였고, 그중 445MB 는 전사가 끝난 뒤 번역 단계
내내 남아 있었습니다. 지금은 파일을 메모리에 대응해 두고 잘라 쓸 때만
바꿉니다 -- 그래서 여기서 볼 것은 「배열처럼 굴면서 값이 같은가」 하나입니다.

모델은 올리지 않습니다. 자르는 규약만 봅니다.
"""
import wave

import numpy as np
import pytest

import transcribe_vod as vod


def write_wav(path, samples: np.ndarray, rate=16000, channels=1, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return str(path)


def test_read_wav_matches_the_old_whole_file_read(tmp_path):
    raw = np.array([0, 1, -1, 32767, -32768, 1234, -4321, 7], dtype=np.int16)
    path = write_wav(tmp_path / "a.wav", raw)
    want = raw.astype(np.float32) / 32768.0

    got = vod.read_wav(path)
    assert len(got) == len(raw)
    assert np.array_equal(got[:], want)
    # 전사는 앞에서부터 잘라 갑니다. 조각마다 값이 같아야 합니다.
    for i in range(0, len(raw), 3):
        assert np.array_equal(got[i:i + 3], want[i:i + 3])
    assert got[2:5].dtype == np.float32


def test_read_wav_reads_past_a_LIST_chunk(tmp_path):
    """표본 덩어리가 파일 앞머리에 있다고 가정하지 않습니다.

    ffmpeg 는 설정에 따라 `LIST`(제작 정보) 덩어리를 `data` 앞에 씁니다.
    덩어리를 걸어가지 않고 고정 오프셋을 쓰면 그 파일에서 잡음이 나옵니다.
    """
    raw = np.array([5, -5, 100, -100], dtype=np.int16)
    path = tmp_path / "b.wav"
    write_wav(path, raw)
    body = path.read_bytes()
    at = body.index(b"data")
    # 홀수 길이 덩어리를 끼웁니다 -- RIFF 는 짝수로 채우므로 한 바이트가 더 붙습니다.
    extra = b"LIST" + (5).to_bytes(4, "little") + b"INFOx" + b"\x00"
    patched = body[:at] + extra + body[at:]
    size = int.from_bytes(patched[4:8], "little") + len(extra)
    patched = patched[:4] + size.to_bytes(4, "little") + patched[8:]
    path.write_bytes(patched)

    got = vod.read_wav(str(path))
    assert np.array_equal(got[:], raw.astype(np.float32) / 32768.0)


def test_read_wav_refuses_what_the_pipeline_cannot_decode(tmp_path):
    """16kHz 모노 16비트가 아니면 그 자리에서 말합니다.

    예전 `read_wav` 는 assert 였습니다. assert 는 -O 로 돌면 사라지고,
    메시지도 없어 사용자에게 무엇이 잘못됐는지 말해 주지 못했습니다.
    """
    raw = np.zeros(8, dtype=np.int16)
    stereo = write_wav(tmp_path / "s.wav", raw, channels=2)
    with pytest.raises(vod.VodError):
        vod.read_wav(stereo)
    slow = write_wav(tmp_path / "r.wav", raw, rate=44100)
    with pytest.raises(vod.VodError):
        vod.read_wav(slow)
    notwav = tmp_path / "n.wav"
    notwav.write_bytes(b"not a riff file at all")
    with pytest.raises(Exception):
        vod.read_wav(str(notwav))
