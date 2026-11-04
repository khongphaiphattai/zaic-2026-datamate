# Replay bản sửa khách/trạng thái theo mã đơn

Đây là replay SQL đã lưu của205đáp án v3.1 trên loader đã sửa; không chạy lại
parser/model, không kiểm coverage hoặc correctness Public. So summary.json
và source-manifest.json. 3unanswerable không có SQL, có audit thiếu địa chỉ
ở báo cáo baseline. Không xuất candidate/submission hoặc gán score.

Bản thử trước scope cả ngày làm9outputdev013thayđổi; không dùng làm release.
Bản cuối giữ fillngày và chỉ scope customer/status theo mãđơn. Fullsuite321pass.

Kết quả cuối: **202matches,0differs,3not_available**. Source hashes không đổi
trong lượt chạy. Đây là preservation của frozenSQLoutputs, không phải xác nhận
đáp án đúng hoặc fullpipeline equivalence. Rootv3.1hash giữ nguyên.
