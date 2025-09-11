#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epubstandard v1.0 — enhanced complete code
- Remove ALL existing CSS and inject a minimal standard stylesheet
- Fix wrapped lines and conservative dehyphenation across <br>
- Remove repeated journal banners/running headers (only if ≥2 spine items AND ≥2 occurrences)
- Create/repair bi-directional links between in-text citations and notes
  • Normalize Unicode superscripts (U+00B9, U+00B2, U+00B3, U+2070–U+2079) to ASCII digits for matching
  • Recognize <a epub:type="noteref">, class*="noteref", or <sup>…</sup> as refs
  • Handle symbol notes: *, †, ‡ (author/editor note) with backlinks
  • Only FIRST occurrence of a given note marker gets an id anchor (avoid duplicate ids)
- Remove soft hyphens (U+00AD) globally from XHTML
- Collapse runs of empty paragraphs; trim whitespace
- Ensure <meta charset="utf-8"> present in <head>
- Optional blacklist regex pass for short boilerplate lines; actions logged
- Idempotent marker in OPF + META-INF/cleanup.json with config hash
- Expanded rollup metrics per EPUB

Requires: lxml (pip install lxml)
"""

import argparse
import csv
import dataclasses
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from collections import Counter
from typing import Dict, List, Optional, Tuple

from lxml import etree
from lxml import html

NSMAP = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'xhtml': 'http://www.w3.org/1999/xhtml',
}

STD_CSS_FILENAME = "styles/epubstandard.css"
STD_CSS_CONTENT = """/* epubstandard v1.0 — minimal, readable defaults */
html, body { margin: 0; padding: 0; }
body { line-height: 1.4; word-wrap: break-word; }
p { margin: 0; text-indent: 1.2em; }
p.noindent, h1 + p, h2 + p, h3 + p, h4 + p, h5 + p, h6 + p { text-indent: 0; }
h1, h2, h3, h4, h5, h6 { margin: 1.2em 0 0.6em 0; font-weight: bold; }
blockquote { margin: 1em 2em; }
ul, ol { margin: 0.6em 0 0.6em 2em; padding: 0; }
sup { line-height: 1; font-size: 0.8em; vertical-align: super; }
hr { border: none; border-top: 1px solid #aaa; margin: 1.2em 0; }
a { text-decoration: none; }
a:hover { text-decoration: underline; }
/* Hyphenation is left to reading systems. */
"""

NOTE_ID_PATTERNS = [
    r'^(fn|footnote|note)[-_]?\d+$',
    r'^note\d+$',
    r'^\d+$',
]

BANNER_CONF = {
    "enabled": True,
    "keep_first": True,
    "min_repeat_ratio": 0.6,
    "top_chars": 150,
    "bottom_chars": 150,
}

COMMON_COMPOUND_KEEP = set("""
co-founder co-operate co-operation re-entry re-issue re-iterate re-open re-creation
pre-existing pre-eminent pre-empt post-war cross-examine long-term short-term
""".split())

MARKER_PROP = "cleanup:processed-by"
TOOL_TAG = "epubstandard v1.0"

SOFT_HYPHEN = '\u00AD'
SUPERSCRIPT_MAP = {
    '\u00B9': '1', '\u00B2': '2', '\u00B3': '3',
    '\u2070': '0', '\u2074': '4', '\u2075': '5', '\u2076': '6',
    '\u2077': '7', '\u2078': '8', '\u2079': '9',
}

SYMBOLS = {
    '*': ('star', r'^\*\s*'),
    '†': ('dagger', r'^†\s*'),
    '‡': ('dagger2', r'^‡\s*'),
}

@dataclasses.dataclass
class Config:
    inplace: bool = False
    force: bool = False
    dry_run: bool = False
    banner: Dict = dataclasses.field(default_factory=lambda: dict(BANNER_CONF))
    audit: bool = False
    blacklist_file: Optional[str] = None

    def hash(self) -> str:
        payload = json.dumps({
            "inplace": self.inplace,
            "banner": self.banner,
            "audit": self.audit,
            "blacklist_file": self.blacklist_file or "",
        }, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


def load_blacklist(path: Optional[str]) -> List[re.Pattern]:
    if not path or not os.path.exists(path):
        return []
    pats = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                pats.append(re.compile(s, re.I))
            except re.error:
                pass
    return pats


def unzip_epub(epub_path: str, workdir: str) -> str:
    with zipfile.ZipFile(epub_path, 'r') as zf:
        zf.extractall(workdir)
    return workdir


def zip_epub(src_dir: str, out_path: str):
    mimetype_path = os.path.join(src_dir, "mimetype")
    with zipfile.ZipFile(out_path, 'w') as zf:
        if os.path.exists(mimetype_path):
            with open(mimetype_path, 'rb') as f:
                data = f.read()
            zf.writestr('mimetype', data, compress_type=zipfile.ZIP_STORED)
        for root, _, files in os.walk(src_dir):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, src_dir)
                if rel == 'mimetype':
                    continue
                zf.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)


def find_opf_path(root_dir: str) -> Optional[str]:
    cpath = os.path.join(root_dir, "META-INF", "container.xml")
    if not os.path.exists(cpath):
        return None
    try:
        tree = etree.parse(cpath)
        rootfiles = tree.xpath(
            '//container:rootfile',
            namespaces={'container': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        )
        if rootfiles:
            opf_rel = rootfiles[0].get('full-path')
            return os.path.join(root_dir, opf_rel)
    except Exception:
        return None
    return None


def load_xml(path: str) -> etree._ElementTree:
    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    return etree.parse(path, parser)


def save_xml(tree: etree._ElementTree, path: str):
    tree.write(path, encoding="utf-8", xml_declaration=True, pretty_print=True)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def looks_like_banner(snippet: str) -> bool:
    s = re.sub(r'\s+', ' ', snippet).strip()
    if not s or len(s) > 120:
        return False
    if re.search(r'\bISSN\b', s, re.I): return True
    if re.search(r'\bVol(?:\.|ume)?\s*\d+', s, re.I): return True
    if re.search(r'\bNo\.\s*\d+', s, re.I): return True
    if re.search(r'\b(Spring|Summer|Fall|Winter)\b\s+\d{4}', s, re.I): return True
    if re.search(r'\bdoi:\s*10\.\d{4,9}/', s, re.I): return True
    if re.search(r'journal|law review|harvard|yale|columbia|nyu|stanford', s, re.I): return True
    return False


def collect_spine(opf_tree: etree._ElementTree, opf_dir: str) -> List[str]:
    manifest = {item.get('id'): item.get('href') for item in opf_tree.xpath('//opf:manifest/opf:item', namespaces=NSMAP)}
    spine_ids = [item.get('idref') for item in opf_tree.xpath('//opf:spine/opf:itemref', namespaces=NSMAP)]
    paths = []
    for idref in spine_ids:
        href = manifest.get(idref)
        if href:
            paths.append(os.path.normpath(os.path.join(opf_dir, href)))
    return paths


def ensure_meta_charset(root: html.HtmlElement) -> bool:
    head = root.find('head')
    created_head = False
    if head is None:
        head = etree.Element('head')
        root.insert(0, head)
        created_head = True
    metas = head.xpath('.//meta[@charset]')
    for m in metas:
        if (m.get('charset') or '').lower() == 'utf-8':
            return created_head
    metas2 = head.xpath('.//meta[translate(@http-equiv, "ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="content-type"]')
    for m in metas2:
        ct = (m.get('content') or '').lower()
        if 'charset=utf-8' in ct:
            return created_head
    meta = etree.Element('meta')
    meta.set('charset', 'utf-8')
    head.insert(0, meta)
    return True


def strip_all_css_and_links(opf_tree: etree._ElementTree, opf_path: str) -> List[str]:
    removed = []
    opf_dir = os.path.dirname(opf_path)

    manifest_items = opf_tree.xpath('//opf:manifest/opf:item', namespaces=NSMAP)
    css_items = [it for it in manifest_items if (it.get('media-type') in ('text/css',) or (it.get('href','').lower().endswith('.css')))]
    css_hrefs = [os.path.normpath(os.path.join(opf_dir, it.get('href'))) for it in css_items]

    for it in css_items:
        it.getparent().remove(it)

    for css_path in css_hrefs:
        if os.path.exists(css_path):
            try:
                os.remove(css_path)
                removed.append(os.path.relpath(css_path, opf_dir))
            except Exception:
                pass

    spine = collect_spine(opf_tree, opf_dir)
    for xhtml_path in spine:
        if not os.path.exists(xhtml_path):
            continue
        try:
            doc = html.parse(xhtml_path)
            root = doc.getroot()
            changed = False
            for link in root.xpath('//link[translate(@rel,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="stylesheet"]'):
                link.getparent().remove(link)
                changed = True
            if ensure_meta_charset(root):
                changed = True
            if changed:
                doc.write(xhtml_path, encoding='utf-8', method='html', pretty_print=True)
        except Exception:
            continue

    return removed


def inject_standard_css(opf_tree: etree._ElementTree, opf_path: str):
    opf_dir = os.path.dirname(opf_path)
    css_abs = os.path.join(opf_dir, STD_CSS_FILENAME)
    ensure_dir(os.path.dirname(css_abs))
    with open(css_abs, 'w', encoding='utf-8') as f:
        f.write(STD_CSS_CONTENT)

    manifest = opf_tree.xpath('//opf:manifest', namespaces=NSMAP)
    if not manifest:
        raise RuntimeError("Invalid OPF: missing manifest")
    manifest = manifest[0]

    item_id = 'item-epubstandard-css'
    i = 1
    while opf_tree.xpath(f'//opf:manifest/opf:item[@id="{item_id}"]', namespaces=NSMAP):
        i += 1
        item_id = f'item-epubstandard-css-{i}'
    item = etree.SubElement(manifest, f'{{{NSMAP["opf"]}}}item')
    item.set('id', item_id)
    item.set('href', STD_CSS_FILENAME.replace('\\','/'))
    item.set('media-type', 'text/css')

    for xhtml_path in collect_spine(opf_tree, opf_dir):
        try:
            doc = html.parse(xhtml_path)
            root = doc.getroot()
            head = root.find('head')
            if head is None:
                head = etree.Element('head')
                root.insert(0, head)
            already = root.xpath(f'//link[@href="{STD_CSS_FILENAME}"]')
            if not already:
                link = etree.Element('link', rel='stylesheet', href=STD_CSS_FILENAME)
                head.append(link)
                doc.write(xhtml_path, encoding='utf-8', method='html', pretty_print=True)
        except Exception:
            continue


def mark_idempotent(opf_tree: etree._ElementTree, config_hash: str):
    metadata = opf_tree.xpath('//opf:metadata', namespaces=NSMAP)
    if not metadata:
        pkg = opf_tree.getroot()
        md = etree.SubElement(pkg, f'{{{NSMAP["opf"]}}}metadata')
    else:
        md = metadata[0]
    for meta in md.xpath(f'./opf:meta[@property="{MARKER_PROP}"]', namespaces=NSMAP):
        md.remove(meta)
    tag = etree.SubElement(md, f'{{{NSMAP["opf"]}}}meta')
    tag.set('property', MARKER_PROP)
    tag.set('content', f'{TOOL_TAG} ({config_hash})')


def already_processed(opf_tree: etree._ElementTree, config_hash: str) -> bool:
    metas = opf_tree.xpath(f'//opf:meta[@property="{MARKER_PROP}"]', namespaces=NSMAP)
    for m in metas:
        content = m.get('content','')
        if config_hash in content and TOOL_TAG in content:
            return True
    return False


def normalize_superscripts_to_ascii(s: str) -> str:
    return ''.join(SUPERSCRIPT_MAP.get(ch, ch) for ch in s)


def collapse_empty_paragraphs(root: html.HtmlElement) -> bool:
    changed = False
    for el in root.xpath('//text()'):
        if isinstance(el, str) and not el.strip():
            parent = el.getparent()
            if parent is not None:
                if parent.text is el:
                    parent.text = None
                else:
                    for child in parent:
                        if child.tail is el:
                            child.tail = None
                            break
                changed = True
    for p in root.xpath('//p'):
        txt = ''.join(p.itertext()).strip().replace('\xa0','')
        if txt == '':
            nxt = p.getnext()
            if nxt is not None and nxt.tag.lower() == 'p':
                nxt_txt = ''.join(nxt.itertext()).strip().replace('\xa0','')
                if nxt_txt == '':
                    parent = p.getparent()
                    if parent is not None:
                        parent.remove(p)
                        changed = True
    return changed


def strip_soft_hyphens(root: html.HtmlElement) -> bool:
    changed = False
    for node in root.xpath('//text()'):
        if isinstance(node, str) and SOFT_HYPHEN in node:
            newt = node.replace(SOFT_HYPHEN, '')
            parent = node.getparent()
            if parent is not None:
                if parent.text is node:
                    parent.text = newt
                else:
                    for child in parent:
                        if child.tail is node:
                            child.tail = newt
                            break
            changed = True
    return changed


def normalize_br_text_runs(p: html.HtmlElement) -> List[str]:
    lines = []
    buf = []
    def flush():
        s = ''.join(buf)
        lines.append(re.sub(r'\s+', ' ', s).strip())
        buf.clear()
    for node in p.iter():
        if node is p:
            continue
        if isinstance(node.tag, str) and node.tag.lower() == 'br':
            flush()
            continue
        if node.text and node is not p:
            buf.append(node.text)
        if node.tail:
            buf.append(node.tail)
    if buf:
        flush()
    return [x for x in lines if x]


def should_join_wrapped(line1: str, line2: str) -> bool:
    if not line1 or not line2:
        return False
    if line1.endswith('-'):
        return True
    # FIXED regex (no stray backslash)
    if re.search(r'[.!?]["\']?$', line1):
        return False
    if re.match(r'^[,;:)\]]', line2):
        return True
    if re.match(r'^[a-z0-9]', line2):
        return True
    return False


def dehyphenate_pair(word1: str, word2: str) -> Optional[str]:
    w1 = word1.rstrip('-')
    candidate = (w1 + word2)
    original_compound = w1 + '-' + word2
    if original_compound.lower() in COMMON_COMPOUND_KEEP:
        return None
    if word2 and word2[0].isupper():
        return None
    if re.match(r'^[A-Za-z]{2,}$', w1) and re.match(r'^[a-z]{2,}', word2):
        return candidate
    return None


def fix_linebreaks_and_dehyphenation(root: html.HtmlElement) -> bool:
    changed_any = False
    skip_tags = set('ul ol li table thead tbody tfoot tr td th code pre h1 h2 h3 h4 h5 h6 blockquote'.split())
    for p in root.xpath('//p|//div'):
        if p.tag.lower() in skip_tags:
            continue
        if p.xpath('./*[(self::ul or self::ol or self::table or self::pre or self::code)]'):
            continue
        lines = normalize_br_text_runs(p)
        if len(lines) < 2:
            continue
        new_text = []
        i = 0
        changed_local = False
        while i < len(lines):
            cur = lines[i]
            if i+1 < len(lines) and should_join_wrapped(cur, lines[i+1]):
                nxt = lines[i+1]
                if cur.endswith('-'):
                    m1 = re.search(r'(.*\b)([A-Za-z][A-Za-z\-]*)-$', cur)
                    m2 = re.search(r'^([a-zA-Z]+)(.*)$', nxt)
                    if m1 and m2:
                        maybe = dehyphenate_pair(m1.group(2), m2.group(1))
                        if maybe:
                            cur = m1.group(1) + maybe + m2.group(2)
                            i += 2
                            new_text.append(cur)
                            changed_local = True
                            continue
                cur = re.sub(r'\s+$', '', cur) + ' ' + re.sub(r'^\s+', '', lines[i+1])
                i += 2
                new_text.append(cur)
                changed_local = True
            else:
                new_text.append(cur)
                i += 1
        if changed_local:
            for child in list(p):
                p.remove(child)
            p.text = ' '.join(new_text).strip()
            changed_any = True
    return changed_any


def find_note_nodes(root: html.HtmlElement) -> Dict[str, html.HtmlElement]:
    notes = {}
    for el in root.xpath('//*[@id]'):
        nid = el.get('id').strip()
        for pat in NOTE_ID_PATTERNS:
            if re.match(pat, nid):
                notes[nid] = el
                break
        role = el.get('role','')
        if role == 'doc-footnote':
            notes[nid] = el
    return notes


def find_noterefs(root: html.HtmlElement) -> List[html.HtmlElement]:
    refs = []
    refs.extend(root.xpath('//sup'))
    # handle epub:type=noteref, whether namespace-bound or literal
    refs.extend(root.xpath('//*[@epub:type="noteref"] | //*[@*[name()="epub:type"]="noteref"]'))
    refs.extend(root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " noteref ")]'))
    seen = set()
    uniq = []
    for el in refs:
        key = id(el)
        if key not in seen:
            uniq.append(el); seen.add(key)
    return uniq


def find_symbol_note_targets(root: html.HtmlElement):
    targets = {}
    for sym, (name, rx) in SYMBOLS.items():
        pat = re.compile(rx)
        for el in root.xpath('//p|//div|//li'):
            txt = ' '.join(el.itertext()).strip()
            if pat.match(txt) or re.match(r'^(Author|Editor).{0,30}(note|n\.)', txt, re.I):
                targets[name] = el
                break
    return targets


def ensure_bidirectional_links(root: html.HtmlElement) -> Tuple[int,int,int]:
    changed_forward = 0
    added_backlinks = 0
    first_ids_added = 0

    # numeric notes
    notes = find_note_nodes(root)
    numeric_to_id = {}
    for nid in notes.keys():
        m = re.search(r'(\d+)$', nid)
        if m:
            numeric_to_id[m.group(1)] = nid

    seen_first_ref_for_num = set()

    # symbol targets (look once per doc)
    symbol_targets = find_symbol_note_targets(root)

    for el in find_noterefs(root):
        num_txt_raw = ''.join(el.itertext())
        num_txt_raw = normalize_superscripts_to_ascii(num_txt_raw)
        num_txt = re.sub(r'\D+', '', num_txt_raw).strip()

        symbol_key = None
        for sym, (name, _) in SYMBOLS.items():
            if sym in num_txt_raw:
                symbol_key = name
                break

        target_id = None
        if num_txt:
            target_id = numeric_to_id.get(num_txt)

        if target_id:
            a = el if el.tag == 'a' else el.find('.//a')
            if a is not None:
                href = a.get('href','')
                if href != f'#{target_id}':
                    a.set('href', f'#{target_id}')
                    a.set('epub:type', 'noteref')
                    changed_forward += 1
            else:
                a = etree.Element('a', href=f'#{target_id}')
                a.set('epub:type', 'noteref')
                if el.text:
                    a.text = el.text
                    el.text = None
                for child in list(el):
                    el.remove(child); a.append(child)
                el.append(a)
                changed_forward += 1

            if num_txt not in seen_first_ref_for_num and 'id' not in el.attrib:
                el.set('id', f'ref-{num_txt}')
                seen_first_ref_for_num.add(num_txt)
                first_ids_added += 1
            continue

        if symbol_key and symbol_key in symbol_targets:
            tgt_el = symbol_targets[symbol_key]
            tgt_id = tgt_el.get('id') or f'note-{symbol_key}'
            if 'id' not in tgt_el.attrib:
                tgt_el.set('id', tgt_id)

            a = el if el.tag == 'a' else el.find('.//a')
            if a is not None:
                if a.get('href','') != f'#{tgt_id}':
                    a.set('href', f'#{tgt_id}')
                    a.set('epub:type', 'noteref')
                    changed_forward += 1
            else:
                a = etree.Element('a', href=f'#{tgt_id}')
                a.set('epub:type', 'noteref')
                if el.text:
                    a.text = el.text
                    el.text = None
                for child in list(el):
                    el.remove(child); a.append(child)
                el.append(a)
                changed_forward += 1

            ref_id = f'ref-{symbol_key}'
            if not root.xpath(f'//*[@id="{ref_id}"]') and 'id' not in el.attrib:
                el.set('id', ref_id)
                first_ids_added += 1

            if not tgt_el.xpath('.//a[contains(@class,"backlink")]'):
                bl = etree.Element('a', href=f'#{ref_id}')
                bl.set('class', 'backlink'); bl.text = '↩'
                tgt_el.append(bl)
                added_backlinks += 1

    # numeric backlinks
    for nid, node in notes.items():
        if node.xpath('.//a[contains(@class,"backlink")]'):
            continue
        m = re.search(r'(\d+)$', nid)
        if not m:
            continue
        num = m.group(1)
        ref_id = f'ref-{num}'
        if root.xpath(f'//*[@id="{ref_id}"]'):
            a = etree.Element('a', href=f'#{ref_id}')
            a.set('class','backlink'); a.text = '↩'
            node.append(a)
            added_backlinks += 1

    return (changed_forward, added_backlinks, first_ids_added)


def extract_banner_snippets(root: html.HtmlElement, top_chars: int, bottom_chars: int) -> Tuple[str, str]:
    body_txt = ''.join(root.xpath('//body//text()'))
    body_txt = re.sub(r'\s+', ' ', body_txt).strip()
    return (body_txt[:top_chars], body_txt[-bottom_chars:] if len(body_txt) > bottom_chars else '')


def remove_repeated_banners(spine_paths: List[str], conf: Dict, dry_run=False) -> Tuple[int,int]:
    if not conf.get("enabled", True):
        return (0,0)
    if len(spine_paths) < 2:
        return (0,0)

    top_chars = int(conf.get("top_chars", 150))
    bottom_chars = int(conf.get("bottom_chars", 150))
    min_ratio = float(conf.get("min_repeat_ratio", 0.6))
    keep_first = bool(conf.get("keep_first", True))

    tops, bottoms = [], []
    for p in spine_paths:
        try:
            doc = html.parse(p)
            root = doc.getroot()
            t, b = extract_banner_snippets(root, top_chars, bottom_chars)
            tops.append(t)
            bottoms.append(b)
        except Exception:
            tops.append('')
            bottoms.append('')

    n = len(spine_paths)
    top_counts = Counter([t for t in tops if looks_like_banner(t)])
    bot_counts = Counter([b for b in bottoms if looks_like_banner(b)])

    top_remove = set([t for t,c in top_counts.items() if c >= 2 and (c / n) >= min_ratio])
    bot_remove = set([b for b,c in bot_counts.items() if c >= 2 and (c / n) >= min_ratio])

    removed = 0
    kept = 0
    seen_top = set()
    seen_bot = set()

    for idx, p in enumerate(spine_paths):
        try:
            doc = html.parse(p)
            root = doc.getroot()
            changed = False

            t, b = (tops[idx], bottoms[idx])

            def snippet_matches_heading(snip: str) -> bool:
                if not snip: return False
                h1 = root.xpath('//h1')
                if h1:
                    htxt = ''.join(h1[0].itertext()).strip()
                    return htxt and snip.strip().startswith(htxt[:min(len(htxt), len(snip))])
                return False

            if t in top_remove and t and not snippet_matches_heading(t):
                if keep_first and t not in seen_top:
                    seen_top.add(t); kept += 1
                else:
                    body = root.find('body')
                    if body is not None:
                        for el in body.iter():
                            txt = (el.text or '').strip()
                            if txt and t.startswith(txt[:min(len(txt), len(t))]):
                                if not dry_run:
                                    el.text = ''
                                changed = True
                                removed += 1
                                break

            if b in bot_remove and b and not snippet_matches_heading(b):
                if keep_first and b not in seen_bot:
                    seen_bot.add(b); kept += 1
                else:
                    body = root.find('body')
                    if body is not None:
                        last_with_text = None
                        for el in body.iter():
                            if (el.text and el.text.strip()):
                                last_with_text = el
                        if last_with_text is not None:
                            if not dry_run:
                                last_with_text.text = ''
                            changed = True
                            removed += 1

            if changed and not dry_run:
                doc.write(p, encoding='utf-8', method='html', pretty_print=True)
        except Exception:
            continue

    return (removed, kept)


def apply_blacklist_shortlines(root: html.HtmlElement, patterns: List[re.Pattern]) -> int:
    if not patterns:
        return 0
    removed = 0
    body = root.find('body')
    if body is None:
        return 0
    for el in body.iter():
        if el is body:
            continue
        txt = (el.text or '').strip()
        if not txt or len(txt) > 160:
            continue
        for pat in patterns:
            if pat.search(txt):
                el.text = ''
                removed += 1
                break
    return removed


def process_single_xhtml(path: str, cfg, metrics: dict, skip_content_edits: bool = False, blacklist_patterns: List[re.Pattern]=None):
    try:
        doc = html.parse(path)
        root = doc.getroot()
    except Exception:
        return

    changed = False

    if ensure_meta_charset(root):
        changed = True

    if not skip_content_edits:
        if strip_soft_hyphens(root):
            metrics['soft_hyphens_removed'] += 1
            changed = True

        if fix_linebreaks_and_dehyphenation(root):
            metrics['linebreak_files_changed'] += 1
            changed = True

        if collapse_empty_paragraphs(root):
            metrics['empties_collapsed'] += 1
            changed = True

        fw, bl, first_ids = ensure_bidirectional_links(root)
        if fw: metrics['forward_links_fixed'] += fw
        if bl: metrics['backlinks_added'] += bl
        if first_ids: metrics['first_ref_ids_added'] += first_ids
        if fw or bl or first_ids:
            changed = True

        if blacklist_patterns:
            removed = apply_blacklist_shortlines(root, blacklist_patterns)
            if removed:
                metrics['blacklist_removed'] += removed
                changed = True

    if changed and not cfg.dry_run:
        doc.write(path, encoding='utf-8', method='html', pretty_print=True)


def write_cleanup_manifest(root_dir: str, config_hash: str, details: dict):
    os.makedirs(os.path.join(root_dir, "META-INF"), exist_ok=True)
    path = os.path.join(root_dir, "META-INF", "cleanup.json")
    payload = {
        "tool": TOOL_TAG,
        "config_hash": config_hash,
        "timestamp": int(time.time()),
        **details
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def process_single_epub(epub_path: str, out_dir: Optional[str], cfg: Config, rollup: list, blacklist_patterns: List[re.Pattern]):
    tmp = tempfile.mkdtemp(prefix="epubstd_")
    base = os.path.basename(epub_path)
    try:
        unzip_epub(epub_path, tmp)
        opf = find_opf_path(tmp)
        if not opf or not os.path.exists(opf):
            rollup.append((epub_path, "fail", "no opf", 0,0,0,0,0,0,0,0))
            return

        opf_tree = load_xml(opf)
        cfg_hash = cfg.hash()

        if (not cfg.force) and already_processed(opf_tree, cfg_hash):
            rollup.append((epub_path, "skip", "already processed (same config)", 0,0,0,0,0,0,0,0))
            return

        removed_css = strip_all_css_and_links(opf_tree, opf)
        inject_standard_css(opf_tree, opf)

        spine_paths = collect_spine(opf_tree, os.path.dirname(opf))

        metrics = {
            'linebreak_files_changed': 0,
            'forward_links_fixed': 0,
            'backlinks_added': 0,
            'first_ref_ids_added': 0,
            'soft_hyphens_removed': 0,
            'empties_collapsed': 0,
            'blacklist_removed': 0,
            'banners_removed': 0,
            'banners_kept_first': 0,
        }

        for pth in spine_paths:
            skip_edits = os.path.basename(pth).lower().startswith("nav")
            process_single_xhtml(pth, cfg, metrics, skip_content_edits=skip_edits, blacklist_patterns=blacklist_patterns)

        removed_banners, kept_banners = remove_repeated_banners(spine_paths, cfg.banner, dry_run=cfg.dry_run)
        metrics['banners_removed'] = removed_banners
        metrics['banners_kept_first'] = kept_banners

        if not cfg.dry_run:
            mark_idempotent(opf_tree, cfg_hash)
            save_xml(opf_tree, opf)
            write_cleanup_manifest(tmp, cfg_hash, {"removed_css": removed_css, **metrics})

        if cfg.inplace:
            out_path = epub_path
            if not cfg.dry_run:
                tmp_out = out_path + ".tmp"
                zip_epub(tmp, tmp_out)
                shutil.move(tmp_out, out_path)
            status = "ok-inplace"
        else:
            os.makedirs(out_dir or "", exist_ok=True)
            out_path = os.path.join(out_dir, base)
            if not cfg.dry_run:
                zip_epub(tmp, out_path)
            status = "ok"

        rollup.append((epub_path, status, "done",
                       metrics['linebreak_files_changed'],
                       metrics['forward_links_fixed'],
                       metrics['backlinks_added'],
                       metrics['first_ref_ids_added'],
                       metrics['soft_hyphens_removed'],
                       metrics['empties_collapsed'],
                       metrics['blacklist_removed'],
                       metrics['banners_removed']))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_batch(src_dir: str, dest_dir: Optional[str], cfg: Config, blacklist_patterns: List[re.Pattern]) -> str:
    epubs = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith('.epub'):
                epubs.append(os.path.join(root, f))
    epubs.sort()

    rollup = []
    if not cfg.inplace:
        os.makedirs(dest_dir or "out", exist_ok=True)

    for ep in epubs:
        process_single_epub(ep, dest_dir, cfg, rollup, blacklist_patterns)

    report_dir = dest_dir if (dest_dir and not cfg.inplace) else src_dir
    report_path = os.path.join(report_dir, f"epubstandard_report_{int(time.time())}.csv")
    with open(report_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(["file", "status", "note",
                    "linebreak_files_changed", "forward_links_fixed", "backlinks_added",
                    "first_ref_ids_added", "soft_hyphens_removed", "empties_collapsed",
                    "blacklist_removed", "banners_removed"])
        w.writerows(rollup)
    return report_path


def main():
    p = argparse.ArgumentParser(description="epubstandard v1.0 — normalize EPUBs after ABBYY")
    p.add_argument("--src", required=True, help="Source directory to scan for .epub")
    p.add_argument("--dest", default="out", help="Destination directory (ignored with --inplace)")
    p.add_argument("--inplace", action="store_true", help="Modify EPUBs in place")
    p.add_argument("--force", action="store_true", help="Process even if same-config marker present")
    p.add_argument("--dry-run", action="store_true", help="Simulate changes without writing")
    p.add_argument("--no-banners", action="store_true", help="Disable banner removal")
    p.add_argument("--banner-min-repeat", type=float, default=BANNER_CONF["min_repeat_ratio"], help="Min repeat ratio [0-1]")
    p.add_argument("--banner-top-chars", type=int, default=BANNER_CONF["top_chars"])
    p.add_argument("--banner-bottom-chars", type=int, default=BANNER_CONF["bottom_chars"])
    p.add_argument("--blacklist", default=None, help="Optional regex file; one pattern per line")
    p.add_argument("--audit", action="store_true", help="Reserved: emit extra diffs (off by default)")
    args = p.parse_args()

    cfg = Config(
        inplace=args.inplace,
        force=args.force,
        dry_run=args.dry_run,
        audit=args.audit,
        blacklist_file=args.blacklist,
        banner={
            "enabled": not args.no_banners,
            "min_repeat_ratio": args.banner_min_repeat,
            "top_chars": args.banner_top_chars,
            "bottom_chars": args.banner_bottom_chars,
            "keep_first": True
        }
    )

    blacklist_patterns = load_blacklist(cfg.blacklist_file)

    report = run_batch(args.src, None if args.inplace else args.dest, cfg, blacklist_patterns)
    print(f"[epubstandard] Done. Report: {report}")
    if args.dry_run:
        print("[epubstandard] Dry run: no files were written.")


if __name__ == "__main__":
    main()
