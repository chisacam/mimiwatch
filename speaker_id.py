"""Attaches a speaker label to a speech segment.

Taken from `speaker_id` in hayamimi (MIT, oboroge0). It pulls a CAM++ embedding
and, by cosine similarity, either attaches it to an existing speaker or makes a
new one; it does not need to know the number of speakers up front, which suits
a stream.

VOD only. CAM++ needs enough voice inside one segment to place it, and live
splits at 3-4 s to keep up with someone who does not stop talking, so that much
is not there. In a measurement six people came out over 70 seconds, and there
were not that many. A label that invents speakers who are not there is worse
than no label.

The labeler is only an add-on on the job. Until now it was a hard one: a
disappeared model (a 187MB file that a cleanup script or a moved model folder
deletes) raised out of the constructor and stopped the whole VOD. `make_labeler`
is the surface the job uses, and a missing model is a line in the log and a
transcript without labels, not a failed job.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import sherpa_onnx

SIM_THRESHOLD = 0.45      # This alike or more counts as the same speaker
MAX_EMBED_SECONDS = 6.0   # Feeding the embedding more than this does not make it better


def _model_path() -> str:
    from stream import model_dir
    return os.path.join(model_dir(), "campplus_sv.onnx")


class SpeakerLabeler:
    def __init__(self, threads: int = 2, threshold: float = SIM_THRESHOLD,
                 solo: bool = False):
        model = _model_path()
        if not os.path.exists(model):
            raise FileNotFoundError(
                f"no campplus_sv.onnx: {model}\n"
                "Run ./install.sh, or check MIMIWATCH_MODEL_DIR.")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model,
                                                        num_threads=threads))
        self._threshold = threshold
        self._solo = solo
        self._centroids: list[np.ndarray] = []   # Mean embedding per speaker
        self._counts: list[int] = []

    def label(self, samples: np.ndarray, sample_rate: int) -> str:
        cap = int(MAX_EMBED_SECONDS * sample_rate)
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate, samples[:cap])
        stream.input_finished()
        emb = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        emb /= np.linalg.norm(emb) + 1e-9

        if self._solo:
            # One voice, nothing to classify. The centroid still follows the
            # voice as it drifts, so a label that was started solo can be
            # handed over to the split path without a cold start.
            if self._centroids:
                n = self._counts[0]
                self._centroids[0] = (self._centroids[0] * n + emb) / (n + 1)
                self._counts[0] = n + 1
            else:
                self._centroids.append(emb)
                self._counts.append(1)
            return "S1"

        best, best_sim = -1, -1.0
        for i, c in enumerate(self._centroids):
            sim = float(np.dot(emb, c) / (np.linalg.norm(c) + 1e-9))
            if sim > best_sim:
                best, best_sim = i, sim

        if best >= 0 and best_sim >= self._threshold:
            # Melting it into the mean follows the same person's voice as it drifts.
            n = self._counts[best]
            self._centroids[best] = (self._centroids[best] * n + emb) / (n + 1)
            self._counts[best] = n + 1
            return f"S{best + 1}"

        self._centroids.append(emb)
        self._counts.append(1)
        return f"S{len(self._centroids)}"


def make_labeler(speakers: bool, solo: bool = False,
                 threshold: float | None = None) -> SpeakerLabeler | None:
    """Build the labeler -- or None, where the VOD goes on without labels.

    `speakers` off is a None for a different reason (the box was not ticked),
    and the missing-model None is the one the log line explains. The caller
    cannot tell them apart and does not need to: both mean "no speaker chip".
    """
    if not speakers:
        return None
    try:
        return SpeakerLabeler(threshold=threshold if threshold is not None
                              else SIM_THRESHOLD, solo=solo)
    except FileNotFoundError as exc:
        print(f"[speaker] {exc} -- continuing without speaker labels",
              file=sys.stderr, flush=True)
        return None
