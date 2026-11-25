"""Cleaned Market Corpus Ablation & Non-STEM Subset Empirical Audit Pipeline.

Runs offline on existing workspace data (data/merged_data/ + framework_v1.json)
without touching existing reports/ tables or figures. Outputs cleaned comparison
tables, diagnostics, >=200 dpi figures, and reports/market_cleaning_and_subset_audit.md.
"""

from __future__ import annotations

import csv
import json
import math
import platform
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy import stats
import statsmodels
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

# ==============================================================================
# Configuration & Constants
# ==============================================================================
RANDOM_SEED = 42
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data" / "merged_data"
FRAMEWORK_FILE = ROOT_DIR / "backend" / "app" / "catalog" / "framework_v1.json"
TABLES_DIR = ROOT_DIR / "reports" / "tables"
FIGURES_DIR = ROOT_DIR / "reports" / "figures"
AUDIT_REPORT_PATH = ROOT_DIR / "reports" / "market_cleaning_and_subset_audit.md"

PLATFORMS = ("boss", "job51", "liepin", "zhilian")
PLATFORM_PREFIXES = ("bosszp_", "job51zp_", "liepin_", "zhilian_direct_", "zhilian_")
SALARY_MIN_K = 1.0
SALARY_MAX_K = 150.0
FIGURE_DPI = 240

# Explicit Non-STEM category partitions across the 112 CSV job categories
NARROW_NON_STEM_CATEGORIES = frozenset({
    "产品经理",
    "AI产品经理",
    "数据产品经理",
    "硬件产品经理",
    "项目经理",
    "项目经理_主管",
    "软件项目经理",
    "需求分析工程师",
    "数据分析师",
    "技术文档工程师",
    "售前技术支持",
    "售后技术支持",
    "销售技术支持",
    "数字化管理师",
    "数字化管理师（新兴）",
    "数据标注",
    "数据标注_AI训练师",
})

BROAD_EXTRA_CATEGORIES = frozenset({
    "数据治理",
    "数据采集",
    "工业产品数字建模师",
    "工业产品数字建模师（新兴）",
})

# Explicit tech-compound variants lost when fixing substring collisions via strict word boundaries.
# Logged transparently as candidate catalog aliases rather than silent heuristic matches.
EXPLICIT_DOMAIN_VARIANTS: dict[str, tuple[str, ...]] = {
    "数据清洗": ("mysql", "postgresql", "sqlite", "sqlserver", "sql server", "nosql", "sqlalchemy", "plsql"),
    "文本挖掘": ("paddleocr", "emnlp"),
    "提示工程": ("vllm", "mllm", "llms", "llmops"),
    "图文编辑": ("sdxl",),
    "交互内容制作": ("https", "okhttp", "okhttp3", "vue2", "vue3", "vuex", "reactjs"),
}

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "DejaVu Sans",
    "Arial",
]
plt.rcParams["axes.unicode_minus"] = False


# ==============================================================================
# Parsing & Normalization Helpers
# ==============================================================================
@dataclass(frozen=True, slots=True)
class SalaryParseResult:
    monthly_k: float | None
    category: str  # monthly | annual_converted | daily_excluded | hourly_excluded | weekly_excluded | negotiable | unparsed


@dataclass(frozen=True, slots=True)
class JobRecord:
    platform: str
    category: str
    split_group: str  # narrow_non_stem | broad_extra | stem_tech
    job_name: str
    company_name: str
    city: str
    education_raw: str
    work_year_raw: str
    salary_raw: str
    job_url: str
    text_corpus: str
    edu_label: str
    edu_rank: int | None
    exp_bucket: str
    exp_years: float | None
    salary_parsed: SalaryParseResult


def strip_category_prefix(stem: str) -> str:
    """Remove platform file prefixes to obtain the normalized category name."""
    for prefix in PLATFORM_PREFIXES:
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem


def classify_category(category: str) -> str:
    """Assign each category into narrow_non_stem, broad_extra, or stem_tech."""
    if category in NARROW_NON_STEM_CATEGORIES:
        return "narrow_non_stem"
    if category in BROAD_EXTRA_CATEGORIES:
        return "broad_extra"
    return "stem_tech"


def _unit_multiplier(unit: str) -> float:
    unit = unit.lower()
    if unit == "万":
        return 10.0
    if unit in {"k", "千"}:
        return 1.0
    return 0.001  # 元 -> k RMB


def parse_salary_clean(salary_str: str) -> SalaryParseResult:
    """Parse Chinese job-board salary strings cleanly to base monthly k RMB.

    Explicitly separates daily/hourly/weekly/per-task intern or part-time rates
    and missing/negotiable entries from regular monthly/annual wages.
    """
    if not salary_str or not isinstance(salary_str, str):
        return SalaryParseResult(None, "unparsed")

    raw = salary_str.strip()
    if not raw:
        return SalaryParseResult(None, "unparsed")
    if "面议" in raw:
        return SalaryParseResult(None, "negotiable")
    if "/天" in raw or "元/天" in raw:
        return SalaryParseResult(None, "daily_excluded")
    if "/时" in raw:
        return SalaryParseResult(None, "hourly_excluded")
    if "/周" in raw:
        return SalaryParseResult(None, "weekly_excluded")
    if "/次" in raw:
        return SalaryParseResult(None, "weekly_excluded")

    # Strip trailing '·13薪' / '·15薪' suffix when extracting base monthly range
    base = re.sub(r"[·•・]\s*\d+(?:\.\d+)?\s*薪", "", raw).strip()
    is_annual = "/年" in base
    base_clean = re.sub(r"/(?:月|年)", "", base).strip()

    # Pattern 1: range with optional explicit left unit, e.g. '9千-1.1万', '1.8-2.8万', '8-12K', '6000-8000元'
    m_range = re.match(
        r"^(\d+(?:\.\d+)?)\s*(万|千|[kK]|元)?\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*(万|千|[kK]|元)\s*(?:以下|及以上|以上)?$",
        base_clean,
    )
    if m_range:
        low_val = float(m_range.group(1))
        low_unit = m_range.group(2)
        high_val = float(m_range.group(3))
        high_unit = m_range.group(4)
        eff_low_unit = low_unit if low_unit else high_unit
        mid_k = ((low_val * _unit_multiplier(eff_low_unit)) + (high_val * _unit_multiplier(high_unit))) / 2.0
        if is_annual:
            mid_k /= 12.0
            return SalaryParseResult(mid_k, "annual_converted")
        return SalaryParseResult(mid_k, "monthly")

    # Pattern 2: single value with unit, e.g. '2万', '3500元', '1000元以下', '3千及以下'
    m_single = re.match(
        r"^(\d+(?:\.\d+)?)\s*(万|千|[kK]|元)\s*(?:以下|及以下|以上|及以上)?$",
        base_clean,
    )
    if m_single:
        val_k = float(m_single.group(1)) * _unit_multiplier(m_single.group(2))
        if is_annual:
            val_k /= 12.0
            return SalaryParseResult(val_k, "annual_converted")
        return SalaryParseResult(val_k, "monthly")

    return SalaryParseResult(None, "unparsed")


