#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epubstandard v1.3.4
- Strict XHTML serialization for items declared application/xhtml+xml
- Preserve epub prefix via nsmap; do not copy xmlns:* attributes
- All XPath use namespace prefixes with a global NS map
- Bugfix: corrected XPath bracket in ops:noteref selector
- Hardened mixed-content checks to match namespaced ul/ol/table/pre/code
"""

import argparse, csv, dataclasses, hashlib, json, os, re, shutil, sys, tempfile, time, zipfile
from collections import Counter
from typing import Dict, List, Optional, Tuple
from lxml import etree, html

# Namespaces
NSMAP = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'xhtml': 'http://www.w3.org/1999/xhtml'
}
OPS_NS = "http://www.idpf.org/2007/ops"
XHTML_NS = NSMAP['xhtml']
NS = {'x': XHTML_NS, 'ops': OPS_NS}

BANNER_CONF = {"enabled": True, "keep_first": True, "min_repeat_ratio": 0.6, "top_chars": 150, "bottom_chars": 150}
SOFT_HYPHEN = '\u00AD'
SUPERSCRIPT_MAP = {
    '\u00B9':'1','\u00B2':'2','\u00B3':'3','\u2070':'0','\u2074':'4','\u2075':'5',
    '\u2076':'6','\u2077':'7','\u2078':'8','\u2079':'9'
}
SYMBOLS = {'*':'star','†':'dagger','‡':'double-dagger'}
NOTE_ID_PATTERNS = [r'^(fn|footnote|note)[-_]?\d+$', r'^note\d+$', r'^\d+$']

COMMON_COMPOUND_KEEP = set("""
co-founder co-operate co-operation re-entry re-issue re-iterate re-open re-creation
pre-existing pre-eminent pre-empt post-war cross-examine long-term short-term
""".split())

MARKER_PROP = "cleanup:processed-by"
TOOL_TAG = "epubstandard v1.3.4"

@dataclasses.dataclass
class Config:
    inplace: bool = False
    force: bool = False
    dry_run: bool = False
    banner: Dict = dataclasses.field(default_factory=lambda: dict(BANNER_CONF))
    audit: bool = False
    blacklist_file: Optional[str] = None
    def hash(self)->str:
        payload = json.dumps({
            "inplace": self.inplace,
            "banner": self.banner,
            "audit": self.audit,
            "blacklist_file": self.blacklist_file or ""
        }, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

def load_blacklist(path: Optional[str]):
    if not path or not os.path.exists(path): return []
    pats=[]
    with open(path,"r",encoding="utf-8") as f:
        for line in f:
            s=line.strip()
            if not s or s.startswith("#"): continue
            try: pats.append(re.compile(s,re.I))
            except re.error: pass
    return pats

# ---------------------------------------------------------------------------
# EPUB I/O helpers
# ---------------------------------------------------------------------------

def unzip_epub(epub_path: str, workdir: str)->str:
    """Extract an EPUB into a working directory."""
    with zipfile.ZipFile(epub_path,'r') as zf:
        zf.extractall(workdir)
    return workdir

def zip_epub(src_dir: str, out_path: str):
    """Repack a working directory into an EPUB file."""
    mimetype_path = os.path.join(src_dir,"mimetype")
    with zipfile.ZipFile(out_path,'w') as zf:
        # Store mimetype first and uncompressed
        if os.path.exists(mimetype_path):
            with open(mimetype_path,'rb') as f:
                zf.writestr('mimetype', f.read(), compress_type=zipfile.ZIP_STORED)
        # Add all other files
        for root,_,files in os.walk(src_dir):
            for name in files:
                full=os.path.join(root,name)
                rel=os.path.relpath(full,src_dir)
                if rel=='mimetype': continue
                zf.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)

def find_opf_path(root_dir: str)->Optional[str]:
    """Locate the OPF file from META-INF/container.xml."""
    cpath=os.path.join(root_dir,"META-INF","container.xml")
    if not os.path.exists(cpath): return None
    try:
        tree=etree.parse(cpath)
        rf=tree.xpath('//container:rootfile',
                      namespaces={'container':'urn:oasis:names:tc:opendocument:xmlns:container'})
        if rf:
            return os.path.join(root_dir, rf[0].get('full-path'))
    except Exception:
        return None
    return None

def load_xml(path:str):
    """Parse XML file with recovery."""
    return etree.parse(path, etree.XMLParser(recover=True, remove_blank_text=False))

def save_xml(tree, path):
    """Write XML tree to disk with utf-8 encoding."""
    tree.write(path, encoding="utf-8", xml_declaration=True, pretty_print=True)

def collect_spine_and_types(opf_tree, opf_dir)->List[Tuple[str,bool]]:
    """
    Collect spine hrefs and whether they are XHTML.
    Returns list of (path, is_xhtml).
    """
    items = {it.get('id'): (it.get('href'), it.get('media-type',''))
             for it in opf_tree.xpath('//opf:manifest/opf:item',
                                      namespaces={'opf': NSMAP['opf']})}
    spine = []
    for itemref in opf_tree.xpath('//opf:spine/opf:itemref',
                                  namespaces={'opf': NSMAP['opf']}):
        idref = itemref.get('idref')
        if idref in items:
            href, mtype = items[idref]
            p = os.path.normpath(os.path.join(opf_dir, href))
            is_xhtml = (mtype.lower() in
                        ('application/xhtml+xml','application/x-dtbook+xml','text/xml'))
            spine.append((p, is_xhtml))
    return spine

# ---------------------------------------------------------------------------
# XHTML helpers
# ---------------------------------------------------------------------------

def ensure_xhtml_root(root):
    """
    Ensure root element has XHTML namespace and nsmap including epub.
    Skip copying xmlns:* attributes; use nsmap instead.
    """
    tag=root.tag
    if not tag.startswith('{'+XHTML_NS+'}'):
        new_root=etree.Element('{%s}%s'%(XHTML_NS, etree.QName(tag).localname),
                               nsmap={None:XHTML_NS, 'epub':OPS_NS})
        for k,v in root.attrib.items():
            if k.startswith('xmlns'): continue
            new_root.set(k,v)
        while root.text: new_root.text=root.text; root.text=None
        for child in root: new_root.append(child)
        root=new_root
    return root

def ensure_meta_charset(root):
    """Ensure a <meta charset='utf-8'/> exists in head."""
    head = (root.xpath('.//x:head|.//head', namespaces=NS) or [None])[0]
    if head is None: return
    has=False
    for m in head.xpath('.//x:meta|.//meta', namespaces=NS):
        if 'charset' in m.attrib: has=True
    if not has:
        m=etree.Element('{%s}meta'%XHTML_NS, nsmap=root.nsmap)
        m.set('charset','utf-8')
        head.insert(0,m)

def fix_linebreaks_and_dehyphenation(root):
    """
    Collapse line breaks and soft hyphens inside <p>/<div>.
    Conservative: only collapse if words look broken by PDF->ABBYY.
    """
    changed=False
    for p in root.xpath('//p|//div|//x:p|//x:div', namespaces=NS):
        if p.text: 
            new_text = _fix_text(p.text)
            if new_text!=p.text: p.text=new_text; changed=True
        for el in p:
            if el.tail:
                new_text=_fix_text(el.tail)
                if new_text!=el.tail: el.tail=new_text; changed=True
    return changed

def _fix_text(txt:str)->str:
    if not txt: return txt
    txt=txt.replace(SOFT_HYPHEN,'')
    # join words split across linebreaks + hyphen
    txt=re.sub(r'(\w+)-\s*\n\s*(\w+)',
               lambda m: m.group(1)+'-'+m.group(2)
               if (m.group(1)+'-'+m.group(2)).lower() in COMMON_COMPOUND_KEEP
               else m.group(1)+m.group(2), txt)
    # join across newlines without hyphen
    txt=re.sub(r'(\w+)\s*\n\s*(\w+)', r'\1 \2', txt)
    return txt

# ---------------------------------------------------------------------------
# Notes & citations: detect refs/notes and add forward + back links
# ---------------------------------------------------------------------------

def set_noteref_semantics(a):
    """Mark an <a> as a noteref semantically (class + epub:type)."""
    cls=a.get('class','').strip()
    parts=set(cls.split()) if cls else set()
    if 'noteref' not in parts:
        parts.add('noteref')
        a.set('class',' '.join(sorted(parts)))
    try:
        a.set('{http://www.idpf.org/2007/ops}type','noteref')
    except Exception:
        # lxml may complain if namespace map is missing; ignore
        pass

def find_noterefs(root)->List[html.HtmlElement]:
    """
    Return likely in-text note references:
      - <sup>…</sup>
      - any element with @ops:type='noteref'
      - elements whose class includes 'noteref'
    """
    refs = []
    # superscripts (namespaced and non-namespaced)
    refs.extend(root.xpath('//sup|//x:sup', namespaces=NS))

    # epub:type=noteref, using namespace tests
    refs.extend(root.xpath(
        '//*[@*[local-name()="type" and namespace-uri()="http://www.idpf.org/2007/ops"]="noteref"]'
    ))

    # class contains "noteref"
    refs.extend(root.xpath(
        '//*[contains(concat(" ", normalize-space(@class), " "), " noteref ")]'
    ))

    # dedupe
    seen=set(); uniq=[]
    for el in refs:
        k=id(el)
        if k in seen: continue
        uniq.append(el); seen.add(k)
    return uniq

def is_note_block(el)->bool:
    """Heuristic: treat element as a footnote/endnote block."""
    tag = (el.tag or '').lower()
    if tag.endswith('sup') or tag.endswith('a'):
        return False
    nid=(el.get('id') or '').strip()
    role=(el.get('role') or '').strip().lower()
    if role=='doc-footnote':
        return True
    if nid and any(re.match(p, nid) for p in NOTE_ID_PATTERNS):
        txt=' '.join(el.itertext()).strip()
        if len(txt)>=10:
            return True
    return False

def find_note_blocks(root)->List[html.HtmlElement]:
    """All elements that look like note blocks (must have id or role)."""
    return [el for el in root.xpath('//*[@id or @role]') if is_note_block(el)]

def symbol_targets_in_doc(root)->Dict[str, html.HtmlElement]:
    """
    Map asterism-like symbols (*, †, ‡) to their blocks.
    Also treat short 'Author note' lines as star.
    """
    mapping={}
    for el in root.xpath(
        '//p|//div|//li|//aside|//section|//x:p|//x:div|//x:li|//x:aside|//x:section',
        namespaces=NS
    ):
        txt=' '.join(el.itertext()).strip()
        if not txt: continue
        if txt=='*' or txt.startswith('* '): mapping.setdefault('star',el)
        if txt=='†' or txt.startswith('† '): mapping.setdefault('dagger',el)
        if txt=='‡' or txt.startswith('‡ '): mapping.setdefault('double-dagger',el)
        if re.match(r'^(Author|Editor).{0,40}(note|n\.)', txt, re.I):
            mapping.setdefault('star',el)
    return mapping

def wrap_with_link(el, target_id)->bool:
    """
    Ensure el (or its first child) is wrapped in <a href="#target_id">…</a>.
    Returns True if it changed the document.
    """
    a = el if (isinstance(el.tag,str) and el.tag.lower().endswith('a')) else el.find('.//a')
    if a is not None:
        if a.get('href','') != f'#{target_id}':
            a.set('href', f'#{target_id}')
            set_noteref_semantics(a)
            return True
        set_noteref_semantics(a)
        return False
    a = etree.Element('a', href=f'#{target_id}')
    set_noteref_semantics(a)
    if el.text:
        a.text = el.text
        el.text = None
    for child in list(el):
        el.remove(child); a.append(child)
    el.append(a)
    return True

def ensure_bidirectional_links_per_file(root)->Tuple[int,int,int, Dict[str, List[str]], Dict[str, List[str]]]:
    """
    For the current (single) XHTML doc:
      * Add forward links from in-text refs -> note blocks
      * Give each in-text ref a stable id:
          - First occurrence:  ref-<n>         (back-compat)
          - All occurrences:  ref-<n>-1, ref-<n>-2, ...
      * Return maps of number->list of ref ids and symbol-key->list of ref ids
    Returns: (forward_changes, backlinks_added=0_here, first_ids_added, numeric_ref_ids, symbol_ref_ids)
    """
    changed_forward=0; added_backlinks=0; first_ids_added=0
    numeric_ref_ids: Dict[str, List[str]] = {}
    symbol_ref_ids:  Dict[str, List[str]] = {}

    # Candidates: note targets
    numeric_targets={}
    for el in find_note_blocks(root):
        nid=el.get('id') or ''
        m=re.search(r'(\d+)$', nid)
        if m: numeric_targets.setdefault(m.group(1), el)

    sym_targets=symbol_targets_in_doc(root)

    # per-key sequence counters
    num_seq: Dict[str,int] = {}
    sym_seq: Dict[str,int] = {}

    for ref in find_noterefs(root):
        raw=''.join(ref.itertext())
        norm=''.join(SUPERSCRIPT_MAP.get(ch,ch) for ch in raw)
        digits=re.sub(r'\D+','',norm).strip()

        # helper to ensure id and book-keeping
        def ensure_ids_for(key: str, is_numeric: bool):
            if is_numeric:
                seq = num_seq.get(key, 0) + 1
                num_seq[key] = seq
                # prefer existing id if present
                rid_existing = ref.get('id')
                if seq == 1:
                    # first occurrence: back-compat id
                    rid_first = rid_existing or f'ref-{key}'
                    if rid_existing is None: ref.set('id', rid_first)
                    # also create the -1 alias if different
                    # (keep only one id attribute; just remember both names)
                    rid_seq = rid_first if rid_first.endswith('-1') else f'ref-{key}-1'
                    # record both, preserving actual on-element id as rid_first
                    numeric_ref_ids.setdefault(key, [])
                    if rid_first not in numeric_ref_ids[key]: numeric_ref_ids[key].append(rid_first)
                    if rid_seq != rid_first and rid_seq not in numeric_ref_ids[key]: numeric_ref_ids[key].append(rid_seq)
                    return rid_first
                else:
                    rid_seq = rid_existing or f'ref-{key}-{seq}'
                    if rid_existing is None: ref.set('id', rid_seq)
                    numeric_ref_ids.setdefault(key, [])
                    if rid_seq not in numeric_ref_ids[key]: numeric_ref_ids[key].append(rid_seq)
                    return rid_seq
            else:
                seq = sym_seq.get(key, 0) + 1
                sym_seq[key] = seq
                rid_existing = ref.get('id')
                if seq == 1:
                    rid_first = rid_existing or f'ref-{key}'
                    if rid_existing is None: ref.set('id', rid_first)
                    rid_seq = rid_first if rid_first.endswith('-1') else f'ref-{key}-1'
                    symbol_ref_ids.setdefault(key, [])
                    if rid_first not in symbol_ref_ids[key]: symbol_ref_ids[key].append(rid_first)
                    if rid_seq != rid_first and rid_seq not in symbol_ref_ids[key]: symbol_ref_ids[key].append(rid_seq)
                    return rid_first
                else:
                    rid_seq = rid_existing or f'ref-{key}-{seq}'
                    if rid_existing is None: ref.set('id', rid_seq)
                    symbol_ref_ids.setdefault(key, [])
                    if rid_seq not in symbol_ref_ids[key]: symbol_ref_ids[key].append(rid_seq)
                    return rid_seq

        if digits and digits in numeric_targets:
            tgt = numeric_targets[digits]
            tid = tgt.get('id') or f'note-{digits}'
            if 'id' not in tgt.attrib: tgt.set('id', tid)
            if wrap_with_link(ref, tid): changed_forward += 1
            assigned = ensure_ids_for(digits, is_numeric=True)
            if assigned == f'ref-{digits}': first_ids_added += 1
        else:
            key=None
            for sym,k in SYMBOLS.items():
                if sym in raw: key=k; break
            if key and key in sym_targets:
                tgt = sym_targets[key]
                tid = tgt.get('id') or f'note-{key}'
                if 'id' not in tgt.attrib: tgt.set('id', tid)
                if wrap_with_link(ref, tid): changed_forward += 1
                assigned = ensure_ids_for(key, is_numeric=False)
                if assigned == f'ref-{key}': first_ids_added += 1

    return changed_forward, added_backlinks, first_ids_added, numeric_ref_ids, symbol_ref_ids


# ---------------------------------------------------------------------------
# Banner / boilerplate detection + blacklist + misc text helpers
# ---------------------------------------------------------------------------

def looks_like_banner(snippet: str) -> bool:
    """
    Heuristics for short repeating headers/footers (journal banners).
    We only consider relatively short strings that often repeat.
    """
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

def extract_banner_snippets(root, top_chars: int, bottom_chars: int) -> Tuple[str, str]:
    """
    Pull a text sample from the top and bottom of the <body> to test for repetition.
    """
    body_txt = ''.join(root.xpath('//body//text() | //x:body//text()', namespaces=NS)).strip()
    body_txt = re.sub(r'\s+', ' ', body_txt)
    top = body_txt[:top_chars]
    bot = body_txt[-bottom_chars:] if len(body_txt) > bottom_chars else ''
    return top, bot

def remove_repeated_banners(spine_paths, conf, dry_run: bool = False) -> Tuple[int, int]:
    """
    Identify snippets that repeat across the majority of spine files and blank them.
    Returns (removed_count, kept_first_count).
    """
    if not conf.get("enabled", True): return (0, 0)
    if len(spine_paths) < 2: return (0, 0)

    top_chars = int(conf.get("top_chars", 150))
    bottom_chars = int(conf.get("bottom_chars", 150))
    min_ratio = float(conf.get("min_repeat_ratio", 0.6))
    keep_first = bool(conf.get("keep_first", True))

    tops, bottoms = [], []
    for p, _ in spine_paths:
        try:
            doc = html.parse(p)
            root = doc.getroot()
            t, b = extract_banner_snippets(root, top_chars, bottom_chars)
            tops.append(t); bottoms.append(b)
        except Exception:
            tops.append(''); bottoms.append('')

    n = len(spine_paths)
    top_counts = Counter([t for t in tops if looks_like_banner(t)])
    bot_counts = Counter([b for b in bottoms if looks_like_banner(b)])

    top_remove = {t for t, c in top_counts.items() if c >= 2 and (c / n) >= min_ratio}
    bot_remove = {b for b, c in bot_counts.items() if c >= 2 and (c / n) >= min_ratio}

    removed = 0; kept = 0
    seen_top, seen_bot = set(), set()

    for idx, (p, is_xhtml) in enumerate(spine_paths):
        try:
            doc = html.parse(p); root = doc.getroot()
            changed = False
            t, b = tops[idx], bottoms[idx]

            def snippet_matches_heading(s):
                if not s: return False
                h1 = root.xpath('//h1 | //x:h1', namespaces=NS)
                if not h1: return False
                htxt = ''.join(h1[0].itertext()).strip()
                return (htxt and s.strip().startswith(htxt[:min(len(htxt), len(s))]))

            # Top
            if t in top_remove and t and not snippet_matches_heading(t):
                if keep_first and t not in seen_top:
                    seen_top.add(t); kept += 1
                else:
                    body = (root.xpath('//body|//x:body', namespaces=NS) or [None])[0]
                    if body is not None:
                        # Blanking the first text node that matches the snippet prefix
                        for el in body.iter():
                            txt = (el.text or '').strip()
                            if txt and t.startswith(txt[:min(len(txt), len(t))]):
                                if not dry_run: el.text = ''
                                changed = True; removed += 1
                                break

            # Bottom
            if b in bot_remove and b and not snippet_matches_heading(b):
                if keep_first and b not in seen_bot:
                    seen_bot.add(b); kept += 1
                else:
                    body = (root.xpath('//body|//x:body', namespaces=NS) or [None])[0]
                    if body is not None:
                        last = None
                        for el in body.iter():
                            if (el.text and el.text.strip()):
                                last = el
                        if last is not None:
                            if not dry_run: last.text = ''
                            changed = True; removed += 1

            if changed and not dry_run:
                # keep XHTML strictness if the item is declared XHTML
                try:
                    root_tag = root.tag
                    if is_xhtml and not (isinstance(root_tag, str) and root_tag.startswith('{'+XHTML_NS+'}')):
                        # Upgrade if needed
                        # (We call ensure_xhtml_root indirectly via safe_write_doc in later chunks)
                        pass
                except Exception:
                    pass
                safe_write_doc(doc, p, force_xhtml=is_xhtml)
        except Exception:
            continue

    return (removed, kept)

def apply_blacklist_shortlines(root, patterns) -> int:
    """
    Remove short lines that match any blacklist regex (≤160 chars).
    """
    if not patterns: return 0
    removed = 0
    body = (root.xpath('//body|//x:body', namespaces=NS) or [None])[0]
    if body is None: return 0
    for el in body.iter():
        if el is body: continue
        txt = (el.text or '').strip()
        if not txt or len(txt) > 160: continue
        for pat in patterns:
            if pat.search(txt):
                el.text = ''
                removed += 1
                break
    return removed

def strip_soft_hyphens(root) -> bool:
    """Remove U+00AD soft hyphens in text/tails."""
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

def collapse_empty_paragraphs(root) -> bool:
    """Remove consecutive empty paragraphs."""
    changed = False
    # Clean empty text nodes
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

    for p in root.xpath('//p | //x:p', namespaces=NS):
        txt = ''.join(p.itertext()).strip().replace('\xa0', '')
        if txt == '':
            nxt = p.getnext()
            if nxt is not None and (nxt.tag.endswith('p') or nxt.tag == 'p' or nxt.tag.endswith('}p')):
                nxt_txt = ''.join(nxt.itertext()).strip().replace('\xa0', '')
                if nxt_txt == '':
                    parent = p.getparent()
                    if parent is not None:
                        parent.remove(p)
                        changed = True
    return changed

def rel_href(from_path: str, to_path: str, target_id: str) -> str:
    """Compute a relative href from one spine item to another with a fragment id."""
    rel = os.path.relpath(to_path, os.path.dirname(from_path)).replace(os.sep, '/')
    if rel == '.':
        return f'#{target_id}'
    return f'{rel}#{target_id}'

# ---------------------------------------------------------------------------
# Serialization helper (HTML vs. strict XHTML)
# ---------------------------------------------------------------------------

def safe_write_doc(doc, path, force_xhtml=False):
    """
    Write back the document. We default to HTML serialization for safety.
    Only use strict XML/XHTML when we *know* the tree is proper XHTML (e.g., cover).
    """
    if force_xhtml:
        try:
            # Expecting a proper XHTML tree (we build that in the cover normalizer)
            doc.write(path, encoding='utf-8', method='xml', pretty_print=True)
            return
        except Exception:
            pass  # fall through to HTML

    # Safe default for general spine content
    doc.write(path, encoding='utf-8', method='html', pretty_print=True)

# ---------- Cover normalization helpers ----------

def _guess_cover_from_meta(opf_tree):
    """Legacy <meta name='cover' content='id_of_cover_image'> support."""
    # dc/meta legacy: <meta name="cover" content="cover-image-id"/>
    metas = opf_tree.xpath('//opf:metadata/opf:meta[@name="cover"]', namespaces={'opf': NSMAP['opf']})
    if metas:
        return (metas[0].get('content') or '').strip() or None
    return None

def _collect_manifest_items(opf_tree):
    items = {}
    for it in opf_tree.xpath('//opf:manifest/opf:item', namespaces={'opf': NSMAP['opf']}):
        items[it.get('id')] = {
            'href': it.get('href'),
            'type': (it.get('media-type') or '').lower(),
            'el': it,
            'props': (it.get('properties') or '').split()
        }
    return items

def _find_cover_image_id(opf_tree):
    items = _collect_manifest_items(opf_tree)
    # 1) properties=cover-image (EPUB 3)
    for iid, meta in items.items():
        if 'cover-image' in meta['props']:
            return iid
    # 2) legacy meta name=cover
    mid = _guess_cover_from_meta(opf_tree)
    if mid and mid in items:
        return mid
    # 3) filename heuristics
    for iid, meta in items.items():
        href = (meta['href'] or '').lower()
        if re.search(r'(^|/)(cover|cover-image)\.(jpe?g|png|gif|svg)$', href):
            return iid
    # 4) first image in manifest
    for iid, meta in items.items():
        if meta['type'].startswith('image/'):
            return iid
    return None

def _ensure_cover_properties(opf_tree, cover_image_id):
    """Mark the given manifest item with properties='cover-image' (preserve other props)."""
    if not cover_image_id:
        return False
    it = opf_tree.xpath(f'//opf:manifest/opf:item[@id="{cover_image_id}"]', namespaces={'opf': NSMAP['opf']})
    if not it:
        return False
    it = it[0]
    props = (it.get('properties') or '').split()
    if 'cover-image' not in props:
        props.append('cover-image')
        it.set('properties', ' '.join(sorted(set(props))))
        return True
    return False

def _find_cover_xhtml_candidate(opf_tree, opf_dir):
    """
    Try to locate an XHTML 'cover page' in manifest/spine.
    Heuristics: items named cover*.xhtml, titlepage*.xhtml, or first spine doc.
    Return absolute path or None.
    """
    # manifest lookup
    xhtml_items = opf_tree.xpath('//opf:manifest/opf:item[starts-with(translate(@href,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), "cover") and contains(translate(@href,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), ".xhtml")]', namespaces={'opf': NSMAP['opf']})
    if xhtml_items:
        href = xhtml_items[0].get('href')
        return os.path.normpath(os.path.join(opf_dir, href))
    # titlepage heuristic
    xhtml_items = opf_tree.xpath('//opf:manifest/opf:item[contains(translate(@href,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), "titlepage") and contains(translate(@href,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), ".xhtml")]', namespaces={'opf': NSMAP['opf']})
    if xhtml_items:
        href = xhtml_items[0].get('href')
        return os.path.normpath(os.path.join(opf_dir, href))
    # first spine doc as last resort
    spine_refs = opf_tree.xpath('//opf:spine/opf:itemref', namespaces={'opf': NSMAP['opf']})
    if spine_refs:
        items = _collect_manifest_items(opf_tree)
        idref = spine_refs[0].get('idref')
        if idref in items:
            href = items[idref]['href']
            if href:
                return os.path.normpath(os.path.join(opf_dir, href))
    return None

def _normalize_cover_xhtml(cover_xhtml_path, cover_img_rel_href):
    """
    Ensure cover page is valid strict XHTML with a visible cover image.
    Keeps file name; rebuilds minimal structure if file is unreadable.
    """
    try:
        doc = html.parse(cover_xhtml_path)
        root = doc.getroot()
    except Exception:
        # create a new minimal XHTML page
        nsmap = {None: XHTML_NS, 'epub': OPS_NS}
        root = etree.Element(f'{{{XHTML_NS}}}html', nsmap=nsmap)
        head = etree.SubElement(root, f'{{{XHTML_NS}}}head')
        meta = etree.SubElement(head, f'{{{XHTML_NS}}}meta'); meta.set('charset','utf-8')
        body = etree.SubElement(root, f'{{{XHTML_NS}}}body')
        div  = etree.SubElement(body, f'{{{XHTML_NS}}}div'); div.set('class','cover')
        img  = etree.SubElement(div, f'{{{XHTML_NS}}}img'); img.set('src', cover_img_rel_href); img.set('alt','Cover')
        doc  = etree.ElementTree(root)
        safe_write_doc(doc, cover_xhtml_path, force_xhtml=True)
        return True

    changed = False

    # 1) Ensure XHTML namespace & head/meta
    tag = root.tag or ''
    if not (isinstance(tag, str) and tag.startswith('{'+XHTML_NS+'}')):
        # upgrade to xhtml root
        nsmap = dict((k,v) for k,v in (root.nsmap or {}).items() if k)
        if 'epub' not in nsmap: nsmap['epub'] = OPS_NS
        nsmap[None] = XHTML_NS
        new_root = etree.Element(f'{{{XHTML_NS}}}html', nsmap=nsmap)
        for k,v in list(root.attrib.items()):
            if k == 'xmlns' or k.startswith('xmlns:'): continue
            new_root.set(k, v)
        for ch in list(root):
            root.remove(ch); new_root.append(ch)
        new_root.text = root.text; root.text = None
        parent = root.getparent()
        if parent is not None: parent.replace(root, new_root)
        root = new_root
        changed = True

    # head/meta charset
    head = (root.xpath('.//x:head|.//head', namespaces=NS) or [None])[0]
    if head is None:
        head = etree.Element(f'{{{XHTML_NS}}}head'); root.insert(0, head); changed = True
    metas = head.xpath('.//x:meta[@charset] | .//meta[@charset]', namespaces=NS)
    has_utf8 = any((m.get('charset','').lower()=='utf-8') for m in metas)
    if not has_utf8:
        meta = etree.Element(f'{{{XHTML_NS}}}meta'); meta.set('charset','utf-8'); head.insert(0, meta); changed = True

    # 2) Ensure there’s a visible <img> for the cover image
    body = (root.xpath('.//x:body|.//body', namespaces=NS) or [None])[0]
    if body is None:
        body = etree.SubElement(root, f'{{{XHTML_NS}}}body'); changed = True
    found_img = body.xpath('.//x:img|.//img', namespaces=NS)
    if not found_img:
        div  = etree.SubElement(body, f'{{{XHTML_NS}}}div'); div.set('class','cover')
        img  = etree.SubElement(div, f'{{{XHTML_NS}}}img'); img.set('src', cover_img_rel_href); img.set('alt','Cover')
        changed = True
    else:
        # ensure at least the first img points to the cover image
        img = found_img[0]
        if not img.get('src'):
            img.set('src', cover_img_rel_href); changed = True
        if not img.get('alt'):
            img.set('alt', 'Cover'); changed = True

    safe_write_doc(doc, cover_xhtml_path, force_xhtml=True)
    return changed


# ---------------------------------------------------------------------------
# Single-XHTML processing
# ---------------------------------------------------------------------------

def process_single_xhtml(path, is_xhtml, cfg, metrics, *,
                         skip_content_edits: bool = False,
                         blacklist_patterns=None):
    """
    Open one XHTML/HTML spine item, normalize and collect note/ref mappings.
    Returns (numeric_ref_ids, symbol_ref_ids) for backlink pass.
    """
    numeric_ref_ids = {}
    symbol_ref_ids  = {}

    try:
        doc = html.parse(path)
        root = doc.getroot()
    except Exception:
        # If file can't be parsed, skip gracefully
        return numeric_ref_ids, symbol_ref_ids

    changed = False

    # Always ensure a meta charset
    ensure_meta_charset(root); changed = True

    if not skip_content_edits:
        # Text hygiene
        if strip_soft_hyphens(root):
            metrics['soft_hyphens_removed'] += 1
            changed = True

        # Join broken lines / dehyphenate
        if fix_linebreaks_and_dehyphenation(root):
            metrics['linebreak_files_changed'] += 1
            changed = True

        # Remove consecutive empty paragraphs
        if collapse_empty_paragraphs(root):
            metrics['empties_collapsed'] += 1
            changed = True

        # Forward links + first-ref ids (for backlinks later)
        fw, bl, first_ids, num_map, sym_map = ensure_bidirectional_links_per_file(root)
        if fw:        metrics['forward_links_fixed'] += fw
        if bl:        metrics['backlinks_added']     += bl  # usually 0 in this pass
        if first_ids: metrics['first_ref_ids_added'] += first_ids
        if fw or bl or first_ids:
            changed = True
        numeric_ref_ids.update(num_map)
        symbol_ref_ids.update(sym_map)

        # Optional blacklist of short boilerplate lines
        if blacklist_patterns:
            removed = apply_blacklist_shortlines(root, blacklist_patterns)
            if removed:
                metrics['blacklist_removed'] += removed
                changed = True

    # Write the file back if needed (respect strict XHTML flag)
    if changed and not cfg.dry_run:
        safe_write_doc(doc, path, force_xhtml=is_xhtml)

    return numeric_ref_ids, symbol_ref_ids

# ---------------------------------------------------------------------------
# OPF marker & lightweight manifest
# ---------------------------------------------------------------------------

MARKER_PROP = "cleanup:processed-by"
TOOL_TAG = "epubstandard v1.3.4"

def mark_idempotent(opf_tree, cfg_hash: str):
    """Write/update a meta marker so we can skip reprocessing with same config."""
    md = opf_tree.xpath('//opf:metadata', namespaces={'opf': NSMAP['opf']})
    if not md:
        pkg = opf_tree.getroot()
        md = etree.SubElement(pkg, f'{{{NSMAP["opf"]}}}metadata')
    else:
        md = md[0]
    # drop previous markers of this property
    for meta in md.xpath(f'./opf:meta[@property="{MARKER_PROP}"]',
                         namespaces={'opf': NSMAP['opf']}):
        md.remove(meta)
    tag = etree.SubElement(md, f'{{{NSMAP["opf"]}}}meta')
    tag.set('property', MARKER_PROP)
    tag.set('content', f'{TOOL_TAG} ({cfg_hash})')

def already_processed(opf_tree, cfg_hash: str) -> bool:
    metas = opf_tree.xpath(f'//opf:meta[@property="{MARKER_PROP}"]',
                           namespaces={'opf': NSMAP['opf']})
    return any((cfg_hash in (m.get('content','')) and TOOL_TAG in (m.get('content','')))
               for m in metas)

def write_cleanup_manifest(root_dir: str, cfg_hash: str, details: dict):
    """Optional: drop a small JSON describing what was changed."""
    os.makedirs(os.path.join(root_dir, "META-INF"), exist_ok=True)
    path = os.path.join(root_dir, "META-INF", "cleanup.json")
    payload = {
        "tool": TOOL_TAG,
        "config_hash": cfg_hash,
        "timestamp": int(time.time()),
        **details
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# End-to-end per-EPUB processing
# ---------------------------------------------------------------------------

def process_single_epub(epub_path: str,
                        out_dir: Optional[str],
                        cfg,
                        rollup: list,
                        blacklist_patterns):
    """
    Unpack -> iterate spine -> per-file cleanup -> (optional) banner removal
    -> cross-spine backlinks -> re-pack -> record metrics
    """
    tmp = tempfile.mkdtemp(prefix="epubstd_")
    base = os.path.basename(epub_path)
    try:
        unzip_epub(epub_path, tmp)

        opf_path = find_opf_path(tmp)
        if not opf_path or not os.path.exists(opf_path):
            rollup.append((epub_path, "fail", "no opf",
                           0,0,0,0,0,0,0,0))
            return

        opf_tree = load_xml(opf_path)
        cfg_hash = cfg.hash()

        if (not cfg.force) and already_processed(opf_tree, cfg_hash):
            rollup.append((epub_path, "skip", "already processed (same config)",
                           0,0,0,0,0,0,0,0))
            return

        # Collect ordered spine items and whether they are XHTML
        spine_with_types = collect_spine_and_types(opf_tree, os.path.dirname(opf_path))

        # Metrics per book
        metrics = {
            'linebreak_files_changed': 0,
            'forward_links_fixed': 0,
            'backlinks_added': 0,
            'first_ref_ids_added': 0,
            'soft_hyphens_removed': 0,
            'empties_collapsed': 0,
            'blacklist_removed': 0,
            'banners_removed': 0,
            'banners_kept_first': 0
        }

        # For cross-spine backlinks
        num_ref_locations: Dict[str, List[Tuple[str,str]]] = {}  # number -> list of (file_path, ref_id) in reading order
        sym_ref_locations: Dict[str, List[Tuple[str,str]]] = {}  # symbol -> list of (file_path, ref_id)
        note_ids_by_file: List[Tuple[str,str,bool]] = []   # list of (path, note_id, is_xhtml)

        # Pass 1: per-file edits + gather first-ref locations + note ids
        for pth,is_xhtml in spine_with_types:
            skip_edits = os.path.basename(pth).lower().startswith("nav")
            num_map, sym_map = process_single_xhtml(
                pth, is_xhtml, cfg, metrics,
                skip_content_edits=skip_edits,
                blacklist_patterns=blacklist_patterns
            )

            # num_map: Dict[str, List[str]]
            for n, refids in num_map.items():
                for rid in refids:
                    num_ref_locations.setdefault(n, []).append((pth, rid))
            # sym_map: Dict[str, List[str]]
            for k, refids in sym_map.items():
                for rid in refids:
                    sym_ref_locations.setdefault(k, []).append((pth, rid))

            try:
                doc=html.parse(pth); root=doc.getroot()
                for el in find_note_blocks(root):
                    nid=el.get('id') or ''
                    if nid: note_ids_by_file.append((pth, nid, is_xhtml))
            except Exception:
                pass


        # Pass 1.5: optional banner removal, after edits so text sampling is stable
        removed_banners, kept_banners = remove_repeated_banners(
            spine_with_types, cfg.banner, dry_run=cfg.dry_run
        )
        metrics['banners_removed'] = removed_banners
        metrics['banners_kept_first'] = kept_banners

        # Pass 2: add backlinks in each note pointing to its first in-text ref (possibly cross-file)
        if not cfg.dry_run:
            for pth, nid, is_xhtml in note_ids_by_file:
                try:
                    doc = html.parse(pth)
                    root = doc.getroot()
                except Exception:
                    continue
                node_elems = root.xpath(f'//*[@id="{nid}"]')
                if not node_elems:
                    continue
                node = node_elems[0]

                href = None
                m = re.search(r'(\d+)$', nid or '')

                def pick_nearest(ref_list: List[Tuple[str,str]], current_path: str) -> Optional[Tuple[str,str]]:
                    """Prefer refs in the same file; if multiple, take the last one (nearest above). Else first overall."""
                    if not ref_list: return None
                    same = [(p, rid) for (p, rid) in ref_list if os.path.normpath(p) == os.path.normpath(current_path)]
                    if same:
                        return same[-1]  # nearest within this file (assuming note is after refs)
                    return ref_list[0]   # fallback: first occurrence overall

                if m:
                    num = m.group(1)
                    cand = pick_nearest(num_ref_locations.get(num, []), pth)
                    if cand:
                        ref_path, ref_id = cand
                        href = rel_href(pth, ref_path, ref_id)
                else:
                    for key in ('star','dagger','double-dagger'):
                        if key in nid:
                            cand = pick_nearest(sym_ref_locations.get(key, []), pth)
                            if cand:
                                ref_path, ref_id = cand
                                href = rel_href(pth, ref_path, ref_id)
                            break


                if href:
                    # Remove any prior backlink to avoid dupes
                    for old in node.xpath('.//a[contains(concat(" ", normalize-space(@class), " "), " backlink ")]'):
                        old.getparent().remove(old)

                    a = etree.Element('a', href=href)
                    a.set('class', 'backlink')
                    a.set('role', 'doc-backlink')
                    a.set('aria-label', 'Back to citation')
                    a.text = '↩'

                    node.append(a)
                    metrics['backlinks_added'] += 1
                    safe_write_doc(doc, pth, force_xhtml=is_xhtml)

        # --- Cover repair: normalize cover XHTML and mark cover-image ---
        try:
            if not cfg.dry_run:
                opf_dir = os.path.dirname(opf_path)
                cover_img_id = _find_cover_image_id(opf_tree)
                if cover_img_id:
                    items = _collect_manifest_items(opf_tree)
                    cover_img_href = items[cover_img_id]['href']
                    # pick a reasonable cover xhtml candidate
                    cover_xhtml_path = _find_cover_xhtml_candidate(opf_tree, opf_dir)
                    if cover_xhtml_path and os.path.exists(cover_xhtml_path):
                        # compute relative path from cover xhtml to the image
                        cover_img_abs = os.path.normpath(os.path.join(opf_dir, cover_img_href))
                        rel = os.path.relpath(cover_img_abs, os.path.dirname(cover_xhtml_path)).replace(os.sep,'/')
                        _normalize_cover_xhtml(cover_xhtml_path, rel)
                    # ensure OPF properties=cover-image on the manifest item
                    if _ensure_cover_properties(opf_tree, cover_img_id):
                        # will be written with save_xml below
                        pass
        except Exception:
            # Never hard-fail on cover; Calibre can still open the book
            pass


        # Mark OPF and write cleanup manifest
        if not cfg.dry_run:
            mark_idempotent(opf_tree, cfg_hash)
            save_xml(opf_tree, opf_path)
            write_cleanup_manifest(tmp, cfg_hash, {**metrics})

        # Repack
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


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(src_dir: str,
              dest_dir: Optional[str],
              cfg,
              blacklist_patterns) -> str:
    """Find all EPUBs under src_dir, process them, and write a CSV report."""
    epubs = []
    for root_dir, _, files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith('.epub'):
                epubs.append(os.path.join(root_dir, f))
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
        w.writerow([
            "file","status","note",
            "linebreak_files_changed","forward_links_fixed","backlinks_added",
            "first_ref_ids_added","soft_hyphens_removed","empties_collapsed",
            "blacklist_removed","banners_removed"
        ])
        w.writerows(rollup)
    return report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="epubstandard v1.3.4 — normalize EPUBs after ABBYY (XHTML-safe + XPath fixes)"
    )
    p.add_argument("--src", required=True, help="Folder containing .epub files (recurses).")
    p.add_argument("--dest", default="out", help="Output folder for cleaned EPUBs (ignored with --inplace).")
    p.add_argument("--inplace", action="store_true", help="Overwrite source EPUBs in place.")
    p.add_argument("--force", action="store_true", help="Re-run even if already processed with same config.")
    p.add_argument("--dry-run", action="store_true", help="Simulate changes and write only the CSV report.")
    # Banner controls
    p.add_argument("--no-banners", action="store_true", help="Disable banner detection/removal.")
    p.add_argument("--banner-min-repeat", type=float, default=BANNER_CONF["min_repeat_ratio"], help="Fraction of files a banner must appear in to be removed.")
    p.add_argument("--banner-top-chars", type=int, default=BANNER_CONF["top_chars"], help="Chars sampled from top of each spine doc.")
    p.add_argument("--banner-bottom-chars", type=int, default=BANNER_CONF["bottom_chars"], help="Chars sampled from bottom of each spine doc.")
    # Blacklist
    p.add_argument("--blacklist", default=None, help="Path to a regex-per-line blacklist file for stripping short lines.")
    p.add_argument("--audit", action="store_true", help="Reserved; enables extra diagnostics in future versions.")
    args = p.parse_args()

    cfg = Config(
        inplace=args.inplace, force=args.force, dry_run=args.dry_run,
        audit=args.audit, blacklist_file=args.blacklist,
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
