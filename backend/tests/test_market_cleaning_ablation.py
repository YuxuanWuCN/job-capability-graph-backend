"""Focused unit and smoke tests for cleaned market corpus ablation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("statsmodels")

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_cleaned_market_and_subset_ablation import (  # noqa: E402
    BROAD_EXTRA_CATEGORIES,
    NARROW_NON_STEM_CATEGORIES,
    CapabilityMatchers,
    JobRecord,
    classify_category,
    deduplicate_records,
    parse_education_clean,
    parse_experience_clean,
    parse_salary_clean,
    run_ols_analysis,
)


def test_salary_parsing_handles_wan_qian_annual_and_excludes_daily_hourly() -> None:
    assert parse_salary_clean("8-12K").monthly_k == pytest.approx(10.0)
    assert parse_salary_clean("1.8-2.8万").monthly_k == pytest.approx(23.0)
    assert parse_salary_clean("9千-1.1万").monthly_k == pytest.approx(10.0)
    assert parse_salary_clean("20-40K·16薪").monthly_k == pytest.approx(30.0)
    assert parse_salary_clean("6000-8000元").monthly_k == pytest.approx(7.0)
    ann = parse_salary_clean("30-42万/年")
    assert ann.category == "annual_converted"
    assert ann.monthly_k == pytest.approx(30.0)

    assert parse_salary_clean("250-500元/天").monthly_k is None
    assert parse_salary_clean("250-500元/天").category == "daily_excluded"
    assert parse_salary_clean("80-150元/时").category == "hourly_excluded"
    assert parse_salary_clean("面议").category == "negotiable"


def test_education_and_experience_parsing_avoids_silent_bachelor_or_1yr_defaults() -> None:
    assert parse_education_clean("统招本科") == ("本科", 3)
    assert parse_education_clean("硕士") == ("硕士", 4)
    assert parse_education_clean("学历不限") == ("不限/在校可投", 1)
    assert parse_education_clean("高中") == ("中专/高中及以下", 1)
    assert parse_education_clean("阿里巴巴集团") == ("缺失/噪声", None)

    assert parse_experience_clean("经验不限") == ("0年/应届/不限", 0.0)
    assert parse_experience_clean("无需经验") == ("0年/应届/不限", 0.0)
    assert parse_experience_clean("3年及以上") == ("3-5年", 3.0)
    assert parse_experience_clean("5年及以上") == ("5-10年", 5.0)
    assert parse_experience_clean("5-10年") == ("5-10年", 7.5)


def test_boundary_matcher_rejects_false_positive_short_ascii_substrings() -> None:
    caps = [
        {"name": "图文编辑", "dual_track": "gap", "meta_dimension": "hard_skill", "aliases": ["PS", "SD"]},
        {"name": "交互内容制作", "dual_track": "gap", "meta_dimension": "hard_skill", "aliases": ["Less", "HTTP"]},
        {"name": "文本挖掘", "dual_track": "gap", "meta_dimension": "hard_skill", "aliases": ["RAG", "OCR"]},
        {"name": "数据清洗", "dual_track": "gap", "meta_dimension": "hard_skill", "aliases": ["Python", "SQL"]},
        {"name": "数据可视化", "dual_track": "gap", "meta_dimension": "hard_skill", "aliases": ["PPT"]},
    ]
    matchers = CapabilityMatchers(caps)

    noisy_text = "deepseek api, android sdk, ssd storage, serverless architecture, audiocraft mppt".lower()
    # Raw substring triggers false positives for PS, SD, Less, RAG, OCR, PPT
    raw_hits = matchers.match_raw_substring(noisy_text)
    assert {"图文编辑", "交互内容制作", "文本挖掘", "数据可视化"}.issubset(raw_hits)

    # Boundary matchers reject all of those embedded non-skill tokens
    assert matchers.match_strict_boundary(noisy_text) == set()
    assert matchers.match_domain_boundary(noisy_text) == set()

    # Legitimate CJK-adjacent ASCII tokens still match
    valid_text = "熟练使用python、sql，了解mysql和https协议，能使用ps精修".lower()
    assert matchers.match_strict_boundary(valid_text) == {"数据清洗", "图文编辑"}
    assert matchers.match_domain_boundary(valid_text) == {"数据清洗", "图文编辑", "交互内容制作"}


def test_category_partition_disjoint_and_dedup_plus_ols_smoke() -> None:
    assert NARROW_NON_STEM_CATEGORIES.isdisjoint(BROAD_EXTRA_CATEGORIES)
    assert classify_category("产品经理") == "narrow_non_stem"
    assert classify_category("数据治理") == "broad_extra"
    assert classify_category("大模型算法") == "stem_tech"

    # Build 36 minimal records to smoke-test dedup + OLS pipeline
    recs: list[JobRecord] = []
    for i in range(36):
        sal_str = f"{8 + (i % 10)}-{12 + (i % 10)}K"
        edu_str = "硕士" if i % 4 == 0 else ("本科" if i % 4 != 3 else "大专")
        exp_str = f"{(i % 5) + 1}年及以上"
        recs.append(
            JobRecord(
                platform="boss",
                category="产品经理" if i % 2 == 0 else "Python",
                split_group=classify_category("产品经理" if i % 2 == 0 else "Python"),
                job_name=f"岗位{i if i != 35 else 0}",
                company_name=f"企业{i if i != 35 else 0}",
                city="广州",
                education_raw=edu_str,
                work_year_raw=exp_str,
                salary_raw=sal_str,
                job_url=f"https://example.com/{i}",  # distinct URL, entity #35 duplicates #0
                text_corpus="团队协作 python sql" if i % 2 == 0 else "独立交付 python",
                edu_label=parse_education_clean(edu_str)[0],
                edu_rank=parse_education_clean(edu_str)[1],
                exp_bucket=parse_experience_clean(exp_str)[0],
                exp_years=parse_experience_clean(exp_str)[1],
                salary_parsed=parse_salary_clean(sal_str),
            )
        )

    url_dedup, strict_dedup, meta = deduplicate_records(recs)
    assert meta["raw_count"] == 36
    assert meta["url_dedup_count"] == 36
    assert meta["strict_dedup_count"] == 35
    assert meta["cross_split_collisions"] == 1

    skill_sets = [
        ({"团队协作", "独立交付", "数据清洗"} if i % 3 == 0 else ({"团队协作"} if i % 2 == 0 else {"数据清洗"}))
        for i in range(len(strict_dedup))
    ]
    ols = run_ols_analysis(strict_dedup, skill_sets, {"数据清洗"}, {"团队协作", "独立交付"}, "smoke")
    assert ols["valid"] is True
    assert ols["n"] == 35
    assert "soft" in ols["coefs"]
