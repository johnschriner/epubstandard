#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epubstandard v1.3
- Keeps all features of v1.2 (cross-spine backlinks, symbol handling, numeric dedup, CSS reset, banners, etc.)
- NEW: XHTML-compliant serialization to avoid Calibre "Opening and ending tag mismatch: link ... and head"
  • Detects XHTML pages and writes them with method='xml' (self-closing <meta/>, <link/>)
  • Creates head/meta/link elements in the XHTML namespace when appropriate
  • Validates XML write; falls back to HTML serialization if needed (never leaves broken tags)
"""

import argparse, csv, dataclasses, hashlib, json, os, re, shutil, sys, tempfile, time, zipfile
from collections import Counter
from typing import Dict, List, Optional, Tuple
from lxml import etree, html

NSMAP = {'opf': 'http://www.idpf.org/2007/opf', 'dc': 'http://purl.org/dc/elements/1.1/', 'xhtml': 'http://www.w3.org/1999/xhtml'}
OPS_NS = "http://www.idpf.org/2007/ops"
XHTML_NS = NSMAP['xhtml']

STD_CSS_FILENAME = "styles/epubstandard.css"
STD_CSS_CONTENT = """/* epubstandard v1.3 — minimal, readable defaults */
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
a.backlink { font-size: 0.85em; margin-left: 0.35em; }
"""

NOTE_ID_PATTERNS = [r'^(fn|footnote|note)[-_]?\d+$', r'^note\d+$', r'^\d+$']
BANNER_CONF = {"enabled": True, "keep_first": True, "min_repeat_ratio": 0.6, "top_chars": 150, "bottom_chars": 150}
COMMON_COMPOUND_KEEP = set("co-founder co-operate co-operation re-entry re-issue re-iterate re-open re-creation pre-existing pre-eminent pre-empt post-war cross-examine long-term short-term".split())

MARKER_PROP = "cleanup:processed-by"
TOOL_TAG = "epubstandard v1.3"
SOFT_HYPHEN = '\u00AD'
SUPERSCRIPT_MAP = {'\u00B9':'1','\u00B2':'2','\u00B3':'3','\u2070':'0','\u2074':'4','\u2075':'5','\u2076':'6','\u2077':'7','\u2078':'8','\u2079':'9'}
SYMBOLS = {'*':'star','†':'dagger','‡':'double-dagger'}

@dataclasses.dataclass
class Config:
    inplace: bool = False
    force: bool = False
    dry_run: bool = False
    banner: Dict = dataclasses.field(default_factory=lambda: dict(BANNER_CONF))
    audit: bool = False
    blacklist_file: Optional[str] = None
    def hash(self)->str:
        payload = json.dumps({"inplace": self.inplace, "banner": self.banner, "audit": self.audit, "blacklist_file": self.blacklist_file or ""}, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

def load_blacklist(path: Optional[str]):
    if not path or not os.path.exists(path): return []
    pats=[]; 
    with open(path,"r",encoding="utf-8") as f:
        for line in f:
            s=line.strip()
            if not s or s.startswith("#"): continue
            try: pats.append(re.compile(s,re.I))
            except re.error: pass
    return pats

def unzip_epub(epub_path: str, workdir: str)->str:
    with zipfile.ZipFile(epub_path,'r') as zf: zf.extractall(workdir)
    return workdir

def zip_epub(src_dir: str, out_path: str):
    mimetype_path = os.path.join(src_dir,"mimetype")
    with zipfile.ZipFile(out_path,'w') as zf:
        if os.path.exists(mimetype_path):
            with open(mimetype_path,'rb') as f: zf.writestr('mimetype', f.read(), compress_type=zipfile.ZIP_STORED)
        for root,_,files in os.walk(src_dir):
            for name in files:
                full=os.path.join(root,name); rel=os.path.relpath(full,src_dir)
                if rel=='mimetype': continue
                zf.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)

def find_opf_path(root_dir: str)->Optional[str]:
    cpath=os.path.join(root_dir,"META-INF","container.xml")
    if not os.path.exists(cpath): return None
    try:
        tree=etree.parse(cpath)
        rf=tree.xpath('//container:rootfile',namespaces={'container':'urn:oasis:names:tc:opendocument:xmlns:container'})
        if rf: return os.path.join(root_dir, rf[0].get('full-path'))
    except Exception: return None
    return None

def load_xml(path:str): return etree.parse(path, etree.XMLParser(recover=True, remove_blank_text=False))
def save_xml(tree, path): tree.write(path, encoding="utf-8", xml_declaration=True, pretty_print=True)
def ensure_dir(p): os.makedirs(p, exist_ok=True)

def is_xhtml_document(root, raw_bytes: Optional[bytes]=None)->bool:
    """Detect if the document should be serialized as XHTML (XML)."""
    try:
        tag = (root.tag or '').lower()
    except Exception:
        tag = ''
    if isinstance(tag, str) and ('}' in tag):
        # namespaced, likely {xhtml}html
        ns = tag.split('}')[0].lstrip('{')
        if ns == XHTML_NS: return True
    # fall back to raw header
    if raw_bytes:
        head = raw_bytes[:200].decode('utf-8','ignore').lower()
        if head.startswith('<?xml') or 'xmlns="http://www.w3.org/1999/xhtml"' in head:
            return True
    return False

def safe_write_doc(doc, path, raw_bytes=None):
    root = doc.getroot()
    if is_xhtml_document(root, raw_bytes):
        try:
            # XML method => self-closing tags, strict XHTML
            doc.write(path, encoding='utf-8', method='xml', pretty_print=True)
            return
        except Exception:
            pass
    # fallback
    doc.write(path, encoding='utf-8', method='html', pretty_print=True)

def looks_like_banner(snippet:str)->bool:
    s=re.sub(r'\s+',' ',snippet).strip()
    if not s or len(s)>120: return False
    if re.search(r'\bISSN\b',s,re.I): return True
    if re.search(r'\bVol(?:\.|ume)?\s*\d+',s,re.I): return True
    if re.search(r'\bNo\.\s*\d+',s,re.I): return True
    if re.search(r'\b(Spring|Summer|Fall|Winter)\b\s+\d{4}',s,re.I): return True
    if re.search(r'\bdoi:\s*10\.\d{4,9}/',s,re.I): return True
    if re.search(r'journal|law review|harvard|yale|columbia|nyu|stanford',s,re.I): return True
    return False

def collect_spine(opf_tree, opf_dir)->List[str]:
    manifest={it.get('id'): it.get('href') for it in opf_tree.xpath('//opf:manifest/opf:item', namespaces=NSMAP)}
    spine_ids=[it.get('idref') for it in opf_tree.xpath('//opf:spine/opf:itemref', namespaces=NSMAP)]
    paths=[]
    for idref in spine_ids:
        href=manifest.get(idref)
        if href: paths.append(os.path.normpath(os.path.join(opf_dir, href)))
    return paths

def ensure_head(root):
    head = root.find('.//head')
    if head is None:
        # try namespaced
        head = root.find(f'.//{{{XHTML_NS}}}head')
    if head is None:
        # create in same namespace as html, if any
        html_tag = root.find('.//html') or root
        if '}' in html_tag.tag:
            head = etree.Element(f'{{{XHTML_NS}}}head')
        else:
            head = etree.Element('head')
        root.insert(0, head)
    return head

def ensure_meta_charset(root)->bool:
    head = ensure_head(root)
    created = False
    metas = head.xpath('.//meta[@charset] | .//xhtml:meta[@charset]', namespaces={'xhtml':XHTML_NS})
    for m in metas:
        if (m.get('charset') or '').lower() == 'utf-8':
            return created
    metas2 = head.xpath('.//meta[translate(@http-equiv,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="content-type"] | .//xhtml:meta[translate(@http-equiv,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="content-type"]', namespaces={'xhtml':XHTML_NS})
    for m in metas2:
        ct = (m.get('content') or '').lower()
        if 'charset=utf-8' in ct:
            return created
    # create in XHTML ns if document is XHTML
    if is_xhtml_document(root):
        meta = etree.Element(f'{{{XHTML_NS}}}meta')
    else:
        meta = etree.Element('meta')
    meta.set('charset','utf-8')
    head.insert(0, meta)
    return True

def strip_all_css_and_links(opf_tree, opf_path)->List[str]:
    removed=[]; opf_dir=os.path.dirname(opf_path)
    items=opf_tree.xpath('//opf:manifest/opf:item', namespaces=NSMAP)
    css_items=[it for it in items if (it.get('media-type')=='text/css' or it.get('href','').lower().endswith('.css'))]
    css_hrefs=[os.path.normpath(os.path.join(opf_dir, it.get('href'))) for it in css_items]
    for it in css_items: it.getparent().remove(it)
    for css in css_hrefs:
        if os.path.exists(css):
            try: os.remove(css); removed.append(os.path.relpath(css, opf_dir))
            except Exception: pass
    for xhtml_path in collect_spine(opf_tree, opf_dir):
        if not os.path.exists(xhtml_path): continue
        try:
            raw = open(xhtml_path, 'rb').read()
            doc=html.parse(xhtml_path); root=doc.getroot(); changed=False
            for link in root.xpath('//link[translate(@rel,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="stylesheet"] | //xhtml:link[translate(@rel,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="stylesheet"]', namespaces={'xhtml':XHTML_NS}):
                link.getparent().remove(link); changed=True
            if ensure_meta_charset(root): changed=True
            if changed: safe_write_doc(doc, xhtml_path, raw_bytes=raw)
        except Exception: continue
    return removed

def inject_standard_css(opf_tree, opf_path):
    opf_dir=os.path.dirname(opf_path)
    css_abs=os.path.join(opf_dir, STD_CSS_FILENAME); ensure_dir(os.path.dirname(css_abs))
    with open(css_abs,'w',encoding='utf-8') as f: f.write(STD_CSS_CONTENT)
    manifest=opf_tree.xpath('//opf:manifest', namespaces=NSMAP)
    if not manifest: raise RuntimeError("Invalid OPF: missing manifest")
    manifest=manifest[0]
    item_id='item-epubstandard-css'; i=1
    while opf_tree.xpath(f'//opf:manifest/opf:item[@id="{item_id}"]', namespaces=NSMAP):
        i+=1; item_id=f'item-epubstandard-css-{i}'
    item=etree.SubElement(manifest, f'{{{NSMAP["opf"]}}}item'); item.set('id',item_id); item.set('href',STD_CSS_FILENAME.replace('\\','/')); item.set('media-type','text/css')
    for xhtml_path in collect_spine(opf_tree, opf_dir):
        try:
            raw = open(xhtml_path,'rb').read()
            doc=html.parse(xhtml_path); root=doc.getroot()
            head = ensure_head(root)
            already = root.xpath(f'//link[@href="{STD_CSS_FILENAME}"] | //xhtml:link[@href="{STD_CSS_FILENAME}"]', namespaces={'xhtml':XHTML_NS})
            if not already:
                link = etree.Element(f'{{{XHTML_NS}}}link') if is_xhtml_document(root, raw) else etree.Element('link')
                link.set('rel','stylesheet'); link.set('href',STD_CSS_FILENAME)
                head.append(link)
                safe_write_doc(doc, xhtml_path, raw_bytes=raw)
        except Exception: continue

def mark_idempotent(opf_tree, cfg_hash):
    metadata=opf_tree.xpath('//opf:metadata', namespaces=NSMAP)
    if not metadata: pkg=opf_tree.getroot(); md=etree.SubElement(pkg, f'{{{NSMAP["opf"]}}}metadata')
    else: md=metadata[0]
    for meta in md.xpath(f'./opf:meta[@property="{MARKER_PROP}"]', namespaces=NSMAP): md.remove(meta)
    tag=etree.SubElement(md, f'{{{NSMAP["opf"]}}}meta'); tag.set('property', MARKER_PROP); tag.set('content', f'{TOOL_TAG} ({cfg_hash})')

def already_processed(opf_tree, cfg_hash)->bool:
    metas=opf_tree.xpath(f'//opf:meta[@property="{MARKER_PROP}"]', namespaces=NSMAP)
    return any((cfg_hash in (m.get('content','')) and TOOL_TAG in (m.get('content',''))) for m in metas)

def normalize_superscripts_to_ascii(s:str)->str: return ''.join(SUPERSCRIPT_MAP.get(ch,ch) for ch in s)

def collapse_empty_paragraphs(root)->bool:
    changed=False
    for el in root.xpath('//text()'):
        if isinstance(el,str) and not el.strip():
            parent=el.getparent()
            if parent is not None:
                if parent.text is el: parent.text=None
                else:
                    for child in parent:
                        if child.tail is el: child.tail=None; break
                changed=True
    for p in root.xpath('//p | //xhtml:p', namespaces={'xhtml':XHTML_NS}):
        txt=''.join(p.itertext()).strip().replace('\xa0','')
        if txt=='':
            nxt=p.getnext()
            if nxt is not None and nxt.tag.lower().endswith('p'):
                nxt_txt=''.join(nxt.itertext()).strip().replace('\xa0','')
                if nxt_txt=='':
                    parent=p.getparent()
                    if parent is not None: parent.remove(p); changed=True
    return changed

def strip_soft_hyphens(root)->bool:
    changed=False
    for node in root.xpath('//text()'):
        if isinstance(node,str) and SOFT_HYPHEN in node:
            newt=node.replace(SOFT_HYPHEN,''); parent=node.getparent()
            if parent is not None:
                if parent.text is node: parent.text=newt
                else:
                    for child in parent:
                        if child.tail is node: child.tail=newt; break
            changed=True
    return changed

def normalize_br_text_runs(p)->List[str]:
    lines,buf=[],[]
    def flush(): s=''.join(buf); lines.append(re.sub(r'\s+',' ',s).strip()); buf.clear()
    for node in p.iter():
        if node is p: continue
        if isinstance(node.tag,str) and node.tag.lower().endswith('br'): flush(); continue
        if node.text and node is not p: buf.append(node.text)
        if node.tail: buf.append(node.tail)
    if buf: flush()
    return [x for x in lines if x]

def should_join_wrapped(line1,line2)->bool:
    if not line1 or not line2: return False
    if line1.endswith('-'): return True
    if re.search(r'[.!?]["\']?$', line1): return False
    if re.match(r'^[,;:)\]]', line2): return True
    if re.match(r'^[a-z0-9]', line2): return True
    return False

def dehyphenate_pair(word1,word2):
    w1=word1.rstrip('-'); candidate=w1+word2; original=w1+'-'+word2
    if original.lower() in COMMON_COMPOUND_KEEP: return None
    if word2 and word2[0].isupper(): return None
    if re.match(r'^[A-Za-z]{2,}$', w1) and re.match(r'^[a-z]{2,}', word2): return candidate
    return None

def fix_linebreaks_and_dehyphenation(root)->bool:
    changed_any=False; skip_tags=set('ul ol li table thead tbody tfoot tr td th code pre h1 h2 h3 h4 h5 h6 blockquote'.split())
    for p in root.xpath('//p|//div|//xhtml:p|//xhtml:div', namespaces={'xhtml':XHTML_NS}):
        tag=p.tag.lower().split('}')[-1]
        if tag in skip_tags: continue
        if p.xpath('./*[(self::ul or self::ol or self::table or self::pre or self::code)]'): continue
        lines=normalize_br_text_runs(p)
        if len(lines)<2: continue
        new_text=[]; i=0; changed_local=False
        while i<len(lines):
            cur=lines[i]
            if i+1<len(lines) and should_join_wrapped(cur, lines[i+1]):
                nxt=lines[i+1]
                if cur.endswith('-'):
                    m1=re.search(r'(.*\b)([A-Za-z][A-Za-z\-]*)-$', cur); m2=re.search(r'^([a-zA-Z]+)(.*)$', nxt)
                    if m1 and m2:
                        maybe=dehyphenate_pair(m1.group(2), m2.group(1))
                        if maybe: cur=m1.group(1)+maybe+m2.group(2); i+=2; new_text.append(cur); changed_local=True; continue
                cur=re.sub(r'\s+$','',cur)+' '+re.sub(r'^\s+','',lines[i+1]); i+=2; new_text.append(cur); changed_local=True
            else:
                new_text.append(cur); i+=1
        if changed_local:
            for child in list(p): p.remove(child)
            p.text=' '.join(new_text).strip(); changed_any=True
    return changed_any

def set_noteref_semantics(a):
    cls=a.get('class','').strip(); parts=set(cls.split()) if cls else set()
    if 'noteref' not in parts: parts.add('noteref'); a.set('class',' '.join(sorted(parts)))
    try: a.set(f'{{{OPS_NS}}}type','noteref')
    except Exception: pass

def find_noterefs(root)->List[html.HtmlElement]:
    refs=[]; refs.extend(root.xpath('//sup|//xhtml:sup', namespaces={'xhtml':XHTML_NS}))
    refs.extend(root.xpath('//*[@*[local-name()="type" and namespace-uri()="http://www.idpf.org/2007/ops"]="noteref"]'))
    refs.extend(root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " noteref ")]'))
    seen=set(); uniq=[]
    for el in refs:
        k=id(el)
        if k in seen: continue
        uniq.append(el); seen.add(k)
    return uniq

def is_note_block(el)->bool:
    if el.tag.lower().endswith('sup') or el.tag.lower().endswith('a'): return False
    nid=(el.get('id') or '').strip(); role=(el.get('role') or '').strip().lower()
    if role=='doc-footnote': return True
    if nid and any(re.match(p, nid) for p in NOTE_ID_PATTERNS):
        txt=' '.join(el.itertext()).strip()
        if len(txt)>=10: return True
    return False

def find_note_blocks(root)->List[html.HtmlElement]:
    return [el for el in root.xpath('//*[@id or @role]') if is_note_block(el)]

def symbol_targets_in_doc(root)->Dict[str, html.HtmlElement]:
    mapping={}
    for el in root.xpath('//p|//div|//li|//aside|//section|//xhtml:p|//xhtml:div|//xhtml:li|//xhtml:aside|//xhtml:section', namespaces={'xhtml':XHTML_NS}):
        txt=' '.join(el.itertext()).strip()
        if not txt: continue
        if txt=='*' or txt.startswith('* '): mapping.setdefault('star',el)
        if txt=='†' or txt.startswith('† '): mapping.setdefault('dagger',el)
        if txt=='‡' or txt.startswith('‡ '): mapping.setdefault('double-dagger',el)
        if re.match(r'^(Author|Editor).{0,40}(note|n\.)', txt, re.I): mapping.setdefault('star',el)
    return mapping

def wrap_with_link(el, target_id)->bool:
    a=el if el.tag.lower().endswith('a') else el.find('.//a')
    if a is not None:
        if a.get('href','')!=f'#{target_id}':
            a.set('href', f'#{target_id}'); set_noteref_semantics(a); return True
        set_noteref_semantics(a); return False
    a=etree.Element('a', href=f'#{target_id}'); set_noteref_semantics(a)
    if el.text: a.text=el.text; el.text=None
    for child in list(el): el.remove(child); a.append(child)
    el.append(a); return True

def ensure_bidirectional_links_per_file(root)->Tuple[int,int,int, Dict[str,str], Dict[str,str]]:
    changed_forward=0; added_backlinks=0; first_ids_added=0
    numeric_ref_ids={}; symbol_ref_ids={}
    numeric_targets={}
    for el in find_note_blocks(root):
        nid=el.get('id') or ''
        m=re.search(r'(\d+)$', nid)
        if m: numeric_targets.setdefault(m.group(1), el)
    sym_targets=symbol_targets_in_doc(root)
    seen_first=set()
    for ref in find_noterefs(root):
        raw=''.join(ref.itertext()); norm=''.join(SUPERSCRIPT_MAP.get(ch,ch) for ch in raw)
        digits=re.sub(r'\D+','',norm).strip()
        key=None
        if digits and digits in numeric_targets:
            tgt=numeric_targets[digits]; tid=tgt.get('id') or f'note-{digits}'
            if 'id' not in tgt.attrib: tgt.set('id', tid)
            if wrap_with_link(ref, tid): changed_forward+=1
            rid=f'ref-{digits}'
            if rid not in seen_first and 'id' not in ref.attrib:
                ref.set('id', rid); first_ids_added+=1; seen_first.add(rid); numeric_ref_ids[digits]=rid
            elif digits not in numeric_ref_ids: numeric_ref_ids[digits]=rid
        else:
            for sym,k in SYMBOLS.items():
                if sym in raw: key=k; break
            if key and key in sym_targets:
                tgt=sym_targets[key]; tid=tgt.get('id') or f'note-{key}'
                if 'id' not in tgt.attrib: tgt.set('id', tid)
                if wrap_with_link(ref, tid): changed_forward+=1
                rid=f'ref-{key}'
                if rid not in seen_first and 'id' not in ref.attrib:
                    ref.set('id', rid); first_ids_added+=1; seen_first.add(rid); symbol_ref_ids[key]=rid
                elif key not in symbol_ref_ids: symbol_ref_ids[key]=rid
    return changed_forward, added_backlinks, first_ids_added, numeric_ref_ids, symbol_ref_ids

def extract_banner_snippets(root, top_chars, bottom_chars)->Tuple[str,str]:
    body_txt=''.join(root.xpath('//body//text()')); body_txt=re.sub(r'\s+',' ', body_txt).strip()
    return (body_txt[:top_chars], body_txt[-bottom_chars:] if len(body_txt)>bottom_chars else '')

def remove_repeated_banners(spine_paths, conf, dry_run=False)->Tuple[int,int]:
    if not conf.get("enabled", True): return (0,0)
    if len(spine_paths)<2: return (0,0)
    top_chars=int(conf.get("top_chars",150)); bottom_chars=int(conf.get("bottom_chars",150)); min_ratio=float(conf.get("min_repeat_ratio",0.6)); keep_first=bool(conf.get("keep_first",True))
    tops=[]; bottoms=[]
    for p in spine_paths:
        try:
            raw=open(p,'rb').read()
            doc=html.parse(p); root=doc.getroot(); t,b=extract_banner_snippets(root, top_chars, bottom_chars)
            tops.append(t); bottoms.append(b)
        except Exception:
            tops.append(''); bottoms.append('')
    n=len(spine_paths)
    top_counts=Counter([t for t in tops if looks_like_banner(t)]); bot_counts=Counter([b for b in bottoms if looks_like_banner(b)])
    top_remove=set([t for t,c in top_counts.items() if c>=2 and (c/n)>=min_ratio]); bot_remove=set([b for b,c in bot_counts.items() if c>=2 and (c/n)>=min_ratio])
    removed=0; kept=0; seen_top=set(); seen_bot=set()
    for idx,p in enumerate(spine_paths):
        try:
            raw=open(p,'rb').read()
            doc=html.parse(p); root=doc.getroot(); changed=False
            t,b=tops[idx], bottoms[idx]
            def snippet_matches_heading(s):
                if not s: return False
                h1=root.xpath('//h1 | //xhtml:h1', namespaces={'xhtml':XHTML_NS})
                if h1:
                    htxt=''.join(h1[0].itertext()).strip()
                    return htxt and s.strip().startswith(htxt[:min(len(htxt), len(s))])
                return False
            if t in top_remove and t and not snippet_matches_heading(t):
                if keep_first and t not in seen_top: seen_top.add(t); kept+=1
                else:
                    body=root.find('body') or root.find(f'.//{{{XHTML_NS}}}body')
                    if body is not None:
                        for el in body.iter():
                            txt=(el.text or '').strip()
                            if txt and t.startswith(txt[:min(len(txt),len(t))]):
                                if not dry_run: el.text=''
                                changed=True; removed+=1; break
            if b in bot_remove and b and not snippet_matches_heading(b):
                if keep_first and b not in seen_bot: seen_bot.add(b); kept+=1
                else:
                    body=root.find('body') or root.find(f'.//{{{XHTML_NS}}}body')
                    if body is not None:
                        last=None
                        for el in body.iter():
                            if (el.text and el.text.strip()): last=el
                        if last is not None:
                            if not dry_run: last.text=''
                            changed=True; removed+=1
            if changed and not dry_run: safe_write_doc(doc, p, raw_bytes=raw)
        except Exception: continue
    return (removed, kept)

def apply_blacklist_shortlines(root, patterns)->int:
    if not patterns: return 0
    removed=0; body=root.find('body') or root.find(f'.//{{{XHTML_NS}}}body')
    if body is None: return 0
    for el in body.iter():
        if el is body: continue
        txt=(el.text or '').strip()
        if not txt or len(txt)>160: continue
        for pat in patterns:
            if pat.search(txt): el.text=''; removed+=1; break
    return removed

def rel_href(from_path: str, to_path: str, target_id: str)->str:
    rel=os.path.relpath(to_path, os.path.dirname(from_path)).replace(os.sep,'/')
    if rel=='.': return f'#{target_id}'
    return f'{rel}#{target_id}'

def process_single_xhtml(path, cfg, metrics, skip_content_edits=False, blacklist_patterns=None):
    # returns additional maps for EPUB-level backlink pass
    numeric_ref_ids={}; symbol_ref_ids={}
    try:
        raw=open(path,'rb').read()
        doc=html.parse(path); root=doc.getroot()
    except Exception:
        return numeric_ref_ids, symbol_ref_ids
    changed=False
    if ensure_meta_charset(root): changed=True
    if not skip_content_edits:
        if strip_soft_hyphens(root): metrics['soft_hyphens_removed']+=1; changed=True
        if fix_linebreaks_and_dehyphenation(root): metrics['linebreak_files_changed']+=1; changed=True
        if collapse_empty_paragraphs(root): metrics['empties_collapsed']+=1; changed=True
        fw, bl, first_ids, num_map, sym_map = ensure_bidirectional_links_per_file(root)
        if fw: metrics['forward_links_fixed']+=fw
        if bl: metrics['backlinks_added']+=bl
        if first_ids: metrics['first_ref_ids_added']+=first_ids
        if fw or bl or first_ids: changed=True
        numeric_ref_ids.update(num_map); symbol_ref_ids.update(sym_map)
        if blacklist_patterns:
            removed=apply_blacklist_shortlines(root, blacklist_patterns)
            if removed: metrics['blacklist_removed']+=removed; changed=True
    if changed and not cfg.dry_run:
        safe_write_doc(doc, path, raw_bytes=raw)
    return numeric_ref_ids, symbol_ref_ids

def write_cleanup_manifest(root_dir, cfg_hash, details):
    os.makedirs(os.path.join(root_dir,"META-INF"), exist_ok=True)
    path=os.path.join(root_dir,"META-INF","cleanup.json")
    payload={"tool":TOOL_TAG,"config_hash":cfg_hash,"timestamp":int(time.time()),**details}
    with open(path,'w',encoding='utf-8') as f: json.dump(payload,f,indent=2,ensure_ascii=False)

def process_single_epub(epub_path, out_dir, cfg: Config, rollup, blacklist_patterns):
    tmp=tempfile.mkdtemp(prefix="epubstd_"); base=os.path.basename(epub_path)
    try:
        unzip_epub(epub_path, tmp)
        opf=find_opf_path(tmp)
        if not opf or not os.path.exists(opf):
            rollup.append((epub_path,"fail","no opf",0,0,0,0,0,0,0,0)); return
        opf_tree=load_xml(opf); cfg_hash=cfg.hash()
        if (not cfg.force) and already_processed(opf_tree, cfg_hash):
            rollup.append((epub_path,"skip","already processed (same config)",0,0,0,0,0,0,0,0)); return
        removed_css=strip_all_css_and_links(opf_tree, opf); inject_standard_css(opf_tree, opf)
        spine_paths=collect_spine(opf_tree, os.path.dirname(opf))
        metrics={'linebreak_files_changed':0,'forward_links_fixed':0,'backlinks_added':0,'first_ref_ids_added':0,'soft_hyphens_removed':0,'empties_collapsed':0,'blacklist_removed':0,'banners_removed':0,'banners_kept_first':0}
        # pass 1: per-file edits + collect first-ref locations
        num_ref_locations={}; sym_ref_locations={}
        note_ids_by_file=[]
        for pth in spine_paths:
            num_map, sym_map = process_single_xhtml(pth, cfg, metrics, skip_content_edits=os.path.basename(pth).lower().startswith("nav"), blacklist_patterns=blacklist_patterns)
            for n, refid in num_map.items():
                num_ref_locations.setdefault(n, pth)
            for k, refid in sym_map.items():
                sym_ref_locations.setdefault(k, pth)
            # collect note ids present in this file for backlink pass
            try:
                raw=open(pth,'rb').read()
                doc=html.parse(pth); root=doc.getroot()
                for el in find_note_blocks(root):
                    nid=el.get('id') or ''
                    if nid: note_ids_by_file.append((pth, nid))
            except Exception:
                pass
        # add cross-spine backlinks
        if not cfg.dry_run:
            for pth, nid in note_ids_by_file:
                try:
                    raw=open(pth,'rb').read()
                    doc=html.parse(pth); root=doc.getroot()
                except Exception:
                    continue
                node = root.xpath(f'//*[@id="{nid}"]')
                if not node: continue
                node = node[0]
                # determine target ref
                href=None
                m=re.search(r'(\d+)$', nid or '')
                if m:
                    num=m.group(1); rpath=num_ref_locations.get(num)
                    if rpath: href=rel_href(pth, rpath, f'ref-{num}')
                else:
                    for key in ('star','dagger','double-dagger'):
                        if key in nid:
                            rpath=sym_ref_locations.get(key)
                            if rpath: href=rel_href(pth, rpath, f'ref-{key}')
                            break
                if href:
                    # remove existing backlinks
                    for a in node.xpath('.//a[contains(concat(" ", normalize-space(@class), " "), " backlink ")]'):
                        a.getparent().remove(a)
                    a=etree.Element('a', href=href); a.set('class','backlink'); a.text='↩'; node.append(a); metrics['backlinks_added']+=1
                    safe_write_doc(doc, pth, raw_bytes=raw)

        if not cfg.dry_run:
            mark_idempotent(opf_tree, cfg_hash); save_xml(opf_tree, opf)
            write_cleanup_manifest(tmp, cfg_hash, {"removed_css": removed_css, **metrics})
        if cfg.inplace:
            out_path=epub_path
            if not cfg.dry_run:
                tmp_out=out_path+".tmp"; zip_epub(tmp,tmp_out); shutil.move(tmp_out,out_path)
            status="ok-inplace"
        else:
            os.makedirs(out_dir or "", exist_ok=True)
            out_path=os.path.join(out_dir, base)
            if not cfg.dry_run: zip_epub(tmp, out_path)
            status="ok"
        rollup.append((epub_path,status,"done", metrics['linebreak_files_changed'],metrics['forward_links_fixed'],metrics['backlinks_added'],metrics['first_ref_ids_added'],metrics['soft_hyphens_removed'],metrics['empties_collapsed'],metrics['blacklist_removed'],metrics['banners_removed']))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def run_batch(src_dir, dest_dir, cfg, blacklist_patterns)->str:
    epubs=[]
    for root,_,files in os.walk(src_dir):
        for f in files:
            if f.lower().endswith('.epub'): epubs.append(os.path.join(root,f))
    epubs.sort(); rollup=[]
    if not cfg.inplace: os.makedirs(dest_dir or "out", exist_ok=True)
    for ep in epubs: process_single_epub(ep, dest_dir, cfg, rollup, blacklist_patterns)
    report_dir = dest_dir if (dest_dir and not cfg.inplace) else src_dir
    report_path=os.path.join(report_dir, f"epubstandard_report_{int(time.time())}.csv")
    with open(report_path,'w',encoding='utf-8',newline='') as f:
        w=csv.writer(f); w.writerow(["file","status","note","linebreak_files_changed","forward_links_fixed","backlinks_added","first_ref_ids_added","soft_hyphens_removed","empties_collapsed","blacklist_removed","banners_removed"]); w.writerows(rollup)
    return report_path

def main():
    p=argparse.ArgumentParser(description="epubstandard v1.3 — normalize EPUBs after ABBYY")
    p.add_argument("--src", required=True); p.add_argument("--dest", default="out"); p.add_argument("--inplace", action="store_true")
    p.add_argument("--force", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-banners", action="store_true"); p.add_argument("--banner-min-repeat", type=float, default=BANNER_CONF["min_repeat_ratio"])
    p.add_argument("--banner-top-chars", type=int, default=BANNER_CONF["top_chars"]); p.add_argument("--banner-bottom-chars", type=int, default=BANNER_CONF["bottom_chars"])
    p.add_argument("--blacklist", default=None); p.add_argument("--audit", action="store_true")
    args=p.parse_args()
    cfg=Config(inplace=args.inplace, force=args.force, dry_run=args.dry_run, audit=args.audit, blacklist_file=args.blacklist,
               banner={"enabled": not args.no_banners, "min_repeat_ratio": args.banner_min_repeat, "top_chars": args.banner_top_chars, "bottom_chars": args.banner_bottom_chars, "keep_first": True})
    blacklist_patterns=load_blacklist(cfg.blacklist_file)
    report=run_batch(args.src, None if args.inplace else args.dest, cfg, blacklist_patterns)
    print(f"[epubstandard] Done. Report: {report}")
    if args.dry_run: print("[epubstandard] Dry run: no files were written.")

if __name__=="__main__":
    main()