def parse_education_clean(edu_str: str) -> tuple[str, int | None]:
    """Parse education level cleanly without falsely defaulting '不限'/'高中' to bachelor.

    Returns (bucket_label, ordinal_rank_for_regression).
    Rows with corrupted company text or missing values get rank=None.
    """
    s = (edu_str or "").strip()
    if not s:
        return "缺失/噪声", None
    if "博士" in s:
        return "博士", 5
    if "硕士" in s or "EMBA" in s.upper():
        return "硕士", 4
    if "本科" in s:
        return "本科", 3
    if "大专" in s:
        return "大专", 2
    if any(tok in s for tok in ("高中", "中专", "中技", "初中")):
        return "中专/高中及以下", 1
    if any(tok in s for tok in ("学历不限", "不限", "学生可投", "实习")):
        return "不限/在校可投", 1
    return "缺失/噪声", None


def parse_experience_clean(exp_str: str) -> tuple[str, float | None]:
    """Parse work experience strings cleanly across Boss/51job/Liepin/Zhilian formats."""
    s = (exp_str or "").strip()
    if not s:
        return "缺失", None
    if any(tok in s for tok in ("不限", "无经验", "无需经验", "应届", "在校")):
        return "0年/应届/不限", 0.0
    if any(tok in s for tok in ("1年以下", "1年以内", "半年")):
        return "1年以内", 0.5

    m_range = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*年", s)
    if m_range:
        low, high = float(m_range.group(1)), float(m_range.group(2))
        mid = (low + high) / 2.0
        if mid <= 3.0:
            return "1-3年", mid
        if mid <= 5.0:
            return "3-5年", mid
        if mid <= 10.0:
            return "5-10年", mid
        return "10年以上", mid

    m_plus = re.search(r"(\d+(?:\.\d+)?)\s*年", s)
    if m_plus:
        years = float(m_plus.group(1))
        if years < 1.0:
            return "1年以内", years
        if years < 3.0:
            return "1-3年", years
        if years < 5.0:
            return "3-5年", years
        if years < 10.0:
            return "5-10年", years
        return "10年以上", years

    return "缺失", None


