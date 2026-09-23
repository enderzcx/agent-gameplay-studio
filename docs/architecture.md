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

## Two modes with different semantics

The checker's four modes are not interchangeable, and conflating them is the most likely misuse:

| Mode | Input | Exit 0 means |
|---|---|---|
| `timeline` / `units` / `sheet` | TSV (TAB or `\|`) / TSV / Markdown | **structure is valid only** — fields present, arithmetic consistent, sections complete. A blank template passes. This says nothing about whether the cut is watchable. |
| `ready` | Markdown + `--final-mp4` | **delivery-ready**: every A/E item answered yes *with evidence*, no placeholders, a unique `通过` conclusion, no unresolved issues, and the final media's tracks and duration independently probed and consistent with what the sheet declares. |

`ready` does **not** watch the video, inspect frames, or verify sync. It answers exactly one
question: *may this sheet be treated as reviewed?* It never replaces human review.

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
     → structure gate now; `ready` gate after export, against the real MP4
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
