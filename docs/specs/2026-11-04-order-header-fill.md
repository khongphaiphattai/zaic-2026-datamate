# Bounded fix: điền ô trống trong cùng đơn

Authorization: người dùng yêu cầu triển khai ưu tiên audit/sửa lỗi có bằng
chứng theo spec semantic-data-and-query-plans.

Rulebook§8 chỉ cho phép kế thừa trường header trong cùng nhóm đơn. Loader
hiện ffill toàn bảng, làm khách/trạng thái của đơn trước rơi vào đơn mới.
Reproducer clean_dataframe với A/kháchLan vàB/kháchtrống đã xác nhận lỗi.

Contract: khi có cột mã đơn được hỗ trợ, điền mã đơn trống như trước để xác
định nhóm; trường khách/trạng thái chỉ ffill trong cùng mã đơn. Ngày giữ cơ chế điền
hiện có vì ô ngày có thể gộp theo ngày qua nhiều hóa đơn; chưa thay contract
nhóm ngày. Replay thử cho thấy group ngày theo đơn làm đổi9đáp án dev013,
không được promote cách diễn giải đó. Đơn mới có ô
header đầu nhóm trống vẫn trống, không tự gán hoàn thành/khách lẻ. Đơn xuất
hiện lại kế thừa header trước của chính nó. Không backfill tương lai, không
thay parser/metric/status policy. Bảng thiếu khóa giữ hành vi cũ, là giới hạn
chưa giải quyết; nhiều cột mã chọn cùng thứ tự ưu tiên hiện tại.

Acceptance: tests chứng minh kế thừa trong đơn, chặn khách và trạng thái
xuyên đơn, xử lý ID trống và đơn xuất hiện lại; fullsuite qua. Chưa promote
submission hoặc gán scoregain; phải full evaluation riêng trước release.
