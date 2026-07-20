# Role Mapping Runner (single designation)

`scripts/role_mapping_runner.py`

A backend workaround to **bulk-generate v3 role mappings, one designation at a time**, run in the
**DEV environment**. It is the companion to [`bulk_summary_runner.md`](bulk_summary_runner.md) and
shares the **same input file** (`.xlsx` multi-tab / `.csv`) and the **same harness** (column
auto-detection, dedup, batch-of-10 concurrency, idempotent resume, append logging, token capture,
per-row status write-back, dry-run).

It reuses the real v3 logic (`src/services/v3/role_mapping_service.py`) and writes a `role_mappings`
row **exactly like the v3 flow** — but runs only the passes needed for a single, already-known
designation, and skips the API's extra bookkeeping.

---

## 1. The 4-pass v3 flow — what this runs

| Pass | v3 does | This runner |
|---|---|---|
| **PASS 1** — designation extraction (LLM) | reads WAO summaries, extracts all designations | **SKIPPED** — the designation is given in the file |
| **PASS 2** — FRAC generation (LLM) | generates competencies/responsibilities/activities per designation | **RUN** — 1 LLM call per designation |
| **PASS 3** — domain-from-WAO (LLM) | re-derives Domain competencies from the raw WAO PDF | **SKIPPED** — not required |
| **PASS 4** — KCM reconciliation (no LLM) | corrects Behavioural/Functional competencies against `data/competencies.json` | **RUN** — deterministic |

Then it saves one `role_mappings` row (`status=COMPLETED`). The v3 **API additionally** creates a
placeholder row, splits results across rows, and auto-matches each designation to the iGOT master —
**this runner skips all of that** (iGOT matching is opt-in via `--igot-match`).

**One LLM call per designation** (PASS 2). PASS 4 is pure Python.

---

## 2. Prerequisites (dev)

- **Document summaries must already exist** for each scope — run
  [`bulk_summary_runner.py`](bulk_summary_runner.md) first. PASS 2 reads them; a scope with no
  `COMPLETED` summaries is reported `unresolved` and skipped.
- `.env` points at the dev DB + Vertex creds (`GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_PROJECT_ID`).
  (GCS is **not** needed here — PASS 3, which reads PDFs, is skipped.)
- **`--user-id <UUID>` is required for `--execute`** — role mappings are stored **per user**
  (`role_mappings.user_id` is non-nullable, and mappings are looked up per user). All generated rows
  are attributed to this user.
- **`org_type`** (state vs ministry) selects the generation prompt. It's taken from a per-row
  `org_type` column if present, otherwise from `--org-type` (default `state`).

---

## 3. Input file (same format as the summary runner)

`.xlsx` (all tabs) or `.csv`. Columns auto-detected (case/space/`_`-insensitive):

