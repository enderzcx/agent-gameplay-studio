# tools/voice — TTS adapter, EDL assembler, subtitles, subtitle burner

Small, independent pieces. None of them is a video editor, and none of them ships a model.

| File | Does | Needs |
|---|---|---|
| `tts_adapter.py` | **Optional external adapter.** Turns a segment spec into measured audio files, with a content-addressed cache | **Your** endpoint + key (`TTS_BASE_URL`, `TTS_API_KEY`) |
| `make_cut.sh` | **The delivery-default wrapper**: preflight -> decide source audio (keep+duck / record-and-continue) -> force `BURN_SUBS=1` -> adopted-timeline audit | `ffmpeg`, same inputs as below |
| `build_sample.sh` | Assembles source ranges + narration into a playable cut, with paged subtitles; optionally burns them and mixes source audio (historical defaults unchanged) | `ffmpeg`, a 7-column EDL, a voice directory |
| `subtitles.py` | Splits one narration span into phrase-sized cues; writes SRT and a **true-size** ASS (PlayRes = video size) | python3 only |
| `burn_subs.py` | Burns an SRT into an MP4 with **libass** (`ass=` filter) | `ffmpeg` **with libass** |
| `check_burned_subs.py` | Pixel-diffs the burned cut against the unburned one: is each cue really on screen, in-band, unclipped, off the protected UI band | `ffmpeg`, `ffprobe` |

---

## `tts_adapter.py` — an adapter, not a capability

```
┌───────────────────────────────────────────────────────────────────────┐
│ Installing this repository does NOT give you speech synthesis.        │
│ No model, no key, no proxy, no subscription, no logged-in session.     │
│ It runs only against an endpoint YOU provide that speaks this shape.   │
└───────────────────────────────────────────────────────────────────────┘
```

