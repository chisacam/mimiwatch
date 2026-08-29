"""설정(backends.json)을 읽고 쓰는 규칙."""
import json
import os

import config


def _example():
    with open(config.EXAMPLE_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def test_first_load_copies_example():
    assert not os.path.exists(config.CONFIG)
    cfg = config.load()
    assert os.path.exists(config.CONFIG)
    assert cfg["active"] == _example()["active"]
    assert {b["id"] for b in cfg["backends"]} >= {b["id"] for b in _example()["backends"]}


def test_save_is_atomic_and_locked():
    cfg = config.load()
    cfg["active"] = "local-m2m100"
    config.save(cfg)
    assert not os.path.exists(config.CONFIG + ".tmp")
    assert config.load()["active"] == "local-m2m100"


def test_seed_brings_new_engines_but_not_deleted_ones():
    ex = _example()
    # 예시의 전사 엔진 하나를 빠뜨린 설정. seeded에 없으면 들여오고, 있으면 아닙니다.
    lite = [b for b in ex["asr_backends"] if b["id"] != "tcpp-best"][0]["id"]
    base = {**ex, "asr_backends": [b for b in ex["asr_backends"] if b["id"] != lite]}
    with open(config.CONFIG, "w", encoding="utf-8") as f:
        json.dump({**base, "seeded": []}, f)
    assert lite in {b["id"] for b in config.load()["asr_backends"]}

    with open(config.CONFIG, "w", encoding="utf-8") as f:
        json.dump({**base, "seeded": [lite]}, f)        # 사용자가 지웠던 것
    assert lite not in {b["id"] for b in config.load()["asr_backends"]}


def test_upsert_and_delete():
    got = config.upsert("tr", {"id": "x", "label": "X", "backend": "openai",
                              "base_url": "http://h", "model": "m"})
    assert any(b["id"] == "x" for b in got["backends"])
    got = config.upsert("tr", {"id": "x", "label": "X2", "backend": "openai",
                              "base_url": "http://h", "model": "m"})
    assert [b["label"] for b in got["backends"] if b["id"] == "x"] == ["X2"]
    got = config.delete("tr", "x")
    assert not any(b["id"] == "x" for b in got["backends"])
    assert "error" in config.delete("tr", "x")


def test_set_active_and_setup_flag():
    assert config.active("asr") == "tcpp-lite"            # 예시의 기본은 가벼운 CPU 엔진
    assert config.active("tr") == "local-m2m100"
    assert "error" in config.set_active("asr", "nope")
    config.set_active("asr", "tcpp-best")
    assert config.load()["asr_active"] == "tcpp-best"
    assert config.setup_done() is False
    config.mark_setup_done()
    assert config.setup_done() is True


def test_protected_engines_cannot_be_deleted():
    assert "error" in config.delete("tr", config.PROTECTED["tr"])
    assert "error" in config.delete("asr", config.PROTECTED["asr"])


def test_deleting_active_falls_back_to_example_default():
    cfg = config.load()
    cfg["backends"].append({"id": "remote", "backend": "openai", "base_url": "h", "model": "m"})
    cfg["active"] = "remote"
    config.save(cfg)
    got = config.delete("tr", "remote")
    assert got["active"] == _example()["active"]      # local-m2m100이 아니라 예시의 기본
    # 예시 기본까지 지운 뒤에는 지울 수 없는 기본으로.
    cfg = config.load()
    cfg["backends"] = [b for b in cfg["backends"] if b["id"] != _example()["active"]]
    cfg["backends"].append({"id": "remote2", "backend": "openai", "base_url": "h", "model": "m"})
    cfg["active"] = "remote2"
    config.save(cfg)
    assert config.delete("tr", "remote2")["active"] == config.PROTECTED["tr"]


def test_find_and_active():
    assert config.find_backend("local-m2m100")["backend"] == "local"
    assert config.find_asr("no-such") is None
    assert config.active("asr") == _example()["asr_active"]
