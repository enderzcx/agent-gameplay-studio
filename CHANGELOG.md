# Changelog

## Unreleased — 2026-09-24 (round 4)

**One prompt → finished cut: an executable default path, real subtitles, and honest source audio.**

The skill said it would drive an end-to-end request to a finished cut, but there was no executable
default path: the burn-in step was not in the pipeline, the assembler dropped the source audio, and the
"how do I actually call the video-understanding channel" knowledge lived only in a project-local
script. Closed with the smallest pieces that were already proven on a real 92-second pilot.

- **`references/runbook.md` — the default execution path.** One prompt in, ordered steps out
  (preflight → candidate events → back-to-source verification → commentary → EDL → assembly →
  paging/burn → pixel check → listen review + picture review → `audit` + `ready`), with the exact
  commands, the failure semantics, and the four cases that legitimately require asking the user.
  It states plainly that this is an **agent-executed skill plus toolset, not a resident deterministic
  one-click director**, and that a missing dependency, missing authorization or thin source material
  must surface as a real error instead of a plausible-looking artefact. It also pins the four lengths
  that must never be conflated: narrative unit ≠ TTS group ≠ subtitle cue ≠ shot.
- **Subtitles are paged, burned and then actually looked at.** New `tools/voice/subtitles.py`
  (phrase-level paging, SRT + a **true-size** ASS whose `PlayRes` equals the video, total-centisecond
  timestamps) and a rewritten `tools/voice/burn_subs.py` that burns through ffmpeg **libass**.
  The Pillow + temporary-PNG + `overlay` chain is **removed**: it burned only the first cue on a long
  cut and depended on a temp directory. `build_sample.sh` now emits `subs.ass` and takes
  `BURN_SUBS=1`; the burn happens **before** the receipt is written, so the audit binds the file that
  is actually delivered, and the unburned cut is kept as `final-nosub.mp4`.
- **New `tools/voice/check_burned_subs.py` — pixel-level subtitle evidence.** It diffs each cue's
  frame against the same frame of the unburned cut, so "the word" and "something that was already
  white" can be told apart, and reports per cue: on screen at all / inside the subtitle band /
  clear of the left-right safety margin (a clipped final character) / clear of the protected UI band.
  It is explicit about not judging listening quality, semantic match or line-break comfort.
- **Source audio can be kept, or the build fails.** `build_sample.sh` gained `KEEP_SRC_AUDIO=1`
  (slice the source audio with the same EDL, clamp it to the same window, mix under the narration,
  `SRC_AUDIO_DUCK=1` sidechain-ducks it; both stems survive). With no audio track in the source it
  **fails closed** rather than shipping a silent track passed off as game audio. The default is still
  narration-only, and the log says so.
- **`references/routing.md` rewritten** around the role split that was actually verified: fast video
  model for structure, high-resolution clip/crop for detail reads, **audio-only** for listen review,
  **the final exported MP4** for picture review — plus the honest rules for fallbacks (an "automatic
  fallback" may only be called that once it has been implemented and observed under its real trigger;
  auth/permission/refusal is never routed around, and oversized uploads use a compressed analysis copy
  with recorded hashes and time mapping, never a modified master).
- **`SKILL.md`** now points at the runbook first, forbids "make a sample and wait for approval" as a
  default move, and states the boundary and the four distinct lengths.
- Tests: 204 -> **213** offline cases (32 checker + 112 audit + **5 subtitles** + 9 -> **13** EDL
  assembly + 51 TTS). The new assembly cases cover burn + pixel check, wrong-band rejection,
  source-audio mixing with silent-source refusal, and refusal to emit a "cut" with no subtitle text.
  No new runtime dependency: `subtitles.py` and `check_burned_subs.py` are standard library.

### Round 4 follow-up — correctness and honesty fixes found by running the new path on real footage

Running the new path on a real 26-second clip surfaced defects that the unit fixtures did not, so the
same round was closed out with these:

- **Phrase paging vs the delivery gate.** `check_timeline_audit.py` bound subtitles as **1 cue ↔ 1
  narration row**, so a paged long line could never pass `ready`. The binding is now
  "the row's cues concatenate to the row's script" **and** "their union equals the row's real audio
  span (offset included)": paging is allowed, while a squeezed or holed line still fails
  (out-of-order, out-of-span, overlapping, missing or extra text are all rejected).
- **`make_cut.sh`** — one command for the delivery default: preflight, decide the source audio
  (keep + duck when the source really has audio, record it and continue when it does not), force
  `BURN_SUBS=1`, then the adopted-timeline audit. `build_sample.sh` keeps its historical defaults.
- **`scripts/find_tools.py`** — `tools/voice/` is not inside the skill directory (the snapshot layout
  is `<root>/skills/<name>/…` plus `<root>/tools/voice/…`). The locator prints the real path instead of
  letting a host agent guess, and says not to hand-roll a bypass script.
