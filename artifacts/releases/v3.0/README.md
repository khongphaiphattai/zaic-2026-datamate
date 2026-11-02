# v3.0 — release chờ người dùng nộp

File nộp: root `submission.jsonl` hoặc bản snapshot trong thư mục này, **đủ 1.000 dòng**. Hai bản trùng byte. Giữ nguyên tên file khi upload.

SHA256: `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`.

Người dùng xác nhận sẽ tự nộp sau và báo kết quả. **Chưa có upload/score mới được xác nhận.** Receipt ở `submission-receipt.json`, version v3.0; 0.13400 chỉ là mốc v2.8 do người dùng báo.

Pipeline đã tích hợp Qwen business24 và fallback count paraphrases; Train PA 0.2706667, 159 tests pass. Tuy nhiên output Public vẫn trùng v2.8: 204 trả lời/796 abstain, 0 đáp án mới. Không gọi đây là tăng score Public. Nếu nộp một lần theo mục tiêu người dùng, lượt này xác nhận mốc tham chiếu; không có thay đổi đáp án để quy tăng/giảm điểm cho thuật toán.

Bản root 5 dòng cũ được giữ local tại `root-before-publication.jsonl` (không được Git track do .gitignore). Kế hoạch phiên sau: `docs/plans/2026-11-03-improvement-plan.md`. Khi có score, cập nhật receipt và ROADMAP; không sửa file release rồi gán score cũ cho file khác.
