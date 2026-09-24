# Verification log

What was actually run for this exported revision, and what the results were.
Everything below is reproducible offline from a clean checkout. No model was called, no game was
launched, and no credential was used.

Environment: macOS (arm64), `swiftc` from Xcode command-line tools, `ffmpeg`/`ffprobe` present,
Python 3.9+.

One network nuance, stated up front: the TTS suite binds a throwaway HTTP server on **`127.0.0.1`
only** and talks to it over loopback. No packet leaves the machine and no remote host is contacted.

---

## 1. Offline test suite at 0.1.0 — 74 cases

> Historical record for the published 0.1.0 revision. The suite is now **204** cases; see §9.

```
$ make check
python3 tests/test_check_postproduction.py
  examples/timeline.example.tsv passes structure
  examples/units.example.tsv passes structure
  examples/review-sheet.example.md passes structure
  timeline: wrong final arithmetic is rejected
  timeline: clip/source length mismatch is rejected
  timeline: overlapping clip ranges are rejected
  timeline: unparsable speed/freeze is rejected
  timeline: missing columns are rejected
  units: empty stated_reason is rejected
  units: hindsight reason_source is flagged as backfill
  units: illegal coverage value is rejected
  units: coverage_gap warns without failing
  sheet: missing required section is rejected
  sheet: missing `unknown` escape hatch is rejected
  ready: complete sheet + probed MP4 passes
  ready: missing --final-mp4 is not ready
  ready: leftover placeholder is not ready
  ready: unknown misreport count is not ready
  ready: duration outside tolerance is not ready
  ready: declared file != probed file is not ready
  ready: double-option verdict is not a pass
  ready: negative --tol is a usage error

  22 passed, 0 failed

python3 tests/test_build_sample.py
  ok  test_overlong_is_rejected（超长段被拒，且没有产出成片）
  ok  test_overlong_can_be_fixed_by_registered_freeze（定格是合法出路，窗口=源+定格）
  ok  test_normal_length_mapping（窗口/字幕/总长映射正确，音视频等长）
  ok  test_bad_header_is_rejected（少一列不会静默错位）

  全部通过（4 项）

python3 tests/test_tts_guarantees.py
  ok  G2 empty audio is a failure
  ok  G2 no output file left behind
  ok  G2 no success sidecar written
  ok  G3 unmeasurable duration is a failure
  ok  G3 half-written file was deleted
  ok  G3 no success sidecar written
  ok  happy path measured a duration
  ok  happy path sent the requested voice
  ok  happy path recorded no rewrite
  ok  sidecar was written
  ok  the wire carried no voice when none was requested
  ok  endpoint came from the environment
  ok  key was sent as a bearer header (from env, never a literal)
  ok  G1 a rewrite that came back anyway is flagged, not silently accepted
  ok  G1 the record keeps the text WE sent
  ok  G1 strict_no_rewrite turns the rewrite into a hard failure
  ok  G1 strict failure left no output file
  ok  G1b voice-design model sends optimize_text_preview explicitly false on the wire
  ok  G5 voice-design model is never sent a voice field
  ok  G6 missing TTS_BASE_URL is refused (no baked-in default)
  ok  a measured segment is cached

  48 passed, 0 failed

offline tests: OK
```

**74 cases, 0 failures (at 0.1.0).** The `ready` cases build a throwaway MP4 with `ffmpeg` lavfi sources in a
temp directory and delete it afterwards; the TTS cases use a throwaway loopback endpoint. Nothing is
written into the repository.

Negative cases are the point of this suite — a checker that only ever passes proves nothing. Every
gate the standard claims to enforce has at least one case that must fail:

| Enforced rule | Case that must fail |
|---|---|
| `len(final) == len(clip)/speed + freeze` | `timeline: wrong final arithmetic is rejected` |
| `len(clip) == len(source)` | `timeline: clip/source length mismatch is rejected` |
| No overlapping ranges within an asset | `timeline: overlapping clip ranges are rejected` |
| All required columns present | `timeline: missing columns are rejected` |
| Missing "reason at the time" must be explicit | `units: empty stated_reason is rejected` |
| No hindsight backfill into `stated_reason` | `units: hindsight reason_source is flagged as backfill` |
| `coverage` vocabulary is closed | `units: illegal coverage value is rejected` |
| Review sheet sections complete | `sheet: missing required section is rejected` |
| `unknown` escape hatch documented | `sheet: missing 'unknown' escape hatch is rejected` |
| No placeholders at delivery | `ready: leftover placeholder is not ready` |
| `unknown` ≠ pass | `ready: unknown misreport count is not ready` |
| Probed media must match declared duration | `ready: duration outside tolerance is not ready` |
| Review A ⇒ verify A | `ready: declared file != probed file is not ready` |
| Double options are not a verdict | `ready: double-option verdict is not a pass` |
| Tolerance must be a finite non-negative number | `ready: negative --tol is a usage error` |
| Narration must fit its picture window | `test_overlong_is_rejected` |
| The EDL header must match exactly | `test_bad_header_is_rejected` |
| Empty audio is not a success | `G2 empty audio is a failure` |
| Unmeasurable duration is not a success | `G3 unmeasurable duration is a failure` |
| A provider rewrite is not silently accepted | `G1 a rewrite that came back anyway is flagged…` |
| `--strict-no-rewrite` must refuse, not warn | `G1 strict_no_rewrite turns the rewrite into a hard failure` |
| No endpoint ⇒ refuse (no baked-in default) | `G6 missing TTS_BASE_URL is refused` |

## 2. Examples (structure semantics)

```
$ make check-examples
[timeline] examples/timeline.example.tsv  检查 3 项
  语义: structure-valid only: 字段/算术/章节齐全；不代表审片通过，也不代表成片可用
结果：结构通过（≠审片通过）
[units] examples/units.example.tsv  检查 6 项
  WARN  ev-006: 标记 coverage_gap，该单元不得进入成片解说
结果：结构通过（≠审片通过）
[sheet] examples/review-sheet.example.md  检查 1 项
结果：结构通过（≠审片通过）
```

The warning is expected and correct: `ev-006` is a deliberate `coverage_gap` fixture (present in the
log, absent from the footage), and the checker's job is to say that unit must not enter the cut.

`examples/review-sheet.example.md` is intentionally **not** `ready`: its misreport counts are
`unknown`, which means unverified, which is not a pass.

## 3. Recorder: compile + offline checks

Compiled from source with the shipped `build.sh`. Not a signed binary; built ad-hoc, and the build
output is gitignored.

```
$ make recorder-offline
tools/gamerec/build.sh
编译 main.swift → tools/gamerec/build/GameAVRec.app/Contents/MacOS/GameAVRec
main.swift:795:32: warning: expression implicitly coerced from 'Any?' to 'Any'
Identifier=com.example.gameavrec
Format=app bundle with Mach-O thin (arm64)
Signature=adhoc
OK: tools/gamerec/build/GameAVRec.app

bash tools/gamerec/tests/regression.sh
  ✓ O1 未指定目标 → 用法错误（退出码 64）
  ✓ O1 且不产出文件
  ✓ O1 报错写明必须显式指定目标
  ✓ O2 --help 退出码 0
  ✓ O2 --help 写明不内置默认目标
  ✓ O3 数字静音判失败（退出码 2）
  ✓ O3 报告逐秒覆盖范围
  ✓ O3 打印逐秒有声桶占比
  ✓ O4 1 帧视频被判失败（退出码 2，实际帧数 1）
  ✓ O4 失败原因写明只有 1 帧
  ✓ O5 纯音频用 --expect audio 通过
  ✓ O5 同一文件 --expect av 失败（模式不匹配）
  ✓ O5 失败原因写明没有视频轨
  ✓ O6 无 GAME_BUNDLE_ID 时 preflight 拒绝（退出码 64）
  ✓ O6 拒绝原因写明缺少录制目标
  ✓ O7 --out 已存在时默认拒绝覆盖（退出码 3）
  ✓ O7 原文件字节未被改动
  ✓ O7 报错写明退出码 3 与两条出路
  ✓ O8 --log 已存在时默认拒绝覆盖（退出码 3）
  ✓ O8 且不产出 --out 文件

  权限组：未设置 GAME_BUNDLE_ID → SKIP（P1–P7，共 7 项）
  结果：通过 20，失败 0，跳过 7
```

**20 checks passed, 7 skipped, 0 failed.** The one compiler warning is pre-existing in the original
source (an `Any?` → `Any` coercion) and was left alone rather than silenced.

