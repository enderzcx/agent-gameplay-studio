#!/usr/bin/env python3
"""Optional external TTS adapter: `chat/completions` + `audio` payload.

    ┌──────────────────────────────────────────────────────────────────────┐
    │ This is an ADAPTER, not a bundled capability.                        │
    │ Installing this repository does NOT give you a speech model.         │
    │ You must already have an endpoint that speaks this wire shape, plus   │
    │ your own credentials. Without them this script cannot run at all.     │
    └──────────────────────────────────────────────────────────────────────┘

**This is NOT a universal provider abstraction.** It does not claim to work across TTS vendors,
and it must not be read as "supports any speech API". It targets exactly the one wire shape below.
Any other provider needs its own adapter; do not assume this one generalises, and do not assume two
providers behave the same just because both expose a `/chat/completions` route.

Wire shape this adapter targets (verify against *your* provider's docs before use):

    POST {TTS_BASE_URL}/chat/completions
    Authorization: Bearer $TTS_API_KEY
    messages = [ {role:user,      content: <voice direction / design>},   # optional
                 {role:assistant, content: <text to speak>} ]             # required
    audio    = {format, voice?, optimize_text_preview?}
    -> choices[0].message.audio.data   (base64)

Design goals (unchanged from the project it was extracted from):
  * semantic segments, one request per segment
  * content-addressed cache so a single segment can be redone without re-billing the rest
  * lossless intermediate files (wav/pcm)
  * **measured** duration per segment -- unmeasurable means failure, never a "success" cache entry
  * credentials read from env, never printed and never written into artefacts
  * metadata records only what was **actually put on the wire** (never a defaulted voice)

Cache identity (all of it, or the cache is not used):
  `text`, `model`, `voice`, `direction`, `optimize`, `fmt`, `strict_no_rewrite`, and a **one-way
  fingerprint of the endpoint**. The last two matter: without `strict_no_rewrite` a take accepted
  leniently could be replayed to a strict run, and without the endpoint fingerprint, pointing at a
  different provider would replay the previous provider's audio. The endpoint **URL** is never stored
  — only its fingerprint, and only for this comparison.

  A matching key is necessary but **not sufficient**: the entry is only reused when the audio on disk
  still IS the audio that was measured — `duration_s` must be a finite number > 0, and the file's
  **sha256** and byte size must match the sidecar. A truncated, overwritten or hand-swapped file
  therefore re-synthesizes instead of being certified by a stale sidecar.

Credential and error hygiene:
  * The key is read from the environment, or from a dotenv-style file named by `TTS_ENV_FILE` (read
    for those variable names only; contents are never echoed, logged or written to an artefact).
  * Provider error bodies and transport messages are **redacted** (bearer tokens, `api_key=`/
    `token=`/`secret=` style pairs, key-shaped strings, and key-bearing query parameters) and
    truncated before they can reach a log, a traceback or an exception message.

Wire-truth rules enforced here:
  * `audio.voice` is sent ONLY for models that accept it and only when a voice was requested.
    A voice-design model never gets a voice field, so its metadata can never claim a built-in voice.
  * **Your script is never rewritten.** A rewrite is never requested by default, and
    `optimize_text_preview` is sent **explicitly as false** for the design model (the only one known
    to support it), so "do not rewrite my script" is a real request on the wire, not an omission.
    If the response returns a non-null `final_text_preview` while we asked for false, the record is
    flagged `text_rewritten_unexpectedly` instead of being silently accepted; with
    `--strict-no-rewrite` that becomes a hard failure (`SynthError`) and no success record is written.
  * **Empty or unmeasurable audio is a failure**, not a cached success: a missing/zero-byte file, a
    response with no audio payload, or a duration that ffprobe cannot establish (missing, 0, NaN) all
    raise `SynthError`. The half-written file is deleted, so the next run redoes the segment instead
    of serving a dud from the cache.

Environment (no private defaults -- `TTS_BASE_URL` and `TTS_API_KEY` are required):
    TTS_BASE_URL   e.g. https://your-endpoint.example/v1      (required)
    TTS_API_KEY    bearer token                               (required)
    TTS_MODEL      default model id                            (optional)
    TTS_VOICE      default voice id; empty = do not send one   (optional)
    TTS_ENV_FILE   optional dotenv-style file to read the above from when the process
                   environment does not already set them (values are read, never echoed).
                   This covers `TTS_MODEL` and `TTS_VOICE` too: both are resolved at call
                   time, not captured once at import.

Voice selection is a **listening decision**, not a config default. There is no baked-in voice
here on purpose: the voice that sounded right in one project is that project's finding, not a
recommendation for yours. Pick one by listening to the same line across candidates.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Model / voice resolution is deliberately NOT done at import time.
#
# These used to be module-level `os.environ.get(...)` calls, which meant a value supplied through
# TTS_ENV_FILE never reached the runtime: the module captured only the process environment, once,
# at import. They are now resolved on every call through `_env()`, so the env file works for the
# model and voice exactly as it does for the base URL and the key.
#
# A voice-design model takes the voice as prose in the `user` message and must NOT receive
# `audio.voice`; a voice-clone model takes a sample. Names are defaults, not requirements.

DEFAULT_MODEL_ID = "mimo-v2.5-tts"
DEFAULT_VOICEDESIGN_ID = "mimo-v2.5-tts-voicedesign"
DEFAULT_VOICECLONE_ID = "mimo-v2.5-tts-voiceclone"


class SynthError(RuntimeError):
    """Raised when a segment did not produce a usable, measurable audio file."""


def _env(name: str, required: bool = False) -> str:
    v = os.environ.get(name)
    if v:
        return v
    env_file = os.environ.get("TTS_ENV_FILE")
    if env_file:
        try:
            for line in open(env_file, encoding="utf-8"):
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, val = line.split("=", 1)
                if k.strip() == name:
                    return val.strip().strip('"').strip("'")
        except FileNotFoundError:
            pass
    if required:
        raise SystemExit(
            f"missing {name}: set the environment variable, or point TTS_ENV_FILE at a dotenv-style "
            f"file containing {name}=... (the file's contents are read, never echoed)"
            + (f" [currently TTS_ENV_FILE={env_file}]" if env_file else ""))
    return ""


def _base() -> str:
    base = _env("TTS_BASE_URL", required=True).rstrip("/")
    # Strip a trailing /chat/completions so both `https://x/v1` and the full URL work.
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base


# --- model / voice defaults, resolved per call so TTS_ENV_FILE reaches the runtime ---------------

def voicedesign_model() -> str:
    return _env("TTS_MODEL_VOICEDESIGN") or DEFAULT_VOICEDESIGN_ID


def voiceclone_model() -> str:
    return _env("TTS_MODEL_VOICECLONE") or DEFAULT_VOICECLONE_ID


def voice_models() -> set:
    """Models that take the voice out-of-band and must NOT receive an `audio.voice` field."""
    return {voicedesign_model(), voiceclone_model()}


def default_model() -> str:
    return _env("TTS_MODEL") or DEFAULT_MODEL_ID


def default_voice() -> str:
    return _env("TTS_VOICE") or ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_record_is_reusable(old: dict, ck: str, out: Path) -> bool:
    """A cache entry is reusable only if the request matches AND the audio on disk is the audio
    that was measured.

    `cache_key` alone is not enough: the file could have been truncated, overwritten by a partial
    write, or swapped by hand since the sidecar was written, and a stale sidecar would then certify
    a dud. So this also requires

      * `duration_s` to be a finite number > 0 (a non-finite or zero duration is not evidence), and
      * the file's **sha256** to equal the recorded one, and
      * the byte size to match the recorded one.

    Any mismatch means: re-synthesize rather than serve a possibly-broken take.
    """
    if old.get("cache_key") != ck:
        return False
    dur = old.get("duration_s")
    if not isinstance(dur, (int, float)) or isinstance(dur, bool):
        return False
    if not math.isfinite(dur) or dur <= 0:
        return False
    if not out.exists() or out.stat().st_size == 0:
        return False
    recorded_sha = old.get("sha256")
    if not recorded_sha or sha256_file(out) != recorded_sha:
        return False
    recorded_bytes = old.get("bytes")
    if isinstance(recorded_bytes, int) and recorded_bytes != out.stat().st_size:
        return False
    return True


def measure_duration(path: Path) -> float:
    """Measured duration via ffprobe. Raises SynthError when it cannot be established."""
    if not path.exists() or path.stat().st_size == 0:
        raise SynthError(f"{path.name}: file missing or empty")
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        dur = float(out)
    except Exception as e:  # noqa: BLE001
        raise SynthError(f"{path.name}: ffprobe could not measure duration ({type(e).__name__}: {e})") from e
    if not math.isfinite(dur) or dur <= 0:
        raise SynthError(f"{path.name}: measured duration invalid ({out!r})")
    return round(dur, 6)


def cache_key(text: str, model: str, voice: str, direction: str, optimize: bool, fmt: str,
              strict_no_rewrite: bool = False, endpoint_fp: str = "") -> str:
    """Content-addressed cache key.

    It covers everything that could change the audio or the acceptance decision:

      * `text`, `model`, `voice`, `direction`, `optimize`, `fmt` — the request itself;
      * `strict_no_rewrite` — otherwise a take accepted leniently could be served to a strict run
        (and vice versa), silently bypassing the guarantee;
      * `endpoint_fp` — a **non-reversible fingerprint of the endpoint**, so pointing at a
        different provider invalidates the cache instead of replaying another vendor's audio.

    The endpoint itself is never stored: only its fingerprint, and only for this comparison.
    """
    blob = json.dumps([text, model, voice, direction, optimize, fmt,
                       bool(strict_no_rewrite), endpoint_fp], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def endpoint_fingerprint(base: str) -> str:
    """Short one-way fingerprint of an endpoint URL, for cache keying only.

    Deliberately a hash rather than the URL: switching providers must invalidate the cache, but
    the sidecar should not accumulate a record of which endpoints were used.
    """
    return hashlib.sha256((base or "").encode("utf-8")).hexdigest()[:12]


# Error bodies and transport messages can echo the request, and therefore the credential.
# Redact before anything reaches a log, a traceback or an exception message.
# Order matters: quoted key/value first (so the whole value is consumed, quotes included), then
# unquoted key/value, then bare bearer tokens, then key-shaped strings, then query parameters.
_SECRET_KEYS = (r"api[_-]?key|apikey|authorization|auth|access[_-]?token|refresh[_-]?token|"
                r"token|secret|password|passwd|credential")
_REDACTIONS = (
    # "authorization": "Bearer xyz"   /   'api_key': 'xyz'
    (re.compile(r"""(?i)(['"]?(?:%s)['"]?\s*[:=]\s*['"])([^'"]{4,})(['"])""" % _SECRET_KEYS),
     r"\1<redacted>\3"),
    # bare bearer tokens (run before the unquoted rule so it cannot leave a trailing fragment)
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}"), "Bearer <redacted>"),
    # api_key=xyz   token: xyz      (unquoted; never eats the word "Bearer" itself)
    (re.compile(r"""(?i)(['"]?(?:%s)['"]?\s*[:=]\s*)(?!Bearer\b)([^\s",}&']{4,})""" % _SECRET_KEYS),
     r"\1<redacted>"),
    # key-shaped strings
    (re.compile(r"(?i)\b(sk|gho|ghp|ghs|ghr|github_pat|xox[baprs])[-_][A-Za-z0-9_\-]{8,}"),
     "<redacted-token>"),
    # credential-bearing query parameters
    (re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token|auth)=)[^&\s]{4,}"), r"\1<redacted>"),
)


