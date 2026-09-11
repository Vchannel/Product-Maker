#!/usr/bin/env python3
"""Web UI cho import_product.py - chạy local, tự phục vụ import không cần dòng lệnh.

Cách dùng:
    python webapp.py
Rồi mở http://127.0.0.1:5000 trên browser.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

import import_product as core

load_dotenv()

app = Flask(__name__)

JOBS: dict = {}
JOBS_LOCK = threading.Lock()


def _run_job(job_id: str, urls: list, discount: int, status: str, category_names: list, model: str) -> None:
    job = JOBS[job_id]
    core.set_log_sink(job["logs"])
    try:
        if len(urls) == 1:
            link = core.run_simple_product_flow(urls[0], False, False, discount, status, model, category_names)
        else:
            link = core.run_variable_product_flow(urls, False, False, discount, status, model, category_names)
        job["result"] = link
    except (
        core.ScrapeError,
        core.ImageDownloadError,
        core.RewriteError,
        core.WooCommerceAPIError,
    ) as e:
        job["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - surface unexpected errors to the UI too
        job["error"] = f"Lỗi không lường trước: {e}"
    finally:
        job["done"] = True


@app.route("/")
def index():
    missing_env = [k for k in core.REQUIRED_ENV if not os.environ.get(k)]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing_env.append("ANTHROPIC_API_KEY")
    return render_template(
        "index.html",
        default_discount=int(os.environ.get("PRICE_DISCOUNT_VND", core.DEFAULT_DISCOUNT_VND)),
        default_status=os.environ.get("WC_PRODUCT_STATUS", core.DEFAULT_STATUS),
        missing_env=missing_env,
    )


@app.route("/import", methods=["POST"])
def start_import():
    raw_urls = request.form.get("urls", "")
    urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
    if not urls:
        return render_template("index.html", error="Cần ít nhất 1 URL sản phẩm.", default_discount=45000, default_status="draft", missing_env=[]), 400

    for u in urls:
        try:
            core.slug_from_url(u)
        except core.ScrapeError as e:
            return render_template("index.html", error=f"URL không hợp lệ: {e}", default_discount=45000, default_status="draft", missing_env=[]), 400

    discount = int(request.form.get("discount") or core.DEFAULT_DISCOUNT_VND)
    status = request.form.get("status") or core.DEFAULT_STATUS
    category_raw = request.form.get("category", "")
    category_names = [c.strip() for c in category_raw.split(",") if c.strip()]
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"logs": [], "done": False, "error": None, "result": None, "urls": urls}

    thread = threading.Thread(
        target=_run_job, args=(job_id, urls, discount, status, category_names, model), daemon=True
    )
    thread.start()

    return redirect(url_for("job_page", job_id=job_id))


@app.route("/job/<job_id>")
def job_page(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return "Không tìm thấy job.", 404
    return render_template("job.html", job_id=job_id, urls=job["urls"])


@app.route("/api/job/<job_id>")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify(
        {"logs": job["logs"], "done": job["done"], "error": job["error"], "result": job["result"]}
    )


if __name__ == "__main__":
    Path("cache").mkdir(exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
