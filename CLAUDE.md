# Standards Calc — project guide for Claude Code

A dating-standards calculator. People set filters (age, income, height…) and see how many
real people match: "1 in N", a delusion score out of 10, a filter breakdown and a share card.

The UI is finished and lives in `site/index.html`, a single self-contained file. It currently
runs on a **demo model**: rough national guesses that treat every filter as independent.
**The job now is to replace the demo model with real survey data.** Do not redesign the UI.

---

## Ground rules (read before every task)

1. **Never multiply national percentages together.** Age, income, marital status, kids and
   ethnicity are correlated. Count real survey respondents who match *all* filters at once,
   using their survey weights.
2. **Never invent or estimate a number** that should come from data. If a figure can't be
   computed, say so in the UI ("not available") instead of guessing.
3. **Everything runs in the browser.** No server and no live API calls at runtime. The site
   promises "Anonymous · stays on your device". The data ships as a static file.
4. **Keep the filter state shape exactly as it is** (listed below). The UI depends on it.
5. **Raw downloads never go in git.** `data/raw/` is gitignored. Only the small, processed
   files in `site/data/` are committed.
6. **Verify variable names and codes against the official data dictionary** before using
   them. The names below are believed correct but must be checked for the exact year used.
7. Work in the phases below, one at a time. Show results and **commit after each phase**.

---

## Folder layout to create

```
standards-calc/
  CLAUDE.md
  site/
    index.html          ← the finished UI (move the provided file here)
    data/               ← processed, browser-ready data (committed)
  pipeline/             ← Python scripts that build site/data from raw downloads
    01_acs.py
    02_nhanes.py
    03_validate.py
  data/raw/             ← big downloads (gitignored)
  .gitignore            ← include data/raw/
```

Preview the site with a local server, because `fetch()` doesn't work when the file is
opened by double-clicking:
`cd site && python -m http.server 8000`, then open http://localhost:8000

---

## Filter state (the contract between UI and data)

The UI builds this object (see the "Filter state" panel at the bottom of the page):

| Field | Values | Used for |
|---|---|---|
| `gender` | `male` / `female` | the user, not the filter |
| `seeking` | `men` / `women` / `either` | **odds** (sex) |
| `ethnicities` | `"any"` or array of `white`, `black`, `asian`, `middle_eastern`, `mixed`, `hispanic` (Phase 4) | **odds** (OR logic) |
| `ageMin`, `ageMax` | 18–99 | **odds** |
| `excludeMarried` | bool | **odds** |
| `kidsMin`, `kidsMax` | 0–4 (4 means "4 or more") | **odds** |
| `heightMinIn`, `heightMaxIn` | inches 59–88, or `null` = no limit | **odds** (via NHANES) |
| `heightUnit` | `imperial` / `metric` | display only |
| `minIncome` | one of the `INCOME_STEPS` values (0 … 1,000,000) | **odds** |
| `preferredIncome` | ≥ `minIncome` | strictness score only |
| `excludeObese` | bool (BMI ≥ 30) | **odds** (via NHANES) |
| `region` | `us` (others later) | dataset choice |
| `distance` | `metro` / `state` / `country` / `anywhere` | context line only, never the odds |
| `resultTone` | `stats` / `playful` / `brutal` | copy only |
| `looksImportance`, `emotionalRegulation` | 0–10 | strictness only |
| `behavior.*`, `skills.*` | `any` / `preferred` / `required` | strictness only |
| `dealbreakers.*` | bool | strictness only |
| `bodyTypes` | `"any"` or array of ids (`w01`–`w13`, `m01`–`m11`) | strictness only (see below) |
| `sizePreference` | `any` / `average_plus` / `above_average` | strictness only for now |

`INCOME_STEPS` in index.html: 0, 10k, 20k, 25k, 30k, 40k, 50k, 60k, 75k, 100k, 125k, 150k,
200k, 250k, 300k, 400k, 500k, 750k, 1M.

---

## Data sources

### A. ACS PUMS: the main pool (sex, age, marital, ethnicity, income, kids)
- American Community Survey **2024 1-year Public Use Microdata Sample, person file, whole US**
  (`csv_pus.zip`). Released Dec 4, 2025. No account or API key needed.
  Docs and data dictionary: https://www.census.gov/programs-surveys/acs/microdata/documentation.html
  Download area: https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/
