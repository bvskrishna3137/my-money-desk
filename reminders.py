"""Optional phone reminders for My Money Desk.
Needs the Termux:API app and `pkg install termux-api`. If they are missing, nothing happens."""
import calendar, datetime, json, os, shutil, subprocess, threading, time

def _addm(key, n):
    y, m = map(int, key.split('-')); t = y * 12 + (m - 1) + n
    return '%04d-%02d' % (t // 12, t % 12 + 1)

def due_list(data, today):
    remind = int((data.get('settings') or {}).get('remind', 3)); cur = today.strftime('%Y-%m'); out = []
    items = [b for b in data.get('bills', [])] + [d for d in data.get('debts', []) if (d.get('balance') or 0) > 0.005]
    for it in items:
        dd = int(it.get('dueDay') or 15); done = set(it.get('paidMonths') or []) | set(it.get('dismissedMonths') or [])
        k = it.get('created') or cur; keys = []; n = 0
        while k <= cur and n < 24: keys.append(k); k = _addm(k, 1); n += 1
        keys.append(_addm(cur, 1))
        for key in keys:
            if key in done: continue
            y, m = map(int, key.split('-')); due = datetime.date(y, m, min(dd, calendar.monthrange(y, m)[1]))
            if it.get('createdDate') and key == it.get('created') and due.isoformat() < it['createdDate']: continue
            diff = (due - today).days
            if diff <= remind: out.append((it.get('name', '?'), diff))
    return sorted(out, key=lambda x: x[1])

def occurrences(rec, lo, hi):
    start = datetime.date.fromisoformat(rec.get('start') or lo.isoformat()); lo = max(lo, start); out = []
    if rec.get('freq') == 'monthly':
        k = lo.strftime('%Y-%m')
        for _ in range(40):
            if k > hi.strftime('%Y-%m'): break
            y, m = map(int, k.split('-')); d = datetime.date(y, m, min(int(rec.get('day') or 1), calendar.monthrange(y, m)[1]))
            if lo <= d <= hi: out.append(d)
            k = _addm(k, 1)
        return out
    wd = int(rec.get('weekday') or 0); d = start
    while (d.weekday() + 1) % 7 != wd: d += datetime.timedelta(days=1)
    step = 14 if rec.get('freq') == 'biweekly' else 7
    if d < lo: d += datetime.timedelta(days=-(-(lo - d).days // step) * step)
    while d <= hi: out.append(d); d += datetime.timedelta(days=step)
    return out

def pending_income(data, today):
    out = []
    for r in data.get('recurring', []):
        for d in occurrences(r, today - datetime.timedelta(days=31), today):
            if d.isoformat() not in (r.get('done') or []): out.append((r.get('name', '?'), d))
    return out

def summary(data, a, b):
    earned = sum(i.get('amount', 0) for i in data.get('income', []) if a <= i.get('date', '') <= b)
    living = loan = 0
    for e in data.get('expenses', []):
        if not (a <= e.get('date', '') <= b): continue
        c = e.get('category')
        if c == 'Debts': loan += e.get('amount', 0)
        elif c not in ('CreditCardPay', 'Lent', 'SendIndia'): living += e.get('amount', 0)
    return earned, living, loan

def check(now, data, state, send):
    s = data.get('settings') or {}; hour = s.get('notifyHour', 20)
    if hour is None or hour < 0 or now.hour < hour: return False
    today = now.date(); iso = today.isoformat(); sent = state.setdefault('sent', {}); changed = False
    def once(key, text):
        nonlocal changed
        if key in sent: return
        send('My Money Desk', text); sent[key] = iso; changed = True
    due = due_list(data, today)
    if due: once('due-' + iso, 'Due: ' + ', '.join('%s (%s)' % (n, 'overdue %dd' % -d if d < 0 else 'today' if d == 0 else 'in %dd' % d) for n, d in due[:5]))
    pend = pending_income(data, today)
    if pend: once('inc-' + iso, 'Confirm money received: ' + ', '.join('%s (%s)' % (n, d.strftime('%a %b %d')) for n, d in pend[:4]))
    if today.weekday() == 6:
        e, l, p = summary(data, (today - datetime.timedelta(days=6)).isoformat(), iso)
        once('week-' + iso, 'This week: earned $%.0f, spent $%.0f, loans paid $%.0f, kept $%.0f.' % (e, l, p, e - l - p))
    if today.day == 1:
        last = today - datetime.timedelta(days=1)
        e, l, p = summary(data, last.replace(day=1).isoformat(), last.isoformat())
        once('month-' + last.strftime('%Y-%m'), '%s: earned $%.0f, spent $%.0f, loans paid $%.0f, kept $%.0f.' % (last.strftime('%B'), e, l, p, e - l - p))
    for k in sorted(sent)[:-200]: sent.pop(k, None)
    return changed

def termux_send(title, text):
    if shutil.which('termux-notification'):
        subprocess.run(['termux-notification', '--title', title, '--content', text, '--id', str(abs(hash(text)) % 99999)], timeout=20)

def start(data_path, interval=600):
    state_path = os.path.join(os.path.dirname(data_path), 'notify-state.json')
    def loop():
        while True:
            try:
                with open(data_path) as f: data = json.load(f)
                try:
                    with open(state_path) as f: state = json.load(f)
                except Exception: state = {}
                if check(datetime.datetime.now(), data, state, termux_send):
                    with open(state_path, 'w') as f: json.dump(state, f)
            except Exception: pass
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True).start()
