# Examples — synthetic, not real gameplay

**Everything in this directory is fabricated.** The numbers, asset ids, event ids and narration
lines are made up to show the *shapes* the tools read and write. No real match, no real footage,
no real audio, no real player.

Do not read these as a benchmark result, a performance claim, or a record of a real run.

| File | What it is | Checked by |
|---|---|---|
| `timeline.example.tsv` | Unified timeline: source → clip → final, with speed/freeze arithmetic | `check_postproduction.py timeline` |
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
