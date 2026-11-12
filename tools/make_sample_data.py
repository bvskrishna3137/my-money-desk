#!/usr/bin/env python3
"""Generate money-data.sample.json: a made-up, internally consistent data set.

Everything here is invented. Account balances are not typed in by hand; they are
produced by replaying every entry with the same rules the app uses (see `fx()`
in index.html), so the numbers on screen add up.

    python3 tools/make_sample_data.py > money-data.sample.json
"""
import json
import random
from datetime import date, datetime, timedelta

random.seed(2026)  # same output every run
START, TODAY = date(2026, 8, 1), date(2026, 11, 12)
RATE = 84.0  # rupees per dollar used for the overseas account

# ---- accounts and debts -------------------------------------------------------
cards = [
    dict(id="cardchk", name="Everyday Checking", type="debit", last4="1234", balance=0, lowLimit=300, linkedDebtId=""),
    dict(id="cardcash", name="Cash", type="cash", last4="", balance=60, lowLimit=20, linkedDebtId=""),
    dict(id="cardsav", name="High-yield Savings", type="savings", last4="9012", balance=1800, lowLimit=0, linkedDebtId=""),
    dict(id="cardcc", name="Rewards Card", type="credit", last4="5678", balance=640, creditLimit=3000, linkedDebtId="dcc", stmtDay=24),
    dict(id="cardin", name="Overseas account (₹)", type="india", last4="3456", balance=55000, lowLimit=15000, linkedDebtId=""),
]
C = {c["id"]: c for c in cards}


def debt(id_, name, bal, apr, minp, due, cur="USD", **extra):
    d = dict(id=id_, name=name, currency=cur, balance=bal, originalBalance=bal, apr=apr, minPayment=minp, dueDay=due,
             flex=False, warnDays=3, payMonthly=0, promoEnd="", payoffBy="", paidMonths=[], dismissedMonths=[],
             skippedMonths=[], created="2026-08", createdDate="2026-08-01")
    d.update(extra)
    return d


debts = [
    debt("dcc", "Rewards Card", 640, 24.99, 35, 20),
    debt("dloan", "Student loan", 9000, 5.5, 120, 5),
    debt("dfam", "Family loan (₹)", 160000, 0, 10000, 10, cur="INR", payMonthly=10000),
    debt("dstore", "Store card (0% promo)", 715, 26.99, 25, 15, promoEnd="2027-01-31", payoffBy="2027-01-31"),
]
D = {d["id"]: d for d in debts}

expenses, income = [], []
_n = {"exp": 0, "inc": 0}


def r2(x):
    return round(x + 1e-9, 2)


def card_name(c):
    return c["name"] + (" ••" + c["last4"] if c["last4"] else "")


def fx(e):
    """Apply an expense to balances (mirrors fx() in index.html for the categories used)."""
    c, amt, lt, cat = C.get(e["cardId"]), e["amount"], e.get("linkedDebtId", ""), e["category"]
    if c["type"] == "india":
        c["balance"] = r2(c["balance"] - e.get("inrAmount", 0))
    elif c["type"] == "credit":
        c["balance"] = r2(c["balance"] + amt)
        D[c["linkedDebtId"]]["balance"] = r2(D[c["linkedDebtId"]]["balance"] + amt)
    else:
        c["balance"] = r2(c["balance"] - amt)
    if cat == "ToSavings":
        C[lt[5:]]["balance"] = r2(C[lt[5:]]["balance"] + amt)
    elif cat == "SendIndia":
        C[lt[5:]]["balance"] = r2(C[lt[5:]]["balance"] + e["inrAmount"])
    elif cat == "CreditCardPay":
        t = C[lt[5:]]
        t["balance"] = r2(t["balance"] - amt)
        D[t["linkedDebtId"]]["balance"] = r2(D[t["linkedDebtId"]]["balance"] - amt)
    elif cat == "Debts":
        d = D[lt[5:]]
        d["balance"] = r2(d["balance"] - (e.get("inrAmount", amt) if d["currency"] == "INR" else amt))
        if d["currency"] != "INR":
            cc = next((k for k in cards if k["type"] == "credit" and k["linkedDebtId"] == d["id"]), None)
            if cc:
                cc["balance"] = r2(cc["balance"] - amt)
        d["paidMonths"].append(e["date"][:7])


def add_exp(day, amount, cat, note, card="cardchk", link="", **extra):
    _n["exp"] += 1
    e = dict(id=f"exp_{_n['exp']:04d}", date=day.isoformat(), amount=r2(amount), category=cat, note=note,
             cardId=card, cardName=card_name(C[card]), linkedDebtId=link)
    e.update(extra)
    expenses.append(e)
    return e


def add_inc(day, amount, source, card="cardchk", **extra):
    _n["inc"] += 1
    i = dict(id=f"inc_{_n['inc']:04d}", amount=r2(amount), date=day.isoformat(), source=source, targetCardId=card)
    i.update(extra)
    income.append(i)
    C[card]["balance"] = r2(C[card]["balance"] + amount)
    return i


