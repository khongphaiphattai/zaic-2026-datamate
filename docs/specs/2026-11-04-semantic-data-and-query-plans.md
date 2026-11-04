# Spec: dữ liệu nghiệp vụ và kế hoạch truy vấn có kiểm chứng

## Problem Statement
v3.1 đạt Public0.13500 theo người dùng, tăng0.00100. Model chưa có đáp án
Public được chấp nhận. Coverage thấp và độ đúng Train không đại diện Public.
205 câu trả lời và135điểm ròng hàm ý170đúng/35sai nếu chấm toàn bộ1000câu
đúng artifact theo+1/−1/0; không biết lỗi từng câu.

## Solution
Ưu tiên kiểm độ đúng baseline, rồi tạo hợp đồng dữ liệu nghiệp vụ có nguồn
chứng minh dùng chung cho nhiều phép tính. Model đề xuất kế hoạch đầy đủ
metric/thời gian/filters/group/comparison/output. Compiler thực thi khi kiểm
được ngữ nghĩa và dữ liệu; thiếu chứng cứ thì abstain. Không dự báo điểm.

## User Stories
1. Là người dự thi, tôi muốn biết model thực sự thêm bao nhiêu đúng/sai.
2. Là người dự thi, tôi muốn phát hiện lỗi trong đáp án đang trả lời.
3. Là người vận hành, tôi muốn phân biệt suy luận tổng hợp với nhãn từng câu.
4. Là người dùng, tôi muốn ngày bán không bị nhầm với ngày tạo/trả hàng.
5. Là người dùng, tôi muốn doanh thu tính đúng giảm giá và trạng thái.
6. Là người dùng, tôi muốn JOIN không nhân đôi tiền theo dòng hàng.
7. Là người dùng, tôi muốn tỷ trọng giữ đúng kênh/sản phẩm và mẫu số.
8. Là người dùng, tôi muốn tăng trưởng giữ đúng kỳ và điều kiện.
9. Là người dùng, tôi muốn xếp hạng đúng đơn vị và thứ tự hòa điểm.
10. Là người vận hành, tôi muốn truy được quyết định về README/file/sheet.
11. Là người dự thi, tôi muốn sửa đáp án cũ khi có bằng chứng nghiệp vụ.
12. Là người vận hành, tôi muốn giữ artifact để tái lập và rollback.
13. Là người dự thi, tôi muốn so parser/base/adapter trên cùng execution.
14. Là người vận hành, tôi muốn giữ ngân sách GPU cho thí nghiệm có giá trị.

## Implementation Decisions
- Thực hiện thứ tự audit baseline → hợp đồng nghiệp vụ → kế hoạch truy vấn
  → ablation model → đánh giá frozen-source. Mỗi giai đoạn có báo cáo riêng.
- Grain đơn, dòng hàng, thanh toán, trả hàng tách rõ; mapping có nguồn file,
  sheet, README/rulebook, khóa và cardinality. Thiếu chứng cứ ghi unresolved.
- SQL chạy thành công hoặc JSON hợp lệ không chứng minh câu trả lời đúng.
- Giữ snapshot cũ làm đối chứng; cho phép sửa output cũ dựa trên nghiệp vụ,
  không dựa vào ID/numeric target Public. Không hardcode doanh nghiệp.
- Audit đầu: kiểm kê toàn bộ205 output, replay SQL có sẵn, lập bản đồ rủi ro
  theo business/metric/date/status; có nhãn Train để kiểm lỗi tương ứng.
- Bản rộng cần chia lát cắt theo grain; nhóm tỷ trọng là ứng viên kiểm khả thi,
  chưa quyết định mở toàn bộ128 câu trong audit Train cũ.

## Testing Decisions
- Seam chính là pipeline trả lời câu hỏi trên BusinessContext thật/fixture.
  Audit ban đầu dùng trace frozen + SQL đã chạy để kiểm tái lập, không gọi GPU.
- Tests quan sát type/value/refusal; controls chỉ khác kênh/ca/kỳ phải bắt lỗi
  mất điều kiện. Đảo thứ tự dòng không đổi tổng; dòng ngoài kỳ không ảnh hưởng;
  JOIN không nhân đơn. Chỉ kiểm tổng phân nhóm khi rời nhau/đầy đủ/cộng được.
- Chấm Train sau execution; không dùng target để tạo kế hoạch. Nhóm6 cũ đã
  dùng development, không gọi unseen. Bộ test mới phải freeze trước tuning.
- So deterministic/base7B/adapter7B cùng compiler; chạy model lần lượt.

## Acceptance Criteria / Tasks
- [x] Audit đủ205 IDs, route/type/business và phạm vi SQL replay; ghi mọi
  trường hợp không thể kiểm, không coi chúng là pass.
- [ ] Đối chiếu lỗi có nhãn Train với README/rulebook/dữ liệu; mỗi finding
  phân loại reproduced defect / risk / unresolved, có bước tái lập.
- [ ] Chọn lát cắt nghiệp vụ tiếp theo bằng số liệu feasibility trên Train;
  chốt spec/test trước sửa production.
- [ ] Hợp đồng chứng minh ngày/metric/grain/status và nguồn, từ chối xung đột.
- [ ] Báo đúng/sai ròng và mức đóng góp riêng của model, giữ controls an toàn.
- [ ] Full Train/Public cùng source/config freeze; snapshot/hash/receipt đúng.

## Out of Scope
Training ngay, thả SQL tự do, dò nhãn Public bằng nhiều submissions, đổi GPU,
JOIN chưa có hợp đồng khóa, cam kết precision100% hoặc score chưa đo.

## Further Notes
Người dùng ngày04/11/2026 yêu cầu chuyển nội dung đã trao đổi thành spec và
bắt đầu công việc ưu tiên đầu tiên; đây là authorization cho audit local.
Issue tracker/triage chưa cấu hình: lưu spec local, chưa publish tracker.
Tham chiếu chiến lược sau v3.1 và ROADMAP để đọc bằng chứng/số liệu chi tiết.
