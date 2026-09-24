# Agent Gameplay Studio

**Turn a gameplay recording into a cut you can actually review.** Capture the game's own audio and
picture, work out what happened, write the decision commentary, voice it, assemble the edit, then
check the finished file against a written standard before you ship it.

It is a **skill-driven toolkit**: the skill carries the process, the templates and the gates, and a
few small tools do the mechanical work. You drive it as a skill plus some scripts, and you bring your
own editor and model channel. Nothing is bundled behind it — no model, no key, no service — so the
parts that need judgement stay yours, and the parts that must not be fudged are checked by machine.

### What you get

- **A postproduction SOP** (`skills/gameplay-postproduction/`) covering
  ingest → analyze → commentary plan → edit plan → review → repair, with a canonical standard, four
  artifact templates and one worked game checklist.
- **Two deterministic checkers** — `check_postproduction.py` (structure + the `ready` gate) and
  `check_timeline_audit.py` (input preflight + adopted-timeline semantics), both pure standard
  library, zero model calls. Between them they check the timeline/units/sheet structurally, probe
  every source for audio and sampling state, audit the adopted timeline for phase anchors, labelled
  holds, per-gap silence justification, subtitle/audio version binding and stale source manifests,
  and run a strict `ready` gate against a real exported MP4.
- **A macOS recorder** (`tools/gamerec/`) that captures one target app's audio *and* picture, filtered
  at the app level, and compiles from source — no signed binary is shipped.
- **Voice and assembly tools** (`tools/voice/`) — an optional TTS adapter, an EDL assembler that
  refuses to export a mistimed cut, and a subtitle burner for ffmpeg builds without libass.
- **213 offline tests** that need no network, no keys and no game.