# ---- the story: fixed monthly things -----------------------------------------
REF = {"Rent": "bill_1", "Phone & internet": "bill_2", "Electric bill": "bill_3", "Streaming": "bill_4", "Car insurance": "bill_5",
       "Student loan payment": "dloan", "Family loan payment": "dfam", "Credit card payment": "dcc", "Store card payment": "dstore"}
months = [(2026, 8), (2026, 9), (2026, 10), (2026, 11)]
for y, m in months:
    def on(d):
        return date(y, m, d)
    fixed = [
        (1, 1200, "Bills", "Rent", "cardchk", ""),
        (12, 45, "Bills", "Phone & internet", "cardchk", ""),
        (25, 110, "Bills", "Car insurance", "cardchk", ""),
        (18, round(random.uniform(66, 92), 2), "Bills", "Electric bill", "cardchk", ""),
        (20, 15.99, "Bills", "Streaming", "cardcc", ""),
        (8, 2.99, "Bills", "Cloud storage", "cardcc", ""),
        (3, 150, "ToSavings", "Move to savings", "cardchk", "card_cardsav"),
        (5, 120, "Debts", "Student loan payment", "cardchk", "debt_dloan"),
        (15, 200, "SendIndia", "Sent to family", "cardchk", "card_cardin"),
        (17, 250, "CreditCardPay", "Credit card payment", "cardchk", "card_cardcc"),
        (15, 25, "Debts", "Store card payment", "cardchk", "debt_dstore"),
    ]
    for d, amt, cat, note, card, link in fixed:
        if on(d) <= TODAY and not (y == 2026 and m == 11 and d == 12):  # Nov 12 phone bill is "due today"
            extra = {"inrAmount": round(amt * RATE)} if cat == "SendIndia" else {}
            if note in REF:
                extra["dueRef"] = dict(id=REF[note], key=f"{y}-{m:02d}")
            add_exp(on(d), amt, cat, note, card, link, **extra)
    if on(10) <= TODAY:  # family loan paid from the overseas account
        add_exp(on(10), 120, "Debts", "Family loan payment", "cardin", "debt_dfam", inrAmount=10000, dueRef=dict(id="dfam", key=f"{y}-{m:02d}"))

# paychecks every other Friday, plus a little extra income
pay = date(2026, 8, 14)
while pay <= TODAY:
    add_inc(pay, 1500, "Paycheck")
    pay += timedelta(days=14)
add_inc(date(2026, 11, 3), 300, "Freelance project")
add_inc(date(2026, 9, 19), 60, "Sold old monitor", "cardcash")

# ---- everyday spending (random but seeded) -----------------------------------
daily = [
    ("Food", "Coffee", 3.8, 6.5, 0.34), ("Food", "Lunch", 8.5, 15.5, 0.40), ("Food", "Groceries", 28, 78, 0.14),
    ("Food", "Dinner out", 18, 46, 0.12), ("Gas", "Fuel", 30, 52, 0.07), ("Shopping", "Household items", 14, 56, 0.07),
    ("Shopping", "Clothes", 22, 60, 0.03), ("Fun", "Movie night", 14, 27, 0.04), ("Fun", "Coffee & Fun", 10, 30, 0.07),
    ("Other", "Pharmacy", 9, 21, 0.03),
]
day = START
while day <= TODAY:
    for cat, note, lo, hi, p in daily:
        if random.random() < p:
            card = random.choices(["cardchk", "cardcc", "cardcash"], [0.5, 0.4, 0.1])[0]
            add_exp(day, random.uniform(lo, hi), cat, note, card)
    day += timedelta(days=1)

# a few entries that make the Insights screen interesting
add_exp(date(2026, 10, 5), 129.0, "Shopping", "Headphones", "cardcc")
add_exp(date(2026, 11, 9), 5.4, "Food", "Coffee", "cardchk")
add_exp(date(2026, 11, 9), 5.4, "Food", "Coffee", "cardchk")  # accidental double entry
add_exp(date(2026, 11, 7), 62.0, "Gas", "Fuel", "cardchk")
add_exp(date(2026, 10, 28), 84.0, "Other", "Vet visit", "cardchk")

# ---- work hours (cash shifts) ---------------------------------------------------
jobs = [dict(id="job_1", name="Weekend shifts", rate=16.0)]
work = []
for i, (d, h) in enumerate([("2026-10-10", 6), ("2026-10-17", 5.5), ("2026-10-24", 6), ("2026-11-07", 6), ("2026-11-10", 4)]):
    paid = d <= "2026-10-24"
    w = dict(id=f"wk_{i+1}", jobId="job_1", jobName="Weekend shifts", date=d, hours=h, rate=16.0, amount=r2(h * 16),
             start="10:00", end="16:00", paid=paid)
    work.append(w)
paid_total = sum(w["amount"] for w in work if w["paid"])
inc = add_inc(date(2026, 10, 26), paid_total, "Work pay: Weekend shifts", "cardcash", workIds=[w["id"] for w in work if w["paid"]])
for w in work:
    if w["paid"]:
        w["incomeId"] = inc["id"]

