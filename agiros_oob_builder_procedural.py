#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
改进版 AGIROS OOB 构建器（过程式，支持 resume 与错误重跑，多包支持，自动回退 openEuler 版本）
- 解析 tracks.yaml 的 jazzy 段
- 根据 actions 判断需要生成 debian/spec
- 已生成但错误的（如包含 !nocheck）会重跑
- 正确生成的会跳过
- 将失败记录保存到 fail.log
- 支持单个源码目录下多个 package.xml 的子包，逐个处理
- 生成 spec 时优先尝试 openEuler:24（示例），如果 agirosdep 缺失则回退到其他 openEuler 版本
- 所有缺失的 rosdep rules 会记录到 fail.log，交互默认 "n"
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional
import yaml


def log(msg: str):
    print(msg, flush=True)


def run(cmd, cwd=None, dry_run=False):
    shown = " ".join(cmd)
    prefix = "[DRY]" if dry_run else "[RUN]"
    log(f"{prefix} {shown} (cwd={cwd or os.getcwd()})")
    if dry_run:
        return 0, None
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True)
    out_lines = []
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        out_lines.append(line)
        if "Continue [Y/n]?" in line:
            proc.stdin.write("n\n")
            proc.stdin.flush()
    proc.wait()
    rc = proc.returncode
    if rc != 0:
        return rc, "\n".join(out_lines)
    return rc, None


# ----------------------------- Resume 检查 -----------------------------

def is_valid_debian(pkg_dir: Path) -> bool:
    control = pkg_dir / "debian" / "control"
    if not control.exists():
        return False
    text = control.read_text(encoding="utf-8", errors="ignore")
    if "!nocheck" in text:
        return False
    if "Depends:" not in text:
        return False
    return True


def is_valid_spec(pkg_dir: Path) -> bool:
    rpm_dir = pkg_dir / "rpm"
    if not rpm_dir.is_dir():
        return False
    specs = list(rpm_dir.glob("*.spec"))
    if not specs:
        return False
    for s in specs:
        text = s.read_text(encoding="utf-8", errors="ignore")
        if "!nocheck" in text:
            return False
    return True


# ----------------------------- Tracks Parser -----------------------------

