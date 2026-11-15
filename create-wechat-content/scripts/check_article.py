#!/usr/bin/env python3
"""Check Chinese article readability signals with only the standard library."""

import argparse
import re
from pathlib import Path

BANNED = ("综上所述", "值得注意的是", "不可否认", "在当今时代", "众所周知", "本文将分析")


def main():
    parser = argparse.ArgumentParser(description="检查文章长句、模板句和普通人建议")
    parser.add_argument("article", help="Markdown 文章路径")
    parser.add_argument("--strict", action="store_true", help="发现问题时返回非零状态")
    parser.add_argument("--require-advice", action="store_true", help="要求包含普通人建议标记")
    args = parser.parse_args()

    path = Path(args.article)
    if not path.is_file():
        raise SystemExit(f"文章不存在：{path}")
    text = path.read_text(encoding="utf-8")
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    cjk = len(re.findall(r"[\u3400-\u9fff]", body))
    found = [phrase for phrase in BANNED if phrase in body]

    long_sentences = []
    for raw in re.split(r"(?<=[。！？；])|\n", body):
        item = raw.strip()
        if not item or item.startswith(("#", "![", "---")):
            continue
        item = re.sub(r"^[>\-\s]+", "", item)
        item = re.sub(r"[*`\[\]]", "", item)
        length = len(re.sub(r"\s+", "", item))
        if length > 25:
            long_sentences.append((length, item))

    has_advice = "【普通人建议】" in body or "普通人建议" in body
    print(f"file={path}")
    print(f"cjk_chars={cjk}")
    print(f"template_phrases={found}")
    print(f"sentences_over_25={len(long_sentences)}")
    for length, sentence in long_sentences:
        print(f"{length}: {sentence}")
    print(f"ordinary_advice={'OK' if has_advice else 'MISSING'}")

    problems = bool(found or long_sentences or (args.require_advice and not has_advice))
    if args.strict and problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