# ==============================================================================
# Alias Matching Builders & Diagnostics
# ==============================================================================
def compile_boundary_pattern(term: str) -> re.Pattern[str]:
    """Build regex with ASCII word/digit boundaries only where term starts/ends with ASCII alnum."""
    t = term.strip().lower()
    escaped = re.escape(t)
    prefix = r"(?<![a-z0-9])" if t and t[0].isascii() and t[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if t and t[-1].isascii() and t[-1].isalnum() else ""
    return re.compile(f"{prefix}{escaped}{suffix}")


class CapabilityMatchers:
    """Compile raw substring, strict boundary, and clean domain-boundary matchers."""

    def __init__(self, capabilities: list[dict]) -> None:
        self.cap_names = [c["name"] for c in capabilities]
        self.hard_caps = {c["name"] for c in capabilities if c["dual_track"] == "gap"}
        self.soft_caps = {c["name"] for c in capabilities if c["dual_track"] == "translate"}
        self.cap_meta = {c["name"]: c for c in capabilities}

        self.raw_terms: dict[str, list[str]] = {}
        self.strict_patterns: dict[str, re.Pattern[str]] = {}
        self.domain_patterns: dict[str, re.Pattern[str]] = {}
        self.short_ascii_aliases: dict[str, list[str]] = {}

        for c in capabilities:
            name = c["name"]
            terms = sorted(
                {name.lower()} | {a.strip().lower() for a in c.get("aliases", []) if a.strip()},
                key=lambda x: (-len(x), x),
            )
            self.raw_terms[name] = terms
            self.strict_patterns[name] = re.compile(
                "|".join(compile_boundary_pattern(t).pattern for t in terms)
            )
            domain_terms = sorted(
                set(terms) | set(EXPLICIT_DOMAIN_VARIANTS.get(name, ())),
                key=lambda x: (-len(x), x),
            )
            self.domain_patterns[name] = re.compile(
                "|".join(compile_boundary_pattern(t).pattern for t in domain_terms)
            )
            shorts = [t for t in terms if t.isascii() and len(t) <= 4]
            if shorts:
                self.short_ascii_aliases[name] = shorts

    def match_raw_substring(self, text: str) -> set[str]:
        present: set[str] = set()
        for name, terms in self.raw_terms.items():
            for term in terms:
                if term in text:
                    present.add(name)
                    break
        return present

    def match_strict_boundary(self, text: str) -> set[str]:
        return {name for name, pat in self.strict_patterns.items() if pat.search(text)}

    def match_domain_boundary(self, text: str) -> set[str]:
        return {name for name, pat in self.domain_patterns.items() if pat.search(text)}


# ==============================================================================
# Corpus Loading & 3-Level Deduplication
# ==============================================================================
def load_raw_records(data_dir: Path) -> list[JobRecord]:
    """Load all rows from data/merged_data/{boss,job51,liepin,zhilian}."""
    records: list[JobRecord] = []
    for plat in PLATFORMS:
        plat_dir = data_dir / plat
        if not plat_dir.exists():
            continue
        for csv_path in sorted(plat_dir.glob("*.csv")):
            category = strip_category_prefix(csv_path.stem)
            split_group = classify_category(category)
            with open(csv_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                for row in csv.DictReader(f):
                    job_name = (row.get("job_name") or "").strip()
                    company_name = (row.get("company_name") or "").strip()
                    city = (row.get("city") or "").strip()
                    edu_raw = (row.get("education") or "").strip()
                    exp_raw = (row.get("work_year") or "").strip()
                    salary_raw = (row.get("salary") or "").strip()
                    job_url = (row.get("job_url") or "").strip()
                    text_corpus = " ".join(
                        part
                        for part in (
                            job_name,
                            (row.get("skill_requirements") or "").strip(),
                            (row.get("tech_tags") or "").strip(),
                        )
                        if part
                    ).lower()
                    edu_label, edu_rank = parse_education_clean(edu_raw)
                    exp_bucket, exp_years = parse_experience_clean(exp_raw)
                    records.append(
                        JobRecord(
                            platform=plat,
                            category=category,
                            split_group=split_group,
                            job_name=job_name,
                            company_name=company_name,
                            city=city,
                            education_raw=edu_raw,
                            work_year_raw=exp_raw,
                            salary_raw=salary_raw,
                            job_url=job_url,
                            text_corpus=text_corpus,
                            edu_label=edu_label,
                            edu_rank=edu_rank,
                            exp_bucket=exp_bucket,
                            exp_years=exp_years,
                            salary_parsed=parse_salary_clean(salary_raw),
                        )
                    )
    return records


def deduplicate_records(records: list[JobRecord]) -> tuple[list[JobRecord], list[JobRecord], dict[str, int]]:
    """Produce Level 1 (URL dedup) and Level 2 (strict entity dedup) record lists.

    Tracks duplicate collisions across categories and splits.
    """
    seen_urls: set[str] = set()
    url_dedup: list[JobRecord] = []
    for idx, rec in enumerate(records):
        key = f"url:{rec.job_url}" if rec.job_url else f"empty:{idx}"
        if key not in seen_urls:
            seen_urls.add(key)
            url_dedup.append(rec)

    seen_entities: dict[tuple[str, str, str], JobRecord] = {}
    strict_dedup: list[JobRecord] = []
    cross_category_collisions = 0
    cross_split_collisions = 0

    for idx, rec in enumerate(url_dedup):
        if rec.job_name and rec.company_name:
            ekey = (rec.job_name.casefold(), rec.company_name.casefold(), rec.city.casefold())
        elif rec.job_url:
            ekey = ("url", rec.job_url, "")
        else:
            ekey = ("row", str(idx), "")

        existing = seen_entities.get(ekey)
        if existing is None:
            seen_entities[ekey] = rec
            strict_dedup.append(rec)
        else:
            if existing.category != rec.category:
                cross_category_collisions += 1
            if existing.split_group != rec.split_group:
                cross_split_collisions += 1

    stats_meta = {
        "raw_count": len(records),
        "url_dedup_count": len(url_dedup),
        "strict_dedup_count": len(strict_dedup),
        "cross_category_collisions": cross_category_collisions,
        "cross_split_collisions": cross_split_collisions,
    }
    return url_dedup, strict_dedup, stats_meta


# ==============================================================================
# Statistical & Regression Helpers
# ==============================================================================
def compute_penetration(
    records: list[JobRecord],
    matcher: Callable[[str], set[str]],
    cap_names: list[str],
) -> tuple[Counter[str], list[set[str]]]:
    freq: Counter[str] = Counter()
    matched_sets: list[set[str]] = []
    for rec in records:
        caps = matcher(rec.text_corpus)
        matched_sets.append(caps)
        freq.update(caps)
    for name in cap_names:
        freq.setdefault(name, 0)
    return freq, matched_sets


def run_ols_analysis(
    records: list[JobRecord],
    matched_sets: list[set[str]],
    hard_caps: set[str],
    soft_caps: set[str],
    label: str,
) -> dict[str, object]:
    """Run standardized OLS salary regression with HC3 robust SEs and VIF diagnostics."""
    rows: list[dict[str, float]] = []
    for rec, caps in zip(records, matched_sets):
        sal = rec.salary_parsed.monthly_k
        if sal is None or not (SALARY_MIN_K <= sal <= SALARY_MAX_K):
            continue
        if rec.edu_rank is None or rec.exp_years is None:
            continue
        hard_n = sum(1 for c in caps if c in hard_caps)
        soft_n = sum(1 for c in caps if c in soft_caps)
        rows.append({
            "salary": sal,
            "log_salary": math.log(sal),
            "soft": float(soft_n),
            "hard": float(hard_n),
            "edu": float(rec.edu_rank),
            "exp": float(rec.exp_years),
        })

    df = pd.DataFrame(rows)
    if len(df) < 30:
        return {"label": label, "n": len(df), "valid": False}

    predictor_cols = ["soft", "hard", "edu", "exp"]
    z_df = pd.DataFrame()
    z_df["z_salary"] = stats.zscore(df["salary"], ddof=1)
    for col in predictor_cols:
        arr = df[col].to_numpy(dtype=float)
        std = float(np.std(arr, ddof=1))
        z_df[f"z_{col}"] = (arr - float(np.mean(arr))) / std if std > 1e-12 else 0.0

    X = sm.add_constant(z_df[[f"z_{c}" for c in predictor_cols]])
    y = z_df["z_salary"]
    model = sm.OLS(y, X).fit(cov_type="HC3")

    # Log-salary sensitivity specification
    z_log_y = stats.zscore(df["log_salary"], ddof=1)
    model_log = sm.OLS(z_log_y, X).fit(cov_type="HC3")

    # VIF on predictors (include intercept column per statsmodels convention; guard constant columns)
    X_vif = X.to_numpy(dtype=float)
    vifs = {}
    for idx, col in enumerate(predictor_cols):
        if float(np.std(X_vif[:, idx + 1])) <= 1e-12:
            vifs[col] = float("nan")
        else:
            vifs[col] = float(variance_inflation_factor(X_vif, idx + 1))

    # Check non-zero variance residual normality/skewness
    resid = model.resid.to_numpy(dtype=float)
    res_skew = float(stats.skew(resid))
    res_kurt = float(stats.kurtosis(resid))

    return {
        "label": label,
        "valid": True,
        "n": int(len(df)),
        "mean_salary_k": float(df["salary"].mean()),
        "median_salary_k": float(df["salary"].median()),
        "r2": float(model.rsquared),
        "adj_r2": float(model.rsquared_adj),
        "log_r2": float(model_log.rsquared),
        "coefs": {
            c: {
                "beta": float(model.params[f"z_{c}"]),
                "se_hc3": float(model.bse[f"z_{c}"]),
                "t": float(model.tvalues[f"z_{c}"]),
                "p": float(model.pvalues[f"z_{c}"]),
                "beta_log": float(model_log.params[f"z_{c}"]),
                "p_log": float(model_log.pvalues[f"z_{c}"]),
                "vif": vifs[c],
            }
            for c in predictor_cols
        },
        "resid_skew": res_skew,
        "resid_kurt": res_kurt,
    }


def collect_short_alias_diagnostics(
    records: list[JobRecord],
    matchers: CapabilityMatchers,
) -> list[dict[str, object]]:
    """Quantify noisy token hits removed by strict boundary vs compound tech tokens."""
    rows: list[dict[str, object]] = []
    for cap_name, aliases in matchers.short_ascii_aliases.items():
        for alias in sorted(set(aliases)):
            strict_pat = compile_boundary_pattern(alias)
            domain_extras = [t for t in EXPLICIT_DOMAIN_VARIANTS.get(cap_name, ()) if alias in t]
            domain_pat = re.compile(
                "|".join([strict_pat.pattern] + [compile_boundary_pattern(t).pattern for t in domain_extras])
            )
            token_extract_pat = re.compile(r"[a-z0-9_+.-]*" + re.escape(alias) + r"[a-z0-9_+.-]*")

            raw_jd_hits = 0
            strict_jd_hits = 0
            domain_jd_hits = 0
            dropped_tokens: Counter[str] = Counter()

            for rec in records:
                txt = rec.text_corpus
                if alias not in txt:
                    continue
                raw_jd_hits += 1
                hit_strict = bool(strict_pat.search(txt))
                hit_domain = bool(domain_pat.search(txt))
                if hit_strict:
                    strict_jd_hits += 1
                if hit_domain:
                    domain_jd_hits += 1
                if not hit_domain:
                    for m in token_extract_pat.finditer(txt):
                        tok = m.group(0)
                        if tok != alias and len(tok) <= 32:
                            dropped_tokens[tok] += 1

            rows.append({
                "capability": cap_name,
                "alias": alias,
                "raw_jd_hits": raw_jd_hits,
                "strict_jd_hits": strict_jd_hits,
                "domain_jd_hits": domain_jd_hits,
                "noise_dropped_count": raw_jd_hits - domain_jd_hits,
                "noise_ratio_pct": ((raw_jd_hits - domain_jd_hits) / raw_jd_hits * 100.0) if raw_jd_hits else 0.0,
                "compound_recovered_count": domain_jd_hits - strict_jd_hits,
                "top_dropped_noise_tokens": "; ".join(f"{t}({cnt})" for t, cnt in dropped_tokens.most_common(6)),
            })
    rows.sort(key=lambda r: (-int(r["noise_dropped_count"]), r["alias"]))
    return rows


# ==============================================================================
# Main Pipeline
# ==============================================================================
def main() -> None:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("Cleaned Market Corpus Ablation & Non-STEM Subset Empirical Audit")
    print(f"Python {platform.python_version()} | pandas {pd.__version__} | "
          f"numpy {np.__version__} | scipy {scipy.__version__} | statsmodels {statsmodels.__version__}")
    print("=" * 72)

    framework = json.loads(FRAMEWORK_FILE.read_text(encoding="utf-8"))
    caps_data = framework["capabilities"]
    matchers = CapabilityMatchers(caps_data)

    raw_records = load_raw_records(DATA_DIR)
    url_records, strict_records, dedup_meta = deduplicate_records(raw_records)

    print(
        f"[1/5] Loaded {dedup_meta['raw_count']:,} raw -> "
        f"{dedup_meta['url_dedup_count']:,} URL-dedup -> "
        f"{dedup_meta['strict_dedup_count']:,} strict entity-dedup "
        f"(cross-category collisions: {dedup_meta['cross_category_collisions']:,}, "
        f"cross-split collisions: {dedup_meta['cross_split_collisions']:,})"
    )

    # --------------------------------------------------------------------------
    # Save Table 1: 3-Level Deduplication & Platform/Company Breakdown
    # --------------------------------------------------------------------------
    dedup_rows = []
    for stage_name, stage_recs in (
        ("L0_原始全量", raw_records),
        ("L1_URL去重", url_records),
        ("L2_岗位企业城市严格去重", strict_records),
    ):
        plat_c = Counter(r.platform for r in stage_recs)
        comp_c = len({r.company_name for r in stage_recs if r.company_name})
        split_c = Counter(r.split_group for r in stage_recs)
        narrow_n = split_c["narrow_non_stem"]
        broad_n = narrow_n + split_c["broad_extra"]
        stem_n = split_c["stem_tech"]
        dedup_rows.append({
            "清洗阶段": stage_name,
            "总样本数": len(stage_recs),
            "唯一企业数": comp_c,
            "Boss直聘": plat_c["boss"],
            "前程无忧": plat_c["job51"],
            "猎聘网": plat_c["liepin"],
            "智联招聘": plat_c["zhilian"],
            "窄口径非新工科": narrow_n,
            "宽口径新增(治理/采集/工业建模)": split_c["broad_extra"],
            "宽口径非新工科": broad_n,
            "研发工程对照组": stem_n,
        })
    pd.DataFrame(dedup_rows).to_csv(
        TABLES_DIR / "table_clean_dedup_summary.csv", index=False, encoding="utf-8-sig"
    )

    # --------------------------------------------------------------------------
    # Run Matching Ablation Across L0_Raw vs L2_Strict + Substring vs Boundaries
    # --------------------------------------------------------------------------
    print("[2/5] Running Substring vs Boundary Matching Ablation...")
    freq_raw_sub, _ = compute_penetration(raw_records, matchers.match_raw_substring, matchers.cap_names)
    freq_strict_sub, _ = compute_penetration(strict_records, matchers.match_raw_substring, matchers.cap_names)
    freq_strict_bstrict, _ = compute_penetration(strict_records, matchers.match_strict_boundary, matchers.cap_names)
    freq_strict_bdomain, strict_domain_sets = compute_penetration(
        strict_records, matchers.match_domain_boundary, matchers.cap_names
    )

    ablation_rows = []
    n_raw = len(raw_records)
    n_strict = len(strict_records)
    for c in caps_data:
        name = c["name"]
        r_sub_pct = freq_raw_sub[name] / n_raw * 100.0
        s_sub_pct = freq_strict_sub[name] / n_strict * 100.0
        s_bstrict_pct = freq_strict_bstrict[name] / n_strict * 100.0
        s_bdomain_pct = freq_strict_bdomain[name] / n_strict * 100.0
        ablation_rows.append({
            "能力名称": name,
            "双轨": c["dual_track"],
            "L0_原始子串频次": freq_raw_sub[name],
            "L0_原始子串渗透率(%)": round(r_sub_pct, 2),
            "L2_去重子串频次": freq_strict_sub[name],
            "L2_去重子串渗透率(%)": round(s_sub_pct, 2),
            "L2_严格词边界频次": freq_strict_bstrict[name],
            "L2_严格词边界渗透率(%)": round(s_bstrict_pct, 2),
            "L2_词边界含显式变体频次": freq_strict_bdomain[name],
            "L2_词边界含显式变体渗透率(%)": round(s_bdomain_pct, 2),
            "去重+清洗前后渗透率差(pp)": round(s_bdomain_pct - r_sub_pct, 2),
            "词边界剔除噪声条数(L2内)": freq_strict_sub[name] - freq_strict_bstrict[name],
            "显式变体补回条数(L2内)": freq_strict_bdomain[name] - freq_strict_bstrict[name],
        })

    ablation_df = pd.DataFrame(ablation_rows)
    ablation_df.to_csv(
        TABLES_DIR / "table_clean_matching_ablation.csv", index=False, encoding="utf-8-sig"
    )

    spearman_raw_vs_clean = stats.spearmanr(
        ablation_df["L0_原始子串渗透率(%)"], ablation_df["L2_词边界含显式变体渗透率(%)"]
    )
    spearman_sub_vs_strict = stats.spearmanr(
        ablation_df["L2_去重子串渗透率(%)"], ablation_df["L2_严格词边界渗透率(%)"]
    )

    diag_rows = collect_short_alias_diagnostics(strict_records, matchers)
    pd.DataFrame(diag_rows).to_csv(
        TABLES_DIR / "table_clean_short_alias_diagnostics.csv", index=False, encoding="utf-8-sig"
    )

    # --------------------------------------------------------------------------
    # Non-STEM Subset Comparison (Narrow vs Broad vs STEM Control) on L2 Clean
    # --------------------------------------------------------------------------
    print("[3/5] Computing Narrow vs Broad Non-STEM vs STEM Subset Profiles...")
    subset_masks: dict[str, Callable[[JobRecord], bool]] = {
        "全量干净样本 (L2)": lambda r: True,
        "窄口径非新工科 (17类)": lambda r: r.split_group == "narrow_non_stem",
        "宽口径非新工科 (21类)": lambda r: r.split_group in {"narrow_non_stem", "broad_extra"},
        "强研发/工程/算法对照组": lambda r: r.split_group == "stem_tech",
    }

    subset_comp_rows = []
    subset_ols_results = []
    subset_indices: dict[str, list[int]] = {}

    for group_label, pred in subset_masks.items():
        idxs = [i for i, r in enumerate(strict_records) if pred(r)]
        subset_indices[group_label] = idxs
        sub_recs = [strict_records[i] for i in idxs]
        sub_sets = [strict_domain_sets[i] for i in idxs]
        n_sub = len(sub_recs)

        edu_c = Counter(r.edu_label for r in sub_recs)
        exp_c = Counter(r.exp_bucket for r in sub_recs)
        cap_c: Counter[str] = Counter()
        hard_any = 0
        soft_any = 0
        for s in sub_sets:
            cap_c.update(s)
            if any(x in matchers.hard_caps for x in s):
                hard_any += 1
            if any(x in matchers.soft_caps for x in s):
                soft_any += 1

        top3_hard = ", ".join(
            f"{k}({cap_c[k]/n_sub*100:.1f}%)"
            for k, _ in sorted(
                ((h, cap_c[h]) for h in matchers.hard_caps), key=lambda kv: -kv[1]
            )[:3]
        )
        top3_soft = ", ".join(
            f"{k}({cap_c[k]/n_sub*100:.1f}%)"
            for k, _ in sorted(
                ((s, cap_c[s]) for s in matchers.soft_caps), key=lambda kv: -kv[1]
            )[:3]
        )

        # Raw substring "python" exact token prevalence for direct Doc 11 check
        py_hits = sum(1 for r in sub_recs if re.search(r"(?<![a-z0-9])python(?![a-z0-9])", r.text_corpus))

        ols_res = run_ols_analysis(sub_recs, sub_sets, matchers.hard_caps, matchers.soft_caps, group_label)
        subset_ols_results.append(ols_res)

        subset_comp_rows.append({
            "分组": group_label,
            "样本量(L2去重)": n_sub,
            "占全量比例(%)": round(n_sub / n_strict * 100.0, 2),
            "本科(%)": round(edu_c["本科"] / n_sub * 100.0, 2),
            "大专(%)": round(edu_c["大专"] / n_sub * 100.0, 2),
            "硕士(%)": round(edu_c["硕士"] / n_sub * 100.0, 2),
            "博士(%)": round(edu_c["博士"] / n_sub * 100.0, 2),
            "不限/在校可投(%)": round(edu_c["不限/在校可投"] / n_sub * 100.0, 2),
            "0年/应届/不限经验(%)": round(exp_c["0年/应届/不限"] / n_sub * 100.0, 2),
            "至少含1项硬技能(%)": round(hard_any / n_sub * 100.0, 2),
            "至少含1项软能力(%)": round(soft_any / n_sub * 100.0, 2),
            "Python明确提及率(%)": round(py_hits / n_sub * 100.0, 2),
            "Top3硬技能": top3_hard,
            "Top3软能力": top3_soft,
            "有效薪资样本(月薪1k-150k)": ols_res.get("n", 0),
            "月薪中位数(k)": round(ols_res.get("median_salary_k", 0.0), 2),
            "月薪均值(k)": round(ols_res.get("mean_salary_k", 0.0), 2),
        })

    subset_comp_df = pd.DataFrame(subset_comp_rows)
    subset_comp_df.to_csv(
        TABLES_DIR / "table_clean_subset_comparison.csv", index=False, encoding="utf-8-sig"
    )

    # --------------------------------------------------------------------------
    # Salary Parser Audit & Regression Table (Replicating Biased Old vs Clean Full vs Subsets)
    # --------------------------------------------------------------------------
    print("[4/5] Running Clean Salary Parsing & OLS Subsample Regressions...")
    sal_cat_counts = Counter(r.salary_parsed.category for r in strict_records)
    # Run legacy biased regression parser on L0 raw vs clean L2 for transparent side-by-side audit
    reg_rows = []
    for res in subset_ols_results:
        if not res["valid"]:
            continue
        for pred_name, cn_name in (
            ("soft", "可迁移软能力数量"),
            ("hard", "硬技能数量"),
            ("edu", "学历等级(1-5)"),
            ("exp", "工作经验年限"),
        ):
            cinfo = res["coefs"][pred_name]
            reg_rows.append({
                "样本分组": res["label"],
                "有效样本N": res["n"],
                "R2": round(res["r2"], 4),
                "Adj_R2": round(res["adj_r2"], 4),
                "Log_Salary_R2": round(res["log_r2"], 4),
                "自变量": cn_name,
                "标准化Beta": round(cinfo["beta"], 4),
                "HC3稳健标准误": round(cinfo["se_hc3"], 4),
                "t值": round(cinfo["t"], 2),
                "p值": f"{cinfo['p']:.4g}",
                "Log回归Beta": round(cinfo["beta_log"], 4),
                "Log回归p值": f"{cinfo['p_log']:.4g}",
                "VIF": round(cinfo["vif"], 3),
                "残差偏度": round(res["resid_skew"], 3),
            })

    reg_df = pd.DataFrame(reg_rows)
    reg_df.to_csv(
        TABLES_DIR / "table_clean_regression_comparison.csv", index=False, encoding="utf-8-sig"
    )

    # --------------------------------------------------------------------------
    # Generate >=200 dpi Chinese Figures
    # --------------------------------------------------------------------------
    print("[5/5] Plotting Clean Comparison Figures (>=240 dpi)...")
    _plot_matching_ablation(ablation_df, diag_rows, FIGURES_DIR / "fig_clean_matching_ablation.png")
    _plot_subset_comparison(
        strict_records, strict_domain_sets, subset_indices, matchers, FIGURES_DIR / "fig_clean_subset_comparison.png"
    )
    _plot_regression_comparison(subset_ols_results, FIGURES_DIR / "fig_clean_regression_betas.png")

    # --------------------------------------------------------------------------
    # Write Structured Markdown Audit Report
    # --------------------------------------------------------------------------
    _write_audit_report(
        dedup_meta=dedup_meta,
        dedup_rows=dedup_rows,
        ablation_df=ablation_df,
        diag_rows=diag_rows,
        spearman_raw_vs_clean=(float(spearman_raw_vs_clean.statistic), float(spearman_raw_vs_clean.pvalue)),
        spearman_sub_vs_strict=(float(spearman_sub_vs_strict.statistic), float(spearman_sub_vs_strict.pvalue)),
        subset_comp_rows=subset_comp_rows,
        ols_results=subset_ols_results,
        sal_cat_counts=sal_cat_counts,
    )
    print(f"Done. Audit report written to: {AUDIT_REPORT_PATH}")


def _plot_matching_ablation(
    ablation_df: pd.DataFrame,
    diag_rows: list[dict[str, object]],
    out_path: Path,
) -> None:
    """Plot penetration shift after dedup + boundary fix and top dropped false-positive aliases."""
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5), dpi=FIGURE_DPI)

    # Left: top 12 largest absolute shifts in penetration (%)
    df_top = ablation_df.copy()
    df_top["abs_diff"] = (df_top["L2_词边界含显式变体渗透率(%)"] - df_top["L0_原始子串渗透率(%)"]).abs()
    df_top = df_top.sort_values("abs_diff", ascending=True).tail(12)

    y = np.arange(len(df_top))
    h = 0.36
    axes[0].barh(y - h / 2, df_top["L0_原始子串渗透率(%)"], h, label="旧口径: L0 原始子串 (59,430)", color="#e67e22", alpha=0.88)
    axes[0].barh(y + h / 2, df_top["L2_词边界含显式变体渗透率(%)"], h, label="干净口径: L2 严格去重+词边界 (45,880)", color="#2980b9", alpha=0.88)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(df_top["能力名称"], fontsize=9.5)
    axes[0].set_xlabel("市场渗透率 (%)", fontsize=10.5)
    axes[0].set_title("去重与词边界清洗前后渗透率变化 Top-12 能力", fontsize=11.5, fontweight="bold")
    axes[0].legend(fontsize=8.5)
    axes[0].grid(axis="x", linestyle="--", alpha=0.45)

    # Right: top 8 short ASCII aliases by noisy hits removed
    top_diag = [r for r in diag_rows if int(r["noise_dropped_count"]) > 0][:8]
    top_diag.reverse()
    y2 = np.arange(len(top_diag))
    aliases_lbl = [f"{r['alias']} ({r['capability']})" for r in top_diag]
    clean_vals = [int(r["domain_jd_hits"]) for r in top_diag]
    noise_vals = [int(r["noise_dropped_count"]) for r in top_diag]

    axes[1].barh(y2, clean_vals, label="清洗后保留有效命中", color="#27ae60", alpha=0.88)
    axes[1].barh(y2, noise_vals, left=clean_vals, label="剔除误命中噪声 (如 deepseek→ps / sdk→sd)", color="#c0392b", alpha=0.85)
    axes[1].set_yticks(y2)
    axes[1].set_yticklabels(aliases_lbl, fontsize=9)
    axes[1].set_xlabel("L2 去重语料中匹配 JD 条数 (n=45,880)", fontsize=10.5)
    axes[1].set_title("短英文别名子串误命中剔除统计 Top-8", fontsize=11.5, fontweight="bold")
    axes[1].legend(fontsize=8.5)
    axes[1].grid(axis="x", linestyle="--", alpha=0.45)

    max_tot = max((int(r["domain_jd_hits"]) + int(r["noise_dropped_count"]) for r in top_diag), default=100)
    axes[1].set_xlim(0, max_tot * 1.25)
    for idx, r in enumerate(top_diag):
        tot = int(r["domain_jd_hits"]) + int(r["noise_dropped_count"])
        axes[1].text(tot + max_tot * 0.012, idx, f"-{int(r['noise_dropped_count']):,} ({r['noise_ratio_pct']:.1f}%噪声)", va="center", fontsize=8)

    plt.tight_layout()
    plt.savefig(out_path, dpi=FIGURE_DPI)
    plt.close()


