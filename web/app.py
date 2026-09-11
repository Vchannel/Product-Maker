"""Flask application: pages + JSON API for the importer."""
from __future__ import annotations

import mimetypes
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, url_for
from markupsafe import Markup, escape

from lib import pipeline, settings
from lib.rewriter import RewriteError, check_api
from lib.scraper import ScrapeError, validate_url
from lib.wc_client import WooCommerceAPIError

from .db import Database
from .jobs import JobManager

VERSION = "2.0.0"
MAX_URLS = 12

# Windows can map .js to text/plain in the registry, which breaks <script type="module">.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Dữ liệu gửi lên không hợp lệ.")
    return data


def _clean_urls(raw) -> list:
    if isinstance(raw, str):
        raw = raw.splitlines()
    urls = []
    for u in raw or []:
        u = str(u).strip()
        if u and u not in urls:
            urls.append(u)
    if not urls:
        raise ApiError("Cần ít nhất 1 link sản phẩm.")
    if len(urls) > MAX_URLS:
        raise ApiError(f"Tối đa {MAX_URLS} link cho một lần nhập.")
    for u in urls:
        try:
            validate_url(u)
        except ScrapeError as e:
            raise ApiError(str(e)) from e
    return urls


def _backfill_products(db: Database) -> None:
    """Products published by the old version only exist as cache/*/state.json -
    list them once so they show up in the Products page."""
    site = pipeline.site_url()
    if not site or not settings.CACHE_DIR.exists():
        return
    known = {p["product_id"] for p in db.list_products(site)}
    for state_path in settings.CACHE_DIR.glob("*/state.json"):
        state = pipeline.load_json(state_path) or {}
        pid = state.get("product_id") or state.get("wc_product_id") or state.get("wc_parent_id")
        if not pid or pid in known:
            continue
        key_dir = state_path.parent
        rewritten = pipeline.load_json(key_dir / "rewritten.json") or {}
        variable = key_dir.name.startswith("_variable-")
        media = state.get("parent_media_ids") or state.get("media_ids") or {}
        thumb = next(iter(media.values()), {}).get("url", "")
        raw = pipeline.load_json(key_dir / "raw.json") or {}
        db.upsert_product(
            site,
            {
                "product_id": pid,
                "sku": pipeline.make_sku(key_dir.name[len("_variable-"):] if variable else key_dir.name),
                "kind": "variable" if variable else "simple",
                "name": rewritten.get("title") or raw.get("title") or key_dir.name,
                "status": "",
                "permalink": state.get("wc_parent_link") or state.get("wc_product_link") or "",
                "edit_link": pipeline.edit_link(pid),
                "thumb_url": thumb,
                "price_min": raw.get("price_vnd"),
                "price_max": raw.get("price_vnd"),
                "variations": [{"sku": sku, "id": vid, "label": sku} for sku, vid in (state.get("variations") or {}).items()],
                "source_urls": [raw["source_url"]] if raw.get("source_url") else [],
                "created_at": state_path.stat().st_mtime,
            },
        )


