DOI_Zotero — PDF DOI extraction and Crossref metadata importer
=============================================================

Overview
--------
This repository contains a Python script, `DOI_Zotero.py`, that scans a folder of PDF files, extracts DOI candidates (preferring the first pages), verifies them using the Crossref API, and writes bibliographic exports usable by Zotero:

- `references.bib` (BibTeX)
- `references.ris` (RIS)
- `doi_report.csv` (diagnostic report used for manual review and tuning)

The script also attempts to detect theses/dissertations and will mark those entries accordingly.

High-level process flow
-----------------------
For each PDF in the specified folder the script:

1. Extracts text from the first N pages (default N = 3).
2. Extracts DOI candidates from the first pages (explicit DOI labels are prioritized), then optionally from the full text if needed.
3. Extracts a short title candidate from the first pages (first non-header, reasonably long line).
4. For every DOI candidate, queries Crossref to verify the DOI and obtain metadata.
5. Computes a title similarity score between the PDF's title candidate and the Crossref title.
6. Accepts a Crossref match when: either a high-confidence candidate was found on the first pages (e.g., `doi:` label) OR the title similarity meets a configurable threshold.
7. Falls back to a Crossref title search when no DOI candidate is accepted.
8. Classifies the record as a thesis if heuristics indicate a dissertation/thesis.
9. Writes bibliographic entries and adds a diagnostic row to `doi_report.csv`.

Key functions and decision rules
-------------------------------
- `find_doi_candidates_in_pdf_text(full_text, first_pages_text=None)`
  - Scans text for explicit DOI labels (e.g. `doi:` or `https://doi.org/…`) and general DOI-like patterns. Returns ordered unique candidates with tags describing where they were found.

- `query_crossref_by_doi(doi)` and `query_crossref_by_title(title)`
  - Call Crossref REST API to fetch metadata. The script uses a polite `User-Agent` that includes the `CROSSREF_MAILTO` email address.

- `extract_title_candidate_from_text(text)`
  - Heuristic to pick a likely title line from the PDF first pages (skips typical headers like "Abstract").

- `title_similarity(a, b)`
  - Uses `difflib.SequenceMatcher` to compute a 0..1 similarity ratio between the PDF title candidate and the Crossref title.

- Acceptance logic
  - HIGH confidence: candidate DOI found on the first pages with an explicit label — accepted immediately (configurable).
  - MEDIUM confidence: candidate accepted only if the title similarity >= `TITLE_SIM_THRESHOLD`.
  - Fallback: Crossref title search accepted if similarity >= `TITLE_SIM_THRESHOLD`.

Outputs and `doi_report.csv` columns
-----------------------------------
The script produces these files in the repository root (or current working directory):

- `references.bib` — BibTeX entries created from Crossref metadata.
- `references.ris` — RIS entries created from Crossref metadata.
- `doi_report.csv` — diagnostic report with columns:
  - filename — PDF filename
  - doi — matched DOI (if any)
  - status — `matched`, `no_match`, or `error`
  - doi_source — `pdf_doi_candidate` or `title_search`
  - confidence — `high`, `medium`, `title_search`, or empty
  - tag — where the DOI was found (`firstpages_label`, `firstpages_any`, `anywhere_any`, …)
  - sim — title similarity score (0..1)
  - cr_title — Crossref title (for review)
  - pdf_title_candidate — the title string extracted from the PDF first pages
  - type_hint — `thesis` when heuristics detect a dissertation/thesis
  - warning — any warnings or errors encountered

Theses/Dissertations and ambiguous title-search matches
------------------------------------------------------
- Important: many theses and dissertations do not have DOIs. The script includes heuristics to detect theses (checks Crossref `type`, looks for words like "thesis"/"dissertation" in titles or first pages, and also inspects filenames). When a record is detected as a thesis the `type_hint` column in `doi_report.csv` will contain `thesis`.

- Title similarity is used to avoid false positives: when a DOI-like string is found in a PDF it is only accepted automatically if it appears on the first pages with an explicit DOI label (high confidence) or the Crossref metadata title is sufficiently similar to the PDF-extracted title (similarity >= `TITLE_SIM_THRESHOLD`).

- If a Crossref title-search is attempted (fallback) but not auto-accepted by the script (AUTO_ACCEPT_TITLE_SEARCH is `False` by default), the attempt is recorded in two places:
  - `manual_review.csv` (if any title-search attempts occurred) with columns: filename, pdf_title_candidate, attempted_cr_title, attempted_cr_doi, sim, reason
  - `doi_report.csv` will include a `warning` such as `title_search_no_accept: sim=0.123 cr_title=...` so you can spot attempted-but-rejected title-search matches.

