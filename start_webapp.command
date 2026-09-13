#!/bin/bash
# macOS: double-click để chạy Product Maker.
cd "$(dirname "$0")" || exit 1

PY=python3
command -v $PY >/dev/null 2>&1 || { echo "Chưa cài Python 3. Tải tại https://www.python.org/downloads/"; read -r -p "Nhấn Enter để đóng..."; exit 1; }

if [ ! -x ".venv/bin/python" ]; then
  echo "Lần chạy đầu: đang tạo môi trường Python (.venv)..."
  $PY -m venv .venv || { read -r -p "Không tạo được .venv. Nhấn Enter để đóng..."; exit 1; }
fi

if ! .venv/bin/python -c "import flask, waitress, anthropic, PIL, numpy, lxml, bs4, dotenv" >/dev/null 2>&1 || [ requirements.txt -nt .venv/.installed ]; then
  echo "Đang cài thư viện cần thiết..."
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt && touch .venv/.installed
fi

.venv/bin/python webapp.py "$@"
