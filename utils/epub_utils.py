import os
from ebooklib import epub
from bs4 import BeautifulSoup
from tqdm import tqdm


def extract_html_chunks(epub_path, max_chunk_chars=6000):
    book = epub.read_epub(epub_path)
    chunks = []
    chunk_id = 0

    for item in book.get_items():
        if item.get_type() == epub.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            current_chunk = ""
            for tag in soup.find_all(["p", "div", "span", "h1", "h2", "h3", "h4", "li"]):
                text = tag.get_text(strip=True)
                if not text:
                    continue
                if len(current_chunk) + len(text) > max_chunk_chars:
                    if current_chunk:
                        chunks.append((f"id{chunk_id}", current_chunk))
                        chunk_id += 1
                        current_chunk = ""
                current_chunk += text + " "
            if current_chunk:
                chunks.append((f"id{chunk_id}", current_chunk))
                chunk_id += 1

    return chunks


def rebuild_epub_from_chunks(original_path, corrected_chunks, output_path):
    book = epub.read_epub(original_path)
    chunk_map = {f"id{i}": html for i, html in enumerate(corrected_chunks)}

    for item in book.get_items():
        if item.get_type() == epub.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            output_html = ""
            for i, (chunk_id, chunk_text) in enumerate(chunk_map.items()):
                output_html += f"<div>{chunk_text}</div>\n"
            item.set_content(output_html.encode("utf-8"))

    epub.write_epub(output_path, book)


def save_chunks_as_html(chunks, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("<html><head><title>Corrected EPUB</title></head><body>\n")
        for chunk in chunks:
            f.write(f"<div class='editable' contenteditable='true'>{chunk}</div><hr>\n")
        f.write("</body></html>")
