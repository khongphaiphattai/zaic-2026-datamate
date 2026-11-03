# v3.1 capability: tăng/giảm doanh thu tháng liền trước

## Bằng chứng chọn phạm vi

Kiểm kê từ khóa: 118 câu Train-24/20 business, 53 câu Public/12 business thuộc nhóm tăng trưởng doanh thu rộng (có chồng lấn). Chỉ dùng câu Train-24 để xây grammar tháng tuyệt đối so tháng liền trước; bản grammar trong `artifacts/v31-coverage/monthly-growth-design.json` chốt trước khi đo các nhóm khác.

Probe 61 câu/15 business Train-24: 22 đúng/0 sai sau kiểm metric một bảng, 39 từ chối. Đây là thăm dò đã dùng labels để đánh giá; không phải untouched validation. Không mở JOIN/MTD/YoY hoặc bỏ metric guard để tăng số lượng.

## Thiết kế triển khai được chọn

Fallback deterministic opt-in, chạy sau pipeline v3.0 đã abstain; model không cần tính số học. Whole-question grammar xác định tháng/năm tuyệt đối và phép so với tháng liền trước. Công thức `(current - previous) / previous * 100`, làm tròn hai chữ số, giữ dấu âm khi giảm. Hai tổng cùng metric/cùng table/cùng quy ước trạng thái.

Hợp đồng dữ liệu fail-closed trước query:

- Chỉ một bảng bán hàng chi tiết, README xác nhận line grain và cột thành tiền sau giảm qua metric verifier hiện có.
- README scoped đúng bảng xác nhận cột mã đơn, ngày bán, trạng thái đơn; không đoán từ tên cột đơn lẻ.
- IDs/ngày/trạng thái/tiền không null, IDs không trống, ngày parse được, tiền hữu hạn không âm. Mỗi đơn chỉ có một ngày và một trạng thái; từ chối khi mâu thuẫn.
- Chỉ chấp nhận trạng thái đã quan sát trong Train-24 được hỗ trợ: hoàn thành, hủy, hoàn/trả hàng, đang giao dịch/giao hàng. Dùng cùng quy ước loại hủy/trả của resolver hiện tại; trạng thái lạ phải abstain. Đây là tập hữu hạn, không được tự mở theo Public.
- Cả hai tháng hoàn tất trước/đến as_of_date; min/max ngày dữ liệu phải bao phủ cả tháng trước và tháng hiện tại. Điều này là kiểm biên bảo thủ, không chứng minh mọi giao dịch ở giữa đã được xuất đủ.
- Query tự sinh khoảng ngày từ calendar, không nhận WHERE từ model. Mẫu số >0, tử số hiện tại >0; còn lại abstain. Không suy unanswerable chỉ vì một tháng không có dòng.

## Ranh giới

Từ chối cùng kỳ năm trước, MTD, khách/sản phẩm/kênh/ca, ngưỡng tiền, nhiều tháng, câu boolean, tháng không hợp lệ/tương lai/đang diễn ra. Không đổi Fast/Slow/count đã có đáp án. Route riêng `monthly_growth_path`, audit giữ hai tổng/khoảng ngày/metric/reason; hai entrypoint cùng cờ `--monthly-growth`.

## Nghiệm thu

Test synthetic với dòng ngoài biên, năm chuyển tiếp, đơn nhiều dòng, hủy/trả, phần trăm âm, zero denominator, thiếu metadata, null/non-finite, mixed order dates/status, unknown statuses và điều kiện ngôn ngữ bị cấm. Grammar/tests chốt trước đo SFT-excluded/Public. Full Train so từng đáp án cũ; nếu có lỗi mới phải điều tra, không hardcode ID/nhãn. Public chỉ đếm diff và kiểm output; candidate riêng, không thay root v3.0 đã nộp.
