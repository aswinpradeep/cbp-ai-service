# Bulk Document Summary Runner

`scripts/bulk_summary_runner.py`

A backend workaround to **bulk-generate document summaries** for many documents at once,
**run in the DEV environment**. It reuses the *exact* logic the CBP API uses, so the summaries
are written to the database in **precisely the same format** as a normal API-triggered summary —
there is no separate/duplicate write path.

- **Input:** a source file — **`.xlsx` (all worksheet tabs are read) or `.csv`** — listing
  **(State Center, Department, Designation)** rows with a `document_available` Yes/No flag.
- **Work:** keep the `Yes` rows, dedupe them to unique **(state, dept)** scopes, **look up each
  scope's documents in the DB** (`documents.state_center_id` + `department_id`), and summarize each
  document — **10 at a time**.
- **Output:** the `documents` table is updated exactly as the API does, **plus** per-row status
  written back into the source CSV (a live progress tracker), **plus** a detailed per-document
  report CSV.

> **Source file:** `.xlsx` or `.csv`. For an `.xlsx`, **every tab is read and combined** by default
> (each report row is tagged with its source tab); restrict to one tab with `--sheet`. Tabs are
> expected to share the same column headers.

> **Two resolution modes** (auto-selected from the columns present):
> - **SCOPE mode** *(the common case)* — the file has **no document id**, only state/dept ids. Each
>   `Yes` row's `(state, dept)` is used to fetch that scope's file_ids from the DB. Designation is
>   irrelevant (documents are stored per state+dept), so duplicate designation rows collapse to one
>   scope.
> - **PER-DOCUMENT mode** — used only if the file actually has a `file_id` (UUID) or `filename`
>   column; then each row maps directly to one document.

---

## 1. Prerequisites (dev)

- Run where the **dev database and the GCS bucket are reachable** (the PDFs are read from GCS).
- `.env` must point at the target data:
  - `DATABASE_URL` → the dev DB that holds the ~3000 document rows.
  - `DOCUMENT_STORAGE_TYPE=gcp`, `GCP_STORAGE_BUCKET`, `GCP_STORAGE_PREFIX`, `GCP_STORAGE_CREDENTIALS` → the bucket holding those PDFs.
  - `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_PROJECT_ID` → Vertex/Gemini access.
- The venv has the app deps (`openpyxl` is used for `.xlsx`).
- Model + prompt are the app's own: `settings.GEMINI_PRO_MODEL_NAME` (e.g. `gemini-3.1-pro-preview`)
  with `DOCUMENT_SUMMARY_PROMPT`.

---

## 2. Input file formats

### 2a. The source file (`--excel`, accepts `.xlsx` or `.csv`) — SCOPE mode

The common format. For `.xlsx`, all tabs are read and combined. Columns are auto-detected
(case/space/`_`-insensitive); override any with a flag if detection is wrong.

| Column in the sheet | Role | Required? | Notes |
|---|---|---|---|
| `State Center id` | **state_id** | ✅ | must exactly match `documents.state_center_id` (see the ⚠️ below) |
| `State Center name` | state_name | optional | copied into the report for readability |
| `Department/MDO id` | **dept_id** | ✅ | must exactly match `documents.department_id` |
| `Department/MDO name` | dept_name | optional | copied into the report |
| `Designation name` | — | ignored | documents are stored per state+dept, not per designation |
| `number of users on Prod` | — | ignored | |
| `document_available` | **filter** | ✅ | the row is used only if this is `Yes` |
| `number_of_documents` | n_docs | optional | sanity check — warns if the DB count differs |
| `work_allocation_order_tag` | — | ignored | |

**How the rows become documents:** keep `document_available = Yes` rows → dedupe to unique
`(State Center id, Department/MDO id)` scopes → for each scope, fetch **all** its rows from the
`documents` table → one summary job per document found.

**Sample:**

| State Center id | State Center name | Department/MDO id | Department/MDO name | Designation name | number of users on Prod | document_available | number_of_documents | work_allocation_order_tag |
|---|---|---|---|---|---|---|---|---|
| 01000000000000000001 | UTTARAKHAND | 02000000000000000001 | Secondary Education | Lecturer | 8450 | Yes | 1 | No |
| 01000000000000000001 | UTTARAKHAND | 02000000000000000001 | Secondary Education | Guest Teacher (Lecturer) | 3025 | Yes | 1 | No |
| 01000000000000000002 | PUNJAB | 02000000000000000002 | Dept. of School Education | Lecturer | 10544 | No | 0 | No |

