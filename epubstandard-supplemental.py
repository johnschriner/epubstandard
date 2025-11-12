# epubstandard-supplemental.py (v2 - Fixes HTML write method)
import logging
import re
from typing import Dict, List
from lxml import etree
from pathlib import Path
import uuid
import zipfile
import shutil
import sys
import os

# Attempt to import from local 'utils.py'
try:
    from utils import XHTML_NS, OPF_NS, DC_NS
except ImportError:
    logging.error("Failed to import 'utils.py'. Make sure it's in the same directory.")
    # Define fallbacks so script can at least try to run
    XHTML_NS = "http://www.w3.org/1999/xhtml"
    OPF_NS = "http://www.idpf.org/2007/opf"
    DC_NS = "http://purl.org/dc/elements/1.1/"

# Define namespaces for XPath queries
XML_NS = {'xhtml': XHTML_NS, 'epub': 'http://www.idpf.org/2007/ops'}

# --- Copied from main script ---
def remove_broken_fragment_links_html(doc: etree._ElementTree) -> int:
    count = 0
    links = doc.xpath('//a[starts-with(@href, "#")]') 
    all_ids = set(doc.xpath('//@id'))
    for link in links:
        href = link.get('href', '')
        fragment = href.lstrip('#')
        if fragment and fragment not in all_ids:
            try:
                parent = link.getparent()
                if parent is None: continue
                text = (link.text or '')
                tail = (link.tail or '')
                prev = link.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or '') + text + tail
                else:
                    parent.text = (parent.text or '') + text + tail
                parent.remove(link)
                count += 1
            except Exception as e:
                logging.warning(f"Failed to remove broken link {href}: {e}")
    return count

# --- NEW FUNCTION ---
def fix_nested_links_html(doc: etree._ElementTree) -> int:
    """
    Finds all <a> tags that are descendants of other <a> tags
    and converts the inner <a> tag to a <span>, preserving its
    children and text, but removing its "link-ness".
    """
    count = 0
    nested_links = doc.xpath('//a//a')
    
    for link in nested_links:
        try:
            link.tag = 'span'
            link.set('class', 'unwrapped-nested-link')
            
            if 'href' in link.attrib:
                del link.attrib['href']
            if f"{{{XML_NS['epub']}}}type" in link.attrib:
                del link.attrib[f"{{{XML_NS['epub']}}}type"]
                
            count += 1
        except Exception as e:
            logging.warning(f"Failed to unwrap nested link: {e}")
            
    if count > 0:
        logging.info(f"Unwrapped {count} nested <a> tags.")
    return count

# --- Main Processing Logic ---
def process_single_epub(epub_file: Path, output_dir: Path):
    if not epub_file.exists():
        logging.error(f"File not found: {epub_file}")
        return

    unzip_dir = output_dir / f"{epub_file.stem}_unzipped"
    if unzip_dir.exists():
        shutil.rmtree(unzip_dir)
    unzip_dir.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"Processing: {epub_file.name}")

    # --- 1. Unzip ---
    try:
        with zipfile.ZipFile(epub_file, 'r') as zip_ref:
            zip_ref.extractall(unzip_dir)
    except Exception as e:
        logging.error(f"Failed to unzip {epub_file.name}: {e}")
        return

    # --- 2. Find OPF ---
    opf_path = None
    try:
        container_xml_path = unzip_dir / 'META-INF' / 'container.xml'
        if not container_xml_path.exists():
            logging.error("No META-INF/container.xml found.")
            return

        container_tree = etree.parse(str(container_xml_path))
        rootfile_path = container_tree.find('.//{*}rootfile').get('full-path')
        opf_path = (unzip_dir / rootfile_path).resolve()
        
        if not opf_path.exists():
            logging.error(f"OPF file not found at: {rootfile_path}")
            return
            
        opf_tree = etree.parse(str(opf_path))
    except Exception as e:
        logging.error(f"Failed to parse container.xml or opf: {e}")
        shutil.rmtree(unzip_dir)
        return

    # --- 3. Process XHTML Files ---
    total_nested_fixed = 0
    total_broken_fixed = 0
    
    try:
        manifest_items = opf_tree.xpath("//opf:item[@media-type='application/xhtml+xml']", namespaces={'opf': OPF_NS})
        parser_html = etree.HTMLParser(recover=True)
        
        xhtml_files_to_process = []
        for item in manifest_items:
            href = item.get('href')
            if not href: continue
            xhtml_file_path = (opf_path.parent / href).resolve()
            if xhtml_file_path.is_file():
                xhtml_files_to_process.append(xhtml_file_path)

        for xhtml_path in xhtml_files_to_process:
            try:
                doc = etree.parse(str(xhtml_path), parser_html)
                
                # --- RUN THE FIXES ---
                nested_fixed = fix_nested_links_html(doc)
                broken_fixed = remove_broken_fragment_links_html(doc)
                
                if nested_fixed > 0 or broken_fixed > 0:
                    logging.info(f"  Fixed {xhtml_path.name}: {nested_fixed} nested, {broken_fixed} broken links")
                    
                    # --- THIS IS THE FIX ---
                    # Write as 'xml' to force self-closing tags (e.g., <br/>)
                    # This is required for EPUB/XHTML compliance.
                    doc.write(str(xhtml_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
                    # -----------------------

                    total_nested_fixed += nested_fixed
                    total_broken_fixed += broken_fixed
                    
            except Exception as e:
                logging.warning(f"  Could not process {xhtml_path.name}: {e}")

    except Exception as e:
        logging.error(f"An error occurred during XHTML processing: {e}")
        shutil.rmtree(unzip_dir)
        return

    # --- 4. Re-zip EPUB ---
    output_epub_path = output_dir / epub_file.name
    logging.info(f"Total fixes: {total_nested_fixed} nested, {total_broken_fixed} broken links.")
    
    try:
        with zipfile.ZipFile(output_epub_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            mimetype_path = unzip_dir / 'mimetype'
            if mimetype_path.exists():
                 zipf.write(mimetype_path, arcname='mimetype', compress_type=zipfile.ZIP_STORED)
            
            for file_path in unzip_dir.rglob('*'):
                if file_path.name == 'mimetype':
                    continue 
                arcname = file_path.relative_to(unzip_dir)
                zipf.write(file_path, arcname=arcname)
                
        logging.info(f"Successfully created fixed EPUB: {output_epub_path}")
        
    except Exception as e:
        logging.error(f"Failed to re-zip file: {e}")
    finally:
        # --- 5. Clean up ---
        shutil.rmtree(unzip_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    
    BASE_DIR = Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DDIR = BASE_DIR / 'output' / 'fixed'
    
    if not OUTPUT_DIR.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) < 2:
        logging.error("Usage: python epubstandard-supplemental.py <file1.epub> <file2.epub> ...")
        sys.exit(1)
        
    input_files = [Path(f) for f in sys.argv[1:]]
    
    for epub_file in input_files:
        # Rerun on the *original* file
        process_single_epub(epub_file, OUTPUT_DIR)