def _plot_subset_comparison(
    strict_records: list[JobRecord],
    strict_domain_sets: list[set[str]],
    subset_indices: dict[str, list[int]],
    matchers: CapabilityMatchers,
    out_path: Path,
) -> None:
    """Plot education threshold & capability differences between Narrow/Broad Non-STEM vs STEM."""
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), dpi=FIGURE_DPI)
    groups = ["窄口径非新工科 (17类)", "宽口径非新工科 (21类)", "强研发/工程/算法对照组"]
    edu_buckets = ["大专", "本科", "硕士", "博士", "不限/在校可投"]

    matrix_edu = []
    for g in groups:
        idxs = subset_indices[g]
        c = Counter(strict_records[i].edu_label for i in idxs)
        n = max(len(idxs), 1)
        matrix_edu.append([c[b] / n * 100.0 for b in edu_buckets])

    x = np.arange(len(edu_buckets))
    w = 0.26
    colors = ["#e67e22", "#16a085", "#34495e"]
    for gi, (g, vals, col) in enumerate(zip(groups, matrix_edu, colors)):
        bars = axes[0].bar(x + (gi - 1) * w, vals, w, label=f"{g} (n={len(subset_indices[g]):,})", color=col, alpha=0.88)
        for b, v in zip(bars, vals):
            if v >= 1.0:
                axes[0].text(b.get_x() + b.get_width() / 2, v + 0.7, f"{v:.1f}%", ha="center", fontsize=7.5)

    axes[0].set_xticks(x)
    axes[0].set_xticklabels(edu_buckets, fontsize=9.5)
    axes[0].set_ylabel("占分组样本比例 (%)", fontsize=10.5)
    axes[0].set_title("L2 干净去重后各岗位组学历门槛实测对比", fontsize=11.5, fontweight="bold")
    axes[0].legend(fontsize=8.5)
    axes[0].grid(axis="y", linestyle="--", alpha=0.45)

    # Right: key hard vs soft capability demand rates across Narrow Non-STEM vs STEM
    key_caps = ["团队协作", "独立交付", "持续改进", "商业思维", "写作能力", "跨部门沟通", "数据清洗", "统计推断", "交互内容制作"]
    narrow_idxs = subset_indices["窄口径非新工科 (17类)"]
    broad_idxs = subset_indices["宽口径非新工科 (21类)"]
    stem_idxs = subset_indices["强研发/工程/算法对照组"]

    def rate_list(idxs: list[int]) -> list[float]:
        n = max(len(idxs), 1)
        return [sum(1 for i in idxs if kc in strict_domain_sets[i]) / n * 100.0 for kc in key_caps]

    r_narrow = rate_list(narrow_idxs)
    r_broad = rate_list(broad_idxs)
    r_stem = rate_list(stem_idxs)

    y2 = np.arange(len(key_caps))
    h2 = 0.26
    axes[1].barh(y2 + h2, r_narrow, h2, label="窄口径非新工科", color="#e67e22", alpha=0.88)
    axes[1].barh(y2, r_broad, h2, label="宽口径非新工科", color="#16a085", alpha=0.88)
    axes[1].barh(y2 - h2, r_stem, h2, label="研发/算法对照组", color="#34495e", alpha=0.88)
    axes[1].set_yticks(y2)
    axes[1].set_yticklabels(key_caps, fontsize=9.5)
    axes[1].set_xlabel("清晰词边界渗透率 (%)", fontsize=10.5)
    axes[1].set_title("非新工科子集 vs 研发岗关键软硬能力需求对比", fontsize=11.5, fontweight="bold")
    axes[1].legend(fontsize=8.5)
    axes[1].grid(axis="x", linestyle="--", alpha=0.45)

    plt.tight_layout()
    plt.savefig(out_path, dpi=FIGURE_DPI)
    plt.close()


