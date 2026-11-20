"""Synthetic building permits shaped like Chicago's dataset. Not real records."""

from __future__ import annotations

import random
from datetime import date, timedelta

COLUMNS = {
    "id": "text", "permit_": "text", "permit_type": "text", "review_type": "text",
    "application_start_date": "calendar_date", "issue_date": "calendar_date",
    "street_number": "number", "street_direction": "text", "street_name": "text",
    "work_description": "text", "reported_cost": "number", "total_fee": "number",
    "community_area": "number", "ward": "number", "latitude": "number", "longitude": "number",
    "contact_1_type": "text", "contact_1_name": "text",
}

# (permit type, share of permits, typical cost range)
TYPES = [
    ("PERMIT - RENOVATION/ALTERATION", 0.38, (60_000, 900_000)),
    ("PERMIT - NEW CONSTRUCTION", 0.17, (250_000, 6_000_000)),
    ("PERMIT - ELECTRIC WIRING", 0.15, (50_000, 300_000)),
    ("PERMIT - WRECKING/DEMOLITION", 0.08, (50_000, 400_000)),
    ("PERMIT - ELEVATOR EQUIPMENT", 0.07, (80_000, 700_000)),
    ("PERMIT - PORCH CONSTRUCTION", 0.08, (50_000, 150_000)),
    ("PERMIT - SIGNS", 0.07, (50_000, 120_000)),
]
WORK = {
    "PERMIT - RENOVATION/ALTERATION": ["Interior alteration of {n}-unit residential building", "Kitchen and bath remodel, new partitions", "Tenant build-out for retail space on 1st floor", "Gut rehab of existing two-story frame building"],
    "PERMIT - NEW CONSTRUCTION": ["Erect {n}-story masonry residential building", "New single-family residence with detached garage", "Erect mixed-use building, retail and {n} dwelling units"],
    "PERMIT - ELECTRIC WIRING": ["Install new {n}00A electric service", "Electrical work for EV chargers in parking garage", "Rewire existing {n}-unit building"],
    "PERMIT - WRECKING/DEMOLITION": ["Wreck and remove {n}-story frame building", "Demolish existing garage"],
    "PERMIT - ELEVATOR EQUIPMENT": ["Modernize {n} traction elevators", "Install new hydraulic elevator"],
    "PERMIT - PORCH CONSTRUCTION": ["Replace rear wood porch, {n} stories"],
    "PERMIT - SIGNS": ["Install illuminated wall sign", "Install {n} non-illuminated signs"],
}
STREETS = [("N", "CLARK ST"), ("W", "DIVISION ST"), ("S", "HALSTED ST"), ("N", "MILWAUKEE AVE"), ("W", "FULLERTON AVE"),
           ("S", "ASHLAND AVE"), ("N", "BROADWAY"), ("W", "ARMITAGE AVE"), ("S", "COTTAGE GROVE AVE"), ("N", "LINCOLN AVE"),
           ("W", "CHICAGO AVE"), ("S", "WESTERN AVE"), ("W", "BELMONT AVE"), ("N", "SHERIDAN RD"), ("S", "STATE ST")]
FIRST = ["MARIA", "JAMES", "WEI", "AISHA", "ROBERT", "ANA", "DAVID", "PRIYA"]
LAST = ["NOWAK", "JOHNSON", "GARCIA", "CHEN", "WILLIAMS", "PATEL", "KOWALSKI", "BROWN"]


def make_permits(start: date, end: date, seed: int = 7) -> list[dict]:
    """Records with a `_posted` date (when the portal shows them) and optional `_revision`."""
    rng = random.Random(seed)
    rows = []
    day = start
    n = 0
    while day <= end:
        per_day = rng.randint(4, 9) if day.weekday() < 5 else rng.randint(0, 2)
        for _ in range(per_day):
            n += 1
            ptype = rng.choices([t[0] for t in TYPES], weights=[t[1] for t in TYPES])[0]
            low, high = next(t[2] for t in TYPES if t[0] == ptype)
            cost = round(rng.lognormvariate(0, 0.6) * (low + high) / 4, -2)
            cost = int(min(max(cost, low), high * 1.5))
            direction, street = rng.choice(STREETS)
            desc = rng.choice(WORK[ptype]).format(n=rng.randint(2, 6))
            row = {
                "id": str(3_300_000 + n),
                "permit_": f"10{rng.randint(1000000, 9999999)}",
                "permit_type": ptype,
                "review_type": rng.choice(["STANDARD PLAN REVIEW", "EXPRESS PERMIT PROGRAM", "SELF CERT"]),
                "application_start_date": f"{(day - timedelta(days=rng.randint(10, 120))).isoformat()}T00:00:00.000",
                "issue_date": f"{day.isoformat()}T00:00:00.000",
                "street_number": str(rng.randint(100, 9999)),
                "street_direction": direction,
                "street_name": street,
                "work_description": desc,
                "reported_cost": str(cost),
                "total_fee": f"{round(cost * rng.uniform(0.004, 0.012), 2)}",
                "community_area": str(rng.randint(1, 77)),
                "ward": str(rng.randint(1, 50)),
                "latitude": f"{rng.uniform(41.70, 42.00):.6f}",
                "longitude": f"{rng.uniform(-87.80, -87.55):.6f}",
                # Personal data the feed must NOT store:
                "contact_1_type": "OWNER",
                "contact_1_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                # Most records appear in 0-3 days; a few are posted 1-2 weeks late.
                "_posted": day + timedelta(days=rng.choice([0, 0, 1, 1, 2, 3, 3, 9, 12])),
            }
            if rng.random() < 0.03:
                row["work_description"] = "  " + desc.upper() + "\n  (REVISED)  "  # messy whitespace
            if rng.random() < 0.02:
                row["ward"] = "4.5"  # bad value: not a whole number
            if rng.random() < 0.04:
                # The portal later corrects the reported cost.
                row["_revision"] = (row["_posted"] + timedelta(days=rng.randint(5, 15)), str(int(cost * 1.25)))
            rows.append(row)
        day += timedelta(days=1)
    return rows


def visible_on(rows: list[dict], today: date) -> list[dict]:
    """What the portal would show on `today`."""
    out = []
    for r in rows:
        if r["_posted"] > today:
            continue
        row = {k: v for k, v in r.items() if not k.startswith("_")}
        if "_revision" in r and r["_revision"][0] <= today:
            row["reported_cost"] = r["_revision"][1]
        out.append(row)
    return out
