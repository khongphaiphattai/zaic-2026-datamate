# Kế hoạch Phát triển `v3.3` (Post-`v3.2` `0.27300`) — Khử Lỗi Public Test & Mở Rộng Năng Lực `BusinessSemanticContract`

> **Ngày lập kế hoạch:** 07/11/2026  
> **Trạng thái hiện tại (`v3.2`):**
> - **Train (`3.000` câu / 30 doanh nghiệp):** `1.268` trả lời / **`1.268` đúng / `0` sai** / `1.732` abstain (**Precision = `100.00%`**, **PA = `+0.4227`**).
> - **Public Test (`1.000` câu / 14 doanh nghiệp `dev_001`..`dev_014`):** `379` trả lời / `621` abstain $\rightarrow$ **Public Score = `0.27300`** (**`326` câu ĐÚNG / `53` câu SAI**, **Precision = `86.02%`**).
> - **Mục tiêu của `v3.3`:**
>   1. **Pha 1 (Precision Fix — Sửa ~25–35 câu đang sai trong 379 câu của `v3.2`):** Mỗi câu sửa từ Sai (-1) sang Đúng (+1) mang lại **+2 điểm ròng (+0.00200)**. Sửa 25–30 câu sai sẽ đẩy điểm từ `0.27300` lên **`~0.32500 – 0.34000`** ngay cả khi chưa mở thêm câu mới.
>   2. **Pha 2 (Coverage Expansion trên `BusinessSemanticContract` — Mở khóa 4 nhóm câu hỏi lớn từ 621 câu đang `abstain`):** Mở thêm **120–180 câu trả lời mới** với độ chính xác $\ge 98\%$ trên Train, hướng tới mốc **`0.42000 – 0.50000+`**.

---

## 🔍 I. PHÂN TÍCH PHÁP Y (FORENSIC AUDIT): TẠI SAO TRAIN ĐẠT 100% (1.268/1.268) NHƯNG PUBLIC TEST CÓ 53 CÂU SAI?

Qua rà soát toàn bộ `data/public_test/public_test/rulebook.md` và 14 file `data/public_test/public_test/data/dev_*/README.md`, nguyên nhân cốt lõi khiến Train đạt 0 lỗi nhưng Public có 53 câu sai là vì **Public Test bổ sung 3 ngành hàng mới hoàn toàn chưa có trong Train** (`Mẹ & bé`: `dev_010`, `dev_013`; `Cửa hàng điện thoại`: `dev_011`, `dev_014`; `Tiệm sửa xe`: `dev_012`) cùng các quy ước đặt tên cột/dòng tổng kết đặc thù.

Cụ thể, chúng ta đã xác minh được **5 lỗi kỹ thuật (Smoking Guns) ảnh hưởng trực tiếp tới >25 câu trong 379 câu đã trả lời của `v3.2`**:

