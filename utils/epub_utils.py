from ebooklib import epub
from bs4 import BeautifulSoup

def extract_epub_text(epub_path):
    book = epub.read_epub(epub_path)
    all_text = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.content, 'html.parser')
        all_text.append(soup.get_text())
    return all_text

def rebuild_epub(original_path, corrected_text, output_path):
    book = epub.read_epub(original_path)
    parts = corrected_text.split("\n\n")

    for i, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
        if i < len(parts):
            soup = BeautifulSoup(item.content, 'html.parser')
            soup.body.clear()
            soup.body.append(parts[i])
            item.content = str(soup).encode('utf-8')

    epub.write_epub(output_path, book)
