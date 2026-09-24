# Architecture

## Shape

This repository is a **process contract with a deterministic checker**, flanked by three small
independent tools. It is not a framework, not an application, and not an editor.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ skills/gameplay-postproduction/       the SOP: what must be true              │
│   SKILL.md                            entry point, boundaries, output contract│
│   references/postproduction-standard.md   canonical rules (Chinese)           │
│   references/routing.md               which role does which job               │
│   references/voice-design.md          the sound layer: what can/can't be asked│
│   references/spire-checklist.md       ONE worked game example (not general)   │
│   templates/*.md                      the artifact shapes                     │
│   scripts/check_postproduction.py     DETERMINISTIC checker — no model calls  │
│   scripts/check_timeline_audit.py     preflight + timeline semantics — no model│
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ validates artifacts from                        ▲ validates
        │                                                  │
┌───────┴───────────────┐   ┌──────────────────────┐   ┌──┴────────────────────┐
│ tools/gamerec/        │   │ tools/voice/         │   │ your editor / your    │
│ capture target app's  │   │ tts_adapter.py       │   │ video-understanding   │
│ audio + picture       │   │ build_sample.sh      │   │ channel               │
│ (macOS, Swift)        │   │ burn_subs.py         │   │ (NOT bundled)         │
└───────────────────────┘   └──────────────────────┘   └───────────────────────┘
```

The pieces are deliberately decoupled: the checker never calls a model, the recorder never edits, and
the assembler never transcribes. Substituting any one of them should not require touching the others.

## Stages

Six **stage names** — a vocabulary for talking about progress, not an API or a state machine:

```
ingest → analyze → commentary_plan → edit_plan → review → repair
```

Nothing requires running all six. A single review pass uses `review` alone. The names exist so that
"we're stuck in analyze" is a sentence with a shared meaning.

## Data flow

```
 recording
     │  ffprobe
     ▼
 asset register ──────────────► (asset_id, sha256, size, duration, fps, codec, resolution, has_audio)
     │
     │  your model channel reads the video
     ▼
 CANDIDATE events ────────────► explicitly labelled as candidates, never as facts
     │
     │  verify against source frames  ← the step that cannot be skipped
     ▼
 commentary units ────────────► state / action / stated_reason / retrospective_commentary /
     │                          outcome / coverage   (one row per occurrence)
     │
     ▼
 unified timeline ────────────► asset_id | event_id | source_range | clip_range | final_range |
     │                          speed/freeze | narration_text | audio_duration_s | subtitle_source
     │
     ├──► your editor renders ──► exported MP4
     │                                 │
     │                                 ▼
     │                            review sheet ──► (local repair, then re-review)
     │                                 │
     └──► checker validates ───────────┘
```

## The three invariants, and where they are enforced

| Invariant | Enforced by | Fails how |
|---|---|---|
| **One time base.** `len(final) == len(clip)/speed + freeze`; `len(clip) == len(source)`; clip and final ranges within an asset do not overlap | `check_postproduction.py timeline` | exit `1`, per-row error naming the row and the expected value |
| **Three fields never backfill each other.** `stated_reason` / `retrospective_commentary` / `outcome` are separate; an explicitly hindsight-labelled `stated_reason` is rejected | `check_postproduction.py units` | exit `1`, error identifying the event |
| **Verification gates export.** Narration that cannot fit its picture window fails before rendering | `build_sample.sh` precheck (+ a second in-render clamp check and an A/V equal-length check) | exit `3`, no `final.mp4`, prints the three legal fixes |
| **A line is anchored to what is actually on screen.** The declared `(asset, event, interval)` is cross-checked against that row's real shot span: a claim about a later phase, a line anchored to a *different* event at the same phase, or a referenced interval outside the shot is rejected. A cross-turn shot must declare its turns (`event_id` = `a+b`) and the line must cover the whole span | `check_timeline_audit.py audit` (enforced by the `ready` gate) | exit `1`, error naming the event, the claimed phase/event/interval and the on-screen span |
| **Human annotations are not machine proof.** `anchor_*`, `evidence`, `hold_mark` and `visible_window` are declarations; the tool only checks they are self-consistent with the shot | report field `human_annotations_not_machine_verified`, and the `unverified` list | not a failure — an explicit statement of the boundary |
| **A registered hold is a labelled hold.** `freeze > 0` needs an on-screen mark carrying the held source timecode; a reward hold's anchor must fall inside the candidate-visible window | `check_timeline_audit.py audit` | exit `1`, error naming the hold and the window it misses |
| **Long silence is justified per stretch.** Every narration gap ≥ threshold needs one ledger row (`keep` / `cut` / `narration_added` + a substantive reason); a stale row is an error | `check_timeline_audit.py audit` + `templates/silence-ledger.md` | exit `1`, listing the unjustified gap or the stale row |
| **One version, bound by content — not by counts.** The timeline declares `subtitle_sha256` / `audio_sha256` / `final_sha256`; the audit checks those digests, the per-cue subtitle text *and* its timing against each row's window, the audio duration, and the cut's duration and audio stream. Same segment count with re-worded or re-timed subtitles, an older audio track or an older export all fail | `check_timeline_audit.py audit` | exit `1`, naming which binding broke |
| **The source ledger is content-addressed.** The preflight ledger's sha256/size must still match the file on disk | `check_timeline_audit.py audit` | exit `1`; a changed source is a *stale manifest*, not a warning |
| **Nothing skips the gate.** `ready` always re-runs the audit in-process (no "here is my audit JSON" input) and requires every media parameter; `build_sample.sh` runs the same audit and otherwise stamps its output `DRAFT.txt` | `check_postproduction.py ready`, `tools/voice/build_sample.sh` | exit `1` / exit `3`, plus a `DRAFT.txt` next to the cut |
| **NaN / inf / negative / reversed never pass.** Every interval, duration and tolerance is validated | all three checkers | exit `1` (payload) or `2` (usage) |
| **Input state is probed, not assumed.** Audio `present`/`silent`/`absent` and sampling `normal`/`sparse` come from real ffprobe/ffmpeg; an undecidable probe exits non-zero | `check_timeline_audit.py preflight` | exit `1` with `status: undetermined` |

## Modes with different semantics

The checkers' modes are not interchangeable, and conflating them is the most likely misuse:

| Mode | Input | Exit 0 means |
|---|---|---|
| `timeline` / `units` / `sheet` | TSV (TAB or `\|`) / TSV / Markdown | **structure is valid only** — fields present, arithmetic consistent, sections complete. A blank template passes. This says nothing about whether the cut is watchable. |
| `preflight` | source asset paths (real ffprobe/ffmpeg) | **the input state is explicit** — audio track presence and whether it carries a signal, measured frame density, digest and size. An undecidable probe is `undetermined` and exits non-zero. |
| `audit` | timeline TSV + preflight ledger + silence ledger + subtitle + audio + final cut | **the adopted timeline is self-consistent** — declared anchors cross-checked against each shot's real span, phase order, labelled holds inside their visible window, narration fits its window, per-gap silence justification, content-level version binding (subtitle text/timing + three digests), no stale manifest. |
| `ready` | Markdown + `--final-mp4` + the whole audit input set | **delivery-ready**: every A/E item answered yes *with evidence*, no placeholders, a unique `通过` conclusion, no unresolved issues, the final media's tracks and duration independently probed and consistent with what the sheet declares, **and the adopted timeline passing the semantic audit**. |

`ready` **requires** `--final-mp4`, `--timeline`, `--preflight`, `--silence-ledger`, `--subtitle` and
`--audio`; omitting any of them reports the state as `unverified`. It also **re-runs the audit
itself** — there is deliberately no option to hand it a previously computed report, so a stale or
fabricated JSON cannot stand in for the live check. The audit is therefore not a checker nobody
calls: it is on both the delivery path and the authoring path (`build_sample.sh`, which marks its
output `DRAFT.txt` when the audit inputs are absent).

`ready` and `audit` do **not** watch the video, listen to the audio, inspect frames, or verify sync.
They answer exactly one question: *may this sheet be treated as reviewed?* They never replace human
review, and the audit report carries an explicit `not_a_verdict_on` list (narration ratio, decode
success, perceived delivery, factual semantics) so a green run cannot be read as "the content is
good".

The `unknown` token is load-bearing across the whole design: it is a legitimate value meaning
**unverified**, and `ready` refuses to treat it as a pass. Without independent ground truth you cannot
claim zero misses, so the honest entry is `unknown`, not `0`.

## Interaction example

```
Request: "把录屏整理成后期流程，给我素材登记和解说准备"

1. ffprobe the recording
     → asset row: sha256, size, duration, fps, codec, resolution, has_audio
2. model channel reads the video
     → candidate events. NOT facts, and NOT cut points.
3. every load-bearing claim verified against source frames
     → win/loss, card picked, damage, HP, gold
     → with no footage for a unit: coverage_gap, and that unit stays OUT of the narration
     → with no ground truth at all: unknown
4. commentary units written
     → five elements (situation/candidates/choice/reason/result) are INTERNAL prep,
       not a script; the narration distils the key trade-off instead of reading them out
5. unified timeline built
     → one file shared by video, voice and subtitles
6. checker run
     → structure gate now; `preflight` before writing narration; `audit` on the adopted timeline;
       `ready` gate after export, against the real MP4 and the whole audit input set
7. review sheet written
     → per-item evidence; unresolved issues keep it not-ready
```

## Why the separation from evaluation is structural

Postproduction is a **describe-what-happened** activity; evaluation is a **judge-how-well** activity.
If they share a model channel, a fixture, or a feedback path, the evaluation stops measuring the
agent and starts measuring the pipeline. So the standard makes it a hard rule (§0): postproduction
outputs are candidates, they must not flow back into any evaluated behaviour, and this project does
not specify tools for the evaluation side at all.

The related failure mode is **in-frame answer leakage**. A two-window capture (game + report panel)
puts the answer inside the picture, which both leaks it and makes "did the model understand the
audio?" unanswerable. Such a capture is allowed only if the leak is declared in the review sheet —
and the checker has a dedicated item for exactly that (`G2`, the one item where `N/A` is permitted).
