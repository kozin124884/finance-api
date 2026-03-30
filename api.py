import os
import json
import urllib.request
import urllib.parse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List


# --- Принудительно задаём переменные из окружения (с проверкой) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL environment variable not set!")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY environment variable not set!")

print(f"✅ SUPABASE_URL = {SUPABASE_URL}")
print(f"✅ SUPABASE_KEY starts with {SUPABASE_KEY[:20]}...")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def sb_get(table, params=""):
    """Универсальный GET запрос к Supabase"""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    print(f"DEBUG: requesting {url}")  # добавим вывод для логов
    req = urllib.request.Request(
        url,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())

def sb_post(table, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}",
        data=body, method='POST',
        headers={
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal'
        })
    with urllib.request.urlopen(req) as res:
        return res.status

def sb_patch(table, filter_col, filter_val, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filter_col}=eq.{filter_val}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, method='PATCH',
        headers={
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type': 'application/json'
        })
    with urllib.request.urlopen(req) as res:
        return res.status
        

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/initial-data")
def get_initial_data():
    try:
        cats = sb_get("categories")
        accounts = sb_get("accounts")
        transactions = sb_get("transactions", "order=date.desc")
        archive = sb_get("archive_debts")
    except Exception as e:
        print("Error fetching data:", e)
        raise

    config = {"accounts": [], "inc": {}, "exp": {}}
    for c in cats:
        if c["type"] == "Счет":
            config["accounts"].append(c["name"])
        elif c["type"] == "Доход":
            g = c.get("group_name") or "Прочее"
            config["inc"].setdefault(g, []).append(c["name"])
        elif c["type"] == "Расход":
            g = c.get("group_name") or "Прочее"
            config["exp"].setdefault(g, []).append(c["name"])

    formatted_tx = []
    for r in transactions:
        d = r["date"]
        day_month = d[8:10] + "." + d[5:7]
        formatted_tx.append({
            "r": r["id"],
            "fd": d,
            "d": day_month,
            "type": r["type"],
            "catE": r.get("cat_expense") or "",
            "sumE": r.get("sum_expense") or 0,
            "accE": r.get("acc_expense") or "",
            "comE": r.get("comment_expense") or "",
            "catI": r.get("cat_income") or "",
            "sumI": r.get("sum_income") or 0,
            "accI": r.get("acc_income") or "",
            "comI": r.get("comment_income") or "",
            "accF": r.get("acc_from") or "",
            "accT": r.get("acc_to") or "",
            "sumTr": r.get("sum_transfer") or 0,
        })

    formatted_acc = [{"name": a["name"], "balance": a["balance"]} for a in accounts]
    formatted_arch = []
    for a in archive:
        arch_id = str(a["id"])[:8] if a["id"] else "x"
        formatted_arch.append({
            "r": "arch_" + arch_id,
            "d": a["date"],
            "n": a["name"],
            "amt": a["amount"],
            "c": a.get("comment") or "",
        })

    return {
        "config": config,
        "accounts": formatted_acc,
        "transactions": formatted_tx,
        "archiveDebts": formatted_arch,
    }

class Transaction(BaseModel):
    fd: str
    type: str
    catE: Optional[str] = ""
    sumE: float = 0
    accE: Optional[str] = ""
    comE: Optional[str] = ""
    catI: Optional[str] = ""
    sumI: float = 0
    accI: Optional[str] = ""
    comI: Optional[str] = ""
    accF: Optional[str] = ""
    accT: Optional[str] = ""
    sumTr: float = 0

@app.post("/api/transactions/batch")
def batch_add(transactions: List[Transaction]):
    existing = sb_get('transactions', 'order=id.desc&limit=1')
    next_id = (existing[0]['id'] + 1) if existing else 1

    records = []
    for i, t in enumerate(transactions):
        records.append({
            'id': next_id + i, 'date': t.fd, 'type': t.type,
            'cat_expense': t.catE or None, 'sum_expense': t.sumE or None,
            'acc_expense': t.accE or None, 'comment_expense': t.comE or None,
            'cat_income': t.catI or None, 'sum_income': t.sumI or None,
            'acc_income': t.accI or None, 'comment_income': t.comI or None,
            'acc_from': t.accF or None, 'acc_to': t.accT or None,
            'sum_transfer': t.sumTr or None,
        })

    for i in range(0, len(records), 100):
        sb_post('transactions', records[i:i+100])

    for t in transactions:
        if t.type == 'Расход' and t.accE:
            acc = sb_get('accounts', f'name=eq.{urllib.parse.quote(t.accE)}')
            if acc:
                sb_patch('accounts', 'name', urllib.parse.quote(t.accE),
                         {'balance': acc[0]['balance'] - t.sumE})
        elif t.type == 'Доход' and t.accI:
            acc = sb_get('accounts', f'name=eq.{urllib.parse.quote(t.accI)}')
            if acc:
                sb_patch('accounts', 'name', urllib.parse.quote(t.accI),
                         {'balance': acc[0]['balance'] + t.sumI})
    return True

class EditData(BaseModel):
    row: int; date: str; cat: str
    sum: float; comment: str; mode: str

@app.post("/api/transactions/edit")
def edit_transaction(data: EditData):
    update = {'date': data.date}
    if data.mode == 'expense':
        update.update({'cat_expense': data.cat,
                       'sum_expense': data.sum,
                       'comment_expense': data.comment})
    else:
        update.update({'cat_income': data.cat,
                       'sum_income': data.sum,
                       'comment_income': data.comment})
    sb_patch('transactions', 'id', data.row, update)
    return True

@app.get("/api/export-status")
def export_status():
    data = sb_get("transactions", "is_exported=eq.false&select=id")
    return {"unsynced": len(data)}

GAS_URL = "https://script.google.com/macros/s/AKfycbwwQ0_a0ASNi-xWgEl5Ibuu_6kdUVeckCSE50XRfvMsekEDihwe9ecMlw5DnICQlFPx/exec"

@app.post("/api/export-to-sheets")
def export_to_sheets():
    transactions = sb_get("transactions", "is_exported=eq.false")
    if not transactions:
        return {"exported_count": 0, "message": "Нет новых данных"}
    try:
        req = urllib.request.Request(
            GAS_URL,
            data=json.dumps(transactions).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return {"status": "error", "message": f"GAS вернул {resp.status}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    for tx in transactions:
        sb_patch("transactions", "id", tx["id"], {"is_exported": True})
    return {"status": "success", "exported_count": len(transactions)}