New here? Go to [Quick start](#quick-start-shortest-path-that-actually-works), then
[Using the skill](#using-the-skill), then read
[what is verified and what is not](#what-is-verified-and-what-is-not) before trusting anything.

Where this stops, stated once up front: it is deliberately **not** a GUI application, **not** a
one-click render, and **not** a claim of fully automatic human-quality output. It is tooling for
people who want the process explicit and the result checkable. Every boundary and unverified edge is
itemised in the status table below and in [Honest boundaries](#honest-boundaries) rather than being
buried in caveats here.

This project is separate from `agent-gamebench`, an agent evaluation project — see
[Non-cheating and separation from evaluation](#non-cheating-and-separation-from-evaluation).

---

## Quick start (shortest path that actually works)

Requires **Python 3.9+** — verified, not assumed: the entire offline suite was run on Python
**3.9.6**. `ffmpeg`/`ffprobe` are needed for media steps and for `ready` mode; burning subtitles
additionally needs an `ffmpeg` built with **libass** (`ffmpeg -filters | grep ass`). The checkers
themselves are pure standard library.

```bash
git clone https://github.com/enderzcx/agent-gameplay-studio.git
cd agent-gameplay-studio

# 0) See what this can do, then run every offline check. No network, no keys, no game.
make help
make check          # 4 offline suites: checker + timeline audit + EDL assembly + TTS guarantees

# 1) The deterministic checkers work on your own artifacts right away.
C=skills/gameplay-postproduction/scripts/check_postproduction.py
A=skills/gameplay-postproduction/scripts/check_timeline_audit.py
python3 "$C" timeline examples/timeline.example.tsv      # structure only
python3 "$C" units    examples/units.example.tsv
python3 "$C" sheet    examples/review-sheet.example.md
# exit 0 = structurally valid. For "timeline"/"units"/"sheet" that does NOT mean the cut is good.

# Preflight your sources (real ffprobe/ffmpeg): audio present/silent/absent, sampling sparse or not.
python3 "$A" preflight --json --out preflight.json rec-example-01=/abs/path/to/recording.mp4

# 2) Read the standard, then fill the templates for your own recording.
#    references/postproduction-standard.md is canonical.
```

The `ready` gate additionally probes a real exported MP4 **and requires the whole audit input set**
(timeline, preflight ledger, silence ledger, subtitle, audio), so the semantic audit cannot be
skipped on the delivery path:

```bash
python3 "$A" audit timeline.tsv --preflight preflight.json --silence-ledger gaps.tsv \
    --subtitle subs.srt --audio voice_master.wav --final-mp4 final.mp4 \
    --receipt produce-receipt.json --report-out audit-report.json
python3 "$C" ready review-sheet.md --final-mp4 /abs/path/to/final.mp4 \
    --timeline timeline.tsv --preflight preflight.json \
    --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav
```

`ready` = "this sheet can be treated as reviewed": every A/E item answered **yes with evidence**,
no leftover placeholders, a unique `通过` conclusion, no unresolved issues, and the final media's
tracks/duration independently probed and matching the declared duration. It **does not** watch the
video, check frames, or verify sync, and it never replaces human review.

---


## Using the skill

The skill is the part that makes this a workflow rather than a pile of scripts. It is a standard
Agent Skill package (`SKILL.md` + `references/` + `templates/` + `evals/` + `scripts/`), so it works
with any host that loads that shape.

```bash
# Run this from the repository root — the directory you just cloned into.
REPO="$(pwd)"
SKILL=gameplay-postproduction
# Point SKILL_HOME at your host's skill directory (~/.agents/skills, ~/.dsh/skills, …).
SKILL_HOME="${SKILL_HOME:-$HOME/.agents/skills}"
DEST="$SKILL_HOME/$SKILL"

# 1) create the parent directory
mkdir -p "$SKILL_HOME"
# 2) refuse if ANYTHING already sits at the destination — a directory, a file, or a symlink.
#    Without this check, `cp -R` into an existing directory would nest or merge instead of failing.
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  echo "refusing to overwrite existing: $DEST" >&2
else
  # 3) copy the package in
  cp -R "$REPO/skills/$SKILL" "$DEST"
fi
```

That is the only install path this project recommends, and it fails closed: it never overwrites and
never merges. Host skill directories differ (`~/.agents/skills`, `~/.dsh/skills`, …) — set
`SKILL_HOME` accordingly.

If your host can load a skill straight from a path, you can skip installation entirely:

```bash
REPO="$(pwd)"        # from the repository root
python3 "$REPO/skills/gameplay-postproduction/scripts/check_postproduction.py" --help
```

**This repository never installs anything for you, never overwrites an existing skill, and never
touches a global config.** Every step above is yours to run.

Once loaded, the skill drives the work and loads references on demand rather than all at once:

| You want | Read |
|---|---|
| The whole process and its rules | `references/postproduction-standard.md` (canonical) |
| The boundary with your video-analysis / editing capability, and how to swap it | `references/routing.md` |
| The sound layer: voice profile, pronunciation, what TTS control actually does | `references/voice-design.md` |
| Recording the game's own audio | `references/spire-checklist.md` §recording game audio |
| The artifact shapes to fill in | `templates/*.md` |

**Video understanding and editing are provided by your host, not by this package** — the roles are
replaceable and `references/routing.md` says so explicitly. This package contains no model, endpoint
or quota.

---

## Safety defaults that are not configurable away

Three fail-closed behaviours are baked into the tools and covered by always-on tests, because each one
prevents a silent, expensive mistake:

| Behaviour | Why |
|---|---|
| The recorder has **no default target** and **never falls back** to a different app | Capturing the wrong app yields a file that looks fine and is useless. A missing target is a usage error (`64`) raised *before* any permission prompt. |
| Outputs are **never overwritten by default** (`--out`, `--json`, `--log`, `--status`) | Re-running a capture must not destroy the previous evidence. Overwriting requires an explicit `--overwrite`; otherwise it exits `3` without touching a byte. |
| Recording **stops bounded**, never hangs | A 30 s startup bound and a shutdown deadline both end in a terminal state (`exit 4`) rather than an indefinite wait. |

These are verified offline, without permissions — a safety property should not need a granted
permission just to be testable.

---

## What is verified and what is not

Read this table before you trust anything. "Verified" means it ran on the author's machine and the
evidence is in this repo; "unverified" means nobody has shown it works.

| Area | Status | Basis |
|---|---|---|
| **Ready-made GUI / one-click render** | **Does not exist — by design** | This is a skill plus scripts. You bring the editor. |
| **Bundled model, key, endpoint or quota** | **None** | Installing this grants no model capability; the TTS adapter does nothing until you point it at your own endpoint. |
| Python 3.9 compatibility | **Verified** | The whole offline suite was run on Python **3.9.6** (`/usr/bin/python3`), not only on a newer interpreter. |
| Postproduction artifacts (timeline / units / review sheet) | **Verified — offline** | `tests/test_check_postproduction.py`: 32 cases, no network, no model |
| Deterministic checker (structure + `ready` gate) | **Verified — offline** | Same suite; `ready` independently probes the media with `ffprobe` and refuses to pass without the audit inputs |
| Input preflight (audio `present`/`silent`/`absent`, sampling `normal`/`sparse`) | **Verified — offline** | `tests/test_timeline_audit.py`: real ffmpeg/ffprobe on lavfi-generated media, including the `undetermined` failure path |
| Adopted-timeline audit (phase anchors, labelled holds, visible window, silence ledger, version binding, stale manifest) | **Verified — offline** | Same suite: 112 cases over anonymous synthetic fixtures, each defect reproduced by a committed fixture (anchors, phase declaration, audio-span silence, content binding, receipt, numeric rejection) |
| Perceived delivery, factual semantics, "is this cut any good" | **Not judged — by design** | `audit` reports a `not_a_verdict_on` list; only a human listening/watching plus source-frame checks can settle these |
| Subtitle paging and ASS time base | **Verified — offline** | `tests/test_subtitles.py`: 5 cases — phrase paging covers the span exactly, total-centisecond ASS carry, PlayRes = video size, escaping |
| EDL assembly with synthetic media (real `ffmpeg`) | **Verified — offline** | `tests/test_build_sample.py`: 13 cases, lavfi fixtures, incl. the one-shot adopted-timeline loop, A/B source mismatch, the freeze fail-closed path, libass burn + pixel check, wrong-band detection, source-audio mix and silent-source refusal |
| TTS adapter hard guarantees (no rewrite, empty/unmeasurable audio fails, no dud cache, per-node re-render) | **Verified — offline** | `tests/test_tts_guarantees.py`: 51 cases against a loopback fake endpoint |
| TTS adapter works with **your** provider | **Unverified — by design** | Only one wire shape is targeted; no cross-vendor claim is made |
| macOS recorder compiles and its offline checks pass | **Verified** | `tools/gamerec/tests/regression.sh` offline group: 20 checks, incl. both no-overwrite gates |
| macOS recorder: explicit target required, **never** falls back to another app | **Verified — offline** | Regression `O1`/`O6`; the usage error fires before any permission request |
| macOS recorder: refuses to overwrite existing outputs without `--overwrite` | **Verified — offline** | Regression `O7`/`O8`: exit `3`, original bytes unchanged |
| macOS recorder: bounded stop (startup and shutdown deadlines) | **Verified** | Startup bound exercised offline; the shutdown bound is in the permission group (`P7`) |
| macOS recorder: capture while target app is **not frontmost**, window on screen | **Verified on the author's machine** | `references/spire-checklist.md` §record-game-audio: −29.7 dBFS, 16/16 voiced seconds with another app in front |
| macOS recorder: app-level isolation (only the target app's audio) | **Verified on the author's machine** | Same section: a second captured app measured −160 dBFS / 22 silent seconds |
| macOS recorder: minimized / hidden / locked screen | **Unverified** | Never tested |
| macOS recorder: output muted, game restarted, multi-display | **Unverified** | Never tested |
| macOS recorder: Windows / Linux | **Unverified** | macOS-only, ScreenCaptureKit |
| Action-level audio/video **sync** | **Unverified** | The verifier compares container durations and timestamps only and says so explicitly |
| Whether a nonzero waveform is *that app's* audio | **Not proven by the tool** | Needs isolation + before/after + log cross-checks |
| Voice-over "human-likeness", retention, popularity | **Not measured** | No listening study; machine scores are opinions, not ground truth |
| Whether director notes actually change TTS delivery | **Not demonstrated** | In one small 2×2 sample (n=1 per cell, 2 takes each) no *stable* prompt effect was shown. No significance test was run, so this is "not demonstrated", not "disproven" — and it says nothing about the provider's capability. |
| Second game adapter | **Does not exist** | Only one `<game>-checklist.md` ships. No other game is adapted |

Full command output and limits for the exported revision: [`docs/verification-log.md`](docs/verification-log.md).

---


## Repository layout

```
agent-gameplay-studio/
├── README.md                  # this file (English)
├── README.zh-CN.md            # 中文说明（同一交付的中文版）
├── LICENSE                    # MIT — own work only, see NOTICE
├── NOTICE.md                  # provenance, third-party status, what is intentionally absent
├── SECURITY.md                # secret/redaction policy and the scan scope actually used
├── CHANGELOG.md
├── Makefile                   # offline entry points
├── .env.example               # placeholders only
├── docs/
│   ├── architecture.md        # stages, data flow, interaction example
│   └── verification-log.md    # what was run for this revision, with raw-ish output
├── examples/                  # SYNTHETIC fixtures + example timeline/EDL/sheet
├── skills/gameplay-postproduction/
│   ├── SKILL.md               # the SOP (Chinese; declares its own license: MIT)
│   ├── references/            # canonical standard, routing, voice design, one game checklist
│   ├── templates/             # asset register / commentary unit / timeline / silence ledger / review sheet
│   ├── evals/trigger_cases.json
│   └── scripts/               # check_postproduction.py + check_timeline_audit.py (no model calls)
├── tools/gamerec/             # macOS app-level "target app audio + picture" recorder (Swift)
├── tools/voice/               # optional TTS adapter, EDL assembler, subtitle burner
└── tests/                     # offline test suite
```

The `skills/gameplay-postproduction/` package is a standard Agent Skill: `SKILL.md` plus
`references/`, `templates/`, `evals/`, `scripts/`. It works standalone — the checker needs no model.

---


## Real prerequisites (and what is *not* bundled)

| Step | Needs | Bundled here? |
|---|---|---|
| Artifact validation (`timeline`/`units`/`sheet`/`ready`) | Python 3.9+, `ffprobe` for `ready` | **Yes** |
| Media bookkeeping, cut assembly | `ffmpeg` / `ffprobe` | Not bundled; install it |
| Recording game audio + picture | macOS 15+, Screen Recording permission | **Source included** (`tools/gamerec/`), you compile it |
| Subtitle paging and burn-in | `ffmpeg` **with libass** | **Included** (`tools/voice/subtitles.py` + `burn_subs.py` + `check_burned_subs.py`) |
| **Video understanding / event extraction** | *Your* model channel | **No. Not bundled.** |
| **Speech synthesis** | *Your* TTS endpoint + key | **No. Only an optional adapter — and not a provider abstraction.** |
| Cutting / compositing / export | *Your* editor | **No. Not bundled.** |

`tools/voice/tts_adapter.py` targets **one** wire shape (`POST {base}/chat/completions` with an
`audio` field). It does **not** claim cross-vendor generality and should not be read as "supports any
speech API". A different provider needs its own adapter. What it *does* guarantee, and what the
offline suite proves against a local fake endpoint, is that:

- **your script is never rewritten** — a rewrite is not requested by default, `optimize_text_preview`
  is sent explicitly `false` where the shape supports it, and a rewrite that comes back anyway is
  flagged rather than silently accepted (`--strict-no-rewrite` makes it a hard failure);
- **empty or unmeasurable audio is a failure, not a cached success** — an empty payload, no audio
  field, or a duration `ffprobe` cannot establish all raise, and the half-written file is deleted so
  the cache cannot serve a dud;
- **the cache is keyed on what actually matters, and then re-checked** — the request *plus* the strict
  flag *plus* a one-way fingerprint of the endpoint, and a hit is only honoured when the audio on disk
  still matches its recorded **sha256** and byte size with a **finite duration > 0**. A take accepted
  leniently is never replayed to a strict run, one provider's audio is never served for another, and a
  truncated or hand-swapped file is never certified by a stale sidecar. The endpoint URL is never
  stored, only its fingerprint;
- **credentials come from the environment, or from a dotenv-style file you name with `TTS_ENV_FILE`** —
  read for those names only, never echoed, never written into an artifact;
- **provider errors are redacted** — bearer tokens, `api_key=` / `token=` / `secret=` / `password=`
  pairs (quoted or bare), key-shaped strings and credential-bearing query parameters are replaced with
  `<redacted>` and truncated before they can reach a log, a traceback or an exception message.

The assembler next to it is deliberately **narration-only**: every picture clip is rendered with
`-an`, so the source's own audio is discarded at the cutting stage and the final bed is built solely
from your narration segments. It never silently mixes source audio into the cut; if the source is
intentionally silent the result is honestly silent, and ducking the original game sound afterwards is
left as an explicit decision for you.

**Installing this repository does not give you any model capability.** There is no bundled model, no
API key, no proxy, no subscription, and no logged-in session. `tools/voice/tts_adapter.py` is an
*adapter*: it does nothing until **you** set `TTS_BASE_URL` and `TTS_API_KEY` to an endpoint of your
own that speaks the documented wire shape. The skill's `references/routing.md` lists roles such as
`editor` / `watch` / a video-understanding channel — those are how the author's machine delegated
that work, **not dependencies you must install**. Substitute your own.

The author's own video-understanding preference was a configurable Flash-tier model with a second
vendor as a fallback. That is a *configurable choice*, not a capability this repo ships, and it is
not a statement about anyone's account, proxy or login state.

### Recording game audio on macOS

```bash
tools/gamerec/build.sh                                  # → tools/gamerec/build/GameAVRec.app
# Replace the example id with YOUR target app's bundle id.
# There is NO built-in default, by design: capturing the wrong app is worse than failing.
export GAME_BUNDLE_ID="com.megacrit.SlayTheSpire2"

tools/gamerec/record-game.sh preflight
tools/gamerec/record-game.sh start --out run.mp4 --duration 60 --focus-log run.focus.jsonl
tools/gamerec/record-game.sh verify run.mp4
```

Run `tools/gamerec/record-game.sh preflight` first: it tells you whether the id you chose actually
matched a running app, before you record anything.

`verify` reports **only what it measured**: an audio track exists, per-second peak levels across the
whole track, the voiced-bucket ratio, that video frames keep arriving, and that sampled frame content
actually changes (md5). It **states in its own output** that it does not prove sync, picture quality,
or that the sound came from the target app. Some games mute themselves when unfocused; the recorder
cannot tell that apart from a capture failure, so it reports silence rather than guessing.

---


## Architecture

Six **stage names**, not an API — run as many as you need; nothing requires implementing all of them:

```
ingest → analyze → commentary_plan → edit_plan → review → repair
```

```
recording ──ffprobe──► asset register ──(your model channel)──► candidate events
                                                                      │
                                                       verify against source frames  (mandatory)
                                                                      ▼
                                   commentary units ──► unified timeline ──► your editor
                                   (5 elements, 3 fields kept apart)             │
                                                                    exported MP4 ──► review sheet
                                                                                          │
                                                                                   local repair only
```

Three invariants are enforced by machine rather than by good intentions:

1. **One time base.** Video, voice and subtitles share a single timeline file. `final` length must
   equal `clip` length ÷ speed + freeze, and clip/final ranges may not overlap within an asset.
2. **Three fields never backfill each other.** `stated_reason` (what was said at the time),
   `retrospective_commentary` (hindsight, including hindsight rationalisation) and `outcome` are
   separate columns. The checker rejects an explicitly hindsight-labelled `stated_reason`.
3. **Verification gates export.** In EDL assembly, a narration segment that cannot fit its on-screen
   window **fails before rendering** rather than warning and exporting a mistimed cut.
- **The narration is anchored to the phase it narrates.** A post-battle or card-pick line sitting on
  the battle opening, a hold with no on-screen mark, a reward hold parked on a frame where the
  candidates are already gone, or a long silence nobody justified — each is a specific, named failure
  from `check_timeline_audit.py audit`, not a matter of taste.

### Interaction example

A full request, and what the tooling actually does with it:

```
You: "把录屏整理成后期流程，给我素材登记和解说准备"   (organise this recording; give me the
                                                     asset register and commentary prep)

  1. ffprobe the source                       → asset row: sha256, size, duration, fps, codec,
                                                resolution, has_audio
  2. your model channel reads the video       → CANDIDATE events. Not facts. Not cut points.
  3. every load-bearing claim goes back to    → win/loss, card picked, damage, HP, gold verified
     a source frame                              against frames; unmatched → `unknown`
  4. commentary units written                 → state / action / stated_reason / retrospective /
                                                outcome / coverage, one row per occurrence
  5. unified timeline built                   → source → clip → final, speed/freeze arithmetic, plus
                                                the phase/anchor/hold/visible-window columns
  6. checkers run                             → preflight before narration; structure gate; `audit`
                                                on the adopted timeline; `ready` after export
  7. review sheet                             → per-item evidence; unresolved issues block `ready`
```

---


## Non-cheating and separation from evaluation

This is a **hard constraint in the standard** (`references/postproduction-standard.md` §0), not a
courtesy note:

- Evaluation and postproduction are separate lines. This project **does not specify or recommend**
  any model or tool for the evaluation side. Evaluation follows its own track's protocol.
- Postproduction outputs — events, card reads, `stated_reason` — are **candidates, never rulings**
  on what actually happened in a match.
- Postproduction output **must not flow back** into competition decisions or any evaluated
  behaviour. **Postproduction does not participate in decisions.**
- **On-screen answer leakage:** multi-window captures (game + an assistant/report panel) put the
  answer *inside the frame*, which both leaks it and makes "did the model really understand the
  audio?" unanswerable. If you must capture that way, you declare it in the review sheet.
- The `ready` gate makes a related refusal explicit: an item that was never checked is `unknown`,
  and **`unknown` is not a pass**. "Zero misses" cannot be claimed without independent ground truth.

**What the boundary actually is.** It is an *information* boundary, not a product or vendor rule:
postproduction analysis, card reads and answer-bearing frames must not flow back into a decision that
is being evaluated, and an evaluated run must not be able to read them. That is the requirement, and
it is a requirement on the *pipeline and the evaluation protocol*, not a claim that a particular
model, tool or vendor is inherently disqualifying — using the same editor, the same ffmpeg, or the
same model for both sides is not by itself a violation. What is disqualifying is letting the answer
reach the thing being scored.

`agent-gamebench` and this repository are therefore kept separate as separate *projects* — different
repos, different fixtures, no shared scoring path — so that this isolation is easy to see and easy to
keep. The separation is a design choice that supports the rule; the rule itself is about information
flow.

---


## Honest boundaries

Read this before quoting anything from this repository.

**What it is not.** No GUI. No one-click render. No fully automatic, human-quality presenter. The
skill drives a process and enforces gates; a human still decides whether the result is good, and
`ready` explicitly refuses to judge picture or sound quality.

**What is unverified.** Audio/video **sync** is not verified by any tool here — the recorder's
verifier compares container durations and timestamps and says so in its own output. The recorder has
only been exercised for capture while the target app is **not frontmost with its window on screen**,
plus app-level isolation; minimized, hidden and locked screen, muted output, mid-capture restart,
multi-display and non-macOS recording are **untested**. The TTS adapter targets **one** wire shape and
is not a cross-vendor abstraction. Whether director notes change TTS delivery is **not demonstrated**
(one small 2×2 sample, no significance test) — which is not the same as disproven, and says nothing
about the provider.

**What is untested by choice.** The recorder's permission-group tests are skipped unless you supply a
real target and grant Screen Recording; they report SKIP rather than a pass they did not earn.

**What the example data is.** Everything under `examples/` is synthetic — no real match, no real
result, no performance claim.

Full command output, and the limits of each check, are in
[`docs/verification-log.md`](docs/verification-log.md).

---

## License and scope

**MIT for the parts this project owns** — see [`LICENSE`](LICENSE). The third-party situation, the
things deliberately *not* included, and the provenance of every shipped file are in
[`NOTICE.md`](NOTICE.md). Nothing closed-source is re-licensed here, and no third-party code is
vendored.

Shipped docs and tool output are largely in **Chinese**, matching the author's working language and
the skill's own text. Both READMEs are bilingual by design.