Note where the two **no-overwrite** gates now sit. In `main.swift` the conflict check runs *before*
`let rec = Recorder(...)` and before `Task { start() }`, so it is reachable without Screen Recording
permission — a fail-closed safety property should not need a granted permission just to be testable.
`O7`/`O8` therefore run in the offline group. The same reasoning applies to the explicit-target gate
(`O1`), which throws a usage error before `SCShareableContent` is ever touched.

The skipped group requires Screen Recording permission, a display, and an app that actually emits
audio — i.e. real capture. It was **not** run for this revision, was not shortened, and does not
report a pass it did not earn. Everything the recorder claims about real capture in the README comes
from the author's earlier runs recorded in `references/spire-checklist.md`, not from this export run.

## 4. TTS adapter: the guarantees are proven, not asserted

`tests/test_tts_guarantees.py` stands up a fake provider on loopback, so the adapter's real wire
behaviour and failure modes are exercised with no external network, credential or vendor account.

| Guarantee | Asserted by |
|---|---|
| The script is never rewritten; a provider-side rewrite is flagged | `G1 … flagged, not silently accepted`, `G1 the record keeps the text WE sent` |
| `--strict-no-rewrite` refuses instead of warning | `G1 strict_no_rewrite turns the rewrite into a hard failure`, `G1 strict failure left no output file` |
| No rewrite is *requested* on the wire | `G1b … sends optimize_text_preview explicitly false on the wire` |
| Empty audio fails and leaves nothing behind | `G2 …` ×3 (raises, no file, no sidecar) |
| Unmeasurable duration fails and deletes the partial file | `G3 …` ×3 |
| Voice sent only when requested; design model never gets one | `happy path sent the requested voice`, `the wire carried no voice when none was requested`, `G5 …` |
| Endpoint is required; key comes from env and goes out as a bearer header | `endpoint came from the environment`, `key was sent as a bearer header (from env, never a literal)`, `G6 …` |
| Only a genuinely measured success is cached | `a measured segment is cached` |
| A leniently accepted take is not replayed to a strict run | `C1 a lenient take is NOT replayed to a strict run` |
| A different endpoint is not served from another provider's cache | `C2 switching endpoint re-synthesizes instead of replaying foreign audio` |
| The endpoint is fingerprinted, never stored; the credential is never stored | `C3 sidecar carries an endpoint fingerprint`, `C3 sidecar never stores the endpoint URL`, `C3 sidecar never stores the credential` |
| Provider error bodies are redacted and the key cannot surface | `R1 the key-shaped secret is redacted from the error`, `R1 redaction is visible in the message` |
| A matching cache key with tampered audio is NOT reused | `C4-N1 tampered/truncated audio is NOT served from cache` |
| A non-finite / zero / negative / non-numeric `duration_s` is NOT reused | `C4-N2 invalid duration_s (…) is NOT served from cache` ×6 |
| `TTS_MODEL` / `TTS_VOICE` from `TTS_ENV_FILE` reach the runtime | `C5 TTS_MODEL from TTS_ENV_FILE reaches the runtime`, `C5 TTS_VOICE …` |
| Redaction over-matches without destroying benign content | `R2 benign text survives redaction`, `R2 a bare bearer token is redacted`, `R2 a single-quoted key pair is redacted`, `R2 a credential-bearing query parameter is redacted` |

Two defects were found and fixed **in the test harness itself** while writing this (a `SystemExit`
escaping an `except Exception`, and an env var popped before a later case needed it) — recorded here
because a harness that swallows its own bugs would make every assertion above worthless.

**This is not a cross-vendor guarantee.** The adapter targets one wire shape; only that shape is
tested. Nothing here supports a claim that it works with any other provider.

## 5. Skill package shape

Checked with the author's `sunny-meta-skill` package checker (an external, optional tool — the
repository does not depend on it):

```
$ python3 ~/.agents/skills/sunny-meta-skill/scripts/check_sunny_skill.py skills/gameplay-postproduction
OK: skills/gameplay-postproduction passes Sunny skill package check as contract
```

`contract` is the correct class for this shape: a skill that owns a standard, templates, a
deterministic checker and a trigger-case eval. The installable package root stays clean (only
`SKILL.md`, `references/`, `templates/`, `evals/`, `scripts/`) so it validates as an installable unit;
the repository-level files (`README`, `LICENSE`, `Makefile`, …) live outside it.

## 6. Static scans on the tracked tree

