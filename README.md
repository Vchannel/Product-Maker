# Product Maker · VCHANNEL

Ứng dụng web chạy trên máy tính để **nhập sản phẩm DJI từ flycampro.vn lên WooCommerce của vchannelstore.com**:
tải dữ liệu → xoá logo trên ảnh → AI (Claude) viết lại nội dung → **bạn duyệt & chỉnh sửa** → đăng sản phẩm.

## Tính năng chính

- **Xem trước ngay khi dán link**: tên, giá sau giảm, số ảnh, số dòng thông số, cách gộp phiên bản.
- **Gộp nhiều combo thành 1 sản phẩm nhiều phiên bản**: mỗi phiên bản có giá, ảnh, SKU và danh sách "Trong hộp có gì" riêng.
- **Bước duyệt nội dung**, tự động lưu bản nháp:
  - Sửa tên, mô tả ngắn và mô tả chi tiết (trình soạn thảo có chế độ HTML).
  - Kéo-thả sắp xếp ảnh, chọn ảnh đại diện, so sánh ảnh gốc với ảnh đã xoá logo.
  - Sửa giá, phiên bản, phụ kiện, danh mục, tag, trạng thái.
  - Xem trước giao diện như trên cửa hàng.
- **Theo dõi tiến trình trực tiếp**: 6 bước kèm nhật ký chi tiết. Có thể huỷ, thử lại từ bước bị lỗi, hoặc mở lại bản nháp.
- **Không tạo trùng, không upload trùng**:
  - Nhận biết sản phẩm đã có theo SKU. Tuỳ chọn *giữ nguyên, chỉ thêm phiên bản còn thiếu* hoặc *cập nhật toàn bộ*.
  - Ảnh đã upload lên WordPress được dùng lại.
- **Trang Sản phẩm**: danh sách sản phẩm đã nhập, đồng bộ trạng thái mới nhất từ website.
- **Trang Cài đặt**: điền key ngay trên giao diện, có nút *Kiểm tra kết nối* cho từng dịch vụ. Key được che, không hiện đầy đủ.
- Giao diện sáng/tối, dùng được trên điện thoại/tablet trong cùng máy.

## 1. Chạy ứng dụng

**macOS**: double-click `start_webapp.command`.
**Windows**: double-click `start_webapp.bat`.

Lần đầu, script tự tạo môi trường Python (`.venv`) và cài thư viện (mất 1–2 phút). Sau đó trình duyệt tự mở
**http://127.0.0.1:8686**. Giữ cửa sổ dòng lệnh mở trong lúc dùng; đóng cửa sổ (hoặc Ctrl+C) để tắt.

Chạy thủ công:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python webapp.py                     # thêm --port 9000 / --no-browser nếu cần
```

> Cổng mặc định là 8686, không dùng 5000 vì macOS dành cổng 5000 cho AirPlay. Nếu cổng bận, app tự chọn cổng kế tiếp.
> Ứng dụng chỉ nhận kết nối từ chính máy này vì nó giữ key của cửa hàng.

## 2. Cấu hình

Vào trang **Cài đặt** trong app, điền đủ 4 nhóm rồi bấm *Kiểm tra kết nối*. Thông tin được lưu vào file `.env`
cạnh mã nguồn (xem mẫu `.env.example`).

| Nhóm | Cần gì | Lấy ở đâu |
|---|---|---|
| Website WooCommerce | Địa chỉ website, Consumer Key, Consumer Secret | WooCommerce › Settings › Advanced › REST API › Add key (quyền **Read/Write**) |
| Upload ảnh | Tên đăng nhập WordPress + **Application Password** | wp-admin › Users › Profile › Application Passwords. WooCommerce key không upload ảnh được. |
| AI | Anthropic API key, chọn model | console.anthropic.com |
| Mặc định | Số tiền giảm giá, trạng thái khi đăng | Đổi được cho từng lần nhập |

## 3. Cách dùng

1. **Nhập sản phẩm** → dán link flycampro.vn:
   - 1 link → sản phẩm đơn.
   - Nhiều link của cùng một máy (Creator Combo, Standard Combo…) → 1 sản phẩm có thuộc tính *Phiên bản*.
2. Chỉnh tuỳ chọn (giảm giá, trạng thái, danh mục…) → **Bắt đầu nhập**.
3. Ứng dụng tải trang, xử lý ảnh, AI viết nội dung (thường 30–90 giây). Bạn có thể rời trang, quá trình vẫn chạy nền.
4. **Duyệt**: kiểm tra số liệu, sửa nội dung, sắp xếp ảnh, chỉnh giá → **Đăng lên WooCommerce**.
5. Xong: mở sản phẩm trong WordPress hoặc xem trên cửa hàng.

Tắt *Duyệt trước khi đăng* nếu muốn chạy thẳng từ đầu đến cuối.

### Khi sản phẩm đã có trên website

Ứng dụng nhận biết sản phẩm theo SKU (`fcp-<slug>`) và cho bạn chọn cách xử lý:

- **Giữ nguyên nội dung** (mặc định):
  - Với sản phẩm đơn, không thay đổi gì.
  - Với sản phẩm nhiều phiên bản, chỉ **thêm các phiên bản còn thiếu**, ví dụ khi flycampro ra combo mới. Phiên bản mới được thêm vào thuộc tính *Phiên bản* của sản phẩm cha.
- **Cập nhật toàn bộ**: ghi đè tên, mô tả, ảnh, giá của sản phẩm và các phiên bản.

## 4. Dòng lệnh (không có bước duyệt)

```bash
.venv/bin/python import_product.py https://flycampro.vn/products/dji-pocket-4-creator-combo
.venv/bin/python import_product.py <link-combo-1> <link-combo-2> --discount 100000 --status publish --category "Gimbal camera"
```

| Cờ | Ý nghĩa |
|---|---|
| `--discount 100000` | Số tiền trừ vào giá gốc để ra giá khuyến mãi |
| `--status draft\|pending\|publish` | Trạng thái khi tạo |
| `--category "A, B"` | Tên danh mục có sẵn trên site (khớp chính xác; danh mục cha tự gán kèm) |
| `--update` | Sản phẩm đã có thì ghi đè nội dung/giá |
| `--no-box` | Bỏ qua bước AI đọc phụ kiện trong hộp |
| `--force-scrape` / `--force-rewrite` | Bỏ qua cache, tải lại trang gốc / gọi lại AI |

## 5. Cơ chế hoạt động

```
Link ─► Lấy dữ liệu ─► Xử lý ảnh ─► AI viết nội dung ─► Duyệt ─► Upload ảnh ─► Đăng sản phẩm
        raw.json       _original/     rewritten.json     draft     media_index   state.json
                       ảnh sạch       box_description
