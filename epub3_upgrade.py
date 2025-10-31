#!/usr/bin/env python3
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from lxml import etree
from datetime import datetime
from utils import find_opf_and_basedir, repack_epub, unzip_epub, OPF_NS, DC_NS, XHTML_NS

# Define the namespaces for XPath queries
NS = {
    'opf': OPF_NS, 'dc': DC_NS, 'xhtml': XHTML_NS,
    'epub': "http://www.idpf.org/2007/ops"
}

def ensure_unique_identifier(opf_tree: etree._ElementTree, metadata: etree._Element):
    package = opf_tree.getroot()
    uid_ref = "book-id"
    package.set('unique-identifier', uid_ref)
    if not metadata.xpath(f"./dc:identifier[@id='{uid_ref}']", namespaces=NS):
        existing_identifier = metadata.find('./dc:identifier', namespaces=NS)
        if existing_identifier is not None:
            existing_identifier.set('id', uid_ref)
        else:
            new_identifier = etree.SubElement(metadata, f"{{{DC_NS}}}identifier", id=uid_ref)
            new_identifier.text = f"urn:uuid:{uuid.uuid4()}"

def ensure_single_modified_date(metadata: etree._Element):
    for elem in metadata.xpath('./opf:meta[@property="dcterms:modified"]', namespaces=NS):
        metadata.remove(elem)
    modified_meta = etree.SubElement(metadata, 'meta', property="dcterms:modified")
    modified_meta.text = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def fix_xhtml_structure_and_title(opf_tree: etree._ElementTree, opf_path: Path):
    metadata = opf_tree.find('.//opf:metadata', namespaces=NS)
    book_title_element = metadata.find('.//dc:title', namespaces=NS)
    book_title = book_title_element.text.strip() if book_title_element is not None and book_title_element.text else "Untitled"
    manifest_items = opf_tree.xpath("//opf:item[@media-type='application/xhtml+xml']", namespaces=NS)
    parser = etree.HTMLParser(recover=True)
    for item in manifest_items:
        href = item.get('href')
        if not href: continue
        xhtml_path = (opf_path.parent / href).resolve()
        if xhtml_path.is_file():
            try:
                doc = etree.parse(str(xhtml_path), parser)
                html_root = doc.getroot()
                if html_root is None: continue
                for head in html_root.xpath('/html/head'):
                    html_root.remove(head)
                new_head = etree.Element('head')
                title_el = etree.SubElement(new_head, 'title')
                title_el.text = book_title
                html_root.insert(0, new_head)
                doc.write(str(xhtml_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
            except Exception as e:
                logging.warning(f"Could not fix title in {xhtml_path.name}: {e}")

def build_nav_and_landmarks(opf_tree: etree._ElementTree, opf_path: Path):
    spine_idrefs = opf_tree.xpath("//opf:spine/opf:itemref/@idref", namespaces=NS)
    manifest_map = {item.get('id'): item.get('href') for item in opf_tree.xpath("//opf:manifest/opf:item", namespaces=NS)}
    
    toc_entries = []
    start_of_content_href = None

    parser = etree.HTMLParser(recover=True)
    for idref in spine_idrefs:
        href = manifest_map.get(idref)
        if not href or not href.endswith(('.xhtml', '.html')): continue
        if not start_of_content_href: start_of_content_href = href
        file_path = (opf_path.parent / href).resolve()
        if file_path.is_file():
            try:
                doc = etree.parse(str(file_path), parser)
                headings = doc.xpath('//xhtml:h1 | //xhtml:h2', namespaces=NS)
                for heading in headings:
                    text = ' '.join(heading.itertext()).strip()
                    if text:
                        heading_id = heading.get('id')
                        if not heading_id:
                            heading_id = f"heading-{uuid.uuid4()}"
                            heading.set('id', heading_id)
                            doc.write(str(file_path), encoding='utf-8', method='xml', xml_declaration=True, doctype="<!DOCTYPE html>")
                        toc_entries.append({'href': f"{href}#{heading_id}", 'text': text})
            except Exception as e:
                logging.warning(f"Could not parse {href} for ToC: {e}")

    # --- FINAL FIX: If no headings were found, create a default entry to prevent an empty ToC ---
    if not toc_entries and start_of_content_href:
        toc_entries.append({'href': start_of_content_href, 'text': 'Start of Content'})

    nav_xhtml = etree.Element('html', nsmap={None: XHTML_NS, 'epub': NS['epub']})
    head = etree.SubElement(nav_xhtml, 'head')
    etree.SubElement(head, 'title').text = "Navigation"
    body = etree.SubElement(nav_xhtml, 'body')
    
    toc_nav = etree.SubElement(body, 'nav', attrib={f"{{{NS['epub']}}}type": "toc"})
    etree.SubElement(toc_nav, 'h1').text = "Table of Contents"
    toc_ol = etree.SubElement(toc_nav, 'ol')
    for entry in toc_entries:
        li = etree.SubElement(toc_ol, 'li')
        a = etree.SubElement(li, 'a', href=entry['href'])
        a.text = entry['text']
            
    landmarks_nav = etree.SubElement(body, 'nav', attrib={f"{{{NS['epub']}}}type": "landmarks"})
    etree.SubElement(landmarks_nav, 'h1').text = "Landmarks"
    landmarks_ol = etree.SubElement(landmarks_nav, 'ol')
    li = etree.SubElement(landmarks_ol, 'li')
    a = etree.SubElement(li, 'a', href='nav.xhtml', attrib={f"{{{NS['epub']}}}type": 'toc'})
    a.text = 'Table of Contents'
    if start_of_content_href:
        li = etree.SubElement(landmarks_ol, 'li')
        a = etree.SubElement(li, 'a', href=start_of_content_href, attrib={f"{{{NS['epub']}}}type": 'bodymatter'})
        a.text = 'Start of Content'
    
    return nav_xhtml

def run_upgrade(epub_path: Path, output_path: Path):
    with tempfile.TemporaryDirectory() as temp_dir:
        unzip_dir = Path(temp_dir)
        unzip_epub(epub_path, unzip_dir)

        opf_path, opf_dir, opf_tree = find_opf_and_basedir(unzip_dir)
        if opf_tree is None: return False

        package = opf_tree.getroot()
        package.set('version', '3.0')
        metadata = opf_tree.find('.//opf:metadata', namespaces=NS)
        if metadata is None: metadata = etree.SubElement(package, 'metadata')
        
        ensure_unique_identifier(opf_tree, metadata)
        ensure_single_modified_date(metadata)
        
        manifest = opf_tree.find('.//opf:manifest', namespaces=NS)

        # Aggressive Cleanup
        items_to_remove = manifest.xpath('.//opf:item[contains(@href, ".ncx") or contains(@href, "nav.xhtml") or contains(@href, "navigation.xhtml")]', namespaces=NS)
        for item in items_to_remove:
            file_to_delete = opf_dir / item.get('href')
            if file_to_delete.exists(): file_to_delete.unlink()
            item.getparent().remove(item)
        for item in manifest.xpath('.//opf:item[@properties]', namespaces=NS):
            if 'nav' in (item.get('properties') or ''):
                new_props = item.get('properties').replace('nav', '').strip()
                if new_props: item.set('properties', new_props)
                else: item.attrib.pop('properties')

        # Fix content files BEFORE building nav
        fix_xhtml_structure_and_title(opf_tree, opf_path)

        # Build and Integrate New Nav
        nav_xhtml_content = build_nav_and_landmarks(opf_tree, opf_path)
        nav_path = opf_path.parent / "nav.xhtml"
        nav_path.write_bytes(etree.tostring(nav_xhtml_content, pretty_print=True, xml_declaration=True, encoding='utf-8', doctype="<!DOCTYPE html>"))

        etree.SubElement(manifest, 'item', id="nav", href="nav.xhtml", attrib={"media-type": "application/xhtml+xml"}, properties="nav")

        # Finalize
        opf_tree.write(str(opf_path), encoding='utf-8', xml_declaration=True, pretty_print=True)
        repack_epub(unzip_dir, output_path)
    return True