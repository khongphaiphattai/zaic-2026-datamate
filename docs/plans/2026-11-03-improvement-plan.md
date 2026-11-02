# Kế hoạch cải thiện cho phiên tiếp theo — 03/11/2026

Mục tiêu: dùng kết quả Train và một lần submission ngày 02/11 để chọn thay đổi có bằng chứng. Ưu tiên sửa nguồn sai và mở intent nhỏ có thể kiểm chứng. Ngân sách người dùng báo còn 120 units trước phiên khôi phục; số còn lại sau phiên chưa xác nhận. Chưa có bằng chứng cần training lại.

## Điểm xuất phát và điều kiện kết luận

Baseline v2.8 có 1.000 câu, 204 trả lời / 796 abstain, score Public người dùng báo 0.13400. Snapshot SHA256 `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`.

Grammar mới được thiết kế từ Train-24, giữ checkpoint/prompt/metric/Fast Path. Audit offline mở thêm 19 Slow candidates Train và 1 Public sau Fast. Đây là số câu đủ điều kiện, không phải số đáp án đúng. Lượt full Train/Public mới đang hoàn tất; kết quả cuối cùng và score/submit ID thực tế phải được ghi ở ROADMAP và submission receipt. Chưa có score mới thì không kết luận tăng điểm.

Nếu file chỉ thay đổi một dòng và portal chấm cùng 1.000 câu theo PA +1/-1, tác động dự kiến bị chặn trong ±0.001; đây là hệ quả có điều kiện của công thức, không dự báo đáp án đúng. Score tăng chỉ là bằng chứng cho lần thay đổi đó, không chứng minh Slow Path có precision 100%. Nếu file trùng baseline, nộp lại không tạo phép thử chất lượng mới.

## Thứ tự công việc

1. **Đóng sổ lần nộp hôm nay.** Lưu SHA256 file đã upload, submit ID/thời điểm, score thực tế, delta so 0.13400 và đúng tập ID thay đổi. Không đổi file sau upload rồi gán score cho bản khác. Nếu thiếu receipt, lấy lại từ portal trước phân tích.
2. **Điều tra 5 lỗi percent trên Train-24.** Các câu tỷ lệ hủy ở biz_105/biz_117 đều thấp hơn target 0.01 điểm phần trăm. Truy vấn kiểm tra đã xác nhận tỷ lệ COUNT DISTINCT hiện tại lần lượt 124/5650, 83/4431, 11/913, 18/666, 20/1024; đổi sang đếm dòng không khớp. Kiểm README về grain, ngày đại diện của đơn nhiều dòng, duplicate/status không nhất quán và mẫu số. Chưa kết luận lỗi chỉ do rounding; không cộng hằng số 0.01, không hardcode ID/business. Viết test tái hiện cùng đối chứng đúng trước sửa.
3. **Chọn một intent tiếp theo có dữ liệu hỗ trợ.** Khảo sát Train-24 đang abstain; nhóm đếm đơn có khoảng 91 câu theo regex thăm dò (các nhóm đếm có thể chồng lấp, chưa phải coverage cam kết). Ưu tiên đếm đơn theo ngày/tháng nếu README xác nhận khóa đơn, trạng thái và thời gian. Chốt spec/negative pairs trước mở gate. Nếu chưa chứng minh grain/status, tiếp tục abstain.
4. **Kiểm thời gian trong Slow Path trước khi mở rộng.** Metric guard hiện chỉ xác minh cột/shape, chưa chứng minh WHERE/date/status/table choice. Dùng lỗi/counterexamples từ Train-24 để thiết kế temporal/status guard; không dùng đáp án Public để sửa câu cụ thể. SQL ngoài grammar kiểm được thì abstain.
5. **Đo lại một thay đổi mỗi lần.** Lưu full Train, nhóm 24/6, Fast preservation, số đúng/sai/abstain tăng thêm, latency và số network/cache calls. Nhóm 6 đã tham gia debugging nên chỉ là development regression. Giữ riêng một tập counterexample mới trước tune nếu cần đánh giá ít bị ảnh hưởng bởi lịch sử thiết kế.
6. **Chốt candidate có kiểm tra.** Đủ 1.000 ID/format, so từng dòng với baseline, chỉ nhận khác biệt do pipeline tự động đã kiểm. Giữ snapshot cũ và source hashes. Dùng Public score để đánh giá bản hệ thống; không suy/điền ground truth theo ID từ điểm rồi chép đáp án.

## Tiêu chí chọn và ngân sách

Thay đổi phải có nguyên nhân được chứng minh, regression tests, tăng điểm thuần trên development và không tạo lỗi mới chưa giải thích. Không dùng một con số precision trên mẫu nhỏ để cam kết tổng quát hóa. Báo riêng độ phủ và các phần guard chưa kiểm được.

Dùng lại raw response chỉ khi provenance/config/frozen-source/prompt hash khớp; không cache nhãn hoặc kết quả SQL. Chuẩn bị câu thử và code local trước bật GPU; gom request, dừng API/runtime sau khi hết việc cần GPU. Không giả định 120 units tương đương số giờ cố định.

Chỉ đề xuất training mới khi lỗi còn lại thực sự từ model (SQL/format/schema/date) và các sửa deterministic/guard không giải quyết được. Khi đó báo trước mục tiêu, dataset/split, tiêu chí dừng và chi phí dựa trên phiên đo; không tiếp tục checkpoint cũ đã học cả 30 business hoặc gọi tập đã tune là unseen.
