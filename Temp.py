import re
import time
import requests
from difflib import SequenceMatcher

# stricter DOI regex: allow typical DOI characters but avoid short false matches
DOI_REGEX = re.compile(
    r'\b(10\.\d{4,9}/[^\s"\'<>]+\b)', re.IGNORECASE
)

# find DOI candidates: prefer first_pages search with DOI label
def find_doi_candidates_in_pdf_text(full_text, first_pages_text=None):
    """
    Return list of (doi_candidate, context_tag) where context_tag is 'firstpage_label',
    'firstpage_any', or 'anywhere'.
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
def query_crossref_by_doi(doi, mailto, timeout=30):
    url = f"https://api.crossref.org/works/{requests.utils.requote_uri(doi)}"
    headers = {"User-Agent": f"pdf-doi-extractor (mailto:{mailto})"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json().get('message')
    except Exception:
        return None
    return None


# pick a candidate title from the PDF (first sizable line(s))
def extract_title_candidate_from_text(full_text):
    if not full_text:
        return ""
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    # Heuristic: skip common header words
    skip_words = ('abstract', 'keywords', 'introduction', 'acknowledg', 'copyright')
    # find first line of reasonable length that's not a header
    for ln in lines[:30]:
        low = ln.lower()
        if len(ln) > 20 and not any(low.startswith(s) for s in skip_words):
            return ln
    # fallback to first non-empty line
    return lines[0] if lines else ""

# similarity check (0..1)
def title_similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# In your main loop for each PDF (pseudocode snippet to replace the existing DOI logic):
#   - extract first_pages_text (e.g., first 2 pages) and full text (you already do)
#   - get DOI candidates via find_doi_candidates_in_pdf_text
#   - verify each via Crossref and title matching
#
# Example loop fragment (integrate into your existing code):

CROSSREF_MAILTO = "your_email@example.com"
SLEEP_BETWEEN_REQUESTS = 1.0
TITLE_SIM_THRESHOLD = 0.55   # tuneable: 0.5-0.7; lower for non-standard titles, higher for strict
HIGH_CONFIDENCE_TAGS = ('firstpages_label', 'firstpages_any')

# --- inside your for pdf in pdf_files loop ---
# text_full = extract_text_from_pdf(str(pdf))
# text_first_pages = extract_text_from_pdf(str(pdf))  # or modify extract_text to get first N pages separately
# For efficiency, you can call extract_text(path, maxpages=3) for first pages and only full if needed.

doi_candidates = find_doi_candidates_in_pdf_text(text_full, first_pages_text=text_first_pages)
pdf_title_candidate = extract_title_candidate_from_text(text_first_pages or text_full)

chosen = None
chosen_msg = None
chosen_confidence = 'none'
for doi_cand, tag in doi_candidates:
    # verify Crossref
    msg = query_crossref_by_doi(doi_cand, CROSSREF_MAILTO)
    time.sleep(SLEEP_BETWEEN_REQUESTS)
    if not msg:
        continue  # invalid DOI or not in Crossref
    # Crossref returned metadata: get its title
    cr_title = ""
    if isinstance(msg.get('title'), list) and msg.get('title'):
        cr_title = msg['title'][0]
    # score similarity vs PDF title
    sim = title_similarity(pdf_title_candidate, cr_title)
    # Accept with logic:
    # - If DOI found on first pages with label => accept if Crossref found (high confidence)
    # - Else accept only if title similarity >= threshold
    if tag in HIGH_CONFIDENCE_TAGS:
        chosen = doi_cand
        chosen_msg = msg
        chosen_confidence = 'high'
        break
    else:
        if sim >= TITLE_SIM_THRESHOLD:
            chosen = doi_cand
            chosen_msg = msg
            chosen_confidence = 'medium'
            break
        else:
            # if sim low, record as low-confidence candidate for manual review
            # continue searching other candidates
            continue

# After loop:
if chosen:
    # use chosen_msg as Crossref metadata (safe)
    # add to bib_db and ris_entries as before, but mark confidence in report
    report_rows.append([filename, chosen, 'matched', 'pdf_doi_candidate', chosen_confidence])
else:
    # no DOI confidently matched; consider title-based Crossref search as fallback
    # (only if pdf_title_candidate exists)
    report_rows.append([filename, '', 'no_match', '', 'no confident doi found'])