# Review chốt v3.0 — 03/11/2026

Kết luận: file đã chốt có thể dùng cho lần nộp xác nhận của người dùng; **không có thay đổi đáp án Public so v2.8**. Chưa có score mới. Không nâng phạm vi Slow Path trong phiên review này.

## Bằng chứng

`report.json` chứa kiểm tra hashes, IDs, source/config, correctness Train tính lại, routing funnel và hai ca gọi trực tiếp Colab. `served-model-provenance.json` khớp revision/data/split/businesses đã chốt; đây là metadata do server khai báo, không phải chứng thực độc lập trọng số. Raw responses lưu local ở `live-trace.jsonl` (Git ignore).

| Phần đóng góp | Train thêm đúng | Public thêm đáp án |
|---|---:|---:|
| Model business24 qua Slow Path | 13 | 0 |
| Luật canonical count | 49 | 0 |

Public revenue grammar khớp 11 câu: 10 đã có đáp án Fast, 1 còn lại bị policy một bảng chặn. Count grammar khớp 2 câu: cả hai đã có đáp án Fast. Giới hạn hiện tại là phần việc được giao cho model chưa chuyển thành đáp án Public được chấp nhận. Chưa có phép so sánh base-model/LoRA cùng điều kiện để quy 13 câu đúng riêng cho hiệu quả fine-tuning.

Kiểm tra trực tiếp không cache, tuần tự, tổng **3 network inference calls**:

- Train `q_1f8c7dce062c` (biz_102): model → SQL → **9.078.100**, đúng target, khớp trace cũ; 1 call, 25,95 giây.
- Public `q_acb6a360a60d` (dev_010): model đưa SQL JOIN, vẫn **abstain vì one-table policy**, khớp trace cũ; 2 calls, 26,58 giây. Không biết đáp án đúng của Public, không thực thi JOIN bỏ guard để điền đáp án.

Hai mẫu xác nhận đường gọi model đang hoạt động và tái hiện hành vi đã ghi. Chúng được chọn có chủ đích, không phải phép đánh giá generalization mới. Tổng Train vẫn 818 đúng / 6 sai / 2.176 abstain; 159 tests pass là kết quả lượt implementation trước, không phải một suite vừa chạy lại trong review này.

## Phát hiện và việc cần xử lý trong v3.1

1. **Required trước khi mở gate:** `revenue_metric.py` kiểm metric/shape, chưa chứng minh WHERE/date/status/table choice. `confidence >= 0.95` do model tự khai không bảo đảm đúng 95%. Cần kiểm temporal/status và bằng chứng cardinality nếu hỗ trợ JOIN; fail-closed khi không đủ bằng chứng. Không ghi nhận việc này đã làm sai Public v3.0 vì Public chưa nhận đáp án Slow nào.
2. **Required trước khi thống nhất phát hành:** generator thông thường chỉ dùng `HybridAgent`, chưa có count wrapper; mặc định tắt Slow và model name là `datamate`. Diagnostics của release có wrapper, Slow bật, model `datamate-business24` và kiểm provenance. Người chạy nhầm entrypoint có thể thiếu tính năng mà báo cáo đo đã dùng. Tạm dùng đúng diagnostics command; v3.1 cần chung factory/config và kiểm tra tương đương hai entrypoint.
3. **Tối ưu chi phí:** lỗi one-table policy vẫn vào self-correction. Trên dữ liệu đã đo: 9 ca Train tốn 9 retry không tạo đáp án; ca Public vừa tái hiện cũng tốn 1 retry. Tách policy error khỏi syntax error, replay kiểm không mất đáp án trước khi bỏ retry. Không khẳng định mọi policy retry ở mọi dữ liệu đều vô ích.
4. **Độ chính xác tài liệu:** docstring `hybrid_agent.py` còn mô tả 0ms, 99.87% và khả năng complex long-tail vượt phạm vi gate hiện tại. Cần sửa cùng lượt cập nhật source/cache của v3.1; không dùng các dòng đó làm bằng chứng chất lượng.

Các hạng mục được hoãn có chủ đích theo yêu cầu review tối nay/triển khai phiên sau. Bản release và inference source không thay đổi. Không phát hiện sai lệch file chốt trong các kiểm tra trên; review không bao phủ mọi dòng code, dependency security, GPU reproducibility hay Docker offline.

Kế hoạch và tiêu chí nghiệm thu: `docs/plans/2026-11-03-improvement-plan.md`. Chưa cần training mới; chỉ cân nhắc khi thí nghiệm phân biệt được lỗi model với lỗi gate/schema/execution.
