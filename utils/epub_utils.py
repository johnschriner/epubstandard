import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

def extract_epub_chunks(epub_path):
    book = epub.read_epub(epub_path)
    chunks = []

    for idx, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
        soup = BeautifulSoup(item.content, 'html.parser')
        text = soup.get_text()
        chunks.append((item.get_id(), text))

    return chunks

def rebuild_epub_from_chunks(original_path, corrected_chunks, output_path):
    book = epub.read_epub(original_path)
    corrected_map = {cid: ctext for cid, ctext in corrected_chunks}

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        cid = item.get_id()
        if cid in corrected_map:
            soup = BeautifulSoup(item.content, 'html.parser')
            soup.body.clear()

            # Split corrected text into paragraphs and add each as a <p> tag
            corrected_paragraphs = corrected_map[cid].split("\n\n")
            for para in corrected_paragraphs:
                if para.strip():
                    p_tag = soup.new_tag("p")
                    p_tag.string = para.strip()
                    soup.body.append(p_tag)

            item.content = str(soup).encode('utf-8')

    epub.write_epub(output_path, book)
