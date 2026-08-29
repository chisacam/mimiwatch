# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 명세. `packaging/build.sh`(맥·리눅스)와 `build.ps1`(윈도우)이 씁니다.

한 디렉터리 묶음(onedir)입니다. 한 파일(onefile)은 켤 때마다 수백 MB를 임시
디렉터리에 풀어야 해서 시작이 몇 초 늦고, 모델은 어차피 밖에 있으므로 얻는 것이
없습니다.

모델은 넣지 않습니다. 6GB를 판마다 다시 받게 할 이유가 없고, 사용자 영역
(`paths.home()`)에 두면 저장소에서 돌리던 사람의 모델을 그대로 씁니다. 첫 실행
때 화면의 「모델·도구」가 받습니다.

네이티브 런타임 넷(transcribe.cpp, llama.cpp, sherpa-onnx, CTranslate2)은 각자
자기 패키지 디렉터리 안의 공유 라이브러리를 `@loader_path`나 패키지 경로로
찾습니다. `collect_all`이 패키지 디렉터리 구조를 그대로 옮기므로 그 규칙이
묶음 안에서도 성립합니다. transcribe_cpp 는 네이티브 제공자를 **패키지 메타데이터의
entry point** 로 찾으므로 dist-info 도 함께 넣어야 합니다(copy_metadata) --
이것이 빠지면 "no native provider" 로 시작조차 못 합니다.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(ROOT, "web"), "web"),
    (os.path.join(ROOT, "backends.example.json"), "."),
    # 묶음에도 라이선스가 따라갑니다 -- 비상업 조건과 제3자 고지가 거기 있습니다.
    (os.path.join(ROOT, "LICENSE"), "."),
    # `mimiwatch --doctor` 가 돌리는 진단 스크립트. 소스 그대로 넣습니다.
    (os.path.join(ROOT, "bench", "doctor.py"), "bench"),
]
binaries = []
hiddenimports = [
    # 서버가 늦게(함수 안에서) 가져오는 우리 모듈들. 정적 분석이 놓칠 수 있습니다.
    "asr", "bus", "config", "export", "jobs", "live", "modelhub", "models", "paths",
    "speaker_id", "store", "stream", "tcpp_asr", "transcribe_vod", "translate",
    "sentencepiece", "transcribe_cpp", "certifi",
]
for pkg in ("transcribe_cpp_native", "llama_cpp", "sherpa_onnx", "ctranslate2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += copy_metadata("transcribe_cpp_native") + copy_metadata("transcribe_cpp")
# yt-dlp 는 추출기 천여 개를 필요할 때 가져옵니다. 전부 넣습니다.
hiddenimports += collect_submodules("yt_dlp")

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "ruff", "_pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mimiwatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # 콘솔을 둡니다. 윈도우에서 두 번 누르면 창이 하나 뜨고 거기에 로그가 흐릅니다 --
    # run.ps1 로 띄우던 것과 같은 경험이고, Ctrl-C 로 끄는 길도 그대로입니다.
    # 맥의 .app 은 콘솔 없이 뜨므로 app.py 가 로그를 파일로 돌립니다.
    console=True,
    target_arch=None,
    codesign_identity=os.environ.get("MIMIWATCH_CODESIGN") or None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="mimiwatch",
)

if sys.platform == "darwin":
    # Finder 에서 두 번 눌러 띄우는 .app. 같은 onedir 묶음을 감쌉니다. 서명이 없으므로
    # 처음 열 때 게이트키퍼가 막습니다 -- docs/PACKAGING.md 의 안내를 보십시오.
    app = BUNDLE(
        coll,
        name="mimiwatch.app",
        icon=None,
        bundle_identifier="dev.chisacam.mimiwatch",
        info_plist={
            "CFBundleName": "mimiwatch",
            "CFBundleDisplayName": "mimiwatch",
            "CFBundleShortVersionString": os.environ.get("MIMIWATCH_VERSION", "0.0.0"),
            "NSHighResolutionCapable": True,
            # 브라우저에서 화면을 여는 서버라 Dock 에 오래 있을 이유가 없습니다.
            "LSUIElement": True,
        },
    )
