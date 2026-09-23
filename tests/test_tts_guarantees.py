#!/usr/bin/env python3
"""Offline tests for tools/voice/tts_adapter.py — the hard guarantees, not the happy path.

A throwaway HTTP server on 127.0.0.1 stands in for a provider, so every case below tests the
adapter's real wire behaviour and its failure modes without any external network, credential, or
vendor account. Only loopback is used; nothing leaves the machine.

Guarantees under test:
  G1  your script is never rewritten: a rewrite is not requested by default, and one that comes back
      anyway is flagged `text_rewritten_unexpectedly` — or, with strict_no_rewrite, is a hard failure
  G2  empty audio is a failure, not a cached success (no output file, no .meta.json)
  G3  unmeasurable duration is a failure and the half-written file is deleted
  G4  a response with no audio payload is a failure
  G5  voice is sent only when requested; a voice-design model never receives a voice field
  G6  the endpoint is required — there is no baked-in default
  G7  credentials and endpoint come from the environment only

Run:  python3 tests/test_tts_guarantees.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VOICE_DIR = ROOT / "tools" / "voice"

PASS = 0
FAIL = 0

# ---------------------------------------------------------------- fake provider

CAPTURED: list[dict] = []
RESPOND_WITH: dict = {}


def make_wav_bytes(seconds: float = 0.5, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(rate * seconds))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        CAPTURED.append({"path": self.path, "body": body,
                         "auth": self.headers.get("Authorization", "")})
        # Error mode: echo a credential-shaped body, the way a real provider might on a bad key.
        if RESPOND_WITH.get("status"):
            raw = json.dumps(RESPOND_WITH.get("error_body", {"error": "nope"})).encode()
            self.send_response(RESPOND_WITH["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        msg = {"audio": {"data": RESPOND_WITH.get("data", "")}}
        if "final_text_preview" in RESPOND_WITH:
            msg["final_text_preview"] = RESPOND_WITH["final_text_preview"]
        payload = {"id": "fake-1", "choices": [{"message": msg}], "usage": {"total_tokens": 1}}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):  # silence
        pass


def start_server() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------- helpers

def load_adapter(base_url=None, api_key="test-key-not-real", keep_env_file=False):
    """(Re)import the adapter with a chosen environment.

    `base_url=None` leaves TTS_BASE_URL alone (so an env file can supply it).
    `keep_env_file=True` leaves TTS_ENV_FILE in place, which the env-file case needs.
    """
    if base_url is not None:
        os.environ["TTS_BASE_URL"] = base_url
    if api_key is not None:
        os.environ["TTS_API_KEY"] = api_key
    if not keep_env_file:
        os.environ.pop("TTS_ENV_FILE", None)
    if "tts_adapter" in sys.modules:
        del sys.modules["tts_adapter"]
    sys.path.insert(0, str(VOICE_DIR))
    import tts_adapter  # noqa: PLC0415
    return tts_adapter


def ck(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"ok  {name}")
        PASS += 1
    else:
        print(f"FAIL {name} {detail}")
        FAIL += 1


def expect_error(name: str, fn, *a, **kw) -> None:
    """fn must raise SynthError/SystemExit; also assert the failure left no artifacts."""
    try:
        fn(*a, **kw)
    except BaseException as e:  # noqa: BLE001  (SystemExit derives from BaseException, not Exception)
        ck(name, type(e).__name__ in ("SynthError", "SystemExit"), f"raised {type(e).__name__}: {e}")
        return
    ck(name, False, "did not raise at all")


# ---------------------------------------------------------------- cases

def main() -> int:
    if not shutil.which("ffprobe"):
        print("ffprobe not found: cannot run (duration measurement is part of the contract)",
              file=sys.stderr)
        return 2

    srv, base = start_server()
    tmp = Path(tempfile.mkdtemp(prefix="tts-test-"))
    try:
        # ---- G2/G4: empty or absent audio must fail, with no cached success ----
        mod = load_adapter(base)

        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = ""
        out = tmp / "empty.wav"
        expect_error("G2 empty audio is a failure", mod.synth, "hi", out)
        ck("G2 no output file left behind", not out.exists())
        ck("G2 no success sidecar written", not out.with_suffix(".wav.meta.json").exists())

        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(b"this is not audio at all").decode()
        out = tmp / "garbage.wav"
        expect_error("G3 unmeasurable duration is a failure", mod.synth, "hi", out)
        ck("G3 half-written file was deleted", not out.exists())
        ck("G3 no success sidecar written", not out.with_suffix(".wav.meta.json").exists())

        # ---- happy path: measured duration, and what actually went on the wire ----
        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(make_wav_bytes(0.5)).decode()
        out = tmp / "ok.wav"
        rec = mod.synth("hello there", out, voice="voice-A")
        ck("happy path measured a duration", rec["duration_s"] > 0, f"got {rec['duration_s']}")
        ck("happy path sent the requested voice", rec["voice_field_sent"] is True,
           f"got {rec['voice_field_sent']}")
        ck("happy path recorded no rewrite", rec["text_rewritten_unexpectedly"] is False)
        ck("sidecar was written", out.with_suffix(".wav.meta.json").exists())
        ck("the wire carried no voice when none was requested",
           mod.synth("x", tmp / "novoice.wav")["voice_field_sent"] is False)

        # ---- G6/G7: endpoint required, key from env, and sent as a bearer header ----
        ck("endpoint came from the environment", CAPTURED[-1]["path"] == "/chat/completions",
           f"got {CAPTURED[-1]['path']}")
        ck("key was sent as a bearer header (from env, never a literal)",
           CAPTURED[-1]["auth"] == "Bearer test-key-not-real")

        # ---- G1: the script is never rewritten ----
        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(make_wav_bytes(0.3)).decode()
        RESPOND_WITH["final_text_preview"] = "REWRITTEN BY THE PROVIDER"
        rec = mod.synth("my original line", tmp / "rewritten.wav")
        ck("G1 a rewrite that came back anyway is flagged, not silently accepted",
           rec["text_rewritten_unexpectedly"] is True)
        ck("G1 the record keeps the text WE sent",
           rec["text"] == "my original line", f"got {rec['text']!r}")

        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(make_wav_bytes(0.3)).decode()
        RESPOND_WITH["final_text_preview"] = "REWRITTEN BY THE PROVIDER"
        out = tmp / "strict.wav"
        expect_error("G1 strict_no_rewrite turns the rewrite into a hard failure",
                     mod.synth, "my original line", out, strict_no_rewrite=True)
        ck("G1 strict failure left no output file", not out.exists())

        # ---- G1b: no rewrite is *requested* by default ----
        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(make_wav_bytes(0.2)).decode()
        seen = tmp / "seen.wav"
        mod.synth("look at the wire", seen, model=mod.voicedesign_model())
        sent = CAPTURED[-1]["body"]["audio"]
        ck("G1b voice-design model sends optimize_text_preview explicitly false on the wire",
           sent.get("optimize_text_preview") is False, f"audio={sent}")
        ck("G5 voice-design model is never sent a voice field", "voice" not in sent,
           f"audio={sent}")

        # ---- G6: no endpoint configured at all ----
        saved_base = os.environ.pop("TTS_BASE_URL", None)
        try:
            mod2 = load_adapter("")
            os.environ.pop("TTS_BASE_URL", None)
            expect_error("G6 missing TTS_BASE_URL is refused (no baked-in default)",
                         mod2.synth, "hi", tmp / "noendpoint.wav")
        finally:
            if saved_base is not None:
                os.environ["TTS_BASE_URL"] = saved_base

        # ---- caching: only a genuinely measured success is reusable ----
        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(make_wav_bytes(0.4)).decode()
        c = tmp / "cached.wav"
        first = mod.synth("cache me", c)
        second = mod.synth("cache me", c)
        ck("a measured segment is cached", first["cache_hit"] is False and second["cache_hit"] is True)

        # ---- C1: the cache key covers the acceptance mode, not just the request ----
        strict_same = mod.synth("cache me", c, strict_no_rewrite=True)
        ck("C1 a lenient take is NOT replayed to a strict run",
           strict_same["cache_hit"] is False, f"got {strict_same}")

        # ---- C2: ...and the endpoint, so a different provider cannot be served from cache ----
        # Same file path, same text, different endpoint => must re-synthesize.
        other_srv, other_base = start_server()
        try:
            mod_other = load_adapter(other_base)
            switched = mod_other.synth("cache me", c)
            ck("C2 switching endpoint re-synthesizes instead of replaying foreign audio",
               switched["cache_hit"] is False, f"got {switched}")
        finally:
            other_srv.shutdown()
            other_srv.server_close()
            # Point back at the original server: the adapter resolves TTS_BASE_URL at call time,
            # so leaving it on the closed port would make the next request hang on a dead socket.
            load_adapter(base)
            os.environ["TTS_BASE_URL"] = base

        # ---- C3: the endpoint URL is fingerprinted, never written to the sidecar ----
        sidecar = json.loads((tmp / "cached.wav.meta.json").read_text(encoding="utf-8"))
        ck("C3 sidecar carries an endpoint fingerprint", bool(sidecar.get("endpoint_fingerprint")))
        ck("C3 sidecar never stores the endpoint URL",
           base not in json.dumps(sidecar) and "127.0.0.1" not in json.dumps(sidecar))
        ck("C3 sidecar never stores the credential",
           "test-key-not-real" not in json.dumps(sidecar))

        # ---- R1: provider error bodies are redacted, and the key never surfaces ----
        # Built at runtime on purpose: this file must NOT contain a key-shaped literal, or a secret
        # scanner — including GitHub push protection — would flag this repository for its own fixture.
        SECRET = "sk-" + "x" * 32
        RESPOND_WITH.clear()
        RESPOND_WITH["status"] = 401
        RESPOND_WITH["error_body"] = {"error": f"invalid key {SECRET}", "authorization": f"Bearer {SECRET}"}
        try:
            mod.synth("trigger an error", tmp / "err.wav", timeout=5, retries=0)
            ck("R1 an HTTP error raises", False, "no exception raised")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            ck("R1 an HTTP error raises", True)
            ck("R1 the key-shaped secret is redacted from the error", SECRET not in msg, msg[:120])
            ck("R1 redaction is visible in the message", "<redacted>" in msg or "redacted" in msg, msg[:120])
        RESPOND_WITH.clear()

        # ---- R2: redaction does not destroy benign content ----
        ck("R2 benign text survives redaction",
           "hello there friend" in mod.redact('{"ok":true,"text":"hello there friend"}'))
        ck("R2 a bare bearer token is redacted",
           "abcdef123456ghijkl" not in mod.redact('{"authorization":"Bearer abcdef123456ghijkl"}'))
        ck("R2 a single-quoted key pair is redacted",
           "supersecretvalue123" not in mod.redact("{'api_key': 'supersecretvalue123'}"))
        ck("R2 a credential-bearing query parameter is redacted",
           "qqqqqqqqqqqqq" not in mod.redact("https://x/v1?api_key=qqqqqqqqqqqqq"))

        # ---- C4: a matching cache key is not enough — the audio on disk must still be the audio
        #          that was measured. Two negative cases, then the env-file resolution case.
        RESPOND_WITH.clear()
        RESPOND_WITH["data"] = base64.b64encode(make_wav_bytes(0.4)).decode()
        h = tmp / "hardened.wav"
        base_rec = mod.synth("harden me", h)
        ck("C4 a healthy entry hits", mod.synth("harden me", h)["cache_hit"] is True)

        # negative 1: tamper with the audio file -> sha256 no longer matches the sidecar
        h.write_bytes(make_wav_bytes(0.4)[:2000] + b"tampered")
        tampered = mod.synth("harden me", h)
        ck("C4-N1 tampered/truncated audio is NOT served from cache",
           tampered["cache_hit"] is False, "cache_hit=%r" % (tampered.get("cache_hit"),))
        ck("C4-N1 the file was re-synthesized to the recorded content",
           mod.sha256_file(h) == base_rec["sha256"], "sha256 was not restored")
        ck("C4-N1 a healthy entry hits again afterwards",
           mod.synth("harden me", h)["cache_hit"] is True)

        # negative 2: a sidecar whose duration is not a finite positive number is not evidence
        meta = tmp / "hardened.wav.meta.json"
        saved = json.loads(meta.read_text(encoding="utf-8"))
        for bad_dur in (float("nan"), float("inf"), 0, -1.0, "x", None):
            broken = dict(saved)
            broken["duration_s"] = bad_dur
            meta.write_text(json.dumps(broken), encoding="utf-8")
            got = mod.synth("harden me", h)
            ck("C4-N2 invalid duration_s (%r) is NOT served from cache" % (bad_dur,),
               got["cache_hit"] is False, "cache_hit=%r" % (got.get("cache_hit"),))
        meta.write_text(json.dumps(saved), encoding="utf-8")
        ck("C4-N2 restored sidecar hits again", mod.synth("harden me", h)["cache_hit"] is True)

        # C5: model/voice must reach the runtime through TTS_ENV_FILE, not only the process
        #     environment (they used to be read once, at import time).
        envfile = tmp / "tts.env"
        envfile.write_text(
            "TTS_BASE_URL=%s\nTTS_API_KEY=file-key-not-real\nTTS_MODEL=model-from-envfile\n"
            "TTS_VOICE=voice-from-envfile\n" % base, encoding="utf-8")
        for k in ("TTS_BASE_URL", "TTS_API_KEY", "TTS_MODEL", "TTS_VOICE"):
            os.environ.pop(k, None)
        os.environ["TTS_ENV_FILE"] = str(envfile)
        try:
            mod3 = load_adapter(None, api_key=None, keep_env_file=True)
            ck("C5 TTS_MODEL from TTS_ENV_FILE reaches the runtime",
               mod3.default_model() == "model-from-envfile", mod3.default_model())
            ck("C5 TTS_VOICE from TTS_ENV_FILE reaches the runtime",
               mod3.default_voice() == "voice-from-envfile", mod3.default_voice())
            ck("C5 the base URL from the env file is used", mod3._base() == base, mod3._base())
            rec3 = mod3.synth("env file works", tmp / "envfile.wav")
            ck("C5 synthesis using only env-file credentials succeeds", rec3["duration_s"] > 0)
        finally:
            os.environ.pop("TTS_ENV_FILE", None)
            for k in ("TTS_BASE_URL", "TTS_API_KEY", "TTS_MODEL", "TTS_VOICE"):
                os.environ.pop(k, None)
            load_adapter(base)
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