- **Subtitle input is no longer forgiving.** `parse_srt` fails loudly on a missing timecode, `end <=
  start`, empty text, or non-monotonic cues (it used to `continue`, which is how "74 cues, 1 burned"
  went unnoticed); CRLF is normalised and wrapped Latin lines keep their word boundaries.
- **The burner escapes its filter argument.** `ass=<path>` broke the filtergraph for output paths
  containing `,` `:` or `'`; the ASS actually handed to ffmpeg is now copied to a safe temporary path.
  A missing `--labels` file is an error instead of a silent skip.
- **`ffprobe` failure is no longer read as "no audio".** Both the assembler and the burner used to
  treat an empty probe as a silent source and would drop the track; they now fail instead.
- **The pixel checker stops over-claiming.** Out-of-band and margin pixels are judged against an
  explicit noise tolerance (re-encoding produces some), `--min-text-px` must be > 0, band/protect/
  late-sample are validated, and `px/字` is printed as a **diagnostic only** — a pixel diff can prove
  something was drawn, never which characters. `not_a_verdict_on` now leads with text correctness.
- **bash 3.2 + non-ASCII.** `$OUT，` (variable immediately followed by a CJK character) made the
  draft notice itself die with `unbound variable`; braces are now used, and a test guards the notice.
- Tests: 213 -> **223** offline cases (5 -> 8 subtitles, 13 -> 20 assembly).


## Unreleased — 2026-09-24

**Production-path closure (review round 3).**

The default authoring path could still pass falsely or force a second render:

- **First build closes the loop.** The final digests cannot exist before they are produced, so the
  input recipe may now carry placeholders. `tools/voice/produce_timeline.py` cross-checks the recipe
  against the real EDL (shot order, source ranges, 1x speed, freeze, narration text, measured audio
  length, offset) and the real `SRC_VIDEO` digest **before rendering**, then writes
  `adopted-timeline.tsv` (real digests, real `audio_offset_s`, real `hold_burned_in`) and
  `produce-receipt.json` after rendering. `ready` consumes the adopted file — no
  "build once for the digests, fill them in, build again" cycle.
- **The receipt records what was actually read.** `source_video` and `edl` are no longer copied
  from the preflight ledger; the audit requires `source_video.sha256` to belong to the assets the
  timeline uses, and `--edl` (optional; used by the default path) verifies the EDL digest. A
  preflight for A with `SRC_VIDEO=B` (same length, different picture) fails before rendering.
- **Silence and the ratio use the merged union of the real audio spans**, offset included, instead
  of `sum(spans)`.
- **The one-cue-per-row subtitle must MATCH its row's audio span** (tolerance `--tol`); merely
  sitting inside it is not enough, so a whole sentence squeezed into the last 0.1 s now fails.
  `audio_offset_s` is recorded with the real EDL value instead of a "~0.2 s" assumption.