| Scan | Method | Result |
|---|---|---|
| Personal absolute paths (`/Users/...`, `/Volumes/...`, project dirs) | `grep -rniE` over the tree | **0 matches** |
| Vendor / non-public company names (private endpoints, internal gateways) | `grep -rniE` | **0 matches** |
| Retired branded identifiers (old binary name, old bundle id, personal handle) | `grep -rniE` | **0 matches** — the extraction notes describe the *class* of change without reproducing the strings |
| Credential-shaped strings (`gho_`, `ghp_`, `github_pat_`, `AKIA`, `xox[baprs]-`, `sk-`+20, `-----BEGIN`, bearer tokens) | `grep -rniE` | **0 matches** |
| Real email addresses of any kind (including the personal global git address) | `grep -rniE` for `user@host` shapes | **0 matches** |
| GitHub login handles in file **contents** | `grep -rniE` | **Expected in one place only:** the `git clone` URL in `README.md` / `README.zh-CN.md`. It is the public account that hosts this repository, so naming it is what makes the copy-paste command work — it is not private information. No credential, email address or private identifier accompanies it. |
| Real Steam IDs / session IDs / machine identifiers | `grep -rniE` | **0 matches** |
| Symlinks (including any that could escape the tree) | `find -type l` | **0 found** |
| Binary / non-UTF-8 files | `file --mime-encoding` over every file | **0 found** (all text) |
| Files > 256 KB | `find -size +256k` | **1**, and it is the compiled recorder in the gitignored `build/` directory |
| Real media (`.mp4/.wav/.srt/...`) | manual + `.gitignore` backstop | **0 tracked**; all `examples/` fixtures are hand-written synthetic text |

Non-zero matches inspected and **kept deliberately**:

- `LICENSE` — the copyright holder's name, `Sunny`. Required for an MIT grant. The redundant account
  handle was dropped, so no GitHub login appears in any tracked file. The login is still visible in
  the repository URL and in the commit author, which is inherent to publishing on that account, but
  it is not written into the file contents.
- `README.md` / `README.zh-CN.md` — the `git clone` URL names the public GitHub account that hosts
  this repository. Deliberate: a clone command that does not name a repository is not runnable, and
  this is a public account, not a secret. No credential or email address appears alongside it.
- `docs/verification-log.md` — this file quotes the scan patterns themselves, so it matches its own
  scan. Self-referential and expected.

### Limits of these scans — stated, not glossed

- The checks are **pattern-based**, not a general-purpose secret scanner. An unusual secret format,
  a secret split across lines, or one embedded in a binary blob would not be caught.
- They cover **text files that are tracked by git**. Ignored or untracked files on disk are outside
  their view.
- They say **nothing about media content**. A recording that shows a password is invisible to a text
  scan; that is what the standard's "close or move off-screen anything sensitive before recording"
  rule and the `G1` review item exist for.
- They are not a substitute for a human reading the diff before publishing.

## 7. Commit identity

The published commit must not carry an employer or personal global git identity. Neither is used, and
**no global git config was modified** to achieve this. The concrete addresses are deliberately **not
reproduced in this file**, because this file is tracked and therefore public; identity values belong
in the commit metadata, not in the tree.

| | |
|---|---|
| Global config | Inspected read-only, **left untouched**. It held a non-GitHub personal address, which appears **0 times** in the commit metadata and **0 times** in any tracked file. Its value is not reproduced here. |
| Repository-local config | Set with `git config --local` only — never `--global`, never `--system`. |
| Derivation | `gh api user --jq '.id, .login'`, composed as `<id>+<login>@users.noreply.github.com`. This is GitHub's documented ID-based noreply form. |
| Supporting evidence | The account reports `email == null`, i.e. GitHub's "keep my email addresses private" setting is enabled — so the noreply form is the correct choice, not a workaround. |
| Commit author **and** committer | Both reset to the derived noreply address, not just the author. The exact value is visible in the commit metadata (`git log --format='%an <%ae>%n%cn <%ce>'`). |
| Leak check | No personal email address and no account handle appears in the **contents** of any tracked file. Email-shaped and handle-shaped patterns are covered by the scans in §6. |

Verifying this claim from a clone is one command:

```bash
git log -1 --format='%an <%ae> | committer %cn <%ce>'
```

## 8. Fresh-snapshot verification (0.1.0 record)

