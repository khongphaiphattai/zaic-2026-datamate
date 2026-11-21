CÁC CÂU HỎI THƯỜNG GẶP
ZALO AI CHALLENGE 2026
*Update lần cuối: 12h00 09/11/2026
1. Thí sinh dưới 18 tuổi có được tham gia Zalo AI Challenge 2026 không?
Có. Thí sinh dưới 18 tuổi vẫn có thể đăng ký và tham gia Zalo AI Challenge 2026 theo các điều kiện tương ứng với độ tuổi như sau:
1. Nếu bạn từ đủ 15 đến dưới 18 tuổi: ở bước đăng ký ban đầu, thí sinh không cần cung cấp giấy xác nhận của cha mẹ hoặc người giám hộ hợp pháp. Bạn có thể tiến hành đăng ký và tham gia cuộc thi bình thường. Trong trường hợp đội của bạn đoạt giải và làm thủ tục nhận tiền thưởng, Ban Tổ chức sẽ có hướng dẫn chi tiết và có thể yêu cầu bổ sung một số giấy tờ/xác nhận cần thiết theo quy định pháp lý tại thời điểm đó. 
2. Nếu bạn chưa đủ 15 tuổi: các thí sinh chứng minh rằng các bạn đã được cha, mẹ, người giám hộ của các bạn đồng ý bằng văn bản cho các bạn tham gia cuộc thi. Bạn vui lòng cung cấp tài liệu chứng minh trước ngày 16/11/2026 - "Submit solutions" deadline.
* Các tài liệu này bao gồm:
   * (i) Bản gốc Giấy xác nhận (theo mẫu đính kèm này);
   * (ii)Bản sao được chứng thực của: Giấy khai sinh của thí sinh, CMND/CCCD của thí sinh và của cha, mẹ, người giám hộ của thí sinh.  
2. Làm thế nào để rời đội? 
Nếu muốn rời khỏi đội đã tham gia, thí sinh vui lòng liên hệ Trưởng nhóm để được xóa khỏi danh sách thành viên của đội.
3. Làm thế nào để xóa đội?
Đội trưởng có thể thực hiện xóa đội với nút “Delete Team” trong mục “Manage Team”. 
Lưu ý: Chỉ có thể thực hiện xóa đội trong trường hợp đội chưa nộp solutions.
4. Làm thế nào để đội trưởng điều chỉnh thông tin của các thông tin thành viên?
Đội trưởng vui lòng thực hiện thao tác “Remove member” với thành viên cần điều chỉnh và thêm vào lại đội với thông tin đã được điều chỉnh.
5. Làm thế nào để gộp đội?
Để gộp hai đội thi, đội muốn giải thể cần thực hiện thao tác xóa đội trên hệ thống (áp dụng với các đội chưa từng nộp solutions). Sau đó, đội trưởng của đội được giữ lại thực hiện thao tác thêm thành viên để mời các thành viên từ đội đã giải thể tham gia vào đội của mình. 
6. Thời gian để điều chỉnh thông tin, thành viên trong đội và xóa đội?
Kể từ 00:00 ngày 16/11/2026, Ban Tổ chức sẽ vô hiệu hóa chức năng điều chỉnh thông tin, thêm/xóa thành viên, và xóa đội. Sau thời điểm này, đội thi không thể thay đổi danh sách thành viên.
Đội trưởng có thể thêm hoặc xóa thành viên trong đội theo thời gian quy định của cuộc thi. Các bài đã nộp của các thành viên đã bị xóa khỏi nhóm vẫn được tính cho Đội tại thời điểm cá nhân đó nộp 
7. Một đội có được phép đăng ký và tham gia đồng thời cả hai đề thi không?
Có. Mỗi đội được phép tham gia cả 2 đề thi
Giới hạn số lượt nộp bài mỗi ngày được tính riêng cho từng đề thi, không tính chung cho cả hai. Cụ thể: Mỗi đội được nộp tối đa 05 (năm) lượt mỗi ngày ở mỗi đề thi. Như vậy, nếu tham gia cả hai đề, đội của bạn có thể nộp tối đa 05 lượt/ngày cho đề 1 và 05 lượt/ngày cho đề 2. 






Câu hỏi chung thường gặp cho cả 2 track
1. Em thấy trong cả 2 track LifeMate và DataMate thì đều cho phép sử dụng bộ dataset public. Vậy đội thi có được đánh nhãn thủ công hay phải sử dụng phương pháp tự động gì trên bộ dataset public này không?
Tập public test không được sử dụng để train model và không được sử dụng trong quá trình inference.
2. Ngoài dataset public thì còn được synthesize data. Thì đội thi có được tự tạo thủ công bộ data của riêng mình bằng cách tự đi thu thập dữ liệu, tự đánh nhãn hoặc sử dụng model gọi API để tạo không?
 Được phép sử dụng data public và synthesis với việc bản đảm bảo đáp ứng license của tập dữ liệu.
