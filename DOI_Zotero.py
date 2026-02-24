#!/usr/bin/env python3
"""
Scan a folder of PDFs, extract DOIs, fetch metadata from Crossref,
and write BibTeX (.bib) and RIS (.ris) files for import into Zotero.
Also writes a CSV report with status for each PDF.

Usage:
    python extract_dois_make_zotero_bib.py /path/to/pdf/folder
"""

import re, time, csv
from pathlib import Path
import requests
from tqdm import tqdm
from pdfminer.high_level import extract_text
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase
from difflib import SequenceMatcher

# ---------- CONFIG ----------
CROSSREF_MAILTO = "gus.p.williams@gmail.com"  # crossref asks for polite contact; change to yours
SLEEP_BETWEEN_REQUESTS = 1.0  # seconds (be gentle)
TITLE_SIM_THRESHOLD = 0.55   # tuneable: 0.45-0.65
HIGH_CONFIDENCE_TAGS = ('firstpages_label', 'firstpages_any')
AUTO_ACCEPT_TITLE_SEARCH = False  # if True, try to accept title-search matches (use with care)
# ----------------------------
DEFAULT_PDF_FOLDER = r"G:\GIT_Repo\PaperDOI_Zotero\PDF_files"

# stricter DOI regex: allow typical DOI characters but avoid short false matches
DOI_REGEX = re.compile(r'\b(10\.\d{4,9}/[^\s"\'<>]+)\b', re.IGNORECASE)

# find DOI candidates: prefer first_pages search with DOI label
def find_doi_candidates_in_pdf_text(full_text, first_pages_text=None):
    """
    Return list of (doi_candidate, context_tag) where context_tag is like
    'firstpages_label', 'firstpages_any', or 'anywhere_any'.
    """
    candidates = []

    def extract_from_text(txt, tag):
        if not txt:
            return
        # look for explicit DOI labels first
        label_matches = re.findall(r'(?:doi[:\s]|DOI[:\s]|https?://doi\.org/)\s*(10\.\d{4,9}/[^\s"\'<>]+)', txt, flags=re.IGNORECASE)
        for m in label_matches:
            candidates.append((m.strip().rstrip('.,;') , tag + '_label'))
        # fallback: any DOI-like pattern
        for m in DOI_REGEX.findall(txt):
            candidates.append((m.strip().rstrip('.,;'), tag + '_any'))

    # search first pages (higher confidence)
    if first_pages_text:
        extract_from_text(first_pages_text, 'firstpages')

    # then search full text for any remaining candidates
    extract_from_text(full_text, 'anywhere')

    # deduplicate preserving order
    seen = set()
    unique = []
    for doi, tag in candidates:
        if doi.lower() not in seen:
            seen.add(doi.lower())
            unique.append((doi, tag))
    return unique

