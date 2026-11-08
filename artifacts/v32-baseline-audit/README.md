# Baseline audit — ưu tiên1, lượt đầu

## Phạm vi
Kiểm kê205 đáp án v3.1 tại14business Public; replay SQL đã lưu trên DataLoader
hiện có. Không có nhãn Public, không gọi model, không sửa submission/production.
Đây là kiểm tái lập và bản đồ rủi ro, không phải xác nhận205 đáp án đúng.

## Kết quả và coverage
-201 Fast scalar có SQL: giá trị tái lập bằng đáp án đã xuất.
-1 MTD: phải tính cả hai kỳ và công thức, không so trực tiếp SUM hiện tại với
 phần trăm. Replay cả hai kỳ và công thức khớp đáp án−3.27%; tổng cộng
 **202/202numeric/percent outputs tái lập**,3unanswerablechưakiểmsemantic.
-3 unanswerable không có SQL để replay: cần kiểm lý do thiếu dữ liệu từng câu.
-195 đáp án dùng substring status policy: điểm cần review alias/phủ định/
 loại cả đơn; không có nghĩa195câu sai.121SUM cần chứng minh grain/metric;
 19dùng cột giờ/ngày tạo cần kiểm nghiệp vụ. Các nhóm chồng lấn.

## Đối chứng có nhãn Train
Năm lỗi tỷ lệ huỷ đã biết tạibiz105/117 được thử đổi riêng cộtGiờ sang cột
README định nghĩa ngày bán, giữ nguyênSQLkhác: **0/5 thay đổi kết quả**,
cả5vẫn lệch target0.01. Đây là negative result, không sửa date để chữa5lỗi.
Rulebook§1 quy định kỳ theo ngày đặt; README ghiGiờlà giờ đặt, dòng trả dùng
lúc trả. Chưa chứng minh lỗi chọn ngày cho cancellation query. Không cộng
bù0.01 hoặc suy target sai. Lỗi doanh thuTrainbiz101chênh30.000vẫn unresolved.
Probe từngSQL/giá trị tạitrain-date-probe.json.

## Finding kiểm mã nguồn
Heuristic chọn ngày theo ítnullnhất, chưa dùng ngữ nghĩa nghiệp vụ; nguy cơ
chứ chưa chứng minh lỗi từngPublic. Hàm chọn revenue còn nhánh theo businessID
cho dev010/dev013; cần kiểm công thức theo README/rulebook rồi thay bằng
contract chung. Chưa sửa/hardcode thêm hoặc quy hai nhánh này thành nguyên
nhân35lỗi từ portal.

## Tiếp theo trong ưu tiên1
1. Kiểm3unanswerable bằng README/files/phạm vi dữ liệu, không suy nhãn từscore.
2. Audit status thực tế và contract doanh thu trên các dạng bảng; đối chứng
   độc lập về grain/discount/returns. Replay chung loader không bắt lỗi loader.
3. Dựng regression ở pipeline seam cho lỗi có chứng cứ, rồi sửa riêng và đo
   đúng/sai ròng. Chưa bắt đầu triển khai lớp semantic rộng hay training.

## Tái lập
```
.venv/bin/python artifacts/v32-baseline-audit/audit.py
.venv/bin/python artifacts/v32-baseline-audit/probe_train_dates.py
```
answers.json là205record kiểm kê; profiles.json chứaREADME vàschema;
summary.json ghi hash artifact/counts/coverage. Các risk flags chỉ là truy hồi
hữu hạn theoSQL, không phải bộ chứng minh đầy đủ. Dùng snapshot v3.1 để so sánh.

## Lượt semantic và sửa loader tiếp theo
- Scan3business có câu địa chỉ: không có cột địa chỉ được nhận diện và không
  có tên đường được hỏi trong các cột text đã nạp. Ủng hộ3refusals theo§7;
  không phải nhãnPublic hoặc bằng chứng parser không bỏ sót thông tin nguồn.
-75rawstatus/table/column entries đã quan sát:0bất đồng giữa nhãn canonical
  đã biết và chính sách substring cũ. Không có cơ sở đổi status để tăng điểm
  trong lát cắt này; nhãn canonical chưa biết vẫn là unresolved.
-dev010:25.191headerrows/25.191uniqueorders đều nối được tổng chi tiết;
 0mismatch giữa SUM(cong) và thanh_tien+tien_giam−phi_van_chuyen. Công thức
 đang dùng có bằng chứng số liệu; chưa cần thay bằngJOINproduction.
-Defect được tái hiện trên fixture: ffill header toàn bảng chuyển khách/trạng
 thái từ đơnA sang đơnB. Đã sửa sang ffill trong mã đơn, giữIDfillnhưcũ,
 giữ trường riêng của dòng chưa cóID.3regressiontests;2testsfailedtrướcsửa,
 testorphanfailedtrướcbổsungguard. Không gán ảnh hưởngPublic từfixture.
-Đang đánh giá replay sau sửa, output riêng để bảo toàn audit trước sửa.
  Các bước này chưa xác định được35lỗiPublic hoặc rootcause6lỗiTrain.

