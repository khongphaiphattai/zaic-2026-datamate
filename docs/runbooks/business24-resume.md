# Tiếp tục business24 sau khi Colab mất runtime

Chốt phiên 02/11/2026: tạm dừng phần cần GPU theo yêu cầu người dùng; tiếp tục khi Colab cấp GPU lại. Ngân sách người dùng báo còn 120 units. Thời điểm GPU khả dụng phụ thuộc Colab, chưa xác nhận. Chưa có bằng chứng cần train thêm; chỉ khôi phục adapter để merge và inference.

## File dùng cho phiên sau

- Import [04_business24_restore_and_serve.ipynb](../../notebooks/04_business24_restore_and_serve.ipynb) lên Colab; chọn A100 40 GB / High RAM. Notebook tự chứa toàn bộ helper, không cần upload các file Python hay training bundle.
- Khi cell 3 yêu cầu, chọn `/home/pt/AI Challenge/business24-adapter-backup.zip` (~154 MB). ZIP không được lưu trong Git vì `.gitignore`; giữ bản này trên máy hoặc Drive.
- SHA256 backup: `65914f4369f8409854b75c220ef3999077d4f73b2761c4c48dc71dbde7604965`. Kiểm lại SHA256/CRC/manifest/data/split thành công. Adapter train xong 633 mẫu từ 24 business; base Qwen revision `c03e6d358207e414f1eca0bb1891e29f1db0e242`.

## Thứ tự 8 cell

1. Kiểm GPU/RAM/disk trước khi cài hoặc tải weights, chuẩn bị helper nhúng sẵn.
2. Tạo venv riêng, cài Torch CUDA/merge dependencies đã pin, kiểm import/CUDA/bfloat16; không cài vào kernel Colab.
3. Upload ZIP hoặc dùng đường dẫn Drive tùy chọn; kiểm hash/CRC/provenance, giải nén vào thư mục staging rồi chuyển vào adapter. Dùng lại adapter/merged model hợp lệ nếu còn.
4. Merge đúng base revision trên RAM hệ thống; dùng lại merged model đầy đủ nếu đã có. Refuse thư mục dở dang, không tự xóa.
5. Mở API, gửi Base URL cho agent. Chạy lại dùng cùng process; bật `REOPEN_TUNNEL` để mở riêng tunnel, giữ model trong GPU.
6. Kiểm local/public API provenance, không sinh token.
7. Xem trạng thái/log để bàn giao.
8. Chỉ bật `DO_STOP` sau khi agent đo xong. Mặc định tắt nên Run all không tự dừng API. Dừng process chưa phải xóa runtime Colab; sau khi chắc backup đã lưu local/Drive, người dùng có thể Disconnect and delete runtime để kết thúc phiên cấp GPU.

Mất runtime cần cài lại môi trường/tải base/merge lại nếu `/content` mất; không cần training. Log ở `/content/business24-recovery/{install,merge,api}.log`. Chưa thực thi GPU merge/inference của notebook mới ở local; runtime thật sẽ xác nhận bước này. Không gọi phiên bản mới là bảo đảm không bao giờ có lỗi mạng/Colab.

### Nếu bản notebook cũ báo `No module named pip` ở cell 2

Không sang cell 3. Venv có Python chưa đảm bảo đã có pip. Bản notebook đã sửa sẽ tạo venv bằng `--without-pip`, kiểm pip thực tế và dùng pip của kernel với `--python` để cài pip vào đúng venv (không cần ensurepip). Runtime đang mở có thể thêm cell tạm sau, chạy rồi chạy lại cell 2; chỉ tiếp tục khi có `IMPORT/CUDA OK`:

```python
import sys, subprocess
venv_python = '/content/business24-venv/bin/python'
subprocess.run([sys.executable, '-m', 'pip', '--python', venv_python, 'install', 'pip'], check=True)
subprocess.run([venv_python, '-m', 'pip', '--version'], check=True)
```

Cách quản lý venv thiếu pip có trong [tài liệu pip](https://pip.pypa.io/en/stable/topics/python-option/). Không restart runtime, không xóa backup/model, không training lại. Nếu cell sửa cũng lỗi, dừng và gửi output đầy đủ; chưa có xác nhận import/CUDA trên runtime thật thì chưa coi cell 2 hoàn tất.

## Trạng thái agent cần tiếp tục

Grammar đã chốt từ Train-24 ở commit `68b6cac`: thêm 19 Slow candidates trên Train (17 Train-24, 2 SFT-excluded) và 1 Public theo audit offline. Chưa có chất lượng live cho grammar mới. Endpoint cũ NXDOMAIN; không tái dùng URL đó.

Sau khi nhận URL mới, xác minh model `datamate-business24` và data/split/base revision rồi chạy nối tiếp từ root repo:

```bash
.venv/bin/python -u scripts/diagnose_hybrid.py --dataset train --base-url "$BUSINESS24_BASE_URL" --output-dir artifacts/broad-train-paraphrases --reuse-run artifacts/broad-train-business24
.venv/bin/python -u scripts/diagnose_hybrid.py --dataset public_test --base-url "$BUSINESS24_BASE_URL" --output-dir artifacts/public-shadow-paraphrases
```

Đặt `BUSINESS24_BASE_URL` bằng URL mới trước khi chạy. Dùng output directory mới nếu đã có run dở. Cache chỉ dùng raw model responses có provenance/config/source/prompt hash khớp; kiểm guard và thực thi SQL lại.

Kiểm đủ 3.000 Train/1.000 Public IDs, score từng nhóm 24/6, Public không có nhãn, Fast giữ baseline, calls cache/network tách riêng. Không tune grammar từ Public/nhóm 6 hoặc gọi development evidence là untouched validation. Cập nhật ROADMAP sau từng việc.

Snapshot v2.8 đủ 1.000 dòng ở `artifacts/baseline-v2.8/submission.jsonl`, score Public người dùng báo 0.13400, SHA256 `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`. Root `submission.jsonl` là bản 5 dòng có sẵn, được giữ nguyên; không nộp nhầm bản này. Chưa upload portal; Slow Path vẫn mặc định tắt.
