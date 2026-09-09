"""The rules for reading and writing the config (backends.json)."""
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
    # A config missing one of the example's transcription engines. Absent from seeded it is brought in, present it is not.
    lite = [b for b in ex["asr_backends"] if b["id"] != "tcpp-best"][0]["id"]
    base = {**ex, "asr_backends": [b for b in ex["asr_backends"] if b["id"] != lite]}
    with open(config.CONFIG, "w", encoding="utf-8") as f:
        json.dump({**base, "seeded": []}, f)
    assert lite in {b["id"] for b in config.load()["asr_backends"]}

    with open(config.CONFIG, "w", encoding="utf-8") as f:
        json.dump({**base, "seeded": [lite]}, f)        # Something the user deleted
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
    assert config.active("asr") == "tcpp-lite"            # The example defaults to the light CPU engine
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
    assert got["active"] == _example()["active"]      # The example's default, not local-m2m100
    # Once the example's default is deleted too, it falls back to the one that cannot be deleted.
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


def test_viewer_lang_getter_and_setter():
    # Unset: the getter answers the default, so a config written before this
    # setting existed still seats the select (unlike ui_lang, where the front
    # end falls back to the browser's guess).
    assert config.viewer_lang() == config.DEFAULT_VIEWER_LANG
    # Stale: a code the current list does not know falls back to the default
    # rather than being handed back to a select that cannot hold it.
    cfg = config.load()
    cfg["viewer_lang"] = "fr"
    config.save(cfg)
    assert config.viewer_lang() == config.DEFAULT_VIEWER_LANG
    # Set: a valid code round-trips to the file and back.
    assert config.set_viewer_lang("ja") == {"viewer_lang": "ja"}
    assert config.viewer_lang() == "ja"
    assert config.load()["viewer_lang"] == "ja"
    # The list is fixed; a code outside it is refused, not written.
    assert "error" in config.set_viewer_lang("fr")
    assert config.viewer_lang() == "ja"
