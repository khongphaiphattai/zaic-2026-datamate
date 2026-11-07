# Kế hoạch Phát triển `v3.4` (Post-`v3.3` `0.39100`): Mở rộng diễn đạt, Xếp hạng/Số lượng và Giảm câu sai

> **Trạng thái:** Đề xuất, CHƯA triển khai.
> **Baseline:** `v3.3`, Public `0.39100` = 432 đúng / 41 sai / 527 abstain (precision 91.33%), commit `827b5d0`.
> **Ràng buộc:** Còn **3 lượt nộp**. Hạn chót 16/11/2026 23:59 (GMT+7). Còn 9 ngày.
> **Mục tiêu:** Public **0.45–0.50** (+60 đến +110 điểm ròng) mà **không tăng tuyệt đối số câu sai** (W ≤ 41).

---

## 0. Intent và Acceptance Criteria

**Intent:** Tăng điểm ròng bằng ba đòn bẩy độc lập, đo được riêng:

- **A.** Mở thêm cách diễn đạt cho các phép tính `QueryPlan` đã kiểm chứng.
- **B.** Thêm năng lực xếp hạng, số lượng và tìm ngày, bám đúng định nghĩa trong **rulebook Public**.
- **C.** Tìm và triệt các nguồn sai có hệ thống trên Public, tức 41 câu sai.

**Acceptance Criteria (cổng phát hành):**

| # | Tiêu chí | Ngưỡng |
|---|---|---|
| AC1 | Precision trên **tập holdout** (xem §1) cho mỗi phép tính mới hoặc mở rộng | ≥ 95% (dưới 90% thì tắt phép tính đó) |
| AC2 | Precision toàn Train (30 shop) | Không có câu sai mới so với `v3.3` |
| AC3 | Public: câu `v3.3` đã trả lời mà nay bị đổi giá trị | Mỗi câu đổi phải có lý do truy được (bug fix hoặc luật rulebook); không có câu đổi "vô cớ" |
| AC4 | Public: câu `v3.3` đã trả lời mà nay chuyển sang abstain | Chỉ được phép theo C4 chế độ chặt (bắt buộc có `disagree` ở C2), **≤ 10 câu**, có log lý do |
| AC5 | Định dạng | 1.000 dòng, đúng thứ tự `questions.jsonl`, đúng schema Mục 9, tên file `submission.jsonl` |
| AC6 | Test | `uv run pytest` xanh, `ruff check` sạch |

---

## 1. Bằng chứng nền và bài học từ v3.3

### 1.1. Train không còn dự đoán được Public

- Train báo 1.391/1.391 đúng, **0 sai**, nhưng Public có **41 sai**.
- Nguyên nhân: các luật được viết và chỉnh trên chính nhãn Train. Con số 100% trên Train chỉ là sai số trên tập đã dò, không phải ước lượng tổng quát.
- Hệ quả kinh tế: ở precision 91%, một câu mới chỉ mang về khoảng **+0.82 điểm**. Sửa một câu sai thành đúng mang về **+2**, chuyển câu sai sang abstain mang về **+1**.

**Giao thức holdout bắt buộc cho v3.4:**

