#!/usr/bin/env bash
# 一键启动人名识别预训练器（自动建 venv + 装依赖 + 起服务并开浏览器）。
# 用法: ./run.sh [--crawler-db 市场数据库路径] [--port 8765]
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "[setup] 创建虚拟环境 .venv ..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
echo "[setup] 安装/校验依赖（首次较慢，含 torch/ltp）..."
pip install -q --disable-pip-version-check -r requirements.txt
python app.py "$@"