- If the 2025 1-year PUMS has been released by the time you run this, use it instead.
- Several GB unzipped. Read only the columns you need, in chunks.

Variables (confirm in the 2024 dictionary):

| Need | Variable | Recode |
|---|---|---|
| Person weight | `PWGTP` | sum this, never count rows for population |
| Age | `AGEP` | keep 18+ |
| Sex | `SEX` | 1 = male, 2 = female |
| Marital status | `MAR` | 1 married → not single; 2 widowed, 3 divorced, 5 never married → single; 4 separated → **not** single (still legally married) |
| Race | `RAC1P` | 1 White, 2 Black, 6 Asian, 9 Two or more → `mixed`, others → `other` |
| Hispanic origin | `HISP` | 01 = not Hispanic; anything else = Hispanic |
| Ancestry | `ANC1P`, `ANC2P` | use to identify Middle Eastern / North African (see below) |
| Income | `PINCP` × (`ADJINC` / 1,000,000) | total personal income, inflation-adjusted. **Apply ADJINC.** |
| Own children | see "Children" below | 0, 1, 2, 3, 4+ |
| State | `STATE` | context table only (2024 file uses `STATE`, not `ST`) |

**Ethnicity groups must be mutually exclusive.** Assign each person exactly one:
1. `hispanic` if `HISP` ≠ 01 (any race)
2. else `middle_eastern` if `ANC1P` or `ANC2P` is a Middle Eastern / North African ancestry
   (Arab, Egyptian, Iranian, Iraqi, Lebanese, Syrian, Palestinian, Moroccan, Algerian, Turkish
   etc. Take the exact code list from the dictionary's ancestry codes, and list the codes used
   in a comment). The ACS has no MENA race category, so this is the standard workaround.
3. else `white`, `black`, `asian`, `mixed` from `RAC1P`
4. else `other`

> **Decided:** add a Hispanic/Latino chip (`hispanic`) in Phase 4. About 18% of US adults
> (ACS 2024), who were otherwise only counted under "Doesn't matter".

**Children:** ACS only knows about children who **live in the same household**. A parent
whose kids live elsewhere counts as 0. Derive "own children in household" per adult by
linking people within a household (`SERIALNO`) via the relationship variable (`RELSHIPP`:
biological, adopted and stepchildren of the householder count toward the householder and
their spouse/partner). (Checked: `NOC` is household-level and `PAOC` is women-only
presence, so neither fits; `01_acs.py` derives the count via `RELSHIPP` plus subfamily `SFN`/`SFR`,
under-18 never-married children, matching the Census own-child definition.) Cap at 4 ("4+"). Add this note under the kids slider:
"Counts children living with them."

### B. NHANES: height and obesity (not in the ACS)
- CDC National Health and Nutrition Examination Survey, **August 2021–August 2023** cycle
  (file suffix `_L`). Files: demographics `DEMO_L.xpt`, body measures `BMX_L.xpt`.
  https://wwwn.cdc.gov/nchs/nhanes/ (no account needed). Read with `pandas.read_sas`.
- Variables (confirm): `RIAGENDR` sex, `RIDAGEYR` age, `RIDRETH3` race/Hispanic origin,
  `BMXHT` standing height (cm), `BMXBMI` BMI, `WTMEC2YR` exam weight (use it, always).
- Build lookup tables by **sex × age band × race group**:
  - age bands: 18–29, 30–39, 40–49, 50–59, 60+
  - race groups mapped from `RIDRETH3`: Mexican American + Other Hispanic → `hispanic`,
    NH White → `white` (also used for `middle_eastern`), NH Black → `black`,
    NH Asian → `asian`, Other/multiracial → `mixed` and `other`
  - for each group: a **weighted height CDF** in whole inches 59–88, and the
    **weighted share with BMI ≥ 30**
  - if a group has fewer than 100 respondents, merge neighbouring age bands and note it

### C. Penis size (only if Nick decides it should affect the odds)
Veale et al., BJU International 2015, erect length mean 13.12 cm, SD 1.66 cm (n = 692).
"Above average only" ≈ 50%, "Average or above" = share above mean − 0.5 SD ≈ 69%.
**Leave as strictness-only unless Nick says otherwise.**

### Not from any dataset (keep as strictness only)
Body types, all of Step 2 (behaviour, calm under stress, dealbreakers), skills, looks
importance, preferred income. No survey measures these. Do not invent proxies.
Body types could later get a fun stat from the site's own anonymous usage
("8% of people picked only BBL"), but that needs analytics and is out of scope.

