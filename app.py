import os
import tempfile
from flask import Flask, request, render_template, send_file
from utils.epub_utils import extract_epub_text, rebuild_epub
from utils.ai_corrector import correct_text
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

        original_texts = extract_epub_text(temp_path)
        full_text = "\n\n".join(original_texts)

        corrected_text = correct_text(full_text)

        # Side-by-side diff
        diff_html = HtmlDiff().make_table(
            full_text.splitlines(),
            corrected_text.splitlines(),
            fromdesc='Original',
            todesc='Corrected',
            context=True,
            numlines=2
        )

        # Save new epub
        output_path = os.path.join(app.config['CORRECTED_FOLDER'], 'corrected_' + epub_file.filename)
        rebuild_epub(temp_path, corrected_text, output_path)

        return render_template('result.html', diff=diff_html, download_path=output_path)

    return render_template('index.html')

@app.route('/download/<filename>')
def download(filename):
    return send_file(os.path.join(app.config['CORRECTED_FOLDER'], filename), as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
