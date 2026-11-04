# Khả năng thực thi growth trên Train-24 — 04/11/2026

**Đã hoàn tất bước1 của kế hoạch capability-first, hoàn toàn local.**
Chọn bộ tính MTD offline làm lát cắt tiếp theo:18câu ở3business đã qua
roles/metric/common-data/boundary checks. Chưa tính đáp án MTD, chưa biết
bao nhiêu câu sẽ đúng. Không cần Colab, sửa prompt hoặc training cho bước này.

## Phạm vi và nhãn

Baseline là `artifacts/v31-growth-train/trace.jsonl`, chỉ lấy Train-24 còn
abstain. Truy hồi rộng theo doanh thu và từ chỉ tỷ lệ/thay đổi được281câu
tại20business. Đã đọc toàn bộ281câu truy hồi, sửa nhãn sơ bộ và chốt
`cases.json` trước execution. File nhãn không chứa numeric target. Đây là
nhãn chẩn đoán do agent review từ question/as_of, không phải parser mới hay
đánh giá độc lập. IDs chỉ để nối bằng chứng, không được đưa vào inference.

Phân loại:60full-month,39MTD,128tỷ trọng,22YoY,14boolean nhiều tháng,
13so ca bán,4seasonal/Tết,1doanh thu có lọc.182câu ngoài hai capability
đang xét không chạy phép tính. Truy hồi có thể bỏ sót lỗi gõ/cách nói khác;
không tuyên bố đây là toàn bộ câu growth trong Train.

## Kết quả99câu full-month/MTD

| Điểm dừng đầu tiên | Full-month | MTD | Tổng |
| --- | ---: | ---: | ---: |
| Chưa xác minh vai trò cột | 34 | 18 | 52 |
| Chưa xác minh metric | 18 | 0 | 18 |
| Dữ liệu/trạng thái chưa đạt contract | 0 | 3 | 3 |
| Tính được bằng resolver tháng hiện có | 8 | 0 | 8 |
| Qua tiền điều kiện, thiếu compiler MTD | 0 | 18 | 18 |
| Tổng | 60 | 39 | 99 |

- **8full-month tính đúng/0sai**, ở biz102(3),biz104(4),biz130(1).
  Câu hỏi được chuẩn hóa từ intent đã gán, resolver/guards giữ nguyên.
  Đây là cơ hội mở thêm8câu trên baseline local nếu tự động hiểu đúng câu;
  không phải8đáp án mới đã xuất từ pipeline hoặc mức tăng Public.
- **18MTD chưa tính**: biz121(4),biz122(9),biz130(5). Có cơ hội kiểm compiler
  trên nhiều business mà không đồng thời sửa schema/metric. Các business
  vẫn là development, không gọi là unseen hoặc chứng minh generalization.
- **3MTD biz112**: roles/metric qua nhưng có status ngoài tập hiện hỗ trợ;
  không gọi là thiếu dữ liệu. Không tự map trạng thái để tăng coverage.
- **52role blockers** không đồng nghĩa52cột chọn sai. Trong29câu, README
  hiện chỉ ra day-role candidate khác cột được chọn; còn lại có thể do
  ánh xạ file/sheet/tên bảng hoặc bảng chi tiết thiếu vai trò ngày. Phải
  phân biệt trước sửa. Profile lưu đầy đủ candidates và definitions.
- Profile còn ghi blockers chồng lấn: một câu dừng ở role thường vẫn vướng
  metric/status/returns. Sửa role không tự biến toàn bộ52câu thành đáp án.
  Với sổ viết tay, giá trị âm và order conflict có thể thuộc cách ghi trả
  hàng hợp lệ; ở đây chúng không phù hợp contract hiện tại, không kết luận
  dữ liệu nguồn bị hỏng.

## Quyết định bước tiếp theo

1. Tách việc tính trên hai khoảng ngày khỏi phần nhận diện câu trong module
   tháng, giữ full-month tương đương. Compiler MTD chỉ nhận intent typed đã
   kiểm; chưa nối model hoặc đưa map nhãn này vào pipeline. Giữ nguyên
   metric/status/grain guard đã có trong lát cắt đầu.
2. Chốt hai khoảng ngày: đầu tháng hiện tại→as_of; đầu tháng trước→cùng số
   ngày, cap cuối tháng ngắn. Test28/29/30/31, năm nhuận/chuyển năm, bounds,
   denominator0, NULL/nonfinite, cancel/return, order conflict; không cộng
   hàng ngoài khoảng hoặc đổi MTD thành hai tháng đầy đủ.
3. Đo offline từ intent đã gán cho39câu MTD, báo riêng18câu đủ tiền điều
   kiện và21câu bị chặn. Target chỉ dùng chấm sau phép tính. Nếu sinh sai,
   điều tra contract/period trước; chưa gọi model để che lỗi execution.
4. Khi đường tính chứng minh giá trị, mới sửa semantic contract extractor
   và đo tự động. Giữ8câu full-month có cơ hội làm regression/đối chứng.
   Sau freeze mới full Train/Public; không suy điểm từ tổng số eligible.

Sửa resolver theo README vẫn trong backlog có bằng chứng, nhưng không mở
đồng thời toàn bộ73blockers với compiler MTD. Việc đó cần tách theo grain,
returns, header/detail và ánh xạ sheet; chưa có bằng chứng đây là sửa một
dòng tên cột. Không cần A100 hoặc training ở giai đoạn local này.

## Tái lập và kiểm chứng

```bash
.venv/bin/python scripts/audit_growth_feasibility.py --output-dir artifacts/growth-feasibility/run-02
```

Phải dùng thư mục mới; runner kiểm hash cases/trace và identity/partition/
abstain của từng câu. Chỉ có39MTD được đánh giá tiền điều kiện; không có SQL
proxy hoặc numeric output MTD. Scope/schema/data errors được giữ tách biệt.

`run-01/report.json` là tổng hợp; `profiles.json` ghi các contract và data
observations; `results.json` từng câu. `verification.json` đối chiếu281IDs,
counts,8kết quả bằng evaluator, null outputs MTD và source/submissionSHA.
`input-files.json` lưu hash120file nguồn tại19business có intent hỗ trợ,
chụp sau lượt audit read-only. Cases SHA
`7e62077f90c2c5c4da21d3e47bcbfbce2140f4b3b77918866ca5f3a0c75b9ee7`.

**46tests pass (0.91s)** cho audit/monthly/intent; một cảnh báo pandas từ
fixture cố ý chứa ngày sai. Không sửa production source/notebook/submission,
không gọi network inference. Min/max ngày không chứng minh xuất đủ giao
dịch bên trong; pass các tiền điều kiện không thay cho numeric evaluation.