| Column in the file | Role | Required? |
|---|---|---|
| `State Center id` | state scope | ✅ (exact DB id — **not** `1.36E+18`, see the summary runner's ⚠️) |
| `Department/MDO id` | dept scope | ✅ |
| **`Designation name`** | **the designation to map** | ✅ |
| `document_available` | filter (keep `Yes`) | optional (defaults to keeping `Yes`; no filter col → all rows) |
| `org_type` | `state` / `ministry` per row | optional (else `--org-type`) |
| `State Center name`, `Department/MDO name` | labels (saved on the row + report) | optional |

**Each row = one (state, dept, designation)** → one role mapping. Rows are **deduped** on
`(state, dept, designation)`; exact duplicates are marked `duplicate` and processed once.

**Sample:**

```csv
State Center id,State Center name,Department/MDO id,Department/MDO name,Designation name,document_available,number_of_documents,work_allocation_order_tag,org_type
01000000000000000001,PUNJAB,02000000000000000001,School Education,Lecturer,Yes,1,No,state
01000000000000000002,UTTARAKHAND,02000000000000000002,Secondary Education,Guest Teacher (Lecturer),Yes,1,No,state
```

---

## 4. What gets written to the DB

For each processed designation, one `role_mappings` row is inserted with `status=COMPLETED`:
`user_id` (from `--user-id`), `org_type`, `state_center_id`/`name`, `department_id`/`name`,
`designation_name`, `wing_division_section`, `role_responsibilities`, `activities`,
`competencies` (Behavioral/Functional/Domain, KCM-reconciled), `sort_order`. This matches the v3
API's write. With `--igot-match`, `igot_designation_id`/`igot_designation_name` are also filled.

---

## 5. Parallelism & idempotent resume

- **Parallelism:** a semaphore keeps `--batch-size` (default **10**) designations generating at once;
  one failure never cancels the batch.
- **Idempotent / resumable:** before a run, each designation's `(user, state, dept, designation)` is
  checked against existing `role_mappings`. If a `COMPLETED` row already exists it is **skipped**
  (`prior_status=COMPLETED`) — so re-running after a crash/interrupt only generates the missing ones.
  Use `--force` to regenerate (it deletes the existing row for that designation first, then recreates).
- **Per-doc timeout** (1200s) and **in-run retries** (`--retries`, default 1) as in the summary runner.
- No stuck-state to clean up: rows are only written on success, so a partial run just leaves some
  designations unwritten — the next run picks them up.

---

## 6. Steps to run

```bash
cd /path/to/cbp-ai-service
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/vertex-creds.json"

# 1) DRY RUN (no LLM) — resolve scopes, confirm summaries exist, show plan, write status into the CSV.
#    Check: resolution mostly 'matched'; 'unresolved' means summaries are missing for that scope.
.venv/bin/python scripts/role_mapping_runner.py --excel /path/source.csv --user-id <UUID>

# 2) SMOKE TEST 5 for real, then check the role_mappings rows + the status CSV
.venv/bin/python scripts/role_mapping_runner.py --excel /path/source.csv --user-id <UUID> --execute --limit 5

# 3) FULL RUN (under nohup/tmux so it survives your session)
nohup .venv/bin/python scripts/role_mapping_runner.py --excel /path/source.csv --user-id <UUID> --execute > rm.out 2>&1 &
tail -f scripts/logs/role_mapping_runner.log

# resume after a crash: re-run the same command (COMPLETED designations are skipped)
# also match to iGOT master: add --igot-match
```

### CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--excel PATH` | — | source `.xlsx` (all tabs) or `.csv` (required) |
| `--sheet NAME` | all tabs | xlsx: restrict to one tab |
| `--user-id UUID` | — | attribution for generated rows (**required for `--execute`**) |
| `--org-type state\|ministry` | `state` | used when the file has no `org_type` column |
| `--instruction TEXT` | — | optional extra instruction passed to generation |
| `--igot-match` | off | also match each designation to the iGOT master |
| `--execute` | off (dry run) | actually generate |
| `--limit N` | 0 (all) | process at most N designations |
| `--force` | off | regenerate even if a mapping already exists |
| `--batch-size N` | 10 | concurrent designations |
| `--retries N` | 1 | extra in-run attempts on failure |
| `--filter-col` / `--filter-value` | auto / `yes` | override the keep filter |
| `--state-col` / `--dept-col` / `--designation-col` | auto | override column detection |
| `--log-file PATH` | `scripts/logs/role_mapping_runner.log` | append-only run log |
| `--status-out PATH` | source `.csv` in place (or `<src>.rolemap_status.csv` for xlsx) | per-row status |
| `--no-annotate` | off | don't write per-row status |

---

## 7. Output

### 7a. Database
One `role_mappings` row per designation (see §4) — identical to a v3 API-generated mapping.

### 7b. Report CSV (`role_mapping_run_<ts>.csv`, next to the source)
One row per designation: `sheet, row, resolution, state_id, dept_id, designation, org_type,
state_name, dept_name, prior_status, final_status, attempts, competencies, role_mapping_id,
tok_input, tok_output, tok_thinking, tok_total, error`.

### 7c. Per-row status (written back into the source CSV, in place)
Columns added: `run_status` (`COMPLETED`/`FAILED`/`UNRESOLVED`/`DUPLICATE`/`SKIPPED`/`PENDING`),
`run_competencies` (count), `run_role_mapping_id`, `run_tokens`, `run_error`, `run_updated_at`.
Refreshed live during the run and at the end.

### 7d. Logs (console + append-only file)
Per designation:
```
(12/500) <state>/<dept> :: Lecturer NOT_STARTED -> COMPLETED comps=18  tokens[in=35507 out=1270 think=3422 total=40199]
```
plus the service's own `PASS 2` / `KCM reconciliation summary` lines, and a run total
`TOTAL tokens over N designations: ...`.

---

## 8. Troubleshooting

- **`unresolved` = "no COMPLETED document summaries in scope"** → run the **summary runner first**
  for that scope; role mapping needs summaries as input.
- **`--user-id required`** → role mappings are per-user; pass a UUID.
- **Wrong prompt (state vs ministry)** → set the per-row `org_type` column or `--org-type`.
- **id columns look like `1.36E+18`** → the long ids are corrupted by Excel; format as Text or use a
  CSV (the runner warns and those rows resolve to `unresolved`).
- **Everything `TO PROCESS: 0`** → those designations already have `COMPLETED` mappings; use `--force`.
