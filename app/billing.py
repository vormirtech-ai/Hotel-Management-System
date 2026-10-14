"""Bill and tax calculation. Pure functions, Decimal arithmetic, no database."""
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def dec(value):
    return Decimal(str(value or 0))


def money(value):
    return dec(value).quantize(CENT, ROUND_HALF_UP)


def nights_between(check_in, check_out):
    return (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days


def matching_taxes(taxes, category, unit_rate):
    """Active tax rules for a category whose slab contains unit_rate."""
    unit_rate = dec(unit_rate)
    return [
        t for t in taxes
        if t["active"]
        and t["applies_to"] in (category, "all")
        and dec(t["min_amount"]) <= unit_rate
        and (t["max_amount"] is None or unit_rate <= dec(t["max_amount"]))
    ]


def compute_bill(lines, taxes, round_off=True):
    """Price a list of lines.

    Each line: description, category ('room'|'venue'|'extra'), qty, rate and
    optionally discount (amount), sac and slab_rate (the unit value used to
    pick a tax slab; defaults to the discounted unit price).
    """
    out, summary = [], {}
    subtotal = discount_total = taxable_total = tax_total = Decimal(0)
    for ln in lines:
        qty, rate = dec(ln["qty"]), dec(ln["rate"])
        amount = money(qty * rate)
        discount = min(money(ln.get("discount", 0)), amount)
        taxable = amount - discount
        slab = dec(ln["slab_rate"]) if ln.get("slab_rate") is not None else (taxable / qty if qty else Decimal(0))
        line_taxes = []
        for t in matching_taxes(taxes, ln["category"], slab):
            tax = money(taxable * dec(t["rate"]) / 100)
            line_taxes.append({"name": t["name"], "rate": float(t["rate"]), "amount": float(tax)})
            row = summary.setdefault((t["name"], float(t["rate"])), [Decimal(0), Decimal(0)])
            row[0] += taxable
            row[1] += tax
            tax_total += tax
        subtotal += amount
        discount_total += discount
        taxable_total += taxable
        out.append({
            "description": ln["description"],
            "category": ln["category"],
            "sac": ln.get("sac", ""),
            "qty": float(qty),
            "rate": float(rate),
            "amount": float(amount),
            "discount": float(discount),
            "taxable": float(taxable),
            "taxes": line_taxes,
        })
    total = taxable_total + tax_total
    rounding = (total.quantize(Decimal(1), ROUND_HALF_UP) - total) if round_off else Decimal(0)
    return {
        "lines": out,
        "tax_summary": [
            {"name": name, "rate": rate, "taxable": float(v[0]), "amount": float(v[1])}
            for (name, rate), v in sorted(summary.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ],
        "subtotal": float(subtotal),
        "discount": float(discount_total),
        "taxable": float(taxable_total),
        "tax_total": float(tax_total),
        "round_off": float(rounding),
        "total": float(total + rounding),
    }


def booking_lines(booking, room_label, charges, settings):
    """Bill lines for a room stay: nights, extra beds and extra charges."""
    nights = nights_between(booking["check_in"], booking["check_out"])
    beds = booking["extra_beds"] or 0
    stay_value = dec(booking["rate"]) * nights + dec(booking["extra_bed_rate"]) * beds * nights
    # GST slab is decided by the tariff actually charged per night.
    room_slab = (stay_value - dec(booking["discount"])) / nights
    lines = [{
        "description": f"{room_label} · {nights} night{'s' if nights != 1 else ''} "
                       f"({booking['check_in']} to {booking['check_out']})",
        "category": "room", "sac": settings.get("sac_room", ""),
        "qty": nights, "rate": booking["rate"], "discount": booking["discount"], "slab_rate": room_slab,
    }]
    if beds:
        lines.append({
            "description": f"Extra bed × {beds}", "category": "room", "sac": settings.get("sac_room", ""),
            "qty": beds * nights, "rate": booking["extra_bed_rate"], "slab_rate": room_slab,
        })
    lines += _charge_lines(charges, settings, room_slab)
    return lines


def venue_lines(vb, venue_label, charges, settings):
    slot = {"full": "Full day", "morning": "Morning slot", "evening": "Evening slot"}[vb["slot"]]
    title = f"{venue_label} · {slot} on {vb['event_date']}"
    if vb["event_name"]:
        title += f" – {vb['event_name']}"
    lines = [{
        "description": title, "category": "venue", "sac": settings.get("sac_venue", ""),
        "qty": 1, "rate": vb["rate"], "discount": vb["discount"],
    }]
    return lines + _charge_lines(charges, settings, None)


def _charge_lines(charges, settings, room_slab):
    return [{
        "description": f"{c['description']} ({c['charge_date']})",
        "category": c["tax_class"],
        "sac": settings.get(f"sac_{c['tax_class']}", ""),
        "qty": c["quantity"], "rate": c["unit_price"],
        # Room-class charges (e.g. late checkout) follow the room's slab.
        "slab_rate": room_slab if c["tax_class"] == "room" and room_slab is not None else None,
    } for c in charges]


def late_checkout_fee(settings, booking, now=None):
    """Fee due when a guest leaves after check-out time + grace on/after the departure day."""
    pct = dec(settings.get("late_checkout_fee_percent"))
    if pct <= 0:
        return Decimal(0)
    now = now or datetime.now()
    hh, mm = (int(x) for x in settings.get("check_out_time", "11:00").split(":"))
    due = datetime.combine(date.fromisoformat(booking["check_out"]), datetime.min.time()).replace(hour=hh, minute=mm)
    due += timedelta(hours=float(settings.get("late_checkout_grace_hours") or 0))
    return money(dec(booking["rate"]) * pct / 100) if now > due else Decimal(0)


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n):
    return _ONES[n] if n < 20 else _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def number_to_words(n):
    """Integer to words in the Indian system (thousand, lakh, crore)."""
    if n == 0:
        return "Zero"
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    hundred, n = divmod(n, 100)
    parts = []
    if crore:
        parts.append(number_to_words(crore) + " Crore")
    if lakh:
        parts.append(_below_100(lakh) + " Lakh")
    if thousand:
        parts.append(_below_100(thousand) + " Thousand")
    if hundred:
        parts.append(_ONES[hundred] + " Hundred")
    if n:
        parts.append(_below_100(n))
    return " ".join(parts)


def amount_in_words(amount):
    value = money(amount)
    sign = "Minus " if value < 0 else ""
    value = abs(value)
    rupees = int(value)
    paise = int((value - rupees) * 100)
    words = f"{sign}Rupees {number_to_words(rupees)}"
    if paise:
        words += f" and {number_to_words(paise)} Paise"
    return words + " Only"
