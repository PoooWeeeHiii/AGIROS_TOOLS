#!/usr/bin/env bash
set -euo pipefail

# 修改为你的实际路径
CODE_DIR="${CODE_DIR:-/opt/code_dir}"

# 打印开始信息
echo "[INFO] 清理 $CODE_DIR 下的 debian/ 和 rpm/ 目录..."

# 仅删除名为 debian 或 rpm 的目录（不区分大小写）
find "$CODE_DIR" -type d \( -iname debian -o -iname rpm \) -exec rm -rf {} + -print

echo "[INFO] 清理完成。下次运行 agiros_oob_builder_procedural.py 会重新生成。"