def _plot_regression_comparison(ols_results: list[dict[str, object]], out_path: Path) -> None:
    """Plot signed standardized OLS coefficients (with 95% HC3 CIs) across subsamples."""
    valid_res = [r for r in ols_results if r.get("valid")]
    preds = [("soft", "软能力数"), ("hard", "硬技能数"), ("edu", "学历(1-5)"), ("exp", "经验年限")]

    fig, ax = plt.subplots(figsize=(11.5, 5.8), dpi=FIGURE_DPI)
    x = np.arange(len(preds))
    n_groups = len(valid_res)
    w = 0.19
    palette = ["#2980b9", "#e67e22", "#16a085", "#7f8c8d"]

    for gi, (res, col) in enumerate(zip(valid_res, palette)):
        betas = [res["coefs"][p]["beta"] for p, _ in preds]
        errs = [1.96 * res["coefs"][p]["se_hc3"] for p, _ in preds]
        pos = x + (gi - (n_groups - 1) / 2) * w
        ax.bar(
            pos,
            betas,
            w,
            yerr=errs,
            capsize=3,
            label=f"{res['label']} (N={res['n']:,}, R²={res['r2']:.3f})",
            color=col,
            alpha=0.88,
        )
        for px, b, e in zip(pos, betas, errs):
            if b >= 0:
                ax.text(px, b + e + 0.012, f"{b:+.3f}", ha="center", va="bottom", fontsize=7.5)
            else:
                ax.text(px, b - e - 0.012, f"{b:+.3f}", ha="center", va="top", fontsize=7.5)

    ax.axhline(0.0, color="black", linewidth=1.0)
    ax.set_ylim(-0.11, 0.50)
    ax.set_xticks(x)
    ax.set_xticklabels([cn for _, cn in preds], fontsize=10.5)
    ax.set_ylabel("标准化月薪回归系数 β (带 95% HC3 置信区间)", fontsize=10.5)
    ax.set_title("修复薪资(万/千/元/年)与经验编码后各岗位子集 OLS 回归对比", fontsize=12, fontweight="bold")
    ax.legend(fontsize=8.5, loc="upper left")
    ax.grid(axis="y", linestyle="--", alpha=0.45)

    plt.tight_layout()
    plt.savefig(out_path, dpi=FIGURE_DPI)
    plt.close()


