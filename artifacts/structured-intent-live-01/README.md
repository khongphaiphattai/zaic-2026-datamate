# Structured intent trên L4 — 04/11/2026

**Không đạt cổng nghiệm thu; chưa mở compiler MTD hoặc tích hợp extractor vào pipeline.**
Đã chạy đúng 14 calls tuần tự, không retry, không cache, trên model business24.
Cases/prompt/extractor giữ đúng hash trước đo; metadata model khớp data/split/base revision.
Đây là bộ development đã tuyển chọn, không phải đánh giá unseen.

| Nhóm | Kết quả |
| --- | --- |
| 6 câu hỗ trợ | 6/6 đúng intent, period, year, month |
| 8 câu cần từ chối | 1/8 trả đúng abstain |
| Bộ kiểm tra chặn thêm | 3 schema không hỗ trợ + 1 tháng tương lai |
| Nhận nhầm còn lọt | 3/8: ca sáng, kênh Shopee, so cùng tháng năm trước |
| Lỗi HTTP/network/OOM quan sát | 0 |

Cả 14 raw responses đều là JSON có cú pháp hợp lệ. `report.errors=4` là lỗi
schema/date, không phải lỗi kết nối hoặc JSON hỏng. Tổng cộng 5/8 negatives bị
chặn (1 model abstain +4 validator rejection); không ghi thành 5 model abstentions.
Các false accepts chỉ là intent được nhận trong phép thử shadow; không có SQL
hoặc đáp án số được thực thi/xuất cho chúng.

## Nguyên nhân quan sát và giới hạn kết luận

- Boolean nhiều tháng: model tự phát minh `boolean_revenue_trend/multi_month`.
- Khách Lan/sản phẩm A: model giữ điều kiện bằng field ngoài contract; strict schema chặn đúng. Không bỏ field để cứu output.
- Ca sáng/Shopee: model làm rơi điều kiện rồi xuất JSON hợp lệ cho toàn cửa hàng.
- So tháng 3/2024: model xuất `full_month` tháng 3/2025, khiến baseline ngầm thành tháng 2/2025. Đây là đổi phép so sánh, không phải lỗi parse ngày.
- Tháng tương lai: model vẫn nhận, nhưng validator chặn. Prompt injection: trả abstain đúng trong một ca này.

Không quan sát thấy SQL output hoặc JSON sai cú pháp, nên chưa có bằng chứng
cho kết luận checkpoint chỉ biết sinh SQL. Prompt chưa đủ mạnh, khả năng tuân
thủ phạm vi của checkpoint, hoặc cả hai đều có thể giải thích lỗi. Chưa có
đối chứng base model hay prompt khác để tách nguyên nhân. GPU lớn hơn không
phải cách sửa đã được chứng minh cho lỗi mất điều kiện.

## L4 và chi phí thực thi quan sát

Server khai báo NVIDIA L4, BF16/SDPA, batch1, tổng VRAM22.03GiB và peak
allocated lúc startup14.2GiB. 14 request hoàn tất, tổng latency client28.115s,
median1.875s, khoảng0.861–2.946s. Có thời gian network/tunnel; không phải GPU
kernel timing hay tổng thời gian Colab được cấp. Không có số đo units hoặc
đối chứng A100. Startup peak không gồm memory reserved/tiến trình khác và
không đo peak của từng request; chưa kiểm tải context8192tokens.

Tiếp tục L4 cho phép thử ngắn batch1. Chỉ đề xuất A100 ở phiên sau nếu workload
thực cần gây OOM trên L4 hoặc latency đo được không đáp ứng ngân sách. Không
cần đổi GPU để xử lý ba lỗi ngữ nghĩa hiện tại.

## Bước tiếp theo theo kết quả fail

1. Giữ bộ14 làm regression đã biết. Thiết kế một prompt sửa, kiểm điều kiện loại trừ trước khi điền intent: giữ mọi bộ lọc và kiểm rõ cả kỳ hiện tại lẫn kỳ so sánh. Ví dụ hướng dẫn phải khác câu kiểm thử; không hardcode ID/Shopee/ca sáng để làm đẹp kết quả. Giữ strict JSON/date validator.
2. Chốt prompt/hash và chuẩn bị24 câu development mới trước khi đo: 8 positive,16 negative, gồm biến thể MTD/tháng hoàn tất/năm nhuận/chuyển năm và điều kiện khách/sản phẩm/ca/kênh/YoY/boolean/thời gian tương lai/thiếu kỳ/prompt injection. Lưu lịch sử tuning; không gọi bộ mới là untouched vì thiết kế từ lỗi đã xem.
3. Phiên đo kế tiếp đề xuất tối đa38 calls:14 regression với prompt mới trước; chỉ nếu6/6 positive,8/8 negative,0 schema/date errors thì đo24 câu mới một lần/câu. Có false accept thì dừng mở rộng; không tự train. Chưa thực hiện các calls này trong phiên hiện tại.
4. Chỉ khi các cổng trên qua mới viết spec/compiler MTD và test ngày28/29/30/31, năm nhuận/chuyển năm, cap ngày tháng trước, doanh thu0, status/grain/schema. Compiler tự nó không phát hiện một điều kiện đã bị extractor làm rơi.
5. Nếu prompt sửa vẫn nhận nhầm, thiết kế đối chứng base model cùng revision/prompt/cases để phân biệt ảnh hưởng adapter trước khi đề xuất retraining. Cần chuẩn bị serving riêng và ngân sách; chưa đổi model hoặc notebook đang chạy.

## Bằng chứng và kiểm tra

- `results.json`: toàn bộ question/expected/raw/parsed/error và phân loại từng ca; giữ được trong Git dù `trace.jsonl` bị ignore.
- `report.json`, `config.json`, `provenance.json`: output runner nguyên bản.
- `verification.json`: kiểm lại IDs/order, hashes, parser, counts, provenance, latency và root/release/baseline SHA.
- 54 tests liên quan pass (1.45s): intent, monthly growth, recovery, API. Không sửa production source trong lượt đo này.
- Root `submission.jsonl` vẫn SHA `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`; v3.0 score0.13400 giữ nguyên, không có submit/release mới.
