"""通过 GitHub REST API 发布仓库、触发 CI、下载产物。

用途：本机 `github.com` 域名被 DNS 污染（git push 不通），但 `api.github.com`
可达。因此改走 API：把文件以 blob/tree/commit 的方式推送，再 workflow_dispatch，
最后用 artifacts API 把 zip 下载到本地。

用法：
    # 只看计划，不联网（可离线验证文件收集是否正确）
    python tools/ci_build.py --dry-run

    # 真正执行（需要 PAT：classic token 勾选 repo + workflow）
    set GH_TOKEN=ghp_xxx
    python tools/ci_build.py --repo <owner>/<name> --create --private --watch

    # 仓库已存在、只想重新触发一次
    python tools/ci_build.py --repo <owner>/<name> --watch
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

API = "https://api.github.com"
WORKFLOW_FILE = "build.yml"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "dist-ci"


class CiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 本地文件收集
# --------------------------------------------------------------------------- #
def tracked_files() -> list[Path]:
    """用 git ls-files 取纳入版本控制的文件（自动遵守 .gitignore）。"""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return [PROJECT_ROOT / name for name in names]


# --------------------------------------------------------------------------- #
# GitHub API
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ytdl-ci-runner",
            }
        )

    def call(self, method: str, path: str, **kwargs):
        url = path if path.startswith("http") else f"{API}{path}"
        # 400/429/5xx 在弱网下多半是被截断或限流，值得重试
        retryable = {400, 429, 500, 502, 503, 504}
        for attempt in range(1, 6):
            try:
                response = self.session.request(method, url, timeout=60, **kwargs)
            except requests.RequestException as exc:
                if attempt == 5:
                    raise CiError(f"{method} {url} 网络失败：{exc}") from exc
                time.sleep(2 * attempt)
                continue

            if response.status_code in (200, 201, 204):
                return response
            if response.status_code == 404:
                return response
            if response.status_code in (401, 403):
                raise CiError(
                    f"{method} {url} -> {response.status_code}：{response.text[:300]}\n"
                    "检查 token 权限（classic token 需 repo + workflow；fine-grained 需 Contents/Actions/Workflows 写权限）"
                )
            if response.status_code in retryable and attempt < 5:
                time.sleep(2 * attempt)
                continue
            raise CiError(f"{method} {url} -> {response.status_code}：{response.text[:500]}")
        raise CiError("unreachable")

    def json(self, method: str, path: str, **kwargs):
        response = self.call(method, path, **kwargs)
        if response.status_code == 404:
            return None
        return response.json()


def ensure_repo(client: Client, owner: str, name: str, *, create: bool, private: bool, whoami: str) -> None:
    existing = client.json("GET", f"/repos/{owner}/{name}")
    if existing:
        print(f"  仓库已存在：{existing['full_name']}（private={existing['private']}）")
        return
    if not create:
        raise CiError(f"仓库 {owner}/{name} 不存在；加 --create 让脚本创建（仅当 owner 是本人时可用）")
    if owner != whoami:
        raise CiError(f"只能在自己的账号下创建仓库（token 属于 {whoami}）")
    print(f"  创建仓库 {owner}/{name}（private={private}）")
    client.call("POST", "/user/repos", json={"name": name, "private": private, "auto_init": False})


def ensure_initial_commit(client: Client, owner: str, name: str) -> None:
    """空仓库没有 commit，Git Data API 会返回 409；先用 Contents API 建首个提交。

    用项目自己的 README.md 打底，避免留下多余文件（后续 tree 会覆盖成相同内容）。
    """
    contents = client.json("GET", f"/repos/{owner}/{name}/contents/")
    if contents is not None:
        return

    print("  空仓库：先用 Contents API 建立初始提交")
    readme = PROJECT_ROOT / "README.md"
    payload = {
        "message": "Initial commit",
        "content": base64.b64encode(readme.read_bytes()).decode("ascii"),
        "branch": "main",
    }
    client.call("PUT", f"/repos/{owner}/{name}/contents/README.md", json=payload)


def upload_commit(client: Client, owner: str, name: str, files: list[Path], message: str) -> str:
    """用 Git Data API 一次性提交所有文件，返回新 commit sha。"""
    print(f"  上传 {len(files)} 个文件（blob 方式）")
    tree_entries = []
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        blob = client.json("POST", f"/repos/{owner}/{name}/git/blobs",
                           json={"content": content, "encoding": "base64"})
        if not blob:
            raise CiError(f"创建 blob 失败：{relative}")
        tree_entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if index % 10 == 0 or index == len(files):
            print(f"    {index}/{len(files)}")

    base_tree = None
    ref = client.json("GET", f"/repos/{owner}/{name}/git/ref/heads/main")
    parents: list[str] = []
    if ref and ref.get("object"):
        parents = [ref["object"]["sha"]]
        parent_commit = client.json("GET", f"/repos/{owner}/{name}/git/commits/{parents[0]}")
        base_tree = parent_commit["tree"]["sha"] if parent_commit else None

    tree_payload = {"tree": tree_entries}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = client.json("POST", f"/repos/{owner}/{name}/git/trees", json=tree_payload)
    if not tree:
        raise CiError("创建 tree 失败")

    commit = client.json("POST", f"/repos/{owner}/{name}/git/commits",
                         json={"message": message, "tree": tree["sha"], "parents": parents})
    if not commit:
        raise CiError("创建 commit 失败")

    if parents:
        client.call("PATCH", f"/repos/{owner}/{name}/git/refs/heads/main",
                    json={"sha": commit["sha"], "force": True})
    else:
        client.call("POST", f"/repos/{owner}/{name}/git/refs",
                    json={"ref": "refs/heads/main", "sha": commit["sha"]})
        client.call("PATCH", f"/repos/{owner}/{name}", json={"default_branch": "main"})
    return commit["sha"]


def dispatch(client: Client, owner: str, name: str) -> None:
    print(f"  触发 workflow：{WORKFLOW_FILE}")
    client.call("POST", f"/repos/{owner}/{name}/actions/workflows/{WORKFLOW_FILE}/dispatches",
                json={"ref": "main"})


def wait_for_run(client: Client, owner: str, name: str, *, timeout: int) -> dict:
    deadline = time.time() + timeout
    run = None
    while time.time() < deadline:
        runs = client.json("GET", f"/repos/{owner}/{name}/actions/runs?per_page=10")
        if runs and runs.get("workflow_runs"):
            candidates = [r for r in runs["workflow_runs"]
                          if r.get("event") == "workflow_dispatch" and r.get("head_branch") == "main"]
            if candidates:
                run = candidates[0]
                print(f"  run #{run['run_number']} 状态={run['status']} 结论={run.get('conclusion')}")
                if run["status"] == "completed":
                    return run
        time.sleep(15)
    raise CiError("等待 CI 超时")


def download_artifacts(client: Client, owner: str, name: str, run_id: int, out_dir: Path) -> list[Path]:
    artifacts = client.json("GET", f"/repos/{owner}/{name}/actions/runs/{run_id}/artifacts")
    items = (artifacts or {}).get("artifacts", [])
    if not items:
        raise CiError("该 run 没有产物；可能是构建失败")

    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for item in items:
        target = out_dir / f"{item['name']}.zip"
        print(f"  下载 {item['name']}（{item['size_in_bytes'] / 1048576:.1f} MB）")
        response = client.call("GET", item["archive_download_url"])
        target.write_bytes(response.content)
        saved.append(target)
        extract_dir = out_dir / item["name"]
        if not extract_dir.exists():
            with zipfile.ZipFile(target) as archive:
                archive.extractall(extract_dir)
    return saved


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通过 GitHub API 发布并触发 CI")
    parser.add_argument("--repo", help="owner/name")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不联网")
    parser.add_argument("--create", action="store_true", help="仓库不存在时创建")
    parser.add_argument("--private", action="store_true", help="创建私有仓库（推荐）")
    parser.add_argument("--watch", action="store_true", help="轮询直到结束并下载产物")
    parser.add_argument("--timeout", type=int, default=2700, help="等待 CI 的秒数")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="产物下载目录")
    parser.add_argument("--message", default="Update via ci_build.py")
    args = parser.parse_args(argv)

    files = tracked_files()
    total_mb = sum(f.stat().st_size for f in files) / 1048576
    print(f"待上传文件：{len(files)} 个，合计 {total_mb:.1f} MB")

    if args.dry_run:
        for path in files[:8]:
            print(f"  {path.relative_to(PROJECT_ROOT).as_posix()}")
        print(f"  ...（共 {len(files)} 个）")
        if not args.repo:
            print("\n--dry-run 完成：文件收集正常。真正执行需要 --repo 与 GH_TOKEN。")
            return 0

    if not args.repo:
        parser.error("需要 --repo owner/name")
    owner, _, name = args.repo.partition("/")
    if not owner or not name:
        parser.error("--repo 需要形如 owner/name")

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("缺少 GH_TOKEN 环境变量（classic PAT，需 repo + workflow 权限）", file=sys.stderr)
        return 2

    client = Client(token)
    me = client.json("GET", "/user")
    if not me:
        raise CiError("token 无效")
    print(f"身份：{me['login']}")

    ensure_repo(client, owner, name, create=args.create, private=args.private, whoami=me["login"])
    ensure_initial_commit(client, owner, name)
    sha = upload_commit(client, owner, name, files, args.message)
    print(f"  已提交：{sha[:10]}")
    dispatch(client, owner, name)

    if not args.watch:
        print(f"\n已触发。查看：https://github.com/{owner}/{name}/actions")
        return 0

    run = wait_for_run(client, owner, name, timeout=args.timeout)
    print(f"  结论：{run.get('conclusion')}  ({run['html_url']})")
    if run.get("conclusion") != "success":
        print("  CI 未成功，日志：", file=sys.stderr)
        logs = client.call("GET", f"/repos/{owner}/{name}/actions/runs/{run['id']}/logs")
        log_zip = Path(args.out) / "logs.zip"
        log_zip.parent.mkdir(parents=True, exist_ok=True)
        log_zip.write_bytes(logs.content)
        print(f"  {log_zip}", file=sys.stderr)
        return 1

    saved = download_artifacts(client, owner, name, run["id"], Path(args.out))
    print("\n产物：")
    for path in saved:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CiError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
