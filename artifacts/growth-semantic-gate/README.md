# Semantic boundary local — 04/11/2026

**Raw question→certifier→compiler:17đúng,0sai,264abstain trên281câu.**
Đây là shadow deterministic trên tập Train-24 development đã xem, chưa là
pipeline production hoặc Public score. Không dùng nhãn intent/ID để quyết
định thực thi. Targets chỉ chấm sau khi tính,0model calls.

| Bước | Kết quả |
| --- | --- |
| Tập truy hồi đã review | 281câu còn abstain theo baseline |
| Chứng nhận được toàn bộ câu | 77:39full-month,38MTD |
| Đề xuất sai phạm vi lọt qua | 0 trên182câu ngoài capability |
| Qua cả compiler/guards | 17MTD đúng tại3business |
| Bị execution guards chặn | 60:39full-month,21MTD |
| Chưa chứng nhận được wording | 204:182ngoài capability,22wording hỗ trợ về nghĩa nhưng ngoài grammar |

17đúng:biz121(4),biz122(9),biz130(4). So18đúng với intent đã biết ở bước
compiler, mất1câu có `hown`/`cungf` ngoài grammar hiện có. Không sửa grammar
sau đo để tăng báo cáo.17giá trị khớp lượt kiểm Pandas/Decimal trước đó.

## Cổng làm gì và giới hạn

`certify_growth_question` chỉ nhận question/as_of. Nó đối chiếu toàn câu với
grammar hữu hạn, trả explicit comparison và hai date windows; không xóa tùy
ý hậu tố hay chặn theo vài từ khóa. Full-month dùng nguyên grammar cũ; MTD
dùng cách nói đã quan sát trên Train-24. Đây là mở một capability có compiler
đã đo, không phải bộ hiểu ngôn ngữ tổng quát.

`validate_growth_proposal` bắt buộc intent/period/year/month/comparison/filters,
reject keys thừa/trùng, kiểu sai, legacy4fields, kỳ sai và filters không rỗng.
Một proposal khai `filters=[]` vẫn bị từ chối nếu câu gốc không được chứng
nhận độc lập. Không tự điền comparison bị thiếu. Module không chạy SQL và
chưa được import bởi production factory/wrapper.

Vì proposal chỉ được nhận nếu đồng ý với certifier đã hiểu được câu, gọi
model ở nhánh này hiện không thêm độ phủ. Không ghi17đúng là đóng góp AI.
Model có thể hiểu wording khác, nhưng cổng này chưa đủ bằng chứng cho phép
nhận các câu đó. Muốn mở rộng cần thí nghiệm riêng, không bỏ guard để lấy
thêm đáp án. Unknown wording là abstain, không phải unanswerable.

## Controls và hồi quy

-24synthetic controls chốt trước implementation:8positive đúng và16negative
  bị chặn, kể cả proposal giả hợp lệ cố tình bỏ filter hoặc đổi comparison.
  Các negatives xuất phát từ base phrase được hỗ trợ; không dựa vào việc
  base phrase vốn đã bị parser từ chối. Đây vẫn là development controls.
-14câu probe cũ:4/6positive được nhận,8/8negative bị chặn. Hai full-month
  positives ngoài grammar bị abstain, ghi là mất recall. Đây là chạy lại
  câu hỏi qua certifier local, không phải đo model mới hoặc sửa kết quả L4.
-**300tests pass (25.85s)** toàn suite; một cảnh báo pandas đã biết ở fixture
  ngày sai. TDD tái hiện API còn thiếu trước implementation; tests kiểm cả
  minimal pairs, wrong period/filter/types/duplicate/legacy proposals.
-`run-01/verification.json` kiểm đủ281IDs,77plans đúng nhãn tham chiếu,
  17numeric matches,24controls,14probe counts và source hashes giữ nguyên.
  Zero observed errors trong mẫu không chứng minh precision100% ngoài mẫu.

Tái lập:

```bash
.venv/bin/python -m scripts.evaluate_growth_semantics --output-dir artifacts/growth-semantic-gate/run-02
```

## Review và quyết định tiếp theo

Review phạm vi/code: toàn bộ câu phải match, không ID/business-specific rules,
không nhận metadata model thay bằng chứng câu gốc, không mở pipeline/model
prompt cũ. Compiler tiếp tục guard dữ liệu và metric, không thay đổi code
tính MTD đã đo. Kết quả/schema/proposal độc lập với numeric targets.

Chốt source này trước bước tích hợp opt-in vào factory dùng chung. Bước tiếp
theo: bảo toàn mọi đáp án cũ, thêm route MTD riêng, full Train rồi Public bằng
offline replay đã hỗ trợ; kiểm số đúng/sai tăng thêm, route accounting và
candidate diff. Không dùng Public để thêm wording. Chưa có lý do gọi GPU
cho nhánh MTD chỉ để xác nhận lại câu certifier đã hiểu; vẫn giữ AI Slow Path
cũ trong baseline. Nếu Public vẫn không đổi, ghi rõ0gain và phân tích schema
coverage trước mở rộng tiếp, không resubmit file trùng.

Root v3.0 vẫn SHA
`288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`.
Chưa integration/full Train/Public hoặc submission mới trong bước này.