Local runs can be flattered by leftovers — an untracked file, a stale build directory, or an exported
environment variable — so the README entry points were re-run on **two independent pristine
snapshots**, with the relevant environment variables unset.

**Snapshot A — `git archive HEAD | tar -x`.** An archive contains only committed blobs: no `.git`, no
untracked files, no ignored files. Verified before running: 41 files present, **no**
`tools/gamerec/build/`, **no** `__pycache__`, **no** `.app`/`.mp4`/`.wav`/`.pyc`.

**Snapshot B — `git clone` of the local repository**, with `GAME_BUNDLE_ID`, `TTS_*`, `SRC_VIDEO` and
`SRC_CROP` all unset (confirmed zero relevant variables set). Verified before running: 41 files,
`git status --porcelain` empty, and `--ignored` reporting nothing present.

| Entry point (exactly as the README documents it) | Snapshot A | Snapshot B |
|---|---|---|
| `make help` | exit 0 | exit 0 |
| `make check` (32 + 112 + 9 + 51 cases) | all pass, exit 0 | `offline tests: OK`, exit 0 |
| `make check-examples` | exit 0 | exit 0 |
| `make recorder-offline` (compiles the Swift recorder from scratch) | builds, **20 passed / 0 failed / 7 skipped**, exit 0 | same |
| `tests/run_offline_tests.sh` | `离线测试全部通过`, exit 0 | same |
| `check_postproduction.py timeline/units/sheet` on `examples/` | exit 0 each | exit 0 each |
| Rebuild after `rm -rf tools/gamerec/build` | 20 passed / 0 failed / 7 skipped | — |

Because the archive snapshot passes everything with **no ignored files present at all**, the results
cannot be an artifact of this machine's untracked state.

**Negative control — the tests are not passing vacuously.** With `ffprobe` shadowed by a
always-failing shim on `PATH`, `test_tts_guarantees.py` exits **non-zero**. The suite therefore
genuinely depends on the tool it claims to use, rather than catching an exception and reporting
success.

## 9. Workflow hardening (2026-09-24) — what was actually run

The suite grew from 74 to **204 offline cases**; the numbers below are the raw tail of each run.

```
$ python3 tests/test_check_postproduction.py
  ... 32 passed, 0 failed
$ python3 tests/test_timeline_audit.py
  ... 112 passed, 0 failed
$ python3 tests/test_build_sample.py
  全部通过（9 项）
$ python3 tests/test_tts_guarantees.py
  ... 51 passed, 0 failed
```

**Red-first evidence.** The five defective timelines now under `tests/fixtures/` were first run
through the *unmodified* checker at the pre-change HEAD (`6e88382`). All five passed it with
**zero errors** — `check_postproduction.py timeline` returned `rc=0` for the phase-mismatch,
unlabelled-hold, hold-outside-visible-window, overlong-narration and mixed-version fixtures — and
there was no mode able to request `preflight` or `audit` at all. That gap is asserted inside the
suite itself (`structure mode still accepts the … fixture (the gap this suite closes)`), so a future
change that silently reopens it fails the tests. The raw before output is mirrored with this
revision's review packet.

**Negative controls.** Every new rule has a case that must fail: phase claimed ahead of the picture;
a line anchored to a different event at the same phase; an anchor interval outside the shot; a
cross-turn shot whose line does not cover the span; NaN and negative anchor intervals; `freeze > 0`
with no mark; reward anchor outside `visible_window`; narration longer than its window; two
`subtitle_source` values; a same-cue-count subtitle that was re-worded, or re-timed out of its
segment; a replaced audio track and an older export of the same length (content-hash binding); a
missing final cut; each media parameter omitted in turn; a missing, stale, self-contradicting or
non-finite silence ledger; `--tol nan`, `--tol -1`, `--silence-threshold nan`, `--silence-threshold
-5`, `--min-fps inf` as usage errors; a tampered preflight digest (stale manifest); a removed source
file; and a non-media file that cannot be probed (`status: undetermined`).

