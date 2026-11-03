# v3.1 local — kết luận ngày 03/11/2026

Đã triển khai và kiểm cấu hình chung, provenance generator, policy retry, offline replay và monthly growth opt-in. Spec/runbook trong `docs/plans/2026-11-03-{v31-local,monthly-growth}-design.md` và `docs/runbooks/v31-local-evaluation.md`.

- Retry optimization: mọi đáp án cũ giữ nguyên trên4.000 câu; calls Train43→34, Public2→1,0network.
- Monthly: Train840 đúng/6 sai, PA0.27800; thêm22 đúng/0sai, tất cả22 thuộc Train-24, nhóm6 không thêm.
- Public204 trả lời/796 abstain,0new; chỉ1monthly candidate sauFast, README role chưa verified nên abstain. SHA giốngv3.0. Không đề xuất nộp lại, chưa có scorev3.1.
- Tests201pass fullsuite, sau thêm một integration test thì27monthly tests pass. Không training/GPU trong phiên.

`inventory.json` là nhóm từ khóa chồng lấn, không phải executable eligibility. `monthly-growth-probe.json` là Train-24 exploration trước contract đầy đủ. `monthly-growth-design.json` lưu grammar/source hash chốt trước đo nhóm khác. Kết quả cuối ở `artifacts/v31-growth-{train,public}/`, cả hai có verification. Lượt interrupted không phải benchmark.

Giới hạn: metadata/schema checks và biên ngày không chứng minh dữ liệu xuất đủ bên trong; 22/22 không bảo đảm precision trên dữ liệu mới. New route là deterministic, không gán đóng góp này cho model. Model Slow cũ vẫn có giới hạn kiểm ngày/status riêng.

Handoff: chưa mởColab; thiết kế structured intent/period extraction với compiler/guard và probe Train trước khi xin endpoint. Không tiếp tục bổ sung phrase chỉ vì tăngTrain; không dùng Public targets hoặc hardcode ID/schema từ Public. Root v3.0 đã nộp vẫn0.13400.
