# Failure Note: Attention Is All You Need

Target output requested:

- `papers/attention-is-all-you-need/index.html`
- assets under `papers/attention-is-all-you-need/assets/`

The final HTML report was not generated because the required PDF acquisition and parsing preflight could not be completed.

## What was attempted

1. The user did not provide a PDF or web URL, so the paper was resolved by title to the known arXiv identifier `1706.03762`.
2. Direct PDF download was attempted from:
   - `https://arxiv.org/pdf/1706.03762`
3. The local shell download failed because DNS resolution is unavailable in this execution environment:
   - `curl: (6) Could not resolve host: arxiv.org`
4. Node `fetch()` was also tested against the same PDF URL and failed:
   - `TypeError: fetch failed`
5. The workspace and writable temp areas were searched for an existing PDF matching the title or arXiv identifier; no usable PDF was found.
6. Local PDF tooling was checked:
   - `pypdf` is available.
   - `pdfinfo` and `pdftoppm` are not available.
   - `pdfplumber`, `Pillow`, and `PyMuPDF` are not available.

## Why no HTML was generated

The repository instructions require confirmation that:

- the PDF was successfully obtained;
- the title, authors, abstract, and section structure were read from the PDF;
- figures, tables, algorithms, and equations were located;
- key figures/tables were cropped from the PDF pages;
- the report is not based only on arXiv or web summaries.

Those checks could not be completed without a local readable PDF and figure/table extraction path. Generating `index.html` from memory, public snippets, or only a web summary would violate the project instructions.

## Required next step

Place a readable copy of the paper PDF at:

`papers/attention-is-all-you-need/attention-is-all-you-need.pdf`

Then rerun the paper-reading workflow so the PDF text and figure/table crops can be extracted before creating the final offline HTML.
