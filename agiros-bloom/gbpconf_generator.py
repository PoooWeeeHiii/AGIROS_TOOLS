#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate debian/gbp.conf for a package by reading its tracks.yaml.

Usage (standalone):
  1) Using package name + default roots (override via env):
     python -m agiros_bloom.gbpconf_generator --distro jazzy --pkg acado_vendor

  2) Using explicit paths:
     python -m agiros_bloom.gbpconf_generator --distro jazzy \
         --release-path /path/to/ros2_release_dir/acado_vendor \
         --source-path  /path/to/code_dir/acado_vendor

Environment overrides:
  - AGIROS_RELEASE_DIR  default root of tracks.yaml directories (ros2_release_dir)
  - AGIROS_CODE_DIR     default root of source code directories (code_dir)

This module is designed to be imported by agirosdebian (with a CLI switch like
--generate_gbp), but it can also be executed directly.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    print("ERROR: PyYAML is required. Try: pip install pyyaml", file=sys.stderr)
    raise

# -----------------------------
# Global configuration (overridable via env)
# -----------------------------
DEFAULT_RELEASE_ROOT = Path(os.getenv("AGIROS_RELEASE_DIR", \
    "/home/wangruiqi/agiros_tool_new/agiros_tools/ros2_release_dir"))
DEFAULT_SOURCE_ROOT = Path(os.getenv("AGIROS_CODE_DIR", \
    "/home/wangruiqi/agiros_tool_new/agiros_tools/code_dir"))
DEFAULT_DISTRO = os.getenv("AGIROS_DISTRO", "jazzy")

DEBIAN_DIR_NAME = "debian"
GBP_CONF_NAME = "gbp.conf"
TRACKS_CANDIDATES = ("tracks.yaml", "track.yaml")

# -----------------------------
# Helpers
# -----------------------------

def _find_tracks_file(release_dir: Path) -> Path:
    for name in TRACKS_CANDIDATES:
        p = release_dir / name
        if p.is_file():
            return p
    raise FileNotFoundError(f"tracks.yaml not found under: {release_dir}")


