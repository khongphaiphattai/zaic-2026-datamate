# Compiler MTD offline — 04/11/2026

**39 câu:18đúng,0sai,21abstain.67 invocations full-month giữ nguyên.**
Đã hoàn tất bước tính toán offline theo phạm vi duyệt; chưa nối model hoặc
mở nhận diện câu MTD trong pipeline. Các intent là nhãn chẩn đoán từ câu
Train-24 đã chốt, không phải output của một extractor đã được chứng minh an
toàn. Không có bài nộp/score Public mới và không gọi Colab.

| Nhóm | Số câu | Đúng | Sai | Abstain |
| --- | ---: | ---: | ---: | ---: |
| Qua tiền kiểm trước đó | 18 | 18 | 0 | 0 |
| Bị chặn trước đó | 21 | 0 | 0 | 21 |
| Tổng | 39 | 18 | 0 | 21 |

18câu đúng:biz121(4),biz122(9),biz130(5).21câu vẫn từ chối do vai trò
README/cột thiếu hoặc status ngoài phạm vi. Mọi câu trong bộ39đều đã được
thử trực tiếp qua compiler, không loại21câu khỏi mẫu đo để tăng precision.
Abstain có correctness=null và không tính thành đáp án đúng.

## Thay đổi

`GrowthIntent` là yêu cầu tính toán typed, `compile_growth_windows` tạo hai
khoảng ngày inclusive. MTD lấy đầu tháng→as_of, kỳ trước lấy đầu tháng
trước→cùng ngày, cap cuối tháng ngắn. Full-month vẫn so hai tháng đầy đủ.
`resolve_growth_intent` và resolver full-month dùng chung phần kiểm dữ liệu
và thực thi SQL. Audit bổ sung previous_end/period để kiểm trực tiếp.

Không đổi metric/status/grain/README policy. Giữ yêu cầu cả hai doanh thu
dương; current=0 vẫn abstain theo contract bảo thủ cũ, dù về toán học có
thể ra-100%. Không tự mở JOIN hoặc chuẩn hóa status mới. API typed không
tự chứng minh question không chứa ca/kênh/YoY; chưa được phép dùng trực tiếp
với model intent hiện đang fail3negative controls.

Test phát hiện lỗi connection cũ: truy vấn AST trong revenue metric guard
dùng fetchone, còn result khi caller muốn register lại bảng sau refusal.
Đổi sang fetchall để đọc hết result; không đổi điều kiện metric. Test tái
hiện trước sửa, các test metric và full suite đều qua sau sửa.

## Kiểm chứng

- **264tests pass (25.30s)** toàn suite. Một cảnh báo pandas từ fixture cố ý
  chứa ngày sai, không có test fail/skip. Numeric fixture có tiền lớn ngoài
  hai khoảng, nhiều dòng cùng đơn, giao dịch cuối ngày, cancel/return.
- Test ngày28/29/30/31, năm nhuận, chuyển năm, period/year/month/anchor sai,
  null/nonfinite/âm, order conflict, zero revenues, metric/boundary refusal.
- `full-month-baseline.json` chốt67invocations từ trace trước sửa. Reexecute
  giữ nguyên type/value/reason cho cả67; grammar và wrapper chưa mở MTD.
  Đây là regression của đường full-month đã gọi, không phải một lượt mới
  full pipeline4000câu. Nhóm ngoàiTrain24 trong replay cũng là dữ liệu đã
  dùng development trước đây, không được gọi là untouched validation.
- `run-01/verification.json` đối chiếu đủIDs, frozen source hashes, kết quả
  evaluator và tính lại18câu bằng Pandas masks + Decimal sums/rounding,
  không dùng SQL compiler/status helper để tính tổng độc lập. Kiểm này vẫn
  dùng DataLoader chung nên không chứng minh loader hoặc export hoàn hảo.
- Root/snapshot v3.0 vẫn SHA
  `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`.

Tái lập bằng thư mục mới:

```bash
.venv/bin/python -m scripts.evaluate_mtd_compiler --output-dir artifacts/mtd-compiler/run-02
```

Runner kiểm frozen annotations/trace, partition/business và call-set baseline,
chốt source hashes trước đo và kiểm lại sau đo. Targets chỉ dùng sau tính.
Config/report/results/regression ở `run-01/`; không xuất submission.

## Review và bước tiếp theo

Review diff theo spec: date windows explicit, SQL dùng date đã validate và
identifier được quote; giữ mọi guard và pipeline gate. Không thêm dependency,
model call hoặc nhãn theoID vào production. Context được đóng sau mỗi business;
partial/no-report run không được coi là benchmark. Không có finding cản trở
lưu compiler offline sau khi sửa lỗi result chưa đọc hết.

Giờ đã có bằng chứng phần tính toán có giá trị trên bộ development. Bước
tiếp theo là thiết kế/test semantic contract để không mất bộ lọc và kỳ so
sánh khi nhận diện câu: giữ14negative/positive probe đã xem làm regression,
thêm controls trước đo và so đóng góp model với đường deterministic cùng
compiler. Vẫn có thể chuẩn bị local; chưa gọi Colab hoặc train. Chỉ khi
intent được kiểm chứng và tích hợp tự động mới đo full Train/Public và xét
release.18/18không phải bảo đảm precision100% trên dữ liệu mới.
