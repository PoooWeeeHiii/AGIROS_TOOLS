#!/usr/bin/env bash
set -euo pipefail

# ===== 全局变量（可在执行前 export 覆盖） =====
CODE_DIR=${CODE_DIR:-$HOME/agiros_tools/code_dir}
DEB_OUT=${DEB_OUT:-$HOME/deb_out}
DISTRO=${DISTRO:-loong}
DEFAULT_REL_INC=${DEFAULT_REL_INC:-1}
PARALLEL=${PARALLEL:-$(nproc)}
GIT_USER_NAME=${GIT_USER_NAME:-PoooWeeeHiii}
GIT_USER_EMAIL=${GIT_USER_EMAIL:-powehi041210@gmail.com}

log(){ echo "[INFO] $*"; }
warn(){ echo "[WARN] $*" >&2; }
err(){ echo "[ERR ] $*" >&2; }
need(){ command -v "$1" >/dev/null 2>&1 || { err "missing command: $1"; exit 127; }; }

need gbp; need git; need mk-build-deps; need dpkg-buildpackage

# --- 工具函数 ---
first_line(){ sed -n '1p' "$1" 2>/dev/null || true; }

pkg_name_from_changelog(){
  local s
  s=$(awk 'NR==1{print $1;exit}' debian/changelog 2>/dev/null || true)
  [[ -n "$s" ]] && echo "$s" || basename "$PWD"
}

version_from_changelog(){
  awk 'NR==1{match($0,/\(([^)]+)\)/,m); if(m[1]!="") print m[1];}' debian/changelog 2>/dev/null || true
}

version_from_package_xml(){
  sed -n 's@.*<version>\(.*\)</version>.*@\1@p' package.xml 2>/dev/null | head -n1 || true
}

ensure_gbp_conf(){
  mkdir -p debian
  if [[ ! -f debian/gbp.conf ]]; then
    # 同时写入老/新两种段名，避免告警
    cat > debian/gbp.conf <<EOF
[git-buildpackage]
upstream-tag=release/{distro}/{package}/{version}-{release_inc}
upstream-tree=tag

[buildpackage]
upstream-tag=release/{distro}/{package}/{version}-{release_inc}
upstream-tree=tag
EOF
    log "created debian/gbp.conf"
  fi
}

render_tag(){
  local pkg="$1" ver="$2" inc="$3"
  sed -n 's/^upstream-tag=//p' debian/gbp.conf | head -n1 \
    | sed "s/{distro}/$DISTRO/g; s/{package}/$pkg/g; s/{version}/$ver/g; s/{release_inc}/$inc/g"
}

# 让 dpkg-source 忽略 rpm 产物、源码 tar、build 目录及常见二进制/中间产物
ensure_deb_source_options(){
  mkdir -p debian/source
  [[ -f debian/source/format ]] || echo "3.0 (quilt)" > debian/source/format
  local opt=debian/source/options
  touch "$opt"
  # 聚合所有易引发 “unrepresentable changes to source” 的路径/类型
  if ! grep -q 'extend-diff-ignore' "$opt" 2>/dev/null; then
    cat >> "$opt" <<'EOF'
extend-diff-ignore = "^(rpm/|rpm/SOURCES/|build/|CMakeFiles/|.*\.tar(\.gz|\.xz|\.bz2)?|.*\.(bin|out)|^\.obj-.*|CMakeCache\.txt)$"
EOF
  fi
}

# ====== 新增：构建前删除 rpm/、tar、build/、.obj-*、CMakeCache 等 ======
nuke_local_artifacts(){
  # rpm 整树
  if [[ -d rpm ]]; then
    log "pre-clean: removing ./rpm directory completely"
    rm -rf rpm || true
  fi
  # 各类 tar（排除 debian/）
  find . -type f -regextype posix-extended \
    -regex '.*\.tar(\.(gz|xz|bz2))?$' \
    -not -path './debian/*' \
    -delete 2>/dev/null || true
  # CMake 相关中间产物：build/、.obj-*、CMakeFiles、CMakeCache.txt
  rm -rf build .obj-* CMakeFiles CMakeCache.txt cmake_install.cmake 2>/dev/null || true
}

# 清理会污染 Debian 源包的 rpm 产物（保留，作为温和清理）
clean_rpm_artifacts(){
  if [[ -d rpm ]]; then
    rm -rf rpm/BUILD rpm/BUILDROOT rpm/RPMS rpm/SRPMS rpm/tmp 2>/dev/null || true
    find rpm/SOURCES -maxdepth 1 -type f -regextype posix-extended \
      -regex '.*\.tar(\.gz|\.xz|\.bz2)?' -delete 2>/dev/null || true
  fi
}