class TracksParser:
    def __init__(self, distro: str = "jazzy"):
        self.distro = distro

    def _find_distro_case_insensitive(self, tracks: Dict[str, Any]) -> Optional[str]:
        target = self.distro.lower()
        for k in tracks.keys():
            if isinstance(k, str) and k.lower() == target:
                return k
        for k in tracks.keys():
            if isinstance(k, str) and target in k.lower():
                return k
        return None

    def parse_file(self, tracks_yaml_path: Path) -> Optional[Dict[str, Any]]:
        try:
            data = yaml.safe_load(tracks_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log(f"[WARN] 解析 YAML 失败: {tracks_yaml_path} -> {e}")
            return None

        tracks = None
        if isinstance(data, dict) and "tracks" in data:
            tracks = data["tracks"]
        elif isinstance(data, dict):
            tracks = data
        else:
            return None

        key = self._find_distro_case_insensitive(tracks)
        if key is None:
            return None
        section = tracks.get(key)
        return section if isinstance(section, dict) else None


# ----------------------------- Main Flow -----------------------------

def find_subpackages(pkg_dir: Path):
    results = []
    for root, dirs, files in os.walk(pkg_dir):
        if "package.xml" in files:
            results.append(Path(root))
    return results if results else [pkg_dir]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-dir", required=True)
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--ros-distro", default="loong")
    ap.add_argument("--ubuntu-default", default="jammy")

    # 将原来的 rhel-* 参数替换为 openeuler-* 参数
    ap.add_argument("--openeuler-default", default="24",
                    help="openEuler 首选版本，如 22/23/24")
    ap.add_argument("--openeuler-fallback", nargs="*", default=["22", "23"],
                    help="openEuler 版本回退列表，按顺序尝试")

    ap.add_argument("--bloom-bin", default="bloom-generate")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    release_dir = Path(args.release_dir)
    code_dir = Path(args.code_dir)
    fail_log = Path("fail.log")

    log(f"[INFO] 初始化 OOB Builder: code_dir={code_dir}, release_dir={release_dir}")

    parser = TracksParser("jazzy")
    pkgs = []
    for child in sorted(release_dir.iterdir()):
        if not child.is_dir():
            continue
        for name in ("tracks.yaml", "track.yaml"):
            p = child / name
            if p.exists():
                pkgs.append((child.name, p))
                break

    log(f"[INFO] 开始扫描 release_dir... 共发现 {len(pkgs)} 个含 tracks.yaml 的包目录")

    total = 0
    with fail_log.open("w", encoding="utf-8") as flog:
        for pkg_name, yaml_path in pkgs[: args.limit or len(pkgs)]:
            section = parser.parse_file(yaml_path)
            if not section:
                log(f"[SKIP] {pkg_name}: 无 jazzy 段")
                continue

            actions = section.get("actions") or []
            if not actions:
                log(f"[SKIP] {pkg_name}: jazzy.actions 为空")
                continue

            # 检测需求
            need_ubuntu = any("--os-name ubuntu" in a for a in actions)
            need_oe = any("--os-name openeuler" in a for a in actions) or any("--os-name rhel" in a for a in actions)
            if not (need_ubuntu or need_oe):
                log(f"[SKIP] {pkg_name}: 无 ubuntu/openeuler 相关 actions")
                continue

            pkg_dir = code_dir / pkg_name
            if not pkg_dir.is_dir():
                log(f"[SKIP] code_dir 中不存在: {pkg_dir}")
                continue

            subpackages = find_subpackages(pkg_dir)

            for subpkg in subpackages:
                log(f"[INFO] 处理包: {pkg_name}/{subpkg.relative_to(pkg_dir)}")

                sub_need_ubuntu, sub_need_oe = need_ubuntu, need_oe
                if sub_need_ubuntu and is_valid_debian(subpkg):
                    log(f"[RESUME] {pkg_name}: 已有有效 debian，跳过 ubuntu 生成。")
                    sub_need_ubuntu = False
                if sub_need_oe and is_valid_spec(subpkg):
                    log(f"[RESUME] {pkg_name}: 已有有效 spec，跳过 openEuler 生成。")
                    sub_need_oe = False
                if not (sub_need_ubuntu or sub_need_oe):
                    continue

                try:
                    if sub_need_ubuntu:
                        rc, out = run([args.bloom_bin, "agirosdebian", "--ros-distro", args.ros_distro,
                                      "--os-name", "ubuntu", "--os-version", args.ubuntu_default],
                                      cwd=subpkg, dry_run=args.dry_run)
                        if rc == 0:
                            total += 1
                            log(f"[OK] {pkg_name}: 已生成 debian/")
                        else:
                            flog.write(f"{pkg_name} ubuntu 失败 rc={rc}\n")
                            if out:
                                for l in out.splitlines():
                                    if "No agirosdep rule for" in l:
                                        flog.write(f"缺失 rule: {l}\n")

                    if sub_need_oe:
                        versions = [args.openeuler_default] + [v for v in args.openeuler_fallback if v != args.openeuler_default]
                        success = False
                        for ver in versions:
                            rc, out = run([args.bloom_bin, "agirosrpm", "--ros-distro", args.ros_distro,
                                          "--os-name", "openeuler", "--os-version", ver],
                                          cwd=subpkg, dry_run=args.dry_run)
                            if rc == 0:
                                total += 1
                                log(f"[OK] {pkg_name}: 已生成 rpm/ (openeuler:{ver})")
                                success = True
                                break
                            else:
                                flog.write(f"{pkg_name} openeuler:{ver} 失败 rc={rc}\n")
                                if out:
                                    for l in out.splitlines():
                                        if "No agirosdep rule for" in l:
                                            flog.write(f"缺失 rule: {l}\n")
                        if not success:
                            log(f"[ERR] {pkg_name}: 所有 openEuler 版本均失败")

                except Exception as e:
                    flog.write(f"{pkg_name} 异常: {e}\n")
                    log(f"[ERR] {pkg_name}: 发生异常 {e}")

    log(f"[INFO] 所有包处理完成。成功生成数：{total}")
    log(f"[INFO] 失败记录已保存到 {fail_log}")


if __name__ == "__main__":
    main()
