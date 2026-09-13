"""Background job queue: one worker thread runs import phases in order, so two
jobs never write the same product cache or hit the store at the same time."""
from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid

from lib import pipeline, settings
from lib.scraper import validate_url

from .db import Database

ACTIVE_STATUSES = ("queued", "running", "review")


class JobReporter(pipeline.Reporter):
    def __init__(self, manager: "JobManager", job_id: str):
        self.m = manager
        self.job_id = job_id
        self.stages = (manager.db.get_job(job_id) or {}).get("stages") or {}
        self._last_progress_flush = 0.0

    def log(self, stage: str, msg: str, level: str = "info") -> None:
        print(f"[{self.job_id}] [{stage}] {msg}", flush=True)
        self.m.db.add_log(self.job_id, stage, level, msg)

    def _flush(self) -> None:
        self.m.db.update_job(self.job_id, stages=self.stages)

    def stage(self, key: str, status: str, detail: str = "") -> None:
        entry = self.stages.setdefault(key, {})
        entry.update({"status": status, "detail": detail, "at": time.time()})
        if status == "running":
            entry.pop("done", None)
            entry.pop("total", None)
        self._flush()

    def progress(self, key: str, done: int, total: int) -> None:
        entry = self.stages.setdefault(key, {"status": "running"})
        entry.update({"done": done, "total": total})
        now = time.time()
        if done >= total or now - self._last_progress_flush > 0.4:
            self._last_progress_flush = now
            self._flush()

    def check_cancelled(self) -> None:
        if self.job_id in self.m.cancel_requested:
            raise pipeline.Cancelled()

    def fail_running(self) -> None:
        for entry in self.stages.values():
            if entry.get("status") == "running":
                entry["status"] = "error"
        self._flush()


