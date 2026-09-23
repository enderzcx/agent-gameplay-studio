# Changelog

## 0.1.0 — 2026-09-23

First public-package revision. Extracted from a private working repository into a standalone,
reviewable repository with a fresh git history. Nothing was uploaded before review.

### Contents

- `skills/gameplay-postproduction/` — the postproduction SOP: canonical standard, routing,
  voice-design reference, one game-specific checklist, four templates, a trigger-case eval, and the
  deterministic checker `scripts/check_postproduction.py`.
- `tools/gamerec/` — macOS app-level "target app audio + picture" recorder
  (Swift / ScreenCaptureKit), its build script, a preflight/start/verify wrapper, and a two-group
  regression suite.
- `tools/voice/` — an optional, env-configured TTS adapter; an EDL assembler that fails before
  rendering when narration cannot fit its window; a subtitle burner for systems without libass.
- `tests/` — **74 offline checks** (22 checker + 4 EDL assembly + 48 TTS guarantees). No network
  egress, no model, no credentials. The TTS suite talks only to a throwaway loopback endpoint.
- `examples/` — fully synthetic fixtures and example artifacts.

### Changed while extracting (vs. the private originals)

- **Recorder renamed and de-branded.** Its binary name, bundle identifier, log prefix and
  dispatch-queue labels previously carried a product name and a personal handle; they are now neutral
  (`GameAVRec` / `com.example.gameavrec`). The retired identifiers are intentionally not reproduced.
- **No default recording target.** The original fell back to one specific game's bundle id. A target
  is now mandatory, and the missing-target case exits `64` before any permission is requested.
- **Exit-code collision fixed.** Usage errors used to exit `3`, which collided with "refuse to
  overwrite". They now exit `64`.
- **Removed a private default source path** and a hardcoded game-window crop from the EDL assembler.
- **Removed a private dotenv path** and the baked-in endpoint/voice defaults from the TTS client.
- **References genericized.** Private host paths, a non-public company gateway name, and a personal
  absolute path were removed; the methodological findings that did not depend on them were kept.
- **The recorder regression suite was split** into an offline group and a permission-required group,
  so a run without Screen Recording permission reports SKIP instead of a false pass.
- **The no-overwrite safety gate was moved into the always-on offline group.** It executes before the
  recorder is constructed, so it needs no permission; a fail-closed property should not require a
  granted permission to be testable. `--log` is now covered alongside `--out`.
- **The TTS guarantees became executable.** A new offline suite drives the adapter against a loopback
  fake endpoint and proves: the script is never rewritten (and a provider-side rewrite is flagged),
  `--strict-no-rewrite` refuses instead of warning, empty audio fails, an unmeasurable duration fails
  and deletes the partial file, and only a genuinely measured success enters the cache. A
  `--strict-no-rewrite` flag was added to both `synth` and `batch`.
- **No reproduction of retired private identifiers.** The extraction notes describe the *class* of
  change (de-branding, path removal) without quoting the old strings, machine paths, account handles
  or receipt values. The quick-start uses a neutral `$REPO_URL` rather than a named clone URL.
- **Documented honestly** what is verified, what is unverified, and what this repository deliberately
  does not contain or provide.

### Public-readiness fixes (rev 3)

Applied after a first read-back of the export, before anything was published.

- **SKILL.md de-coupled from one host.** It previously said the video understanding came from a
  specific companion skill, that editing went to a specific editor, and that the package was
  "local self-use, no cross-user distribution need" with no packaging. For a public skill-driven
  toolkit that was self-contradictory. The required capabilities are now stated as **host-provided
  video analysis and editing**, with the author's own tools named only as *replaceable examples /
  optional adapters*; the end-to-end ownership sentence (the skill drives through to an actual
  finished cut, and must not fall back to handing back a plan) is kept and strengthened; the obsolete
  local-only wording is gone.
- **A real "Using the skill" section** was added to both READMEs, with symlink / copy / no-install
  options that explicitly check before writing, never overwrite an existing skill, and never touch a
  global config.
- **Checker examples now default to repository-relative paths** instead of any machine's home
  directory, with the installed-path form shown as the alternative.
- **Non-copyable shell examples fixed.** `export GAME_BUNDLE_ID=<your game's bundle id>` was not
  valid shell (redirect plus unclosed quote). Both READMEs and the tool docs now use a quoted, runnable
  example (`GAME_BUNDLE_ID="com.megacrit.SlayTheSpire2"`) with the substitution stated in a comment.
  The same angle-bracket-in-shell pattern was swept out of the recorder docs and the game checklist,
  and every `bash`-labelled fence in the docs is now syntax-checked.
