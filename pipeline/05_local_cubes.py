"""
Build the local cubes: one per state and one per city in US_STATES, loaded on demand by the site.

Source: ACS 2020–2024 5-year PUMS person files, one zip per state (bigger sample than the
1-year file, so small metros still have respondents). The national cube stays on the
1-year file (01_acs.py).
    https://www2.census.gov/programs-surveys/acs/data/pums/2024/5-Year/csv_p<st>.zip
    dictionary: PUMS_Data_Dictionary_2020-2024.csv (same variables as 2024 1-year; one PUMA
    column on 2020 PUMA boundaries for all five years; ADJINC differs by survey year and
    brings every year to 2024 dollars, so it's applied per record)

Steps
  1. For each state: download its zip, recode exactly as 01_acs.py does (same functions:
     single, ethnicity incl. MENA ancestry, ADJINC income buckets, own children via
     RELSHIPP + SFN/SFR, ages 90+ pooled), aggregate to PUMA × cube cell, save a small
     intermediate in data/raw/acs5y/agg/, then DELETE the zip (disk is limited).
  2. State cube = all PUMAs of the state. Metro cube = the PUMAs listed for that city in
     data/raw/geo/city_areas.json (04_crosswalk.py), which may span states.
Output: site/data/state/<ST>.json, site/data/metro/<ST>-<city id>.json, and a `local`
section in site/data/meta.json (area definition, PUMAs, sample sizes for every file).
"""
from __future__ import annotations

import datetime as dt
import gzip
import importlib.util
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "acs5y"
AGG = RAW / "agg"
OUT = ROOT / "site" / "data"
URL = "https://www2.census.gov/programs-surveys/acs/data/pums/2024/5-Year/csv_p{st}.zip"
DICT = RAW / "PUMS_Data_Dictionary_2020-2024.csv"

spec = importlib.util.spec_from_file_location("acs", ROOT / "pipeline" / "01_acs.py")
acs = importlib.util.module_from_spec(spec); spec.loader.exec_module(acs)
cw = importlib.util.spec_from_file_location("cw", ROOT / "pipeline" / "04_crosswalk.py")
crosswalk = importlib.util.module_from_spec(cw); cw.loader.exec_module(crosswalk)

COLS = ["SERIALNO", "PUMA", "PWGTP", "AGEP", "SEX", "MAR", "RAC1P", "RACBLK", "HISP", "ANC1P", "ANC2P",
        "PINCP", "ADJINC", "RELSHIPP", "SFN", "SFR"]
DIMS = ["sex", "age", "single", "eth", "black", "inc", "kids", "living"]


def check_dictionary():
    d = pd.read_csv(DICT, header=None, dtype=str, names=list("abcdefg"))
    for c in COLS:
        assert ((d.a == "NAME") & (d.b == c)).any(), f"{c} missing from 5-year dictionary"
    anc = {int(r.e) for r in d[(d.a == "VAL") & (d.b == "ANC1P")].itertuples() if 400 <= int(r.e) <= 499}
    # The 2020-2024 list has no 408 (Tunisian); every 5-year 400-499 code is in our MENA list,
    # so the ethnicity grouping is identical to the national cube.
    assert anc <= set(acs.MENA_CODES), f"new MENA ancestry codes in 5-year file: {sorted(anc - set(acs.MENA_CODES))}"