def _load_tracks(tracks_path: Path) -> Dict:
    data = yaml.safe_load(tracks_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML structure in {tracks_path}")
    # Some repos use top-level key 'tracks', some flatten
    return data.get("tracks", data)


def _pick_distro_section(tracks: Dict, distro: str) -> Dict:
    key_lower = distro.lower()
    # exact (case-insensitive) match
    for k, v in tracks.items():
        if isinstance(k, str) and k.lower() == key_lower and isinstance(v, dict):
            return v
    # partial fallback (contains)
    for k, v in tracks.items():
        if isinstance(k, str) and key_lower in k.lower() and isinstance(v, dict):
            return v
    raise KeyError(f"No section for distro '{distro}' in tracks.yaml")


def _normalize_branch_name(name: str) -> str:
    # Keep it simple: replace spaces with '-', ensure non-empty
    name = name.strip().replace(" ", "-")
    return name or "upstream"


def _read_pkgxml_version(source_dir: Path) -> str:
    """Read version from package.xml (<version>...</version>).
    Falls back to '0.0.0' if not found.
    """
    pkgxml = source_dir / "package.xml"
    if not pkgxml.is_file():
        return "0.0.0"
    text = pkgxml.read_text(encoding="utf-8", errors="ignore")
    # naive extraction to avoid adding deps
    import re
    m = re.search(r"<\s*version\s*>\s*([^<]+)\s*<\s*/\s*version\s*>", text)
    return (m.group(1).strip() if m else "0.0.0")


def _extract_upstream_info(section: Dict) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (upstream_branch, tag_pattern, tree_value).

    Priority rules:
      upstream_branch: devel_branch -> upstream-branch -> version (if not a placeholder) -> 'upstream'
      tag_pattern   : section['release']['tags'] -> section['release_tag'] (legacy) -> None
      tree_value    : section['release']['tree'] -> None (caller will default to 'tag')
    """
    # upstream_branch
    devel_branch = section.get("devel_branch") or section.get("upstream-branch")
    version = section.get("version")
    if isinstance(devel_branch, str) and devel_branch.strip():
        upstream_branch = _normalize_branch_name(devel_branch)
    elif isinstance(version, str) and version.strip() and not version.startswith(":{"):
        upstream_branch = _normalize_branch_name(version)
    else:
        upstream_branch = "upstream"

    # tag_pattern & tree_value
    tag_pattern: Optional[str] = None
    tree_value: Optional[str] = None

    release = section.get("release")
    if isinstance(release, dict):
        tp = release.get("tags")
        if isinstance(tp, str) and tp.strip():
            tag_pattern = tp.strip()
        tv = release.get("tree")
        if isinstance(tv, str) and tv.strip():
            tree_value = tv.strip()

    if not tag_pattern:
        # legacy field
        legacy = section.get("release_tag") or section.get("release-tag")
        if isinstance(legacy, str) and legacy.strip():
            tag_pattern = legacy.strip()

    return upstream_branch, tag_pattern, tree_value


def _ensure_debian_dir(source_dir: Path) -> Path:
    debian_dir = source_dir / DEBIAN_DIR_NAME
    debian_dir.mkdir(parents=True, exist_ok=True)
    return debian_dir


def _render_tag(pattern: str, *, distro: str, pkg: str, version: str, release_inc: int) -> str:
    """Render {version}/{release_inc} placeholders in tag pattern.
    Supports either '{var}' or ':{var}' placeholder styles.
    Falls back to the original pattern if formatting fails.
    """
    # normalize ':{var}' -> '{var}' to allow str.format
    import re
    norm = re.sub(r":\{(\w+)\}", r"{\1}", pattern)
    mapping = {
        "version": version,
        "release_inc": release_inc,
        "distro": distro,
        "package": pkg,
        "pkg": pkg,
    }
    try:
        return norm.format(**mapping)
    except Exception:
        # if pattern does not use placeholders, just return as is
        return pattern


def _write_gbp_conf(debian_dir: Path, upstream_tag: str, tree_value: Optional[str]) -> Path:
    gbp_path = debian_dir / GBP_CONF_NAME
    lines = ["[git-buildpackage]"]
    # Only two required fields per request
    lines.append(f"upstream-tag={upstream_tag}")
    lines.append(f"upstream-tree={tree_value or 'tag'}")

    gbp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gbp_path


# -----------------------------
# Main API
# -----------------------------

def generate_gbp_conf(*, distro: str, pkg: Optional[str] = None, release_path: Optional[Path] = None, source_path: Optional[Path] = None,
                      release_root: Path = DEFAULT_RELEASE_ROOT, source_root: Path = DEFAULT_SOURCE_ROOT) -> Path:
    """Generate debian/gbp.conf for a package.

    Args:
        distro:        distro key to use (e.g., 'jazzy', 'loong', 'humble').
        pkg:           package name. Used to resolve paths when explicit paths not given.
        release_path:  explicit path to the release repo directory containing tracks.yaml.
        source_path:   explicit path to source code directory where debian/gbp.conf will be written.
        release_root:  default root for release repos (has many <pkg>/tracks.yaml).
        source_root:   default root for source code repos (has many <pkg>/.. with debian/).

    Returns:
        Path to the generated gbp.conf
    """
    if not release_path and not pkg:
        raise ValueError("Either --pkg or --release-path must be provided")

    if not source_path and not pkg:
        raise ValueError("Either --pkg or --source-path must be provided")

    # Resolve paths if not explicitly provided
    if release_path is None:
        release_path = release_root / pkg  # type: ignore[arg-type]
    if source_path is None:
        # Common case: top-level match under source_root
        cand1 = source_root / pkg  # type: ignore[arg-type]
        if cand1.is_dir():
            source_path = cand1
        else:
            # Search one level deeper (some projects are nested one level)
            matches = list(source_root.glob(f"*/{pkg}"))  # type: ignore[arg-type]
            if matches:
                source_path = matches[0]
            else:
                raise FileNotFoundError(f"Could not locate source dir for package '{pkg}' under {source_root}")

    release_path = Path(release_path).resolve()
    source_path = Path(source_path).resolve()

    tracks_file = _find_tracks_file(release_path)
    tracks_dict = _load_tracks(tracks_file)
    section = _pick_distro_section(tracks_dict, distro)

    # Keep existing extraction to get tree preference if provided
    _, _tag_pattern_unused, tree_value = _extract_upstream_info(section)

    # release_inc from tracks
    release_inc = section.get("release_inc")
    try:
        release_inc = int(release_inc) if release_inc is not None else 1
    except Exception:
        release_inc = 1

    # version from package.xml in source tree
    version_from_pkgxml = _read_pkgxml_version(source_path)

    # -----------------------------
    # **Core change requested**: always render as
    # upstream_tag = f"release/{distro}/{pkg}/{version}-{release_inc}"
    # -----------------------------
    pkg_name = (pkg or source_path.name)
    upstream_tag = f"release/{distro}/{pkg_name}/{version_from_pkgxml}-{release_inc}"

    debian_dir = _ensure_debian_dir(source_path)
    gbp_path = _write_gbp_conf(debian_dir, upstream_tag, tree_value)

    print(f"[gbpconf] distro='{distro}', pkg='{pkg_name}'")
    print(f"[gbpconf] release={release_path}")
    print(f"[gbpconf] source ={source_path}")
    print(f"[gbpconf] wrote  ={gbp_path}")

    return gbp_path


# -----------------------------
# CLI
# -----------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate debian/gbp.conf from tracks.yaml")
    p.add_argument("--distro", default=DEFAULT_DISTRO, help="distro key in tracks.yaml (e.g., jazzy, loong, humble)")

    pkg_group = p.add_mutually_exclusive_group(required=False)
    pkg_group.add_argument("--pkg", help="package name (used with default roots)")
    pkg_group.add_argument("--release-path", type=Path, help="explicit path to release dir containing tracks.yaml")

    p.add_argument("--source-path", type=Path, help="explicit path to source dir where debian/gbp.conf will be written")

    p.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT,
                   help=f"root dir of release repos (default: {DEFAULT_RELEASE_ROOT})")
    p.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT,
                   help=f"root dir of source repos (default: {DEFAULT_SOURCE_ROOT})")

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    try:
        generate_gbp_conf(
            distro=args.distro,
            pkg=args.pkg,
            release_path=args.release_path,
            source_path=args.source_path,
            release_root=args.release_root,
            source_root=args.source_root,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