Rows 1–2 are the **same** `(state, dept)` scope (two designations) → deduped to **one** scope; its
document(s) are fetched from the DB. Row 3 is `No` → ignored.

> ### ⚠️ The id columns MUST be exact text — not numbers
> The real ids are 20–22 digit strings (e.g. `01000000000000000001`). If Excel shows them as
> `1.36E+18` (scientific notation) the cells are **numeric and the true id is destroyed** — those
> rows will match **zero** documents. Before running: format `State Center id` and
> `Department/MDO id` as **Text** in the xlsx (or export a CSV, which keeps them as strings). The
> script warns when it detects numeric-looking ids, and the dry run shows them as `unresolved`.

### 2b. State/Dept names — CSV (`--csv`, optional)

**Not needed when the source file already carries the names.** Only useful if your file lacks
the name columns — it adds readable names to the report. Columns auto-detected.

```csv
state_id,dept_id,state_name,dept_name
01000000000000000001,02000000000000000001,Rajasthan,Home (Police)
```

### 2c. PER-DOCUMENT mode (only if the file has a document id)

If the file instead has a `file_id` (UUID) or `filename` column, the script switches to
per-document mode: each row maps to exactly one document (`file_id` = exact match; `filename` =
matched, disambiguated by `state_id`/`dept_id` if present). Rows are tagged
`matched` / `unresolved` / `ambiguous`.

---

## 3. How it works (logic)

1. **Load & detect** — read the Excel, auto-detect the columns, choose SCOPE vs PER-DOCUMENT mode,
   and print what it found (verify this in the dry run).
2. **Filter** — keep only rows whose filter column is `Yes` (configurable via `--filter-value`).
3. **Resolve to DB documents:**
   - **SCOPE mode** — dedupe the kept rows to unique `(state_center_id, department_id)`, then
     fetch every document in each scope from the DB (chunked composite `IN` query). Scopes with no
     documents are tagged `unresolved`; a mismatch vs `number_of_documents` is logged.
   - **PER-DOCUMENT mode** — match each row by `file_id` (exact) or `filename` (+ state/dept).
     Rows are tagged `matched` / `unresolved` / `ambiguous`.
4. **Select what to run** — based on each doc's current `summary_status` in the DB (see §5).
5. **Generate** — call `src.api.v1.document_routes._run_document_summary(file_id)` — **the same
   function the API background task calls** — with a concurrency of 10.
6. **Record** — the DB is written by that function (see §4); the script also flushes a report row
   per document.

---

## 4. What gets written to the DB (identical to the API)

The script does **not** have its own DB-write logic. It calls `_run_document_summary`, which:

- reads the PDF from storage (GCS),
- calls Gemini (`GEMINI_PRO_MODEL_NAME` + `DOCUMENT_SUMMARY_PROMPT`),
- updates the **`documents`** row:
  - `summary_text` ← the generated summary,
  - `summary_status` ← `COMPLETED` (or `FAILED` on error),
  - `summary_error` ← the error message (or cleared),
- and **skips** anything already `COMPLETED`/`IN_PROGRESS` (same idempotency as the API).

The runner additionally stamps `last_summary_request_id` before each generation, mirroring the
API's `POST /files/{id}/summary` endpoint. **Net result: a row summarized by this script is
indistinguishable from one summarized through the UI/API.**

---

## 5. Parallelism & crash-restart (DB-driven)

**Parallelism.** A semaphore keeps exactly `--batch-size` (default **10**) summaries in flight at
once; the rest queue. One document failing or timing out never cancels the others.

**The DB is the state/queue.** `documents.summary_status` is the source of truth, so the job is
**fully resumable — just re-run the same command.** On each run:

| Current `summary_status` | Action |
|---|---|
| `COMPLETED` | **skipped** (already done) — unless `--force` |
| `NOT_STARTED` | processed |
| `FAILED` | **re-processed** automatically (retry across runs) |
| `IN_PROGRESS` | skipped (assumed owned by a live run) — **unless stale** (untouched longer than `--stale-minutes`, default 30 → treated as crashed and auto-reset), or `--reset-inprogress` |

**Crash safety.**
- **Auto crash-recovery:** if a previous run died mid-flight, its documents are left `IN_PROGRESS`.
  On the next run, any `IN_PROGRESS` doc not touched for `--stale-minutes` is auto-reset to
  `NOT_STARTED` and re-processed. Nothing gets permanently stuck.
- **Per-doc timeout:** a hung generation (default 1200s) is marked `FAILED` — a doc is **never left
  stuck `IN_PROGRESS`** by this runner.
