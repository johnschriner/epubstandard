from ebooklib import epub
from bs4 import BeautifulSoup
import ebooklib

def extract_epub_chunks(epub_path):
    """
    Extracts XHTML chunks from the EPUB.
    Returns a list of tuples: (item_id, cleaned HTML from <body>)
    """
    book = epub.read_epub(epub_path)
    chunks = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.content, 'html.parser')
        for tag in soup(['nav', 'header', 'footer', 'script', 'style']):
            tag.decompose()

        body = soup.find('body')
        if body and body.get_text(strip=True):
            cleaned_html = ''.join(str(x) for x in body.contents).strip()
            chunks.append((item.get_id(), cleaned_html))

    return chunks

def rebuild_epub_from_chunks(original_path, corrected_chunks, output_path):
    """
    Rebuilds an EPUB from original, using corrected (id, html) content pairs.
    Only modifies document items with matching IDs.
    """
    book = epub.read_epub(original_path)
    chunk_map = {id_: html for id_, html in corrected_chunks}

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        item_id = item.get_id()
        if item_id not in chunk_map:
            continue

        soup = BeautifulSoup(item.content, 'html.parser')
        body = soup.find('body')
        if not body:
            continue

        body.clear()
        new_content = BeautifulSoup(chunk_map[item_id], 'html.parser')
        for elem in new_content.contents:
            body.append(elem)

        item.content = str(soup).encode('utf-8')

    epub.write_epub(output_path, book)

def rebuild_epub_from_html(edited_html, original_path, output_path):
    """
    Replaces all document bodies with the single edited HTML block.
    """
    book = epub.read_epub(original_path)
    html_soup = BeautifulSoup(edited_html, 'html.parser')

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.content, 'html.parser')
        body = soup.find('body')
        if not body:
            continue
        body.clear()
        for elem in html_soup.contents:
            body.append(elem)
        item.content = str(soup).encode('utf-8')

    epub.write_epub(output_path, book)
