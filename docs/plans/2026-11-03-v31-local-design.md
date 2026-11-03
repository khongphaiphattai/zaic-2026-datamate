# v3.1 — bước local: cấu hình thống nhất và retry có phân loại

## Phạm vi đã được duyệt

Người dùng yêu cầu tiếp tục kế hoạch v3.1 và xác nhận “Ok triển khai”. Bước này thực hiện kiểm kê Train-24, thống nhất generator/diagnostics và giảm policy retry theo kế hoạch ngày 03/11. Không đổi file v3.0 đã nộp, không training, không mở Slow tự do.

## Lựa chọn

- Mở JOIN ngay: có khả năng tăng coverage nhưng chưa có chứng minh khóa/grain/status; chưa chọn để triển khai.
- Thêm diễn đạt: dễ đo nhưng v3.0 cho thấy tăng Train mà Public không có đáp án mới; không tiếp tục làm hướng chính.
- **Chọn nền tảng tái lập trước**: cấu hình chung và phân loại retry, kiểm bằng local replay; đồng thời chọn capability từ kiểm kê Train-24 để viết spec tiếp theo.

## Hợp đồng triển khai

1. Một factory chọn Hybrid hoặc count wrapper từ cờ rõ ràng. Mặc định Slow tắt, count tắt để tương thích; model phục vụ mặc định `datamate-business24`. Bật Slow mà thiếu client phải lỗi cấu hình, không âm thầm chạy Fast-only.
2. Hai script dùng factory và cùng helper kiểm provenance. Generator thêm `--order-paraphrases`, giữ alias daily; route count không bị tính nhầm là Slow. Từ chối provenance sai trước inference/xuất file. Đóng mọi context kể cả lỗi.
3. Slow vẫn cho tối đa một correction với lỗi parser/binder/execution. Từ chối policy single SELECT/one registered table/metric là kết quả cuối, không gửi model thêm lần nữa. Kiểm cả số calls lẫn abstain/output: không để correction biến query bị cấm thành đáp án mới.
4. Replay chỉ dùng raw model responses có checkpoint/generation/prompt tương thích; SQL chạy lại dưới implementation mới. Khi source execution thay đổi, không giả hash cũ vẫn khớp. Ghi rõ replay là thí nghiệm migration cache; không phải live validation. Thiếu prompt thì fail, không gọi network âm thầm.
5. Giữ nguyên các prediction cũ trong bước tối ưu. Đo 3.000 Train và 1.000 Public nếu raw cache phủ đủ; so từng ID/type/answer. Chỉ giảm logical calls, không tuyên bố tiết kiệm units đã đo vì không gọi GPU.

## Nghiệm thu

- Test tái hiện policy retry hiện tại thất bại trước sửa; parser/binder correction vẫn trả đáp án đúng, policy bị chặn sau đúng một call.
- Integration generator và diagnostics dùng cùng cấu hình cho cùng output, count có route riêng, sai provenance dừng trước client inference; Slow tắt không cần endpoint.
- Full suite pass; report coverage/replay kèm source hashes, model calls và preservation. Root/release SHA v3.0 giữ nguyên.
- ROADMAP cập nhật sau mỗi mốc. Capability mới có spec và counterexamples trước implementation; không dùng Public targets hoặc suy nhãn từ score.