- **In-run retries:** `--retries` (default 1) retries a `FAILED` doc within the same run; cross-run
  retries are automatic (FAILED is always re-processed).
- **Durable report:** each result row is flushed to the report CSV as it completes, so a crash
  still leaves a usable partial report.

**To resume after a crash or a `Ctrl-C`:** re-run the identical command. Completed docs skip,
failed docs retry, stale in-progress docs recover.

---

## 6. Steps to run

```bash
cd /path/to/cbp-ai-service     # on the dev box, with .env pointed at the dev DB + bucket

# 1) DRY RUN (default) — no Gemini calls, no GCS needed. VERIFY the "detected columns" line,
#    the mode (should be SCOPE), the kept-row count, unique scopes, and the resolution tally.
#    Watch for any "looks NUMERIC" warning or a high 'unresolved' count. Writes a *_plan_*.csv.
.venv/bin/python scripts/bulk_summary_runner.py --excel /path/source.xlsx   # or source.csv

# 2) SMOKE TEST — process 5 for real and check the DB + report.
.venv/bin/python scripts/bulk_summary_runner.py --excel /path/source.xlsx --execute --limit 5

# 3) FULL RUN — process everything, 10 at a time.
.venv/bin/python scripts/bulk_summary_runner.py --excel /path/source.xlsx --execute

# 4) RESUME after a crash/interrupt — just run the same command again.
.venv/bin/python scripts/bulk_summary_runner.py --excel /path/source.xlsx --execute

# 5) REGENERATE everything (even COMPLETED):
.venv/bin/python scripts/bulk_summary_runner.py --excel /path/source.xlsx --execute --force

# (xlsx) restrict to a single tab instead of reading all of them:
.venv/bin/python scripts/bulk_summary_runner.py --excel /path/source.xlsx --sheet "Batch-A" --execute
```

> `--csv` is optional and **not needed when the source file carries the names**. Add it only if the
> file lacks the name columns.

### CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--excel PATH` | — | source file: `.xlsx` (all tabs) or `.csv` (required) |
| `--sheet NAME` | all tabs | xlsx: restrict to one worksheet tab |
| `--csv PATH` | — | optional state/dept id→name reference (labels only) |
| `--out PATH` | auto, next to the source file | report CSV path |
| `--execute` | off (dry run) | actually generate summaries |
| `--limit N` | 0 (all) | process at most N documents |
| `--batch-size N` | 10 | concurrent summaries |
| `--retries N` | 1 | extra in-run attempts on FAILED |
| `--force` | off | regenerate even if COMPLETED |
| `--reset-inprogress` | off | re-run **all** IN_PROGRESS docs |
| `--no-resume` | off | disable auto-recovery of stale IN_PROGRESS |
| `--stale-minutes N` | 30 | IN_PROGRESS older than this = crashed → recover |
| `--filter-col NAME` / `--filter-value V` | auto / `yes` | override the yes/no column and kept value |
| `--file-id-col` / `--filename-col` / `--state-col` / `--dept-col` | auto | override column detection |
| `--log-file PATH` | `scripts/logs/bulk_summary_runner.log` | append-only run log (see §7c) |

---

## 7. Output

### 7a. Database
The `documents` rows for processed files are updated in place (`summary_text`, `summary_status`,
`summary_error`, `last_summary_request_id`) — **exactly as an API-triggered summary**. This is the
real deliverable; the report below is just a run log.

### 7b. Report CSV
Auto-named next to the source file: `bulk_summary_run_<UTC-timestamp>.csv` (dry runs write
`bulk_summary_plan_<...>.csv`). One row per document:

| column | meaning |
|---|---|
| `sheet` | source tab the row came from (`(csv)` for a CSV source) |
| `row` | 1-based row number within that tab |
| `resolution` | `matched` / `unresolved` / `ambiguous` |
| `file_id` | resolved `documents.file_id` |
| `filename` | document filename |
| `state_id`, `dept_id` | scope from the source |
| `state_name`, `dept_name` | names from the source (or `--csv`) |
| `prior_status` | `summary_status` before this run |
| `final_status` | `summary_status` after (`COMPLETED` / `FAILED` / skipped-status) |
| `attempts` | how many attempts it took |
| `tok_input` / `tok_output` / `tok_thinking` / `tok_total` | Gemini token usage for this document (`thinking` = reasoning tokens, billed at the output rate) |
| `error` | error message if `FAILED` |

**Sample:**

