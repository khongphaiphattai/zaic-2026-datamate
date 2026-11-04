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
