# v3.1 — local evaluation và candidate

Baseline đã nộp: v3.0 Public 0.13400, root `submission.jsonl` và snapshot `artifacts/releases/v3.0/` giữ nguyên. Các output dưới đây là candidate riêng, không tự nộp portal.

## Cấu hình

Generator/diagnostics dùng chung `PipelineConfig`:
- `--order-paraphrases`: fallback count đã có ở v3.0.
- `--monthly-growth`: capability tháng hoàn tất so tháng liền trước, mặc định tắt.
- Model mặc định `datamate-business24`, confidence 0.95. Generator cần `--enable-slow-path` để gọi model; diagnostics bật Slow để đo toàn bộ pipeline.

## Replay không cần Colab

Chọn output directory mới cho mỗi lượt:

```bash
.venv/bin/python scripts/diagnose_hybrid.py \
  --dataset train --output-dir artifacts/v31-growth-train \
  --reuse-run artifacts/v3-period-train \
  --order-paraphrases --monthly-growth \
  --offline-replay --allow-execution-changes
```

Sau khi Train kiểm không có lỗi mới, đổi dataset thành `public_test`, reuse-run thành `artifacts/v3-period-public`, output-dir thành `artifacts/v31-growth-public`. Kiểm đủ ID, preservation và diff trước khi chọn candidate.

`--allow-execution-changes` chỉ hợp lệ với offline replay. Nó cho phép SQL execution/guards thay đổi nhưng vẫn khóa checkpoint, generation settings, prompt-generator source và full prompt/system prompt. Chỉ raw responses được dùng lại; SQL/labels/kết quả không được cache. Nếu thiếu prompt, run phải lỗi và không xuất candidate. Không gọi network thay thế âm thầm. Đây là replay dưới source mới, không phải live model validation.

## Generator tương đương

Khi cần model thật và đã có endpoint mới:

```bash
.venv/bin/python scripts/generate_public_submission.py \
  --enable-slow-path --base-url "$BUSINESS24_BASE_URL" \
  --model datamate-business24 --order-paraphrases --monthly-growth \
  --output artifacts/v31-generator/submission.jsonl
```

Generator kiểm provenance từ server so với split/data local trước inference. Không dùng `--overwrite` vào root release cũ. Nếu chỉ thử deterministic, bỏ `--enable-slow-path`; kết quả đó không được gán là full Hybrid.

## Đọc kết quả

`monthly_growth`/`monthly_growth_path` là deterministic, tách khỏi model Slow và count fallback. Trace có hai doanh thu tháng, khoảng ngày, cột/table và lý do từ chối. Public `correct`/target phải null. Score mới chỉ xác nhận sau người dùng nộp đúng candidate và gửi receipt; khác SHA chưa có nghĩa chắc chắn tăng điểm.

Kiểm biên tháng không chứng minh mọi giao dịch ở giữa đã xuất đủ. Các doanh nghiệp SFT-excluded đã tham gia development trước đó, không gọi kết quả là untouched validation.
