"""The wav reader that does not load the whole audio.

`read_wav` used to turn the entire file into a float32 array. On a 116-minute
stream that was a peak RSS of 1,116MB, and 445MB of it stayed for the whole
translation stage after transcription was done. Now the file is memory-mapped
and converted only where it is sliced -- so the one thing to watch here is
whether it behaves like an array and gives the same values.

No model is loaded. Only the slicing contract is examined.
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
    # Transcription slices from the front. Every slice has to hold the same values.
    for i in range(0, len(raw), 3):
        assert np.array_equal(got[i:i + 3], want[i:i + 3])
    assert got[2:5].dtype == np.float32


def test_read_wav_reads_past_a_LIST_chunk(tmp_path):
    """Do not assume the sample chunk sits at the head of the file.

    Depending on its settings ffmpeg writes a `LIST` (authoring info) chunk ahead
    of `data`. Using a fixed offset instead of walking the chunks turns such a
    file into noise.
    """
    raw = np.array([5, -5, 100, -100], dtype=np.int16)
    path = tmp_path / "b.wav"
    write_wav(path, raw)
    body = path.read_bytes()
    at = body.index(b"data")
    # Insert a chunk of odd length -- RIFF pads to even, so one more byte is appended.
    extra = b"LIST" + (5).to_bytes(4, "little") + b"INFOx" + b"\x00"
    patched = body[:at] + extra + body[at:]
    size = int.from_bytes(patched[4:8], "little") + len(extra)
    patched = patched[:4] + size.to_bytes(4, "little") + patched[8:]
    path.write_bytes(patched)

    got = vod.read_wav(str(path))
    assert np.array_equal(got[:], raw.astype(np.float32) / 32768.0)


def test_read_wav_refuses_what_the_pipeline_cannot_decode(tmp_path):
    """Anything that is not 16 kHz mono 16-bit is called out on the spot.

    The old `read_wav` used an assert. An assert disappears under -O, and it
    carried no message, so it could not tell the user what was wrong.
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
