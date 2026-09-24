# Examples — synthetic, not real gameplay

**Everything in this directory is fabricated.** The numbers, asset ids, event ids and narration
lines are made up to show the *shapes* the tools read and write. No real match, no real footage,
no real audio, no real player.

Do not read these as a benchmark result, a performance claim, or a record of a real run.

| File | What it is | Checked by |
|---|---|---|
| `timeline.example.tsv` | Unified timeline: source → clip → final, with speed/freeze arithmetic **and the audit columns** (phase / claim phase / anchor asset+event+interval / evidence / hold mark / candidate-visible window / three content digests) | `check_postproduction.py timeline` |
| `silence-ledger.example.tsv` | Per-gap justification for the one long silence the example timeline contains (one row, matching `41.300-62.300`) | `check_timeline_audit.py audit --silence-ledger …` |
| `units.example.tsv` | Per-unit commentary prep, with `stated_reason` / `retrospective_commentary` / `outcome` kept separate | `check_postproduction.py units` |
| `review-sheet.example.md` | A *filled* review sheet that is structurally valid — and deliberately **not** delivery-ready | `check_postproduction.py sheet` |
| `edl.example.tsv` | 7-column edit decision list consumed by `tools/voice/build_sample.sh` | `tests/smoke_build_sample.sh` |

## Why `review-sheet.example.md` is not `ready`

`sheet` mode only checks **structure**. The `ready` gate is stricter, and this example fails it on
purpose, for a reason worth internalising:

- `误报数：unknown` / `漏报数：unknown` — there is no independent ground truth here, so the honest
  value is `unknown`, and **`unknown` means unverified, which must not be treated as a pass**.

That is the whole point of having two modes. Run both and compare:

```bash
C=skills/gameplay-postproduction/scripts/check_postproduction.py
python3 "$C" sheet examples/review-sheet.example.md    # exit 0 — structure is fine
```

`ready` additionally requires a real exported MP4 to probe, so it cannot be exercised from a
fixture alone. `tests/test_check_postproduction.py` builds a throwaway synthetic MP4 and both a
passing and a failing sheet to cover that path.

## Why the example timeline is only structure-checked here

`timeline.example.tsv` carries 64-zero digests in its three `*_sha256` columns: they are
placeholders that keep the example readable, and a real timeline must carry the real content
digests or the audit rejects the binding. It also uses realistic-looking source timecodes (470 s+),
so auditing it would need a recording that long. The example pair (`timeline.example.tsv` + `silence-ledger.example.tsv`) is
kept internally consistent — the ledger's `41.300-62.300` row is exactly the gap the timeline
produces — but the audit itself runs in `tests/test_timeline_audit.py` against short synthetic media
generated at test time, where the whole chain (preflight → audit → stale detection) can be executed
for real without committing any binary.
