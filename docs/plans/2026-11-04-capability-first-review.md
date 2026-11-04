# Rà lại trọng tâm v3.1 — 04/11/2026

## Kết luận

Kế hoạch chỉ sửa prompt rồi vượt bộ14+24câu chưa đủ để giải quyết mục tiêu
tăng đáp án đúng ròng trên các doanh nghiệp. Nó xử lý lỗi nhận diện điều kiện,
nhưng chưa chứng minh dữ liệu/metric/compiler phía sau có thể trả lời. Thay
thứ tự công việc bằng đánh giá khả năng thực thi trước, sửa nhận diện sau.
Không gọi model hoặc thay production source trong lượt review này.

## Bằng chứng mới

Đối chiếu sáu positives của probe với trace Train đã đo, chạy resolver tháng
hiện có trên ba câu full-month chuẩn hóa thủ công từ intent câu hỏi, kiểm
README roles/metric cho cả sáu business. Không lấy numeric target để sinh
câu chuẩn hóa. Đây là chẩn đoán khả năng tính toán khi đã biết đúng intent;
không phải kết quả pipeline tự động hoặc bằng chứng trên Public.

| Trường hợp | Quan sát |
| --- | --- |
| biz102 full-month | Đã được monthly path trả đúng; không phải đáp án tăng thêm |
| biz104 full-month có lỗi gõ | Chuẩn hóa câu hỏi cho resolver cũ ra1.54, khớp Train; có tiềm năng mở một đáp án |
| biz120 full-month | Chọn thời điểm tạo thay ngày bán; metric header cũng chưa qua contract |
| biz105 và117 MTD | Chọn cột Giờ thay cột README định nghĩa ngày bán; metric cũng bị chặn; chưa chạy compiler MTD |
| biz122 MTD | Roles/metric qua kiểm tra hiện có; chưa có compiler MTD, chưa kiểm đầy đủ dữ liệu/phép tính |

README biz105 ghi `Ngày giao dịch: ngày bán`, biz117 ghi `Ngày lập: ngày bán`;
`Giờ` là giờ đặt đơn hoặc giờ trả hàng. Biz120 phân biệt `Thời gian: ngày bán`
và `Thời gian tạo: thời điểm đặt đơn`. Đây là bằng chứng cần sửa cách chọn vai
trò cột dựa trên tài liệu, không phải bỏ guard để chấp nhận cột đã chọn sai.

Sổ bán hàng ghi trả hàng bằng số lượng âm; hóa đơn header có tổng trước giảm
giá đơn và bảng chi tiết riêng. Chưa chứng minh có thể dùng chung SUM/status
contract hiện tại cho các dạng này. Không đổi date column rồi tuyên bố đã
giải quyết revenue grain, returns hoặc discount.

Audit chi tiết: `artifacts/structured-intent-plan-review/feasibility-audit.json`.
Phạm vi chỉ sáu positives Train-24; chưa phải thống kê toàn bộ cơ hội cải thiện.
Con số53Public trong inventory là khớp từ khóa chồng lấn, không phải53câu có
thể tính được. Monthly trước đây tăng22Train nhưng0Public là lý do phải đo
khả năng thực thi, không chỉ tăng điểm intent.

## Thứ tự sửa lại và tiêu chí dừng

### 1. Đo cơ hội thực thi trên Train-24, chưa cần GPU

Lập tập câu growth còn abstain, phân nhóm full-month/MTD/filtered/YoY/không
rõ, kèm business và dạng dữ liệu. Nhãn intent chẩn đoán chỉ từ câu hỏi;
numeric target chỉ dùng chấm kết quả sau khi tính. Ghi từng bước: đúng phạm
vi → roles/metric đủ bằng chứng → dữ liệu hợp lệ → compiler tính được →
đúng/sai so Train. Phân biệt thiếu dữ liệu, thiếu bằng chứng và lỗi resolver.

Nghiệm thu: có số câu và business ở từng bước, lý do loại, ít nhất một lát
cắt từ câu hỏi đến đáp án có khả năng mở câu mới ở nhiều business. Nếu MTD
chỉ có giá trị ở một schema hẹp hoặc phần lớn cần contract chưa có, so lại
với ứng viên capability khác trước khi tiếp tục đầu tư model. Không đặt chỉ
tiêu điểm Public từ inventory hoặc sáu mẫu đã chọn.

### 2. Kiểm chứng resolver/metric và compiler offline theo lát cắt đó

