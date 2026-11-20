"""
Phase 5 — validate the processed data against published figures.

Run after 01_acs.py and 02_nhanes.py:
    python pipeline/03_validate.py

Official ACS 2024 1-year figures are fetched once from the data.census.gov table API
(no key needed) and cached in data/raw/official/. Variable labels were checked against
https://api.census.gov/data/2024/acs/acs1[/subject]/variables:
  S0101_C01/C03/C05_026E  population 18+ (total / male / female)
  B12002_007/008E, _100/101E  never married, 25–29 and 30–34, male / female
  B01001_011/012E, _035/036E  population 25–29 and 30–34, male / female
  B19326_002E, _005E   median income, population 15+ with income, male / female
(CLAUDE.md names S1201 for the never-married check, but S1201 has no 25–34 age group,
so the detailed table B12002 is used instead.)

Survey-side numbers are recomputed from the raw PUMS / NHANES files, and the shipped
files in site/data are checked to agree with them. Exits non-zero if any check fails.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SITE = ROOT / "site"
OFFICIAL = RAW / "official"

TABLES = {"B09021": "ACSDT1Y2024.B09021", "B02009": "ACSDT1Y2024.B02009", "S0101": "ACSST1Y2024.S0101", "B12002": "ACSDT1Y2024.B12002",
          "B01001": "ACSDT1Y2024.B01001", "B19326": "ACSDT1Y2024.B19326"}


def official(table: str) -> dict:
    """Published US-level ACS 2024 1-year values for one table, cached on disk."""
    OFFICIAL.mkdir(parents=True, exist_ok=True)
    path = OFFICIAL / f"{table}.json"
    if not path.exists():
        url = f"https://data.census.gov/api/access/data/table?id={TABLES[table]}&g=010XX00US"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (standards-calc validation)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_bytes(r.read())
    rows = json.loads(path.read_text())["response"]["data"]
    return dict(zip(rows[0], rows[1]))


def wmedian(x, w):
    o = np.argsort(x)
    c = np.cumsum(w[o])
    return float(x[o][np.searchsorted(c, c[-1] / 2)])


results = []


def check(name, ours, target, ok, note=""):
    results.append((name, ours, target, ok, note))


def acs_checks():
    cols = ["AGEP", "SEX", "MAR", "PWGTP", "PINCP", "ADJINC", "RACBLK", "SERIALNO", "RELSHIPP"]
    parts = []
    with zipfile.ZipFile(RAW / "acs2024" / "csv_pus.zip") as z:
        for m in sorted(n for n in z.namelist() if n.startswith("psam_pus") and n.endswith(".csv")):
            with z.open(m) as f:
                parts.append(pd.read_csv(f, usecols=cols, dtype={"SERIALNO": str}))
    p = pd.concat(parts, ignore_index=True)
    adults = p[p.AGEP >= 18]

    s0101 = official("S0101")
    total = int(adults.PWGTP.sum())
    off_total = int(s0101["S0101_C01_026E"])
    check("Weighted US adults 18+", f"{total:,}", f"{off_total:,} (S0101)",
          abs(total / off_total - 1) < 0.005, f"diff {total - off_total:+,} ({100 * (total / off_total - 1):+.2f}%)")
    for sex, col, label in [(1, "S0101_C03_026E", "men"), (2, "S0101_C05_026E", "women")]:
        ours = int(adults.PWGTP[adults.SEX == sex].sum())
        off = int(s0101[col])
        check(f"Adults 18+, {label}", f"{ours:,}", f"{off:,} (S0101)", abs(ours / off - 1) < 0.005,
              f"{100 * (ours / off - 1):+.2f}%")

    ours = int(p.PWGTP[p.RACBLK == 1].sum())
    off = int(official("B02009")["B02009_001E"])
    check("Black alone or in combination, all ages (RACBLK)", f"{ours:,}", f"{off:,} (B02009)",
          abs(ours / off - 1) < 0.005, f"{100 * (ours / off - 1):+.2f}%")

    # Living situation (site filter) vs B09021, adults 18-34 in households
    import importlib.util
    spec = importlib.util.spec_from_file_location("acs", ROOT / "pipeline" / "01_acs.py")
    acs = importlib.util.module_from_spec(spec); spec.loader.exec_module(acs)
    p["living"] = acs.living(p)
    b09 = official("B09021")
    y = p[p.AGEP.between(18, 34) & ~p.RELSHIPP.isin([37, 38])]
    yw = y.PWGTP.sum()
    for code, var, label in [(0, "B09021_009E", "lives alone"), (2, "B09021_012E", "with parents (child of householder)")]:
        ours = 100 * y.PWGTP[y.living == code].sum() / yw
        off = 100 * int(b09[var]) / int(b09["B09021_008E"])
        check(f"Living 18–34: {label}", f"{ours:.1f}%", f"{off:.1f}% (B09021)", abs(ours - off) <= 1.0, f"{ours - off:+.2f} pts")

    b12, b01 = official("B12002"), official("B01001")
    spec = {1: ("men", ["B12002_007E", "B12002_008E"], ["B01001_011E", "B01001_012E"]),
            2: ("women", ["B12002_100E", "B12002_101E"], ["B01001_035E", "B01001_036E"])}
    for sex, (label, nm, pop) in spec.items():
        y = p[(p.SEX == sex) & p.AGEP.between(25, 34)]
        ours = 100 * y.PWGTP[y.MAR == 5].sum() / y.PWGTP.sum()
        off = 100 * sum(int(b12[k]) for k in nm) / sum(int(b01[k]) for k in pop)
        check(f"Never married 25–34, {label}", f"{ours:.1f}%", f"{off:.1f}% (B12002/B01001)", abs(ours - off) <= 1.0,
              f"{ours - off:+.2f} pts")

    b19 = official("B19326")
    inc = p.PINCP.fillna(0) * p.ADJINC / 1e6
    for sex, col, label in [(1, "B19326_002E", "men"), (2, "B19326_005E", "women")]:
        m = (p.AGEP >= 15) & (p.SEX == sex) & (inc != 0)  # Census "with income" = non-zero income
        ours = wmedian(inc[m].to_numpy(), p.PWGTP[m].to_numpy())
        off = int(b19[col])
        check(f"Median income 15+ with income, {label}", f"${ours:,.0f}", f"${off:,} (B19326)",
              abs(ours / off - 1) < 0.03, f"{100 * (ours / off - 1):+.1f}%")
    return total


def site_checks(raw_total):
    cube = json.loads((SITE / "data" / "acs_2024_cube.json").read_text())
    d = cube["data"]
    ctot = int(sum(d["w"]))
    check("Cube total = raw PUMS total", f"{ctot:,}", f"{raw_total:,}", ctot == raw_total)

    states = json.loads((SITE / "data" / "state_adults.json").read_text())
    stot = sum(v[g][k] for v in states.values() for g in ("male", "female") for k in ("single", "not_single"))
    empty = [k for k, v in states.items() if not v["male"]["single"] + v["female"]["single"]]
    check("State table total = cube total", f"{stot:,}", f"{ctot:,}", stot == ctot, f"{len(states)} entries")
    check("State table: 50 states + DC, none empty", len(states), 51, len(states) == 51 and not empty,
          f"empty: {empty}" if empty else "")

    ages = sorted(set(d["age"]))
    top = cube["dims"].get("age_top")
    check("Cube ages 18..90, top-coded ages pooled as 90+", f"{ages[0]}..{ages[-1]}", f"18..{top}",
          ages[0] == 18 and ages[-1] == top == 90 and ages == list(range(18, 91)))

    html = (SITE / "index.html").read_text()
    check("index.html knows the 90+ group", "AGE_TOP = 90" in html, True, "var AGE_TOP = 90;" in html)
    m = re.search(r"var INCOME_STEPS = \[([^\]]+)\]", html)
    steps = [int(x) for x in m.group(1).split(",")]
    check("Cube income steps = INCOME_STEPS in index.html", len(cube["dims"]["inc"]), len(steps),
          cube["dims"]["inc"] == steps)
    grp = re.search(r'id="eth-group">(.*?)</div>', html, re.S).group(1)
    opts = re.findall(r'data-value="([a-z_]+)"', grp)
    check("Ethnicity options = Doesn't matter / Black; cube has `black`", ", ".join(opts), "any, black",
          opts == ["any", "black"] and "black" in cube["columns"])

    nh = json.loads((SITE / "data" / "nhanes_2021_23.json").read_text())
    bad = []
    for sex, groups in nh["tables"].items():
        for race, rows in groups.items():
            for i, r in enumerate(rows):
                for key in ("height_le", "height_le_not_obese"):
                    a = r[key]
                    if any(y < x for x, y in zip(a, a[1:])):
                        bad.append(f"{sex}/{race}/{i}/{key} not monotone")
                if abs(r["height_le"][-1] - 1) > 1e-6:
                    bad.append(f"{sex}/{race}/{i} height_le does not end at 1")
                if abs((1 - r["height_le_not_obese"][-1]) - r["obese"]) > 0.02:
                    bad.append(f"{sex}/{race}/{i} joint table disagrees with obese share")
                if min(r["n_height"], r["n_bmi"]) < nh["min_n"]:
                    bad.append(f"{sex}/{race}/{i} n < {nh['min_n']}")
    check("NHANES tables well-formed (monotone, n ≥ 100)", "ok" if not bad else f"{len(bad)} problems",
          "ok", not bad, "; ".join(bad[:3]))


def local_checks():
    """State and metro cubes (05_local_cubes.py) against the ACS 2020–2024 5-year S0101."""
    path = OFFICIAL / "S0101_5y_states.json"
    if not path.exists():
        url = "https://data.census.gov/api/access/data/table?id=ACSST5Y2024.S0101&g=010XX00US$0400000"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (standards-calc validation)"})
        with urllib.request.urlopen(req, timeout=90) as r:
            path.write_bytes(r.read())
    rows = json.loads(path.read_text())["response"]["data"]
    h = rows[0]; pub = {r[h.index("NAME")]: int(r[h.index("S0101_C01_026E")]) for r in rows[1:]}
    meta = json.loads((SITE / "data" / "meta.json").read_text())["local"]
    html = (SITE / "index.html").read_text()
    states = json.loads(re.search(r"var US_STATES = (\[.*?\]);\n", html).group(1))
    worst, missing = (None, 0.0), []
    for st in states:
        f = SITE / "data" / "state" / f"{st['code']}.json"
        if not f.exists():
            missing.append(st["code"]); continue
        ours = meta["states"][st["code"]]["adults"]
        diff = abs(ours / pub[st["name"]] - 1)
        if diff > worst[1]:
            worst = (f"{st['code']} {ours:,} vs {pub[st['name']]:,}", diff)
    check("State cubes: adults 18+ vs ACS 5-year S0101 (51)", f"worst {100 * worst[1]:.2f}%", "each within 1%",
          not missing and worst[1] < 0.01, worst[0] + (f"; missing {missing}" if missing else ""))

    slug = lambda t: re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", t.lower().replace("*", "")))
    keys = [f"{st['code']}-{slug(c)}" for st in states for c in st["cities"]]
    nofile = [k for k in keys if not (SITE / "data" / "metro" / f"{k}.json").exists()]
    check("Metro cube for every city in US_STATES", f"{len(keys) - len(nofile)}/{len(keys)}", f"{len(keys)}/{len(keys)}",
          not nofile, ", ".join(nofile[:5]))
    m = meta["metros"]
    thin = sorted(m.items(), key=lambda kv: kv[1]["rows"])[0]
    check("Smallest metro sample", f"{thin[1]['rows']:,} adults surveyed", "≥ 1,000", thin[1]["rows"] >= 1000, thin[0])
    sizes = [v["bytes_gzip"] for v in list(meta["states"].values()) + list(m.values())]
    check("Local files under 500 KB gzipped", f"max {max(sizes) / 1024:.0f} KB", "< 500 KB", max(sizes) < 500 * 1024)
    weak = [k for k, v in m.items() if not v["approximate"] and (v["area_covered_pct"] < 50 or v["pumas_inside_pct"] < 50)]
    approx = [k for k, v in m.items() if v["approximate"]]
    check("Metro areas: PUMAs cover the area (non-approximate)", f"{len(weak)} weak", "0", not weak,
          f"{len(approx)} approximate, flagged on the site: " + ", ".join(approx))


def nhanes_checks():
    demo = pd.read_sas(RAW / "nhanes" / "DEMO_L.xpt", format="xport")
    bmx = pd.read_sas(RAW / "nhanes" / "BMX_L.xpt", format="xport")
    df = demo.merge(bmx, on="SEQN", how="left")
    df = df[(df.WTMEC2YR > 0) & (df.RIDAGEYR >= 20)]
    b = df[df.BMXBMI.notna() & (df.RIDEXPRG != 1)]
    ob = 100 * np.average(b.BMXBMI >= 30, weights=b.WTMEC2YR)
    check("NHANES adult obesity 20+", f"{ob:.1f}%", "40.3% (CDC, Aug 2021–Aug 2023)", abs(ob - 40.3) <= 1.0)
    for sex, label, target in [(1, "men", 69.0), (2, "women", 63.5)]:
        h = df[(df.RIAGENDR == sex) & df.BMXHT.notna()]
        mh = np.average(h.BMXHT, weights=h.WTMEC2YR) / 2.54
        check(f"Mean height 20+, {label}", f"{mh:.2f} in", f"≈ {target} in", abs(mh - target) <= 0.5)


def main():
    raw_total = acs_checks()
    site_checks(raw_total)
    local_checks()
    nhanes_checks()

    w = [max(len(str(r[i])) for r in results) for i in range(3)]
    w = [max(w[0], 5), max(w[1], 4), max(w[2], 8)]
    print(f"{'Check':<{w[0]}}  {'Ours':<{w[1]}}  {'Expected':<{w[2]}}  Result  Note")
    print("-" * (sum(w) + 30))
    for name, ours, target, ok, note in results:
        print(f"{name:<{w[0]}}  {str(ours):<{w[1]}}  {str(target):<{w[2]}}  {'PASS' if ok else 'FAIL':<6}  {note}")
    failed = sum(not r[3] for r in results)
    print(f"\n{len(results) - failed}/{len(results)} passed")

    meta_path = SITE / "data" / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["validation"] = {"passed": len(results) - failed, "total": len(results),
                          "checks": [{"check": r[0], "ours": str(r[1]), "expected": str(r[2]),
                                      "pass": bool(r[3]), "note": r[4]} for r in results]}
    meta_path.write_text(json.dumps(meta, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
