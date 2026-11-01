# Selective Slow Path — thiết kế đã được duyệt

Intent: bảo toàn baseline v2.8, đo PA đúng rulebook và thử mở doanh thu một ngày khi Fast Path abstain.

Acceptance criteria:
- Lưu snapshot HEAD v2.8 và bản working tree; không ghi đè file chính khi chạy --limit, lỗi format hoặc output đã tồn tại nếu chưa yêu cầu overwrite.
- Một evaluator chung kiểm tra type/format trước khi so giá trị; số chỉ bỏ sai số floating point, percent không tự nhân 100, list giữ thứ tự, table không phụ thuộc thứ tự dòng. Alias chỉ nhận khi caller cung cấp từ dữ liệu shop.
- Fast Path không đổi; Slow Path mặc định tắt. Khi bật chỉ cho câu tổng doanh thu một ngày có ngày hợp lệ, không điều kiện sản phẩm/khách/kênh/so sánh. Không dùng confidence tự khai như bằng chứng độ chính xác.
- Báo cáo riêng Fast/Slow correct, wrong, abstain và delta PA; ghi trace từng câu. Giữ split theo business, phát hiện overlap SFT. Checkpoint cũ thấy cả 30 business nên không được gọi là unseen evaluation.
- Gate được kiểm thử bằng mock. Chỉ khuyến nghị mở trên Public khi có bằng chứng PA dương từ checkpoint không thấy tập giữ riêng. Thiếu endpoint/checkpoint thì báo rõ chưa đo được.

Tasks: snapshot + output protection; strict evaluator + baseline rerun; gated routing + tests; split/provenance + benchmark report; documentation.

## Commands and evidence

- Full strict Fast baseline: `.venv/bin/python scripts/benchmark_lbo.py` (business-grouped Train, not isolated CV).
- Split/provenance only: `.venv/bin/python scripts/evaluate_selective.py --audit-only`.
- Train-partition SFT: `.venv/bin/python scripts/prepare_sft_dataset.py --split artifacts/selective-evaluation/split.json --output artifacts/train-only-sft.jsonl`.
- Clean model validation: `scripts/evaluate_selective.py --training-data <actual-training-jsonl> --base-url <endpoint> --model <served-model>` (using `.venv/bin/python`). Fails if business overlap exists. This audits declared provenance, not checkpoint weights.
- Old model exploration requires explicit `--exploratory`; cannot justify unseen precision.
- Preview outputs: `scripts/generate_public_submission.py --limit 5 --output /tmp/zaic-preview.jsonl`. Existing output requires `--overwrite`; `--limit` cannot target any file named `submission.jsonl`.

Completed strict Train result: 756 correct, 6 wrong, 2,238 abstain; PA 0.25000, precision 99.21%. Scalar/result guards and external-access restrictions are basic defense; resource isolation/timeouts and semantic verification remain work for offline packaging. Alias matching from shop catalogs remains outside the local evaluator.
