#!/usr/bin/env python3
"""Offline smoke tests for the public create-wechat-content Skill."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "create-wechat-content"
PACKAGE = ROOT / "dist/create-wechat-content.skill"


def run(args, expect=0, cwd=None):
    result = subprocess.run(args, text=True, capture_output=True, cwd=cwd)
    if result.returncode != expect:
        raise AssertionError(
            f"命令返回 {result.returncode}，预期 {expect}\n{result.stdout}\n{result.stderr}"
        )
    return result


def main():
    for relative in (
        "SKILL.md",
        "references/onboarding.md",
        "references/workflow.md",
        "references/quality.md",
        "assets/creator-profile-template.json",
        "assets/公众号创作启动清单_填写版.md",
        "scripts/init_workspace.py",
        "scripts/check_article.py",
        "scripts/html2wechat.py",
    ):
        assert (SKILL / relative).is_file(), relative

    with tempfile.TemporaryDirectory(prefix="create-wechat-content-test-") as temp:
        tmp = Path(temp)
        config = json.loads((SKILL / "assets/creator-profile-template.json").read_text(encoding="utf-8"))
        config.update({
            "content_direction": "普通人的 AI 实践",
            "audience": "希望减少重复劳动的内容创作者",
            "workspace": str(tmp / "workspace"),
            "allow_platform_draft": False,
            "never_publish": True,
        })
        config_path = tmp / "creator-profile.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        result = run([sys.executable, str(SKILL / "scripts/init_workspace.py"), "--config", str(config_path)])
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
        assert payload["publish_manually"] is True
        for name in ("articles", "materials", "reviews"):
            assert (tmp / "workspace" / name).is_dir()

        unsafe = dict(config, never_publish=False)
        unsafe_path = tmp / "unsafe.json"
        unsafe_path.write_text(json.dumps(unsafe, ensure_ascii=False), encoding="utf-8")
        run([sys.executable, str(SKILL / "scripts/init_workspace.py"), "--config", str(unsafe_path)], expect=1)

        secret = dict(config, appsecret="not-allowed")
        secret_path = tmp / "secret.json"
        secret_path.write_text(json.dumps(secret, ensure_ascii=False), encoding="utf-8")
        run([sys.executable, str(SKILL / "scripts/init_workspace.py"), "--config", str(secret_path)], expect=1)

        article = tmp / "article.md"
        article.write_text(
            "# 测试文章\n\n这是一篇用于测试的短文。\n\n## 【普通人建议】先明确验收标准\n\n先写清输入和输出。\n",
            encoding="utf-8",
        )
        run([sys.executable, str(SKILL / "scripts/check_article.py"), str(article), "--strict", "--require-advice"])

        source_html = tmp / "article.html"
        source_html.write_text(
            "<!DOCTYPE html><html><head><style>.box{display:flex;gap:12px}.label{color:#17365d}</style></head>"
            "<body><div class='box'><span class='label'>步骤一</span><span>确认目标</span></div></body></html>",
            encoding="utf-8",
        )
        converted_path = tmp / "converted.html"
        run([sys.executable, str(SKILL / "scripts/html2wechat.py"), str(source_html), str(converted_path), "--check", "--md"])
        converted = converted_path.read_text(encoding="utf-8")
        assert "确认目标" in converted
        body = converted.split('<div class="wrap" id="src">', 1)[1]
        assert "class=" not in body
        assert "display:flex" not in body.replace(" ", "")

    with zipfile.ZipFile(PACKAGE) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert "create-wechat-content/SKILL.md" in names
        assert len(names) == 9
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)

    for path in SKILL.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".py"}:
            text = path.read_text(encoding="utf-8")
            assert "/Users/" not in text, f"私人绝对路径：{path}"
            assert "WECHAT_APPSECRET" not in text, f"凭证字段：{path}"

    print("PASS: 配置初始化、安全拒绝、文章检查、HTML 转换、安装包与隐私扫描")
    print("未调用网络、微信接口或自动发表功能。")


if __name__ == "__main__":
    main()
