import json, re, unicodedata
from collections import Counter

def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    return s

CATS = [
    ("compare_growth", r"(so voi|hon hay kem|tang hay giam|tang|giam).*(%|phan tram|may %)|cung ky"),
    ("shift", r"\bca (sang|chieu)\b"),
    ("top_n", r"\b(top\s*\d|\d+\s*(mon|san pham|mat hang)\s*(ban chay|nhieu nhat)|ban chay nhat|nhieu nhat|cao nhat)\b"),
    ("payment", r"\b(tien mat|chuyen khoan|quet the|cod|vi dien tu)\b"),
    ("debt", r"\b(no|con no|cong no|ban chiu|thieu tien)\b"),
    ("customer", r"\bkhach\b"),
    ("profit", r"\b(lai|loi nhuan|lai gop|lai rong|huê hong|hue hong)\b"),
    ("product_qty", r"\b(bao nhieu|may|bn|bnhiu)\s+(ly|cay|chai|hop|goi|cai|chiec|vien|phan)\b"),
]

def census(qpath, subpath=None):
    qs = [json.loads(l) for l in open(qpath)]
    sub = {}
    if subpath:
        sub = {json.loads(l)["id"]: json.loads(l) for l in open(subpath)}
    c_all, c_abs = Counter(), Counter()
    for q in qs:
        t = norm(q["question"])
        cat = next((n for n, p in CATS if re.search(p, t)), "other")
        c_all[cat] += 1
        if sub and sub[q["id"]]["type"] == "abstain":
            c_abs[cat] += 1
    return c_all, c_abs

for name, qp, sp in [("TRAIN", "data/train/train/questions.jsonl", None),
                     ("PUBLIC", "data/public_test/public_test/questions.jsonl", "submission.jsonl")]:
    a, b = census(qp, sp)
    print(f"== {name} ==")
    for k in sorted(a, key=lambda k: -a[k]):
        print(f"{k:16s} total={a[k]:4d}" + (f"  abstained={b[k]:4d}" if sp else ""))
