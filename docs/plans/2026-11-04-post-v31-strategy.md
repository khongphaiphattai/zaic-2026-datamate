# Đề xuất sau điểm v3.1: tăng năng lực dùng chung và kiểm lại baseline

Trạng thái: đề xuất để trao đổi, chưa duyệt triển khai. Không đổi inference,
checkpoint hoặc submission. Không gọi Colab.

## Bằng chứng và điều cần sửa trong cách đánh giá

- Người dùng báo v3.1 Public **0.13500**, tăng **0.00100** so v3.0/v2.8.
  Receipt ghi nguồn là lời người dùng; chưa có screenshot, submit ID/time hoặc
  hash file tải lại từ portal. Root/snapshot local vẫn khớp SHA đã phát hành.
- Local Public: 205 trả lời, 795 abstain; 204 Fast, 1 MTD, 0 Slow accepted.
  Câu tăng thêm đến từ compiler/status, chưa có lợi ích Public từ model.
- Nếu portal chấm cả 1000 dòng theo +1/-1/0, C+W=205 và C-W=135 suy ra
  **170 đúng/35 sai**, precision **82.93%**. Đây là suy luận tổng hợp có điều
  kiện, không chỉ ra 35 câu nào. Baseline v3.0 tương tự suy ra169 đúng/35 sai.
  Không tiếp tục gọi Fast Path là 100% precision từ số liệu Train.
- Train hiện871 đúng/6 sai, precision99.32% trong877 câu trả lời. Khoảng cách
  với Public đáng điều tra; chưa phân biệt được schema shift, thiếu nghiệp vụ,
  lỗi loader, evaluator hoặc các giả định scoring/artifact chưa kiểm độc lập.
- Growth audit cũ trên281 câu Train24 abstain tìm128 câu tỷ trọng; đây là
  tập truy hồi có giới hạn, không phải128 cơ hội chắc chắn đúng. Role audit
  đã thấy nhầm ngày tạo/ngày bán và mất mapping README file+sheet.

## So ba hướng

1. **Sửa lỗi nền tảng của đáp án hiện có.** Hiệu quả tiềm năng cao: theo PA,
   sai→đúng thêm2 điểm, abstain→đúng thêm1 điểm. Tuy nhiên chưa biết câu Public
   nào sai. Điều tra bằng rulebook/README/dữ liệu và đối chứng Train, không
   đoán nhãn từ portal hoặc gửi nhiều bài để dò đáp án.
2. **Lớp dữ liệu nghiệp vụ chuẩn + kế hoạch truy vấn có cấu trúc.** Đề xuất
   chính cho mở rộng. Chuẩn hóa vai trò/grain/metric một lần cho mỗi business,
   sau đó tái sử dụng cho doanh thu, tỷ trọng, tăng trưởng và xếp hạng.
   Chi phí thiết kế cao hơn thêm regex, có thể vẫn bị chặn bởi thiếu chứng cứ.
3. **Đổi model/train thêm hoặc nhiều SQL ứng viên.** Để sau. Model hiện chưa
   chứng minh lợi ích Public; sinh nhiều đáp án đồng ý nhau vẫn có thể cùng
   bỏ điều kiện. Chưa có lý do tiêu units khi cơ sở dữ liệu nghiệp vụ còn sai.

## Thiết kế đề xuất cho hướng 2

README + rulebook + file/sheet + dữ liệu
→ hợp đồng bảng/metric có nguồn chứng minh
→ kế hoạch gồm metric, thời gian, filters, group, comparison, output type
→ compiler DuckDB → kiểm ngữ nghĩa/dữ liệu → đáp án hoặc abstain.

- Phân biệt bảng đơn, dòng hàng, thanh toán và trả hàng; ghi nguồn file/sheet,
  khóa, độ duy nhất, đơn vị, ngày bán, giảm giá, chính sách trạng thái.
  Không ép một bảng duy nhất cho mọi grain; không tự JOIN nếu chưa chứng minh
  khóa/cardinality và tránh nhân tổng. Thiếu role vẫn ghi unresolved.
- Model có nhiệm vụ đọc cách diễn đạt và đề xuất kế hoạch. Ví dụ câu tỷ trọng
  kênh Shopee tháng trước cần tử số cùng kỳ có lọc Shopee, mẫu số toàn bộ cùng
  metric/kỳ, kết quả percent. Bỏ kênh hoặc đổi kỳ là kế hoạch sai dù SQL chạy.
- Kiểm đủ điều kiện câu gốc, mapping giá trị thực trong bảng, metric và grain.
  JSON đúng schema hoặc model tự báo confidence không chứng minh đúng nghĩa.
  Synthetic controls chỉ khác ca/kênh/khách/kỳ phải phát hiện bỏ điều kiện.
- Dùng phép kiểm biến đổi trên fixture: đổi thứ tự dòng không đổi tổng;
  thêm giao dịch ngoài khoảng không đổi kết quả; join không nhân đơn; phân
  nhóm cộng về tổng chỉ khi nhóm đầy đủ, rời nhau và metric cộng được.
  Các phép kiểm này hỗ trợ bắt lỗi, không thay thế đối chiếu nhãn.

Hướng phân rã và chọn schema có tiền lệ ở
[DIN-SQL](https://arxiv.org/abs/2304.11015) và
[CHESS](https://arxiv.org/abs/2405.16755).
Đây là ý tưởng thích nghi cho dự án, không bê kết quả benchmark sang ZAIC,
không yêu cầu dựng nhiều agent hay dùng model ngoài giới hạn cuộc thi.

## Thí nghiệm đầu tiên, nếu duyệt

1. Audit logic tạo205 đáp án hiện tại và đối chứng Train: ngày, trạng thái,
   grain, giảm giá, trả hàng, đơn vị, định dạng. Bảo toàn snapshot v3.1 để
   so sánh; cho phép đề xuất sửa đáp án cũ khi có chứng cứ, không giữ lỗi vì
   invariant "mọi output cũ bất biến". Không sửa theo ID/target Public.
2. Lập đặc tả một lát cắt hợp đồng doanh thu từ Train trên các dạng bảng khác
   nhau; đo khả năng giải quyết cả câu cũ và nhóm tỷ trọng còn abstain trước
   khi đầu tư compiler rộng. Tỷ trọng là ứng viên, chưa chốt là thắng cuộc.
3. Freeze câu/thí nghiệm trước tuning; so cùng dữ liệu/compiler giữa parser
   deterministic, base7B và adapter7B hiện có (base/adapter chạy lần lượt).
   Chỉ mở Colab khi semantic layer thực thi được; chưa training.
4. Báo số đúng/sai ròng, coverage theo business/schema và model contribution;
   chặn release nếu xuất hiện lỗi mất điều kiện ở controls hoặc gain chỉ do
   hardcode một schema. Tách development đã xem khỏi test mới; nhóm6 cũ
   không còn untouched. Holdout mới không xóa được lịch sử đã xem dữ liệu.
5. Chốt source/config rồi chạy Public một lần để đo coverage/diff, không tune
   từng câu Public. Không dự báo score cụ thể hoặc gọi đây là đột phá trước đo.

Chỉ thay kế hoạch nếu audit cho thấy lỗi scoring/artifact, nhóm tỷ trọng đa số
không có dữ liệu/metric đủ rõ, hoặc model không thêm đúng ròng so cùng compiler.
Khi đó ưu tiên sửa lớp lỗi đã chứng minh, không tiếp tục chỉ vì đã đầu tư model.