def redact(text: str, limit: int = 300) -> str:
    """Strip credential-shaped material and flatten whitespace, then truncate."""
    out = str(text)
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    out = " ".join(out.split())
    return out[:limit]


def synth(text: str, out: Path, *, model: str = "", voice: str = "", direction: str = "",
          optimize: bool = False, fmt: str = "wav", cache: bool = True,
          strict_no_rewrite: bool = False, timeout: int = 180, retries: int = 2) -> dict:
    """Synthesize `text` into `out`. Returns a metadata record (wire-truth). Cached unless cache=False."""
    model = model or default_model()
    voice = voice if voice else ""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta_path = out.with_suffix(out.suffix + ".meta.json")
    # Resolve the endpoint once, up front: it is part of the cache identity (a different provider
    # must not be served from cache) and must be a hard failure even on a would-be cache hit.
    base = _base()
    endpoint_fp = endpoint_fingerprint(base)
    ck = cache_key(text, model, voice, direction, optimize, fmt,
                   strict_no_rewrite=strict_no_rewrite, endpoint_fp=endpoint_fp)

    if cache and out.exists() and out.stat().st_size > 0 and meta_path.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            # A matching cache key is necessary but NOT sufficient: the audio on disk must still be
            # the audio that was measured. `cached_record_is_reusable` additionally requires a finite
            # positive duration and a matching sha256/size, so a truncated, overwritten or swapped
            # file re-synthesizes instead of being certified by a stale sidecar.
            if cached_record_is_reusable(old, ck, out):
                old["cache_hit"] = True
                return old
        except Exception:  # noqa: BLE001
            pass

    # --- what actually goes on the wire ---
    voice_requested = voice or None
    voice_field_sent = bool(voice) and model not in voice_models()
    if model == voiceclone_model():
        raise SystemExit(
            "voiceclone needs a sample as base64 `voice`; this adapter does not implement it "
            "(and does not clone real people's voices)")

    messages = []
    if direction:
        messages.append({"role": "user", "content": direction})
    messages.append({"role": "assistant", "content": text})

    audio: dict = {"format": fmt}
    if voice_field_sent:
        audio["voice"] = voice
    optimize_sent = None
    if model == voicedesign_model():
        # Explicitly disable auto-rewrite (only this model has the parameter).
        audio["optimize_text_preview"] = bool(optimize)
        optimize_sent = bool(optimize)

    body = {"model": model, "messages": messages, "audio": audio, "stream": False}
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + _env("TTS_API_KEY", required=True),
                 "Content-Type": "application/json"})

    last_err = None
    payload = None
    t0 = time.time()
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            # The body is provider-controlled and may echo the request — including the credential.
            # Redact before it can reach a log, a traceback or this exception's message.
            try:
                detail = e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                detail = "<unreadable body>"
            last_err = f"HTTP {e.code}: {redact(detail)}"
        except Exception as e:  # noqa: BLE001
            # Transport errors can carry the full request URL (and any key embedded in it).
            last_err = redact(f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    if payload is None:
        raise SynthError(f"TTS request failed for {out.name}: {last_err}")

    elapsed = round(time.time() - t0, 3)
    final_preview = None
    try:
        message = payload["choices"][0]["message"]
        data_b64 = message["audio"]["data"]
        final_preview = message.get("final_text_preview")
    except (KeyError, IndexError, TypeError):
        raise SynthError(f"unexpected TTS response for {out.name}: {redact(json.dumps(payload), 400)}")
    if not data_b64:
        raise SynthError(f"TTS response carried no audio data for {out.name}")

    # The script must not be silently replaced. We never ask for a rewrite by default, and if one
    # comes back anyway, `strict_no_rewrite` refuses it instead of recording a flagged success.
    if final_preview and not optimize and strict_no_rewrite:
        raise SynthError(
            f"{out.name}: provider rewrote the text although no rewrite was requested "
            f"(final_text_preview={final_preview!r}); refusing under --strict-no-rewrite")

    out.write_bytes(base64.b64decode(data_b64))

    # No duration = failure; do not leave a file that looks like a success.
    try:
        dur = measure_duration(out)
    except SynthError:
        out.unlink(missing_ok=True)
        raise

    rec = {
        "file": out.name,
        "cache_key": ck,
        "cache_hit": False,
        "model": model,
        # wire truth: these three must be mutually consistent
        "voice_requested": voice_requested,
        "voice_field_sent": voice_field_sent,
        "voice_effective": voice if voice_field_sent else None,  # never claim a built-in default
        "direction_sent": bool(direction),
        "optimize_text_preview_requested": bool(optimize),
        "optimize_text_preview_sent": optimize_sent,             # None = model has no such parameter
        "text_rewritten_unexpectedly": bool(final_preview) and not optimize,
        "strict_no_rewrite": bool(strict_no_rewrite),
        # Fingerprint only — the endpoint URL and the credential are never written to the sidecar.
        "endpoint_fingerprint": endpoint_fp,
        "format": fmt,
        "text": text,
        "chars": len(text),
        "duration_s": dur,          # guaranteed finite and > 0
        "bytes": out.stat().st_size,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "request_seconds": elapsed,
        "usage": payload.get("usage"),
        "response_id": payload.get("id"),
        "final_text_preview": final_preview,
    }
    meta_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(
        description="TTS segment adapter (chat/completions + audio). Requires TTS_BASE_URL and TTS_API_KEY.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth")
    s.add_argument("--text", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--model", default=default_model())
    s.add_argument("--voice", default=default_voice(),
                   help="voice id; empty (default) = do not send a voice field at all")
    s.add_argument("--direction", default="")
    s.add_argument("--optimize", action="store_true",
                   help="ask the provider to rewrite the text (off by default: your script is kept)")
    s.add_argument("--strict-no-rewrite", action="store_true",
                   help="fail instead of merely flagging when the provider returns rewritten text")
    s.add_argument("--format", default="wav")
    s.add_argument("--no-cache", action="store_true")

    b = sub.add_parser("batch", help="synth every segment in a JSON spec")
    b.add_argument("--spec", required=True,
                   help="JSON list of {id,text,model?,voice?,direction?,optimize?}")
    b.add_argument("--outdir", required=True)
    b.add_argument("--strict-no-rewrite", action="store_true",
                   help="fail instead of merely flagging when the provider returns rewritten text")

    a = ap.parse_args()
    if a.cmd == "synth":
        rec = synth(a.text, Path(a.out), model=a.model, voice=a.voice, direction=a.direction,
                    optimize=a.optimize, fmt=a.format, cache=not a.no_cache,
                    strict_no_rewrite=a.strict_no_rewrite)
        print(json.dumps(rec, ensure_ascii=False))
        return 0

    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    outdir = Path(a.outdir)
    rows, failures = [], []
    for item in spec:
        model = item.get("model", default_model())
        # Key detail: only an explicit voice in the spec is used. Absent means absent --
        # never silently falls back to a default voice.
        if "voice" in item:
            voice = item.get("voice") or ""
        else:
            voice = "" if model in voice_models() else default_voice()
        try:
            rec = synth(item["text"], outdir / f"{item['id']}.{item.get('format', 'wav')}",
                        model=model, voice=voice,
                        direction=item.get("direction", ""),
                        optimize=item.get("optimize", False),
                        fmt=item.get("format", "wav"),
                        strict_no_rewrite=a.strict_no_rewrite)
        except SynthError as e:
            failures.append({"id": item["id"], "error": str(e)})
            print(f"{item['id']:>10}  FAILED  {e}")
            continue
        rows.append(rec)
        flag = "cache" if rec["cache_hit"] else "new"
        extra = "  ! text_rewritten_unexpectedly" if rec.get("text_rewritten_unexpectedly") else ""
        print(f"{item['id']:>10}  {rec['duration_s']:>7.3f}s  {flag}{extra}")
    (outdir / "_batch.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"total measured {sum(r['duration_s'] for r in rows):.3f}s over {len(rows)} segments")
    if failures:
        (outdir / "_failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
        print(f"{len(failures)} segment(s) failed (wrote _failures.json; no success cache written)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
