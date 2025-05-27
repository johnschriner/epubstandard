import os
from pathlib import Path
from flask import Flask, request, render_template, send_file
from utils.epub_utils import extract_epub_chunks, rebuild_epub_from_chunks
from utils.ai_corrector import correct_chunks

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

        output_path = os.path.join(app.config['CORRECTED_FOLDER'], 'corrected_' + epub_file.filename)
        rebuild_epub_from_chunks(temp_path, corrected_chunks, output_path)
        download_filename = Path(output_path).name

        # Combine corrected text for preview
        corrected_text = "\n\n".join([text for _, text in corrected_chunks])

        return render_template('result.html', diff=diff_html, corrected_text=corrected_text, original_file=temp_path)

    return render_template('index.html')

@app.route('/download/<filename>')
def download(filename):
    return send_file(os.path.join(app.config['CORRECTED_FOLDER'], filename), as_attachment=True)

@app.route('/save', methods=['POST'])
def save():
    edited_text = request.form['edited_text']
    original_file = request.form['original_file']
    output_path = os.path.join(app.config['CORRECTED_FOLDER'], 'final_' + os.path.basename(original_file))

    from utils.epub_utils import rebuild_epub
    rebuild_epub(original_file, edited_text, output_path)

    return render_template('download.html', download_path=output_path)
