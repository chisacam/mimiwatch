---
name: mimiwatch-release
description: Cut a mimiwatch release — run the checks, push main, push a v* tag, watch the GitHub Actions build, and confirm every asset landed on the release. Use for "릴리즈해주세요", "새 릴리즈를 발행해주세요", "태그 붙여서 푸시해주세요", "v0.x.y 로 올려주세요", "cut a release", or after a fix the owner wants users to receive. The tag IS the deploy — a v* push starts the build and attaches binaries. Not for pushing main alone, and not for the packaging scripts themselves.
---

# Cut a release

A `v*` tag is not a label, it is the trigger: `.github/workflows/build.yml` builds the
bundles and `softprops/action-gh-release` attaches them. Everything below exists because
the tag cannot be taken back cleanly once someone has downloaded from it.

| # | Step | Reversible? |
|---|---|---|
| 1 | checks green | yes |
| 2 | commit | yes |
| 3 | push `main` | awkward |
| 4 | push `v*` tag | **this publishes** |
| 5 | watch the build | — |
| 6 | verify assets on the release | — |

## Step 0 — read before tagging

```sh
git tag | sort -V | tail -3                    # the last version
sed -n '1,95p' .github/workflows/build.yml     # today's job list and asset globs
git log --oneline $(git tag | sort -V | tail -1)..HEAD   # what this release contains
```

The matrix **changes**. It has held four jobs and three; the CUDA job was removed
because the cu124 wheels carry no `sm_120` kernels and 726 MB was not worth it. Read the
file; do not carry a job count in your head, and do not report a build as incomplete
because a job you remembered is absent.

## Step 0b — the decisions to confirm with the requester

1. **The version number.** Patch for fixes, minor for a feature the owner called big
   (multiview went out as `0.3.0`). Ask; do not infer from the diff size.
2. **Is this release meant to be picked up by the in-app updater?** `update.py` checks
   GitHub releases once a day. A release cut only to test the build is a release users
   will be offered.
3. **Release notes in Korean**, like every other user-facing string here.

## Step 1 — checks, and only then a commit

```sh
.venv/bin/python bench/check_all.py
.venv/bin/ruff check .
```

CI (`check.yml`) runs `ruff check .`, `pytest`, and `bench/ext_check.py`. It installs
only `numpy pytest ruff`, so a test that needs anything else fails there and passes
locally — that has happened on a main push and it is a test bug, not a CI bug.

If `web/overlay.js`, `cuestore.js`, `capture.js`, `ytid.js` or `capture-worklet.js`
changed, copy them to `ext/` first. `ext_check.py` fails on a byte difference, and the
`ext` job zips `ext/` as a release asset — a stale copy ships.

## Step 2 — push main, then tag

```sh
git push origin main
git tag vX.Y.Z && git push origin vX.Y.Z
```

Never tag a commit that is not on `main` yet; the workflow checks out the tag and the
release would carry code no branch has.

`fetch-depth: 0` in the workflow is load-bearing — the version name comes from
`git describe`. A shallow checkout produces a bundle that misreports its own version.

## Step 3 — watch the build, and know what "done" means

Watch all jobs, not the first to finish. Then check the **release page**, not the run:

```sh
gh run list --workflow build.yml --limit 3
gh release view vX.Y.Z --json assets --jq '.assets[].name'
```

Expect one archive per build-matrix entry plus the `ext` zip. `if-no-files-found: error`
catches an empty `dist/`, but a job can be green while the release is missing an asset if
the glob and the produced filename disagree.

## Step 4 — a failed build means retracting the tag

The fix goes on `main`, then the tag moves. Both sides, in this order:

```sh
git push origin :refs/tags/vX.Y.Z     # remote first — it is what users see
git tag -d vX.Y.Z
gh release delete vX.Y.Z --yes        # only if a release object was created
```

Then re-tag the fixed commit. `v0.2.0` was deleted this way. Do not reuse a version
number that has been published for more than a few minutes — bump the patch instead.

## Traps that have bitten before

- **The tag is the deploy.** Tagging to "see if it builds" publishes a release the
  in-app updater will offer. Push a branch and run the workflow manually instead.
- **A green run is not a complete release.** Verify assets from `gh release view`.
- **macOS bundles are unsigned.** Gatekeeper blocks every launch and the user has to
  approve it in System Settings each time. Unresolved as of `v0.4.0` — say so in the
  release notes rather than letting the owner rediscover it.
- **The updater round trip has never been verified end to end**, and the Windows apply
  path (swap script) has not been exercised at all. A release that changes `update.py`
  cannot be called verified on this evidence.
- **Runner images retire.** `macos-14` was replaced by `macos-15`; a build that fails at
  the runner line is an image, not your code.

## Step 5 — after the release

Write the notes in Korean: what changed, and what the release does *not* fix (known
Gatekeeper prompt, any platform left unverified). If the owner is going to test the
bundle, say which platform's asset is which.

## What this skill does not do

- Code signing or notarization. It needs a paid Apple identity; it is the owner's call.
- Verify the update round trip. State that it is unverified and hand it back.
- Build the CUDA variant. `packaging/build.ps1 -Backend cuda` exists for whoever wants
  it locally; it is deliberately out of the release.
- Test the Windows or AMD path. There is no such machine here — the owner has testers.