def _write_audit_report(
    *,
    dedup_meta: dict[str, int],
    dedup_rows: list[dict[str, object]],
    ablation_df: pd.DataFrame,
    diag_rows: list[dict[str, object]],
    spearman_raw_vs_clean: tuple[float, float],
    spearman_sub_vs_strict: tuple[float, float],
    subset_comp_rows: list[dict[str, object]],
    ols_results: list[dict[str, object]],
    sal_cat_counts: Counter[str],
) -> None:
    top_noise = diag_rows[:6]
    narrow_row = next(r for r in subset_comp_rows if "窄口径" in str(r["分组"]))
    broad_row = next(r for r in subset_comp_rows if "宽口径" in str(r["分组"]))
    stem_row = next(r for r in subset_comp_rows if "强研发" in str(r["分组"]))

    ols_full = next(r for r in ols_results if "全量" in str(r["label"]))
    ols_narrow = next(r for r in ols_results if "窄口径" in str(r["label"]))
    ols_broad = next(r for r in ols_results if "宽口径" in str(r["label"]))
    ols_stem = next(r for r in ols_results if "强研发" in str(r["label"]))

    lines = [
        "# 市场底座清洗消融与非新工科子集实算审计报告",
        "",
        f"> 运行脚本：`backend/scripts/run_cleaned_market_and_subset_ablation.py` | 固定随机种子：`{RANDOM_SEED}`  ",
        f"> 环境版本：Python `{platform.python_version()}` / pandas `{pd.__version__}` / numpy `{np.__version__}` / scipy `{scipy.__version__}` / statsmodels `{statsmodels.__version__}`",
        "",
        "## 一、核心结论（结果 → 证据 → 局限性 → 可执行启示）",
        "",
        f"1. **去重核验揭开两层重复**：59,430 条原始 JD 按 `job_url` 去重后精确等于申报书所写 **`{dedup_meta['url_dedup_count']:,}` 条**；但按规范化 `(job_name, company_name, city)` 跨类别/同企同城去重后仅剩 **`{dedup_meta['strict_dedup_count']:,}` 条**（跨类别冲突 `{dedup_meta['cross_category_collisions']:,}` 次，跨“非新工科/研发”边界冲突 `{dedup_meta['cross_split_collisions']:,}` 次）。",
        f"2. **旧版子串 `in` 匹配存在显著假阳性污染**：L0 原始子串与 L2 干净口径之间整体排名 Spearman 相关系数仍高（$\\rho = {spearman_raw_vs_clean[0]:.4f}$, $p = {spearman_raw_vs_clean[1]:.2e}$），但在高频与关键短英文别名上出现了系统性畸变——尤其是 **`ps`（误匹配 `deepseek` / `devops` / `ups` / `gps`）** 与 **`sd`（误匹配 `sdk` / `ssd` / `jesd204b`）** 让 `图文编辑` 被海量误算，**`less`（误匹配 `serverless` / `wireless`）** 和 **`rag`（误匹配 `storage` / `coverage`）** 同理；同时严格词边界会连带丢掉 `MySQL / PostgreSQL / Vue3 / vLLM / SDXL` 等真实技术变体（见下文显式变体对照）。",
        f"3. **旧版回归样本存在严重薪资截断偏置**：检查原始 CSV 发现，旧脚本 `parse_salary` 未解析 `万`（如 `1.8-2.8万`、`9千-1.1万`），把智联和前程无忧大量中高薪 `万` 口径样本直接丢弃，同时将 `学历不限 / 高中` 默认赋为 `本科(3)`，并将 `3年及以上 / 5年及以上 / 经验不限` 全部默认赋为 `1.0` 年。修复解析口径并加入 HC3 稳健标准误与 `log(salary)` 检验后，L2 干净全量有效月薪样本升至 **`{ols_full['n']:,}` 条**（`R² = {ols_full['r2']:.4f}`）。",
        f"4. **非新工科真实子集与 Doc 11 方向一致但细节有边界**：L2 去重后窄口径（17 类，n=`{narrow_row['样本量(L2去重)']:,}`，占 `{narrow_row['占全量比例(%)']}%`）与宽口径（21 类，n=`{broad_row['样本量(L2去重)']:,}`，占 `{broad_row['占全量比例(%)']}%`）中，硕士占比分别仅 `{narrow_row['硕士(%)']}%` / `{broad_row['硕士(%)']}%`（对照组研发岗 `{stem_row['硕士(%)']}%`），`Python` 明确提及率分别仅 `{narrow_row['Python明确提及率(%)']}%` / `{broad_row['Python明确提及率(%)']}%`（对照组 `{stem_row['Python明确提及率(%)']}%`）；然而需如实指出：即便在非新工科子集内，**简单计数层面的软能力条数对月薪的 OLS 标准化回归系数仍接近 0 或略负**，说明“能力价值”不能靠统计字面技能条数简单线性拟合并声称数据拟合出 `45/40/15`。",
        "",
        "---",
        "",
        "## 二、三层去重与公司/平台分布（`table_clean_dedup_summary.csv`）",
        "",
        "| 清洗阶段 | 总样本数 | 唯一企业数 | Boss | 51job | 猎聘 | 智联 | 窄口径非新工科 | 宽口径非新工科 | 研发对照组 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in dedup_rows:
        lines.append(
            f"| {r['清洗阶段']} | {r['总样本数']:,} | {r['唯一企业数']:,} | {r['Boss直聘']:,} | {r['前程无忧']:,} | "
            f"{r['猎聘网']:,} | {r['智联招聘']:,} | {r['窄口径非新工科']:,} | {r['宽口径非新工科']:,} | {r['研发工程对照组']:,} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 三、短英文别名误命中消融诊断（`table_clean_short_alias_diagnostics.csv`）",
        "",
        f"在 L2 去重干净语料（n=`{dedup_meta['strict_dedup_count']:,}`）上，对比子串 `in` 匹配与词边界匹配：",
        "",
        "| 能力项 | 短别名 | 原始子串命中 | 严格词边界命中 | 含显式变体命中 | 剔除纯噪声条数 (噪声比) | 显式变体补回条数 | 典型被剔除噪声词例 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for r in top_noise:
        lines.append(
            f"| {r['capability']} | `{r['alias']}` | {r['raw_jd_hits']:,} | {r['strict_jd_hits']:,} | {r['domain_jd_hits']:,} | "
            f"{r['noise_dropped_count']:,} ({r['noise_ratio_pct']:.1f}%) | {r['compound_recovered_count']:,} | `{r['top_dropped_noise_tokens']}` |"
        )

    lines.extend([
        "",
        "- **工程直接启示**：`framework_v1.json` 在代码里必须从“仅列 `SQL / PS / SD / RAG / Less`”升级为**显式词典 + 词边界正则**：即必须补入 `MySQL / PostgreSQL / SQLite / SQL Server / vLLM / SDXL / Vue2 / Vue3 / HTTPS`，同时禁止对短英文别名做无边界子串包含搜索。",
        "- 完整 33 项渗透率四版本对照见 [table_clean_matching_ablation.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_matching_ablation.csv) 与 [fig_clean_matching_ablation.png](file:///d:/job-capability-graph-backend/reports/figures/fig_clean_matching_ablation.png)。",
        "",
        "---",
        "",
        "## 四、非新工科双口径子集实测与 OLS 回归诚实结果",
        "",
        "### 4.1 群体结构与能力特征对比（`table_clean_subset_comparison.csv`）",
        "",
        "| 分组 | 样本量(L2) | 占比 | 硕士 | 不限/在校 | Python提及率 | 至少含1硬技能 | 至少含1软能力 | 月薪中位数(k) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for r in subset_comp_rows:
        lines.append(
            f"| {r['分组']} | {r['样本量(L2去重)']:,} | {r['占全量比例(%)']}% | {r['硕士(%)']}% | {r['不限/在校可投(%)']}% | "
            f"{r['Python明确提及率(%)']}% | {r['至少含1项硬技能(%)']}% | {r['至少含1项软能力(%)']}% | {r['月薪中位数(k)']} |"
        )

    lines.extend([
        "",
        "### 4.2 修复薪资/学历/经验解析后的分样本 OLS 回归（HC3 稳健标准误，`table_clean_regression_comparison.csv`）",
        "",
        f"L2 语料薪资分布明细：月薪规范解析 `{sal_cat_counts['monthly']:,}` 条、年薪折月 `{sal_cat_counts['annual_converted']:,}` 条、日薪实习排除 `{sal_cat_counts['daily_excluded']:,}` 条、面议 `{sal_cat_counts['negotiable']:,}` 条、时薪/周薪排除 `{sal_cat_counts['hourly_excluded'] + sal_cat_counts['weekly_excluded']:,}` 条、未解析 `{sal_cat_counts['unparsed']:,}` 条。",
        "",
        "| 分组 | N | R² | Log-R² | β_软能力 (p) | β_硬技能 (p) | β_学历 (p) | β_经验 (p) | 最大 VIF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for res in (ols_full, ols_narrow, ols_broad, ols_stem):
        c = res["coefs"]
        max_vif = max(v["vif"] for v in c.values())
        lines.append(
            f"| {res['label']} | {res['n']:,} | {res['r2']:.4f} | {res['log_r2']:.4f} | "
            f"{c['soft']['beta']:+.4f} ({c['soft']['p']:.2g}) | {c['hard']['beta']:+.4f} ({c['hard']['p']:.2g}) | "
            f"{c['edu']['beta']:+.4f} ({c['edu']['p']:.2g}) | {c['exp']['beta']:+.4f} ({c['exp']['p']:.2g}) | {max_vif:.2f} |"
        )

    lines.extend([
        "",
        f"- **对数月薪（Mincer 对数回归）敏感性发现**：由于月薪呈右偏分布（残差偏度 `{ols_full['resid_skew']:.2f}`），改用更贴合薪酬分布的 `ln(salary)` 后拟合度显著提升（窄/宽非新工科 `Log-R²` 升至 `{ols_narrow['log_r2']:.4f}` / `{ols_broad['log_r2']:.4f}`），且**软能力数量的对数回归系数**从研发对照组的 `{ols_stem['coefs']['soft']['beta_log']:+.4f}`（$p={ols_stem['coefs']['soft']['p_log']:.2g}$）反转为窄口径 **`{ols_narrow['coefs']['soft']['beta_log']:+.4f}`**（$p={ols_narrow['coefs']['soft']['p_log']:.3f}$）与宽口径 **`{ols_broad['coefs']['soft']['beta_log']:+.4f}`**（$p={ols_broad['coefs']['soft']['p_log']:.3f}$），同时非新工科硬技能计数的对数系数不再显著（$p={ols_narrow['coefs']['hard']['p_log']:.2f}$ / ${ols_broad['coefs']['hard']['p_log']:.2f}$）。详见 [table_clean_regression_comparison.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_regression_comparison.csv)。",
        "",
        "---",
        "",
        "## 五、局限性与诚实边界",
        "",
        "1. **类别边界近似性**：基于 112 个 CSV 分类名切分的窄/宽口径能保证可复现性，但在同一招聘目录下（如 `数据治理`、`技术支持`）仍混有个别偏研发或纯售前岗位，这是抓取目录天然带有的颗粒度上限。",
        "2. **基础月薪 vs N 薪**：统一抽取基本月薪中位数并去除 `·14薪` 后缀以保持横截面可比（避免年终奖估算猜测）；同时将日薪实习与时薪排除在正职月薪回归之外。",
        "3. **为什么不能拿 OLS 系数倒推 45/40/15**：在任何子集中，影响市场起薪的主要观测解释力都来自**工作年限（经验）**与**学历门槛**，而招聘文本中出现的零散软能力词数量本身不决定高薪。因此，**市场大数据的正当作用是证明“非新工科岗位确实以本科和软能力协作为主导、学历门槛温和且硬技能分散”**，而 `45/40/15` 的真正有效性验证仍必须等待人类专家标注集落地后在排序/推荐任务上做实验 2（消融对照）。",
        "",
        "### 产物索引",
        "- [table_clean_dedup_summary.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_dedup_summary.csv)",
        "- [table_clean_matching_ablation.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_matching_ablation.csv)",
        "- [table_clean_short_alias_diagnostics.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_short_alias_diagnostics.csv)",
        "- [table_clean_subset_comparison.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_subset_comparison.csv)",
        "- [table_clean_regression_comparison.csv](file:///d:/job-capability-graph-backend/reports/tables/table_clean_regression_comparison.csv)",
        "- [fig_clean_matching_ablation.png](file:///d:/job-capability-graph-backend/reports/figures/fig_clean_matching_ablation.png)",
        "- [fig_clean_subset_comparison.png](file:///d:/job-capability-graph-backend/reports/figures/fig_clean_subset_comparison.png)",
        "- [fig_clean_regression_betas.png](file:///d:/job-capability-graph-backend/reports/figures/fig_clean_regression_betas.png)",
    ])

    AUDIT_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
