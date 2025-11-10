# epubstandard.py (Final version with "smart" two-pass parser logic)
import logging
import re
from typing import Dict, List
from lxml import etree
from pathlib import Path
import uuid

from utils import XHTML_NS, OPF_NS, DC_NS

# Define namespaces for XPath queries
XML_NS = {'xhtml': XHTML_NS, 'epub': 'http://www.idpf.org/2007/ops'}

def remove_broken_fragment_links_html(doc: etree._ElementTree) -> int:
    """
    Finds all internal links (e.g., <a href="#fn1">) and checks if the
    target ID (e.g., id="fn1") exists in the document.
    If not, "unwraps" the link, leaving only its text.
    """
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

def run_cleanup_and_semantics(doc: etree._ElementTree, config: dict, book_title: str) -> (int, int, int):
    """
    Runs all our "clean" XML-based enhancements.
    """
    banners_removed = 0
    blacklist_removed = 0
    semantics_added = 0
    
    # --- Banners ---
    banners = config.get('banners', [])
    for banner_config in banners:
        parent_tag = banner_config.get('parent_tag', 'div')
        text = banner_config.get('text_contains', '')
        if not text: continue
        xpath = f"//xhtml:{parent_tag}[contains(., '{text}')]"
        for elem in doc.xpath(xpath, namespaces=XML_NS):
            if elem.getparent() is not None:
                elem.getparent().remove(elem)
                banners_removed += 1
                
    # --- Blacklist ---
    blacklist = config.get('blacklist', {})
    for tag in blacklist.get('tags', []):
        for elem in doc.xpath(f"//xhtml:{tag}", namespaces=XML_NS):
            if elem.getparent() is not None: elem.getparent().remove(elem)
            blacklist_removed += 1
    for attr in blacklist.get('attributes', []):
        for elem in doc.xpath(f"//*[@{attr}]"):
            del elem.attrib[attr]
            blacklist_removed += 1
    for tag, attrs in blacklist.get('attributes_on_tags', {}).items():
        for attr in attrs:
            for elem in doc.xpath(f"//xhtml:{tag}[@{attr}]", namespaces=XML_NS):
                del elem.attrib[attr]
                blacklist_removed += 1

    # --- Semantics ---
    body = doc.find('.//xhtml:body', namespaces=XML_NS)
    if body is not None:
        if book_title:
            headings = body.xpath('.//xhtml:h1 | .//xhtml:h2', namespaces=XML_NS)
            for h in headings:
                if h.text and book_title in h.text:
                    if 'titlepage' not in (body.get(f"{{{XML_NS['epub']}}}type") or ''):
                        body.set(f"{{{XML_NS['epub']}}}type", 'bodymatter titlepage')
                        semantics_added += 1
                        break
        copyright_paras = body.xpath('.//xhtml:p[contains(., "Copyright")]', namespaces=XML_NS)
        for p in copyright_paras:
             if not p.xpath('ancestor::xhtml:section[@epub:type="copyright-page"]', namespaces=XML_NS):
                section = etree.Element(f"{{{XML_NS['xhtml']}}}section", attrib={f"{{{XML_NS['epub']}}}type": "copyright-page"})
                p.addprevious(section)
                section.append(p)
                semantics_added += 1
        chapter_headings = body.xpath('.//xhtml:h1 | .//xhtml:h2', namespaces=XML_NS)
        for h in chapter_headings:
            if h.text and re.match(r'^\s*chapter\s+\d+', h.text, re.IGNORECASE):
                if h.get(f"{{{XML_NS['epub']}}}type") != 'chapter':
                    h.set(f"{{{XML_NS['epub']}}}type", 'chapter')
                    semantics_added += 1
                    
    return banners_removed, blacklist_removed, semantics_added