- When a title-search match is rejected the final `status` for that PDF will be `no_match`. This is intentional: the script errs on the side of not importing probable false positives (common for theses or header/institutional text).

- Example of a confusing DOI you may encounter: `10.5040/9798216405887.ch-014` — this string looks like a chapter DOI (often Crossref uses publisher-specific DOI patterns for books/chapters). If a title-search accidentally matches a chapter or book instead of the thesis, you'll see a low similarity score and the script will not accept it automatically (unless you enable `AUTO_ACCEPT_TITLE_SEARCH`). Review such cases in `manual_review.csv` or `doi_report.csv`.

Running interactively (PyCharm / Python console)
-----------------------------------------------
- The script includes a small helper function `run_default()` which calls `main()` with the `DEFAULT_PDF_FOLDER` configured near the top of the file. This is provided so you can import the module in an interactive console (such as PyCharm's Python console) and call it directly.

  Example (in PyCharm Python console):

  ```python
  import DOI_Zotero
  # prints a short message and runs against the default folder configured in the script
  DOI_Zotero.run_default()
  ```

- Alternatively run the script normally from PowerShell (it accepts an optional folder argument):

  ```powershell
  # Run against the default folder set in the script
  python .\DOI_Zotero.py

  # Or pass a custom folder path
  python .\DOI_Zotero.py 'G:\GIT_Repo\PaperDOI_Zotero\PDF_files'
  ```

- Note: when running interactively make sure your current working directory is where you want the output files (`references.bib`, `references.ris`, `doi_report.csv`) to be written; the script writes outputs to the current working directory.

Typical usage
-------------
1. Edit `DOI_Zotero.py` and set a contact email near the top: set `CROSSREF_MAILTO = "your_email@example.com"`.

2. Install Python dependencies (recommended in a virtual environment):

```powershell
python -m pip install -U pip
python -m pip install pdfminer.six requests bibtexparser tqdm
```

3. Run the script against a PDF folder (example):

```powershell
# Run against the default folder set in the script
python .\DOI_Zotero.py

# Or pass a custom folder path
python .\DOI_Zotero.py 'G:\GIT_Repo\PaperDOI_Zotero\PDF_files'
```

4. Inspect outputs in the current directory: `references.bib`, `references.ris`, and `doi_report.csv`.

Quick debugging and inspection commands (PowerShell)
--------------------------------------------------
Show first 50 lines of the diagnostic CSV:

```powershell
Get-Content .\doi_report.csv -TotalCount 50
```

Show PDFs with no confident DOI match:

```powershell
Import-Csv .\doi_report.csv |
  Where-Object { $_.status -ne 'matched' -or [string]::IsNullOrWhiteSpace($_.confidence) } |
  Format-Table -AutoSize
```

Show possible low-similarity matches (adjust threshold as needed):

```powershell
Import-Csv .\doi_report.csv |
  Where-Object { $_.sim -and ([double]$_.'sim' -lt 0.55) } |
  Format-Table filename, doi, status, confidence, tag, sim, type_hint -AutoSize
```

Tuning tips
-----------
- `TITLE_SIM_THRESHOLD` (default 0.55):
  - Raise to ~0.65 to reduce false positives (more strict).
  - Lower to ~0.45 to accept matches when PDF titles are short/noisy (more permissive).

- `HIGH_CONFIDENCE_TAGS` (default `('firstpages_label','firstpages_any')`):
  - For stricter behavior, keep only `'firstpages_label'` so that only explicit `doi:` or `doi.org` labels are auto-accepted.

- `SLEEP_BETWEEN_REQUESTS` (default 1.0s):
  - Increase to 2.0s to avoid hitting Crossref rate limits when processing many PDFs.

Common failure modes and mitigations
-----------------------------------
- False positives from reference lists: the script prefers first-page candidates, and the CSV `tag` column helps you spot `anywhere_*` matches that likely came from references. If you see many reference-list hits, consider requiring similarity checks for all tags.

- Poor OCR / scanned PDFs: extraction may fail or return noisy text; try running OCR on those PDFs or lower `TITLE_SIM_THRESHOLD`.

- Rate limiting or network errors: increase `SLEEP_BETWEEN_REQUESTS` and ensure `CROSSREF_MAILTO` is set.

Possible improvements
---------------------
- Export a `manual_review.csv` with all low-confidence hits and skip adding them to `references.bib` until manually approved.
- Add a command-line flag to tune thresholds or run in a dry-run/manual-review mode.
- Add additional heuristics that compare Crossref `container-title` (journal name) to words in the first pages to reduce false matches.

Contact and credit
------------------
- Script author: repository owner. Crossref requests include the `CROSSREF_MAILTO` email address — set this to your email for polite API use.



--
This file was generated automatically to explain the repository and make it easy to run and tune the DOI extraction script.
