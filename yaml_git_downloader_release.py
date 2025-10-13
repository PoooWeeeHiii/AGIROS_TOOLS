import os
import subprocess
import requests
import yaml
from tqdm import tqdm  


def safe_git_clone_or_resume(repo_url, repo_path):
    """
    克隆仓库，如果已存在则尝试 git fetch 断点续传。
    返回 True 表示成功，False 表示失败。
    """
    if os.path.exists(repo_path):
        if os.path.isdir(os.path.join(repo_path, ".git")):
            try:
                subprocess.run(["git", "-C", repo_path, "fetch", "--all"],
                               check=True,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                subprocess.run(["git", "-C", repo_path, "reset", "--hard", "origin/HEAD"],
                               check=True,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                return True
            except subprocess.CalledProcessError:
                return False
        else:
            return False
    else:
        # 尝试全新克隆
        try:
            subprocess.run(["git", "clone", repo_url, repo_path],
                           check=True,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            return False


def download_repos_from_yaml(yaml_url: str, target_dir: str = "ros2_release_dir"):
    os.makedirs(target_dir, exist_ok=True)

    # 下载 distribution.yaml 文件
    response = requests.get(yaml_url)
    response.raise_for_status()
    yaml_content = response.text

    # 解析 YAML
    data = yaml.safe_load(yaml_content)

    # 遍历 YAML 找到 release url
    repos = []
    if "repositories" in data:
        for repo_name, repo_info in data["repositories"].items():
            if "release" in repo_info and "url" in repo_info["release"]:
                repos.append((repo_name, repo_info["release"]["url"]))

    total = len(repos)
    print(f"[Info] Found {total} release repositories to download.\n")

    failed_repos = []

    # tqdm 进度条
    with tqdm(total=total, desc="Downloading repos", unit="repo") as pbar:
        for idx, (repo_name, repo_url) in enumerate(repos, start=1):
            repo_path = os.path.join(target_dir, repo_name)

            ok = safe_git_clone_or_resume(repo_url, repo_path)

            if ok:
                tqdm.write(f"[{idx}/{total}] [OK] {repo_name}")
            else:
                tqdm.write(f"[{idx}/{total}] [Error] {repo_name} from {repo_url}")
                failed_repos.append((repo_name, repo_url))

            pbar.update(1)

    # 保存失败列表
    if failed_repos:
        failed_file = os.path.join(target_dir, "failed_repos.txt")
        with open(failed_file, "w") as f:
            for name, url in failed_repos:
                f.write(f"{name} {url}\n")
        print(f"\n[Warning] {len(failed_repos)} repositories failed. See {failed_file}")

    print(f"\n[Done] Finished downloading {total} repositories. "
          f"Success: {total - len(failed_repos)}, Failed: {len(failed_repos)}")


if __name__ == "__main__":
    yaml_url = "http://1.94.193.239/yumrepo/agiros/agirosdep/loong/distribution.yaml"
    download_repos_from_yaml(yaml_url, "ros2_release_dir")