```

- **Cache** ở `cache/<slug>/`. Lỗi giữa chừng thì bấm *Thử lại*: các bước đã xong được dùng lại, không tốn thêm tiền AI.
- **Xoá logo**:
  - Ảnh gốc giữ trong `images/_original/`, ảnh đã xử lý ở `images/`.
  - Thuật toán chỉ tô vùng logo khi nền đồng nhất và cụm điểm ảnh có kích thước giống logo. Phần sản phẩm lấn vào góc ảnh được giữ nguyên.
- **AI**:
  - Claude trả về JSON có cấu trúc, còn HTML do ứng dụng dựng và escape.
  - Bảng thông số không qua AI nên giữ nguyên số liệu gốc.
  - Danh sách phụ kiện ưu tiên đọc ảnh "Trong hộp có gì" chính thức trên trang, rồi mới tới ảnh flat-lay.
- **Chống upload trùng**: mỗi ảnh được nhận diện bằng SHA-1 (`data/media_index.json`). Ảnh đã có trên site thì dùng lại media cũ.
- **Lịch sử** phiên nhập, nhật ký, danh sách sản phẩm lưu trong `data/app.db` (SQLite).

## 6. Cấu trúc thư mục

```
webapp.py              Khởi động web app
import_product.py      Bản dòng lệnh
lib/
  pipeline.py          Luồng chuẩn bị bản nháp → đăng sản phẩm
  scraper.py           Đọc trang flycampro.vn (2 kiểu bảng thông số)
  images.py            Tải ảnh, giữ bản gốc, gọi xoá logo
  watermark.py         Phát hiện và xoá logo
  rewriter.py          Gọi Claude (viết nội dung, đọc phụ kiện)
  wc_client.py         WooCommerce REST + WordPress media
  html_utils.py        Làm sạch HTML, dựng bảng thông số
  settings.py          Đọc/ghi .env, đường dẫn
web/
  app.py, jobs.py, db.py   Flask, hàng đợi xử lý nền, SQLite
  templates/, static/      Giao diện (CSS + JavaScript, không cần build)
tests/                 Test tự động (không gọi mạng, không đụng cửa hàng thật)
```

## 7. Phát triển

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests
.venv/bin/python webapp.py --debug --no-browser
```

## 8. Lưu ý

- Chỉ hỗ trợ link dạng `flycampro.vn/products/...`. Nếu trang có nhiều lựa chọn riêng thì chỉ lấy lựa chọn mặc định.
- AI có thể viết sai. Luôn đọc lại con số trước khi đăng; bảng thông số thì giữ nguyên bản gốc.
- Một số trang flycampro để bảng thông số bằng tiếng Anh. Ứng dụng giữ nguyên, không tự dịch.
