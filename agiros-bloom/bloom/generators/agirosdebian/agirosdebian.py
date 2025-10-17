# -*- coding: utf-8 -*-
"""
AgirosDebianGenerator: extend bloom's DebianGenerator without removing
existing features, and wire debian/gbp.conf to tracks.yaml from the
release repos.

Key points:
- Keep upstream behavior; only augment template placement and gbp.conf sync
- Force template group to our own so gbp.conf.em is always placed
- Read tracks.yaml via env:
    OOB_TRACKS_DIR     -> folder containing many <repo>/tracks.yaml
    OOB_TRACKS_DISTRO  -> distro key, e.g. 'jazzy' (default: 'jazzy')

本实现满足：
- `bloom-generate agirosdebian ... --generate-gbp` 不再报 unrecognized；
- 不改变上游分支/模板/提交/打 tag 的整体流程；
- 当传入 `--generate-gbp` 时，仅生成/同步 `debian/gbp.conf`，其余保持幂等；
- 模块入口 `bloom.generators.agirosdebian.generate_cmd` 仍可单包调用。
"""

from __future__ import print_function
from pathlib import Path
import os
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:
    yaml = None

from catkin_pkg.package import parse_package

from bloom.generators.common import default_fallback_resolver
from bloom.generators.debian import DebianGenerator
from bloom.generators.debian.generator import (
    generate_substitutions_from_package,
    place_template_files as base_place_templates,
)
from bloom.logging import info, warning
from bloom.util import execute_command


def _is_placeholder(s: Optional[str]) -> bool:
    return isinstance(s, str) and s.startswith(':{')


