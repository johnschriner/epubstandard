import os
import tempfile
from flask import Flask, request, render_template, send_file

from utils.epub_utils import extract_epub_chunks, rebuild_epub_from_chunks
from utils.ai_corrector import correct_chunks
from difflib import HtmlDiff

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CORRECTED_FOLDER'] = 'corrected'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CORRECTED_FOLDER'], exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        epub_file = request.files['file']
        if not epub_file.filename.endswith('.epub'):
            return "Only EPUB files are supported", 400

        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], epub_file.filename)
        epub_file.save(temp_path)

        chunks = extract_epub_chunks(temp_path)
        corrected_chunks = correct_chunks(chunks)

        # For now: flatten corrected chunks for diff preview
        original_text = "\n\n".join([text for _, text in chunks])
        corrected_text = "\n\n".join([text for _, text in corrected_chunks])

        diff_html = HtmlDiff().make_table(
            original_text.splitlines(),
            corrected_text.splitlines(),
            fromdesc='Original',
            todesc='Corrected',
            context=True,
            numlines=2
        )

        output_path = os.path.join(app.config['CORRECTED_FOLDER'], 'corrected_' + epub_file.filename)
        rebuild_epub_from_chunks(temp_path, corrected_chunks, output_path)

        return render_template('result.html', diff=diff_html, download_path=output_path)

    return render_template('index.html')
