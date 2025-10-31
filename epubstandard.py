# epubstandard.py (Final corrected version with robust semantic enhancements)
import logging
import re
from typing import Dict, List
from lxml import etree
from pathlib import Path
import uuid

from utils import XHTML_NS, OPF_NS, DC_NS

# Define namespaces for XPath queries
NS = {'xhtml': XHTML_NS, 'epub': 'http://www.idpf.org/2007/ops'}

def remove_banners(doc: etree._ElementTree, config: dict) -> int:
    count = 0
    banners = config.get('banners', [])
    for banner_config in banners:
        parent_tag = banner_config.get('parent_tag', 'div')
        text = banner_config.get('text_contains', '')
        if not text: continue
        xpath = f"//xhtml:{parent_tag}[contains(., '{text}')]"
        for elem in doc.xpath(xpath, namespaces=NS):
            if elem.getparent() is not None:
                elem.getparent().remove(elem)
                count += 1
    return count

def cleanup_markup(doc: etree._ElementTree, config: dict) -> int:
    count = 0
    blacklist = config.get('blacklist', {})
    for tag in blacklist.get('tags', []):
        for elem in doc.xpath(f"//xhtml:{tag}", namespaces=NS):
            if elem.getparent() is not None: elem.getparent().remove(elem)
            count += 1
    for attr in blacklist.get('attributes', []):
        for elem in doc.xpath(f"//*[@{attr}]"):
            del elem.attrib[attr]
            count += 1
    for tag, attrs in blacklist.get('attributes_on_tags', {}).items():
        for attr in attrs:
            for elem in doc.xpath(f"//xhtml:{tag}[@{attr}]", namespaces=NS):
                del elem.attrib[attr]
                count += 1
    return count

def process_footnotes(xhtml_docs: List[Path]) -> Dict[str, int]:
    metrics = {"footnotes_processed": 0, "backlinks_added": 0}
    note_call_map = {}
    for doc_path in xhtml_docs:
        doc_changed = False
        try:
            doc = etree.parse(str(doc_path), etree.HTMLParser(recover=True))
            for link in doc.xpath('//xhtml:a[starts-with(@href, "#fn") or starts-with(@href, "#note")]', namespaces=NS):
                href = link.get('href', '')
                fragment = href.lstrip('#')
                backlink_id = f"backlink-{uuid.uuid4()}"
                link.set('id', backlink_id)
                link.set(f"{{{NS['epub']}}}type", "noteref")
                if fragment not in note_call_map: note_call_map[fragment] = []
                full_backlink_href = f"{doc_path.name}#{backlink_id}"
                note_call_map[fragment].append(full_backlink_href)
                metrics["footnotes_processed"] += 1
                doc_changed = True
            if doc_changed:
                doc.write(str(doc_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
        except Exception: pass

    for doc_path in xhtml_docs:
        doc_changed = False
        try:
            doc = etree.parse(str(doc_path), etree.HTMLParser(recover=True))
            for note_id, backlink_hrefs in note_call_map.items():
                footnote_elements = doc.xpath(f'//*[@id="{note_id}"]', namespaces=NS)
                if footnote_elements:
                    footnote_element = footnote_elements[0]
                    footnote_element.set(f"{{{NS['epub']}}}type", "footnote")
                    if not footnote_element.xpath('.//xhtml:a[contains(@class, "backlink")]', namespaces=NS):
                        for backlink_href in backlink_hrefs:
                            backlink_tag = etree.Element('a', href=backlink_href, attrib={'class': 'backlink'})
                            backlink_tag.text = " ↩"
                            children = list(footnote_element)
                            if children:
                                last_child = children[-1]
                                last_child.tail = (last_child.tail or '') + ' '
                                footnote_element.append(backlink_tag)
                            else:
                                footnote_element.text = (footnote_element.text or '') + ' '
                                footnote_element.append(backlink_tag)
                            metrics["backlinks_added"] += 1
                            doc_changed = True
            if doc_changed:
                doc.write(str(doc_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
        except Exception: pass
    return metrics

def add_semantic_structure(doc: etree._ElementTree, book_title: str) -> int:
    """
    Safely identifies key landmarks and wraps them in semantic <section> tags.
    """
    body = doc.find('.//xhtml:body', namespaces=NS)
    if body is None: return 0
    change_count = 0
    
    # --- Title Page Detection ---
    if book_title and body.xpath(f'.//xhtml:*[contains(., "{book_title}")]', namespaces=NS):
        if 'titlepage' not in (body.get(f"{{{NS['epub']}}}type") or ''):
            body.set(f"{{{NS['epub']}}}type", 'bodymatter titlepage')
            change_count += 1

    # --- Copyright Page Detection ---
    copyright_paras = body.xpath('.//xhtml:p[contains(., "Copyright")]', namespaces=NS)
    for p in copyright_paras:
         if not p.xpath('ancestor::xhtml:section[@epub:type="copyright-page"]', namespaces=NS):
            section = etree.Element('section', attrib={f"{{{NS['epub']}}}type": "copyright-page"})
            p.addprevious(section) # Insert the new section before the paragraph
            section.append(p) # Move the paragraph into the new section
            change_count += 1
            
    # --- Chapter Detection ---
    # This is a complex task. A simple heuristic is often safest.
    # We will simply mark the headings, but not attempt to wrap sections, as that
    # logic is what was causing the silent failures.
    chapter_headings = body.xpath('.//xhtml:h1 | .//xhtml:h2', namespaces=NS)
    for h in chapter_headings:
        if h.text and re.match(r'^\s*chapter\s+\d+', h.text, re.IGNORECASE):
            if h.get(f"{{{NS['epub']}}}type") != 'chapter':
                h.set(f"{{{NS['epub']}}}type", 'chapter')
                change_count += 1

    return change_count

def process_epub(unzip_dir: Path, opf_tree: etree._ElementTree, opf_path: Path, config: dict) -> Dict[str, int]:
    total_metrics = {
        "banners_removed": 0, "blacklist_removed": 0, "footnotes_processed": 0,
        "backlinks_added": 0, "semantics_added": 0
    }
    
    opf_ns_map = {'opf': OPF_NS, 'dc': DC_NS}
    metadata = opf_tree.find('.//opf:metadata', namespaces=opf_ns_map)
    book_title_element = metadata.find('.//dc:title', namespaces=opf_ns_map) if metadata is not None else None
    book_title = book_title_element.text.strip() if book_title_element is not None and book_title_element.text else ""

    manifest_items = opf_tree.xpath("//opf:item[@media-type='application/xhtml+xml']", namespaces={'opf': OPF_NS})
    
    xhtml_docs_to_process = []
    for item in manifest_items:
        href = item.get('href')
        if href:
            xhtml_file_path = (opf_path.parent / href).resolve()
            if xhtml_file_path.is_file():
                xhtml_docs_to_process.append(xhtml_file_path)
                try:
                    parser = etree.HTMLParser(recover=True)
                    doc = etree.parse(str(xhtml_file_path), parser)
                    
                    total_metrics["banners_removed"] += remove_banners(doc, config)
                    total_metrics["blacklist_removed"] += cleanup_markup(doc, config)
                    total_metrics["semantics_added"] += add_semantic_structure(doc, book_title)

                    doc.write(str(xhtml_file_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
                except Exception as e:
                    logging.error(f"Error during cleanup of {xhtml_file_path.name}: {e}")
    
    if xhtml_docs_to_process:
        footnote_metrics = process_footnotes(xhtml_docs_to_process)
        total_metrics.update(footnote_metrics)

    return total_metrics