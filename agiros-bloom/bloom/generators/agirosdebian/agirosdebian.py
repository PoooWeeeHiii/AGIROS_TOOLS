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
"""

from __future__ import print_function

from pathlib import Path
import os
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:
    yaml = None

from bloom.generators.common import default_fallback_resolver
from bloom.generators.debian import DebianGenerator
from bloom.generators.debian.generator import (
    generate_substitutions_from_package,
    place_template_files as base_place_templates,
)
from bloom.generators.debian.generate_cmd import main as debian_main
from bloom.generators.debian.generate_cmd import prepare_arguments
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
        add = parser.add_argument
        add('rosdistro', help="AGIROS distro to target (e.g., loong)")
        return DebianGenerator.prepare_arguments(self, parser)

    def handle_arguments(self, args):
        self.rosdistro = args.rosdistro
        return DebianGenerator.handle_arguments(self, args)

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
            self._ensure_gbp_conf(Path(debian_dir).resolve(), pkg_dir)
            info("gbp.conf synchronized with tracks.yaml (if available)")
        except Exception as e:
            warning(f"Skip gbp.conf sync ({e})")

    # ---------------------- Tracks / gbp.conf plumbing -------------------
    def _ensure_gbp_conf(self, debian_dir: Path, pkg_dir: Path):
        """Create or patch debian/gbp.conf with upstream-branch & tag."""
        debian_dir.mkdir(parents=True, exist_ok=True)
        gbp = debian_dir / 'gbp.conf'

        values = self._read_tracks(pkg_dir)
        upstream_branch = values.get('upstream_branch', 'upstream')
        upstream_tag_tpl = values.get('release_tag', '@(release_tag)')

        if gbp.exists():
            txt = gbp.read_text(encoding='utf-8')
            txt = self._set_conf_key(txt, 'upstream-branch', upstream_branch)
            txt = self._set_conf_key(txt, 'upstream-tag', upstream_tag_tpl)
            if 'upstream-tree' not in txt:
                txt += "\nupstream-tree=tag\n"
            gbp.write_text(txt, encoding='utf-8')
        else:
            content = (
                "[git-buildpackage]\n"
                f"upstream-branch={upstream_branch}\n"
                f"upstream-tag={upstream_tag_tpl}\n"
                "upstream-tree=tag\n"
            )
            gbp.write_text(content, encoding='utf-8')

    def _set_conf_key(self, txt: str, key: str, val: str) -> str:
        lines = []
        found = False
        for line in txt.splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={val}")
                found = True
            else:
                lines.append(line)
        if not found:
            lines.append(f"{key}={val}")
        return "\n".join(lines) + "\n"

    def _read_tracks(self, pkg_dir: Path) -> Dict[str, str]:
        """Read useful keys from tracks.yaml for the current distro."""
        result: Dict[str, str] = {}
        tracks_path = self._locate_tracks(pkg_dir)
        if not tracks_path or yaml is None:
            return result
        try:
            data = yaml.safe_load(tracks_path.read_text(encoding='utf-8')) or {}
        except Exception:
            return result
        tracks = data.get('tracks', data) if isinstance(data, dict) else {}
        distro = (os.environ.get('OOB_TRACKS_DISTRO') or 'jazzy').lower()

        section: Optional[Dict[str, Any]] = None
        for k, v in tracks.items():
            if isinstance(k, str) and k.lower() == distro and isinstance(v, dict):
                section = v
                break
        if not section:
            return result

        devel = section.get('devel_branch') or section.get('upstream-branch')
        version = section.get('version')
        if isinstance(devel, str) and devel.strip():
            result['upstream_branch'] = devel.strip()
        elif isinstance(version, str) and version.strip() and not _is_placeholder(version):
            result['upstream_branch'] = version.strip()
        else:
            result['upstream_branch'] = 'upstream'

        rel_tag = section.get('release_tag') or section.get('release-tag')
        if isinstance(rel_tag, str) and rel_tag.strip():
            result['release_tag'] = rel_tag.strip()
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


def main(args=None):
    # 继续沿用上游入口（不改变既有行为）
    return debian_main(args, generate_substitutions_from_package)


description = dict(
    title='agirosdebian',
    description="Generates AGIROS style debian packaging files (extended)",
    main=main,
    prepare_arguments=prepare_arguments,
)