1. **(Người dùng đã duyệt 07/11.)** Chọn cố định **6/30 shop Train** làm holdout. Ưu tiên phủ đủ ngành: đồ uống, vật liệu, nhà thuốc, sửa xe, điện thoại, mẹ và bé. Ghi danh sách vào `artifacts/v34-holdout/holdout.json`.
2. Khi phát triển A/B/C **không được xem câu hỏi, log hay đáp án của 6 shop này**. Chỉ chạy đo ở cổng phát hành.
3. Tận dụng [`scripts/benchmark_lbo.py`](file:///home/pt/AI%20Challenge/scripts/benchmark_lbo.py) và `scripts/holdout_training.py`; thêm cờ `--holdout-file`.

### 1.2. Rulebook Public KHÁC rulebook Train (phát hiện mới)

`diff` hai rulebook cho thấy Public có thêm hoặc sửa nhiều luật. Đây là nguồn câu sai và câu mở khóa ứng viên số 1:

| Luật mới/sửa trong rulebook Public | Ảnh hưởng |
|---|---|
| Đơn trả hàng sau kỳ, **kể cả trả sau hôm nay**, vẫn bị loại khỏi doanh thu kỳ đặt | C: kiểm tra contract có lọc theo trạng thái cuối cùng không, hay đang cắt theo ngày trả ≤ as_of |
| Báo cáo tiền mặt theo ca: chỉ tiền mặt, theo **giờ thu tiền**, gồm tiền khách trả nợ; doanh thu/số đơn KHÔNG lấy từ báo cáo ca | C: rà `shift_diff_pct` và câu "tiền bán ca chiều" (dev_007) |
| Trung bình mỗi ngày thứ X: chia cho **số ngày thứ X** trong kỳ, kể cả ngày không có đơn | A/C: rà phép tính trung bình theo thứ |
| Khách trung thành nhất = **nhiều ngày có mua nhất**; Khách sộp nhất = AOV cao nhất, chỉ xét khách **≥ 3 đơn**; Khách hay đổi ý nhất = **nhiều đơn huỷ nhất** | B: định nghĩa ranking khách |
| Mặt hàng được ưa chuộng nhất = có mặt trong **nhiều đơn nhất** (KHÔNG phải số lượng) | B: phân biệt "ưa chuộng" với "bán chạy" |
| Nhân viên/thợ chăm chỉ nhất = **nhiều ngày làm việc nhất** (kể cả phiếu bảo hành) | B (dev_012) |
| Ngày đắt khách nhất = **nhiều đơn nhất** | B: `date_lookup` theo `order_count`, khác "ngày doanh thu cao nhất" |
| Giờ cao điểm = khung 1 giờ tròn theo giờ tạo đơn có nhiều đơn nhất; đáp án là giờ bắt đầu, type `number` | B |
| Nhà cung cấp chính = nhiều phiếu nhập nhất, bỏ phiếu huỷ | Để sau (bảng nhập) |
| Topping ghi chung ô với món: tách thành **hai** mặt hàng | C: xác nhận Bug 4 của v3.3 đã khớp đúng luật này |
| Sổ không có cột trạng thái: trạng thái nằm ở Ghi chú; Ghi chú không nhắc trạng thái thì là đơn hoàn thành | C: xác nhận Bug 3 của v3.3 |
| Mục ngành **Mẹ và bé** (voucher/điểm/ship trả số dương; Số sữa/Size bỉm lấy từ cột danh mục; khách thành viên mới theo Ngày đăng ký) | B/A theo từng câu |
| Mục ngành **Sửa xe** (lượt xe = dòng sổ, tính cả phiếu bảo hành; số chiếc = số biển số khác nhau; tỉ lệ bảo hành...) | A/B cho dev_012 |
| Mục ngành **Điện thoại** (thu máy cũ, trả góp = ghi nợ, dòng máy = phần trước `…GB`, hãng = chữ đầu, iPhone thuộc Apple) | B cho dev_011/dev_014 |

> [!IMPORTANT]
> Bộ test private khả năng cao dùng rulebook giống Public, không giống Train. Mọi phép tính mới phải dẫn chiếu **dòng rulebook Public** tương ứng trong docstring hoặc test.

### 1.3. Phân loại 527 câu abstain (heuristic từ khóa, độ chính xác thấp, chỉ để định hướng)

Script: `brain/.../scratch/classify_abstain.py`. Có thể chuyển vào `scripts/triage_abstain.py` ở Pha 0.

| Nhóm (khớp từ khóa đầu tiên) | Số câu |
|---|---:|
| doanh thu/đơn (nhiều câu thực chất là diễn đạt lạ, doanh thu theo mặt hàng, ca) | 192 |
| khác (gồm ranking/quantity không bắt được từ khóa, lãi ròng, dev_012/013) | 105 |
| công nợ/trả góp | 56 |
| khách hàng | 51 |
| xếp hạng top | 46 |
| tìm ngày | 18 |
| tồn kho/hạn dùng | 17 |
| đổi giá | 17 |
| số lượng (bắt được từ khóa đơn vị) | 16 |
| so sánh/boolean | 8 |
| tăng trưởng | 1 |

Abstain nhiều nhất ở dev_003 (77), dev_006 (69), dev_008 (54), dev_014 (54), dev_004 (48).

---

## 2. Pha 0: Hạ tầng đo lường (làm trước, khoảng 0.5 ngày)

| Task | Chi tiết | File |
|---|---|---|
| 0.1 | Chốt holdout 6 shop, sinh `holdout.json` | `artifacts/v34-holdout/` |
| 0.2 | `scripts/triage_abstain.py`: phân loại abstain theo **lý do abstain thật** do pipeline trả về (operation nào bị chặn, cổng nào), không chỉ từ khóa. Cần pipeline log `abstain_reason` | `semantic_contract.py` (`resolve_semantic_plan`), `scripts/` |
| 0.3 | `scripts/compare_submissions.py`: thêm báo cáo theo pillar (tag `pillar=A/B/C` trên mỗi câu đổi) | `scripts/compare_submissions.py` |
| 0.4 | Thêm trường `rule_ref` (ID dòng rulebook) và `pillar` vào audit log từng câu | `agent.py`, `/result/logs/` |

**Done khi:** chạy một lệnh ra được bảng "abstain theo lý do × shop" và "diff v3.3 → candidate theo pillar".

---

## 3. Trụ cột A: Mở thêm cách diễn đạt cho phép tính đã có (rủi ro thấp)

**Giả thuyết:** Nhiều câu trong 192 + 105 câu abstain đã có phép tính tương ứng (`filtered_revenue`, `filtered_order_count`, `period_growth_pct`, `shift_diff_pct`, `share_pct`...), chỉ bị chặn ở bước parse ý định hoặc thời gian.

**Kiểm chứng giả thuyết trước khi code:** Dùng output của 0.2 đếm số câu bị chặn ở `build_query_plan` (parse fail) so với ở `_check_missing_capability` (thiếu năng lực). Chỉ nhóm parse fail mới thuộc A.

| Task | Nội dung | Ví dụ thật từ Public |
|---|---|---|
| A1 | Diễn đạt tăng trưởng kỳ liền trước: "so tháng liền trước", "so với tháng ngay trước đó", "lên xuống mấy %", "tăng giảm bnhiu %", typo "phần trawm" | `dev_010` "Dthu tháng 2/26 so tháng liền trước, tăng giảm bnhiu %?"; `dev_008` "Tiền bán 6/2026 so tháng liền trước thay đổi mấy phần trawm?" |
| A2 | Doanh thu/số lượng theo **mặt hàng cụ thể**: liên kết tên hàng (rapidfuzz) với `item_key` trong contract, chỉ chấp nhận khi khớp duy nhất (score ≥ ngưỡng, chênh với ứng viên thứ 2 ≥ biên) | `dev_001` "Doanh thu của Bia Heineken 330ml trong tháng 7 năm 2025" |
| A3 | Kỳ đặc biệt: "Tết âm lịch 2026" (lấy từ `calendar.csv`), "N ngày trước Tết", "tuần rồi", "quý X" | `dev_003` "Dịp Tết âm lịch 2026 so với 17 ngày trước Tết" |
| A4 | Typo/teen-code: "nhta", "bnhiu", "zo", "mik", "hnay", "nhiềuđơn" (dính chữ) → bảng chuẩn hóa trong `norm_vn` | nhiều câu |
| A5 | `unanswerable` có căn cứ: "lãi ròng" khi shop không có bảng giá vốn/chi phí; câu cảm tính. **Chỉ** bật khi đã kiểm tra contract thật sự thiếu cột | `dev_003` "Lãi ròng của quán trong 10/2025" |

**Kỹ thuật:**

- Viết test paraphrase trước (TDD) trong `tests/test_order_paraphrases.py`: mỗi biến thể phải ra **cùng `QueryPlan`** với câu chuẩn.
- Không thêm phép tính mới trong A. Nếu cần phép tính mới thì chuyển sang B.

**Rủi ro:** `build_query_plan` (khoảng 600 dòng, [`semantic_contract.py#L1692`](file:///home/pt/AI%20Challenge/src/zaic_2026_datamate/semantic_contract.py#L1692)) dễ phát sinh tác dụng phụ khi thêm regex. Giảm thiểu: chạy full Train sau mỗi task và theo dõi AC2.

**Ước lượng:** +35 đến +60 câu trả lời mới, ròng **+30 đến +50**.

---

## 4. Trụ cột B: Xếp hạng, số lượng và tìm ngày (năng lực mới)

`QueryPlan` đã khai báo `filtered_quantity` và `ranking_top_k` ([`semantic_contract.py#L332`](file:///home/pt/AI%20Challenge/src/zaic_2026_datamate/semantic_contract.py#L332)). Trên Public, v3.3 mới ra 3 câu `string` và 5 câu `date`, cho thấy các nhánh này còn bị chặn gần hết.

### B1. Số lượng chuẩn hóa (`base_quantity`)

- Thêm `quantity` và `base_quantity` vào `canonical_order_lines`. Hệ số quy đổi lấy từ danh mục hàng hóa của từng shop (vỉ/hộp/viên, lốc/lọ, thùng/lon, cây...).
- Câu hỏi theo đơn vị nào thì trả theo đơn vị đó. **Không xác định được hệ số quy đổi thì abstain.**
- Loại đơn huỷ/trả theo §1. Topping tách riêng theo luật mới.
- Ví dụ: `dev_004` "Tháng 5/2026 bán ra bao nhiêu cây Ống PVC Bình Minh D50 cây 4m?", `dev_002` "nhóm trà ... nhiều ly nhất".

### B2. `ranking_top_k` với bảng định nghĩa từ rulebook Public

| Cụm từ | Metric | Rulebook |
|---|---|---|
| bán chạy nhất | `SUM(base_quantity)` | luật chung |
| doanh thu cao nhất (mặt hàng/nhóm) | `SUM(line_revenue)` | luật chung |
| được ưa chuộng nhất | `COUNT(DISTINCT order_id)` chứa mặt hàng | **mới** |
| khách mua nhiều đơn nhất | `COUNT(DISTINCT order_id)` | luật chung |
| khách trung thành nhất | `COUNT(DISTINCT order_date)` | **mới** |
| khách sộp nhất | AOV, chỉ khách ≥ 3 đơn | **mới** |
| khách hay đổi ý nhất | số đơn huỷ | **mới** |
| thợ/nhân viên chăm chỉ nhất | số ngày làm việc (gồm phiếu bảo hành) | **mới** |
| thợ làm nhiều nhất | số lượt xe (gồm bảo hành) | **mới** (Sửa xe) |

- **Hòa hạng:** áp đúng quy tắc tie-break trong rulebook. Nếu rulebook không quy định cho trường hợp đó thì **abstain**, không tự chọn.
- `K = 1` → `string`; `K > 1` → `list` theo thứ tự rulebook.
- **"Lãi gộp nhiều nhất"** chỉ bật khi shop có giá vốn rõ ràng; nếu không thì để `unanswerable` hoặc abstain.

### B3. `date_lookup` và giờ cao điểm

- "Ngày nào doanh thu cao nhất" → `GROUP BY order_date ORDER BY SUM(revenue)`.
- "Ngày đắt khách nhất" → theo **số đơn** (luật mới, khác ở trên).
- "Giờ cao điểm" → `number` = giờ bắt đầu khung.
- Ví dụ: `dev_005`, `dev_011`, `dev_010` "ngày nào doanh thu cao nhất".

### B4. Một số phép tính ngành có công thức rõ (chọn lọc)

Chỉ làm khi phép tính dùng một bảng và công thức nằm hẳn trong rulebook:

- **Sửa xe (dev_012):** số lượt xe, số chiếc (biển số khác nhau), số lượt theo thợ.
- **Điện thoại:** tổng tiền thu máy cũ của kỳ (đơn không huỷ, không trả); số máy theo hãng/dòng máy (dòng máy = phần trước `…GB`).
- **Mẹ và bé:** tiền voucher/điểm/ship (số dương, theo ngày hoá đơn); khách thành viên mới theo Ngày đăng ký.

**Không** làm trong v3.4: trả góp quá hạn, tồn kho/lô hết hạn, so sánh trước/sau đổi giá, hoa hồng thợ.

**Kỹ thuật:**

- Mỗi định nghĩa có một test fixture tổng hợp nhỏ kiểm cả nhánh hòa hạng, nhánh đơn huỷ và nhánh thiếu cột (phải abstain).
- Mỗi phép tính mới có cờ bật/tắt trong config để có thể tắt riêng nếu holdout precision < 90%.

**Ước lượng:** +60 đến +90 câu trả lời mới, precision kỳ vọng 80–90%, ròng **+40 đến +70**.

---

## 5. Trụ cột C: Tìm và giảm 41 câu sai

Không có nhãn Public, nên phải dò bằng các tín hiệu gián tiếp.

### C1. Đối chiếu rulebook Public với code (ưu tiên cao nhất, rẻ)

- Với **từng dòng khác biệt** trong bảng §1.2, xác định code hiện tại xử lý thế nào và lập bảng `rule → hàm → khớp/lệch`.
- Ưu tiên kiểm tra:
  1. Đơn trả sau kỳ/sau hôm nay.
  2. Báo cáo tiền mặt theo ca so với doanh thu theo giờ đặt.
  3. Trung bình theo thứ trong tuần.
  4. Sổ không cột trạng thái.
  5. Mục ngành Điện thoại về thu máy cũ.
- Mỗi chỗ lệch: viết test đỏ → sửa → ghi lại các câu Public bị đổi giá trị.

### C2. Kiểm chéo hai đường tính độc lập

- Với các phép tính phổ biến nhất (`filtered_revenue`, `filtered_order_count`, `period_growth_pct`, `share_pct`), viết **đường tính thứ hai** đi thẳng từ DataFrame đã làm sạch bằng SQL DuckDB tối giản, không qua `canonical_orders`.
- Hai đường lệch nhau quá dung sai làm tròn thì gắn cờ `disagree`.
- Câu có `disagree`: **mặc định abstain**. Đồng thời đưa vào danh sách điều tra thủ công để tìm bug gốc. Nếu tìm ra bug thì sửa để chuyển sai thành đúng (+2).

### C3. Soát dữ liệu theo shop dev

- Sinh profile dữ liệu cho 14 shop dev: cột trạng thái lấy từ đâu, số sheet, khoảng năm, tỉ lệ dòng bị ffill, số dòng có `item_name` rỗng, số mã đơn trùng giữa các sheet.
- So với phân phối của 30 shop Train. Shop nào có đặc điểm **chưa từng thấy trên Train** thì đánh dấu rủi ro cao.
- Có thể dùng lại `artifacts/v32-*-replay/profiles.json`.

### C4. Chấm rủi ro và hạ xuống abstain có kiểm soát

- Điểm rủi ro mỗi câu = f(shop rủi ro cao ở C3, phép tính chưa từng được holdout xác nhận, có fuzzy match tên hàng sát ngưỡng, có `disagree` ở C2).
- **Quyết định của người dùng (07/11): áp chế độ CHẶT.**
  - Một câu `v3.3` đã trả lời chỉ bị hạ xuống abstain khi **bắt buộc có `disagree` ở C2** (bằng chứng trực tiếp: hai đường tính độc lập cho kết quả khác nhau) **và** có thêm ít nhất 1 tín hiệu rủi ro khác (shop rủi ro cao ở C3, hoặc fuzzy match sát ngưỡng).
  - Điểm rủi ro heuristic đơn thuần, không kèm `disagree`, **không đủ** để hạ.
  - Ngưỡng chọn trên holdout, không chọn trên Public.
  - **Trần cứng: tối đa 10 câu Public bị hạ** trong toàn bộ v3.4. Vượt trần thì ưu tiên điều tra để tìm bug gốc (sửa thành đúng được +2) thay vì hạ thêm.
  - Mỗi câu bị hạ phải ghi `abstain_reason` + 2 kết quả của hai đường tính vào `artifacts/v34a-replay/downgrades.json`.

**Ước lượng:** ròng **+10 đến +30**. Mỗi bug hệ thống tìm được ở C1/C2 thường kéo theo cả cụm câu.

---

## 6. Chiến lược 3 lượt nộp

Public chỉ trả về một con số, nên dùng các lượt nộp để **đo riêng từng phần** và giữ một lượt dự phòng.

| Lượt | Nội dung | Mục đích | Điều kiện nộp |
|---|---|---|---|
| **#17** | `v3.4a` = v3.3 + **A + C** | Đo phần rủi ro thấp; xác nhận C không làm giảm điểm | AC1–AC6 đạt |
| **#18** | `v3.4b` = v3.4a + **B** | Hiệu điểm (#18 − #17) cho biết ròng của B | Holdout precision B ≥ 90% |
| **#19** | Dự phòng / bản cuối | Bản tốt nhất, hoặc v3.4b đã tắt các phép tính B có tín hiệu xấu | Chỉ dùng khi có thay đổi đáng giá |

**Quy tắc quyết định:**

- Nếu #17 **thấp hơn** 0.391: C đang hạ nhầm câu đúng hoặc A có lỗi. Tách A/C trên holdout trước khi nộp tiếp.
- Nếu (#18 − #17) < +0.02: B có precision thực khoảng ≤ 60%. Tắt các phép tính B mới nhất, giữ lại phần đã có bằng chứng.
- **Không** dùng lượt nộp để dò từng câu sai. 3 lượt quá ít cho việc đó.

> [!NOTE]
> Điểm Public chỉ để định hướng. Kết quả cuối được tính trên bộ test được chạy lại. Ưu tiên luật tổng quát bám rulebook, tránh luật khớp riêng dữ liệu Public.

---

## 7. Lịch dự kiến (08/11 → 16/11)

| Ngày | Việc |
|---|---|
| 08/11 | Pha 0 (holdout, triage theo lý do abstain, log `pillar`/`rule_ref`) + C1 (đối chiếu rulebook) |
| 09/11 | A1–A5 + C2 cho 4 phép tính phổ biến |
| 10/11 | C3–C4, cổng phát hành → **nộp #17 (`v3.4a`)** |
| 11–12/11 | B1 (`base_quantity`) + B2 (ranking theo bảng định nghĩa) |
| 13/11 | B3 + B4 chọn lọc, holdout → **nộp #18 (`v3.4b`)** |
| 14–15/11 | Phân tích hiệu điểm, tắt phần xấu, đóng gói Docker offline, kiểm tái lập (`temperature=0`, `seed=42`) |
| 16/11 | **#19** (nếu cần), freeze trước 23:59 |

---

## 8. Phân tích kịch bản

| Kịch bản | Mô tả | Tín hiệu nhận biết | Hành động |
|---|---|---|---|
| Ω Thuận lợi | C1 tìm ra 1–2 bug hệ thống từ rulebook diff | Nhiều câu Public đổi giá trị theo cụm, có lý do rõ | Có thể vượt 0.50 |
| α Tham chiếu | A và B chạy đúng ước lượng | Holdout precision B khoảng 85–90% | Khoảng 0.46–0.48 |
| Δ Bất lợi | B có precision thấp do hòa hạng và quy đổi đơn vị | (#18 − #17) < +0.02 | Tắt B mới, nộp #19 = v3.4a + B đã lọc |
| Ψ Bất ngờ | Sửa luật rulebook làm đổi hàng loạt câu v3.3 đúng thành sai | #17 < 0.391 | Rollback từng luật, đối chiếu lại câu chữ rulebook |
| ∞ Bậc hai | `build_query_plan` phình to, khó bảo trì cho v3.5 | Mỗi task A gây hồi quy Train | Sau v3.4, tách `build_query_plan` thành bộ nhận diện theo từng phép tính |

**Rủi ro còn lại:** Holdout 6 shop (khoảng 600 câu) cho khoảng tin cậy precision rộng với các phép tính hiếm. Phép tính nào có dưới 15 câu trên holdout thì coi là "chưa xác nhận" và tính vào điểm rủi ro C4.

---

## 9. File dự kiến chạm tới

- `src/zaic_2026_datamate/semantic_contract.py`: contract (`base_quantity`), `build_query_plan`, `execute_query_plan`, đường tính thứ hai.
- `src/zaic_2026_datamate/intent_parser.py`: diễn đạt, typo, kỳ đặc biệt.
- `src/zaic_2026_datamate/data_loader.py`: chỉ khi C1/C3 phát hiện lỗi làm sạch.
- `src/zaic_2026_datamate/agent.py`: log `abstain_reason`, `pillar`, `rule_ref`.
- `scripts/triage_abstain.py` (mới), `scripts/compare_submissions.py`, `scripts/benchmark_lbo.py`.
- `tests/test_order_paraphrases.py`, `tests/test_semantic_contract.py`, `tests/test_ranking_definitions.py` (mới), `tests/test_rulebook_public_rules.py` (mới).
- Artifact: `artifacts/v34a-replay/`, `artifacts/v34b-replay/`, `artifacts/v34-holdout/`.
