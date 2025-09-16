# epubstandard v1.3.5

A post-ABBYY EPUB cleanup and accessibility tool.

---

## Required
**`get yourself a nice comfy venv`**  
```bash
python -m venv venv
source venv/bin/activate
pip install lxml
```

---

## Input

**`--src PATH`**  
The source directory where your `.epub` files live.  
The script will scan this folder (and subfolders) for EPUBs to process.

---

## Output / Destination

**`--dest PATH`** *(default: `out`)*  
Where to put the cleaned EPUBs. Ignored if you use `--inplace`.

**`--inplace`**  
Instead of writing cleaned copies to `--dest`, overwrite the originals inside `--src`.

---

## Processing Behavior

**`--force`**  
Run again even if the EPUB already has a `cleanup:processed-by` marker with the same config.  
(Normally the tool skips files that were already processed with the same settings.)

**`--dry-run`**  
Simulate all changes but don’t actually write any EPUBs.  
Useful for checking the report before touching files.

---

## Banner (journal header/footer) cleanup

**`--no-banners`**  
Turn off banner detection/removal entirely.

**`--banner-min-repeat FLOAT`** *(default: 0.6)*  
Minimum fraction of documents where a banner must repeat to be removed.  
Example: `0.6` = if the snippet appears in ≥60% of spine files, it’s considered boilerplate.

**`--banner-top-chars INT`** *(default: 150)*  
How many characters from the top of each spine file to check for a repeating banner.

**`--banner-bottom-chars INT`** *(default: 150)*  
Same, but for the bottom of each spine file.

---

## Blacklist

**`--blacklist FILE`**  
Path to a text file of regex patterns (one per line).  
Any short line (≤160 chars) matching a pattern is stripped.  
(e.g. `^This article is published under …`)

---

## Fixes Applied

- Strip soft hyphens (U+00AD).  
- Rejoin words split across linebreaks / hyphens (with a whitelist of real compounds).  
- Merge **illegitimate paragraph breaks** (when ABBYY split mid-sentence).  
- Collapse redundant empty `<p>` elements.  
- Normalize cover page and manifest metadata.  
- Normalize navigation and ensure `<meta charset="utf-8">` is present.  
- Add bidirectional note links (in-text ref ↔ footnote block).  
- Ensure note blocks carry `role="doc-footnote"` for accessibility.  
- Optional removal of repeating journal banners.  
- Optional blacklist-based removal of boilerplate lines.  
- Mark EPUBs with `cleanup:processed-by` metadata for idempotence.

---

## Outputs

- Cleaned EPUBs (in `--dest` or in place).  
- A CSV report named `epubstandard_report_<timestamp>.csv` in the same folder as output, with counts of:
  - linebreak fixes  
  - forward links repaired  
  - backlinks added  
  - symbol/numeric note IDs created  
  - soft hyphens removed  
  - empty paragraphs collapsed  
  - blacklist removals  
  - banners removed / kept  
  - **illegitimate paragraphs merged**  

---

## Example usage

```bash
# Clean to a new output directory
python epubstandard.py --src input/ --dest output/

# Clean in place (overwrite originals)
python epubstandard.py --src input/ --inplace

# Dry run (no files written, just a report)
python epubstandard.py --src input/ --dest output/ --dry-run

# With a blacklist of patterns
python epubstandard.py --src input/ --dest output/ --blacklist cleanup_blacklist.txt
```

---

## Notes

- Each run produces a fresh CSV report for auditing.  
- EPUBs are re-packed with strict XML/XHTML where required, HTML elsewhere for safety.  
- Accessibility enhancements are ongoing — see TODOs in the source for planned features (e.g., alt-text generation for images).