### Quyết định sau replay đối chứng
Bản thử áp grouping cho cả ngày làm đổi9outputdev013; không promote bản đó.
Contract cuối chỉ scope trường khách/trạng thái; giữ fillngày hiện có do
ngày có thể dùng chung cho nhiều hóa đơn. Các thư mục order-fill-public-replay
và order-fill-final-replay là thí nghiệm bị loại, không candidate release.
Bản cuối tạiorder-scoped-header-replay, hashsource ghi trongmanifest.
Fullsuite **321passed/1knownwarning**, thêm4regressiontests so baseline317.
Chưa fullpipelineTrain/Public, chưa đo PA mới hoặc thay submission.

## Giải mã 100% 6/6 lỗi Train và phát hiện lỗi Grain `dev_003` / Table Selection `biz_109` (07/11/2026)

1. **5/6 lỗi Train (`biz_105`: `q_f4966c224caa`, `q_f72b8741ddf4`; `biz_117`: `q_008f69128180`, `q_a05aeaae5493`, `q_a954a482d4bf`) — Tỷ lệ hủy/hoàn trả lệch 0.01%:**
   - **Root cause:** Bảng `so_ban_hang` ở `biz_105` và `biz_117` có grain dòng hàng, khi khách trả hàng sau (khác ngày/khác tháng) hệ thống append thêm dòng mới mang cùng `ma_hoa_don`/`ma_phieu`, cột `gio` ghi **thời điểm trả hàng**, còn cột `ngay_giao_dich`/`ngay_lap` trên dòng trả bị trống trong Excel gốc và bị `ffill()` xuyên đơn ghi đè. Khi lọc `WHERE strftime(TRY_CAST(gio AS DATE), '%Y-%m') = ...` ở mức dòng (row grain), các đơn đặt ở tháng $M-1$ nhưng có dòng trả ở tháng $M$ bị lọt vào mẫu số `COUNT(DISTINCT order_id)` của tháng $M$.
   - **Đối chứng:** Chuẩn hóa ngày đặt theo đơn (`MIN(TRY_CAST(gio AS DATE))` cho mỗi `order_id`) khớp **5/5** đáp án chuẩn của BTC:
     - `biz_105` 2026-03: `124 / 5647 = 2.20%` (target `2.2`, cũ `124 / 5650 = 2.19%`)
     - `biz_105` 2026-06 MTD (`<= 2026-06-24`): `83 / 4425 = 1.88%` (target `1.88`, cũ `83 / 4431 = 1.87%`)
     - `biz_117` 2025-10: `11 / 911 = 1.21%` (target `1.21`, cũ `11 / 913 = 1.20%`)
     - `biz_117` 2026-02: `18 / 664 = 2.71%` (target `2.71`, cũ `18 / 666 = 2.70%`)
     - `biz_117` 2026-04: `20 / 1023 = 1.96%` (target `1.96`, cũ `20 / 1024 = 1.95%`)

2. **1/6 lỗi Train (`biz_101`: `q_91eac74caf39`) — Doanh thu tháng 08/2025 lệch 30.000đ (`204.534.500` vs `204.564.500`):**
   - **Root cause:** Trong `src/zaic_2026_datamate/data_loader.py`, điều kiện `is_num_col` và `is_money_col` kiểm tra chuỗi con `"no"` (dự định bắt `cong_no`, `du_no`). Tên cột sản phẩm `Nội dung` (`noi_dung`) ở `biz_101` chứa `"no"`, bị ép qua `clean_vietnamese_currency()` biến `"Pepsi 330ml"` và `"Coca Cola 330ml"` thành cùng số `330.0` (và món không có số thành `NaN`). Bước `cleaned_df.drop_duplicates()` sau đó xóa nhầm 10 dòng hợp lệ trên toàn `biz_101` (gồm 3 dòng `10.000đ` trong tháng 08/2025 thuộc `ORD002717`, `ORD003090`, `ORD003395`).

3. **Lỗi Grain nhân bản tiền hàng ở Public Test `dev_003` (`BanHang_TongHop` — ảnh hưởng 8/205 câu đang trả lời):**
   - Bảng `ban_hang_tong_hop` của `dev_003` có grain dòng sản phẩm (`dong`, `thanh_tien_d`), nhưng lặp lại các cột tổng mức đơn (`tien_hang_truoc_giam`, `tien_giam`, `tong_thanh_toan`) trên mọi dòng của cùng `ma_don`.
   - `find_revenue_column()` chọn `tien_hang_truoc_giam` trước `thanh_tien_d`, làm `SUM(tien_hang_truoc_giam)` nhân bội giá trị đơn hàng theo số dòng món trong đơn trên 8 câu Public Test (`q_01ac0fc9f2a2`, `q_09542f67d04a`, `q_4b260819febf`, `q_51ba2dc4fd49`, `q_77ce65543f25`, `q_900c545f4ed7`, `q_b0ce2336c79b`, `q_de25e5a7e1c6`).

4. **Lỗi chọn bảng thiếu ngày ở Train `biz_109` và cột ngày ở `dev_004`:**
   - Ở `biz_109`, `find_primary_sales_table()` chọn bảng chi tiết `danh_sach_hoa_don_chi_tiet` (không có cột ngày, không có trạng thái) thay vì bảng hóa đơn `du_lieu_ban_hang_danh_sach_hoa_don` (có `thoi_gian`, `trang_thai`, `tong_tien_hang`), làm 99/100 câu của `biz_109` bị abstain.
   - Ở `dev_004`, bảng `so_ban_hang` có cả `gio` (dòng trả ghi lúc trả) và `thoi_gian` (ngày bán); cần ưu tiên ngày đặt/bán của đơn thay vì timestamp dòng trả.
