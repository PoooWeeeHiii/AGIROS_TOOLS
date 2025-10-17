#!/usr/bin/env python3
import os
import shlex
import sys
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError as exc:  # pragma: no cover - rich is required for this CLI
    raise RuntimeError("rich is required to run agiros_tools_menu.py") from exc

try:
    import questionary
except Exception:
    questionary = None


console = Console()
REPO_ROOT = Path(__file__).resolve().parent


def _fallback_select(message: str, choices: Sequence[str], multiselect: bool = False):
    if not choices:
        return [] if multiselect else None
    console.print(f"[bold cyan]{message}[/]")
    for idx, item in enumerate(choices, start=1):
        console.print(f"  {idx}. {item}")
    prompt = "选择多个请用逗号分隔: " if multiselect else "请输入编号: "
    raw = input(prompt).strip()
    if not raw:
        return [] if multiselect else None
    if multiselect:
        indexes = set()
        for token in raw.split(","):
            token = token.strip()
            if not token.isdigit():
                continue
            idx = int(token)
            if 1 <= idx <= len(choices):
                indexes.add(idx - 1)
        return [choices[i] for i in sorted(indexes)]
    if not raw.isdigit():
        return None
    idx = int(raw)
    if 1 <= idx <= len(choices):
        return choices[idx - 1]
    return None


def ask_select(message: str, choices: Sequence[str]) -> Optional[str]:
    if questionary:
        return questionary.select(message, choices=list(choices)).unsafe_ask()
    return _fallback_select(message, choices)


def ask_checkbox(message: str, choices: Sequence[str]) -> List[str]:
    if questionary:
        return questionary.checkbox(message, choices=list(choices)).unsafe_ask()
    return _fallback_select(message, choices, multiselect=True)


def ask_text(message: str, default: Optional[str] = None) -> Optional[str]:
    if questionary:
        return questionary.text(message, default=default or "").unsafe_ask()
    prompt = f"{message}"
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    raw = input(prompt).strip()
    return raw or default