---

## The method

### Build step (Python, in `pipeline/`)
Aggregate ACS adults into a **weighted cube** (state dropped, because location never changes
the odds):

```
dimensions: sex(2) × age(18..90, 90 = 90+) × single(2) × ethnicity group(7) × income bucket(19) × kids(0..4)
values:     sum of PWGTP (population), count of rows (sample size)
```
- Income bucket = the highest `INCOME_STEPS` value that the person's adjusted income is ≥.
  Negative or zero income goes in bucket 0.
- Ages 90+ are pooled as 90: PUMS top-codes the oldest ages by state, so single ages above
  90 aren't real. At runtime a range reaching 90 includes the 90+ group, and a youngest age
  above 90 shows "not available".
- Keep only non-empty cells. Write `site/data/acs_2024_cube.json` (or a compact binary or
  columnar format). **Target under 2 MB gzipped.** Report the actual size.
- Write `site/data/nhanes_2021_23.json` with the height CDFs and obesity shares.
- Write `site/data/state_adults.json`: weighted adults by state × sex × single, for the
  local context line.
- Write `site/data/meta.json`: sources, years, build date, ancestry codes used, total
  weighted adults (for sanity checks).

### Runtime (JavaScript, in `index.html`)
Replace the block marked `/* ================= Demo model (replace with real census data)`
with code that:
1. Loads the JSON files once, with a loading state, and handles errors.
2. For each sex being sought, loops over cube cells that match age range, single status,
   ethnicity selection, `income bucket ≥ minIncome` and kids range.
3. For each matching cell, multiplies its weight by
   P(height in range | sex, age band, race group) × P(not obese | same), if those
   filters are on. These come from the NHANES tables. (Implemented with a joint
   `height_le_not_obese` table, P(height in range AND BMI < 30), so the two are never
   multiplied as if independent.)
4. pool = Σ weights for the sought sex (all adults 18+); matches = Σ adjusted weights.
   N = pool / matches.
5. **Filter breakdown:** for each filter, report "keeps X%". Compute this as
   matches-with-all-filters ÷ matches-without-that-filter, so it reflects real overlaps.
   Sort the biggest cut first (the UI already does).
6. **"What if you relaxed" suggestions:** rerun the real calculation with one filter
   loosened. The UI logic already does this through `compute()`.
7. **Low confidence:** if the matching unweighted row count is under 30, show
   "Very rare: estimate is rough" beside the number.
8. Keep `strictnessExtras()` and the score formula, but feed them the real N.
9. Remove the "Demo numbers" banner and add a small "Sources" line linking a
   methodology section.

Keep `computeFor()` / `compute()` returning the same shape the results UI already reads:
`{ p, N, people, factors[{key,label,note,v}], extras, score }`.

---

## Validation (`pipeline/03_validate.py`) — must pass before calling it done
- Total weighted US adults 18+ ≈ the official ACS 2024 figure (about 260 million; report the exact value).
- Share of 25–34-year-olds never married, by sex, matches the ACS published table (S1201)
  within ~1 point.
- Median personal income by sex is in line with published ACS figures.
- NHANES weighted adult obesity ≈ 40% (CDC published 40.3% for adults 20+, 2021–2023).
- Mean height: men ≈ 69 in, women ≈ 63.5 in.
- Print a table of these checks with pass/fail.

---

## Phases (do one, show the result, commit)
1. **Setup:** folders, `.gitignore`, `git init`, move `index.html` into `site/`.
2. **ACS pipeline:** download, recode, build the cube, report the size, sanity-check totals.
3. **NHANES pipeline:** download, build the height and obesity lookups.
4. **Wire up:** replace the demo model in `index.html`, add loading, error and low-confidence states.
5. **Validate:** run `03_validate.py`, fix anything off, then click through the site.
6. **Later:** methodology page, Hispanic/Latino chip (if approved), other countries,
   hosting (Cloudflare Pages / Vercel / Netlify), share-image download.

## Data credits to show on the site
- U.S. Census Bureau, American Community Survey 2024 1-year PUMS
- CDC / NCHS, National Health and Nutrition Examination Survey, Aug 2021–Aug 2023
- Veale et al. (2015), BJU International (only if the size factor is used)
