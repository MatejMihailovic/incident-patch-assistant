# Fixtures

All data is fictional and comes from the Junior AI Engineer starter pack (pack version `2026-10-02`, see `pack-version.json`). Rules are defined only by [`incidents/domain.md`](incidents/domain.md).

## Layout

| Path | Origin | May change? |
|---|---|---|
| `incidents/` | Starter pack, copied byte-for-byte | **No.** Hashes are in `frozen.json` and checked before and after every run |
| `additions/extra-events.json` | Handwritten addition | Frozen before the first model run |
| `additions/extra-inputs.json` | Handwritten addition | Frozen before the first model run |
| `baseline-results/` | Output of the fixed check command on the unchanged baseline, saved before any model call | No |
| `simulated/` | Handwritten, clearly labelled simulated model responses (negative controls) | No |

## Generation method

Everything was written by hand. Nothing was randomly generated, so no random seed applies.

- **EV4** is the same zero-quantity defect as EV1 and EV2, but with a nonzero total (`1500, 0`) and a traceback-style payload. It checks that the assistant groups evidence by failure, not by an identical input.
- **EV5** is deliberately malformed: it has no `quantity`, `error` or `source`. The loader must report it and keep going. It is a deliberate invalid case, not a data error.
- **extra-inputs.json** holds supplementary boundary inputs, mostly about rounding and negative inputs. `check.py` does not grade them, because the fixed check and its five cases stay unchanged. The assistant reports them in a separate, labelled section.

The original `events.json` is unchanged. The application loads it plus `additions/extra-events.json`.

## Five reference cases (checked by hand before any model run)

The rule from domain.md: if `q == 0` the result is `0`; otherwise it is `round_half_up(total / q)`; negative arguments raise `ValueError`.

| Case | Args | Hand calculation | Expected | Baseline (saved in `baseline-results/`) |
|---|---|---|---|---|
| zero-quantity | (0, 0) | q = 0 → 0 by rule 1 | 0 | **fails**: ZeroDivisionError |
| ordinary | (1000, 2) | 1000 / 2 = 500 exactly | 500 | passes, 500 |
| rounding | (1001, 2) | 1001 / 2 = 500.5 → half up → 501 | 501 | passes, 501 |
| zero-total | (0, 4) | 0 / 4 = 0 | 0 | passes, 0 |
| single | (1999, 1) | 1999 / 1 = 1999 | 1999 | passes, 1999 |

`python3 scripts/verify_reference.py` recomputes every expectation from the rules using `Decimal` with `ROUND_HALF_UP`. It does not import `baseline.py` or any application code. All 11 graded expectations agree.

The baseline formula `(2*t + q) // (2*q)` is half-up rounding for nonnegative integers. That matches rule 2, so the only defect is division by zero when `q == 0`. This agrees with domain.md: "Its other four reference cases pass."

## Ambiguity found (not resolved by the domain rules)

**Negative total with zero quantity, `(-1, 0)`.** Rule 1 says both "for quantity zero, it must return zero" and "negative arguments raise ValueError". It doesn't say which takes priority. None of the frozen reference cases covers this input. The current baseline validates first, so it raises `ValueError`. A patch that adds `if quantity == 0: return 0` *before* validation would return `0` instead.

The assistant doesn't grade this case. It reports the observed behaviour of both baseline and candidate, and the report flags any change from the baseline as a point for the reviewer to decide. Keeping the baseline's existing validation order is the least surprising choice, but that is an assumption, not a domain rule.
