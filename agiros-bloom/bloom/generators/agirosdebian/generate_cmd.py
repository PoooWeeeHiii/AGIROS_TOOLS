# Software License Agreement (BSD License)
#
# Copyright (c) 2013, Open Source Robotics Foundation, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Open Source Robotics Foundation, Inc. nor
#    the names of its contributors may be used to endorse or promote
#    products derived from this software without specific prior
#    written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import print_function

import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from bloom.logging import debug
from bloom.logging import error
from bloom.logging import fmt
from bloom.logging import info
from bloom.logging import warning

from bloom.generators.debian.generator import generate_substitutions_from_package
from bloom.generators.debian.generator import place_template_files
from bloom.generators.debian.generator import process_template_files

# 仅做“别名导入”，在此基础上追加自定义参数；不覆盖本模块函数名
from bloom.generators.debian.generate_cmd import prepare_arguments as _base_prepare
from bloom.generators.debian.generate_cmd import main as _base_main

from bloom.rosdistro_api import get_non_eol_distros_prompt

try:
    from rosdep2 import create_default_installer_context
except ImportError:
    debug(traceback.format_exc())
    error("rosdep was not detected, please install it.", exit=True)

try:
    from catkin_pkg.packages import find_packages
except ImportError:
    debug(traceback.format_exc())
    error("catkin_pkg was not detected, please install it.", exit=True)

try:
    import yaml
except Exception:
    yaml = None


def _is_placeholder(s):
    return isinstance(s, str) and s.startswith(':{')


def _locate_tracks(pkg_dir: Path) -> Optional[Path]:
    """Locate tracks.yaml:
    1) $OOB_TRACKS_DIR/<pkg>/{tracks.yaml,track.yaml}
    2) {pkg_dir, pkg_dir.parent}/{tracks.yaml,track.yaml}
    """
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


def _read_tracks(pkg_dir: Path, tracks_distro: Optional[str]) -> Dict[str, Any]:
    """Extract release_inc and other keys from tracks.yaml."""
    result: Dict[str, Any] = {}
    path = _locate_tracks(pkg_dir)
    if not path or yaml is None:
        return result
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
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


def _resolve_version(pkg_dir: Path) -> Optional[str]:
    try:
        pkg = parse_package(str(pkg_dir))
        return pkg.version
    except Exception:
        return None


def _resolve_ros_distro(cli_distro: Optional[str]) -> str:
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


def _ensure_gbp_conf(
    debian_dir: Path,
    pkg_dir: Path,
    tracks_distro: Optional[str],
    cli_distro: Optional[str],
    pkg_override: Optional[str],
):
    """Create or overwrite debian/gbp.conf with computed upstream-tag/tree."""
    debian_dir.mkdir(parents=True, exist_ok=True)
    gbp = debian_dir / 'gbp.conf'

    track_vals = _read_tracks(pkg_dir, tracks_distro)
    release_inc = track_vals.get('release_inc') or '0'
    version = _resolve_version(pkg_dir) or track_vals.get('track_version') or '0.0.0'
    ros_distro = _resolve_ros_distro(cli_distro)
    pkg_name = (pkg_override or pkg_dir.name).strip()
    if not pkg_name:
        pkg_name = pkg_dir.name

    upstream_tag = f"release/{ros_distro}/{pkg_name}/{version}-{release_inc}"

    content = (
        "[git-buildpackage]\n"
        f"upstream-tag={upstream_tag}\n"
        "upstream-tree=tag\n"
    )
    gbp.write_text(content, encoding='utf-8')
    info(f"gbp.conf updated with upstream-tag={upstream_tag}")


def prepare_arguments(parser):
    # 先注册上游所有参数，再追加两个自定义参数
    _base_prepare(parser)
    parser.add_argument(
        '--generate-gbp',
        action='store_true',
        help='Generate/sync debian/gbp.conf from tracks.yaml (no other actions).'
    )
    parser.add_argument(
        '--tracks-distro',
        default=None,
        help="Override tracks distro key (fallback to $OOB_TRACKS_DISTRO or 'jazzy')"
    )
    parser.add_argument(
        '--pkg',
        default=None,
        help="Override package name used in upstream-tag (default: directory name)"
    )
    parser.add_argument(
        '--distro',
        default=None,
        help="AGIROS distro name for release tag (fallback to $AGIROS_DISTRO)"
    )
    return parser


def get_subs(pkg, os_name, os_version, ros_distro, deb_inc=0, native=False):
    return generate_substitutions_from_package(
        pkg,
        os_name,
        os_version,
        ros_distro,
        deb_inc=deb_inc,
        native=native
    )


def main(args=None, get_subs_fn=None):
    get_subs_fn = get_subs_fn or get_subs
    _place_template_files = True
    _process_template_files = True
    package_path = os.getcwd()
    if args is not None:
        package_path = args.package_path or os.getcwd()
        _place_template_files = args.place_template_files
        _process_template_files = args.process_template_files

    pkgs_dict = find_packages(package_path)
    if len(pkgs_dict) == 0:
        sys.exit("No packages found in path: '{0}'".format(package_path))
    if len(pkgs_dict) > 1:
        sys.exit("Multiple packages found, "
                 "this tool only supports one package at a time.")

    os_data = create_default_installer_context().get_os_name_and_version()
    os_name, os_version = os_data
    ros_distro = os.environ.get('ROS_DISTRO', 'indigo')

    # Allow args overrides
    os_name = args.os_name or os_name
    os_version = args.os_version or os_version
    ros_distro = args.ros_distro or ros_distro

    # Summarize
    info(fmt("@!@{gf}==> @|") +
         fmt("Generating debs for @{cf}%s:%s@| for package(s) %s" %
             (os_name, os_version, [p.name for p in pkgs_dict.values()])))

    for path, pkg in pkgs_dict.items():
        template_files = None
        try:
            subs = get_subs_fn(pkg, os_name, os_version, ros_distro, args.debian_inc, args.native)
            if _place_template_files:
                # Place template files
                place_template_files(path, pkg.get_build_type())
            if _process_template_files:
                # Just process existing template files
                template_files = process_template_files(path, subs)
            if not _place_template_files and not _process_template_files:
                # If neither, do both
                place_template_files(path, pkg.get_build_type())
                template_files = process_template_files(path, subs)

            # ---- AGIROS extension: generate/sync gbp.conf ----
            if getattr(args, 'generate_gbp', False):
                try:
                    _ensure_gbp_conf(
                        Path(path) / 'debian',
                        Path(path),
                        args.tracks_distro,
                        getattr(args, 'distro', None),
                        getattr(args, 'pkg', None),
                    )
                except Exception as e:
                    warning("Skip gbp.conf sync (%s)" % e)

            if template_files is not None:
                for template_file in template_files:
                    os.remove(os.path.normpath(template_file))
        except Exception as exc:
            debug(traceback.format_exc())
            error(type(exc).__name__ + ": " + str(exc), exit=True)
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)

# This describes this command to the loader
description = dict(
    title='agirosdebian',
    description="Generates debian packaging files for a catkin package (AGIROS extended)",
    main=main,
    prepare_arguments=prepare_arguments
)
from catkin_pkg.package import parse_package
