"""발화 구간에 화자 딱지를 붙입니다.

hayamimi(MIT, oboroge0)의 `speaker_id`에서 가져왔습니다. CAM++ 임베딩을
뽑아 코사인 유사도로 기존 화자에 붙이거나 새 화자를 만드는 방식이고,
화자 수를 미리 알 필요가 없어 방송에 맞습니다.

녹화본 전용입니다. CAM++는 한 구간에 목소리가 충분히 들어 있어야 자리를
잡는데, 라이브는 말이 끊기지 않는 사람을 따라가려고 3~4초로 자르기 때문에
그만큼이 없습니다. 실측에서 70초 동안 여섯 명이 나왔고, 실제로는 그만큼
있지 않았습니다. 없는 화자를 만들어 내는 딱지는 없느니만 못합니다.
"""
from __future__ import annotations

import os

import numpy as np
import sherpa_onnx

SIM_THRESHOLD = 0.45      # 이 이상 닮으면 같은 화자로 봅니다
MAX_EMBED_SECONDS = 6.0   # 임베딩은 길게 넣어도 더 좋아지지 않습니다


def _model_path() -> str:
    from stream import model_dir
    return os.path.join(model_dir(), "campplus_sv.onnx")


class SpeakerLabeler:
    def __init__(self, threads: int = 2, threshold: float = SIM_THRESHOLD):
        model = _model_path()
        if not os.path.exists(model):
            raise FileNotFoundError(
                f"campplus_sv.onnx가 없습니다: {model}\n"
                "./install.sh 를 실행하거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model,
                                                        num_threads=threads))
        self._threshold = threshold
        self._centroids: list[np.ndarray] = []   # 화자별 평균 임베딩
        self._counts: list[int] = []

    def label(self, samples: np.ndarray, sample_rate: int) -> str:
        cap = int(MAX_EMBED_SECONDS * sample_rate)
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate, samples[:cap])
        stream.input_finished()
        emb = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        emb /= np.linalg.norm(emb) + 1e-9

        best, best_sim = -1, -1.0
        for i, c in enumerate(self._centroids):
            sim = float(np.dot(emb, c) / (np.linalg.norm(c) + 1e-9))
            if sim > best_sim:
                best, best_sim = i, sim

        if best >= 0 and best_sim >= self._threshold:
            # 평균에 녹여 두면 같은 사람의 목소리가 조금씩 변해도 따라갑니다.
            n = self._counts[best]
            self._centroids[best] = (self._centroids[best] * n + emb) / (n + 1)
            self._counts[best] = n + 1
            return f"S{best + 1}"

        self._centroids.append(emb)
        self._counts.append(1)
        return f"S{len(self._centroids)}"
