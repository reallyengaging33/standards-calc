# Your Delulu Number — project guide for Claude Code

A dating-standards calculator for Black men and women, mostly 25–35. People set filters (age, income, height…) and see how many
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

### If running in Claude Code on the web (cloud session)
- The environment needs network access to `census.gov`, `www2.census.gov` and
  `wwwn.cdc.gov` (and `cdc.gov`). If a download fails with "host not allowed", stop and tell
  Nick to change the environment's network access. Don't try to work around it.
- Disk space is limited. Download the ACS person file in its split parts if available, process
  each part in chunks, and **delete the raw file once its part of the cube is built**.
- There's no localhost preview for Nick. In Phase 1, add a GitHub Actions workflow that
  publishes `site/` to **GitHub Pages**, so every merged change is viewable at a public URL.
  Tell Nick to switch Pages on (repo Settings → Pages → Source: GitHub Actions).
- Work is pushed to a branch. Tell Nick when a phase is ready to merge.

---

## Filter state (the contract between UI and data)

The UI builds this object (the `state` variable in index.html; log it with `console.log(state)` to inspect it):

| Field | Values | Used for |
|---|---|---|
| `gender` | `male` / `female` | the user, not the filter |
| `seeking` | `men` / `women` / `either` | **odds** (sex) |
| `ethnicities` | `"any"` or `["black"]` (the only option in the UI). **Default `["black"]`** | **odds** |
| `ageMin`, `ageMax` | 18–99 | **odds** |
| `excludeMarried` | bool | **odds** |
| `kidsMin`, `kidsMax` | 0–4 (4 means "4 or more"). **Default 0–0 (no kids)** | **odds** |
| `heightMinIn`, `heightMaxIn` | inches 59–88, or `null` = no limit | **odds** (via NHANES) |
| `heightUnit` | `imperial` / `metric` | display only |
| `minIncome` | one of the `INCOME_STEPS` values (0 … 1,000,000) | **odds** |
| `preferredIncome` | ≥ `minIncome` | Lifestyle rarity multiplier (uses real income shares) |
| `excludeObese` | bool (BMI ≥ 30). **Removed from the UI (Nick's decision), always `false`.** The data and math are kept, so it could come back | **odds** (via NHANES) |
| `region` | always `us` for now | dataset choice |
| `distance` | `metro` / `state` / `country` | **odds**: sets the search area (see `searchArea()` in index.html) |
| `usState` | 2-letter code (`GA`) or `null` | **odds**: the state for `state`/`metro` searches |
| `city`, `cityName` | city id and display name from `US_STATES` in index.html (e.g. `atlanta` / `Atlanta`) | **odds**: the metro for `metro` searches |

Search area: `metro` + state + city → that metro area; `metro` or `state` with only a state →
that state; otherwise the whole US. There is no free-text city: PUMS can't identify towns.
| `resultTone` | `stats` ("Water") / `playful` ("On the rocks", default) / `brutal` ("No chaser") | copy only |
| `looksImportance` | 0–10 | power on the Physical rarity multiplier (0.5 to 1.5) |
| `emotionalRegulation` | 0–10 | UI label "Emotional Intelligence & Healthy Attachment". power on the Character rarity multiplier (0.5 to 1.5). Key name kept for compatibility |
| `behavior.*`, `skills.*` | `any` / `preferred` / `required` | pass-rate multipliers (Character / Lifestyle) |
| `dealbreakers.*` | `heavyPartying` (out 4–5 nights a week, 85%), `nightlife` (works in nightlife, 93%), `unreliable` (70%), `multipleCoParents` (multiple BMs/BDs, 85%; ignored when `kidsMax` ≤ 1) | pass-rate multipliers (Character) |
| `living` | array of `alone` / `roommates` / `parents` they can have (default all, at least one) | **odds** (census; Nick agreed). Cube column `living`: 0 alone (head of a 1-person household), 1 roommates (roommate/other non-relative of the head, a head with no partner living with non-relatives, or non-institutional group quarters), 2 parents (adult child of the head, RELSHIPP 25–27), 3 other (never excluded). Matches ACS B09021. No longer in `standardsRarity()` |
| `car` | `"any"` (A to B is fine) or an array of accepted rides: `newer` (2020+), `luxury` (BMW, Mercedes, Audi), `foreign_luxury` (Maserati, Porsche, G-Wagon, Bentley) | pass-rate multiplier (Lifestyle): sum of accepted shares 0.5 / 0.22 / 0.10 (capped at 1) |
| `debt` | `any` / `after_marriage` / `has_plan` / `debt_free` | pass-rate multiplier (Lifestyle): 100% / 90% / 75% / 25% |
| `handy` | `any` / `pays` / `basics` / `very` (asked only when looking for men) | pass-rate multiplier (Lifestyle): 100% / 80% / 60% / 15% |

The page has **4 steps + results**: 1 The Basics (census filters), 2 Preference Filter,
3 Physical (body types, size), 4 Lifestyle & Skills (cooks, active, tidy, debt, handy).
Results are view 5. Score label in the UI is "Delulu score".
| `bodyTypes` | `"any"` or array of ids (`w01`–`w13`, `m01`–`m11`) | Physical multiplier: rarity-weighted share of builds accepted (rarity values in `BODY_SETS`) |
| `sizePreference` | `any` / `average_plus` / `above_average` | Physical multiplier (69% / 50% pass) |

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
| State | `STATE` | state cubes (2024 files use `STATE`, not `ST`) |

**Ethnicity groups must be mutually exclusive.** Assign each person exactly one:
1. `hispanic` if `HISP` ≠ 01 (any race)
2. else `middle_eastern` if `ANC1P` or `ANC2P` is a Middle Eastern / North African ancestry
   (Arab, Egyptian, Iranian, Iraqi, Lebanese, Syrian, Palestinian, Moroccan, Algerian, Turkish
   etc. Take the exact code list from the dictionary's ancestry codes, and list the codes used
   in a comment). The ACS has no MENA race category, so this is the standard workaround.
3. else `white`, `black`, `asian`, `mixed` from `RAC1P`
4. else `other`

> **Ethnicity filter in the UI is only "Doesn't matter" or "Black"** (Nick's decision; do not
> add other chips). For the Black filter, count people who are **Black alone or in combination,
> of any Hispanic origin** (use `RACBLK` = 1 if present in the dictionary, otherwise `RAC1P` = 2
> or a multiracial code that includes Black). Store this as its own boolean in the cube
> (`black`), separate from the mutually exclusive group used for NHANES lookups.
> **As built:** `RACBLK` exists in both the 2024 and 2020–2024 dictionaries (1 = Black alone
> or in combination); every cube has a `black` column from it (36.1M adults, 13.5%, in 2024).

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

### Not from any dataset (rarity multipliers on top of the census odds)
Body types, all of Step 2 (behavior, Emotional Intelligence, dealbreakers), skills, debt,
looks importance, preferred income. No survey measures these. They become **rarity multipliers on top of the census
odds** (real-world odds), never the census odds themselves. Body-type rarity multipliers in `BODY_SETS` are Nick's
assumptions. Do not invent proxies or replace them with "data".
Body types could later get a fun stat from the site's own anonymous usage
("8% of people picked only BBL"), but that needs analytics and is out of scope.

---

## The method

### Build step (Python, in `pipeline/`)
Aggregate ACS adults into a **weighted cube** (state dropped, because location never changes
the odds):

```
dimensions: sex(2) × age(18..90, 90 = 90+) × single(2) × ethnicity group(7) × black(2) × income bucket(19) × kids(0..4) × living(4)
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
- **Local cubes (location filters the odds).** Build the same cube for every state and for
  every city in `US_STATES` (index.html), as separate files loaded on demand:
  `site/data/state/GA.json`, `site/data/metro/atlanta.json`, etc.
  - PUMS identifies only states and PUMAs (areas of 100k+ people), not cities. Map each
    city id to its metro area (CBSA) with a PUMA-to-CBSA crosswalk (Census relationship
    files or MABLE Geocorr), assigning PUMAs by majority population overlap. Record the
    CBSA and PUMAs used for each city id in `meta.json`. Names like
    "Northern Virginia (DC area)" mean the part of that metro inside the chosen state.
  - Use the **ACS 5-year PUMS (2020–2024)** for state and metro cubes (bigger sample, so
    small metros like Pierre or Juneau still work). Keep the national cube on the 1-year file.
  - Keep every file small (target under 500 KB each, gzipped). The browser only loads the
  one it needs.
  - **As built** (`04_crosswalk.py`, `05_local_cubes.py`): metro files are
    `site/data/metro/<ST>-<city id>.json`, because city ids repeat across states (Portland,
    Columbus, Springfield…). Areas are whole counties (OMB July 2023 CBSAs, metropolitan
    divisions such as LA County / Orange County, or explicit counties such as NYC's five
    boroughs); a PUMA is in the area when most of its 2020 population is (2020 tract
    populations + the tract→PUMA file). Cities smaller than any PUMA (Juneau, Pierre, Helena,
    Carson City, Montpelier, Laramie) use the surrounding PUMA and are flagged `approximate`;
    the site says so. Connecticut uses the 2020 delineation (its 2020 PUMAs use the old
    counties), with Waterbury = its three Naugatuck Valley PUMAs. The 2020–2024 dictionary has
    no ancestry code 408 (Tunisian); every other MENA code matches the 1-year file.
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
8. **Census odds × standards = real-world odds.** Keep `standardsRarity()` and the `RARITY`
   config exactly as they are. Their pass rates are deliberate estimates, not data, so don't
   "fix" them with numbers. The only change: the **preferred income** pass rate must use the
   real income shares from the cube (share earning ≥ preferred ÷ share earning ≥ minimum,
   for the matching group), not the demo table.
   - Census odds: N from the cube (shown as "1 in N" and as a % of the pool)
   - Each pillar's rarity = 1 / (product of its pass rates). Physical is raised to the power
     of the Looks multiplier (0.5 to 1.5), Character to the Emotional Intelligence multiplier
   - Real-world odds: N_adj = N × Physical × Character × Lifestyle (also shown as a %)
   - Delusion score = 2.1 × log10(N_adj), capped at 10. This splits exactly into census
     points + each pillar's points, which the equation panel shows.
9. **Location filters the census odds.** Use `searchArea(state)` to pick the cube: the metro
   file, the state file, or the national cube. Load it on demand (show a loading state), and
   run the same calculation on it. Update `#r-local`: remove the "Demo: showing national
   figures" text, and when the local sample is thin, show the low-confidence note instead.
   Add a "What if" idea for metro searches: "Search your whole state" (rerun on the state cube).
10. Remove the "Demo numbers" banner and add a small "Sources" line linking a
   methodology section.

Keep `computeFor()` / `compute()` returning the same shape the results UI already reads:
`{ p, N, people, factors[{key,label,note,v}], std, Nadj, pAdj, peopleAdj, censusPts, total, score }`.

---

## Validation (`pipeline/03_validate.py`) — must pass before calling it done
- Total weighted US adults 18+ ≈ the official ACS 2024 figure (about 260 million; report the exact value).
- Share of 25–34-year-olds never married, by sex, matches the ACS published tables within
  ~1 point (B12002 / B01001: S1201 has no 25–34 age group).
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
6. **Later:** methodology page,
   hosting (Cloudflare Pages / Vercel / Netlify).

Sharing (done): the results page shares to Instagram, Snapchat, X, Threads and Facebook.
The card is drawn to a PNG on a canvas (`drawCard()`, 1080×1080 or 1080×1920). Instagram and
Snapchat open the phone's share sheet with that image (desktop: save image + copy caption);
X and Threads use web intents with the text; Facebook's sharer takes only the link, so the
caption is copied for pasting.

## Data credits to show on the site
(Brand name on the site: **Your Delulu Number**. Live at https://yourdelulunumber.app, set in `SITE_URL` in index.html.)
- U.S. Census Bureau, American Community Survey 2024 1-year PUMS
- CDC / NCHS, National Health and Nutrition Examination Survey, Aug 2021–Aug 2023
- Veale et al. (2015), BJU International (only if the size factor is used)

---

## Anonymous stats (Supabase, opt-in)
- Results page has an **opt-in** switch ("Add my answers to the stats"), off by default.
  Only when it's on does the page insert one row into the Supabase table `results`.
- Config is two constants in index.html: `SUPABASE_URL` and `SUPABASE_KEY` (the
  **publishable** key, `sb_publishable_...`, which is designed to be public). Never put the
  secret key in the site or the repo.
- The table and its insert-only Row Level Security policy are in `supabase/setup.sql`
  (run once in the Supabase SQL Editor; the live project is set up).
  The public key can insert, never read. Reading and analysis happen in the dashboard
  or later through a server-side job using the secret key (stored as a secret, never committed).
- Never add names, emails, IP addresses or free text to the row.
- Later: use these rows (server-side, aggregated) for "more delusional than X% of people"
  and body-type popularity stats, refreshed into a small static JSON file, so the site still
  never reads the table directly.


## Copy style
US English throughout the site (organized, behavior, math, well-being).
