# Kế hoạch phiên tiếp theo — v3.0, 03/11/2026

## Trạng thái chốt

Người dùng đã chọn tên **v3.0**, xác nhận sẽ tự nộp sau và gửi kết quả ngay khi có. **Chưa có score mới/submit ID, chưa có upload được xác nhận.** File chốt `submission.jsonl` đủ 1.000 dòng, SHA256 `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`. Receipt tại `artifacts/releases/v3.0/submission-receipt.json`. Mốc Public 0.13400 là người dùng báo cho v2.8.

v3.0 tích hợp Qwen business24, selective revenue/metric guard và fallback nhận diễn đạt đếm đơn theo ngày/tuần/quý/YTD. Full Train: **818 đúng / 6 sai / 2.176 abstain**, PA **0.2706667** so Fast-only 0.25000. Slow thêm 13 đúng, fallback đếm đơn thêm 49 đúng, không thêm lỗi trong lượt đo và giữ mọi đáp án cũ. **159 tests pass**. Đây là development evidence; nhóm 6 đã tham gia debugging, không phải untouched validation.

Public: **204 trả lời / 796 abstain**, **0 đáp án mới**, file trùng byte v2.8. Có 1 câu vào Slow nhưng bị chặn; fallback count chưa mở đáp án Public. Không thể gọi mức tăng Train là tăng Public. Nếu cùng file/cùng cách chấm, nộp lại không tạo phép thử cải tiến đáp án mới; lượt người dùng yêu cầu có thể xác nhận lại mốc tham chiếu. Nếu score khác 0.13400, trước tiên kiểm tra liên kết file/receipt/rules, không gán khác biệt cho thuật toán khi bytes giống nhau.

## Thứ tự công việc

1. **Nhận score và khóa receipt.** Ghi submit ID/thời điểm, score và SHA file thực sự nộp. Dữ liệu user-report phải ghi rõ nguồn, không giả đã đọc portal. Tính delta so 0.13400 nhưng diễn giải theo việc file trùng baseline. Không thay file chốt sau upload rồi gán score cho bản khác.
2. **Giải quyết độ phủ trước khi training.** Nút thắt đo được là 795 câu Public bị language gate chặn và 1 câu cần cấu trúc ngoài policy; các mở rộng cú pháp tăng Train nhưng chưa tăng Public. Kiểm kê intent/schema ở mức tổng hợp, dùng Train-24 để thiết kế các capability có thể kiểm chứng; Public chỉ hỗ trợ chẩn đoán độ phủ, không điền/sửa đáp án theo ID. Chọn một intent hỗ trợ được qua nhiều business, chốt spec/counterexamples rồi mới code. Không tiếp tục thêm polite phrase chỉ vì Train đẹp hơn.
3. **Điều tra lỗi percent từ Train-24.** Năm câu tỷ lệ hủy ở biz_105/biz_117 thấp hơn target 0.01 điểm phần trăm. Tỷ lệ COUNT DISTINCT thực tế: 124/5650, 83/4431, 11/913, 18/666, 20/1024; thay row-count không khớp. Kiểm README về grain, ngày đại diện của đơn nhiều dòng, duplicate/status không nhất quán và mẫu số. Chưa kết luận chỉ do rounding; không cộng 0.01 hoặc hardcode ID/business. Có negative controls trên câu đã đúng và regression test trước sửa.
4. **Tách policy rejection khỏi lỗi cú pháp để tiết kiệm units.** 9 câu Train bị chặn vì SQL cần JOIN đã dùng 18 calls, gồm 9 retry; policy hiện không hỗ trợ JOIN nên sửa cú pháp không giải quyết được phạm vi. Thiết kế fail-closed không retry cho policy shape, giữ retry cho lỗi syntax thật; replay/test bảo đảm không làm nhận SQL sai và không làm mất đáp án đúng đã có.
5. **Kiểm date/status và khảo sát JOIN có bằng chứng.** Metric guard chỉ chứng minh cột/shape, chưa chứng minh WHERE/date/status/table choice. Nếu intent được chọn cần header/detail, trước hết kiểm PK uniqueness, FK completeness, cardinality, dòng nhân bản và định nghĩa metric trong README. Chỉ mở một shape JOIN được chứng minh, có temporal/status guard, negative tests cho double count/NULL/orphan. Nếu chưa chứng minh, tiếp tục abstain. Không mở Slow Path tự do.
6. **Đo một thay đổi mỗi lần.** Full Train, nhóm 24/6, số đúng/sai/abstain tăng thêm, Fast/Slow preservation, latency và network/cache calls. Chốt grammar/hash trước khi đọc kết quả nhóm khác; ghi lịch sử tuning trung thực. Giữ riêng counterexample set mới trước tune nếu cần một phép đo ít bị lịch sử thiết kế tác động.
7. **Chỉ gọi bản mới là cải thiện Public khi có bằng chứng.** Đủ 1.000 ID/format, so từng dòng/SHA với baseline; nếu vẫn trùng thì ghi rõ không có phép thử đáp án mới. Candidate có khác biệt phải đến từ pipeline tự động, giữ snapshot cũ và source hashes. Lượt nộp tiếp theo theo phạm vi người dùng đồng ý, không tự dùng hết quota.

## Ngân sách và tái lập

Người dùng báo còn 120 units trước phiên khôi phục; số dư hiện tại chưa xác nhận, không quy đổi thành số giờ cố định. Dùng backup business24, không training lại. Chỉ đề xuất training mới khi lỗi còn lại được chứng minh thuộc model và các sửa deterministic/guard không giải quyết được; báo trước mục tiêu, data/split, tiêu chí dừng và chi phí dựa trên phiên đo.

Chuẩn bị local trước bật GPU, gom request, chỉ reuse raw responses khi provenance/config/frozen-source/full-prompt hash khớp; thực thi SQL lại, không cache labels/results. V3 replay cuối dùng 43 cached responses Train và 2 Public, 0 network inference. Ngắt runtime sau khi hết việc GPU.

Bản chốt tái tạo bằng `scripts/diagnose_hybrid.py --order-paraphrases` với business24/provenance đúng; source commit `2f984d4`, hash/config trong `artifacts/v3-period-{train,public}/`. `docs/runbooks/business24-resume.md` và notebook 04 dùng khi mất Colab. Bản này chưa phải gói Docker offline cuối cùng; cần kiểm toàn bộ inference/container offline trước solution freeze.
