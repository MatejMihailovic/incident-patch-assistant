"""Independently recompute expected results from the domain rules.

Does not import baseline.py or any application code: the rules are re-derived
here with Decimal half-up rounding, so a shared bug cannot hide in both places.
Exit code 0 only if every committed expectation agrees with this derivation.
"""
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rule_unit_price(total_cents, quantity):
    if total_cents < 0 or quantity < 0:
        raise ValueError("negative")
    if quantity == 0:
        return 0
    return int((Decimal(total_cents) / Decimal(quantity)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def main():
    ok = True
    reference = json.loads((ROOT / "fixtures/incidents/reference-cases.json").read_text())
    extra = json.loads((ROOT / "fixtures/additions/extra-inputs.json").read_text())["cases"]
    print(f"{'case':28} {'args':14} {'committed':>10} {'derived':>10}  result")
    for case in reference + extra:
        if case.get("ambiguous"):
            print(f"{case['id']:28} {str(case['args']):14} {'-':>10} {'-':>10}  AMBIGUOUS (not graded)")
            continue
        committed = case.get("expected", case.get("expected_error"))
        try:
            derived = rule_unit_price(*case["args"])
        except ValueError:
            derived = "ValueError"
        agree = committed == derived
        ok &= agree
        print(f"{case['id']:28} {str(case['args']):14} {str(committed):>10} {str(derived):>10}  {'agree' if agree else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