```csv
sheet,row,resolution,file_id,filename,state_id,dept_id,state_name,dept_name,prior_status,final_status,attempts,error
Batch-A,2,matched,11111111-1111-1111-1111-111111111111,work_allocation.pdf,01000000000000000001,02000000000000000001,Rajasthan,School Education,NOT_STARTED,COMPLETED,1,
Batch-A,2,matched,22222222-2222-2222-2222-222222222222,rules_2.pdf,01000000000000000001,02000000000000000001,Rajasthan,School Education,FAILED,COMPLETED,2,
Batch-B,5,unresolved,,,01000000000000000003,02000000000000000003,Assam,Secondary Education,,,,no documents in DB for this state/dept scope
```

### 7c. Run log (screen + append-only file)
Every run logs to **both the console and a log file**, at INFO level. The log file is opened in
**append mode — it is never overwritten**, so all runs accumulate in one file (handy for auditing a
multi-hour job or diagnosing a crash + resume).

- **Default location:** `scripts/logs/bulk_summary_runner.log` (override with `--log-file PATH`).
- Each run starts with a separator line so runs are easy to tell apart:
  ```
  ==============================================================================
  RUN 2026-07-20T09:03:11Z | mode=EXECUTE | source=... | limit=all | batch=10 | force=False | resume_stale=True@30m | retries=1
  logging to (append): scripts/logs/bulk_summary_runner.log
  ```
- The file also captures the app's own summary logs (`ai_cbp_service` — "Document summary process
  started/completed/failed for <id>") alongside the runner's per-document progress lines, which
  include **token usage**:
  `(12/500) <file_id> NOT_STARTED -> COMPLETED  tokens[in=9688 out=8812 think=10883 total=29383]`
- At the end of a run it logs a grand total:
  `TOTAL tokens over 500 docs: input=… output=… thinking=… total=…` (handy for cost estimation).
  Per-document token counts are also in the report CSV (`tok_*` columns).

Tail it live during a run:
```bash
tail -f scripts/logs/bulk_summary_runner.log
```

### 7d. Per-row status written back to the source (the tracker you asked for)
Beyond the logs and the per-document report, the runner writes a **per-row status back into a CSV**
so the input file itself is a progress tracker. **When the source is a `.csv`, it updates that same
file in place** (atomic write); for an `.xlsx` it writes a sibling `<source>.status.csv`
(xlsx isn't edited in place). Override the path with `--status-out`, disable with `--no-annotate`.

It is refreshed **incrementally during the run** (every ~10 documents) and once more at the end, so
you can open/tail it any time to see progress — including failures and "no document" cases. In
SCOPE mode each input row aggregates its scope's documents. Columns added:

| column | meaning |
|---|---|
| `run_status` | `COMPLETED` / `PARTIAL` / `FAILED` / `PENDING` / `UNRESOLVED` / `AMBIGUOUS` / `SKIPPED` |
| `run_docs_total` | documents found in the DB for this row's scope |
| `run_docs_done` | how many are `COMPLETED` |
| `run_docs_failed` | how many are `FAILED` |
| `run_file_ids` | the resolved `file_id`s (`;`-separated) |
| `run_error` | error(s) — e.g. `File missing in storage`, or `no documents in DB for this state/dept scope` |
| `run_updated_at` | last time this row was refreshed (UTC) |

- `SKIPPED` = the row wasn't selected (e.g. `document_available` ≠ yes).
- `UNRESOLVED` = a `Yes` row whose scope has **no documents in the DB** (the "file not available"
  case is captured right here, in `run_status` + `run_error`).
- Re-running is safe: the runner strips its own `run_*` columns before rewriting, so they never
  duplicate.

> **Two progress artifacts, use whichever you prefer:** the **source CSV** (per input row, in place)
> and the detailed **`bulk_summary_run_*.csv`** report (per individual document, §7b). Both survive
> a crash. The DB itself always remains the ultimate source of truth.

---

## 8. Troubleshooting

- **Wrong column auto-detected** → check the `detected columns -> …` line and override with
  `--filter-col` / `--file-id-col` / `--filename-col` / `--state-col` / `--dept-col`.
- **`unresolved` rows** → the file_id/filename isn't in the target DB, or the state/dept scope
  doesn't match. Confirm `.env` points at the right DB.
- **`ambiguous` rows** → the same filename exists in multiple scopes; add `state_id`/`dept_id`
  columns to the sheet so they disambiguate (or use a file_id column).
- **Docs stuck `IN_PROGRESS`** → recovered automatically on the next run after `--stale-minutes`;
  force it with `--reset-inprogress`.
- **Everything shows `TO PROCESS: 0`** → they're already `COMPLETED`; use `--force` to redo.
