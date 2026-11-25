"""标注文件校验与一致性统计（离线脚本，不依赖数据库与服务）。

用法：
    python -m backend.scripts.validate_annotations --dir data/annotations
    python -m backend.scripts.validate_annotations --dir data/annotations --json

目录约定：
    data/annotations/
        <resume_id>.txt              解析后文本（GET /resumes/{id}/extracted-text 的 text 字段）
        <resume_id>.<annotator>.jsonl  每行一条证据

校验项（任一失败即以非零码退出）：
    1. capability 必须存在于 framework_v1.json
    2. track 必须与框架定义一致（gap / translate）
    3. section 必须属于 education / experience / project / skill / other
    4. strength 必须属于 mention / project / work
    5. text[start:end] == quote 必须成立
    6. quote 必须在文本中真实出现
    7. 同一文件内 (capability, start, end) 不得重复

统计项：
    - 每份简历的标注条数、覆盖能力数
    - 多标注者之间的能力集合 Jaccard
    - 多标注者之间的 span 完全一致率
    - 能力映射的 Cohen's kappa（两人，按"每条证据是否为该能力"展开）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parents[1] / "app" / "catalog" / "framework_v1.json"
SECTIONS = {"education", "experience", "project", "skill", "other"}
STRENGTHS = {"mention", "project", "work"}


def load_framework() -> dict[str, str]:
    data = json.loads(FRAMEWORK.read_text(encoding="utf-8"))
    return {c["name"]: c.get("dual_track", "") for c in data["capabilities"]}


def load_annotations(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{lineno} JSON 解析失败: {exc}") from exc
            item["_line"] = lineno
            rows.append(item)
    return rows


def validate_resume(
    resume_id: str,
    text: str,
    per_annotator: dict[str, list[dict]],
    framework: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    for annotator, rows in per_annotator.items():
        seen: set[tuple[str, int, int]] = set()
        for row in rows:
            where = f"{resume_id}.{annotator}.jsonl:{row['_line']}"
            cap = row.get("capability")
            if cap not in framework:
                errors.append(f"{where} capability 不在框架内: {cap!r}")
            else:
                want = framework[cap]
                if row.get("track") != want:
                    errors.append(
                        f"{where} track 与框架不符: {row.get('track')!r} != {want!r}"
                    )
            if row.get("section") not in SECTIONS:
                errors.append(f"{where} section 非法: {row.get('section')!r}")
            if row.get("strength") not in STRENGTHS:
                errors.append(f"{where} strength 非法: {row.get('strength')!r}")

            quote = row.get("quote") or ""
            start, end = row.get("start"), row.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                errors.append(f"{where} start/end 必须为整数")
                continue
            if end - start != len(quote):
                errors.append(
                    f"{where} 长度不一致: end-start={end - start}, len(quote)={len(quote)}"
                )
            if not (0 <= start <= end <= len(text)):
                errors.append(f"{where} 偏移越界: [{start}, {end}) 文本长度 {len(text)}")
                continue
            if text[start:end] != quote:
                errors.append(f"{where} 偏移与引文不匹配: text[{start}:{end}] != quote")
            if quote and quote not in text:
                errors.append(f"{where} 引文在文本中找不到（可能改写过）")

            key = (str(cap), start, end)
            if key in seen:
                errors.append(f"{where} 重复条目: {key}")
            seen.add(key)
    return errors


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """二分类 Cohen's kappa。pairs 为 (标注者A, 标注者B) 的判定。"""
    n = len(pairs)
    if n == 0:
        return None
    a = sum(1 for x, _ in pairs if x)
    b = sum(1 for _, y in pairs if y)
    agree = sum(1 for x, y in pairs if x == y)
    po = agree / n
    pe = (a / n) * (b / n) + (1 - a / n) * (1 - b / n)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


def stats_resume(text: str, per_annotator: dict[str, list[dict]]) -> dict:
    out: dict = {"annotators": {}, "pairs": []}
    key_sets: dict[str, set[tuple[str, int, int]]] = {}
    cap_sets: dict[str, set[str]] = {}
    for annotator, rows in per_annotator.items():
        keys = {(r["capability"], r["start"], r["end"]) for r in rows}
        caps = {r["capability"] for r in rows}
        key_sets[annotator] = keys
        cap_sets[annotator] = caps
        out["annotators"][annotator] = {
            "evidence": len(rows),
            "capabilities": len(caps),
        }

    for a, b in combinations(sorted(per_annotator), 2):
        ka, kb = key_sets[a], key_sets[b]
        union = ka | kb
        jac_cap = len(cap_sets[a] & cap_sets[b]) / len(cap_sets[a] | cap_sets[b]) if (
            cap_sets[a] | cap_sets[b]
        ) else 1.0
        span_exact = len(ka & kb) / len(union) if union else 1.0

        # 按 (capability) 展开成二分类：该能力是否被标注者标出
        universe = sorted(cap_sets[a] | cap_sets[b])
        pairs = [(c in cap_sets[a], c in cap_sets[b]) for c in universe]
        out["pairs"].append(
            {
                "pair": f"{a}-{b}",
                "capability_jaccard": round(jac_cap, 3),
                "span_exact_match": round(span_exact, 3),
                "capability_kappa": (
                    None if cohen_kappa(pairs) is None else round(cohen_kappa(pairs), 3)
                ),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="校验标注文件并统计一致性")
    parser.add_argument("--dir", required=True, type=Path, help="标注目录")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计结果")
    args = parser.parse_args(argv)

    if not args.dir.is_dir():
        print(f"目录不存在: {args.dir}", file=sys.stderr)
        return 2

    framework = load_framework()
    texts = {p.stem: p for p in args.dir.glob("*.txt")}
    if not texts:
        print(f"{args.dir} 下没有 .txt 底本", file=sys.stderr)
        return 2

    grouped: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    for p in sorted(args.dir.glob("*.jsonl")):
        parts = p.stem.split(".")
        if len(parts) < 2:
            print(f"跳过命名不合规文件: {p.name}", file=sys.stderr)
            continue
        annotator = parts[-1]
        resume_id = ".".join(parts[:-1])
        grouped[resume_id][annotator] = load_annotations(p)

    all_errors: list[str] = []
    report: dict[str, dict] = {}
    for resume_id, per_annotator in sorted(grouped.items()):
        if resume_id not in texts:
            all_errors.append(f"{resume_id}: 缺少底本 {resume_id}.txt")
            continue
        text = texts[resume_id].read_text(encoding="utf-8")
        all_errors.extend(validate_resume(resume_id, text, per_annotator, framework))
        report[resume_id] = stats_resume(text, per_annotator)

    if args.json:
        print(json.dumps({"ok": not all_errors, "errors": all_errors, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        print(f"底本 {len(texts)} 份，标注文件 {sum(len(v) for v in grouped.values())} 个")
        print()
        for resume_id, item in report.items():
            detail = "  ".join(
                f"{a}: {v['evidence']}条/{v['capabilities']}项"
                for a, v in item["annotators"].items()
            )
            print(f"[{resume_id}] {detail}")
            for pair in item["pairs"]:
                print(
                    f"    {pair['pair']}: 能力Jaccard={pair['capability_jaccard']}"
                    f"  span完全一致={pair['span_exact_match']}"
                    f"  kappa={pair['capability_kappa']}"
                )
        print()
        if all_errors:
            print(f"发现 {len(all_errors)} 个错误：")
            for err in all_errors[:50]:
                print("  -", err)
            if len(all_errors) > 50:
                print(f"  ... 其余 {len(all_errors) - 50} 条省略")
        else:
            print("全部校验通过。")

    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
