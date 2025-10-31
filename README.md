# EPUB Quality Assurance & Enhancement Pipeline

This project is a collection of Python scripts designed to upgrade, clean, validate, and enhance EPUB files, transforming them from basic exports into high-quality, accessible, and repository-ready digital documents.

The pipeline is built to be run on a directory of EPUB files, automating a complex series of improvements and fixes.

## Key Features

This pipeline automatically processes EPUBs to:

* **Upgrade to EPUB 3**: Converts legacy EPUB 2 files to the modern EPUB 3.2 standard.
* **Resolve Validation Errors**: Systematically fixes common `epubcheck` errors, including:
    * Ensuring a valid, non-empty `<title>` tag in all XHTML files.
    * Creating a single, compliant `dcterms:modified` timestamp.
    * Fixing unique identifier-related metadata.
* **Generate Table of Contents**: Automatically builds a complete and valid `nav.xhtml` file, including a nested Table of Contents (ToC) and accessibility Landmarks (e.g., "Start of Content").
* **Enhance Footnotes**: Scans the text and creates a fully accessible, bidirectional footnote system.
    * Adds `epub:type="noteref"` to footnote links.
    * Adds `epub:type="footnote"` to the footnote content.
    * Inserts `↩` backlinks to return the reader to their original spot.
* **Add Semantic Structure**: Enriches the document for accessibility by identifying key sections (like title pages, copyright pages, and chapters) and wrapping them in semantic tags (e.g., `<section epub:type="chapter">`).
* **Clean Content**: Removes specified "banner" text (like "Scanned by...") and blacklisted HTML tags/attributes, all configurable via `config.yaml`.

## File Structure

* `epubstandard_all.py`: **(Main Script)** The primary entry point. Runs the full pipeline on a directory.
* `epub3_upgrade.py`: Handles the EPUB 2-to-3 upgrade, fixes OPF metadata, and generates the `nav.xhtml`.
* `epubstandard.py`: The core content enhancement module. Cleans XHTML, processes footnotes, and adds semantic structure.
* `epubfix.py`: A "healer" script that uses `epubcheck` to validate, apply fixes, and re-validate.
* `utils.py`: Shared utility functions (zipping, unzipping, finding OPF, running `epubcheck`).
* `config.yaml`: External configuration file for customizing banners and blacklisted tags.
* `csvtoconsole.py`: A utility to print a human-readable summary of the CSV report.
* `logs/`: Directory where detailed processing logs are stored.

## Requirements

* Python 3.6+
* Python libraries: `lxml`, `pyyaml`, `pandas`
* **EpubCheck**: A working installation of `epubcheck` is required. The script will try to find it on your system `PATH`.
    * If it's not found, you must set the `EPUBCHECK` environment variable to point to the `epubcheck.jar` file.

### Installation

1.  **Install Python Dependencies:**
    ```bash
    pip install lxml pyyaml pandas
    ```

2.  **Install EpubCheck:**
    * On Debian/Ubuntu: `sudo apt install epubcheck`
    * Or, [download the .jar file](https://github.com/w3c/epubcheck/releases) and set the environment variable:
        ```bash
        export EPUBCHECK="/path/to/your/epubcheck.jar"
        ```

## How to Run the Pipeline

1.  **Configure**: Edit `config.yaml` to add any banners or blacklisted tags you want to remove.
2.  **Place EPUBs**: Add your source EPUB files to the `input/` directory.
3.  **Run the Script**: From your terminal, run the main script. Make sure to set the `EPUBCHECK` variable if needed.

    ```bash
    EPUBCHECK="/usr/bin/epubcheck" python epubstandard_all.py --input input/ --output output/
    ```

4.  **Review Results**:
    * The processed, enhanced EPUBs will be in the `output/` directory.
    * A detailed `epubstandard_report.csv` file will be created in `output/`.
    * Detailed logs are saved in the `logs/` directory.

5.  **View Summary**: For a quick summary, `cd` into the output directory and run the `csvtoconsole.py` script (you'll need to use the full path to the script).
    ```bash
    cd output/
    python ../csvtoconsole.py
    ```
