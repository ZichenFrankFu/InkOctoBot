@echo off
REM 一键启动人名识别预训练器（Windows）。用法: run.bat [--crawler-db 路径] [--port 8765]
cd /d "%~dp0"
if not exist .venv (
  echo [setup] 创建虚拟环境 .venv ...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo [setup] 安装/校验依赖（首次较慢，含 torch/ltp）...
pip install -q --disable-pip-version-check -r requirements.txt
python app.py %*