def create_app(start_worker: bool = True) -> Flask:
    settings.load_env()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    db = Database(settings.DATA_DIR / "app.db")
    jobs = JobManager(db)
    app.extensions["pm_db"] = db
    app.extensions["pm_jobs"] = jobs
    try:
        _backfill_products(db)
    except Exception as e:  # noqa: BLE001 - never block startup on the legacy import
        print(f"Không đọc được sản phẩm cũ từ cache: {e}")
    if start_worker:
        jobs.start()

    categories_cache = {"at": 0.0, "items": None, "site": ""}
    categories_lock = threading.Lock()

    # --- guards ---------------------------------------------------------------
    @app.before_request
    def local_only():
        # The app holds store credentials: refuse requests whose Host isn't
        # this machine (DNS rebinding) and cross-site form posts (CSRF - the
        # API only accepts JSON, which browsers can't send cross-origin
        # without a preflight this server never approves).
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "::1", "[::1]") and not app.config.get("TESTING"):
            abort(403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
            if not request.is_json:
                abort(415)
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1].split(":")[0] not in ("127.0.0.1", "localhost"):
                abort(403)

    @app.errorhandler(ApiError)
    def api_error(e: ApiError):
        return jsonify({"error": str(e), **e.extra}), e.status

    @app.errorhandler(pipeline.DraftError)
    def draft_error(e):
        return jsonify({"error": str(e)}), 422

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(415)
    def http_error(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return render_template("error.html", code=e.code, message=e.description), e.code

    def icon(name: str, cls: str = "") -> Markup:
        href = url_for("static", filename="icons.svg")
        return Markup(f'<svg class="i {escape(cls)}" aria-hidden="true"><use href="{href}#{escape(name)}"></use></svg>')

    app.jinja_env.globals["icon"] = icon

    @app.context_processor
    def inject_globals():
        counts = db.job_counts()
        return {
            "version": VERSION,
            "site_url": settings.get("WP_SITE_URL"),
            "missing_keys": settings.missing_keys(),
            "nav_counts": {"active": counts.get("queued", 0) + counts.get("running", 0), "review": counts.get("review", 0)},
        }

    # --- pages ------------------------------------------------------------------
    @app.get("/")
    def page_dashboard():
        return render_template("dashboard.html", page="dashboard")

    @app.get("/import")
    def page_import():
        return render_template(
            "import.html",
            page="import",
            defaults={
                "discount_vnd": settings.default_discount(),
                "status": settings.default_status(),
                "model": settings.model(),
            },
            statuses=settings.STATUS_CHOICES,
        )

    @app.get("/jobs")
    def page_jobs():
        return render_template("jobs.html", page="jobs")

    @app.get("/jobs/<job_id>")
    def page_job(job_id):
        job = db.get_job(job_id)
        if not job:
            abort(404, "Không tìm thấy phiên nhập này.")
        return render_template("job.html", page="jobs", job_id=job_id, stages=pipeline.STAGES, statuses=settings.STATUS_CHOICES)

    @app.get("/products")
    def page_products():
        return render_template("products.html", page="products")

    @app.get("/settings")
    def page_settings():
        return render_template(
            "settings.html", page="settings", fields=settings.FIELDS, models=settings.MODEL_CHOICES, statuses=settings.STATUS_CHOICES
        )

    @app.get("/media/<slug>/<filename>")
    def media(slug, filename):
        try:
            path = pipeline.resolve_image(f"{slug}/{filename}", original=request.args.get("original") == "1")
        except pipeline.DraftError:
            abort(404)
        return send_file(Path(path).resolve(), max_age=3600)

    # --- API: overview ------------------------------------------------------------
    @app.get("/api/overview")
    def api_overview():
        site = pipeline.site_url()
        products = db.list_products(site) if site else []
        counts = db.job_counts()
        week_ago = time.time() - 7 * 86400
        recent = db.list_jobs(limit=8)
        attention = db.list_jobs("review,failed", limit=6)
        return jsonify(
            {
                "missing_keys": settings.missing_keys(),
                "site": site,
                "model": settings.model(),
                "counts": counts,
                "stats": {
                    "products": len([p for p in products if p.get("remote_state") != "missing"]),
                    "products_week": len([p for p in products if (p.get("created_at") or 0) >= week_ago]),
                    "active": counts.get("queued", 0) + counts.get("running", 0),
                    "review": counts.get("review", 0),
                    "failed": counts.get("failed", 0),
                },
                "recent_jobs": [_job_summary(j) for j in recent],
                "attention": [_job_summary(j) for j in attention],
                "recent_products": products[:6],
                "worker": jobs.snapshot(),
            }
        )

    def _job_summary(job: dict) -> dict:
        result = job.get("result") or {}
        return {
            "id": job["id"],
            "status": job["status"],
            "phase": job["phase"],
            "kind": job.get("kind"),
            "title": job.get("title") or "",
            "thumb": f"/media/{job['thumb']}" if job.get("thumb") else "",
            "urls": job["urls"],
            "stages": job.get("stages") or {},
            "error": job.get("error"),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "result": {k: result.get(k) for k in ("action", "product_id", "edit_link", "permalink")} if result else None,
        }

    # --- API: import ---------------------------------------------------------------
    @app.post("/api/preview")
    def api_preview():
        urls = _clean_urls(_json_body().get("urls"))
        return jsonify(pipeline.preview(urls, pipeline.Reporter()))

    @app.get("/api/categories")
    def api_categories():
        if settings.missing_keys():
            raise ApiError("Chưa cấu hình kết nối WooCommerce.", 409)
        site = pipeline.site_url()
        with categories_lock:
            fresh = time.time() - categories_cache["at"] < 600 and categories_cache["site"] == site
            if request.args.get("refresh") or not fresh or categories_cache["items"] is None:
                try:
                    items = pipeline._wc().list_categories()
                except WooCommerceAPIError as e:
                    raise ApiError(str(e), 502) from e
                by_id = {c["id"]: c for c in items}

                def path_of(c):
                    names, seen = [c["name"]], set()
                    parent = c.get("parent")
                    while parent and parent in by_id and parent not in seen:
                        seen.add(parent)
                        names.insert(0, by_id[parent]["name"])
                        parent = by_id[parent].get("parent")
                    return " › ".join(names)

                categories_cache.update(
                    at=time.time(),
                    site=site,
                    items=sorted(
                        (
                            {"id": c["id"], "name": c["name"], "parent": c.get("parent", 0), "path": path_of(c), "count": c.get("count", 0)}
                            for c in items
                        ),
                        key=lambda c: c["path"].lower(),
                    ),
                )
            return jsonify({"items": categories_cache["items"]})

    @app.get("/api/jobs")
    def api_jobs():
        status = request.args.get("status", "")
        limit = min(int(request.args.get("limit", 100)), 500)
        return jsonify({"items": [_job_summary(j) for j in db.list_jobs(status, limit)]})

    @app.post("/api/jobs")
    def api_create_job():
        body = _json_body()
        urls = _clean_urls(body.get("urls"))
        other = jobs.find_active_overlap(urls)
        if other and not body.get("allow_duplicate"):
            raise ApiError("Đã có một phiên đang xử lý link này.", 409, job_id=other["id"])
        raw_opts = body.get("options") or {}
        options = pipeline.PrepareOptions.from_dict(raw_opts).to_dict()
        options["review"] = raw_opts.get("review", True) is not False
        job_id = jobs.submit(urls, options)
        return jsonify({"id": job_id}), 201

    def _get_job_or_404(job_id: str) -> dict:
        job = db.get_job(job_id)
        if not job:
            raise ApiError("Không tìm thấy phiên nhập.", 404)
        return job

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id):
        job = _get_job_or_404(job_id)
        after = int(request.args.get("after", 0) or 0)
        data = _job_summary(job)
        data.update(
            {
                "options": job["options"],
                "result": job.get("result"),
                "logs": db.logs_after(job_id, after),
                "queue_position": _queue_position(job_id),
            }
        )
        data["has_draft"] = bool(job.get("draft"))
        if request.args.get("draft") == "1":
            data["draft"] = job.get("draft")
        return jsonify(data)

    def _queue_position(job_id: str) -> int:
        waiting = [j["id"] for j in reversed(db.list_jobs("queued", limit=200))]
        return waiting.index(job_id) + 1 if job_id in waiting else 0

    @app.put("/api/jobs/<job_id>/draft")
    def api_save_draft(job_id):
        job = _get_job_or_404(job_id)
        if job["status"] not in ("review", "failed", "cancelled"):
            raise ApiError("Phiên này không còn ở bước chỉnh sửa.", 409)
        draft = pipeline.normalize_draft(_json_body().get("draft"))
        # Conditional on status so a late autosave can't change a draft that is
        # already queued for publishing.
        if not db.transition(job_id, ("review", "failed", "cancelled"), draft=draft, title=draft["title"], thumb=draft["images"][0]):
            raise ApiError("Phiên này không còn ở bước chỉnh sửa.", 409)
        return jsonify({"ok": True, "draft": draft, "saved_at": time.time()})

    @app.post("/api/jobs/<job_id>/publish")
    def api_publish(job_id):
        job = _get_job_or_404(job_id)
        if job["status"] != "review":
            raise ApiError("Chỉ đăng được khi phiên đang ở bước duyệt.", 409)
        if settings.missing_keys():
            raise ApiError("Chưa cấu hình đủ kết nối tới website - vào Cài đặt.", 409)
        draft = pipeline.normalize_draft(_json_body().get("draft") or job.get("draft"))
        if not jobs.approve(job_id, draft):
            raise ApiError("Phiên này vừa được thay đổi ở nơi khác - tải lại trang.", 409)
        return jsonify({"ok": True})

    @app.post("/api/jobs/<job_id>/retry")
    def api_retry(job_id):
        job = _get_job_or_404(job_id)
        if job["status"] not in ("failed", "cancelled"):
            raise ApiError("Chỉ thử lại được phiên bị lỗi hoặc đã huỷ.", 409)
        if not jobs.retry(job_id):
            raise ApiError("Phiên này vừa được thay đổi ở nơi khác - tải lại trang.", 409)
        return jsonify({"ok": True})

    @app.post("/api/jobs/<job_id>/reopen")
    def api_reopen(job_id):
        job = _get_job_or_404(job_id)
        if job["status"] not in ("failed", "cancelled") or not job.get("draft"):
            raise ApiError("Phiên này chưa có bản nháp để mở lại.", 409)
        if not jobs.reopen(job_id):
            raise ApiError("Phiên này vừa được thay đổi ở nơi khác - tải lại trang.", 409)
        return jsonify({"ok": True})

    @app.post("/api/jobs/<job_id>/regenerate")
    def api_regenerate(job_id):
        job = _get_job_or_404(job_id)
        if job["status"] not in ("review", "failed", "cancelled"):
            raise ApiError("Không thể viết lại khi phiên đang chạy.", 409)
        if not jobs.regenerate(job_id):
            raise ApiError("Phiên này vừa được thay đổi ở nơi khác - tải lại trang.", 409)
        return jsonify({"ok": True})

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel(job_id):
        _get_job_or_404(job_id)
        outcome = jobs.cancel(job_id)
        if not outcome:
            raise ApiError("Phiên này đã kết thúc, không thể huỷ.", 409)
        return jsonify({"ok": True, "outcome": outcome})

    @app.delete("/api/jobs/<job_id>")
    def api_delete_job(job_id):
        job = _get_job_or_404(job_id)
        if job["status"] in ("queued", "running"):
            raise ApiError("Huỷ phiên trước khi xoá.", 409)
        db.delete_job(job_id)
        return jsonify({"ok": True})

    # --- API: products --------------------------------------------------------------
    @app.get("/api/products")
    def api_products():
        site = pipeline.site_url()
        return jsonify({"items": db.list_products(site) if site else [], "site": site})

    @app.post("/api/products/sync")
    def api_products_sync():
        if settings.missing_keys():
            raise ApiError("Chưa cấu hình kết nối WooCommerce.", 409)
        site = pipeline.site_url()
        local = db.list_products(site)
        ids = [p["product_id"] for p in local]
        try:
            remote = {p["id"]: p for p in pipeline._wc().list_products(ids)} if ids else {}
        except WooCommerceAPIError as e:
            raise ApiError(str(e), 502) from e
        now = time.time()
        for p in local:
            r = remote.get(p["product_id"])
            if r is None:
                db.update_product_remote(site, p["product_id"], remote_state="missing", synced_at=now)
                continue
            def as_int(value):
                try:
                    return int(float(value)) if value not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            fields = dict(
                name=r.get("name"),
                status=r.get("status"),
                permalink=r.get("permalink"),
                remote_state="trash" if r.get("status") == "trash" else "ok",
                synced_at=now,
            )
            if r.get("images"):
                fields["thumb_url"] = r["images"][0].get("src")
            if r.get("type") == "simple" and as_int(r.get("regular_price")):
                fields["price_min"] = fields["price_max"] = as_int(r.get("regular_price"))
                fields["sale_price"] = as_int(r.get("sale_price"))
            elif r.get("type") == "variable" and not p.get("price_min") and as_int(r.get("price")):
                # WooCommerce reports the cheapest active variation price for variable products.
                fields["price_min"] = fields["price_max"] = as_int(r.get("price"))
            db.update_product_remote(site, p["product_id"], **fields)
        return jsonify({"items": db.list_products(site), "synced_at": now})

    # --- API: settings ------------------------------------------------------------------
    @app.get("/api/settings")
    def api_settings():
        return jsonify({"values": settings.public_view(), "missing": settings.missing_keys(), "env_path": str(settings.ENV_PATH)})

    @app.put("/api/settings")
    def api_save_settings():
        values = _json_body().get("values") or {}
        if "PRICE_DISCOUNT_VND" in values and str(values["PRICE_DISCOUNT_VND"]).strip():
            try:
                if int(str(values["PRICE_DISCOUNT_VND"]).strip()) < 0:
                    raise ValueError
            except ValueError as e:
                raise ApiError("Giảm giá mặc định phải là số không âm.") from e
        if values.get("WP_SITE_URL") and not str(values["WP_SITE_URL"]).startswith("https://"):
            raise ApiError("Địa chỉ website phải bắt đầu bằng https://")
        if values.get("WC_PRODUCT_STATUS") and values["WC_PRODUCT_STATUS"] not in {s for s, _ in settings.STATUS_CHOICES}:
            raise ApiError("Trạng thái mặc định không hợp lệ.")
        new_site = str(values.get("WP_SITE_URL") or "").strip().rstrip("/")
        if new_site and new_site != pipeline.site_url() and pipeline.site_url():
            # Never send saved store credentials to a different host without the
            # user typing them again.
            needed = ["WC_CONSUMER_KEY", "WC_CONSUMER_SECRET", "WP_APP_PASSWORD"]
            if not all(str(values.get(k) or "").strip() for k in needed):
                raise ApiError("Khi đổi địa chỉ website, hãy nhập lại Consumer Key, Consumer Secret và Application Password.")
        settings.save(values)
        categories_cache.update(at=0.0, items=None)
        return jsonify({"values": settings.public_view(), "missing": settings.missing_keys()})

    @app.post("/api/settings/test")
    def api_test_settings():
        service = _json_body().get("service")
        site = pipeline.site_url()
        try:
            if service == "woocommerce":
                if not (site and settings.get("WC_CONSUMER_KEY") and settings.get("WC_CONSUMER_SECRET")):
                    raise ApiError("Điền địa chỉ website, Consumer Key và Secret trước.")
                total = pipeline._wc().check()
                return jsonify({"ok": True, "message": f"Kết nối thành công · cửa hàng có {pipeline.vnd(total)[:-1]} sản phẩm"})
            if service == "wordpress":
                if not (site and settings.get("WP_USERNAME") and settings.get("WP_APP_PASSWORD")):
                    raise ApiError("Điền địa chỉ website, tên đăng nhập và Application Password trước.")
                me = pipeline._wp().whoami()
                caps = me.get("capabilities") or {}
                if caps and not caps.get("upload_files"):
                    return jsonify({"ok": False, "message": f"Đăng nhập được với '{me.get('name')}' nhưng tài khoản không có quyền upload ảnh."})
                return jsonify({"ok": True, "message": f"Đăng nhập thành công với tài khoản '{me.get('name', '')}'"})
            if service == "anthropic":
                if not settings.get("ANTHROPIC_API_KEY"):
                    raise ApiError("Điền Anthropic API key trước.")
                name = check_api(settings.get("ANTHROPIC_API_KEY"), settings.model())
                return jsonify({"ok": True, "message": f"API key hợp lệ · model {name}"})
        except (WooCommerceAPIError, RewriteError) as e:
            return jsonify({"ok": False, "message": str(e)})
        raise ApiError("Dịch vụ không hợp lệ.")

    return app
