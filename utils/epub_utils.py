import os
import uuid
from bs4 import BeautifulSoup
from ebooklib import epub

def extract_epub_chunks(epub_path):
    book = epub.read_epub(epub_path)
    chunks = []
    id_map = {}

    for item in book.get_items():
        if item.get_type() == epub.EpubHtml:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            paragraphs = soup.find_all("p")
            if not paragraphs:
                continue
            chunk = ""
            for p in paragraphs:
                text = p.get_text(strip=True)
                if text:
                    chunk += text + "\n"
            if chunk:
                chunk_id = str(uuid.uuid4())
                chunks.append(chunk.strip())
                id_map[chunk_id] = item.get_id()
    return chunks, id_map


def rebuild_epub_from_chunks(original_path, corrected_chunks, output_path):
    book = epub.read_epub(original_path)
    chunk_map = {id_: html for id_, html in corrected_chunks}

    for item in book.get_items():
        if item.get_type() == epub.EpubHtml:
            if item.get_id() in chunk_map:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                new_content = BeautifulSoup("", "html.parser")
                for para in chunk_map[item.get_id()].split("\n"):
                    new_tag = soup.new_tag("p")
                    new_tag.string = para.strip()
                    new_content.append(new_tag)
                item.set_content(str(new_content).encode("utf-8"))

    epub.write_epub(output_path, book)
