# NOTICE — provenance, third-party status, and intentional omissions

## License scope

MIT (see `LICENSE`) applies to the material in this repository **owned by the author**:
the `gameplay-postproduction` skill package, the recorder source under `tools/gamerec/`, the tools
under `tools/voice/`, the tests, the examples, and the docs.

Nothing here re-licenses third-party work. No third-party source is vendored.

## Provenance of every shipped file

| Path | Origin | Notes |
|---|---|---|
| `skills/gameplay-postproduction/**` | Author's own work; extracted from a private working repository | `SKILL.md` already declared `license: MIT` in its frontmatter before extraction |
| `tools/gamerec/**` | Author's own work | Swift/ScreenCaptureKit recorder; renamed and de-branded for this package (see below) |
| `tools/voice/tts_adapter.py` | Author's own project script, adapted | Was a project-local client for one vendor. Now endpoint-neutral and env-configured, with no baked-in endpoint or voice — but it is **not** a cross-vendor abstraction; it targets one wire shape only |
| `tools/voice/build_sample.sh` | Author's own work, adapted | Removed a private default source path and a hardcoded crop |
| `tools/voice/burn_subs.py` | Author's own work, rewritten for this package | Burn-in now goes through ffmpeg **libass** with a `PlayRes` equal to the real video size. The previous Pillow + temporary-PNG + `overlay` chain was removed because it burned only the first cue on a long cut and depended on a temp directory |
| `tools/voice/subtitles.py` | Author's own work, extracted from the same project | Phrase-level cue paging and SRT/ASS writing; carries the total-centisecond ASS timestamp fix |
| `tools/voice/check_burned_subs.py` | Authored for this package | Pixel-diff subtitle check (burned vs unburned cut). Standard library + ffmpeg only |
| `tests/*.py`, `tests/run_offline_tests.sh` | Author's own work | `test_build_sample.py` came from the same project; the checker suite is new for this package |
| `examples/**` | **Authored for this package** | Fully synthetic. Contains no real footage, gameplay, audio, timeline or result |
| `README*.md`, `docs/**`, `NOTICE.md`, `SECURITY.md`, `CHANGELOG.md`, `Makefile`, `.env.example` | Authored for this package | |

`skills/gameplay-postproduction/SKILL.md` also carries the author's own skill-metadata convention key
(`metadata.sunny_skill_type`). It is a local convention tag with no secret in it; it is kept because
removing it would break the author's local skill tooling. It implies nothing for your use.

## Changes made during extraction

So a reviewer can see exactly what was altered from the working originals:

- **Renamed and de-branded the recorder.** Its binary name, bundle identifier, log prefix and
  dispatch-queue labels previously carried a product name and a personal handle. They are now neutral
  (`GameAVRec`, `com.example.gameavrec`). The old identifiers are deliberately **not** reproduced
  here; the reviewable fact is the class of change, not the retired string.
- **Removed the built-in default recording target.** The original defaulted to one specific game's
  bundle id. Silently capturing the wrong app is worse than failing, so a target is now required,
  and the new "no target" case exits `64` (usage) *before* requesting Screen Recording permission.
- **Fixed an exit-code collision:** usage errors used to exit `3`, the same code as "refuse to
  overwrite". They now exit `64`.
- **Build output moved** into `tools/gamerec/build/` (gitignored) instead of a repo-root `tools/`.
- **Removed a private default source path** from `build_sample.sh` (`SRC_VIDEO` is now required) and
  a hardcoded game-window crop (geometry is now entirely `SRC_CROP`).
- **Removed a private dotenv path** from the TTS client and its baked-in endpoint and voice
  defaults; credentials and endpoint are now entirely yours.
- **Genericized references** that named the author's private host, a non-public company gateway, or
  a personal absolute path. The methodological findings were kept; the private specifics were not.
- **Split the recorder regression suite** into an offline group and a permission-required group so
  the shipped suite cannot appear to pass checks it never ran.

## Intentionally NOT included

These were deliberately left out. Their absence is a decision, not an oversight:

- Any personal absolute path, machine identifier, internal hostname, Steam ID or session ID.
- Any credential, token, cookie, `.env` value, or account detail. `.env.example` holds placeholders
  only.
- Raw API response logs, base64 media blobs, and any captured model I/O.
- Real gameplay footage, game audio, game assets, music, voice recordings, fonts, save files, or
  signed binaries.
- Any recording or clone of a real person's voice.
- Private chat logs, system prompts, or internal rule packs.
- Non-public company project code, endpoints or internal documentation.
- Source control history from the private working repository. This package starts a **fresh git
  history**; it does not carry the origin's commits.

## Third-party names and trademarks

Game titles, engine names, model names, voice names and product names that appear in the docs are
used **descriptively** to explain what was tested. They belong to their respective owners, and their
appearance implies no affiliation, sponsorship or endorsement.

One shipped reference (`references/spire-checklist.md`) is a worked example for **one specific
game**. It is evidence that the pipeline was adapted to a real game once — it is **not** a claim of
support for that game or any other. Every other game is unadapted; the absence of a
`<game>-checklist.md` means exactly that.

## Runtime dependencies (not vendored)

| Dependency | Needed for | License/availability |
|---|---|---|
| Python 3.9+ | everything | PSF license |
| `ffmpeg` / `ffprobe` | media probing, cut assembly, **subtitle burn-in**, `ready` mode | LGPL/GPL depending on build — install it yourself. Burn-in additionally needs a build with **libass** (`ffmpeg -filters \| grep ass`) |
| Xcode command-line tools (`swiftc`, `codesign`) | compiling `tools/gamerec/` | Apple terms |
| macOS 15+ | `tools/gamerec/` only | Apple terms |

No dependency is vendored, pinned into this repo, or downloaded by the test suite. The offline tests
use only Python's standard library plus `ffmpeg`/`ffprobe` for the cases that need real media.