# .gitignore 里忽略 rpm/、build/、CMake 产物与大 tar，减少“未跟踪文件”噪音
ensure_gitignore_rpm(){
  local gi=.gitignore
  touch "$gi"
  grep -qxF 'rpm/' "$gi"          || echo 'rpm/'          >> "$gi"
  grep -qxF 'build/' "$gi"        || echo 'build/'        >> "$gi"
  grep -qxF '.obj-*' "$gi"        || echo '.obj-*'        >> "$gi"
  grep -qxF 'CMakeFiles/' "$gi"   || echo 'CMakeFiles/'   >> "$gi"
  grep -qxF 'CMakeCache.txt' "$gi"|| echo 'CMakeCache.txt'>> "$gi"
  grep -qxF '*.tar' "$gi"         || echo '*.tar'         >> "$gi"
  grep -qxF '*.tar.gz' "$gi"      || echo '*.tar.gz'      >> "$gi"
  grep -qxF '*.tar.xz' "$gi"      || echo '*.tar.xz'      >> "$gi"
  grep -qxF '*.tar.bz2' "$gi"     || echo '*.tar.bz2'     >> "$gi"
  grep -qxF '*.bin' "$gi"         || echo '*.bin'         >> "$gi"
  grep -qxF '*.out' "$gi"         || echo '*.out'         >> "$gi"
}

ensure_git_repo(){
  # 关键：以当前目录有无 .git 判定；没有就初始化“本地独立仓库”
  if [[ ! -d .git ]]; then
    git init
    git config user.name "${GIT_USER_NAME:-PoooWeeeHiii}"
    git config user.email "${GIT_USER_EMAIL:-powehi041210@gmail.com}"
    git add -A
    git commit -m "Initial import"
    log "initialized nested git repo in $(pwd)"
  else
    # 已是独立仓库，确保配置存在
    git config user.name "${GIT_USER_NAME:-PoooWeeeHiii}" || true
    git config user.email "${GIT_USER_EMAIL:-powehi041210@gmail.com}" || true
  fi
}

ensure_tag(){
  local tag="$1"
  if ! git rev-parse "$tag"^{tree} >/dev/null 2>&1; then
    git add -A && git commit -m "Prepare for $tag" || true
    git tag -a "$tag" -m "$tag"
    log "created tag: $tag"
  else
    log "tag exists: $tag"
  fi
}

install_build_deps(){
  sudo apt-get update
  sudo apt-get install -y devscripts equivs || true
  if [[ -f debian/control ]]; then
    sudo mk-build-deps -i -r -t 'apt-get -y' debian/control || {
      warn "mk-build-deps failed; try to continue"
    }
  fi
}

is_python_pybuild(){
  # 依据：debian/rules 含 pybuild；或存在 pyproject.toml/setup.py；或 control 里含 dh-python/python3-all
  if grep -Eqs 'pybuild' debian/rules 2>/dev/null; then
    return 0
  fi
  if [[ -f pyproject.toml || -f setup.py || -f setup.cfg ]]; then
    return 0
  fi
  if grep -Eqs 'dh-python|python3-all|python3-.*-dev' debian/control 2>/dev/null; then
    return 0
  fi
  return 1
}

