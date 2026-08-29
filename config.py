"""엔진 설정(`backends.json`)을 읽고 쓰는 한 곳.

예전에는 `jobs.load_config()`가 있는데도 `live.py`가 같은 파일을 세 자리에서
직접 열어 읽었습니다 -- `jobs`가 `live`를 import 하므로 반대 방향으로는
가져올 수 없었기 때문입니다. 그러면 예시 설정에서 새 엔진을 들여오는 일
(`_seed`)이 라이브 경로에서는 돌지 않고, 파일 모양이 바뀌면 고칠 곳이
넷이 됩니다. 이 모듈은 프로젝트 안의 다른 모듈을 가져오지 않으므로 누구나
가져올 수 있습니다.

쓰기는 **원자적**입니다. 이 파일에는 실제 주소와 API 키가 들어 있어, 쓰는
도중에 죽어 반쯤 남은 파일은 다음 기동을 통째로 막습니다. 임시 파일에 쓰고
`os.replace`로 바꿉니다.

읽고-고쳐-쓰기는 한 락 안에서 합니다. 화면에서 엔진 둘을 잇달아 저장하면
두 요청 스레드가 같은 파일을 동시에 고치는데, 락이 없으면 뒤에 쓴 쪽이 앞의
것을 지웁니다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading

import paths

BASE = paths.BASE
# `MIMIWATCH_CONFIG`로 다른 파일을 쓸 수 있습니다(시험이 임시 파일을 씁니다).
# 묶음(PyInstaller)으로 돌 때는 묶음 안이 읽기 전용이라 사용자 영역으로 나갑니다.
CONFIG = paths.config_path()
# 예시 설정은 프로그램과 함께 다닙니다 -- 저장소 안, 또는 묶음 안.
EXAMPLE_CONFIG = os.path.join(BASE, "backends.example.json")

# 설정의 키 이름. 번역기는 `backends`/`active`, 전사기는 `asr_backends`/`asr_active`.
KINDS = {"tr": ("backends", "active"), "asr": ("asr_backends", "asr_active")}

# 지울 수 없는 기본 엔진. 번역은 대체 경로(WithFallback)가 M2M-100을 늘 뒤에
# 두므로 그 설정이 있어야 하고, 전사는 목록에서 사라지면 고를 것이 없어집니다.
# 화면(app.js의 LOCKED)과 같은 값이어야 합니다 -- 예전에는 서버가 이미 없는
# `local-hayamimi`를 지키고 있어서 API로는 기본 전사기를 지울 수 있었습니다.
PROTECTED = {"tr": "local-m2m100", "asr": "tcpp-best"}

_lock = threading.RLock()


def load() -> dict:
    """설정을 읽습니다. 처음이면 예시를 복사하고, 예시에 새로 생긴 엔진을
    들여옵니다."""
    with _lock:
        if not os.path.exists(CONFIG) and os.path.exists(EXAMPLE_CONFIG):
            os.makedirs(os.path.dirname(CONFIG) or ".", exist_ok=True)
            shutil.copy(EXAMPLE_CONFIG, CONFIG)
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return _seed(cfg)


def save(cfg: dict):
    with _lock:
        tmp = CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG)


def _example() -> dict:
    try:
        with open(EXAMPLE_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _seed(cfg: dict) -> dict:
    """예시에 새로 생긴 엔진을 사용자 설정에 들여옵니다.

    backends.json은 첫 실행 때 한 번 복사되고 그 뒤로는 손대지 않습니다 --
    실제 주소와 API 키가 들어 있어 덮어쓸 수 없기 때문입니다. 그런데 그러면
    나중에 추가된 기본 엔진이 기존 사용자에게 영영 닿지 않습니다. 경량
    전사기를 넣고도 아무도 못 보는 일이 실제로 있었습니다.

    한 번 들여온 id는 `seeded`에 적어 둡니다. 그래서 사용자가 지운 엔진은
    다시 살아나지 않고, **정말로 새로 생긴 것만** 들어옵니다.

    처음 이 코드를 만나는 설정에는 `seeded`가 없습니다. 그때는 지금 가지고
    있는 것을 이미 본 것으로 치고, 예시에만 있는 것을 들여옵니다.
    """
    example = _example()
    if not example:
        return cfg
    seen = set(cfg.get("seeded") or [])
    added = []
    for key, _active in KINDS.values():
        have = {b["id"] for b in cfg.get(key, [])}
        seen |= have                      # 지금 가진 것은 이미 본 것입니다
        for entry in example.get(key, []):
            if entry["id"] in have or entry["id"] in seen:
                continue
            cfg.setdefault(key, []).append(dict(entry))
            seen.add(entry["id"])
            added.append(entry["id"])
    if added or set(cfg.get("seeded") or []) != seen:
        cfg["seeded"] = sorted(seen)
        save(cfg)
    if added:
        print(f"[설정] 새 엔진을 들여왔습니다: {', '.join(added)}",
              file=sys.stderr, flush=True)
    return cfg


def entries(kind: str, cfg: dict | None = None) -> list[dict]:
    key, _ = KINDS[kind]
    return (cfg or load()).get(key, [])


def find(kind: str, engine_id: str, cfg: dict | None = None) -> dict | None:
    for b in entries(kind, cfg):
        if b["id"] == engine_id:
            return b
    return None


def find_backend(engine_id: str) -> dict | None:
    return find("tr", engine_id)


def find_asr(engine_id: str) -> dict | None:
    return find("asr", engine_id)


def active(kind: str, cfg: dict | None = None) -> str:
    """지금 기본으로 쓰는 엔진 id. 없으면 지울 수 없는 기본."""
    _, active_key = KINDS[kind]
    return (cfg or load()).get(active_key) or PROTECTED[kind]


def example_default(kind: str) -> str:
    """예시 설정의 기본 활성 엔진. 활성 엔진을 지웠을 때 되돌아갈 자리입니다."""
    _, active_key = KINDS[kind]
    return _example().get(active_key) or PROTECTED[kind]


def upsert(kind: str, entry: dict) -> dict:
    """엔진 하나를 넣거나 갈아 끼웁니다. 화면의 「엔진 관리」가 부릅니다."""
    key, _ = KINDS[kind]
    with _lock:
        cfg = load()
        cfg[key] = [b for b in cfg.get(key, []) if b["id"] != entry["id"]]
        cfg[key].append(entry)
        save(cfg)
        return cfg


def delete(kind: str, engine_id: str) -> dict:
    """엔진 하나를 설정에서 지웁니다. 번역·전사가 같은 규칙입니다.

    지운 것이 활성 엔진이었으면 예시 설정의 기본으로 되돌립니다(그것이 남아
    있을 때). 예전에는 번역 쪽이 무조건 M2M-100으로 되돌아갔는데, 예시의
    기본은 Gemma입니다 -- 지운 뒤 첫 세션이 갑자기 품질이 떨어졌습니다.
    """
    key, active_key = KINDS[kind]
    protected = PROTECTED[kind]
    if engine_id == protected:
        return {"error": "기본 로컬 엔진은 삭제할 수 없습니다"}
    with _lock:
        cfg = load()
        before = cfg.get(key, [])
        kept = [b for b in before if b["id"] != engine_id]
        if len(kept) == len(before):
            return {"error": f"'{engine_id}' 엔진이 없습니다"}
        cfg[key] = kept
        if cfg.get(active_key) == engine_id:
            want = example_default(kind)
            cfg[active_key] = want if any(b["id"] == want for b in kept) else protected
        save(cfg)
        return cfg
