# flycampro → WooCommerce Product Importer

Tự động lấy sản phẩm từ **flycampro.vn** (nhà phân phối DJI), viết lại nội dung
bằng Claude, và tạo sản phẩm mới trên WooCommerce của **vchannelstore.com**.

## 1. Cài đặt

```bash
pip install -r requirements.txt
```

## 2. Cấu hình `.env`

Copy file mẫu rồi điền thông tin:

```bash
cp .env.example .env
```

Cần **4 nhóm thông tin**, tất cả đều bắt buộc:

### a) WooCommerce REST API (để tạo sản phẩm)
Vào **WooCommerce > Settings > Advanced > REST API > Add key**, chọn quyền
**Read/Write**, lấy `Consumer Key` / `Consumer Secret` điền vào
`WC_CONSUMER_KEY` / `WC_CONSUMER_SECRET`.

### b) WordPress Application Password (để upload ảnh)
Đây là thông tin **khác** với key ở trên — WooCommerce key chỉ gọi được API
`wc/v3/*`, còn upload ảnh cần API lõi WordPress `wp/v2/media` nên phải xác
thực bằng một user WordPress thật:

1. Vào **wp-admin > Users > hồ sơ của bạn (Profile)**
2. Kéo xuống mục **Application Passwords**, đặt tên (vd: `flycampro-importer`), bấm **Add New**
3. Copy chuỗi mật khẩu được sinh ra (dạng `xxxx xxxx xxxx xxxx xxxx xxxx`) vào
   `WP_APP_PASSWORD`, và username tương ứng vào `WP_USERNAME`

> Site phải chạy HTTPS để Application Passwords hoạt động (mặc định WordPress
> chặn tính năng này trên HTTP).

### c) Anthropic API key
Điền `ANTHROPIC_API_KEY` (lấy tại console.anthropic.com). Model dùng để
rewrite nội dung đọc từ `ANTHROPIC_MODEL` (mặc định `claude-sonnet-4-6`).

### d) Giá bán (tuỳ chọn)
`PRICE_DISCOUNT_VND` (mặc định `45000`): script set
`regular_price` = giá gốc lấy từ flycampro.vn, và
`sale_price` = giá gốc − số tiền này, để hiển thị như đang có khuyến mãi.
Có thể override mỗi lần chạy bằng `--discount`.

## 3. Chạy

### Cách 1 - Web app (khuyến nghị, không cần biết dòng lệnh)

```bash
python webapp.py
```

hoặc double-click `start_webapp.bat`, rồi mở **http://127.0.0.1:5000** trên
browser. Có form nhập URL, chọn giảm giá/trạng thái/category, bấm "Bắt đầu
import" và xem log chạy trực tiếp ngay trên trang - không cần chạy gì qua
dòng lệnh hay nhờ AI mỗi lần import nữa. Web app này chạy **local trên máy
bạn** (không public ra internet) vì nó cầm API key/App Password của bạn.

### Cách 2 - Dòng lệnh

**Một sản phẩm** (tạo `simple product`):

```bash
python import_product.py https://flycampro.vn/products/dji-pocket-4-creator-combo
```

**Nhiều URL cùng một dòng sản phẩm, khác combo/phiên bản** (flycampro hay tách
mỗi combo thành 1 trang riêng) → gộp thành **1 `variable product`** với option
để khách chọn, mỗi option tự đổi giá, ảnh đại diện, và mô tả riêng ("trong hộp
có gì"):

```bash
python import_product.py https://flycampro.vn/products/dji-pocket-4-creator-combo https://flycampro.vn/products/dji-pocket-4-standard-combo
```

Tên option lấy từ phần khác nhau giữa các tiêu đề gốc (vd: "Creator Combo" /
"Standard Combo"); tên sản phẩm cha là phần chung ("DJI Pocket 4"). Ảnh
"trong hộp có gì" của mỗi option được Claude (vision) tự đọc từ ảnh flat-lay
phụ kiện trên trang gốc.

Các cờ tuỳ chọn:

| Cờ | Ý nghĩa |
|---|---|
| `--discount 100000` | Ghi đè số tiền giảm giá cho lần chạy này |
| `--status publish` | Đăng công khai ngay thay vì tạo nháp (`draft`) |
| `--category "Gimbal camera"` | Gán category có sẵn trên site (cách nhau bởi dấu phẩy nếu nhiều) — category phải tồn tại sẵn, script không tự tạo mới; category cha cũng tự được gán kèm |
| `--force-scrape` | Bỏ qua cache, scrape lại trang gốc |
| `--force-rewrite` | Bỏ qua cache, gọi lại Claude để viết lại nội dung |

Script in log rõ từng bước (`SCRAPE` → `IMAGES` → `REWRITE` → `WOOCOMMERCE`) và
kết thúc bằng link sản phẩm vừa tạo trên trang quản trị WordPress.

Nếu site có taxonomy **Brands** riêng (native WooCommerce Brands, endpoint
`wc/v3/products/brands`), script tự tìm term khớp với brand đã scrape (mặc
định `DJI`) và gán vào - không cần cờ gì thêm. Nếu site không có taxonomy này
thì bỏ qua, không lỗi.

## 4. Cơ chế hoạt động & an toàn khi chạy lại

Mỗi sản phẩm có một thư mục cache riêng tại `cache/<slug-san-pham>/`:

- `raw.json` — dữ liệu đã scrape (tên, giá, mô tả, thông số, danh sách ảnh)
- `images/` — ảnh gốc đã tải về, đặt tên `<slug>-01.jpg`, `<slug>-02.jpg`, ...
- `rewritten.json` — nội dung Claude đã viết lại
- `state.json` — media ID đã upload lên WordPress + ID/link sản phẩm đã tạo

Nếu script lỗi giữa chừng (mạng đứt, API rate-limit, v.v.), **chạy lại đúng
lệnh cũ** — các bước đã hoàn thành sẽ được đọc từ cache thay vì làm lại
(kể cả từng ảnh đã upload lên WordPress). Nếu sản phẩm đã được tạo thành
công trên WooCommerce trước đó (theo SKU `fcp-<slug>`), script sẽ báo đã tồn
tại và **không tạo trùng**.

## 5. Xóa logo flycampro trên ảnh

Ảnh sản phẩm trên flycampro.vn có logo "FLYCAM PRO.VN" ở góc trên-trái, nền
trắng đồng nhất. Sau khi tải ảnh về, script tự động dò vùng logo (so màu với
nền trắng lấy mẫu từ góc trên-phải) rồi phủ đè bằng đúng màu nền — giữ nguyên
kích thước/bố cục ảnh, không cắt xén nội dung sản phẩm. Xử lý ở
[lib/watermark.py](lib/watermark.py), chạy tự động cho mọi ảnh trong bước
`IMAGES`, không cần cấu hình gì thêm.

## 6. Giới hạn hiện tại

- Chỉ lấy dữ liệu của **biến thể mặc định** trên chính trang flycampro.vn (nếu
  1 trang có nhiều option riêng của flycampro, chỉ lấy option đầu) — việc gộp
  nhiều *trang* thành 1 variable product (mục 3 ở trên) là chuyện khác, đã hỗ trợ.
- Category phải gán thủ công bằng `--category` mỗi lần chạy (không tự suy luận
  loại sản phẩm) — brand thì tự động nếu site có taxonomy Brands.
- Tag tự sinh chỉ gồm brand + tên dòng sản phẩm, khá tối giản.
- Bảng thông số kỹ thuật giữ nguyên số liệu gốc, được chèn vào cuối phần mô
  tả sản phẩm dưới dạng bảng HTML (Claude không chạm vào phần này).
