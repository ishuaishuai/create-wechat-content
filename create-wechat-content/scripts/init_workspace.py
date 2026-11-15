#!/usr/bin/env python3
"""Initialize a portable creator workspace from a JSON configuration."""

import argparse
import json
from pathlib import Path

SECRET_KEYS = {"appsecret", "app_secret", "password", "token", "access_token", "api_key", "apikey"}
REQUIRED_TEXT = ("content_direction", "audience", "workspace")


def find_secret_keys(value, path=""):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}" if path else key
            if key.lower() in SECRET_KEYS:
                found.append(current)
            found.extend(find_secret_keys(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_secret_keys(item, f"{path}[{index}]"))
    return found


def main():
    parser = argparse.ArgumentParser(description="初始化公众号或自媒体创作工作区")
    parser.add_argument("--config", required=True, help="创作者 JSON 配置路径")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.is_file():
        raise SystemExit(f"配置文件不存在：{config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
    if not isinstance(config, dict):
        raise SystemExit("配置顶层必须是 JSON 对象")

    secret_paths = find_secret_keys(config)
    if secret_paths:
        raise SystemExit("配置不得包含凭证字段：" + "、".join(secret_paths))
    missing = [key for key in REQUIRED_TEXT if not str(config.get(key, "")).strip()]
    if missing:
        raise SystemExit("缺少必填字段：" + "、".join(missing))
    if config.get("never_publish") is not True:
        raise SystemExit("never_publish 必须为 true；发表与群发始终人工")

    root = Path(str(config["workspace"])).expanduser()
    if not root.is_absolute():
        root = config_path.parent / root
    root = root.resolve()
    if root.exists() and not root.is_dir():
        raise SystemExit(f"工作区路径不是文件夹：{root}")

    directories = {name: root / name for name in ("articles", "materials", "reviews")}
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)

    resolved = dict(config)
    resolved["workspace"] = str(root)
    resolved["allow_platform_draft"] = bool(config.get("allow_platform_draft", False))
    resolved["never_publish"] = True
    resolved["config_source"] = str(config_path)
    resolved_path = root / "creator-profile.resolved.json"
    resolved_path.write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "workspace": str(root),
        "profile": str(resolved_path),
        "directories": {name: str(path) for name, path in directories.items()},
        "platform_draft_authorized": resolved["allow_platform_draft"],
        "publish_manually": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