### 1. Bug 1 (`data_loader.py` dòng 212 & 219): Chết biến `sheet_year` do đổi tên cột `_sheet_name` $\rightarrow$ `sheet_name` và không tách năm theo từng sheet
- **Hiện trạng:** Ở dòng 200–212 của [`src/zaic_2026_datamate/data_loader.py`](file:///home/pt/AI%20Challenge/src/zaic_2026_datamate/data_loader.py#L200-L224), `normalize_identifier("_sheet_name")` đổi tên cột thành `"sheet_name"`. Ngay sau đó ở dòng 219, code lại kiểm tra `if "_sheet_name" in cleaned_df.columns:`, dẫn tới điều kiện này **luôn luôn `False`** và `sheet_year` luôn bằng `None`!
- **Hậu quả nghiêm trọng hơn khi gộp nhiều sheet tháng (2025 + 2026):** Kể cả khi đọc được cột `"sheet_name"`, nếu chỉ lấy năm của `iloc[0]` (sheet đầu tiên, năm 2025) thì các dòng ngày không ghi năm (`dd/mm`) ở các sheet năm 2026 sẽ bị gán nhầm thành năm 2025 hoặc thành `NaT` rồi bị `.ffill()` lấy ngày cuối cùng của tháng trước!
- **Cách sửa (Pha 1.1):** Trong `clean_dataframe()`, nếu có cột `"sheet_name"`, phải `groupby("sheet_name", sort=False)` để trích xuất `sheet_year` riêng cho từng sheet tháng trước khi gọi `parse_mixed_excel_dates(grp[clean_col], sheet_year=syear)`.

### 2. Bug 2 (`data_loader.py` dòng 142): Dòng chân trang `"Ghi chú: ..."` không bị loại bởi `drop_subtotal_rows`
- **Hiện trạng:** Các sheet Excel của `dev_004` (26 sheet), `dev_013` (20 sheet), `dev_014` (24 sheet) có dòng chú thích ở cuối bảng bắt đầu bằng `"Ghi chú: ..."`.
- **Hậu quả:** Vì `SUBTOTAL_ROW_PATTERN` chưa chặn `^ghi\s*ch[uú]\s*:`, các dòng này tồn tại sau bước lọc, rồi bị cơ chế `ffill()` gán mã đơn hàng và ngày của đơn cuối tháng vào, tạo ra các dòng hàng "ma" (`item_name = ''`, `category = None`).
- **Cách sửa (Pha 1.2):** Bổ sung `ghi\s*ch[uú]\s*:` vào `SUBTOTAL_ROW_PATTERN` trong `data_loader.py`.

### 3. Bug 3 (`data_loader.py` dòng 288–316): Thiếu `"stt"` trong `order_id_cols` và thiếu `"ghi_chu"`, `"kenh"`, `"kenh_ban"` trong `order_header_cols` / `order_scoped_headers`
- **Hiện trạng:**
  - Tại `dev_011` và `dev_014` (Cửa hàng điện thoại), cột định danh đơn hàng là `stt` (`STT`). Vì `"stt"` chưa nằm trong `order_id_cols`, các đơn hàng mua từ 2 món trở lên có dòng thứ 2 để trống `stt` (`NaN`) không được `ffill()`. Khi vào `semantic_contract.py`, tất cả các dòng thứ 2 (`stt = NaN`) bị gom chung thành một đơn hàng giả `'nan'` hoặc bị lệch doanh thu nhóm hàng (`máy mới`, `máy cũ`, `phụ kiện`) hàng trăm triệu đồng!
  - Tại `dev_011`, `dev_013`, `dev_014`, trạng thái huỷ/trả (`cancel`, `đổi trả`, `trả hàng`) được ghi ở cột `ghi_chu` chỉ tại dòng đầu của đơn, và kênh bán (`Zalo OA`, `Sàn TMĐT`, ...) ghi ở cột `kenh` chỉ tại dòng đầu của đơn. Vì `"ghi_chu"` và `"kenh"` chưa có trong `order_header_cols` và `order_scoped_headers`, các dòng thứ 2, thứ 3 của đơn nhiều món bị mất trạng thái huỷ (`ghi_chu = NaN` $\rightarrow$ vẫn tính vào doanh thu!) và mất tên kênh bán (`kenh = NaN` $\rightarrow$ tính thiếu doanh thu kênh!).
- **Bằng chứng thực nghiệm:** Riêng việc sửa Bug 1 + Bug 2 + Bug 3 đã làm thay đổi đáp án của **25 câu trên `dev_011`, `dev_013`, `dev_014`** (ví dụ: `q_51621c1657c5` nhóm máy mới tháng 3/2026 tại `dev_011` từ `737.145.000` về đúng `1.074.473.000`; `q_6d9ad1c4e4fecd` nhóm máy cũ tháng 6/2025 tại `dev_011` từ `1.345.065.000` về đúng `168.700.000`).

### 4. Bug 4 (`semantic_contract.py` dòng 640–710): Dòng đồ uống có kèm `+ Topping` tại `dev_002` bị gán `category = None`
- **Hiện trạng:** Trong `dev_002`, có **1.095 dòng** ghi gộp đồ uống và topping trên cùng một dòng (ví dụ: `"Cà phê sữa (L) + Trân châu đen (7.000đ)"`). Hiện tại `semantic_contract.py` đã tách phần tiền `7.000đ` sang một dòng mới có `category = 'topping'` và trừ `7.000đ` khỏi dòng đồ uống gốc, **nhưng lại giữ nguyên `item_name` của dòng gốc là `"Cà phê sữa (L) + Trân châu đen (7.000đ)"`**.
- **Hậu quả:** Khi tra từ điển `CAFE_CATEGORY_LOOKUP`, chuỗi có đuôi `+ Trân châu đen (7.000đ)` không khớp với `"cà phê sữa (l)"`, khiến toàn bộ **1.072 dòng đồ uống hợp lệ có kèm topping tại `dev_002` bị gán `category = None`**! Mọi câu hỏi về doanh thu hoặc tỷ trọng `%` nhóm `cà phê`, `trà`, `trà sữa`, `sinh tố`, `nước ép`, `đá xay` tại `dev_002` đều bị hụt mất các ly có gọi thêm topping (lệch mất `~0.02% – 1.5%` tỷ trọng và lệch tiền nhóm món).
- **Cách sửa (Pha 1.4):** Khi tách dòng `+ Topping` tại `_build_from_combined_table` (`semantic_contract.py`), chuẩn hóa `base_item_name = raw_name.split("+")[0].strip()` cho dòng đồ uống gốc để tra đúng `category` và `item_key`.

### 5. Bug 5 (`dev_011` & `dev_014` — Quy tắc doanh thu cửa hàng điện thoại có `bớt` và `thu máy cũ` theo Rulebook & README):
- **Hiện trạng:** Trong `dev_011/README.md` và `dev_014/README.md`:
  - Cột `Khách trả` là tiền khách trả cho **cả đơn** (không phải của dòng), đã trừ khoản `Bớt` (giảm giá toàn đơn) và đã trừ `Thu máy cũ`.
  - Theo `rulebook.md` Mục 1: *"Doanh thu một đơn = tổng thành tiền các dòng hàng sau giảm giá dòng, KHÔNG trừ giảm giá chung của đơn (`Bớt`) và KHÔNG trừ tiền thu máy cũ (`Thu máy cũ`), vì thu máy cũ chỉ là hình thức thanh toán cấn trừ"*.
- **Cách sửa (Pha 1.5):** Kiểm tra kỹ cách `intent_parser.py` và `semantic_contract.py` tính doanh thu đơn/dòng tại `dev_011` và `dev_014` (`so_ban_hang`): đảm bảo tính từ `so_luong * don_gia` (hoặc `gia_ban` dòng hàng trước khi trừ `bot` toàn đơn và trước khi trừ `thu_may_cu`), không dùng cột `khach_tra` (vốn là tiền thanh toán cả đơn đã trừ bớt & thu máy cũ và bị lặp/ghi ở dòng đầu).

---

## 🚀 II. PHÂN TÍCH 621 CÂU ĐANG `ABSTAIN` TRÊN PUBLIC TEST (VÀ 1.732 CÂU TRÊN TRAIN)

Phân loại tự động toàn bộ **621 câu đang `abstain` trên Public Test** (và **1.732 câu `abstain` trên Train**) cho thấy bức tranh cơ hội rất rõ ràng:

| Hạng ưu tiên | Nhóm câu hỏi đang `abstain` | Số câu trên Train (`1.732`) | Số câu trên Public (`621`) | Độ khó kỹ thuật | Đòn bẩy kiến trúc |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **Pha 2.1 (Dễ & Ăn chắc 100%)** | **Mở rộng từ viết tắt / phương ngữ trên các mẫu `QueryPlan` đã có sẵn** (`"dthu"`, `"tiền bán"`, `"đh"`, `"8/26"`, `"tháng ni"`, `"30 ngày gần nhất"`, `"phiếu sửa xe"`, lỗi gõ phím `"ban duco"`, `"lãi ròng"`/`"thân thiện nhất"` $\rightarrow$ `unanswerable`) | ~140 câu | **~55–65 câu** | Rất thấp | Chỉ cần mở rộng bộ nhận diện từ khóa/thời gian trong `intent_parser.py` & `semantic_contract.py`, chạy thẳng trên `canonical_orders` / `canonical_order_lines`. |
| **Pha 2.2 (Lợi nhuận cao)** | **Tăng trưởng / So sánh kỳ này vs kỳ trước (`growth_comparison` — `percent` & `number`)** trên mọi kỳ (`tháng`, `tuần`, `quý`, `MTD`, `cùng kỳ`, `nhóm hàng`) chạy trực tiếp trên `canonical_orders` | ~110 câu | **~68 câu** | Trung bình thấp | Hiện tại `monthly_growth` / `mtd_growth` cũ bị chặn bởi cổng README cứng (chỉ mở được 1 câu trên Public!). Nếu chuyển sang chạy trên `canonical_orders` của `BusinessSemanticContract`, mở khóa ngay hàng chục câu tăng trưởng `%`! |
| **Pha 2.3 (Mỏ điểm lớn)** | **Số lượng bán theo mặt hàng / nhóm hàng & Quy đổi đơn vị cơ bản (`item_quantity` — `number`)** (ví dụ: *"Số ly Nước ép cam (M)"*, *"Xiaomi Redmi Note 14 bán được bao nhiêu chiếc"*, *"bán ra bao nhiêu lon sữa bột số 4"*) | ~190 câu | **~65–75 câu** | Trung bình | Thêm cột `quantity` và `base_quantity` (nhân hệ số quy đổi từ `README.md` / bảng danh mục hàng hóa) vào `canonical_order_lines`. |
| **Pha 2.4 (Mỏ điểm lớn)** | **Xếp hạng Top 1 / Top K (`ranking_top_bottom` — `string`, `list`, `date`)** (mặt hàng bán chạy nhất theo số lượng quy đổi, mặt hàng/nhóm hàng doanh thu cao nhất, khách mua nhiều nhất, ngày/giờ cao điểm nhất) | ~180 câu | **~65–75 câu** | Trung bình | Khi `canonical_order_lines` đã có `base_quantity` và `line_revenue`, mọi câu Top 1 / Top K chỉ là `GROUP BY item_key ORDER BY metric DESC, item_code ASC LIMIT K` theo đúng Rulebook Mục 9! |
| **Pha 2.5 (Chuyên biệt)** | **Tìm Ngày (`date_lookup` — `date`) & Kiểm tra Đúng/Sai (`boolean`)** | ~90 câu | **~30 câu** | Trung bình | Truy vấn `canonical_orders` theo ngày hoặc kiểm tra bất đẳng thức giữa 2 kỳ/2 nhóm. |
| *Để sau (Rủi ro cao)* | *Công nợ / Trả góp (`debt_receivable`), Tồn kho / Hạn dùng (`inventory`), Trước/Sau đổi giá (`price_change`), Hoa hồng thợ sửa xe (`dev_012`)* | ~400 câu | ~120 câu | Cao (nhiều bảng phụ) | Chỉ làm những mẫu đơn giản có công thức rõ ràng (như tiền thu máy cũ trừ vào đơn tại `dev_011`/`dev_014`). |

---

## 📋 III. KẾ HOẠCH TRIỂN KHAI CHI TIẾT CHO PHIÊN TIẾP THEO (BÀN GIAO CHO GEMINI 3.8 FLASH / 4 ARGON)

Khi bắt đầu phiên làm việc tiếp theo, thực hiện tuần tự theo **4 bước nguyên tử (Atomic TDD Steps)** dưới đây, **sau mỗi bước đều chạy `pytest`, đo lại trên 3.000 câu Train + 1.000 câu Public Test và ghi ngay vào `ROADMAP.md`**:

### Bước 1 (Ưu tiên #1 — Sửa 5 Bug Pháp Y trên Public Test):
1. Sửa `clean_dataframe()` trong [`src/zaic_2026_datamate/data_loader.py`](file:///home/pt/AI%20Challenge/src/zaic_2026_datamate/data_loader.py):
   - Bổ sung `ghi\s*ch[uú]\s*:` vào `SUBTOTAL_ROW_PATTERN` (loại dòng chân trang `"Ghi chú: ..."` ở `dev_004`, `dev_013`, `dev_014`).
   - Sửa trích xuất `sheet_year` theo từng nhóm `"sheet_name"` (`groupby("sheet_name", sort=False)`), không dùng `"_sheet_name"` đã bị đổi tên và không lấy năm của `iloc[0]` áp cho cả bảng gộp 2025–2026.
   - Bổ sung `"stt"` vào `order_id_cols` & `order_header_cols`; bổ sung `"ghi_chu"`, `"kenh"`, `"kenh_ban"` vào `order_header_cols` & `order_scoped_headers`.
2. Sửa `_build_from_combined_table()` trong [`src/zaic_2026_datamate/semantic_contract.py`](file:///home/pt/AI%20Challenge/src/zaic_2026_datamate/semantic_contract.py):
   - Khi tách dòng có `+ Topping` ở `dev_002`, cắt bỏ phần `+ ...` khỏi tên món gốc khi gán `item_key` và tra `CAFE_CATEGORY_LOOKUP` để toàn bộ 1.072 dòng đồ uống có topping tại `dev_002` nhận đúng `category` (`cà phê`, `trà`, `trà sữa`, `sinh tố`, `nước ép`, `đá xay`).
   - Kiểm tra cách tính `order_revenue` và `line_revenue` tại `dev_011` và `dev_014` (`so_ban_hang` + sheet `Ghi tắt`): đảm bảo doanh thu đơn = tổng tiền hàng các dòng trong đơn (không trừ `bớt` toàn đơn, không trừ `thu_may_cu`), và ánh xạ đúng nhóm hàng (`máy mới`, `máy cũ`, `phụ kiện`) từ sheet `Ghi tắt`.
3. **Tiêu chí nghiệm thu Bước 1:**
   - Viết unit test hồi quy trong `tests/test_data_loader.py` và `tests/test_semantic_contract.py`.
   - Chạy lại toàn bộ 3.000 câu Train: đảm bảo **0 câu sai (100% Precision)** và kiểm tra danh sách câu được sửa trên Public Test (`dev_002`, `dev_004`, `dev_011`, `dev_013`, `dev_014`).

### Bước 2 (Ưu tiên #2 — Mở rộng Nhận diện Ngôn ngữ & Tăng trưởng trên `BusinessSemanticContract`):
1. Mở rộng từ viết tắt & kỳ thời gian trong `parse_period` (`intent_parser.py`) và `build_query_plan` (`semantic_contract.py`):
   - Hỗ trợ `"8/26"`, `"th 3/26"` (tháng/năm 2 chữ số `MM/YY` $\rightarrow$ `20YY`), `"tháng ni"`, `"bữa ni"`, `"N ngày gần nhất"` (`"30 ngày gần nhất"`, `"7 ngày qua"` tính từ `as_of_date - (N-1)` đến `as_of_date`).
   - Hỗ trợ từ viết tắt doanh thu/đơn hàng: `"dthu"`, `"tiền bán"`, `"tien ban"`, `"bán duco"`, `"đh"`, `"phiếu sửa xe"` (tại `dev_012`, trừ dòng bảo hành).
   - Bổ sung Gate 1 `unanswerable` cho câu hỏi `"lãi ròng"` / `"chi phí"` (khi shop không có bảng chi phí) và câu hỏi cảm tính không có trong dữ liệu (`"phục vụ khách thân thiện nhất"`).
2. Tích hợp phép toán `period_growth_pct` vào `QueryPlan` (`semantic_contract.py`):
   - Tính tăng trưởng `%` doanh thu hoặc số đơn giữa kỳ hiện tại và kỳ liền trước (`tháng này vs cùng kỳ tháng trước`, `tháng M vs tháng M-1`, `tuần này vs tuần trước`, `quý này vs quý trước`, kể cả khi có lọc theo nhóm hàng/kênh/ca) trực tiếp trên `canonical_orders` và `canonical_order_lines`.
3. **Tiêu chí nghiệm thu Bước 2:**
   - Trên Train (`3.000` câu): tăng thêm **+80 đến +120 câu đúng**, giữ nguyên **0 câu sai** (hoặc Precision $\ge 99.5\%$).

### Bước 3 (Ưu tiên #3 — Bổ sung `base_quantity` vào `canonical_order_lines` & Mở khóa Nhóm Số lượng + Xếp hạng Top/Bottom):
1. Bổ sung cột `quantity` và `base_quantity` (quy đổi đơn vị cơ bản từ bảng danh mục hàng hóa / `README.md` của shop) vào `canonical_order_lines` trong `semantic_contract.py`.
2. Mở khóa 2 phép toán mới trong `QueryPlan`:
   - `filtered_quantity` (`number`): Đếm số lượng bán (theo đơn vị cơ bản hoặc đơn vị câu hỏi yêu cầu) của một mặt hàng hoặc nhóm hàng trong kỳ.
   - `ranking_top_k` (`string` khi $K=1$, `list` khi $K>1$, `date` khi xếp hạng ngày/tháng): Xếp hạng mặt hàng bán chạy nhất (theo `SUM(base_quantity)`), mặt hàng/nhóm hàng/khách hàng có doanh thu cao nhất (theo `SUM(line_revenue)`), khách mua nhiều đơn nhất (theo `COUNT(DISTINCT order_id)`), với quy tắc tie-break chuẩn Rulebook Mục 9 (`ORDER BY metric DESC, item_code ASC`).

### Bước 4 (Đóng gói & Xuất bản `v3.3`):
- Xuất báo cáo đối sánh `artifacts/v33-replay/` (`summary.json`, `public-diffs.json`, `train-wrong.json`, `submission.jsonl`), cập nhật [`ROADMAP.md`](file:///home/pt/AI%20Challenge/ROADMAP.md) và trình người dùng duyệt chốt bản `v3.3`.