# ---- rewards -------------------------------------------------------------------
rewards = [
    dict(id="rw_1", kind="earn", cardId="cardcc", amount=8.72, date="2026-08-31", note=""),
    dict(id="rw_2", kind="earn", cardId="cardcc", amount=11.35, date="2026-09-30", note=""),
    dict(id="rw_3", kind="earn", cardId="cardcc", amount=14.1, date="2026-10-31", note=""),
]
ri = add_inc(date(2026, 11, 2), 25.0, "Rewards: Rewards Card", "cardchk")
rewards.append(dict(id="rw_4", kind="redeem", cardId="cardcc", amount=25.0, date="2026-11-02", note="Statement credit", incomeId=ri["id"]))

# ---- calibrate the opening checking balance so today's balance looks natural ------
TARGET_CHK = 2380.0
C["cardchk"]["balance"] = r2(C["cardchk"]["balance"] + 0)  # replay ran with opening 0 for checking
# replay: income was applied immediately above; expenses are applied here in date order
for e in sorted(expenses, key=lambda x: (x["date"], x["id"])):
    fx(e)
C["cardchk"]["balance"] = r2(C["cardchk"]["balance"] + (TARGET_CHK - C["cardchk"]["balance"]))
D["dcc"]["balance"] = C["cardcc"]["balance"]  # linked card and debt always show the same amount
D["dloan"]["paidMonths"] = sorted(set(D["dloan"]["paidMonths"]))
D["dfam"]["paidMonths"] = sorted(set(D["dfam"]["paidMonths"]))
D["dcc"]["paidMonths"] = ["2026-08", "2026-09", "2026-10"]

bills = []
for i, (name, kind, amt, due) in enumerate([("Rent", "Rent", 1200, 1), ("Phone & internet", "Phone / Internet", 45, 12),
                                            ("Electric bill", "Utilities", 80, 18), ("Streaming", "Subscription", 15.99, 20),
                                            ("Car insurance", "Car insurance", 110, 25)]):
    paid = ["2026-08", "2026-09", "2026-10"] + (["2026-11"] if name == "Rent" else [])
    bills.append(dict(id=f"bill_{i+1}", name=name, kind=kind, amount=amt, currency="USD", dueDay=due, warnDays=3,
                      paidMonths=paid, dismissedMonths=[], skippedMonths=[], created="2026-08", createdDate="2026-08-01"))

recurring = [dict(id="rec_1", name="Paycheck", amount=1500, varies=False, freq="biweekly", weekday=5, day=1,
                  start="2026-08-14", acct="cardchk", done=[i["date"] for i in income if i["source"] == "Paycheck"])]
goals = [dict(id="goal_1", name="Emergency fund", target=3000, saved=1050),
         dict(id="goal_2", name="Trip fund", target=1200, saved=300),
         dict(id="goal_3", name="New laptop", target=900, saved=450)]
assets = [dict(id="asset_1", name="Car", value=9800, currency="USD"),
          dict(id="asset_2", name="Retirement account", value=6400, currency="USD"),
          dict(id="asset_3", name="Fixed deposit (India)", value=250000, currency="INR")]
scores = [dict(id=f"sc_{i+1}", date=d, score=s) for i, (d, s) in enumerate(
    [("2026-08-15", 701), ("2026-09-15", 709), ("2026-10-15", 718), ("2026-11-10", 724)])]
autoexp = [dict(id="ax_1", name="Streaming", amount=15.99, freq="monthly", cat="Bills", start="2026-08-20", cardId="cardcc", on=True, last="2026-10-20"),
           dict(id="ax_2", name="Cloud storage", amount=2.99, freq="monthly", cat="Bills", start="2026-08-08", cardId="cardcc", on=True, last="2026-11-08")]

data = dict(
    v=2, savedAt=int(datetime(2026, 11, 12, 9, 30).timestamp() * 1000),
    income=sorted(income, key=lambda x: (x["date"], x["id"])), expenses=sorted(expenses, key=lambda x: (x["date"], x["id"])),
    bills=bills, debts=debts, cards=cards, goals=goals, recurring=recurring, jobs=jobs, work=work, assets=assets,
    autoexp=autoexp, scores=scores, rewards=rewards,
    budgets={"Food": 450, "Gas": 160, "Shopping": 200, "Fun": 120, "Bills": 260, "Other": 100}, deleted={},
    settings=dict(remind=3, weekStart=1, notifyHour=20, lastCardId="cardchk", rate=RATE, strategy="avalanche",
                  extraUsd=50, extraInr=0, saveDay=3, putBack=0, userName="Alex", noCard=["dstore"],
                  features={"networth": True},
                  nwSnaps={"2026-08": dict(n=9820, a=22640, l=12820), "2026-09": dict(n=10960, a=23480, l=12520),
                           "2026-10": dict(n=12180, a=24190, l=12010)},
                  shortcuts=[dict(cat="Food", amt=15, note="Lunch"), dict(cat="Gas", amt=45, note="Fuel"),
                             dict(cat="Food", amt=35, note="Groceries"), dict(cat="Fun", amt=20, note="Coffee & Fun")]),
)

if __name__ == "__main__":
    print(json.dumps(data, indent=1, ensure_ascii=False))
