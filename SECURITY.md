# Security and privacy

This project handles two things that leak easily: **screen recordings** and **credentials**.

## Credentials

- `tools/voice/tts_adapter.py` reads its endpoint and key from the environment
  (`TTS_BASE_URL`, `TTS_API_KEY`) or from a dotenv file you point at explicitly with `TTS_ENV_FILE`.
- It **never prints** the key, **never writes** it into the `.meta.json` sidecar, and **never logs**
  it. The metadata records what went on the wire (model, whether a voice field was sent, measured
  duration, usage counters) — not the credential.
- `.env.example` contains **placeholders only**. There is no `.env` in this repository, and
  `.gitignore` excludes `.env` and friends so one cannot be committed by accident.
- Nothing in this repository ships a key, token, cookie, proxy setting or logged-in session. If you
  want speech synthesis you supply your own endpoint and credential.

## Recording: the redaction rule

Recording the screen captures **everything on it**. The standard's first rule for capture is:

> Close or move off-screen any window containing keys, tokens, private conversations, or real names
> before you start recording. Logs and footage must not contain secrets.

The review sheet makes this a gated item (`G1`: no secrets/tokens/private information in picture or
sound) and the `ready` gate refuses to pass without it being answered explicitly.

If it leaks anyway, it is in the master file and in every cut derived from it. Treat the master as
the sensitive artifact — the pipeline does not blur, redact or detect secrets for you.

## What must never be committed

- footage, audio, or subtitle files from a real session (the repo's `.gitignore` blocks common
  media extensions as a backstop — it is a backstop, not a guarantee);
- any `.env` value, key, token or cookie;
- personal absolute paths, machine identifiers, internal hostnames, Steam IDs or session IDs;
- raw provider response logs, or base64 media blobs;
- real people's voices, or voice-clone samples.

`examples/` is intentionally **fully synthetic** and stays that way. Do not replace the fixtures
with real captures.

## Scan scope — what was actually checked, and what that does not cover

For this exported revision, before the first commit, the tracked tree was checked with a
pattern-based scan for: absolute home/volume paths, personal identifiers, credential-shaped
strings, provider/company names, private network ranges, and machine identifiers. The tracked file
list, the exact patterns, and the results are recorded in `docs/verification-log.md`.

**Limits of that scan, stated plainly:**

- It is **pattern-based**, not a general secret scanner. A sufficiently unusual secret format, a
  secret split across lines, or a secret inside a binary blob would not be caught by the patterns.
- It only inspects **text files** and only what is **tracked by git**. Untracked and ignored files
  are outside its view, and the .gitignore backstop means an ignored-but-present secret could sit on
  disk without appearing in the commit.
- It proves nothing about **media content**. A recording that shows a secret is invisible to a text
  scan.
- It is not a substitute for a human read of a diff you are about to publish. Do that too.

## Reporting

This is a personal project with no security response process. If you find a leaked credential or
private artifact in the repository, open an issue **without pasting the secret**, or contact the
author directly, and it will be removed from the tree. Note that removing a file from the tip of a
branch does not remove it from git history — history rewriting may be required, and any secret that
was ever pushed should be treated as compromised and rotated.
