# Structured intent probe — 04/11/2026

## Quyết định

Tiếp tục kế hoạch đã duyệt: đo model trích xuất intent/period trước khi mở capability. Train-24 cho thấy nhiều câu ngoài grammar tháng đầy đủ là MTD cùng kỳ, không đơn thuần là paraphrase. Không mở Text-to-SQL tự do hoặc viết thêm regex dựa trên Public.

Phép thử shadow chỉ nhận question/as_of_date, không đưa đáp án số, SQL hay schema vào prompt. Output JSON giới hạn: abstain hoặc revenue_growth với period full_month/month_to_date, year/month. Với full_month, baseline là tháng trước đầy đủ; với MTD, baseline là cùng số ngày tháng trước (cap theo số ngày tháng trước). Chỉ tổng doanh thu toàn cửa hàng, không điều kiện bổ sung. MTD chưa có compiler trong pipeline: output parser hợp lệ không đồng nghĩa đáp án an toàn.

## Đo lường

Tập nhỏ có nhãn intent thủ công từ câu Train-24 và counterexamples synthetic: MTD, tháng đầy đủ, câu ngoài grammar cũ, khách/sản phẩm/ca/YoY/boolean và prompt injection. Không dùng Public để tạo cases. Lưu nguồn, SHA và phân nhóm; đây là development probe đã tuyển chọn, không đo unseen generalization.

Một call/case, tuần tự, max_tokens256, temperature0/seed42, kiểm provenance business24 trước gọi. Không retry tự sửa output sai. Ghi raw output, lỗi JSON/schema/date, false accept trên negatives, exact intent match trên positives và latency. Thiếu endpoint thì chuẩn bị local và chờ URL, không train lại. Sai JSON hoặc semantic false accept là bằng chứng cần cải thiện, không nới validator để làm đẹp số.

Nghiệm thu bước này: unit tests reject schema thừa/duplicate keys/boolean year/ngày không hợp lệ/tháng chưa hoàn tất; runner không ghi submission hoặc gọi compiler. Sau kết quả live mới quyết định spec compiler MTD, boundary tests (tháng31/ngày29/nămnhuận) và full Train. Không promote dựa trên vài câu probe đúng.
