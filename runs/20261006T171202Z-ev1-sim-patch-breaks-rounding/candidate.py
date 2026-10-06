def unit_price(total_cents, quantity):
    if total_cents < 0 or quantity < 0:
        raise ValueError("Amounts and quantities must be nonnegative")
    if quantity == 0:
        return 0
    return total_cents // quantity