class JobManager:
    def __init__(self, db: Database):
        self.db = db
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_requested: set = set()
        self.current: str = ""
        self._worker = threading.Thread(target=self._loop, name="import-worker", daemon=True)

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        self._recover()
        self._worker.start()

    def _recover(self) -> None:
        """Jobs left mid-flight by a previous run: queued ones go back in the
        queue, running ones are marked failed (they resume from cache on retry)."""
        for job in reversed(self.db.list_jobs("queued,running", limit=500, light=False)):
            if job["status"] == "queued":
                self.queue.put((job["id"], job["phase"]))
            else:
                self.db.update_job(job["id"], status="failed", error="Ứng dụng đã bị tắt khi đang xử lý. Bấm 'Thử lại' để chạy tiếp.")

    # --- commands ---------------------------------------------------------------
    def find_active_overlap(self, urls: list) -> dict | None:
        slugs = {validate_url(u) for u in urls}
        for job in self.db.list_jobs(",".join(ACTIVE_STATUSES), limit=200):
            other = set()
            for u in job["urls"]:
                try:
                    other.add(validate_url(u))
                except Exception:  # noqa: BLE001
                    pass
            if slugs & other:
                return job
        return None

    def submit(self, urls: list, options: dict) -> str:
        job_id = uuid.uuid4().hex[:10]
        self.db.create_job(job_id, urls, options)
        self.queue.put((job_id, "prepare"))
        return job_id

    # Every command is a conditional status transition, so a click that races
    # with the worker (or another tab) can't resurrect or double-run a job.
    def approve(self, job_id: str, draft: dict) -> bool:
        if not self.db.transition(job_id, ("review",), draft=draft, status="queued", phase="publish", error=None):
            return False
        self.cancel_requested.discard(job_id)
        self.queue.put((job_id, "publish"))
        return True

    def retry(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        if not job:
            return False
        phase = job["phase"]
        if phase == "publish" and not job.get("draft"):
            phase = "prepare"
        if not self.db.transition(job_id, ("failed", "cancelled"), status="queued", phase=phase, error=None):
            return False
        self.cancel_requested.discard(job_id)
        self.queue.put((job_id, phase))
        return True

    def reopen(self, job_id: str) -> bool:
        """Back to the review step with the saved draft (no automatic publish)."""
        job = self.db.get_job(job_id)
        if not job or not job.get("draft"):
            return False
        stages = job.get("stages") or {}
        for key in ("upload", "publish"):
            stages.pop(key, None)
        stages["review"] = {"status": "running", "detail": "chờ bạn duyệt", "at": time.time()}
        return self.db.transition(job_id, ("failed", "cancelled"), status="review", phase="publish", error=None, stages=stages)

    def regenerate(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        if not job:
            return False
        options = dict(job["options"])
        options["force_rewrite"] = True
        options["regenerate"] = True  # merge the new AI text into the edited draft
        if not self.db.transition(
            job_id, ("review", "failed", "cancelled"), options=options, status="queued", phase="prepare", error=None, stages={}
        ):
            return False
        self.cancel_requested.discard(job_id)
        self.queue.put((job_id, "prepare"))
        return True

    def cancel(self, job_id: str) -> str:
        """Returns 'cancelled', 'stopping' (running job will stop at the next
        safe point) or '' when the job can no longer be cancelled."""
        if self.db.transition(job_id, ("queued",), status="cancelled", error="Đã huỷ trước khi chạy."):
            return "cancelled"
        if self.db.transition(job_id, ("review",), status="cancelled", error="Đã huỷ ở bước duyệt."):
            return "cancelled"
        job = self.db.get_job(job_id)
        if job and job["status"] == "running":
            self.cancel_requested.add(job_id)
            still = self.db.get_job(job_id)
            if still and still["status"] == "running":
                return "stopping"
            self.cancel_requested.discard(job_id)
        return ""

    # --- worker -------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job_id, phase = self.queue.get()
            try:
                # Claim the job atomically; skip it if it was cancelled, deleted
                # or re-queued for another phase while waiting.
                self.cancel_requested.discard(job_id)
                if not self.db.transition(job_id, ("queued",), phase=phase, status="running", error=None):
                    continue
                self.current = job_id
                self._run(self.db.get_job(job_id), phase)
            finally:
                self.current = ""
                self.cancel_requested.discard(job_id)
                self.queue.task_done()

    def _run(self, job: dict, phase: str) -> None:
        job_id = job["id"]
        reporter = JobReporter(self, job_id)
        try:
            if phase == "prepare":
                self._prepare(job, reporter)
            else:
                self._publish(job, reporter)
        except pipeline.Cancelled:
            reporter.fail_running()
            reporter.log("system", "Đã huỷ theo yêu cầu.", "warn")
            self.db.update_job(job_id, status="cancelled", error="Đã huỷ theo yêu cầu.")
        except (pipeline.DraftError, *pipeline.PIPELINE_ERRORS) as e:
            reporter.fail_running()
            reporter.log("system", str(e), "error")
            self.db.update_job(job_id, status="failed", error=str(e))
        except Exception as e:  # noqa: BLE001 - surface anything unexpected to the UI
            traceback.print_exc()
            reporter.fail_running()
            reporter.log("system", f"Lỗi không lường trước: {e!r}", "error")
            self.db.update_job(job_id, status="failed", error=f"Lỗi không lường trước: {e}")

    def _prepare(self, job: dict, reporter: JobReporter) -> None:
        options = pipeline.PrepareOptions.from_dict(job["options"])
        for key in ("review", "upload", "publish"):
            reporter.stages.pop(key, None)
        draft = pipeline.prepare(job["urls"], options, reporter)
        if job["options"].get("regenerate") and job.get("draft"):
            draft = pipeline.merge_regenerated(job["draft"], draft)
            reporter.log("content", "Đã thay tên, mô tả và phụ kiện bằng bản AI mới; giữ nguyên ảnh, giá, phiên bản, danh mục.")
        thumb = draft["images"][0] if draft["images"] else None
        self.db.update_job(job["id"], draft=draft, kind=draft["kind"], title=draft["title"], thumb=thumb)
        # Options that only mattered for this run shouldn't stick for retries.
        opts = dict(job["options"])
        opts.pop("force_scrape", None)
        opts.pop("force_rewrite", None)
        opts.pop("regenerate", None)
        self.db.update_job(job["id"], options=opts)

        reporter.check_cancelled()
        if job["options"].get("review", True):
            reporter.stage("review", "running", "chờ bạn duyệt")
            self.db.update_job(job["id"], status="review", phase="publish")
            reporter.log("review", "Bản nháp đã sẵn sàng - kiểm tra và chỉnh sửa trước khi đăng.")
        else:
            reporter.stage("review", "skipped", "tự động đăng")
            self.db.update_job(job["id"], phase="publish")
            self._publish(self.db.get_job(job["id"]), reporter)

    def _publish(self, job: dict, reporter: JobReporter) -> None:
        if reporter.stages.get("review", {}).get("status") == "running":
            reporter.stage("review", "done", "đã duyệt")
        for key in ("upload", "publish"):
            reporter.stages.pop(key, None)
        result = pipeline.publish(job["draft"], reporter)
        self.db.update_job(job["id"], status="done", result=result, title=result["name"])
        self.db.upsert_product(pipeline.site_url(), result, job["id"])
        verbs = {
            "created": "Đã tạo sản phẩm mới",
            "updated": "Đã cập nhật sản phẩm",
            "extended": "Đã bổ sung phiên bản",
            "skipped": "Sản phẩm đã có sẵn - không thay đổi",
        }
        reporter.log("publish", f"{verbs.get(result['action'], 'Hoàn tất')} #{result['product_id']}")

    # --- misc -----------------------------------------------------------------
    def snapshot(self) -> dict:
        return {"current": self.current, "waiting": self.queue.qsize(), "site": settings.get("WP_SITE_URL")}