def aggregate_state(st: str, fips: str) -> Path:
    out = AGG / f"{st}.csv.gz"
    if out.exists():
        return out
    RAW.mkdir(parents=True, exist_ok=True)
    zpath = RAW / f"csv_p{st.lower()}.zip"
    if not zpath.exists():
        req = urllib.request.Request(URL.format(st=st.lower()), headers={"User-Agent": "Mozilla/5.0 (standards-calc pipeline)"})
        with urllib.request.urlopen(req, timeout=600) as r, open(zpath, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    parts = []
    with zipfile.ZipFile(zpath) as z:
        for m in [n for n in z.namelist() if n.endswith(".csv")]:
            with z.open(m) as f:
                for chunk in pd.read_csv(f, usecols=COLS, dtype={"SERIALNO": str}, chunksize=500_000):
                    parts.append(chunk)
    df = pd.concat(parts, ignore_index=True)
    for c in ["SFN", "SFR", "ANC1P", "ANC2P", "PINCP"]:
        df[c] = df[c].fillna(0)
    df["kids"] = acs.own_children(df)                       # needs the children's rows
    df["living"] = acs.living(df)                           # needs every household member
    a = df[df.AGEP >= 18].copy()
    a["single"] = a.MAR.isin([2, 3, 5]).astype(np.int8)
    a["eth"] = acs.ethnicity(a)
    a["black"] = acs.black_flag(a)
    a["inc"] = acs.income_bucket((a.PINCP * a.ADJINC / 1e6).to_numpy())
    a["sex"] = (a.SEX - 1).astype(np.int8)
    a["age"] = a.AGEP.clip(upper=acs.AGE_TOP)
    a["puma"] = fips + a.PUMA.astype(int).astype(str).str.zfill(5)
    g = a.groupby(["puma"] + DIMS).agg(w=("PWGTP", "sum"), n=("PWGTP", "size")).reset_index()
    AGG.mkdir(parents=True, exist_ok=True)
    g.to_csv(out, index=False, compression="gzip")
    zpath.unlink()                                          # raw file no longer needed
    print(f"  {st}: {len(df):,} persons, {len(a):,} adults, {len(g):,} PUMA cells", file=sys.stderr, flush=True)
    return out


def write_cube(path: Path, g: pd.DataFrame, info: dict) -> dict:
    cube = g.groupby(DIMS).agg(w=("w", "sum"), n=("n", "sum")).reset_index().sort_values(DIMS)
    doc = {
        "source": "ACS 2020–2024 5-year PUMS, person file",
        "area": info,
        "columns": ["sex", "age", "single", "eth", "black", "inc", "kids", "living", "w", "n"],
        "data": {c: cube[c].astype(int).tolist() for c in DIMS + ["w", "n"]},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(doc, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return {"file": str(path.relative_to(OUT)), "cells": int(len(cube)), "rows": int(cube.n.sum()),
            "adults": int(cube.w.sum()), "bytes": len(raw), "bytes_gzip": len(gzip.compress(raw, 9))}


def main():
    check_dictionary()
    states = crosswalk.us_states()
    fips = crosswalk.FIPS
    for s in states:
        aggregate_state(s["code"], fips[s["code"]])
    allp = pd.concat([pd.read_csv(AGG / f"{s['code']}.csv.gz", dtype={"puma": str}) for s in states], ignore_index=True)

    areas = json.loads((ROOT / "data" / "raw" / "geo" / "city_areas.json").read_text())
    local = {"built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "source": "U.S. Census Bureau, ACS 2020–2024 5-year PUMS (person files by state)",
             "url": "https://www2.census.gov/programs-surveys/acs/data/pums/2024/5-Year/",
             "crosswalk": "2020 tract→PUMA relationship file + 2020 Census tract populations + OMB July 2023 "
                          "CBSA delineation (2020 for Connecticut); a PUMA is in an area when most of its people are",
             "states": {}, "metros": {}}
    for s in states:
        st = s["code"]
        g = allp[allp.puma.str[:2] == fips[st]]
        local["states"][st] = write_cube(OUT / "state" / f"{st}.json", g,
                                         {"level": "state", "id": st, "name": s["name"]})
    for key, a in areas.items():
        g = allp[allp.puma.isin(a["pumas"])]
        info = {"level": "metro", "id": key, "name": a["name"], "state": a["state"], "area": a["area"],
                "approximate": a["approximate"], "pumas": len(a["pumas"])}
        if a["approximate"]:
            info["note"] = (f"{a['name']} is smaller than the smallest census area, so this uses the "
                            f"surrounding census area ({a['pumas_inside_pct']:.0f}% of its people live in {a['area']}).")
        r = write_cube(OUT / "metro" / f"{key}.json", g, info)
        r.update({"area": a["area"], "pumas_list": a["pumas"], "approximate": a["approximate"],
                  "area_covered_pct": a["area_covered_pct"], "pumas_inside_pct": a["pumas_inside_pct"]})
        local["metros"][key] = r

    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["local"] = local
    meta_path.write_text(json.dumps(meta, indent=1))

    sizes = [v["bytes_gzip"] for v in list(local["states"].values()) + list(local["metros"].values())]
    rows = sorted(local["metros"].items(), key=lambda kv: kv[1]["rows"])
    us5 = sum(v["adults"] for v in local["states"].values())
    print(json.dumps({"state_files": len(local["states"]), "metro_files": len(local["metros"]),
                      "largest_gzip_kb": round(max(sizes) / 1024), "median_gzip_kb": round(float(np.median(sizes)) / 1024),
                      "total_gzip_mb": round(sum(sizes) / 2**20, 1), "total_raw_mb": round(sum(
                          v["bytes"] for v in list(local["states"].values()) + list(local["metros"].values())) / 2**20, 1),
                      "us_adults_5yr_sum_of_states": us5,
                      "smallest_metro_samples": [(k, v["rows"]) for k, v in rows[:5]]}, indent=1))


if __name__ == "__main__":
    main()
