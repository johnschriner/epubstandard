#!/usr/bin/env python3
import shutil
import tempfile
from pathlib import Path
import yaml
import logging

from utils import (find_opf_and_basedir, repack_epub, run_epubcheck, unzip_epub)
import epubstandard

def heal_epub(epub_path: Path, config: dict) -> Path:
    """Unzips, runs the full epubstandard processing, and repacks."""
    unzip_dir = Path(tempfile.mkdtemp(suffix="_epubfix"))
    healed_path = Path(tempfile.mkstemp(suffix=".epub")[1])

    try:
        unzip_epub(epub_path, unzip_dir)
        opf_path, _, opf_tree = find_opf_and_basedir(unzip_dir)

        if opf_tree is not None:
            epubstandard.process_epub(unzip_dir, opf_tree, opf_path, config)
            repack_epub(unzip_dir, healed_path)
        else:
            logging.error(f"Skipping healing for {epub_path.name} as OPF could not be found.")
            return epub_path # Return original path if it cannot be processed

    finally:
        shutil.rmtree(unzip_dir, ignore_errors=True)

    return healed_path

def process_with_fix(epub_path: Path, config: dict) -> dict:
    """Run epubcheck -> heal -> epubcheck; replace if errors do not increase."""
    be, bw, bm = run_epubcheck(epub_path)
    if be == -1: # Initial epubcheck failed, cannot proceed
        return {"status": "ERROR", "message": bm}

    healed_path = heal_epub(epub_path, config)
    if healed_path == epub_path: # Healing was skipped
         return {"status": "OK", "before_errors": be, "before_warnings": bw, "after_errors": be, "after_warnings": bw, "used_healed": False}


    ae, aw, am = run_epubcheck(healed_path)

    used_healed = False
    if ae <= be:
        shutil.copyfile(healed_path, epub_path)
        used_healed = True
    
    if healed_path.exists():
        healed_path.unlink()

    return {
        "status": "OK",
        "before_errors": be, "before_warnings": bw, "before_messages": bm,
        "after_errors": ae, "after_warnings": aw, "after_messages": am,
        "used_healed": used_healed
    }