def process_footnotes(xhtml_docs: List[Path]) -> Dict[str, int]:
    metrics = {"footnotes_processed": 0, "backlinks_added": 0}
    note_call_map = {}
    parser = etree.HTMLParser(recover=True)
    
    for doc_path in xhtml_docs:
        doc_changed = False
        try:
            doc = etree.parse(str(doc_path), parser)
            for link in doc.xpath('//a[starts-with(@href, "#fn") or starts-with(@href, "#note")]'):
                href = link.get('href', '')
                fragment = href.lstrip('#')
                backlink_id = f"backlink-{uuid.uuid4()}"
                link.set('id', backlink_id)
                link.set(f"{{{XML_NS['epub']}}}type", "noteref")
                if fragment not in note_call_map: note_call_map[fragment] = []
                full_backlink_href = f"{doc_path.name}#{backlink_id}"
                note_call_map[fragment].append(full_backlink_href)
                metrics["footnotes_processed"] += 1
                doc_changed = True
            if doc_changed:
                doc.write(str(doc_path), encoding='utf-8', method='html', doctype="<!DOCTYPE html>")
        except Exception as e:
            logging.warning(f"Footnote Pass 1 (Discovery) failed on {doc_path.name}: {e}")

    for doc_path in xhtml_docs:
        doc_changed = False
        try:
            doc = etree.parse(str(doc_path), parser)
            for note_id, backlink_hrefs in note_call_map.items():
                footnote_elements = doc.xpath(f'//*[@id="{note_id}"]')
                if footnote_elements:
                    footnote_element = footnote_elements[0]
                    footnote_element.set(f"{{{XML_NS['epub']}}}type", "footnote")
                    if not footnote_element.xpath('.//a[contains(@class, "backlink")]'):
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
                doc.write(str(doc_path), encoding='utf-8', method='html', doctype="<!DOCTYPE html>")
        except Exception as e:
            logging.warning(f"Footnote Pass 2 (Injection) failed on {doc_path.name}: {e}")
    return metrics

def process_epub(unzip_dir: Path, opf_tree: etree._ElementTree, opf_path: Path, config: dict) -> Dict[str, int]:
    total_metrics = {
        "banners_removed": 0, "blacklist_removed": 0, "footnotes_processed": 0,
        "backlinks_added": 0, "semantics_added": 0, "broken_links_removed": 0
    }
    
    opf_ns_map = {'opf': OPF_NS, 'dc': DC_NS}
    metadata = opf_tree.find('.//opf:metadata', namespaces=opf_ns_map)
    book_title_element = metadata.find('.//dc:title', namespaces=opf_ns_map) if metadata is not None else None
    book_title = book_title_element.text.strip() if book_title_element is not None and book_title_element.text else ""

    manifest_items = opf_tree.xpath("//opf:item[@media-type='application/xhtml+xml']", namespaces={'opf': OPF_NS})
    
    parser_xml = etree.XMLParser(recover=False) # Strict
    parser_html = etree.HTMLParser(recover=True) # Forgiving

    xhtml_docs_to_process = []
    for item in manifest_items:
        href = item.get('href')
        if not href: continue
            
        xhtml_file_path = (opf_path.parent / href).resolve()
        if not xhtml_file_path.is_file(): continue
            
        xhtml_docs_to_process.append(xhtml_file_path)
        
        # --- PASS 1: HEALER (HTML PARSER) ---
        # Always run the broken link healer on every file using the HTML parser
        # This fixes files like Guggenheim without breaking clean ones.
        broken_links = 0
        try:
            doc_html = etree.parse(str(xhtml_file_path), parser_html)
            broken_links = remove_broken_fragment_links_html(doc_html)
            if broken_links > 0:
                total_metrics["broken_links_removed"] += broken_links
                doc_html.write(str(xhtml_file_path), encoding='utf-8', method='html', doctype="<!DOCTYPE html>")
        except Exception as e:
            logging.error(f"Error during HTML healing of {xhtml_file_path.name}: {e}")
            continue # Skip this file if healing fails catastrophically

        # --- PASS 2: ENHANCER (XML PARSER) ---
        # Now, try to run all the "clean" XML-based enhancements.
        # This will work on the 15 good files and the newly-healed Guggenheim file.
        changes = 0
        try:
            doc_xml = etree.parse(str(xhtml_file_path), parser_xml)
            
            b, bl, s = run_cleanup_and_semantics(doc_xml, config, book_title)
            total_metrics["banners_removed"] += b
            total_metrics["blacklist_removed"] += bl
            total_metrics["semantics_added"] += s
            changes = b + bl + s

            if changes > 0:
                doc_xml.write(str(xhtml_file_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
                
        except etree.XMLSyntaxError:
            # This should now only happen if a file is *truly* malformed XML
            # (which shouldn't be the case after our epub3_upgrade.py fixes)
            logging.warning(f"File {xhtml_file_path.name} is not valid XML. Skipping XML enhancements.")
        except Exception as e:
            logging.error(f"Error during XML enhancement of {xhtml_file_path.name}: {e}")

    # --- PASS 3: FOOTNOTES (HTML PARSER) ---
    # Footnotes are messy. We always run this last, using the HTML parser.
    if xhtml_docs_to_process:
        footnote_metrics = process_footnotes(xhtml_docs_to_process)
        total_metrics.update(footnote_metrics)

    return total_metrics