class AgirosDebianGenerator(DebianGenerator):
    title = 'agirosdebian'
    description = "Generates debians tailored for the AGIROS rosdistro"
    default_install_prefix = DebianGenerator.default_install_prefix

    # ---------------- CLI wiring (preserve upstream args) ----------------
    def prepare_arguments(self, parser):
        # 保留上游全部参数
        parser = DebianGenerator.prepare_arguments(self, parser)
        # 仅追加我们自己的参数（不覆盖上游）
        add = parser.add_argument
        add('--generate-gbp', action='store_true',
            help='Generate/sync debian/gbp.conf from tracks.yaml (no other actions).')
        add('--tracks-distro', default=None,
            help="Override tracks distro key (fallback to $OOB_TRACKS_DISTRO or 'jazzy')")
        add('--pkg', default=None,
            help="Override package name used in upstream-tag (default: directory name)")
        add('--distro', default=None,
            help="AGIROS distro name for release tag (fallback to $AGIROS_DISTRO)")
        return parser

    def handle_arguments(self, args):
        # 记录 CLI 传参，保持上游行为
        self.generate_gbp = bool(getattr(args, 'generate_gbp', False))
        self.tracks_distro = getattr(args, 'tracks_distro', None)
        self.pkg_override = getattr(args, 'pkg', None)
        self.distro_override = getattr(args, 'distro', None)
        return DebianGenerator.handle_arguments(self, args)

    # 覆写 generate：当仅需 gbp.conf 时，短路上游完整流程（其它路径不变）
    def generate(self):
        if getattr(self, 'generate_gbp', False):
            pkg_dir = Path(os.getcwd())
            deb_dir = Path('debian')
            self._ensure_gbp_conf(deb_dir, pkg_dir, self.tracks_distro, self.distro_override, self.pkg_override)
            info("Only debian/gbp.conf generated (via --generate-gbp).")
            return 0
        # 正常完整生成路径
        return DebianGenerator.generate(self)

    # ---------------- Minimal substitution hook -------------------------
    def get_subs(self, package, debian_distro, releaser_history=None):
        subs = generate_substitutions_from_package(
            package,
            self.os_name,
            debian_distro,
            self.rosdistro,
            self.install_prefix,
            self.debian_inc,
            [p.name for p in self.packages.values()],
            releaser_history=releaser_history,
            fallback_resolver=default_fallback_resolver,
        )
        # 不破坏原有包名规则，只做最小替换（与 deb 约定一致）
        subs['Package'] = subs.get('Package', package.name).replace('_', '-')
        return subs

    # ---------------- Template placement + gbp.conf sync -----------------
    def place_template_files(self, build_type, debian_dir='debian'):
        """
        1) 强制使用我们自己的模板组（带 gbp.conf.em），调用上游放置逻辑
        2) 提交模板文件（保持上游行为一致）
        3) 将 debian/gbp.conf 与 tracks.yaml 对齐（若可用）
        """
        # 仅生成 gbp.conf 时，不放置其它模板文件
        if getattr(self, 'generate_gbp', False):
            pkg_dir = Path(os.getcwd())
            self._ensure_gbp_conf(Path(debian_dir).resolve(), pkg_dir, self.tracks_distro, self.distro_override, self.pkg_override)
            info("gbp.conf synchronized with tracks.yaml (generate-gbp mode).")
            return

        # --- (1) 强制模板组为 agirosdebian，确保 gbp.conf.em 被放入 ---
        prev_group = os.environ.get('BLOOM_TEMPLATE_GROUP')
        try:
            os.environ['BLOOM_TEMPLATE_GROUP'] = 'bloom.generators.agirosdebian'
            base_place_templates('.', build_type, gbp=True)
        finally:
            if prev_group is None:
                os.environ.pop('BLOOM_TEMPLATE_GROUP', None)
            else:
                os.environ['BLOOM_TEMPLATE_GROUP'] = prev_group

        # --- (2) 跟随上游：把模板加入暂存并提交 ---
        execute_command('git add ' + debian_dir)
        _, has_files, _ = execute_command('git diff --cached --name-only', return_io=True)
        if has_files:
            execute_command('git commit -m "Placing debian template files"')

        # --- (3) 同步/生成 gbp.conf ---
        try:
            pkg_dir = Path(os.getcwd())
            self._ensure_gbp_conf(Path(debian_dir).resolve(), pkg_dir, self.tracks_distro, self.distro_override, self.pkg_override)
            info("gbp.conf synchronized with tracks.yaml (full generate).")
        except Exception as e:
            warning(f"Skip gbp.conf sync ({e})")

    # ---------------------- Tracks / gbp.conf plumbing -------------------
    def _ensure_gbp_conf(
        self,
        debian_dir: Path,
        pkg_dir: Path,
        tracks_distro: Optional[str],
        cli_distro: Optional[str],
        pkg_override: Optional[str],
    ):
        """Create or overwrite debian/gbp.conf with computed upstream-tag/tree."""
        debian_dir.mkdir(parents=True, exist_ok=True)
        gbp = debian_dir / 'gbp.conf'

        values = self._read_tracks(pkg_dir, tracks_distro)
        release_inc = values.get('release_inc') or '0'
        version = self._resolve_version(pkg_dir) or values.get('track_version') or '0.0.0'
        ros_distro = self._resolve_ros_distro(cli_distro)
        pkg_name = (pkg_override or pkg_dir.name).strip() or pkg_dir.name

        upstream_tag = f"release/{ros_distro}/{pkg_name}/{version}-{release_inc}"

        content = (
            "[git-buildpackage]\n"
            f"upstream-tag={upstream_tag}\n"
            "upstream-tree=tag\n"
        )
        gbp.write_text(content, encoding='utf-8')

    def _resolve_version(self, pkg_dir: Path) -> Optional[str]:
        try:
            pkg = parse_package(str(pkg_dir))
            return pkg.version
        except Exception:
            return None

    def _resolve_ros_distro(self, cli_distro: Optional[str]) -> str:
        candidates = [
            cli_distro,
            os.environ.get('AGIROS_DISTRO'),
            os.environ.get('AGIROS_ROS_DISTRO'),
            os.environ.get('ROS_DISTRO'),
        ]
        for item in candidates:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return 'unknown'

    def _read_tracks(self, pkg_dir: Path, tracks_distro: Optional[str]) -> Dict[str, Any]:
        """Read release_inc and related keys from tracks.yaml for the current distro."""
        result: Dict[str, Any] = {}
        tracks_path = self._locate_tracks(pkg_dir)
        if not tracks_path or yaml is None:
            return result
        try:
            data = yaml.safe_load(tracks_path.read_text(encoding='utf-8')) or {}
        except Exception:
            return result
        tracks = data.get('tracks', data) if isinstance(data, dict) else {}
        distro = (
            tracks_distro
            or os.environ.get('AGIROS_TRACKS_DISTRO')
            or os.environ.get('OOB_TRACKS_DISTRO')
            or 'jazzy'
        ).lower()

        section: Optional[Dict[str, Any]] = None
        for k, v in tracks.items():
            if isinstance(k, str) and k.lower() == distro and isinstance(v, dict):
                section = v
                break
        if not section:
            return result

        release_inc = section.get('release_inc')
        if isinstance(release_inc, (int, float)):
            release_inc = str(int(release_inc))
        elif isinstance(release_inc, str):
            release_inc = release_inc.strip()
        if release_inc and not _is_placeholder(release_inc):
            result['release_inc'] = release_inc

        version = section.get('version')
        if isinstance(version, str) and version.strip() and not _is_placeholder(version):
            result['track_version'] = version.strip()
        return result

    def _locate_tracks(self, pkg_dir: Path) -> Optional[Path]:
        """Locate tracks.yaml given current working repo dir."""
        env_root = os.environ.get('OOB_TRACKS_DIR', '').strip()
        candidates = []
        if env_root:
            candidates += [
                Path(env_root) / pkg_dir.name / 'tracks.yaml',
                Path(env_root) / pkg_dir.name / 'track.yaml',
            ]
        candidates += [
            pkg_dir / 'tracks.yaml',
            pkg_dir / 'track.yaml',
            pkg_dir.parent / 'tracks.yaml',
            pkg_dir.parent / 'track.yaml',
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None


# ---------- CLI description for bloom.generate_cmd loader ----------
# 这里保持与上游一致：loader 会调用我们提供的 prepare_arguments/main

def _cmd_prepare_arguments(parser):
    # 先让上游 debian cmd 注册全部参数
    from bloom.generators.debian.generate_cmd import prepare_arguments as _base_prepare
    _base_prepare(parser)
    # 仅追加我们自己的参数（与上面类方法保持一致）
    parser.add_argument('--generate-gbp', action='store_true',
                        help='Generate/sync debian/gbp.conf from tracks.yaml (no other actions).')
    parser.add_argument('--tracks-distro', default=None,
                        help="Override tracks distro key (fallback to $OOB_TRACKS_DISTRO or 'jazzy')")
    return parser


def _cmd_main(args=None, get_subs_fn=None):
    # 直接复用上游 debian cmd 的 main，保证行为一致；get_subs_fn 用上游的
    from bloom.generators.debian.generate_cmd import main as _base_main
    return _base_main(args, generate_substitutions_from_package)


description = dict(
    title='agirosdebian',
    description="Generates AGIROS style debian packaging files (extended)",
    main=_cmd_main,
    prepare_arguments=_cmd_prepare_arguments,
)