python_pre_clean(){
  fakeroot debian/rules clean || true
  rm -rf .pc .pybuild .pytest_cache .eggs build dist \
         debian/.debhelper debian/debhelper-build-stamp \
         debian/files debian/*.debhelper.log debian/*.substvars \
         ../*.deb ../*.dsc ../*.changes ../*.build ../*.buildinfo \
         ../*.orig.tar.* .obj-* __pycache__ CMakeFiles CMakeCache.txt \
         cmake_install.cmake || true
}

python_install_build_deps(){
  sudo apt-get update
  sudo apt-get install -y \
    devscripts debhelper dh-python python3-all python3-setuptools \
    python3-wheel python3-pip python3-pytest python3-flake8 fakeroot \
    git-buildpackage || true
  if [[ -f debian/control ]]; then
    sudo mk-build-deps -i -r -t 'apt-get -y' debian/control || true
  fi
}

python_build_with_gbp(){
  export DEB_BUILD_OPTIONS="parallel=$PARALLEL"
  gbp buildpackage \
    --git-ignore-branch \
    --git-ignore-new \
    --git-no-pristine-tar \
    --git-upstream-tree=HEAD \
    --git-builder='debuild -us -uc'
}

# 对单个包执行构建（自动分流：Python/pybuild 或 CMake〔原逻辑〕）
build_one_pkg(){
  local dir="$1"; log "==== Build: $dir ===="
  pushd "$dir" >/dev/null

  if [[ ! -d debian ]]; then
    err "missing debian/ in $dir"; popd >/dev/null; return 1
  fi

  # 强力预清理：rpm/、tar、build/、.obj-*、CMakeCache 等
  nuke_local_artifacts

  ensure_gbp_conf
  ensure_deb_source_options
  ensure_gitignore_rpm
  clean_rpm_artifacts

  # ======= 分流：Python/pybuild =======
  if is_python_pybuild; then
    log "Detected Python/pybuild packaging → use Python flow"
    ensure_git_repo

    # 生成 release 标签（与原有约定一致）
    local pkg ver inc tag
    pkg=$(pkg_name_from_changelog)
    ver=$(version_from_changelog)
    [[ -z "$ver" ]] && ver=$(version_from_package_xml)
    [[ -z "$ver" ]] && { warn "version not found; fallback 0.0.0"; ver=0.0.0; }
    inc=${DEFAULT_REL_INC}
    tag=$(render_tag "$pkg" "$ver" "$inc")
    ensure_tag "$tag"

    # Python 清理与依赖
    python_pre_clean
    python_install_build_deps

    # 构建
    python_build_with_gbp || { err "gbp build failed in $dir"; popd >/dev/null; return 1; }

    # 收集产物（上一级目录）
    mkdir -p "$DEB_OUT/$DISTRO/$pkg"
    shopt -s nullglob
    for f in ../${pkg}_* ../${pkg}-dbgsym_* ../*.build ../*.changes; do
      [[ -e "$f" ]] && cp -av "$f" "$DEB_OUT/$DISTRO/$pkg/"
    done
    shopt -u nullglob
    log "Artifacts => $DEB_OUT/$DISTRO/$pkg"

    popd >/dev/null
    return 0
  fi

  local pkg ver inc tag
  pkg=$(pkg_name_from_changelog)
  ver=$(version_from_changelog)
  [[ -z "$ver" ]] && ver=$(version_from_package_xml)
  [[ -z "$ver" ]] && { warn "version not found; fallback 0.0.0"; ver=0.0.0; }
  inc=${DEFAULT_REL_INC}

  ensure_git_repo
  tag=$(render_tag "$pkg" "$ver" "$inc")
  ensure_tag "$tag"   # 如需完全无 tag，可注释本行

  install_build_deps

  export DEB_BUILD_OPTIONS="parallel=$PARALLEL"
  # 结合方案A的安全参数：不使用 pristine-tar、不强制打 tag、锁定当前分支，并忽略未跟踪
  gbp buildpackage \
    --git-ignore-branch \
    --git-ignore-new \
    --git-no-pristine-tar \
    --git-debian-branch="$(git rev-parse --abbrev-ref HEAD)" \
    -us -uc || {
      err "gbp build failed in $dir"; popd >/dev/null; return 1; }

  # 收集产物（上一级目录）
  mkdir -p "$DEB_OUT/$DISTRO/$pkg"
  shopt -s nullglob
  for f in ../${pkg}_* ../${pkg}-dbgsym_* ../*.build ../*.changes; do
    [[ -e "$f" ]] && cp -av "$f" "$DEB_OUT/$DISTRO/$pkg/"
  done
  shopt -u nullglob
  log "Artifacts => $DEB_OUT/$DISTRO/$pkg"

  popd >/dev/null
}

find_packages(){
  find "$CODE_DIR" -type d -name debian -printf '%h\n' | sort -u
}

main(){
  mkdir -p "$DEB_OUT" || true
  if [[ "${1:-}" == "--auto" ]]; then
    local ok=0 fail=0; : >"$DEB_OUT/fail.list" || true
    while IFS= read -r p; do
      build_one_pkg "$p" && ((ok++)) || { ((fail++)); echo "$p" >>"$DEB_OUT/fail.list"; }
    done < <(find_packages)
    log "Done. success=$ok failed=$fail"
    [[ $fail -gt 0 ]] && { warn "Failed list => $DEB_OUT/fail.list"; return 1; }
  else
    # 单包：在当前目录构建
    build_one_pkg "$PWD"
  fi
}

main "$@"
