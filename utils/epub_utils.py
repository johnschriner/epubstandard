from ebooklib import epub
from bs4 import BeautifulSoup
import ebooklib

def extract_epub_text(epub_path):
    """
    Extract full HTML blocks from all visible XHTML documents.
    Returns a list of cleaned HTML strings (one per document).
    """
    book = epub.read_epub(epub_path)
    html_blocks = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.content, 'html.parser')

        # Remove non-visible elements
        for tag in soup(['nav', 'header', 'footer', 'script', 'style']):
            tag.decompose()

        body = soup.find('body')
        if body and body.text.strip():
            # Keep HTML content (not just .get_text())
            cleaned_body = ''.join(str(x) for x in body.contents).strip()
            html_blocks.append(cleaned_body)

    return html_blocks


def rebuild_epub(original_path, corrected_blocks, output_path):
    """
    Rebuild EPUB by inserting corrected HTML blocks back into their respective sections.
    `corrected_blocks` is a list of raw HTML fragments (one per section).
    """
    book = epub.read_epub(original_path)
    doc_items = [item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)]

    for i, item in enumerate(doc_items):
        if i >= len(corrected_blocks):
            break

        soup = BeautifulSoup(item.content, 'html.parser')
        body = soup.find('body')
        if not body:
            continue

        body.clear()
        new_content = BeautifulSoup(corrected_blocks[i], 'html.parser')

        for elem in new_content.contents:
            body.append(elem)

        item.content = str(soup).encode('utf-8')

    epub.write_epub(output_path, book)
