import json
import re
import unicodedata
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
    ("profit", r"\b(lai|loi nhuan|lai gop|lai rong|hue hong)\b"),
    ("product_qty", r"\b(bao nhieu|may|bn|bnhiu)\s+(ly|cay|chai|hop|goi|cai|chiec|vien|phan)\b"),
]

with open("data/public_test/public_test/questions.jsonl") as f:
    pub_qs = [json.loads(l) for l in f]

with open("submission.jsonl") as f:
    sub = {json.loads(l)["id"]: json.loads(l) for l in f}

# Filter to unclassified abstained questions
unclassified = []
for q in pub_qs:
    qid = q["id"]
    if sub[qid]["type"] != "abstain":
        continue
    t = norm(q["question"])
    cat = next((n for n, p in CATS if re.search(p, t)), None)
    if cat is None:
        unclassified.append(q)

print(f"Tổng số câu chưa phân loại đang bị Abstain trên Public Test: {len(unclassified)}")

# Let's inspect frequency of keywords / question types
words_counter = Counter()
bigrams_counter = Counter()
trigrams_counter = Counter()

patterns = {
    "nhap_hang": r"\b(nhap|ncc|nha cung cap|tien hang nhap|chi phi nhap)\b",
    "ton_kho": r"\b(ton kho|con lai|con ton|so luong ton|ton|kho)\b",
    "don_huy_hoan": r"\b(huy|huỷ|doi tra|tra hang|khong giao duoc|hoan)\b",
    "giam_gia_voucher": r"\b(giam gia|voucher|chiet khau|khuyen mai|bot)\b",
    "gia_ca": r"\b(gia ban|gia von|gia cu|gia moi|don gia|tang gia|giam gia)\b",
    "thoi_gian_ngay": r"\b(ngay nao|hom nao|thu may|gio nao|thoi diem)\b",
    "so_don_theo_dk": r"\b(bao nhieu don|may don|tong so don|bn don)\b",
    "hoa_don_chi_tiet": r"\b(hoa don|bill|phieu)\b",
    "nhan_vien": r"\b(nhan vien|nv|tho|thợ|nguoi ban|ca lam)\b",
    "san_pham_rieng": r"\b(doanh thu cua|tien ban cua|thu ve bao nhieu tien)\b",
    "chuoi_thang_lien": r"\b(3 thang lien|cac thang|ke giup|tung thang)\b",
    "dia_chi_khu_vuc": r"\b(duong|quan|huyen|tinh|thanh pho|khu vuc|dia chi)\b"
}

pat_counts = Counter()
classified_sub = set()
for q in unclassified:
    t = norm(q["question"])
    matched_any = False
    for pat_name, pat_regex in patterns.items():
        if re.search(pat_regex, t):
            pat_counts[pat_name] += 1
            matched_any = True
    if not matched_any:
        pat_counts["con_lai_that_su"] += 1

print("\n--- PHÂN TÍCH TẦN SUẤT THEO NHÓM Ý ĐỊNH TRONG 454 CÂU ---")
for p, c in pat_counts.most_common():
    print(f"{p:25s}: {c:4d} câu")

# Show samples of the top groups
print("\n--- MẪU 5 CÂU MỖI NHÓM ĐẦU BẢNG ---")
for pat_name in ["nhap_hang", "don_huy_hoan", "so_don_theo_dk", "nhan_vien", "san_pham_rieng", "ton_kho", "thoi_gian_ngay"]:
    print(f"\n>>> Nhóm: {pat_name}")
    pat_regex = patterns[pat_name]
    matching = [q for q in unclassified if re.search(pat_regex, norm(q["question"]))]
    for q in matching[:5]:
        print(f"  [{q['business_id']}] {q['id']}: {q['question']}")
