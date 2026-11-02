# v3.0 — đã nộp, Public score 0.13400

File nộp: root `submission.jsonl` hoặc bản snapshot trong thư mục này, **đủ 1.000 dòng**. Hai bản trùng byte. Giữ nguyên tên file khi upload.

SHA256: `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`.

**Đã nộp: Public score 0.13400, bằng v2.8 (delta 0.00000).** Nguồn: ảnh người dùng cung cấp `submitv3.0.png`, đã xem trực tiếp. Portal hiển thị **03/11/2026 00:12:27**, loại model **SLM**, filename `cf843efb-cc80-4fdb-b28a-1d0c730a4f8a.jsonl`. Timezone không hiện trong ảnh. Receipt ở `submission-receipt.json`; SHA ở trên là file local đã chốt, không phải hash tải về từ portal.

Pipeline đã tích hợp Qwen business24 và fallback count paraphrases; Train PA 0.2706667, 159 tests pass. Tuy nhiên output Public vẫn trùng v2.8: 204 trả lời/796 abstain, 0 đáp án mới. Không gọi đây là tăng score Public. Lượt nộp này xác nhận mốc tham chiếu; không có thay đổi đáp án để quy tăng/giảm điểm cho thuật toán.

Bản root 5 dòng cũ được giữ local tại `root-before-publication.jsonl` (không được Git track do .gitignore). Kế hoạch phiên sau: `docs/plans/2026-11-03-improvement-plan.md`. Receipt và ROADMAP đã cập nhật theo ảnh; không sửa file release rồi gán score cũ cho file khác.