3. Trong quá trình train và finetune, có được phép sử dụng một model to hơn (có kích thước lớn hơn quy định của đề bài) hoặc model gọi API bên ngoài, để thực hiện distill cho model nhỏ hơn không?
Được phép sử dụng trong quá trình tạo dữ liệu huấn luyện để thực hiện distill mô hình. Không được phép sử dụng trong quá trình inference. Môi trường inference sẽ hoàn toàn Offline và chỉ sử dụng các mô hình đúng kích thước và tuân thủ quy định của đề thi. 
4. Việc đóng Docker Image thì chỉ cần đóng Docker quá trình infer hay cần đóng Docker cả quá trình training và quá trình chuẩn bị bộ dữ liệu riêng. Hay nói cách khác là có cần reproduce lại cả quá trình train hoặc finetune (sử dụng model bên ngoài to hơn theo yêu cầu của đề bài hoặc model gọi qua API phải sử dụng internet) và synthesize data không (nếu được đánh data thủ công thì chỉ có thể cung cấp data chứ không thể reproduce được)?
Nộp cả code train và quá trình xử lý dữ liệu để training, cũng như dữ liệu bạn synthesis hay xử lý thủ công để training nhé.
5. Khi nào BTC công bố hướng dẫn Docker (base image, lệnh chạy, cấu hình GPU, cách mount /data và /result)?
Ban Tổ chức đã công bố hướng dẫn nộp Docker trong mục Submission Guidance của mỗi đề thi.
6. Yêu cầu giấy phép OSI ở Thể lệ mục 4.5 áp dụng cho trọng số mô hình hay chỉ cho mã nguồn?
Nếu mô hình của bạn có sử dụng mã nguồn mở, bạn chỉ được sử dụng các thư viện hoặc mã nguồn có giấy phép OSI hợp lệ (xem tại opensource.org), không giới hạn việc sử dụng thương mại, trừ trường hợp được chủ sở hữu quyền tác giả hoặc tác giả có thẩm quyền cấp phép đồng ý bằng văn bản cho sử dụng theo các điều kiện khác phù hợp với việc tham gia Cuộc thi. Trong quá trình trao đổi với sở hữu, CC email của Ban tổ chức để nắm được thông tin.
7. Giới hạn tham số: mô hình MoE tính theo tổng hay theo tham số kích hoạt?
Tính theo tổng tham số của model MoE.
8. Vòng private nộp 2 lần thì bài nào được tính làm kết quả chính thức?
Kết quả giống "Docker nộp cuối" là kết quả chính thức.
9. Đối với bảng đấu LifeMate hoặc bản đấu DataMate thì có đánh giá trên các tiêu chí khác như độ trễ,... không?
Tiêu chí đánh giá đầu tiên là score. Nếu cả 2 đội cùng điểm thì sẽ đánh giá dựa trên thời gian inference.
10. Cho em hỏi dữ liệu từ cuộc thi Zalo AI Challenge 2026 có thể sử dụng cho mục đích khác sau khi cuộc thi kết thúc không ạ?
Không, dữ liệu của cuộc thi Zalo AI Challenge 2026 không được phép sử dụng cho mục đích khác ngoài cuộc thi Zalo AI Challenge 2026. Các trường hợp chi tiết hơn bạn có thể gửi mail cho ban tổ chức sau cuộc thi. 
Câu hỏi thường gặp cho đề LifeMate
1. Giới hạn 30 giây/mẫu đo từ lúc nào đến lúc nào — có gồm thời gian nạp mô hình và nhận dạng giọng nói các lượt hội thoại trước (history) không?
Thời gian 30s/mấu đo từ lúc bắt đầu nhận đầu vào, nhận dạng giọng nói, .... cho đến khi có kết quả đầu ra. Các model nên nạp trước và 1 lần trước khi bắt đầu dự đoán tập test.
2. Dữ liệu tổng hợp từ tài liệu của tập public. Luật cho phép tạo dữ liệu tổng hợp bằng dịch vụ/model khác trong lúc phát triển. Đội có được dùng schema tool trong public_test_tools_catalog.json và bộ mô phỏng public_test/zaic_tools để sinh dữ liệu huấn luyện tổng hợp (do đội tự tạo yêu cầu và trạng thái app, không dùng mẫu, audio hay state.json của tập public) không? Việc này không phải gán nhãn mẫu test, nhưng đội muốn chắc chắn trước khi làm.
Không sử dụng dữ liệu public test để tạo sinh thêm dữ liệu. Bạn có thể sử dụng tập huấn luyện.
3. Dùng trạng thái app của tập train để tạo dữ liệu huấn luyện. Luật cấm lời giải/agent đọc thư mục <split>_environment/. Khi tạo dữ liệu huấn luyện (không phải lúc suy luận), đội có được lấy trạng thái app của tập train (qua ToolEnv.get_state() hoặc đọc train_environment/) làm điểm xuất phát rồi biến đổi để sinh mẫu mới không?
Tập train được sử dụng để huấn luyện, bao gồm cả sử dụng các dữ liệu trong train và môi trường để hỗ trợ.
4. Checkpoint sau fine-tune: Nếu model gốc được công bố trước mốc thời gian quy định, đội tự fine-tune ASR/LLM bằng dữ liệu train của cuộc thi sau mốc đó thì checkpoint mới có được sử dụng không? Có yêu cầu công khai checkpoint, adapter hoặc dữ liệu huấn luyện bổ sung không?
Đội huấn luyện mô hình mà sử dụng cho giải pháp thì cần nộp mô hình đã huấn luyện trong docker. Dữ liệu huấn luyện, quá trình huấn luyện, dự đoán đều phải mô tả rõ ràng để có khả năng ""reproduce"" khi cần kiểm chứng. Đội được khuyến khích công bố giải pháp lên các nền tảng sau cuộc thi - không bắt buộc. Lưu ý một số mô hình có license ""ND"" không cho phép công bố các bản finetune từ mô hình gốc.
5. Cách chấm tool calls: Read call và call thất bại có được tính vào F1 không? Một lời gọi đúng nhưng có thêm tham số tùy chọn hợp lệ sẽ được chấm thế nào? BTC có thể cung cấp evaluator offline hoặc ví dụ chấm cho các trường hợp này không?
Read Call và Call thất bại vẫn được tính vào F1. Một lời gọi đúng nhưng có thêm tham số tùy chọn hợp lệ vẫn đc tính khớp, scorer chỉ so tham số có trong đáp án, nhưng lưu ý với tool write nếu tham số làm thay đổi state thì sẽ 0đ
6. Hỏi lại người dùng: Sau khi gọi thành công sys.ask_missing_info, agent có phải kết thúc ngay lượt hiện tại không? Cách chấm yêu cầu khớp tool_name và danh sách params thế nào; hỏi thừa, thiếu hoặc khác thứ tự tham số có bị tính sai không?
Việc chấm điểm thực hiện dựa trên các lời gọi công cụ được ghi nhận bởi môi trường.
Khớp khi tool_name giống tên tool trong đáp án và tập params giống hệt. params không xét thứ tự; hỏi thừa hoặc thiếu một tham số, hoặc sai tên tham số so với schema, đều tính là không khớp. Chỉ hỏi tham số bắt buộc còn thiếu, không hỏi tham số tuỳ chọn.
7. Cách tính giới hạn tham số: Tổng tham số có bao gồm ASR, LLM, embedding, reranker và router không? Với MoE tính toàn bộ tham số hay chỉ phần được kích hoạt? Adapter LoRA được tính thế nào khi merge hoặc chạy riêng?
Cách tính tham số của mô hình:
a/ Một số trường hợp ghi trong tên mô hình công bố của nhà sản xuất ⇒ sử dụng số này. Các trường hợp không rõ thì sử dụng các phương pháp đếm số params.
b/ MOE: vẫn tính total, các dòng model như Gemma-4-E2B vẫn tính đầy đủ 5,1B, Qwen-35B-A3B vẫn tính là mô hình 35B
c/ Các dòng mô hình có tên chính thức: Qwen3.5-4B thì tính là 4B
d/ Không tính tham số của các các vision encoder trong các LLM với bài LifeMate. 
e/ Cách tính size theo quy tắc gần đúng: 4B + 30M, ... Vẫn tính các tham số của LORA.
Với đề LifeMate, ràng buộc 6B để cho các đội chọn các solution có các dòng SLM vừa phải để xây dựng các giải phù hợp với thực tế. Các trường hợp không rõ ràng thì có thể gửi mail hỏi ban tổ chức. 
8. Ngân sách thời gian: Giới hạn thời gian mỗi mẫu có bao gồm nhận dạng audio hiện tại, audio trong history và toàn bộ vòng gọi tool không? Thời gian tải model/khởi tạo có được tách riêng không; có được warm-up và giữ model trong bộ nhớ giữa các mẫu không?
Thời gian tính bằng khi bắt đầu nhận đầu vào, đến lúc có kết quả. Không tính thời gian load model. BTC Cung cấp một sample jupyter để kiểm tra thời gian này. Load model ==> Loop qua từng mẫu ==> Dự đoán.
9. Dữ liệu huấn luyện bổ sung: Đội có được dùng model giáo viên lớn hơn giới hạn để tạo dữ liệu tổng hợp hoặc preference pairs chỉ phục vụ huấn luyện, miễn hệ thống inference cuối đáp ứng giới hạn và không sử dụng public/private test không?
Đội được phép dùng các mô hình lớn hơn, các APIs 3rd để tạo dữ liệu tổng hợp cho phương pháp distill.
10. Dạ em đang ở track Lifemate, thì em muốn hỏi là mình có được phép gắn tool nội bộ vào cho model không ạ, như sympy,…
Các thư viện cho phép sử dụng miễn phí thì được phép sử dụng, thư viện sử dụng các mô hình LLM thì vẫn tính các tham số cho giải pháp.
11. README có minh họa API env.get_state(), nhưng thể lệ yêu cầu agent chỉ truy cập app data thông qua tool calls. BTC có thể xác nhận get_state() chỉ phục vụ debugging và tuyệt đối không được sử dụng làm observation cho agent khi inference hay không?
Tất cả truy cập tới dữ liệu của các ứng dụng của một mẫu dữ liệu test chỉ được phép qua hàm call. Không được truy xuất trực tiếp "state" của DB dưới bất kỳ hình thức nào.
Câu hỏi thường gặp cho đề DataMate
1. Khi phát triển, đội có được dùng file dữ liệu shop (không có đáp án) của public test, và dùng LLM bên ngoài để sinh câu hỏi tổng hợp từ bộ train không?
Tập public test không được sử dụng để train model và không được sử dụng trong quá trình inference. Kể cả việc xử lý tập public test để tạo ra thông tin train khác.
2. Khi chạy lại Docker để so |ΔPA| ≤ 0,03, BTC chạy mấy lần và trên cấu hình nào? Suy luận theo lô (batch) có thể làm kết quả greedy khác nhau giữa các lần chạy.
BTC sẽ chạy 2 lần nếu lần thứ nhất không reproduce được. BTC sẽ chạy trên 2x5090. Việc chạy theo batch hay tuần tự do đội thi quyết định, và hiện thực trong code inference. BTC chỉ run docker và không chỉnh sửa code của đội thi.
3. Dữ liệu tổng hợp: em dùng API của một LLM thương mại để viết lại (paraphrase) câu hỏi trong tập train và sinh thêm dữ liệu giả lập để fine-tune LoRA cho model mở (Qwen3.5-9B). Lúc chạy inference thì hoàn toàn offline, không gọi API nào. Cách làm này có được phép không, và có cần khai báo gì trong tài liệu nộp không ạ?
Cách làm này hợp lệ. Tuy nhiên bạn cần kiểm tra license của bên cung cấp API để xem quyền sử dụng dữ liệu. Bạn cần nộp lại dữ liệu train để BTC có thể reproduce kết quả
4. Rulebook: private test có thể có rulebook khác public. Em có được đóng gói sẵn rulebook của train/public trong Docker để so sánh, phát hiện luật nào bị thay đổi lúc chạy không ạ?
Bạn có thể sử dụng rulebook của tập train để thực hiện việc này. Tập public test không được sử dụng để train hay inference
5. Nộp Docker tối đa 2 lần trước 16/11 và lấy lần cuối: BTC có chạy thử để báo lỗi môi trường trước hạn không, hay chỉ chạy khi có private?
Docker được nộp tối đa 2 lần kèm check sum và BTC sẽ lấy lần cuối cùng, BTC không chạy thử trước môi trường. Ban tổ chức sẽ gửi hướng dẫn Docker để các nội chuẩn bị. 
6. File log /result/logs: với câu được trả lời bằng code Python tính toán thẳng (không qua LLM), trường "code" và "output" nên ghi thế nào cho đúng yêu cầu ạ?
 Thí sinh có thể ghi code và output rỗng
7. Khi tự chạy private (20–23/11) thì em chạy đúng Docker đã nộp trên máy của mình, rồi upload submission.jsonl lên portal phải không ạ?
Bạn cần chạy inference private test bằng solution bạn đã submit với Ban Tổ chức - Docker cuối cùng.