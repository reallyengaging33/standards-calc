"""
Map every city in US_STATES (site/index.html) to the 2020 PUMAs that make up its area.

PUMS only identifies states and PUMAs (areas of 100k+ people), never cities. Each city id is
mapped to an area defined by whole counties (an OMB metro/micro area, a metropolitan
division, or an explicit county list), and a PUMA belongs to the area when the MAJORITY of
its 2020 population lives inside it. Population overlap comes from 2020 Census tract
populations, using the Census tract→PUMA relationship file (tracts nest in counties and in
PUMAs, so tract populations give exact PUMA × county overlap).

If no PUMA is majority-inside (a small area like Pierre, SD, whose PUMA also covers a large
rural region), the PUMA holding the largest share of the area's people is used and the city
is flagged `approximate`, so the site can say so.

Inputs (data/raw/geo/, no account needed):
  2020_Census_Tract_to_2020_PUMA.txt
      https://www2.census.gov/geo/docs/maps-data/data/rel2020/2020_Census_Tract_to_2020_PUMA.txt
  list1_2023.xlsx, list2_2023.xlsx   OMB July 2023 CBSA delineation + principal cities
      https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/
  list1_2020.xls, list2_2020.xls     OMB March 2020 delineation (Connecticut only: the 2023
      file uses CT's new planning regions, while 2020 tracts/PUMAs use the old counties)
  tractpop/<statefips>.json          2020 Census P.L. 94-171 table P1 (total population)
      by tract, from https://data.census.gov/api/access/data/table?id=DECENNIALPL2020.P1&g=040XX00US<ss>$1400000
Output: data/raw/geo/city_areas.json (read by 05_local_cubes.py)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "data" / "raw" / "geo"

FIPS = {'AL': '01', 'AK': '02', 'AZ': '04', 'AR': '05', 'CA': '06', 'CO': '08', 'CT': '09', 'DE': '10',
        'DC': '11', 'FL': '12', 'GA': '13', 'HI': '15', 'ID': '16', 'IL': '17', 'IN': '18', 'IA': '19',
        'KS': '20', 'KY': '21', 'LA': '22', 'ME': '23', 'MD': '24', 'MA': '25', 'MI': '26', 'MN': '27',
        'MS': '28', 'MO': '29', 'MT': '30', 'NE': '31', 'NV': '32', 'NH': '33', 'NJ': '34', 'NM': '35',
        'NY': '36', 'NC': '37', 'ND': '38', 'OH': '39', 'OK': '40', 'OR': '41', 'PA': '42', 'RI': '44',
        'SC': '45', 'SD': '46', 'TN': '47', 'TX': '48', 'UT': '49', 'VT': '50', 'VA': '51', 'WA': '53',
        'WV': '54', 'WI': '55', 'WY': '56'}

# Cities whose name isn't a single OMB principal city, or that mean part of a metro.
#   cbsa:     whole metro/micro area     division: OMB metropolitan division
#   counties: explicit 5-digit county FIPS list
#   in_state: only the part of the area inside the chosen state ("... (DC area)")
MANUAL = {
    ("AR", "Northwest Arkansas (Fayetteville / Rogers)"): {"cbsa": "22220"},
    ("CA", "Los Angeles"): {"division": "31084"},                       # LA County (Orange County is separate below)
    ("CA", "San Francisco Bay Area (SF / Oakland)"): {"cbsa": "41860"},
    ("CA", "Orange County (Anaheim / Irvine)"): {"division": "11244"},
    ("CA", "Inland Empire (Riverside / San Bernardino)"): {"cbsa": "40140"},
    ("GA", "Augusta"): {"cbsa": "12260"},
    ("GA", "Macon"): {"cbsa": "31420"},
    ("GA", "Athens"): {"cbsa": "12020"},
    ("HI", "Honolulu"): {"cbsa": "46520"},
    ("ID", "Boise"): {"cbsa": "14260"},
    ("IL", "Champaign-Urbana"): {"cbsa": "16580"},
    ("IN", "Indianapolis"): {"cbsa": "26900"},
    ("KS", "Kansas City area (Overland Park / Olathe)"): {"cbsa": "28140", "in_state": True},
    ("KY", "Louisville"): {"cbsa": "31140"},
    ("KY", "Lexington"): {"cbsa": "30460"},
    ("MD", "DC suburbs (Silver Spring / Bethesda)"): {"cbsa": "47900", "in_state": True},
    ("MD", "Annapolis"): {"counties": ["24003"]},                       # Anne Arundel County
    ("MA", "Cape Cod"): {"cbsa": "12700"},
    ("NH", "Portsmouth"): {"division": "40484"},                       # Rockingham + Strafford (Seacoast)
    ("NY", "New York City"): {"counties": ["36005", "36047", "36061", "36081", "36085"]},
    ("NY", "Long Island"): {"counties": ["36059", "36103"]},
    ("OK", "Norman"): {"counties": ["40027"]},                          # Cleveland County
    ("RI", "Newport"): {"counties": ["44005"]},                         # Newport County
    ("TN", "Nashville"): {"cbsa": "34980"},
    ("VT", "Montpelier"): {"cbsa": "12740"},                            # Barre micro area
    ("VA", "Northern Virginia (DC area)"): {"cbsa": "47900", "in_state": True},
    # Connecticut: 2020 PUMAs use the old counties, where Waterbury sits inside New Haven County.
    # Match OMB 2023 instead (Waterbury-Shelton = Naugatuck Valley), using PUMA names from TIGER
    # tl_2022_09_puma20: 20701 Waterbury Town, 20702/20703 Naugatuck Valley North/South.
    ("CT", "Waterbury"): {"pumas": ["0920701", "0920702", "0920703"], "label": "Naugatuck Valley (Waterbury-Shelton)"},
    ("CT", "New Haven"): {"cbsa": "35300", "exclude_pumas": ["0920701", "0920702", "0920703"]},
}
IN_STATE_HINT = re.compile(r"\((?:[A-Z]{2,4}|Philadelphia|NYC|DC) area\)")


def slug(t: str) -> str:   # same as slug() in index.html
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", t.lower().replace("*", "")))


def norm(t: str) -> str:
    t = re.sub(r" (city|town|village|municipality|CDP|borough)$", "", t)
    return re.sub(r"[^a-z]", "", t.lower().replace("saint", "st").replace("st.", "st"))


def us_states():
    html = (ROOT / "site" / "index.html").read_text()
    return json.loads(re.search(r"var US_STATES = (\[.*?\]);\n", html).group(1))


def load():
    tr = pd.read_csv(GEO / "2020_Census_Tract_to_2020_PUMA.txt", dtype=str, encoding="utf-8-sig")
    tr = tr[tr.STATEFP.isin(FIPS.values())].copy()   # 50 states + DC (drop PR, island areas)
    tr["tract"] = tr.STATEFP + tr.COUNTYFP + tr.TRACTCE
    tr["county"] = tr.STATEFP + tr.COUNTYFP
    tr["puma"] = tr.STATEFP + tr.PUMA5CE
    pops = {}
    for f in sorted((GEO / "tractpop").glob("*.json")):
        d = json.loads(f.read_text())["response"]["data"]
        h = d[0]; gi, pi = h.index("GEO_ID"), h.index("P1_001N")
        for r in d[1:]:
            pops[r[gi][-11:]] = int(r[pi])
    tr["pop"] = tr.tract.map(pops)
    assert tr["pop"].notna().all(), "tract without population"
    cty = tr.groupby(["puma", "county"])["pop"].sum().reset_index()

    def delin(path, header):
        d = pd.read_excel(path, header=header, dtype=str).dropna(subset=["FIPS County Code"])
        d["county"] = d["FIPS State Code"].str.zfill(2) + d["FIPS County Code"].str.zfill(3)
        return d
    d23 = delin(GEO / "list1_2023.xlsx", 2)
    d20 = delin(GEO / "list1_2020.xls", 2)
    deli = pd.concat([d23[d23["FIPS State Code"] != "09"], d20[d20["FIPS State Code"] == "09"]])
    pc23 = pd.read_excel(GEO / "list2_2023.xlsx", header=2, dtype=str)
    pc20 = pd.read_excel(GEO / "list2_2020.xls", header=2, dtype=str)
    pc = pd.concat([pc23[pc23["FIPS State Code"] != "09"], pc20[pc20["FIPS State Code"] == "09"]])
    pc = pc.dropna(subset=["Principal City Name"]).copy()
    pc["n"] = pc["Principal City Name"].map(norm)
    return cty, deli, pc


def main():
    cty, deli, pc = load()
    puma_pop = cty.groupby("puma")["pop"].sum()
    titles = deli.groupby("CBSA Code")["CBSA Title"].first().to_dict()
    out, notes = {}, []
    for st in us_states():
        sf = FIPS[st["code"]]
        for raw in st["cities"]:
            name = raw.replace("*", "")
            spec = MANUAL.get((st["code"], name))
            if spec is None:
                first = re.split(r" / | \(|, ", name)[0]
                hit = pc[(pc["FIPS State Code"] == sf) & (pc.n == norm(first))]
                assert len(hit) == 1, f"no unique principal city for {st['code']} {name}: {len(hit)}"
                spec = {"cbsa": hit["CBSA Code"].iloc[0]}
                if IN_STATE_HINT.search(name):
                    spec["in_state"] = True
            if "cbsa" in spec:
                counties = set(deli[deli["CBSA Code"] == spec["cbsa"]].county)
                if not counties:
                    raise SystemExit(f"CBSA {spec['cbsa']} not found for {name}")
                label = titles[spec["cbsa"]]
            elif "division" in spec:
                rows = deli[deli["Metropolitan Division Code"] == spec["division"]]
                counties = set(rows.county); label = rows["Metropolitan Division Title"].iloc[0]
            elif "pumas" in spec:
                counties = set(cty[cty.puma.isin(spec["pumas"])].county); label = spec["label"]
            else:
                counties = set(spec["counties"]); label = "counties " + ", ".join(sorted(counties))
            if spec.get("in_state"):
                counties = {c for c in counties if c.startswith(sf)}
                label += f" (part in {st['code']})"
            assert counties or "pumas" in spec, f"empty area for {name}"

            inside = cty[cty.county.isin(counties)].groupby("puma")["pop"].sum()
            share = (inside / puma_pop[inside.index])
            pumas = sorted(share[share > 0.5].index)
            if "pumas" in spec:
                pumas = sorted(spec["pumas"])
                inside = cty[cty.puma.isin(pumas)].groupby("puma")["pop"].sum()
                counties = set(); area_pop_override = int(inside.sum())
            pumas = [x for x in pumas if x not in spec.get("exclude_pumas", [])]
            approx = False
            if not pumas:                                   # area smaller than any PUMA
                pumas = [inside.idxmax()]; approx = True
            area_pop = area_pop_override if "pumas" in spec else int(cty[cty.county.isin(counties)]["pop"].sum())
            if "exclude_pumas" in spec:
                area_pop -= int(cty[cty.county.isin(counties) & cty.puma.isin(spec["exclude_pumas"])]["pop"].sum())
            puma_total = int(puma_pop[pumas].sum())
            covered = int(inside[pumas].sum())
            out[f"{st['code']}-{slug(raw)}"] = {
                "state": st["code"], "name": name, "area": label, "spec": spec,
                "counties": sorted(counties), "pumas": pumas, "approximate": approx,
                "area_pop_2020": area_pop, "pumas_pop_2020": puma_total,
                "area_covered_pct": round(100 * covered / area_pop, 1),
                "pumas_inside_pct": round(100 * covered / puma_total, 1)}
            if approx:
                notes.append(f"{st['code']} {name}: approximate (PUMA {pumas[0]}; "
                             f"{round(100 * covered / puma_total)}% of its people are in {label})")
    (GEO / "city_areas.json").write_text(json.dumps(out, indent=1))
    n_approx = sum(v["approximate"] for v in out.values())
    print(f"{len(out)} cities mapped; {n_approx} approximate")
    print("\n".join(notes))
    worst = sorted(out.items(), key=lambda kv: kv[1]["area_covered_pct"])[:8]
    print("\nlowest coverage of the intended area (non-approximate):")
    for k, v in worst:
        if not v["approximate"]:
            print(f"  {k:40} {v['area'][:45]:45} covered {v['area_covered_pct']}%  pumas inside {v['pumas_inside_pct']}%")


if __name__ == "__main__":
    main()