**Second review round (same day).** An independent read-back found concrete defects that are now
fixed and covered: silence was computed from picture windows instead of the real audio spans; the
per-cue subtitle text/timing was not compared in the revision the reviewer read; `--audio` was not
checked for an actual audio stream; `ready` neither received the final cut for a same-length
comparison nor validated `--silence-threshold`, and it dropped the audit's warnings/`unverified`;
`-show_entries` used `&` instead of `:` and silently lost the `format` block; `measure_audio_signal`
ignored a non-zero `ffmpeg` exit; `source_path` was stored as given, so changing cwd could misjudge
it; the global phase order was treated as a timeline; and four matching digests did not prove the
artifacts came from one production. Each has a regression case, including a receipt bound to a
different timeline, a 1 s line on a 66 s shot (65 s of silence, ratio 1/66), an `--audio` file with
no audio stream, and a 5 s sheet+MP4 paired with a valid 66 s timeline/audio.

**Round 3 (production path).** The recipient of round 3's review was the authoring path: the
first build could not know the output digests, so the old flow implied a second render; the receipt
copied the preflight ledger instead of recording what was actually read; silence still summed
per-segment spans rather than the merged union; a one-cue subtitle only had to sit inside its span;
and `hold_mark` was never actually applied to the picture. Each is fixed and covered:
`test_first_build_closes_the_loop_in_one_invocation` (clean dir, placeholders in the recipe, one
invocation, adopted timeline with real digests), `A/B source mismatch` and `recipe/EDL source-range
and offset mismatches` failing before rendering, the squeezed-subtitle case failing on the default
path, and the freeze case either annotated for real (when `drawtext` exists) or explicitly
fail-closed with `DRAFT.txt` (this machine has no `drawtext`, and the suite asserts that path).

**The authoring path is covered too.** `test_build_sample.py` builds a real cut through
`build_sample.sh`: without the audit inputs the output is stamped `DRAFT.txt` and the script says so
on stderr; with a defective timeline the script exits non-zero and still stamps the draft; with a
timeline bound to the produced artifacts the audit passes, the report is written and the draft mark
is cleared.

**Real ffmpeg, no committed binaries.** `tests/test_timeline_audit.py` generates 20 s / 6 s video and
a 66 s audio track with lavfi at test time, runs the real `preflight` over them, then audits the
fixtures against that ledger. Nothing binary is committed, no model is called, and no network egress
occurs.

**Not claimed.** The audit does not watch the picture or listen to the audio: perceived delivery,
intonation and factual semantics remain human/source-frame checks, and the report carries them in
`not_a_verdict_on`. The audit's own pass is a statement about the timeline, not about the cut.

## 10. Publish boundary

This revision performed **local `git init` + `git commit` only**. No remote was added or configured,
no `git push` was run, and nothing was created on any hosting provider. The full staged tree, the
tracked-file manifest, and a relative-path mirror of the tree are provided for independent review.

## Known limitations carried into this revision

- Only one game has a `<game>-checklist.md`; every other game is unadapted.
- Audio/video **sync** is unverified by the tooling, and the verifier says so in its own output.
- Minimized / hidden / locked screen, muted output, mid-capture restart, multi-display, and non-macOS
  recording are untested.
- Whether director notes change TTS delivery is **not demonstrated**: one small 2×2 sample (n=1 per
  cell, 2 takes each) showed no *stable* prompt effect and no significance test was run. "Not
  demonstrated" is not "disproven", and it supports no conclusion about the provider's capability.
- The TTS adapter is **not** a cross-vendor abstraction. It targets one wire shape; a different
  provider needs a different adapter, and identical route names do not imply identical behaviour.
- `ready` mode inherits `sheet` mode's requirement that the literal token `unknown` appear somewhere
  in the sheet. A fully verified sheet therefore still needs that line. Behaviour is **unchanged**
  from the installed skill so the exported checker is byte-identical to the artifact it describes;
  it is recorded in `CHANGELOG.md` rather than silently patched.
- The adopted-timeline audit (`check_timeline_audit.py audit`) judges **the timeline**, not the cut.
  It does not watch the picture or listen to the audio, so perceived delivery, intonation, and factual
  semantics stay human/source-frame checks; the report lists them under `not_a_verdict_on`. A green
  audit is not "the cut is good".
- The audit's phase rule is ordinal (`setup < battle < reward < map < shop < rest < event < other`).
  `other` sorts last, so a line whose phase is genuinely unknown should be left out of `claim_phase`
  rather than guessed; this ordering is a deliberate simplification, not a model of every game's flow.
- The silence threshold (default 20 s) is a *policy* number chosen for one project's pace. It is a
  CLI flag, not a universal law, and changing it changes which stretches need justifying.
