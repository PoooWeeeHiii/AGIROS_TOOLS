#!/usr/bin/env bash
set -euo pipefail

# ========== 全局变量（可在执行前 export 覆盖） ==========
CODE_DIR=${CODE_DIR:-/opt/code_dir}
WORK_DIR=${WORK_DIR:-$PWD}
RPMS_OUT=${RPMS_OUT:-$HOME/rpms_out}
OS_NAME=${OS_NAME:-openeuler}
OS_VERSION=${OS_VERSION:-24}
ARCH=${ARCH:-$(uname -m)}
AGIROSDEP_INDEX=${AGIROSDEP_INDEX:-http://1.94.193.239/yumrepo/agiros/agirosdep/index-v4.yaml}
AGIROSDEP_WHL=${AGIROSDEP_WHL:-http://1.94.193.239/yumrepo/agiros/agirosdep/agirosdep-0.25.1-py3-none-any.whl}
YUM_BASEURL=${YUM_BASEURL:-http://1.94.193.239/yumrepo/agiros/openeuler2403lts/2506/$ARCH}
YUM_SECTION=${YUM_SECTION:-release}
BLOOM_BIN=${BLOOM_BIN:-bloom-generate}
SKIP_DEBUG=${SKIP_DEBUG:-1}
VERBOSE=${VERBOSE:-0}

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err()  { echo "[ERR ] $*" >&2; }

need() { command -v "$1" >/dev/null 2>&1 || { err "missing command: $1"; exit 127; }; }

# ========== 基础校验 ==========
need rpmbuild; need rpmdev-setuptree; need git; need python3; need dnf

# agirosdep 可选安装
python3 - <<PY || true
import sys,importlib,subprocess
try:
    importlib.import_module('agirosdep')
except Exception:
    subprocess.check_call([sys.executable,'-m','pip','install','--upgrade','pip'])
    subprocess.check_call([sys.executable,'-m','pip','install','${AGIROSDEP_WHL}'])
PY

# 准备 rpmbuild 树
rpmdev-setuptree >/dev/null 2>&1 || true

# 注入 YUM 源
bash -c 'cat > /etc/yum.repos.d/agiros.repo <<EOF
[agiros]
name=AGIROS repo
baseurl=${YUM_BASEURL}/${YUM_SECTION}/
enabled=1
gpgcheck=0
EOF'

dnf makecache -y || true

# ====== 工具函数 ======
has_spec() { ls -1 rpm/*.spec 2>/dev/null | head -n1; }
ensure_sources_dir() { mkdir -p rpm/SOURCES; }

# 从 package.xml 取版本
pkg_version() {
  local v
  v=$(sed -n 's@.*<version>\(.*\)</version>.*@\1@p' package.xml | head -n1)
  echo "$v"
}

# 生成源码归档，遵循 spec 的 Source0 模板
make_source_tar() {
  ensure_sources_dir
  local spec="$1"
  local curdir pkg version spec_name tar_name tar_path abs_tar

  curdir=$(pwd)
  pkg=$(basename "$curdir")
  version=$(pkg_version)
  if [[ -z "$version" ]]; then
    warn "version not found in package.xml, fallback 0.0.0"
    version=0.0.0
  fi

  # 读取 spec 的 Name: 作为 %{name}
  spec_name=$(awk '/^Name:[[:space:]]*/{print $2; exit}' "$spec")
  [[ -z "$spec_name" || "$spec_name" == "%{name}" ]] && spec_name="$pkg"

  # 规范 Source0 与 %setup -n，避免展开路径不一致
  if grep -qE '^Source0:' "$spec"; then
    sed -i "0,/^Source0:.*/s//Source0: %{name}-%{version}.tar.gz/" "$spec"
  else
    printf '\nSource0: %%{name}-%%{version}.tar.gz\n' >> "$spec"
  fi
  if grep -qE '^%setup' "$spec"; then
    sed -i -E '0,/^%setup/s@^(%setup[^\n]*-n)[[:space:]]+[^\n]+@\1 %{name}-%{version}@' "$spec"
  fi

  tar_name="${spec_name}-${version}.tar.gz"
  tar_path="rpm/SOURCES/${tar_name}"
  abs_tar="${curdir}/${tar_path}"

  if [[ -f "$abs_tar" ]]; then
    rm -f "$abs_tar"
  fi

  # 基本健壮性检查
  [[ -f "${curdir}/CMakeLists.txt" ]] || warn "CMakeLists.txt not found in ${curdir} (build may fail)"
  [[ -f "${curdir}/package.xml"   ]] || warn "package.xml not found in ${curdir} (build may fail)"

  log "Create source tar: $tar_path"
  local parent base
  parent=$(dirname "$curdir")
  base=$(basename "$curdir")
  tar -C "$parent" \
      --exclude="${base}/debian" \
      --exclude="${base}/rpm" \
      --exclude-vcs \
      --transform "s@^${base}@${spec_name}-${version}@" \
      -czf "$abs_tar" "$base"
  log "Tar created successfully: $tar_path"
}

# 对单个包执行构建
build_one_pkg() {
  local pkgdir="$1"
  log "==== Build: $pkgdir ===="
  cd "$pkgdir"

  # 1) 若没有 spec 用 bloom 生成
  local spec
  spec=$(has_spec || true)
  if [[ -z "$spec" ]]; then
    log "No spec found, try bloom-generate"
    export ROS_OS_OVERRIDE="${OS_NAME}:${OS_VERSION}"
    export AGIROS_ROS_DISTRO=loong
    ${BLOOM_BIN} agirosrpm --ros-distro loong --os-name "${OS_NAME}" --os-version "${OS_VERSION}" || {
      err "bloom generate failed in $pkgdir"; return 1; }
    spec=$(has_spec)
  fi

  # 2) 准备 Source0
  make_source_tar "$spec"

  # 3) 构建期依赖
  dnf builddep -y "$spec" || warn "builddep had issues; continuing"

  # 4) rpmbuild
  set -x
  local rpmb_args=(
    -ba "$spec"
    --define "_topdir   $HOME/rpmbuild"
    --define "_sourcedir $(pwd)/rpm/SOURCES"
    --define "_specdir  $(pwd)/rpm"
    --define "_builddir $HOME/rpmbuild/BUILD"
    --define "_srcrpmdir $HOME/rpmbuild/SRPMS"
    --define "_rpmdir   $HOME/rpmbuild/RPMS"
  )
  if [[ "$SKIP_DEBUG" = 1 ]]; then
    rpmb_args+=(
      --define 'debug_package %{nil}'
      --define '_enable_debug_packages 0'
      --define '_debuginfo_packages 0'
      --define '_debugsource_packages 0'
    )
  fi
  rpmbuild "${rpmb_args[@]}"
  local rc=$?
  set +x
  if [[ $rc -ne 0 ]]; then
    err "rpmbuild failed in $pkgdir"; return $rc
  fi

  # 5) 收集产物
  mkdir -p "${RPMS_OUT}/${ARCH}/$(basename "$pkgdir")" || true
  cp -av $HOME/rpmbuild/RPMS/${ARCH}/*.rpm "${RPMS_OUT}/${ARCH}/$(basename "$pkgdir")/" 2>/dev/null || true
  cp -av $HOME/rpmbuild/SRPMS/*.src.rpm "${RPMS_OUT}/${ARCH}/$(basename "$pkgdir")/" 2>/dev/null || true
  log "Artifacts => ${RPMS_OUT}/${ARCH}/$(basename "$pkgdir")"
}

# 自动发现包（包含 package.xml）
find_packages() {
  find "$CODE_DIR" -type f -name package.xml -printf '%h\n' | sort -u
}

main() {
  mkdir -p "$RPMS_OUT" || true

  if [[ "${1:-}" == "--auto" ]]; then
    log "Auto-discover packages under $CODE_DIR"
    local ok=0 fail=0; : > fail.log || true
    while IFS= read -r p; do
      build_one_pkg "$p" && ((ok++)) || { ((fail++)); echo "$p" >> fail.log; }
    done < <(find_packages)
    log "Done. success=$ok, failed=$fail"
    [[ $fail -gt 0 ]] && { warn "Failed list written to $(pwd)/fail.log"; return 1; }
  else
    # 单包：在当前目录构建
    build_one_pkg "$WORK_DIR"
  fi
}

main "$@"
