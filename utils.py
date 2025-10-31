import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from lxml import etree

# Namespaces
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS  = "http://www.idpf.org/2007/ops"
CNTR_NS  = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS   = "http://www.idpf.org/2007/opf"
DC_NS    = "http://purl.org/dc/elements/1.1/"

def unzip_epub(epub_path: Path, dest_dir: Path):
    """Extracts an EPUB file to a destination directory."""
    with zipfile.ZipFile(epub_path, 'r') as zf:
        zf.extractall(dest_dir)

def repack_epub(src_dir: Path, archive_path: Path):
    """Repacks a directory into an EPUB archive, ensuring mimetype is first and uncompressed."""
    src_dir = Path(src_dir)
    mimetype_path = src_dir / "mimetype"
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        if mimetype_path.exists():
            zf.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
        for file in sorted(src_dir.rglob("*")):
            if file.is_file() and file.name != "mimetype":
                zf.write(file, file.relative_to(src_dir))

def find_opf_and_basedir(unzip_dir: Path) -> Tuple[Optional[Path], Optional[Path], Optional[etree._ElementTree]]:
    """Finds the OPF file path, its base directory, and its parsed tree."""
    container_path = unzip_dir / "META-INF" / "container.xml"
    if not container_path.is_file():
        return None, None, None

    try:
        tree = etree.parse(str(container_path))
        rootfiles = tree.xpath("//cn:rootfile[@full-path]", namespaces={"cn": CNTR_NS})
        if rootfiles:
            opf_rel_path = rootfiles[0].get("full-path")
            opf_path = (unzip_dir / opf_rel_path).resolve()
            if opf_path.is_file():
                opf_tree = etree.parse(str(opf_path))
                return opf_path, opf_path.parent, opf_tree
    except etree.XMLSyntaxError:
        return None, None, None

    return None, None, None

def _find_epubcheck_cmd():
    """Finds the epubcheck command to run."""
    # 1. From environment variable
    jar_env = os.environ.get("EPUBCHECK")
    if jar_env and Path(jar_env).exists():
        return ["java", "-jar", jar_env]
    # 2. From common paths
    for cmd in (["epubcheck"], ["/usr/share/java/epubcheck.jar"], ["./epubcheck.jar"]):
        try:
            p = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=5)
            if p.returncode == 0:
                if cmd[0].endswith(".jar"):
                    return ["java", "-jar", cmd[0]]
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None

def run_epubcheck(epub_path: Path) -> Tuple[int, int, str]:
    """Runs epubcheck and returns (error_count, warning_count, messages)."""
    cmd = _find_epubcheck_cmd()
    if not cmd:
        return -1, -1, "[CONFIG ERROR] epubcheck not found."

    try:
        p = subprocess.run(cmd + [str(epub_path)], capture_output=True, text=True, timeout=60)
        stderr = p.stderr
        errors = len(re.findall(r"ERROR\(", stderr))
        warnings = len(re.findall(r"WARNING\(", stderr))
        return errors, warnings, stderr
    except subprocess.TimeoutExpired:
        return -1, -1, f"[TIMEOUT] epubcheck timed out after 60s on {epub_path.name}"
    except Exception as e:
        return -1, -1, f"[EXECUTION ERROR] epubcheck failed: {e}"