# verify DOI with Crossref (returns message or None)
def query_crossref_by_doi(doi, timeout=30):
    url = f"https://api.crossref.org/works/{requests.utils.requote_uri(doi)}"
    headers = {"User-Agent": f"pdf-doi-extractor (mailto:{CROSSREF_MAILTO})"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json().get('message')
    except Exception:
        return None
    return None

def query_crossref_by_title(title):
    # fallback: query by title (best-effort)
    if not title:
        return None
    params = {
        "query.title": title,
        "rows": 5,
        "mailto": CROSSREF_MAILTO
    }
    headers = {"User-Agent": f"pdf-doi-extractor (mailto:{CROSSREF_MAILTO})"}
    try:
        r = requests.get("https://api.crossref.org/works", params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            res = r.json().get('message', {})
            items = res.get('items', [])
            if items:
                # return top scored item
                return items[0]
    except Exception:
        return None
    return None

# pick a candidate title from the PDF (first sizable line(s))
def extract_title_candidate_from_text(full_text):
    if not full_text:
        return ""
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    # Heuristic: skip common header words
    skip_words = ('abstract', 'keywords', 'introduction', 'acknowledg', 'copyright','Theses','Dissertations')
    # also skip lines that are likely institutional headers (e.g., university names)
    skip_institution_terms = ('university','college','department','institute','school','brigham','brigham young','byu')
    # find first line of reasonable length that's not a header
    for ln in lines[:30]:
        low = ln.lower()
        # skip common header starts
        if any(low.startswith(s) for s in skip_words):
            continue
        # skip lines that are likely institution names (e.g., 'Brigham Young University')
        if any(term in low for term in skip_institution_terms):
            continue
        if len(ln) > 20:
            return ln
    # fallback to first non-empty line
    return lines[0] if lines else ""

# similarity check (0..1)
def title_similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# extract text helpers
def extract_text_first_pages(path, maxpages=3):
    try:
        return extract_text(path, maxpages=maxpages)
    except Exception:
        return ""

def extract_text_full(path):
    try:
        return extract_text(path)
    except Exception:
        return ""

# basic thesis detection heuristics
def is_thesis(crossref_msg, pdf_title_candidate, first_pages_text, filename=None):
    # check crossref type or titles for thesis/dissertation clues
    if crossref_msg:
        typ = (crossref_msg.get('type') or '').lower()
        if 'dissertation' in typ or 'thesis' in typ:
            return True
        cr_title = ''
        if isinstance(crossref_msg.get('title'), list) and crossref_msg.get('title'):
            cr_title = crossref_msg['title'][0].lower()
        if 'thesis' in cr_title or 'dissertation' in cr_title:
            return True
    # check PDF title or first pages for thesis words
    if pdf_title_candidate and any(k in pdf_title_candidate.lower() for k in ('thesis','dissertation','phd','ph.d')):
        return True
    if first_pages_text and re.search(r'\b(thesis|dissertation|ph\.?d|master\'s)\b', first_pages_text, flags=re.IGNORECASE):
        return True
    # also check filename heuristics (many files include 'Thesis' or 'Dissertation')
    if filename:
        fn = filename.lower()
        if 'thesis' in fn or 'dissert' in fn or 'phd' in fn or 'dissertation' in fn:
            return True
    return False

# to_bibtex and ris converters (slightly adapt to thesis types)
def to_bibtex_entry(crossref_msg, pdf_filename, is_thesis_flag=False):
    # Build a BibTeX entry (type mapping simplified)
    doi = crossref_msg.get('DOI')
    kind = crossref_msg.get('type', 'article-journal')
    bibtype = 'article' if 'journal' in kind else 'misc'
    if is_thesis_flag:
        # approximate thesis entry type
        bibtype = 'phdthesis'
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


def to_ris_entry(crossref_msg, pdf_filename, is_thesis_flag=False):
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
    if is_thesis_flag:
        ris_type = 'THES'
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


def main(pdf_folder):
    folder = Path(pdf_folder)
    if not folder.is_dir():
        print("Folder not found:", pdf_folder); return

    bib_db = BibDatabase()
    bib_db.entries = []
    ris_entries = []
    report_rows = []
    manual_review_rows = []

    pdf_files = list(folder.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files in {folder}")

    for pdf in tqdm(pdf_files):
        filename = pdf.name
        # extract first pages and optionally full text
        text_first = extract_text_first_pages(str(pdf), maxpages=3)
        text_full = ""
        doi = None
        doi_source = None
        crossref_msg = None
        warning = ''
        chosen_confidence = ''
        cr_title = ''
        pdf_title_candidate = extract_title_candidate_from_text(text_first or "")

        # only extract full text if needed (slower)
        # we'll pass text_first as first-pages text; full text may be used to find candidates in references
        # but prefer first pages
        candidates = find_doi_candidates_in_pdf_text(text_full or "", first_pages_text=text_first)

        # If no candidates found in first pages, try scanning full PDF (may be slower)
        if not candidates:
            text_full = extract_text_full(str(pdf))
            candidates = find_doi_candidates_in_pdf_text(text_full or "", first_pages_text=text_first)

        chosen = None
        chosen_msg = None
        chosen_tag = None
        chosen_sim = 0.0
        # tracking for title-search attempts (even if not accepted)
        title_search_attempted = False
        title_search_msg = None
        title_search_sim = None

        for doi_cand, tag in candidates:
            # verify Crossref
            msg = query_crossref_by_doi(doi_cand)
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            if not msg:
                continue  # invalid DOI or not in Crossref
            # Crossref returned metadata: get its title
            cr_title = ''
            if isinstance(msg.get('title'), list) and msg.get('title'):
                cr_title = msg['title'][0]
            # score similarity vs PDF title
            sim = title_similarity(pdf_title_candidate, cr_title)
            # Accept with logic:
            if tag in HIGH_CONFIDENCE_TAGS:
                chosen = doi_cand
                chosen_msg = msg
                chosen_confidence = 'high'
                chosen_tag = tag
                chosen_sim = sim
                break
            else:
                if sim >= TITLE_SIM_THRESHOLD:
                    chosen = doi_cand
                    chosen_msg = msg
                    chosen_confidence = 'medium'
                    chosen_tag = tag
                    chosen_sim = sim
                    break
                else:
                    # low-confidence candidate; continue
                    continue

        # fallback: title search if nothing chosen
        if not chosen and pdf_title_candidate:
            try:
                msg = query_crossref_by_title(pdf_title_candidate)
                time.sleep(SLEEP_BETWEEN_REQUESTS)
                if msg and msg.get('DOI'):
                    # compute similarity
                    cr_title = ''
                    if isinstance(msg.get('title'), list) and msg.get('title'):
                        cr_title = msg['title'][0]
                    sim = title_similarity(pdf_title_candidate, cr_title)
                    # additional guard: reject title-search matches when the PDF title candidate
                    # is short or appears to be an institutional header (e.g., contains 'university')
                    pdf_title_low = (pdf_title_candidate or '').lower()
                    institution_terms = ('university','college','department','institute','school','brigham')
                    # remember that we attempted a title-search
                    title_search_attempted = True
                    title_search_msg = msg
                    title_search_sim = sim
                    # Decide whether to accept this title-search match.
                    # By default we DO NOT auto-accept title-search matches because they are
                    # a common source of false positives for theses/institution headings.
                    if AUTO_ACCEPT_TITLE_SEARCH:
                        if sim >= TITLE_SIM_THRESHOLD and len(pdf_title_candidate) >= 30 and not any(t in pdf_title_low for t in institution_terms):
                            chosen = msg.get('DOI')
                            chosen_msg = msg
                            chosen_confidence = 'title_search'
                            chosen_sim = sim
                    else:
                        # Do not auto-accept: record the attempt in manual_review_rows (handled later)
                        pass
            except Exception as e:
                warning = f"Crossref title search error: {e}"

        # finalize using chosen_msg
        if chosen_msg:
            doi = chosen or chosen_msg.get('DOI')
            doi_source = 'pdf_doi_candidate' if chosen_confidence in ('high','medium') else 'title_search'
            # detect thesis/dissertation
            thesis_flag = is_thesis(chosen_msg, pdf_title_candidate, text_first)
            # add entries
            try:
                bib_entry = to_bibtex_entry(chosen_msg, filename, is_thesis_flag=thesis_flag)
                bib_db.entries.append(bib_entry)
                ris_entries.append(to_ris_entry(chosen_msg, filename, is_thesis_flag=thesis_flag))
                status = 'matched'
            except Exception as e:
                warning = f"Error constructing entry: {e}"
                status = 'error'
        else:
            doi = ''
            status = 'no_match'
            doi_source = ''
            thesis_flag = False
            # if we attempted a title-search and did not accept it, add to manual review
            if title_search_attempted and title_search_msg:
                # record a clear warning and add a manual-review row for all title-search attempts
                warning = warning or f"title_search_no_accept: sim={title_search_sim:.3f} cr_title={ (title_search_msg.get('title') or [''])[0] }"
                manual_review_rows.append([
                    filename,
                    pdf_title_candidate.replace('\n',' '),
                    (title_search_msg.get('title') or [''])[0],
                    title_search_msg.get('DOI'),
                    f"{title_search_sim:.3f}" if title_search_sim is not None else '',
                    'title_search_no_accept'
                ])

        # record report row with extra columns for tuning
        report_rows.append([
            filename,
            doi or "",
            status,
            doi_source or "",
            chosen_confidence or "",
            chosen_tag or "",
            f"{chosen_sim:.3f}" if chosen_sim else "",
            (cr_title or "").replace('\n',' '),
            pdf_title_candidate.replace('\n',' '),
            'thesis' if thesis_flag else '' ,
            warning
        ])

    # write outputs
    bib_writer = BibTexWriter()
    bib_writer.indent = '    '
    with open("references.bib", "w", encoding="utf-8") as f:
        f.write(bib_writer.write(bib_db))
    with open("references.ris", "w", encoding="utf-8") as f:
        f.write("\n\n".join(ris_entries))

    with open("doi_report.csv", "w", newline='', encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename","doi","status","doi_source","confidence","tag","sim","cr_title","pdf_title_candidate","type_hint","warning"])
        w.writerows(report_rows)

    # write manual review CSV for ambiguous title-search attempts
    if manual_review_rows:
        with open("manual_review.csv", "w", newline='', encoding="utf-8") as mf:
            mw = csv.writer(mf)
            mw.writerow(["filename","pdf_title_candidate","attempted_cr_title","attempted_cr_doi","sim","reason"])
            mw.writerows(manual_review_rows)

    print("Done. Wrote references.bib, references.ris, doi_report.csv")


def run_default():
    """Convenience helper to call from an interactive console."""
    print("run_default() invoked. cwd=", Path.cwd())
    main(DEFAULT_PDF_FOLDER)


if __name__ == "__main__":
    # argparse to optionally accept a folder path when run as a script
    import argparse
    parser = argparse.ArgumentParser(description='Extract DOIs from PDFs and fetch Crossref metadata')
    parser.add_argument('folder', nargs='?', default=DEFAULT_PDF_FOLDER, help='Folder containing PDF files')
    args = parser.parse_args()
    print("DOI_Zotero starting. cwd=", Path.cwd())
    print("Using folder:", args.folder)
    main(args.folder)
