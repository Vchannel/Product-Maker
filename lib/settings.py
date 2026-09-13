"""Paths and .env-backed configuration shared by the CLI and the web app.

Every path is anchored to the project folder (not the current working
directory), so launching from a shortcut, another folder, or a double-click
script all read/write the same cache and .env.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = Path(os.environ.get("PM_ENV_FILE") or BASE_DIR / ".env")
CACHE_DIR = Path(os.environ.get("PM_CACHE_DIR") or BASE_DIR / "cache")
DATA_DIR = Path(os.environ.get("PM_DATA_DIR") or BASE_DIR / "data")

DEFAULT_DISCOUNT_VND = 45000
DEFAULT_STATUS = "draft"
DEFAULT_MODEL = "claude-opus-5"

MODEL_CHOICES = [
    ("claude-opus-5", "Claude Opus 5", "Chất lượng viết tốt nhất (khuyến nghị)"),
    ("claude-sonnet-5", "Claude Sonnet 5", "Cân bằng chất lượng / chi phí"),
    ("claude-haiku-4-5", "Claude Haiku 4.5", "Nhanh và rẻ nhất"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6", "Thế hệ trước"),
]

STATUS_CHOICES = [
    ("draft", "Nháp (draft)"),
    ("pending", "Chờ duyệt (pending)"),
    ("publish", "Đăng ngay (publish)"),
]


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    secret: bool = False
    required: bool = False
    help: str = ""
    placeholder: str = ""


FIELDS = [
    Field("WP_SITE_URL", "Địa chỉ website", "woocommerce", required=True,
          placeholder="https://vchannelstore.com", help="Phải là HTTPS để Application Password hoạt động."),
    Field("WC_CONSUMER_KEY", "Consumer Key", "woocommerce", secret=True, required=True,
          placeholder="ck_…", help="WooCommerce › Settings › Advanced › REST API › Add key (quyền Read/Write)."),
    Field("WC_CONSUMER_SECRET", "Consumer Secret", "woocommerce", secret=True, required=True, placeholder="cs_…"),
    Field("WP_USERNAME", "Tên đăng nhập WordPress", "wordpress", required=True,
          help="User có quyền upload media."),
    Field("WP_APP_PASSWORD", "Application Password", "wordpress", secret=True, required=True,
          placeholder="xxxx xxxx xxxx xxxx xxxx xxxx",
          help="wp-admin › Users › Profile › Application Passwords › Add New."),
    Field("ANTHROPIC_API_KEY", "Anthropic API key", "anthropic", secret=True, required=True,
          placeholder="sk-ant-…", help="Lấy tại console.anthropic.com."),
    Field("ANTHROPIC_MODEL", "Model viết nội dung", "anthropic"),
    Field("PRICE_DISCOUNT_VND", "Giảm giá mặc định (VND)", "defaults",
          help="sale_price = giá gốc − số tiền này."),
    Field("WC_PRODUCT_STATUS", "Trạng thái mặc định", "defaults"),
]
FIELD_KEYS = {f.key for f in FIELDS}
REQUIRED_KEYS = [f.key for f in FIELDS if f.required]

_env_lock = threading.Lock()


def load_env(override: bool = False) -> None:
    load_dotenv(ENV_PATH, override=override)


def get(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def model() -> str:
    return get("ANTHROPIC_MODEL", DEFAULT_MODEL)


def default_discount() -> int:
    try:
        return int(get("PRICE_DISCOUNT_VND", str(DEFAULT_DISCOUNT_VND)))
    except ValueError:
        return DEFAULT_DISCOUNT_VND


def default_status() -> str:
    value = get("WC_PRODUCT_STATUS", DEFAULT_STATUS)
    return value if value in {s for s, _ in STATUS_CHOICES} else DEFAULT_STATUS


def missing_keys() -> list:
    return [k for k in REQUIRED_KEYS if not get(k)]


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return "•" * 8 + value[-4:]


def public_view() -> dict:
    """Settings as shown to the browser - secrets never leave the server in
    full, only a masked tail so the user can tell which key is configured."""
    values = {}
    for f in FIELDS:
        raw = get(f.key)
        values[f.key] = {
            "value": mask(raw) if f.secret else raw,
            "set": bool(raw),
            "secret": f.secret,
        }
    return values


def save(updates: dict) -> None:
    """Persist changed keys to .env (keeping its comments/order) and apply
    them to the running process. Empty strings for secrets mean 'unchanged'."""
    fields = {f.key: f for f in FIELDS}
    with _env_lock:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        current = dotenv_values(ENV_PATH)
        for key, value in updates.items():
            if key not in fields:
                continue
            value = "" if value is None else str(value).strip()
            if fields[key].secret and not value:
                continue
            if current.get(key) == value:
                continue
            set_key(str(ENV_PATH), key, value, quote_mode="auto")
            os.environ[key] = value
