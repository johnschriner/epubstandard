# epubstandard v1.3

A post-ABBYY EPUB cleanup tool.

---

## Required
**`pip install lxml`**  

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

## Misc

**`--audit`**  
Reserved flag. Right now it just enables some extra diff output in future extensions.  
Safe to ignore unless you want verbose diagnostics.

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
  - banners removed  

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