def ask_confirm(message: str, default: bool = True) -> bool:
    if questionary:
        return questionary.confirm(message, default=default).unsafe_ask()
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{message} ({suffix}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def run_stream(cmd: Sequence[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> int:
    display = " ".join(shlex.quote(str(x)) for x in cmd)
    working_dir = str(cwd or Path.cwd())
    console.print(f"[bold blue]$[/] {display}\n   [dim]cwd={working_dir}[/]")
    proc = subprocess.Popen(
        list(map(str, cmd)),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        console.print(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        console.print(f"[bold red]命令退出码: {proc.returncode}[/]")
    return proc.returncode


def shlex_split(value: str) -> List[str]:
    return shlex.split(value) if value.strip() else []


@dataclass
class BuildTask:
    display_name: str
    path: Path
    kind: str  # debian | rpm
    extra_args: List[str] = field(default_factory=list)


def to_display_name(state: "MenuState", pkg_path: Path) -> str:
    try:
        return str(pkg_path.relative_to(state.code_dir))
    except ValueError:
        return str(pkg_path)


@dataclass
class MenuState:
    distribution_url: str = "http://1.94.193.239/yumrepo/agiros/agirosdep/loong/distribution.yaml"
    release_dir: Path = Path(os.environ.get("AGIROS_RELEASE_DIR", "ros2_release_dir"))
    code_dir: Path = Path(os.environ.get("AGIROS_CODE_DIR", "ros2_code_dir"))
    code_label: str = os.environ.get("AGIROS_CODE_LABEL", "code_dir")
    tracks_distro: str = os.environ.get("AGIROS_TRACKS_DISTRO", "jazzy")
    ros_distro: str = os.environ.get("AGIROS_ROS_DISTRO", "loong")
    ubuntu_version: str = os.environ.get("AGIROS_UBUNTU_DEFAULT", "jammy")
    openeuler_default: str = os.environ.get("AGIROS_OE_DEFAULT", "24")
    openeuler_fallback: List[str] = field(default_factory=lambda: [item.strip() for item in os.environ.get("AGIROS_OE_FALLBACK", "22,23").split(",") if item.strip()])
    bloom_bin: str = os.environ.get("AGIROS_BLOOM_BIN", "bloom-generate")
    auto_generate_gbp: bool = bool(int(os.environ.get("AGIROS_GENERATE_GBP", "0")))
    debian_build_args: List[str] = field(default_factory=lambda: ["--git-ignore-branch", "--git-ignore-new", "-us", "-uc"])
    rpm_build_base: str = os.environ.get("AGIROS_RPMBUILD_BIN", "rpmbuild")
    rpm_build_args: List[str] = field(default_factory=lambda: ["-ba"])
    deb_out_dir: Path = Path(os.environ.get("DEB_OUT", str(Path.home() / "deb_out")))
    deb_distro: str = os.environ.get("DISTRO", "loong")
    deb_release_inc: str = os.environ.get("DEFAULT_REL_INC", "1")
    deb_parallel: str = os.environ.get("PARALLEL", str(os.cpu_count() or 4))
    git_user_name: str = os.environ.get("GIT_USER_NAME", "PoooWeeeHiii")
    git_user_email: str = os.environ.get("GIT_USER_EMAIL", "powehi041210@gmail.com")
    build_queue: List[BuildTask] = field(default_factory=list)

    def update_env(self) -> None:
        mappings = {
            "AGIROS_RELEASE_DIR": str(self.release_dir),
            "AGIROS_RELEASE_TARGET_DIR": str(self.release_dir),
            "AGIROS_CODE_DIR": str(self.code_dir),
            "AGIROS_CODE_LABEL": self.code_label,
            "AGIROS_TRACKS_DISTRO": self.tracks_distro,
            "AGIROS_ROS_DISTRO": self.ros_distro,
            "AGIROS_DISTRO": self.ros_distro,
            "AGIROS_UBUNTU_DEFAULT": self.ubuntu_version,
            "AGIROS_OE_DEFAULT": self.openeuler_default,
            "AGIROS_OE_FALLBACK": ",".join(self.openeuler_fallback),
            "AGIROS_BLOOM_BIN": self.bloom_bin,
            "AGIROS_GENERATE_GBP": "1" if self.auto_generate_gbp else "0",
            "AGIROS_RPMBUILD_BIN": self.rpm_build_base,
            "DEB_OUT": str(self.deb_out_dir),
            "DISTRO": self.deb_distro,
            "DEFAULT_REL_INC": self.deb_release_inc,
            "PARALLEL": self.deb_parallel,
            "GIT_USER_NAME": self.git_user_name,
            "GIT_USER_EMAIL": self.git_user_email,
        }
        for key, value in mappings.items():
            os.environ[key] = value

    def summary_rows(self) -> List[Tuple[str, str]]:
        return [
            ("Release 仓库", str(self.release_dir)),
            ("源码目录", str(self.code_dir)),
            ("distribution.yaml URL", self.distribution_url),
            ("Tracks 发行版", self.tracks_distro),
            ("ROS 发行版", self.ros_distro),
            ("Ubuntu 版本", self.ubuntu_version),
            ("openEuler 默认", self.openeuler_default),
            ("openEuler 回退", ", ".join(self.openeuler_fallback) or "-"),
            ("bloom 命令", self.bloom_bin),
            ("批量生成 gbp.conf", "启用" if self.auto_generate_gbp else "关闭"),
            ("Debian 构建参数", " ".join(self.debian_build_args)),
            ("RPM 构建命令", f"{self.rpm_build_base} {' '.join(self.rpm_build_args)}".strip()),
            ("Debian 输出目录", str(self.deb_out_dir)),
            ("Debian 发行版", self.deb_distro),
            ("Debian release_inc", self.deb_release_inc),
            ("并行构建线程", self.deb_parallel),
            ("Git User", f"{self.git_user_name} <{self.git_user_email}>"),
            ("构建队列", f"{len(self.build_queue)} 项"),
        ]


def render_state_panel(state: MenuState) -> None:
    table = Table.grid(expand=False)
    table.add_column(justify="right", style="cyan", no_wrap=True)
    table.add_column(style="white", overflow="fold")
    for key, value in state.summary_rows():
        table.add_row(key, value)
    console.print(Panel(table, title="AGIROS 工具菜单", box=box.ROUNDED))


def ensure_directory(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)


def handle_download_release(state: MenuState) -> None:
    url = ask_text("distribution.yaml URL", state.distribution_url)
    if not url:
        console.print("[yellow]已取消：缺少 URL[/]")
        return
    target = ask_text("Release 仓库存放目录", str(state.release_dir))
    if not target:
        console.print("[yellow]已取消：缺少目录[/]")
        return
    target_path = Path(target).expanduser().resolve()
    ensure_directory(target_path)

    console.print(Panel(Text("开始下载 Release 仓库", style="bold"), subtitle=str(target_path), box=box.ROUNDED))
    import yaml_git_downloader_release as downloader

    downloader.TARGET_DIR = str(target_path)
    downloader.LOG_FILE = os.path.join(downloader.TARGET_DIR, "download_log.txt")
    try:
        downloader.download_repos_from_yaml(url, str(target_path))
    except Exception as exc:
        console.print(f"[bold red]下载失败: {exc}[/]")
        return

    state.distribution_url = url
    state.release_dir = target_path
    state.update_env()


def handle_tracks_download(state: MenuState) -> None:
    release_dir = ask_text("Release 仓库目录", str(state.release_dir))
    code_dir = ask_text("源码目录", str(state.code_dir))
    distro = ask_text("Tracks 发行版名称", state.tracks_distro)
    resume = ask_confirm("启用断点续传 (resume)?", default=True)
    limit_raw = ask_text("限制下载包数量 (留空则全部下载)", "")
    args = [
        f"--release-dir={Path(release_dir).expanduser().resolve()}",
        f"--code-dir={Path(code_dir).expanduser().resolve()}",
        f"--distro={distro}",
    ]
    if resume:
        args.append("--resume")
    if limit_raw:
        try:
            int(limit_raw)
        except ValueError:
            console.print("[yellow]limit 必须是数字，忽略该参数[/]")
        else:
            args.extend(["--limit", limit_raw])

    console.print(Panel(Text("处理 tracks.yaml，下载源码", style="bold magenta"), subtitle="oob_tracks_to_sources.py", box=box.ROUNDED))
    import oob_tracks_to_sources as tracks_downloader

    exit_code = tracks_downloader.main(args)
    if exit_code != 0:
        console.print(f"[bold red]处理失败，退出码 {exit_code}[/]")
        return
    state.release_dir = Path(release_dir).expanduser().resolve()
    state.code_dir = Path(code_dir).expanduser().resolve()
    state.tracks_distro = distro
    state.update_env()


def list_code_packages(code_dir: Path) -> List[Path]:
    if not code_dir.exists():
        return []

    depth_limited_packages: List[Path] = []
    for root, dirs, files in os.walk(code_dir):
        rel_parts = Path(root).relative_to(code_dir).parts
        if len(rel_parts) > 2:
            dirs[:] = []
            continue
        if "package.xml" in files:
            depth_limited_packages.append(Path(root))
            dirs[:] = []

    if depth_limited_packages:
        return sorted(set(depth_limited_packages), key=lambda p: str(p))

    return [p for p in sorted(code_dir.iterdir()) if p.is_dir()]


def prompt_package_path(state: MenuState) -> Optional[Path]:
    packages = list_code_packages(state.code_dir)
    options = []
    for p in packages:
        try:
            options.append(str(p.relative_to(state.code_dir)))
        except ValueError:
            options.append(str(p))
    choice = ask_select("选择源码包目录", options + ["手动输入", "返回"]) if options else "手动输入"
    if choice in (None, "返回"):
        return None
    if choice == "手动输入":
        custom = ask_text("请输入源码包路径", "")
        if not custom:
            return None
        return Path(custom).expanduser().resolve()
    index = options.index(choice)
    return packages[index]


def build_bloom_command(state: MenuState, kind: str) -> List[str]:
    base = shlex_split(state.bloom_bin)
    if not base:
        base = ["bloom-generate"]
    text = " ".join(base)
    if "generate_cmd" in text or text.endswith("agirosdebian") or text.endswith("agirosrpm"):
        return base
    if kind == "debian":
        return base + ["agirosdebian"]
    return base + ["agirosrpm"]


def run_single_bloom(state: MenuState, kind: str, package_path: Path, generate_gbp: bool = False) -> None:
    ensure_directory(package_path)
    cmd = build_bloom_command(state, "debian" if kind in {"debian", "gbp"} else "rpm")
    if "agirosdebian" not in cmd and kind in {"debian", "gbp"} and "generate_cmd" not in " ".join(cmd):
        cmd.append("agirosdebian")
    if kind == "rpm" and "agirosrpm" not in cmd and "generate_cmd" not in " ".join(cmd):
        cmd.append("agirosrpm")
    if kind in {"debian", "gbp"}:
        cmd += ["--ros-distro", state.ros_distro, "--os-name", "ubuntu", "--os-version", state.ubuntu_version]
        if generate_gbp:
            cmd.append("--generate-gbp")
            cmd += ["--tracks-distro", state.tracks_distro]
            cmd += ["--distro", state.ros_distro]
            cmd += ["--pkg", package_path.name]
    else:
        cmd += ["--ros-distro", state.ros_distro, "--os-name", "openeuler", "--os-version", state.openeuler_default]
    env = os.environ.copy()
    if generate_gbp:
        env["OOB_TRACKS_DIR"] = str(state.release_dir)
        env["OOB_TRACKS_DISTRO"] = state.tracks_distro
        env["AGIROS_DISTRO"] = state.ros_distro
    rc = run_stream(cmd, cwd=package_path, env=env)
    if rc == 0:
        console.print("[green]完成[/]")


def run_batch_bloom(state: MenuState, mode: str) -> None:
    script = REPO_ROOT / "agiros_oob_builder_procedural.py"
    if not script.exists():
        console.print(f"[bold red]未找到 {script}[/]")
        return
    limit_raw = ask_text("限制处理包数量 (留空=全部)", "")
    dry_run = ask_confirm("启用 dry-run?", default=False)
    cmd: List[str] = [
        sys.executable,
        str(script),
        "--release-dir",
        str(state.release_dir),
        "--code-dir",
        str(state.code_dir),
        "--ros-distro",
        state.ros_distro,
        "--ubuntu-default",
        state.ubuntu_version,
        "--openeuler-default",
        state.openeuler_default,
        "--mode",
        mode,
    ]
    if state.openeuler_fallback:
        cmd.append("--openeuler-fallback")
        cmd.extend(state.openeuler_fallback)
    cmd.extend(["--bloom-bin", state.bloom_bin])
    if limit_raw:
        try:
            int(limit_raw)
        except ValueError:
            console.print("[yellow]limit 必须是数字，忽略该参数[/]")
        else:
            cmd.extend(["--limit", limit_raw])
    if dry_run:
        cmd.append("--dry-run")
    if mode != "gbp" and state.auto_generate_gbp:
        cmd.append("--generate-gbp")
    run_stream(cmd, cwd=REPO_ROOT, env=os.environ.copy())


def bloom_menu(state: MenuState) -> None:
    while True:
        choice = ask_select("Bloom 打包", ["生成 Debian 目录", "生成 spec 文件", "生成 debian+spec", "生成 gbp.conf", "返回"])
        if choice in (None, "返回"):
            return
        scope = ask_select("请选择操作范围", ["单包", "批量", "返回"])
        if scope in (None, "返回"):
            continue
        generate_gbp = state.auto_generate_gbp or (choice in {"生成 Debian 目录", "生成 debian+spec", "生成 gbp.conf"} and ask_confirm("生成 gbp.conf?", default=choice != "生成 spec 文件"))
        if scope == "单包":
            pkg_path = prompt_package_path(state)
            if not pkg_path:
                continue
            if choice == "生成 Debian 目录":
                run_single_bloom(state, "debian", pkg_path, generate_gbp)
                if ask_confirm("将 Debian 构建加入队列?", default=False):
                    state.build_queue.append(BuildTask(to_display_name(state, pkg_path), pkg_path, "debian"))
            elif choice == "生成 spec 文件":
                run_single_bloom(state, "rpm", pkg_path)
                if ask_confirm("将 RPM 构建加入队列?", default=False):
                    state.build_queue.append(BuildTask(to_display_name(state, pkg_path), pkg_path, "rpm"))
            elif choice == "生成 debian+spec":
                run_single_bloom(state, "debian", pkg_path, generate_gbp)
                run_single_bloom(state, "rpm", pkg_path)
                if ask_confirm("将 Debian 构建加入队列?", default=False):
                    state.build_queue.append(BuildTask(to_display_name(state, pkg_path), pkg_path, "debian"))
                if ask_confirm("将 RPM 构建加入队列?", default=False):
                    state.build_queue.append(BuildTask(to_display_name(state, pkg_path), pkg_path, "rpm"))
            else:
                run_single_bloom(state, "gbp", pkg_path, True)
                if ask_confirm("将 Debian 构建加入队列?", default=False):
                    state.build_queue.append(BuildTask(to_display_name(state, pkg_path), pkg_path, "debian"))
        else:
            mode = {
                "生成 Debian 目录": "debian",
                "生成 spec 文件": "spec",
                "生成 debian+spec": "both",
                "生成 gbp.conf": "gbp",
            }[choice]
            run_batch_bloom(state, mode)


def describe_build_task(task: BuildTask, state: MenuState) -> str:
    alias = state.code_label
    pretty_path = f"{alias}/{task.display_name}"
    return f"{task.display_name} ({task.kind}) - {pretty_path}"


def run_debian_build(state: MenuState, path: Path, extra_args: Optional[List[str]] = None) -> int:
    script = REPO_ROOT / "git_build_any.sh"
    env = os.environ.copy()
    if script.exists():
        env.setdefault("WORK_DIR", str(path))
        env.setdefault("CODE_DIR", str(state.code_dir))
        env.setdefault("DEB_OUT", str(state.deb_out_dir))
        env.setdefault("DISTRO", state.deb_distro)
        env.setdefault("DEFAULT_REL_INC", state.deb_release_inc)
        env.setdefault("PARALLEL", state.deb_parallel)
        env.setdefault("GIT_USER_NAME", state.git_user_name)
        env.setdefault("GIT_USER_EMAIL", state.git_user_email)
        while True:
            rc = run_stream(["bash", str(script)], cwd=path, env=env)
            if rc == 0:
                return 0

            action = ask_select("Debian 构建失败，接下来如何操作？", ["输入命令后重试", "退出构建"])
            if action != "输入命令后重试":
                return rc

            user_cmd = ask_text("请输入需要执行的命令（将在包目录下运行）", "")
            if not user_cmd:
                console.print("[yellow]未输入命令，继续尝试构建。[/]")
                continue
            run_stream(["bash", "-lc", user_cmd], cwd=path, env=env)
        # unreachable

    cmd = ["gbp", "buildpackage"] + state.debian_build_args
    if extra_args:
        cmd += extra_args
    env = os.environ.copy()
    return run_stream(cmd, cwd=path, env=env)


def run_rpm_build(state: MenuState, path: Path, extra_args: Optional[List[str]] = None) -> int:
    script = REPO_ROOT / "rpmbuild_any.sh"
    env = os.environ.copy()
    if script.exists():
        env.setdefault("WORK_DIR", str(path))
        env.setdefault("CODE_DIR", str(state.code_dir))
        while True:
            rc = run_stream(["bash", str(script)], cwd=path, env=env)
            if rc == 0:
                return 0

            action = ask_select("RPM 构建失败，接下来如何操作？", ["输入命令后重试", "退出构建"])
            if action != "输入命令后重试":
                return rc

            user_cmd = ask_text("请输入需要执行的命令（将会在包目录下运行）", "")
            if not user_cmd:
                console.print("[yellow]未输入命令，继续尝试构建。[/]")
                continue
            run_stream(["bash", "-lc", user_cmd], cwd=path, env=env)
        # unreachable

    rpm_dir = path / "rpm"
    specs = sorted(rpm_dir.glob("*.spec")) if rpm_dir.exists() else []
    if not specs:
        console.print(f"[yellow]{path} 未找到 rpm/*.spec[/]")
        return 1
    rc = 0
    for spec in specs:
        cmd = [state.rpm_build_base] + state.rpm_build_args + [str(spec)]
        if extra_args:
            cmd += extra_args
        rc = run_stream(cmd, cwd=path, env=os.environ.copy())
        if rc != 0:
            break
    return rc


def execute_build(task: BuildTask, state: MenuState) -> bool:
    console.print(Panel(f"开始构建: {describe_build_task(task, state)}", box=box.ROUNDED))
    success = True
    if task.kind == "debian":
        if run_debian_build(state, task.path, task.extra_args) != 0:
            success = False
    elif task.kind == "rpm":
        if run_rpm_build(state, task.path, task.extra_args) != 0:
            success = False
    else:
        console.print(f"[red]未知的构建类型: {task.kind}[/]")
        success = False
    console.print("[green]构建完成[/]" if success else "[red]构建失败[/]")
    return success


def manage_build_queue(state: MenuState) -> None:
    while True:
        options = [
            "查看队列",
            "添加任务",
            "执行队列",
            "清空队列",
            "编辑构建参数",
            "返回",
        ]
        choice = ask_select("构建菜单", options)
        if choice in (None, "返回"):
            return
        if choice == "查看队列":
            if not state.build_queue:
                console.print("[cyan]队列为空[/]")
            for idx, item in enumerate(state.build_queue, start=1):
                console.print(f"{idx}. {describe_build_task(item, state)}")
            if state.build_queue and ask_confirm("移除任务?", default=False):
                idx_raw = ask_text("输入要移除的编号", "")
                if idx_raw and idx_raw.isdigit():
                    idx = int(idx_raw) - 1
                    if 0 <= idx < len(state.build_queue):
                        removed = state.build_queue.pop(idx)
                        console.print(f"[yellow]已移除 {describe_build_task(removed, state)}[/]")
        elif choice == "添加任务":
            pkg_path = prompt_package_path(state)
            if not pkg_path:
                continue
            kind = ask_select("构建类型", ["debian", "rpm"])
            if not kind:
                continue
            task = BuildTask(to_display_name(state, pkg_path), pkg_path, kind)
            state.build_queue.append(task)
        elif choice == "执行队列":
            if not state.build_queue:
                console.print("[cyan]队列为空[/]")
                continue
            failed = []
            for task in list(state.build_queue):
                if not execute_build(task, state):
                    failed.append(task)
                    if not ask_confirm("继续执行剩余任务?", default=True):
                        break
            state.build_queue = failed
            if failed:
                console.print("[yellow]以下任务未完成，已保留在队列：[/]")
                for task in failed:
                    console.print(f"- {describe_build_task(task, state)}")
            else:
                console.print("[green]队列全部完成[/]")
        elif choice == "清空队列":
            state.build_queue.clear()
            console.print("[yellow]构建队列已清空[/]")
        elif choice == "编辑构建参数":
            edit_build_parameters(state)


def edit_build_parameters(state: MenuState) -> None:
    while True:
        choice = ask_select("编辑构建参数", ["Debian 构建参数", "RPM 构建命令", "切换自动生成 gbp.conf", "返回"])
        if choice in (None, "返回"):
            return
        if choice == "Debian 构建参数":
            current = " ".join(state.debian_build_args)
            new_value = ask_text("请输入 gbp buildpackage 附加参数", current)
            state.debian_build_args = shlex_split(new_value or "")
        elif choice == "RPM 构建命令":
            base = ask_text("rpmbuild 命令 (可包含路径)", state.rpm_build_base)
            args = ask_text("rpmbuild 参数", " ".join(state.rpm_build_args))
            if base:
                state.rpm_build_base = base
            state.rpm_build_args = shlex_split(args or "")
        elif choice == "切换自动生成 gbp.conf":
            state.auto_generate_gbp = not state.auto_generate_gbp
            console.print(f"[cyan]批量操作自动生成 gbp.conf {'已启用' if state.auto_generate_gbp else '已关闭'}[/]")
        state.update_env()


def handle_clean(state: MenuState) -> None:
    script = REPO_ROOT / "clean_generated.sh"
    if not script.exists():
        console.print(f"[red]未找到 {script}[/]")
        return
    env = os.environ.copy()
    env["CODE_DIR"] = str(state.code_dir)
    rc = run_stream(["bash", str(script)], cwd=REPO_ROOT, env=env)
    if rc == 0:
        console.print("[green]清理完成[/]")


def handle_configuration(state: MenuState) -> None:
    while True:
        render_state_panel(state)
        choice = ask_select(
            "配置与状态",
            [
                "修改 Release 目录",
                "修改 源码目录",
                "修改 distribution.yaml URL",
                "修改 ROS/Tracks 配置",
                "修改 openEuler 参数",
                "修改 Bloom 命令",
                "修改 Debian 构建配置",
                "返回",
            ],
        )
        if choice in (None, "返回"):
            return
        if choice == "修改 Release 目录":
            value = ask_text("新的 Release 仓库目录", str(state.release_dir))
            if value:
                state.release_dir = Path(value).expanduser().resolve()
        elif choice == "修改 源码目录":
            value = ask_text("新的源码目录", str(state.code_dir))
            if value:
                state.code_dir = Path(value).expanduser().resolve()
        elif choice == "修改 distribution.yaml URL":
            value = ask_text("新的 URL", state.distribution_url)
            if value:
                state.distribution_url = value
        elif choice == "修改 ROS/Tracks 配置":
            ros = ask_text("ROS 发行版", state.ros_distro)
            tracks = ask_text("Tracks 发行版", state.tracks_distro)
            ubuntu = ask_text("Ubuntu 版本", state.ubuntu_version)
            state.ros_distro = ros or state.ros_distro
            state.tracks_distro = tracks or state.tracks_distro
            state.ubuntu_version = ubuntu or state.ubuntu_version
        elif choice == "修改 openEuler 参数":
            default = ask_text("openEuler 默认版本", state.openeuler_default)
            fallback = ask_text("openEuler 回退列表 (逗号分隔)", ", ".join(state.openeuler_fallback))
            if default:
                state.openeuler_default = default
            if fallback is not None:
                state.openeuler_fallback = [item.strip() for item in fallback.split(",") if item.strip()]
        elif choice == "修改 Bloom 命令":
            bloom = ask_text("bloom 可执行命令", state.bloom_bin)
            if bloom:
                state.bloom_bin = bloom
        elif choice == "修改 Debian 构建配置":
            code_label = ask_text("主界面源码前缀标签", state.code_label)
            deb_out = ask_text("Debian 输出目录", str(state.deb_out_dir))
            distro = ask_text("Debian DISTRO (gbp release_tag 用)", state.deb_distro)
            release_inc = ask_text("默认 release_inc", state.deb_release_inc)
            parallel = ask_text("并行线程数", state.deb_parallel)
            git_name = ask_text("Git 提交用户名", state.git_user_name)
            git_email = ask_text("Git 提交邮箱", state.git_user_email)
            if code_label:
                state.code_label = code_label
            if deb_out:
                state.deb_out_dir = Path(deb_out).expanduser().resolve()
            if distro:
                state.deb_distro = distro
            if release_inc:
                state.deb_release_inc = release_inc
            if parallel:
                state.deb_parallel = parallel
            if git_name:
                state.git_user_name = git_name
            if git_email:
                state.git_user_email = git_email
        state.update_env()


def gather_log_candidates(state: MenuState) -> List[Path]:
    candidates = [
        state.release_dir / "download_log.txt",
        state.release_dir / "failed_repos.txt",
        REPO_ROOT / "fail.log",
    ]
    return [path for path in candidates if path.exists()]


def handle_logs(state: MenuState) -> None:
    logs = gather_log_candidates(state)
    options = [str(p) for p in logs] + ["自定义路径", "返回"]
    choice = ask_select("查看日志", options)
    if choice in (None, "返回"):
        return
    if choice == "自定义路径":
        path_str = ask_text("输入日志文件路径", "")
    else:
        path_str = choice
    if not path_str:
        return
    path = Path(path_str).expanduser()
    if not path.exists():
        console.print(f"[red]未找到 {path}[/]")
        return
    content = path.read_text(encoding="utf-8", errors="ignore")
    console.print(Panel(Text(content if len(content) < 4000 else content[-4000:], style="white"), title=str(path), box=box.ROUNDED))


def main() -> None:
    state = MenuState()
    state.update_env()
    while True:
        render_state_panel(state)
        choice = ask_select(
            "请选择操作",
            [
                "下载 release 仓库",
                "处理 tracks.yaml / 下载源码",
                "Bloom 打包",
                "构建 (Build)",
                "清理生成目录",
                "配置与状态",
                "查看日志",
                "退出",
            ],
        )
        if choice == "下载 release 仓库":
            handle_download_release(state)
        elif choice == "处理 tracks.yaml / 下载源码":
            handle_tracks_download(state)
        elif choice == "Bloom 打包":
            bloom_menu(state)
        elif choice == "构建 (Build)":
            manage_build_queue(state)
        elif choice == "清理生成目录":
            handle_clean(state)
        elif choice == "配置与状态":
            handle_configuration(state)
        elif choice == "查看日志":
            handle_logs(state)
        elif choice == "退出" or choice is None:
            console.print("[cyan]Bye[/]")
            break


if __name__ == "__main__":
    main()
