"""
Phase 2 — build the ACS adult cube for the browser.

Input : data/raw/acs2024/csv_pus.zip   (ACS 2024 1-year PUMS, person file, whole US)
        data/raw/acs2024/PUMS_Data_Dictionary_2024.csv
Output: site/data/acs_2024_cube.json
        site/data/state_adults.json
        site/data/meta.json

Download (no account needed):
  https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/csv_pus.zip
  https://www2.census.gov/programs-surveys/acs/tech_docs/pums/data_dict/PUMS_Data_Dictionary_2024.csv
(The 2025 1-year PUMS was not yet published when this was built: /pums/2025/ returned 404.)

Variables, checked against PUMS_Data_Dictionary_2024:
  SERIALNO  housing unit / GQ serial number (household link)
  PWGTP     person weight                     -> summed for population, never counted
  AGEP      age 0-99, top-coded by state      -> keep 18+; 90 and over pooled as 90 ("90+").
            PUMS replaces the oldest ages in each state with one stand-in value (the
            2024 file has bumps at 92-95 and nobody 97-99), so single ages above 90
            aren't real. The site treats 90 as "90 and over".
  SEX       1 male, 2 female
  MAR       1 married, 2 widowed, 3 divorced, 4 separated, 5 never married
            single = 2,3,5. Separated (4) is still legally married -> not single.
  RAC1P     1 White alone, 2 Black alone, 6 Asian alone, 9 Two or more; others -> other
  RACBLK    1 = Black alone or in combination (any Hispanic origin) -> `black` flag, the
            site's ethnicity filter ("Doesn't matter" or "Black")
  HISP      01 = not Hispanic; 02-24 = Hispanic (any race)
  ANC1P/ANC2P  detailed ancestry, first/second entry (same code list)
  PINCP     total personal income; multiplied by ADJINC / 1e6 (2024 factor 1.015250)
  ADJINC    income adjustment factor, 6 implied decimals
  RELSHIPP  relationship to reference person (20 ref, 21-24 spouse/partner,
            25 biological, 26 adopted, 27 stepchild, ...)
  SFN, SFR  subfamily number / subfamily relationship (links parents who are not the
            householder, e.g. a single mother living with her parents, to their kids)
  STATE     state FIPS code. NOTE: CLAUDE.md said `ST`; in 2024 the column is STATE.
  PAOC      presence/age of own children (women only) — used only to cross-check kids.
  NOC       is a HOUSEHOLD-file variable (household total), so it can't be used per person.

Children ("own children in household"), following the Census definition of an own child:
  a never-married son/daughter (biological, adopted or step) under 18 living in the same
  household. Children who live elsewhere are invisible to the ACS -> counted as 0.
    * householder's children (RELSHIPP 25/26/27, not themselves a subfamily child) count for
      the householder (20) and their spouse/partner (21-24);
    * subfamily children (SFR 4/5/6) count for the parent(s) of the same subfamily
      (SFR 2 = married-couple subfamily spouse, SFR 3 = lone parent).
  Capped at 4 ("4+").
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "acs2024"
ZIP = RAW / "csv_pus.zip"
DICT = RAW / "PUMS_Data_Dictionary_2024.csv"
OUT = ROOT / "site" / "data"
YEAR = 2024
AGE_TOP = 90  # ages >= this are one group in the cube (PUMS top-coding)

# Must match INCOME_STEPS in site/index.html
INCOME_STEPS = [0, 10_000, 20_000, 25_000, 30_000, 40_000, 50_000, 60_000, 75_000,
                100_000, 125_000, 150_000, 200_000, 250_000, 300_000, 400_000,
                500_000, 750_000, 1_000_000]

# Index order used in the cube's `eth` column
ETHNICITIES = ["white", "black", "asian", "middle_eastern", "mixed", "hispanic", "other"]
ETH = {k: i for i, k in enumerate(ETHNICITIES)}

# Middle Eastern / North African ancestry = the whole 400-499 block of the ACS ancestry
# code list ("North Africa and Southwest Asia"). Codes present in the 2024 dictionary:
#   400 Algerian, 402 Egyptian, 404 Libyan, 406 Moroccan, 408 Tunisian, 411 North African,
#   416 Iranian, 417 Iraqi, 419 Israeli, 421 Jordanian, 425 Lebanese, 427 Saudi Arabian,
#   429 Syrian, 431 Armenian, 434 Turkish, 435 Yemeni, 442 Kurdish, 465 Palestinian,
#   483 Assyrian, 484 Chaldean, 490 Mideast, 495 Arab, 496 Arabic, 499 Other Arab
# The list is re-read from the dictionary at run time and asserted against this one.
MENA_CODES = [400, 402, 404, 406, 408, 411, 416, 417, 419, 421, 425, 427, 429, 431,
              434, 435, 442, 465, 483, 484, 490, 495, 496, 499]

COLS = ["SERIALNO", "PWGTP", "AGEP", "SEX", "MAR", "RAC1P", "RACBLK", "HISP", "ANC1P", "ANC2P",
        "PINCP", "ADJINC", "RELSHIPP", "SFN", "SFR", "STATE", "PAOC"]


def read_dictionary():
    d = pd.read_csv(DICT, header=None, dtype=str, names=list("abcdefg"))
    anc = d[(d.a == "VAL") & (d.b == "ANC1P")]
    mena = {int(r.e): r.g for r in anc.itertuples() if 400 <= int(r.e) <= 499}
    assert sorted(mena) == MENA_CODES, f"MENA code list changed: {sorted(mena)}"
    st = d[(d.a == "VAL") & (d.b == "STATE")]
    states = {int(r.e): r.g.split("/")[-1] for r in st.itertuples()}
    for name in COLS:
        assert ((d.a == "NAME") & (d.b == name)).any(), f"{name} not in dictionary"
    return mena, states


def load_people() -> pd.DataFrame:
    parts = []
    with zipfile.ZipFile(ZIP) as z:
        for member in sorted(n for n in z.namelist() if n.startswith("psam_pus") and n.endswith(".csv")):
            with z.open(member) as f:
                for chunk in pd.read_csv(f, usecols=COLS, dtype={"SERIALNO": str}, chunksize=500_000):
                    parts.append(chunk)
            print(f"  read {member}", file=sys.stderr)
    df = pd.concat(parts, ignore_index=True)
    for c in ["SFN", "SFR", "PAOC", "ANC1P", "ANC2P", "PINCP"]:
        df[c] = df[c].fillna(0)
    return df


def own_children(df: pd.DataFrame) -> np.ndarray:
    """Own never-married children under 18 in the same household, per person."""
    kid = (df.AGEP < 18) & (df.MAR == 5)
    sub_child = df.SFR.isin([4, 5, 6])

    # Householder's children (not themselves a subfamily child)
    hh_kids = (kid & df.RELSHIPP.isin([25, 26, 27]) & ~sub_child).groupby(df.SERIALNO).sum()
    n_hh = df.SERIALNO.map(hh_kids).fillna(0).to_numpy()
    is_head_or_partner = df.RELSHIPP.isin([20, 21, 22, 23, 24]).to_numpy()

    # Subfamily children -> parents of the same subfamily
    key = df.SERIALNO + "_" + df.SFN.astype(int).astype(str)
    sf_kids = (kid & sub_child).groupby(key).sum()
    n_sf = key.map(sf_kids).fillna(0).to_numpy()
    is_sf_parent = df.SFR.isin([2, 3]).to_numpy()

    n = np.where(is_head_or_partner, n_hh, 0) + np.where(is_sf_parent, n_sf, 0)
    return np.minimum(n, 4).astype(np.int8)


def ethnicity(df: pd.DataFrame) -> np.ndarray:
    mena = df.ANC1P.isin(MENA_CODES) | df.ANC2P.isin(MENA_CODES)
    race = df.RAC1P.map({1: ETH["white"], 2: ETH["black"], 6: ETH["asian"], 9: ETH["mixed"]}) \
                   .fillna(ETH["other"])
    out = np.where(df.HISP != 1, ETH["hispanic"],
          np.where(mena, ETH["middle_eastern"], race))
    return out.astype(np.int8)


LIVING = ["alone", "roommates", "parents", "other"]


def living(df: pd.DataFrame) -> np.ndarray:
    """Living situation per person (needs every household member's row), from RELSHIPP:
      alone      head of a one-person household (ACS "lives alone")
      parents    adult son/daughter of the head: biological, adopted, step (25-27;
                 ACS "child of householder")
      roommates  roommate/housemate or other non-relative of the head (34, 36), a head with
                 no spouse/partner living with such non-relatives, or the non-institutional
                 group-quarters population (38: dorms, barracks, group homes)
      other      everyone else: with a spouse/partner, own kids, other relatives, or in an
                 institution (37). The site's filter only ever excludes the first three.
    Matches the categories of ACS table B09021 (living arrangements of adults)."""
    r = df.RELSHIPP
    size = df.groupby("SERIALNO").SERIALNO.transform("size")
    hh = df.SERIALNO
    nonrel = r.isin([34, 36]).groupby(hh).transform("any")
    partner = r.isin([21, 22, 23, 24]).groupby(hh).transform("any")
    out = np.full(len(df), 3, dtype=np.int8)                                   # other
    out[((r == 20) & nonrel & ~partner).to_numpy() | r.isin([34, 36, 38]).to_numpy()] = 1   # roommates
    out[r.isin([25, 26, 27]).to_numpy()] = 2                                   # parents
    out[((r == 20) & (size == 1)).to_numpy()] = 0                              # alone
    return out


def black_flag(df: pd.DataFrame) -> np.ndarray:
    """The site's only ethnicity filter: Black alone or in combination, of any Hispanic origin.
    RACBLK (2024 dictionary): 1 = Black or African American alone or in combination. Kept as
    its own flag, separate from the mutually exclusive `eth` group used for NHANES lookups."""
    return (df.RACBLK == 1).astype(np.int8).to_numpy()


def income_bucket(inc: np.ndarray) -> np.ndarray:
    # highest step the income is >= ; zero/negative -> bucket 0
    b = np.searchsorted(np.array(INCOME_STEPS), inc, side="right") - 1
    return np.clip(b, 0, len(INCOME_STEPS) - 1).astype(np.int8)


def wmedian(x, w):
    o = np.argsort(x)
    c = np.cumsum(w[o])
    return float(x[o][np.searchsorted(c, c[-1] / 2)])


def main():
    mena_names, state_names = read_dictionary()
    print("reading PUMS person file…", file=sys.stderr)
    df = load_people()
    print(f"  {len(df):,} person records", file=sys.stderr)

    df["kids"] = own_children(df)  # needs children rows, so before the 18+ filter
    df["living"] = living(df)      # needs every household member
    a = df[df.AGEP >= 18].copy()
    a["single"] = a.MAR.isin([2, 3, 5]).astype(np.int8)
    a["eth"] = ethnicity(a)
    a["black"] = black_flag(a)
    a["income"] = a.PINCP * a.ADJINC / 1e6
    a["inc"] = income_bucket(a.income.to_numpy())
    a["sex"] = (a.SEX - 1).astype(np.int8)  # 0 male, 1 female
    a["AGEP"] = a.AGEP.clip(upper=AGE_TOP)   # top-coded ages -> one 90+ group (cube only)

    # ---- cube ----------------------------------------------------------------------
    dims = ["sex", "AGEP", "single", "eth", "black", "inc", "kids", "living"]
    cube = (a.groupby(dims, sort=True)
              .agg(w=("PWGTP", "sum"), n=("PWGTP", "size"))
              .reset_index())
    cube_json = {
        "source": f"ACS {YEAR} 1-year PUMS, person file",
        "dims": {
            "sex": ["male", "female"],
            "age": "years 18-90; 90 = 90 and over (PUMS top-codes the oldest ages by state)",
            "age_top": AGE_TOP,
            "single": ["not single (married or separated)", "single (never married, divorced, widowed)"],
            "eth": ETHNICITIES,
            "black": "1 = Black alone or in combination with other races, any Hispanic origin (RACBLK = 1)",
            "living": LIVING,
            "inc": INCOME_STEPS,
            "kids": "own children under 18 in household, 0-4 (4 = 4+)",
        },
        "columns": ["sex", "age", "single", "eth", "black", "inc", "kids", "living", "w", "n"],
        "w": "sum of PWGTP (weighted people)",
        "n": "unweighted respondent rows",
        "data": {
            "sex": cube.sex.tolist(), "age": cube.AGEP.tolist(), "single": cube.single.tolist(),
            "eth": cube.eth.tolist(), "black": cube.black.tolist(), "inc": cube.inc.tolist(), "kids": cube.kids.tolist(), "living": cube.living.tolist(),
            "w": cube.w.tolist(), "n": cube.n.tolist(),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    cube_path = OUT / f"acs_{YEAR}_cube.json"
    cube_path.write_text(json.dumps(cube_json, separators=(",", ":")))

    # ---- state context table -------------------------------------------------------
    st = a.groupby(["STATE", "sex", "single"]).PWGTP.sum()
    states = {}
    present = set(st.index.get_level_values(0))  # PR is in the dictionary but not the US file
    for code, abbr in sorted(state_names.items()):
        if code not in present:
            continue
        g = lambda s, si: int(st.get((code, s, si), 0))
        states[abbr] = {"fips": f"{code:02d}",
                        "male": {"single": g(0, 1), "not_single": g(0, 0)},
                        "female": {"single": g(1, 1), "not_single": g(1, 0)}}
    (OUT / "state_adults.json").write_text(json.dumps(states, separators=(",", ":")))

    # ---- sanity numbers ------------------------------------------------------------
    w = a.PWGTP.to_numpy()
    total = int(w.sum())
    by_sex = a.groupby("sex").PWGTP.sum()
    checks = {"total_weighted_adults_18plus": total,
              "weighted_men": int(by_sex[0]), "weighted_women": int(by_sex[1]),
              "respondent_rows_18plus": int(len(a))}
    ya = a[(a.AGEP >= 25) & (a.AGEP <= 34)]
    for s, name in [(0, "men"), (1, "women")]:
        y = ya[ya.sex == s]
        checks[f"never_married_25_34_{name}_pct"] = round(100 * y.PWGTP[y.MAR == 5].sum() / y.PWGTP.sum(), 1)
        p = a[a.sex == s]
        checks[f"median_income_{name}_all_adults"] = round(wmedian(p.income.to_numpy(), p.PWGTP.to_numpy()))
        pi = p[p.income > 0]
        checks[f"median_income_{name}_with_income"] = round(wmedian(pi.income.to_numpy(), pi.PWGTP.to_numpy()))
    eth_share = a.groupby("eth").PWGTP.sum() / total
    checks["ethnicity_share_pct"] = {ETHNICITIES[i]: round(100 * v, 2) for i, v in eth_share.items()}
    kids_share = a.groupby("kids").PWGTP.sum() / total
    checks["kids_share_pct"] = {int(k): round(100 * v, 2) for k, v in kids_share.items()}
    single_share = a.groupby("sex").apply(lambda g: g.PWGTP[g.single == 1].sum() / g.PWGTP.sum())
    checks["single_pct"] = {"men": round(100 * single_share[0], 1), "women": round(100 * single_share[1], 1)}

    # Cross-check derived kids against PAOC (women 18+: PAOC 1-3 = has own kids < 18)
    wom = a[a.sex == 1]
    has_kids_derived = wom.PWGTP[wom.kids > 0].sum() / wom.PWGTP.sum()
    has_kids_paoc = wom.PWGTP[wom.PAOC.isin([1, 2, 3])].sum() / wom.PWGTP.sum()
    checks["women_with_own_kids_pct"] = {"derived": round(100 * has_kids_derived, 2),
                                         "PAOC": round(100 * has_kids_paoc, 2)}

    # ---- meta ----------------------------------------------------------------------
    raw_bytes = cube_path.stat().st_size
    gz_bytes = len(gzip.compress(cube_path.read_bytes(), 9))
    meta = {
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "acs": {
            "source": "U.S. Census Bureau, American Community Survey 2024 1-year PUMS (person file, US)",
            "url": "https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/csv_pus.zip",
            "dictionary": "PUMS_Data_Dictionary_2024",
            "year": YEAR,
            "files": {"cube": cube_path.name, "states": "state_adults.json"},
            "cube_cells": int(len(cube)),
            "cube_bytes": raw_bytes, "cube_bytes_gzip": gz_bytes,
            "ethnicity_order": ETHNICITIES,
            "ethnicity_rule": "hispanic if HISP!=01; else middle_eastern if ANC1P/ANC2P in "
                              "mena_ancestry_codes; else RAC1P 1 white, 2 black, 6 asian, 9 mixed; else other",
            "mena_ancestry_codes": {str(k): v for k, v in sorted(mena_names.items())},
            "single_rule": "MAR 2 widowed, 3 divorced, 5 never married = single; 1 married, 4 separated = not single",
            "income_rule": "PINCP * ADJINC / 1e6, bucket = highest INCOME_STEPS value <= income; <=0 -> bucket 0",
            "income_steps": INCOME_STEPS,
            "kids_rule": "own never-married children under 18 living in the same household "
                         "(householder + spouse/partner via RELSHIPP 25/26/27; subfamily parents via SFN/SFR); capped at 4",
            "universe": "all persons 18+, household and group-quarters population",
            "checks": checks,
        },
    }
    meta_path = OUT / "meta.json"
    old = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    old.update(meta)
    meta_path.write_text(json.dumps(old, indent=2))

    print(json.dumps({"cube_cells": len(cube), "cube_bytes": raw_bytes, "cube_bytes_gzip": gz_bytes,
                      **checks}, indent=2))


if __name__ == "__main__":
    main()