- **A hold mark must reach the picture.** `hold_burned_in: yes` is required for any freeze; the
  assembler burns the annotation with `drawtext` and only freezes the frame it can actually freeze
  (the shot's last frame), otherwise it fails closed and keeps `DRAFT.txt`. Form outside this
  assembler's support (variable speed, multiple sources, complex transitions) fails explicitly.
- Tests: 184 -> **204** offline cases (32 checker + 112 audit + 9 EDL assembly + 51 TTS).

**Workflow hardening: narration anchors, holds, silence justification and version binding.**

A real re-edit of one recording exposed defects the workflow could not see, and the old reports mixed
three different versions of the same cut. The gap was not a missing document — it was that the
deterministic checker had no notion of *which phase a line narrates over*, of an *unlabelled hold*,
of *why a stretch is silent*, or of *which version of the subtitle/audio actually shipped*. Fixed on
the existing architecture: one extra deterministic checker plus five timeline columns, wired into the
delivery path rather than left as an optional tool.

### Added

- `skills/gameplay-postproduction/scripts/check_timeline_audit.py` — two new deterministic modes,
  pure standard library, no model calls:
  - **`preflight`** probes each source with real ffprobe/ffmpeg and writes a machine-readable ledger:
    digest, size, duration, **measured** frame rate, video/audio presence, and
    `audio_signal ∈ present|silent|absent`, `frame_sampling ∈ normal|sparse`,
    `status ∈ determined|undetermined`. An undecidable probe exits non-zero instead of passing, a
    non-zero `ffmpeg` exit is a failure rather than a verdict, `-show_entries` uses the correct `:`
    section separator (the `&` form silently dropped the `format` block), and `source_path` is stored
    absolute-resolved with relative paths resolved against the manifest directory.
  - **`audit`** reads the adopted timeline and enforces: the declared `(anchor_asset,
    anchor_event, anchor_source)` is **cross-checked against that row's real shot span** — a line
    anchored to a different event at the same phase, a referenced interval outside the shot, or a
    cross-turn shot whose line does not cover the span are all errors; a line whose phase differs
    from the picture's must be **declared** `claim_mode=retrospective` (the global phase order is
    explicitly *not* treated as a timeline), and inside one event a line may not give the result
    before the picture reaches that phase; `freeze > 0` needs an on-screen mark carrying the held
    frame's source timecode, and the anchor must also sit inside the narration's referenced interval;
    a reward hold's anchor must fall inside `visible_window`; narration must fit its own picture
    window; **silence is measured on the real audio spans** (`final_start` + measured
    `audio_duration_s`), not on picture windows, and the narration ratio uses the same audio seconds,
    so a 1 s line on a 66 s shot is 65 s of silence; every gap ≥ the threshold needs one
    silence-ledger row (`keep`/`cut`/`narration_added` + a substantive reason, with stale or
    self-contradicting rows rejected); the timeline must declare
    `subtitle_sha256`/`audio_sha256`/`final_sha256` and the audit checks those digests **plus the
    per-cue subtitle text and timing**, that `--audio` really carries an audio stream, and that the
    cut is the same total length — a same-segment-count but re-worded / re-timed subtitle, a replaced
    audio track or an older export all fail; a **produce receipt** written by the production path at
    the moment of production must bind that one timeline to those three artifacts (a receipt for
    another timeline, or old media documented by a fresh declaration, fails); and the preflight
    ledger's sha256/size must still match the file on disk (**stale manifest is an error**).
    NaN / inf / negative / reversed intervals and tolerances are rejected everywhere, and the
    tolerance/threshold validation lives in the library so the CLI and `ready` agree.
- `skills/gameplay-postproduction/templates/silence-ledger.md` — per-gap justification template.
- `examples/silence-ledger.example.tsv` and the five audit columns on
  `examples/timeline.example.tsv`.
- `tests/test_timeline_audit.py` + `tests/fixtures/` — 94 offline cases over anonymous synthetic
  fixtures, including one per reproduced defect. Media is generated with lavfi at test time, so no
  binary fixture is committed.
- Three new TTS cache cases (`C6`): an incremental re-render is **per node** — editing one segment
  re-synthesizes that node only and leaves the neighbours cached.
- The audit report carries `anchors` (what was cross-checked against which shot span),
  `phase_crossings` (declared retrospectives), `silence.audio_spans` (the measured narration spans),
  `human_annotations_not_machine_verified` (declared anchors, evidence text, hold marks and
  visible windows are human input, not machine proof) and `not_a_verdict_on`.

### Changed

- **`ready` is now a full delivery gate.** It requires `--timeline`, `--preflight`,
  `--silence-ledger`, `--subtitle`, `--audio` and `--receipt` in addition to `--final-mp4`;
  it **carries the audit's warnings and `unverified` items into its own payload** instead of
  swallowing them; omitting any of them
  reports `unverified` instead of passing. It also **re-runs the audit in-process** — there is
  deliberately no "pass me your audit JSON" input, and no early return may skip the audit (the media
  checks now accumulate errors instead of returning). The audit is therefore on the default path,
  not a checker nobody calls.
- **`tools/voice/build_sample.sh` runs the same audit on the authoring path.** With `TIMELINE`,
  `PREFLIGHT` and `SILENCE_LEDGER` set it audits the freshly exported cut and exits `3` with a
  `DRAFT.txt` marker if the audit fails; without them it still stamps `DRAFT.txt` and says on stderr
  that the cut has not been through the adoption audit. An unaudited cut can no longer be mistaken
  for a deliverable one.
- `check_postproduction.py timeline` gained the audit-column schema check (all-or-nothing) and now
  warns when they are absent.
- `Makefile` / `tests/run_offline_tests.sh` gained the audit suite; the offline total went from 74 to
  **184** cases (32 checker + 94 audit + 7 EDL assembly + 51 TTS).
- `SKILL.md`, `references/postproduction-standard.md` (§1.1 preflight, §4.1–4.4, §6.1, §7.1),
  `templates/timeline.md`, `templates/review-sheet.md` (A6/A7 + mandatory gate inputs),
  `docs/architecture.md`, `examples/README.md`, `README.md` and `README.zh-CN.md` updated.

### Notes

- The new checks are deterministic and offline. **Perceived delivery, intonation, and factual
  semantics (card picked / damage / win-loss) are still not machine-judged**, and the audit report
  says so in `not_a_verdict_on`. A green audit is not "the cut is good".
- No new dependency, no model call, no npm/release action, and the recorder and TTS adapter's wire
  behaviour are unchanged.

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
