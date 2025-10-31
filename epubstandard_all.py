#!/usr/bin/env python3
import argparse
import csv
import logging
from pathlib import Path
import shutil
import yaml

# Import the refactored modules
import epub3_upgrade
import epubfix

def setup_logging(output_dir: Path):
    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "epubstandard.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    # Add a console handler to see logs in the terminal
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.getLogger().addHandler(console)

def main():
    parser = argparse.ArgumentParser(description="Full EPUB processing pipeline with enhanced debugging.")
    parser.add_argument("--input", required=True, help="Input directory with EPUBs")
    parser.add_argument("--output", required=True, help="Output directory for processed EPUBs and report")
    args = parser.parse_args()

    print("--- Script Started ---") # DEBUG
    
    in_dir = Path(args.input)
    out_dir = Path(args.output)
    
    if not in_dir.is_dir():
        print(f"[FATAL] Input directory not found at: {in_dir}")
        return
        
    out_dir.mkdir(exist_ok=True)
    setup_logging(out_dir)

    # Load configuration
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
        print("--- Config file loaded successfully ---") # DEBUG
    except FileNotFoundError:
        logging.error("FATAL: config.yaml not found. Please create it.")
        return

    report_path = out_dir / "epubstandard_report.csv"
    epub_files = sorted(in_dir.glob("*.epub"))
    
    if not epub_files:
        print(f"[FATAL] No .epub files found in the input directory: {in_dir}")
        return
        
    print(f"--- Found {len(epub_files)} EPUB file(s) to process ---") # DEBUG

    with open(report_path, "w", newline="", encoding='utf-8') as f:
        writer = csv.writer(f)
        headers = ["filename", "status", "message", "before_errors", "before_warnings", "after_errors", "after_warnings", "used_healed"]
        writer.writerow(headers)

        for i, epub_file in enumerate(epub_files, 1):
            logging.info(f"\n--- [{i}/{len(epub_files)}] Processing: {epub_file.name} ---")
            temp_epub = out_dir / epub_file.name
            shutil.copy(epub_file, temp_epub)

            try:
                logging.info("Step 1: Running EPUB3 Upgrade...")
                upgrade_ok = epub3_upgrade.run_upgrade(temp_epub, temp_epub)
                if not upgrade_ok:
                    logging.error("EPUB3 upgrade failed.")
                    writer.writerow([epub_file.name, "ERROR", "EPUB3 upgrade failed", "", "", "", "", ""])
                    continue
                logging.info("EPUB3 Upgrade successful.")

                logging.info("Step 2: Running EPUBFix Heal Process...")
                result = epubfix.process_with_fix(temp_epub, config)
                logging.info("EPUBFix Heal Process completed.")
                
                if result.get('status') == 'OK':
                    writer.writerow([
                        epub_file.name, "OK", "",
                        result.get("before_errors"), result.get("before_warnings"),
                        result.get("after_errors"), result.get("after_warnings"),
                        result.get("used_healed")
                    ])
                else:
                    writer.writerow([epub_file.name, "ERROR", result.get('message', 'Unknown error in epubfix'), "", "", "", "", ""])

            except Exception as e:
                logging.error(f"FATAL unhandled exception for {epub_file.name}: {e}", exc_info=True)
                writer.writerow([epub_file.name, "FATAL ERROR", str(e), "", "", "", "", ""])

    print("--- Script Finished ---") # DEBUG

if __name__ == "__main__":
    main()