Ưu tiên dùng định nghĩa cột theo đúng bảng trong README để chọn ngày bán,
mã đơn và trạng thái; từ chối xung đột/thiếu chứng cứ. Test đối chứng cột ngày
bán cùng tồn tại với ngày tạo, giờ trả hàng, ngày nhập/giao; không map theo
business ID. Contract metric phải xác định grain, giảm giá và trả hàng;
không tự mở JOIN. Nếu cần JOIN phải có spec khóa/cardinality riêng.

Có thể thiết kế/test compiler MTD offline với intent đã xác định, dù model
chưa đạt gate. Tách tính đúng của compiler khỏi độ tin cậy extractor. Quy
ước ngày cùng kỳ, cap tháng ngắn, ngày28/29/30/31, năm nhuận/chuyển năm phải
được chốt và kiểm; thêm0denominator, missing data, returns/status controls.
Chưa gắn bất kỳ output model chưa kiểm chứng nào vào production.

Nghiệm thu: deterministic path có đáp án đúng mới được đo trên nhiều business,
negative tests chứng minh không đổi metric/ngày/grain; baseline cũ không
regress. Nếu không đạt, sửa contract/dữ liệu trước thử prompt.

### 3. Sửa lỗi làm mất điều kiện ở ranh giới model–compiler

Ba false accepts là lỗi semantic mà JSON/date validator không kiểm được.
Contract hiện tại chỉ có intent/period/year/month, kỳ so sánh và bộ lọc bị
ẩn. Thiết kế đề xuất phải thể hiện cả kỳ hiện tại, quan hệ kỳ so sánh và
các điều kiện trong câu; đối chiếu lại câu gốc. Model tự nói `filters=[]`
hay trích một đoạn câu không tự chứng minh đã đọc hết điều kiện. Phần chưa
kiểm được phải abstain. Compiler không được mặc định tháng trước chỉ vì
nhận `full_month`, bỏ qua năm so sánh trong câu.

Prompt kiểm loại trừ là một thí nghiệm của bước này, không phải toàn bộ giải
pháp. Chốt contract/validator trước nếu cần đổi schema; không vừa sửa vừa
chấm trên cùng tập rồi gọi đó là validation độc lập.

### 4. Đo lợi ích của model sau khi đường tính có giá trị

Giữ14ca đã xem làm regression; chuẩn bị tập development mới trước tune,
có cặp câu chỉ khác ca/kênh/khách/YoY và có phân bố schema/business đa dạng.
So cùng compiler giữa đường nhận diện deterministic và model: model thêm
bao nhiêu đúng, bao nhiêu sai, gọi bao nhiêu lần. Không cần base-model
ablation ngay nếu khả năng tính toán còn bị chặn. Chỉ dùng đối chứng base
với adapter khi cần quyết định có phải sửa/train model không.

Tách ba thước đo: model đúng định dạng, validator chặn được output sai, và
pipeline thực sự trả lời đúng. Bốn negative bị validator chặn là hành vi an
toàn của hệ thống dù model chưa trả abstain đúng. Tiêu chí an toàn chính là
không lọt câu sai vào đường trả lời; tỷ lệ model tự abstain là chẩn đoán phụ.
Pass14/24ca chỉ là gate phát hiện lỗi, không chứng minh precision95–100%.

Ngân sách14rồi24calls trước đó trở thành đề xuất có điều kiện sau bước1–3;
chưa mở lượt GPU tiếp hoặc mặc định phải chạy đủ38. Số ca sẽ chốt theo
contract và câu hỏi thí nghiệm; không tự training. L4 đủ cho probe ngắn đã
đo; A100 chỉ khi có nhu cầu bộ nhớ/latency đo được.

### 5. Kiểm từ đầu đến cuối trước release

Chốt source/config, full Train đo đúng/sai/abstain tăng thêm theo business,
bảo toàn output cũ và tách model contribution khỏi compiler contribution.
Đo Public bằng đúng source đã chốt, không sửa theo từng câu/ID/target. Chỉ
đếm coverage/diff và lý do guard vì Public không có nhãn. File không đổi
thì dừng release; file khác cũng chưa chứng minh tăng điểm, cần điểm portal.

## Việc chưa làm trong review này

Chưa sửa resolver, metric, prompt hoặc compiler; chưa đo rộng Train/Public,
chưa gọi GPU, đổi checkpoint hoặc train. Quyết định dừng promotion của lượt
probe14vẫn đúng. Điều chỉnh ở đây là ưu tiên và cách đánh giá bước tiếp theo,
không diễn giải lại bộ probe đã fail thành pass.
