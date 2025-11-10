#!/usr/bin/env python3
import argparse
import csv
import logging
from pathlib import Path
import shutil
import yaml
import pandas as pd # Import pandas for the tally

# Import the refactored modules
import epub3_upgrade
import epubfix

def setup_logging(output_dir: Path):
    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # --- FIX: Changed path from "log_dir / 'logs/epubstandard.log'" to "log_dir / 'epubstandard.log'" ---
    logging.basicConfig(
        filename=log_dir / "epubstandard.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    # -------------------------------------------------------------------------------------------------

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.getLogger().addHandler(console)

def main():
    parser = argparse.ArgumentParser(description="Full EPUB processing pipeline with enhanced debugging.")
    parser.add_argument("--input", required=True, help="Input directory with EPUBs")
    parser.add_argument("--output", required=True, help="Output directory for processed EPUBs and report")
    args = parser.parse_args()

    print("--- Script Started ---") 
    
    in_dir = Path(args.input)
    out_dir = Path(args.output)
    
    if not in_dir.is_dir():
        print(f"[FATAL] Input directory not found at: {in_dir}")
        return
        
    out_dir.mkdir(exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True) # Ensure logs dir exists
    setup_logging(out_dir)

    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
        print("--- Config file loaded successfully ---")
    except FileNotFoundError:
        logging.error("FATAL: config.yaml not found. Please create it.")
        return

    report_path = out_dir / "epubstandard_report.csv"
    epub_files = sorted(in_dir.glob("*.epub"))
    
    if not epub_files:
        print(f"[FATAL] No .epub files found in the input directory: {in_dir}")
        return
        
    print(f"--- Found {len(epub_files)} EPUB file(s) to process ---")

    headers = [
        "filename", "status", "message", 
        "before_errors", "before_warnings", 
        "after_errors", "after_warnings", "used_healed",
        "broken_links_removed", "banners_removed", "blacklist_removed",
        "semantics_added", "footnotes_processed", "backlinks_added"
    ]

    with open(report_path, "w", newline="", encoding='utf-8') as f:
        writer_csv = csv.writer(f)
        writer_csv.writerow(headers)
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')

        for i, epub_file in enumerate(epub_files, 1):
            logging.info(f"\n--- [{i}/{len(epub_files)}] Processing: {epub_file.name} ---")
            temp_epub = out_dir / epub_file.name
            shutil.copy(epub_file, temp_epub)

            try:
                logging.info("Step 1: Running EPUB3 Upgrade...")
                upgrade_ok = epub3_upgrade.run_upgrade(temp_epub, temp_epub)
                if not upgrade_ok:
                    logging.error("EPUB3 upgrade failed.")
                    writer.writerow({"filename": epub_file.name, "status": "ERROR", "message": "EPUB3 upgrade failed"})
                    continue
                logging.info("EPUB3 Upgrade successful.")

                logging.info("Step 2: Running EPUBFix Heal Process...")
                result = epubfix.process_with_fix(temp_epub, config)
                logging.info("EPUBFix Heal Process completed.")
                
                result['filename'] = epub_file.name
                writer.writerow(result)

            except Exception as e:
                logging.error(f"FATAL unhandled exception for {epub_file.name}: {e}", exc_info=True)
                writer.writerow({"filename": epub_file.name, "status": "FATAL ERROR", "message": str(e)})

    # --- FINAL TALLY ---
    print("\n--- Processing Complete ---")
    try:
        report_df = pd.read_csv(report_path)
        total_files = len(report_df)
        success_count = report_df[report_df['after_errors'] == 0].shape[0]
        fail_count = total_files - success_count
        
        print(f"Total files processed: {total_files}")
        print(f"✅ Succeeded (0 errors): {success_count}")
        print(f"❌ Failed (>0 errors): {fail_count}")

        if fail_count > 0:
            print("\nFiles with remaining errors:")
            for _, row in report_df[report_df['after_errors'] > 0].iterrows():
                print(f"  - {row['filename']} (Errors: {int(row['after_errors'])})")
    except Exception as e:
        print(f"Error generating tally: {e}")
        print("--- Script Finished ---")

if __name__ == "__main__":
    main()