Wire shape (verify against your provider's docs before use):

```
POST {TTS_BASE_URL}/chat/completions
Authorization: Bearer $TTS_API_KEY
messages = [ {role:user,      content: <voice direction>},   # optional
             {role:assistant, content: <text to speak>} ]    # required
audio    = {format, voice?, optimize_text_preview?}
-> choices[0].message.audio.data   (base64)
```

> **Not a provider abstraction.** This targets exactly the one wire shape above. It does **not**
> claim to work across TTS vendors, and it must not be read as "supports any speech API" — two
> providers both exposing `/chat/completions` can still differ in every `audio` sub-field. A
> different provider needs its own adapter.

```bash
export TTS_BASE_URL=https://your-endpoint.example/v1
export TTS_API_KEY=...

python3 tts_adapter.py synth --text "one line" --out seg01.wav
python3 tts_adapter.py batch --spec segments.json --outdir ./takes

# refuse instead of merely flagging a provider-side rewrite of your script:
python3 tts_adapter.py synth --text "one line" --out seg01.wav --strict-no-rewrite
```

`segments.json` is a list of `{id, text, model?, voice?, direction?, optimize?}`.

### Behaviour worth knowing

- **Wire truth.** The `.meta.json` sidecar records what was *actually sent*: whether a voice field
  went out, whether an auto-rewrite request was made, measured duration, usage counters. A
  voice-design model never gets a voice field, so its metadata can never claim a built-in voice.
- **Your script is never rewritten.** A rewrite is not requested by default, and
  `optimize_text_preview` is sent explicitly `false` for the model known to support it. If rewritten
  text still comes back, it is flagged `text_rewritten_unexpectedly` — or, with `--strict-no-rewrite`,
  it is a hard failure and no success record is written. `--optimize` is the opt-in, never the default.
- **Empty or unmeasurable audio is a failure.** An empty audio payload, a response with no `audio`
  field, a zero-byte file, or a duration `ffprobe` cannot establish all raise; the half-written file
  is deleted and **no success cache entry is written**, so the next run redoes it instead of serving
  a dud from the cache.
- **No default voice.** `TTS_VOICE` is empty by default, meaning no voice field is sent at all.
  Picking a voice is a **listening decision**; the voice that once sounded right in another project
  is that project's finding, not a default for yours.
- **Content-addressed cache, keyed on everything that matters.** The cache key covers the request
  (`text`, `model`, `voice`, `direction`, `optimize`, `fmt`) **plus `strict_no_rewrite` and a one-way
  fingerprint of the endpoint**. The last two are not decoration: without the strict flag a take
  accepted leniently could be replayed to a strict run, and without the endpoint fingerprint, pointing
  at a different provider would silently replay the *previous* provider's audio. Switching either one
  re-synthesizes. The endpoint **URL is never stored** — only its fingerprint, and only for that
  comparison. Editing one line still re-bills only that line.
- **Credentials come from the environment or an env file you name.** Set `TTS_BASE_URL` /
  `TTS_API_KEY` directly, or point `TTS_ENV_FILE` at a dotenv-style file and they will be read from
  there when the process environment does not already set them. Values are read for those names only,
  never echoed, and never written into an artefact.
- **Provider errors are redacted before they can be seen.** Bodies and transport messages can echo the
  request — including the credential — so bearer tokens, `api_key=`/`token=`/`secret=`/`password=`
  style pairs (quoted, single-quoted or bare), key-shaped strings and key-bearing query parameters are
  replaced with `<redacted>` and the result is truncated, before it can reach a log, a traceback or an
  exception message. Redaction over-matches on purpose; non-secret text and host names are preserved.
- **No voice cloning.** The clone path is deliberately not implemented, so this tool will not clone
  a real person's voice.
- **Secrets stay secret.** The key is never printed and never written into artifacts.

---

## `build_sample.sh` — EDL assembly that refuses to export a mistimed cut

```bash
SRC_VIDEO=/abs/path/to/source.mp4 ./build_sample.sh edl.tsv ./voice ./out
```

`SRC_VIDEO` is **required** — no private default path is baked in. `SRC_CROP` defaults to
`scale=960:-2`; if your capture has an answer/report panel in frame, crop it out here, because an
in-frame answer is a leak, not a cosmetic issue.

EDL format (TAB-separated; header must match exactly):

```
seg	src_start	src_end	audio_file	offset	freeze	subtitle_text
```

`audio_file` is resolved under the voice directory. `freeze` is the explicitly registered
freeze-frame seconds at the end of that segment. The segment's on-screen window is
`(src_end - src_start) + freeze`, and narration must fit: `offset + measured_duration <= window`.

**The finished track is narration only — source audio is never silently mixed in.** Every picture
clip is rendered with `-an`, so the source's own audio track is discarded at the cutting stage, and the
final audio bed is built solely from the narration segments in your voice directory. If the source
recording is intentionally silent, the result is honestly silent apart from the narration; nothing here
fabricates game audio to fill it. If you *do* want to keep or duck the original game sound, mix it in
explicitly afterwards — that is deliberately left as the caller's decision rather than a default.

**The design rule is that verification gates export.** If any narration segment does not fit:

- it **fails before rendering**, with a non-zero exit and no `final.mp4`, and
- it prints the three legitimate fixes: shorten the line, extend the picture, or register an
  explicit freeze.

This is deliberate. The old behaviour — warn, then export anyway — pushed every later line out of
place and got truncated by `-shortest`, leaving a cut whose only visible symptom was the wrong
length. Segment audio is also clamped to its window in both directions (trim if long, pad if short)
and the final audio/video durations must match within tolerance, or the build fails.

Outputs: `final.mp4`, `subs.srt`, `subs.ass`, plus `video_raw.mp4`, `voice_master.wav` and per-segment
intermediates for inspection. With `BURN_SUBS=1` the subtitle burn happens **before** the receipt is
written, so the receipt and the audit bind the file you actually deliver, and the unburned cut is kept
as `final-nosub.mp4` for the pixel check.

Two optional behaviours, both off by default:

```bash
# burn subtitles into the delivered cut (needs ffmpeg with libass)
BURN_SUBS=1 SRC_VIDEO=/abs/rec.mp4 bash build_sample.sh edl.tsv voice_dir out_dir

# keep the source's own audio and mix it under the narration (ducked by the narration)
KEEP_SRC_AUDIO=1 SRC_AUDIO_GAIN=-8 SRC_AUDIO_DUCK=1 \
  SRC_VIDEO=/abs/rec-with-audio.mp4 bash build_sample.sh edl.tsv voice_dir out_dir
```

`KEEP_SRC_AUDIO=1` slices the source audio with the **same EDL**, clamps it to the same window as the
picture, and mixes it with the narration (`src_audio.wav` and `voice_master.wav` stay as separate
stems). If the source has no audio track at all, the build **fails** instead of handing you a silent
track pretending to be game audio.

---

## `subtitles.py` + `burn_subs.py` — page the cues, then burn with libass

```bash
python3 subtitles.py build --cues cues.tsv --srt subs.srt --ass subs.ass --w 960 --h 966 \
    [--font "Hiragino Sans GB"] [--size 28] [--margin-v 16] [--margin-lr 100] [--max-chars 0]
python3 burn_subs.py in.mp4 subs.srt out.mp4 [--labels holds.tsv] [--font NAME] [--size 28]
```

Three things here are not style choices, they are fixed incidents:

1. **One narration span is not one cue.** A 25-character line rendered as a single cue wraps to three
   lines and presses into the HUD. `subtitles.py` splits the span into phrase-sized cues and hands each
   cue a share of the **measured** duration.
2. **PlayResX/PlayResY must equal the video size.** SRT→ASS defaults to a 384×288 script space, so the
   FontSize/MarginV in `subtitles=...:force_style=...` get scaled by ≈3.35× on a 966-pixel-tall picture.
   `burn_subs.py` reads the real size with `ffprobe` and writes ASS itself.
3. **ASS timestamps go through total centiseconds.** `int(round(t % 1 * 100))` turns 0.999 into `.100`.

The old Pillow + temporary-PNG + `overlay` chain is **gone on purpose**: on a long cut it burned only
the first cue (74 cues in, 1 on screen), and once the temp directory was gone the result could not be
reproduced. There is no PNG fallback — a build without libass fails and says so.

---

## `check_burned_subs.py` — does the subtitle actually exist on screen?

```bash
python3 check_burned_subs.py --video out/final.mp4 --baseline out/final-nosub.mp4 \
    --subs out/subs.srt --band 900:966 --protect 655:845 --margin-x 40 --json subs-check.json
```

Counting SRT cues and hashing the file proves nothing about the picture. This samples a frame at each
cue (long cues get a second sample near their end), diffs it against the **same frame of the unburned
cut**, and reports per cue: pixels inside the subtitle band, pixels outside it, pixels inside the
left/right safety margin (a clipped final character), and pixels inside the protected band (hand cards,
key UI). It also reports the widest cue's pixels-per-character so a truncated tail shows up.

Out-of-band and margin pixels are judged against an **explicit noise tolerance** (burn-in re-encodes
the picture, so some out-of-band difference is expected); `--min-text-px` must be greater than zero,
because a zero threshold would let a cut with no subtitles at all "pass".

It does **not** judge listening quality, whether the subtitle matches what was said, whether the line
breaks read well, **or which characters were drawn**. `px/字` is printed as a diagnostic only: a pixel
difference can prove something was painted here, never that the right word (or its final character)
is on screen. `not_a_verdict_on` says so in the report.

---

## Tests

```bash
# from the repository root
python3 tests/test_subtitles.py        # 5 cases: paging, ASS carry, PlayRes, escaping
python3 tests/test_build_sample.py     # 13 cases: overlong rejected, freeze honoured, length mapping,
                                       # bad header rejected, burn + pixel check, wrong-band detection,
                                       # source-audio mix / silent-source refusal, no-text refusal
python3 tests/test_tts_guarantees.py   # 51 cases against a local fake endpoint (loopback only)
```

`test_build_sample.py` builds all its media from `ffmpeg` lavfi sources — no real footage, no game,
no network.

`test_tts_guarantees.py` stands up a throwaway HTTP server on `127.0.0.1` that speaks the wire shape,
so the adapter's real behaviour and failure modes are exercised **without any external network,
credential or vendor account**. It asserts the guarantees above: empty audio fails, unmeasurable
duration fails, no artifacts are left behind, a provider-side rewrite is flagged (and refused under
`--strict-no-rewrite`), the script we sent is the script on record, a voice is sent only when
requested, a voice-design model is never sent one, and a missing `TTS_BASE_URL` is refused because
there is no baked-in default. It also covers the cache identity — changing the endpoint or the strict
flag must re-synthesize rather than replay a foreign or lenient take — and that a provider error body
containing a token is redacted.
