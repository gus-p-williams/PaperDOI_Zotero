#!/usr/bin/env python3
"""
Scan a folder of PDFs, extract DOIs, fetch metadata from Crossref,
and write BibTeX (.bib) and RIS (.ris) files for import into Zotero.
Also writes a CSV report with status for each PDF.

Usage:
    python extract_dois_make_zotero_bib.py /path/to/pdf/folder
"""

import os, sys, re, json, time, csv
from pathlib import Path
import requests
from tqdm import tqdm
from pdfminer.high_level import extract_text
import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase

# ---------- CONFIG ----------
CROSSREF_MAILTO = "your_email@example.com"  # crossref asks for polite contact; change to yours
SLEEP_BETWEEN_REQUESTS = 1.0  # seconds (be gentle)
# ----------------------------
DEFAULT_PDF_FOLDER = r"G:\GIT_Repo\PaperDOI_Zotero\PDF_files"


# DOI regex (robust common pattern)
DOI_REGEX = re.compile(r'\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b')

def find_doi_in_text(text):
    if not text:
        return None
    # many DOIs are lower/upper; preserve case but search
    m = DOI_REGEX.search(text)
    if m:
        doi = m.group(0).rstrip('.;,)')
        return doi
    return None

def query_crossref_by_doi(doi):
    url = f"https://api.crossref.org/works/{requests.utils.requote_uri(doi)}"
    headers = {"User-Agent": f"pdf-doi-extractor (mailto:{CROSSREF_MAILTO})"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 200:
        return r.json().get('message')
    return None

def query_crossref_by_title(title):
    # fallback: query by title (best-effort)
    params = {
        "query.title": title,
        "rows": 5,
        "mailto": CROSSREF_MAILTO
    }
    headers = {"User-Agent": f"pdf-doi-extractor (mailto:{CROSSREF_MAILTO})"}
    r = requests.get("https://api.crossref.org/works", params=params, headers=headers, timeout=30)
    if r.status_code == 200:
        res = r.json().get('message', {})
        items = res.get('items', [])
        if items:
            # return top scored item
            return items[0]
    return None

def to_bibtex_entry(crossref_msg, pdf_filename):
    # Build a BibTeX entry (type mapping simplified)
    doi = crossref_msg.get('DOI')
    kind = crossref_msg.get('type', 'article-journal')
    bibtype = 'article' if 'journal' in kind else 'misc'
    # create a key: author_year_shorttitle
    authors = crossref_msg.get('author', [])
    if authors:
        first_author = authors[0].get('family', authors[0].get('given', ''))
    else:
        first_author = 'anon'
    year_parts = crossref_msg.get('issued', {}).get('date-parts', [[None]])
    year = str(year_parts[0][0]) if year_parts and year_parts[0][0] else 'n.d.'
    title = crossref_msg.get('title', [''])[0]
    key = re.sub(r'\W+', '_', f"{first_author}_{year}_{title[:30]}").strip('_')

    bib = {
        'ID': key,
        'ENTRYTYPE': bibtype,
        'title': title,
        'author': ' and '.join([f"{a.get('given','')} {a.get('family','')}".strip() for a in authors]) if authors else '',
        'year': year,
        'journal': crossref_msg.get('container-title', [''])[0],
        'volume': crossref_msg.get('volume', ''),
        'issue': crossref_msg.get('issue', ''),
        'pages': crossref_msg.get('page', ''),
        'doi': doi or '',
        'url': crossref_msg.get('URL', ''),
        'note': f"PDF file: {pdf_filename}"
    }
    # remove empty fields
    bib = {k:v for k,v in bib.items() if v}
    return bib

def to_ris_entry(crossref_msg, pdf_filename):
    # Simple RIS conversion
    ris_lines = []
    type_map = {
        'article-journal': 'JOUR',
        'book': 'BOOK',
        'report': 'RPRT',
        'proceedings-article': 'CPAPER'
    }
    typ = crossref_msg.get('type','article-journal')
    ris_type = type_map.get(typ, 'GEN')
    ris_lines.append(f"TY  - {ris_type}")
    for a in crossref_msg.get('author', []):
        fn = ' '.join(filter(None, [a.get('given',''), a.get('family','')])).strip()
        if fn:
            ris_lines.append(f"AU  - {fn}")
    title = crossref_msg.get('title', [''])[0]
    if title:
        ris_lines.append(f"TI  - {title}")
    container = crossref_msg.get('container-title', [''])[0]
    if container:
        ris_lines.append(f"JO  - {container}")
    year_parts = crossref_msg.get('issued', {}).get('date-parts', [[None]])
    if year_parts and year_parts[0][0]:
        ris_lines.append(f"PY  - {year_parts[0][0]}")
    if crossref_msg.get('volume'):
        ris_lines.append(f"VL  - {crossref_msg.get('volume')}")
    if crossref_msg.get('issue'):
        ris_lines.append(f"IS  - {crossref_msg.get('issue')}")
    if crossref_msg.get('page'):
        ris_lines.append(f"SP  - {crossref_msg.get('page')}")
    if crossref_msg.get('DOI'):
        ris_lines.append(f"DO  - {crossref_msg.get('DOI')}")
    if crossref_msg.get('URL'):
        ris_lines.append(f"UR  - {crossref_msg.get('URL')}")
    ris_lines.append(f"NOTE - PDF file: {pdf_filename}")
    ris_lines.append("ER  - ")
    return "\n".join(ris_lines)

def extract_text_from_pdf(path):
    try:
        text = extract_text(path, maxpages=3)  # first few pages often contain DOI
        if not text or len(text.strip()) < 10:
            # try full extraction if first pages small
            text = extract_text(path)
        return text
    except Exception as e:
        return ""

def main(pdf_folder):
    folder = Path(pdf_folder)
    if not folder.is_dir():
        print("Folder not found:", pdf_folder); return

    bib_db = BibDatabase()
    bib_db.entries = []
    ris_entries = []
    report_rows = []

    pdf_files = list(folder.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files in {folder}")

    for pdf in tqdm(pdf_files):
        filename = pdf.name
        text = extract_text_from_pdf(str(pdf))
        doi = None
        doi_source = None
        crossref_msg = None
        warning = ''
        if text:
            doi = find_doi_in_text(text)
        if doi:
            doi_source = 'pdf_text'
            # fetch crossref
            try:
                crossref_msg = query_crossref_by_doi(doi)
            except Exception as e:
                warning = f"Crossref DOI query error: {e}"
        else:
            # fallback: try to glean title from first lines
            first_lines = (text or "").splitlines()
            title_candidate = ""
            for ln in first_lines[:10]:
                ln = ln.strip()
                if len(ln) > 6 and not ln.lower().startswith('abstract') and not ln.lower().startswith('keywords'):
                    title_candidate = ln
                    break
            if title_candidate:
                try:
                    crossref_msg = query_crossref_by_title(title_candidate)
                    if crossref_msg and crossref_msg.get('DOI'):
                        doi = crossref_msg.get('DOI')
                        doi_source = 'title_search'
                except Exception as e:
                    warning = f"Crossref title search error: {e}"

        time.sleep(SLEEP_BETWEEN_REQUESTS)

        status = "no_match"
        if crossref_msg:
            status = "matched"
            bib_entry = to_bibtex_entry(crossref_msg, filename)
            bib_db.entries.append(bib_entry)
            ris_entries.append(to_ris_entry(crossref_msg, filename))
            report_rows.append([filename, doi or "", status, doi_source or "", warning])
        else:
            report_rows.append([filename, doi or "", status, doi_source or "", warning])

    # write outputs
    bib_writer = BibTexWriter()
    bib_writer.indent = '    '
    with open("references.bib", "w", encoding="utf-8") as f:
        f.write(bib_writer.write(bib_db))
    with open("references.ris", "w", encoding="utf-8") as f:
        f.write("\n\n".join(ris_entries))

    with open("doi_report.csv", "w", newline='', encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename","doi","status","doi_source","warning"])
        w.writerows(report_rows)

    print("Done. Wrote references.bib, references.ris, doi_report.csv")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
    else:
        folder_path = DEFAULT_PDF_FOLDER

    main(folder_path)
