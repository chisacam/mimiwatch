# -*- mode: python ; coding: utf-8 -*-
"""The PyInstaller spec. Used by `packaging/build.sh` (macOS · Linux) and
`build.ps1` (Windows).

It is a one-directory bundle (onedir). One file (onefile) would have to unpack
hundreds of MB into a temporary directory on every start, which costs a few
seconds, and since the models sit outside anyway there is nothing to gain.

The models are not put in. There is no reason to make 6GB be downloaded again
for every version, and keeping them in the user area (`paths.home()`) means
someone who was running from the repository keeps using the same models. On the
first run "Models and tools" on the screen fetches them.

Each of the four native runtimes (transcribe.cpp, llama.cpp, sherpa-onnx,
CTranslate2) finds the shared libraries inside its own package directory through
`@loader_path` or the package path. `collect_all` moves the package directory
structure as it is, so that rule holds inside the bundle too. transcribe_cpp
finds its native provider through **the package metadata's entry point**, so the
dist-info has to go in with it (copy_metadata) -- without that it cannot even
start, with "no native provider".
"""
import os
import subprocess
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# The version number is baked into the bundle. A running program has to know its
# own version to compare it with the GitHub releases (update.py). The build
# script puts MIMIWATCH_VERSION in, and without it (the spec run by hand) it is
# made with git describe.
_v = os.environ.get("MIMIWATCH_VERSION", "").strip()
if not _v:
    try:
        _v = subprocess.run(["git", "-C", ROOT, "describe", "--tags", "--always"],
                            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        _v = ""
_vfile = os.path.join(ROOT, "build", "_version.txt")
os.makedirs(os.path.dirname(_vfile), exist_ok=True)
with open(_vfile, "w", encoding="utf-8") as f:
    f.write((_v or "0.0.0") + "\n")

datas = [
    (_vfile, "."),
    (os.path.join(ROOT, "web"), "web"),
    (os.path.join(ROOT, "backends.example.json"), "."),
    # The license travels with the bundle too -- the non-commercial terms and the
    # third-party notices are in there.
    (os.path.join(ROOT, "LICENSE"), "."),
    # The diagnostic script `mimiwatch --doctor` runs. It goes in as source.
    (os.path.join(ROOT, "bench", "doctor.py"), "bench"),
]
binaries = []
hiddenimports = [
    # Our own modules the server imports late (inside a function). Static analysis
    # can miss them.
    "asr", "bus", "config", "export", "jobs", "live", "modelhub", "models", "paths",
    "speaker_id", "store", "stream", "tcpp_asr", "transcribe_vod", "translate", "update",
    "sentencepiece", "transcribe_cpp", "certifi",
]
for pkg in ("transcribe_cpp_native", "llama_cpp", "sherpa_onnx", "ctranslate2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += copy_metadata("transcribe_cpp_native") + copy_metadata("transcribe_cpp")
# yt-dlp imports its thousand-odd extractors when it needs them. All of them go in.
# The YouTube JS challenge solver scripts (yt-dlp-ejs, two .js files) are data files,
# so they have to be gathered separately -- without them it quietly becomes
# "some formats may be missing".
hiddenimports += collect_submodules("yt_dlp")
try:
    d, b, h = collect_all("yt_dlp_ejs")
    datas += d
    hiddenimports += h
except Exception:
    print("[spec] no yt_dlp_ejs -- check pip install 'yt-dlp[default]'")

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
    # The console is kept. Double-clicking on Windows brings up one window and the
    # log flows there -- the same experience as bringing it up with run.ps1, and the
    # way out with Ctrl-C is unchanged. The macOS .app comes up without a console, so
    # app.py sends the log to a file instead.
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
    # The .app you bring up by double-clicking in Finder. It wraps the same onedir
    # bundle. It has no signature, so Gatekeeper blocks the first open -- see the
    # guidance in docs/PACKAGING.md.
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
            # It is a server that opens the screen in a browser, so there is no reason
            # for it to sit in the Dock.
            "LSUIElement": True,
        },
    )