- **Over-absolute claims corrected.** The evaluation-separation paragraph no longer implies that
  sharing a model or tool between the two projects is itself a violation; it now states the actual
  rule — an *information* boundary, where postproduction analysis and answers must not reach an
  evaluated decision — and presents the two-repo split as a design choice that supports it. The
  director-note finding is no longer phrased as "did not beat sampling variance" (no test was run);
  it now reads as "not demonstrated in one small 2×2 sample", with the explicit note that this is not
  "disproven" and says nothing about the provider.
- **README opening rewritten as a product description.** It leads with what the project is and what
  you get, keeps the honest scope statement brief, and moves every unverified boundary into the status
  table and a dedicated "Honest boundaries" section instead of front-loading caveats.
- **Minimum Python version now has evidence.** The whole offline suite was run on **Python 3.9.6**, not
  only a newer interpreter, so "3.9+" is a verified claim rather than an assumption.
- **Provider claim tightened.** The TTS adapter is described everywhere as targeting **one** wire
  shape — never as cross-vendor or "supports any speech API" — and no wording suggests that changing a
  base URL makes it universally compatible.

### TTS and assembly hardening (rev 3, wrapped up)

Landed in code and docs, not only asserted in tests.

- **Cache identity now covers the acceptance mode and the endpoint.** The key was
  `text/model/voice/direction/optimize/fmt`; it now also includes `strict_no_rewrite` and a
  **one-way fingerprint of the endpoint**. Without the strict flag a take accepted leniently could be
  replayed to a strict run; without the endpoint fingerprint, pointing at a different provider would
  silently replay the *previous* provider's audio. The endpoint **URL is never stored** — only its
  fingerprint, and only for that comparison.
- **Provider errors are redacted.** Bodies and transport messages can echo the request, so bearer
  tokens, quoted / single-quoted / bare `api_key=`-`token=`-`secret=`-`password=` pairs, key-shaped
  strings and credential-bearing query parameters are replaced with `<redacted>` and truncated before
  they can reach a log, a traceback or an exception message. Redaction deliberately over-matches;
  benign text and host names survive.
- **`.env` file configuration is documented.** Credentials come from the process environment, or from
  a dotenv-style file named by `TTS_ENV_FILE` when the environment does not already set them. Values
  are read for those names only, never echoed, never written into an artefact — and the
  missing-variable error now says so.
- **The assembler is documented as narration-only.** `build_sample.sh` renders every picture clip with
  `-an`, so the source audio track is discarded at the cutting stage, and the final bed is built
  solely from the narration segments. It never silently mixes in source audio; if the source is
  intentionally silent the result is honestly silent, and ducking the original game sound is left as
  an explicit caller decision.

### Release engineering

- **Commit identity is a derived GitHub noreply address**, built from the account id and login
  (`<id>+<login>@users.noreply.github.com`) and set with `--local` only. No employer or personal
  global git identity is used, and **no global git config was modified**.
- **Verified on pristine snapshots.** The README entry points were re-run on a `git archive` of the
  commit (committed blobs only: no `.git`, no untracked, no ignored files) and on a fresh `git clone`,
  with `GAME_BUNDLE_ID`/`TTS_*`/`SRC_VIDEO` unset — so the results cannot be an artifact of leftover
  local state. A negative control with `ffprobe` shadowed by a failing shim confirms the suite exits
  non-zero rather than passing vacuously.
- **Dropped the redundant account handle from the copyright line** in `LICENSE`, so no GitHub login
  appears in any tracked file's contents.

### Known limitations carried in this revision

- Only one game has a `<game>-checklist.md`. No other game is adapted.
- Audio/video **sync is unverified** by the tooling; the verifier says so in its own output.
- Minimized / hidden / locked screen, muted output, multi-display, and non-macOS recording are
  untested.
- Whether director notes change TTS delivery is **not demonstrated** — one small 2×2 sample showed
  no stable prompt effect, with no significance test run. Not demonstrated is not disproven, and it
  says nothing about the provider's capability.
- The TTS adapter is **not a cross-vendor abstraction**. It targets exactly one wire shape and only
  that shape is tested; a different provider needs its own adapter, and two providers exposing the
  same route name may still differ in every `audio` sub-field.
- `ready` mode has a quirk inherited from `sheet` mode: it requires the literal token `unknown` to
  appear somewhere in the sheet, because the standard always documents that escape hatch. A fully
  verified sheet still needs that line present. Behaviour is unchanged from the installed skill so
  that the exported checker is the same artifact; it is recorded here rather than silently patched.
