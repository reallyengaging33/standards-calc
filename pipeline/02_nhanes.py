"""
Phase 3 — NHANES height and obesity lookups.

Input : data/raw/nhanes/DEMO_L.xpt, data/raw/nhanes/BMX_L.xpt
        (CDC/NCHS NHANES Aug 2021–Aug 2023, no account needed)
        https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/DEMO_L.xpt
        https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/BMX_L.xpt
Output: site/data/nhanes_2021_23.json, and an `nhanes` section in site/data/meta.json

Variables, checked against the DEMO_L / BMX_L codebooks:
  SEQN      respondent id (join key)
  RIAGENDR  1 male, 2 female
  RIDAGEYR  age in years, top-coded at 80
  RIDRETH3  1 Mexican American, 2 Other Hispanic, 3 NH White, 4 NH Black,
            6 NH Asian, 7 Other race incl. multiracial (there is no code 5)
  RIDEXPRG  pregnancy at exam (1 = pregnant) — pregnant women are left out of the
            obesity share, as in CDC's published estimates; height still uses them
  WTMEC2YR  MEC exam weight (0 = not examined). Always used.
  BMXHT     standing height, cm
  BMXBMI    BMI, kg/m²

Lookups are by sex × age band × race group:
  age bands   18–29, 30–39, 40–49, 50–59, 60+
  race groups hispanic (1,2), white (3), black (4), asian (6), other (7)
  ACS ethnicity -> NHANES group: middle_eastern -> white; mixed, other -> other
For each group:
  height_le[k] = weighted P(height rounded to the nearest inch <= HEIGHT_FROM + k)
                 for k = 0..30, i.e. inches 58..88. Then
                 P(min <= h <= max) = le(max) - le(min - 1), le(<58) = 0, le(>88) = 1.
  obese        = weighted share with BMI >= 30
If a group has fewer than MIN_N respondents (for height or BMI), it is merged with the
neighbouring age band that has fewer respondents, repeatedly, until every pooled group
has MIN_N. Merges are listed in the output.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "nhanes"
OUT = ROOT / "site" / "data"

MIN_N = 100
BANDS = [(18, 29), (30, 39), (40, 49), (50, 59), (60, 200)]
BAND_LABELS = ["18-29", "30-39", "40-49", "50-59", "60+"]
RACE = {1: "hispanic", 2: "hispanic", 3: "white", 4: "black", 6: "asian", 7: "other"}
RACE_GROUPS = ["white", "black", "asian", "hispanic", "other"]
ETH_MAP = {"white": "white", "black": "black", "asian": "asian", "middle_eastern": "white",
           "mixed": "other", "hispanic": "hispanic", "other": "other"}
SEXES = {1: "male", 2: "female"}
HEIGHT_FROM, HEIGHT_TO = 58, 88  # le() table covers 58..88 so that min=59 works


def load() -> pd.DataFrame:
    demo = pd.read_sas(RAW / "DEMO_L.xpt", format="xport")
    bmx = pd.read_sas(RAW / "BMX_L.xpt", format="xport")
    df = demo[["SEQN", "RIAGENDR", "RIDAGEYR", "RIDRETH3", "RIDEXPRG", "WTMEC2YR"]].merge(
        bmx[["SEQN", "BMXHT", "BMXBMI"]], on="SEQN", how="left")
    df = df[(df.RIDAGEYR >= 18) & (df.WTMEC2YR > 0)].copy()
    df["band"] = np.searchsorted([b[0] for b in BANDS], df.RIDAGEYR, side="right") - 1
    df["race"] = df.RIDRETH3.map(RACE)
    df["sex"] = df.RIAGENDR.map(SEXES)
    df["inch"] = np.round(df.BMXHT / 2.54)
    df["pregnant"] = df.RIDEXPRG == 1
    return df


def stats(g: pd.DataFrame) -> dict:
    h = g[g.BMXHT.notna()]
    b = g[g.BMXBMI.notna() & ~g.pregnant]
    w = h.WTMEC2YR.to_numpy()
    inch = np.clip(h.inch.to_numpy(), HEIGHT_FROM - 1, HEIGHT_TO + 1)
    le = [float(w[inch <= k].sum() / w.sum()) for k in range(HEIGHT_FROM, HEIGHT_TO + 1)]
    return {
        "n_height": int(len(h)), "n_bmi": int(len(b)),
        "mean_height_in": round(float(np.average(h.BMXHT, weights=w) / 2.54), 2),
        "obese": round(float(np.average(b.BMXBMI >= 30, weights=b.WTMEC2YR)), 5),
        "height_le": [round(x, 5) for x in le],
    }


def pooled_bands(df: pd.DataFrame, sex: str, race: str) -> list[list[int]]:
    """Merge adjacent age bands until each pooled group has MIN_N height and BMI rows."""
    sub = df[(df.sex == sex) & (df.race == race)]
    groups = [[i] for i in range(len(BANDS))]

    def n(gr):
        s = sub[sub.band.isin(gr)]
        return min(s.BMXHT.notna().sum(), (s.BMXBMI.notna() & ~s.pregnant).sum())

    while len(groups) > 1:
        sizes = [n(g) for g in groups]
        i = int(np.argmin(sizes))
        if sizes[i] >= MIN_N:
            break
        if i == 0:
            j = 1
        elif i == len(groups) - 1:
            j = i - 1
        else:
            j = i - 1 if sizes[i - 1] <= sizes[i + 1] else i + 1
        a, b = sorted((i, j))
        groups[a:b + 1] = [groups[a] + groups[b]]
    return groups


def main():
    df = load()
    tables, merges = {}, []
    for sex in SEXES.values():
        tables[sex] = {}
        for race in RACE_GROUPS:
            rows = [None] * len(BANDS)
            for gr in pooled_bands(df, sex, race):
                s = stats(df[(df.sex == sex) & (df.race == race) & df.band.isin(gr)])
                label = "+".join(BAND_LABELS[i] for i in gr)
                s["bands"] = label
                if len(gr) > 1:
                    merges.append(f"{sex} {race}: {label} pooled (n_height={s['n_height']}, n_bmi={s['n_bmi']})")
                for i in gr:
                    rows[i] = s
            tables[sex][race] = rows

    # Overall checks (crude, weighted)
    adults20 = df[df.RIDAGEYR >= 20]
    b = adults20[adults20.BMXBMI.notna() & ~adults20.pregnant]
    checks = {
        "respondents_18plus_examined": int(len(df)),
        "obesity_20plus_pct": round(100 * float(np.average(b.BMXBMI >= 30, weights=b.WTMEC2YR)), 1),
    }
    for sex in SEXES.values():
        h = df[(df.sex == sex) & df.BMXHT.notna()]
        checks[f"mean_height_{sex}_in"] = round(float(np.average(h.BMXHT, weights=h.WTMEC2YR) / 2.54), 2)
        bb = df[(df.sex == sex) & df.BMXBMI.notna() & ~df.pregnant]
        checks[f"obesity_18plus_{sex}_pct"] = round(100 * float(np.average(bb.BMXBMI >= 30, weights=bb.WTMEC2YR)), 1)

    out = {
        "source": "CDC/NCHS NHANES August 2021–August 2023 (DEMO_L, BMX_L), MEC exam weights",
        "age_bands": BAND_LABELS,
        "age_band_starts": [b[0] for b in BANDS],
        "eth_map": ETH_MAP,
        "height_from_in": HEIGHT_FROM, "height_to_in": HEIGHT_TO,
        "height_note": "height_le[k] = P(height rounded to nearest inch <= height_from_in + k)",
        "obese_note": "share with BMI >= 30, pregnant women excluded",
        "min_n": MIN_N, "pooled": merges,
        "tables": tables,  # tables[sex][race][band_index]
    }
    path = OUT / "nhanes_2021_23.json"
    path.write_text(json.dumps(out, separators=(",", ":")))

    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta["nhanes"] = {
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "CDC/NCHS, National Health and Nutrition Examination Survey, Aug 2021–Aug 2023",
        "url": "https://wwwn.cdc.gov/nchs/nhanes/continuousnhanes/default.aspx?Cycle=2021-2023",
        "files": ["DEMO_L.xpt", "BMX_L.xpt"], "output": path.name, "bytes": path.stat().st_size,
        "weight": "WTMEC2YR", "pooled_groups": merges, "checks": checks,
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    print(json.dumps({"bytes": path.stat().st_size, **checks, "pooled": merges}, indent=2))
    print("\nper group (sex race band: n_height, mean height, obese%):")
    for sex in SEXES.values():
        for race in RACE_GROUPS:
            seen = set()
            for r in tables[sex][race]:
                if r["bands"] in seen:
                    continue
                seen.add(r["bands"])
                print(f"  {sex:6} {race:8} {r['bands']:22} n={r['n_height']:4}  "
                      f"{r['mean_height_in']:5.1f} in  obese {100 * r['obese']:4.1f}%")


if __name__ == "__main__":
    main()
