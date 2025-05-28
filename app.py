# app.py
import os
import uuid
import shutil
from flask import Flask, request, render_template, send_from_directory, redirect, url_for
from werkzeug.utils import secure_filename
from utils.epub_utils import extract_epub_chunks, rebuild_epub_from_chunks
from ai_corrector import correct_chunks

UPLOAD_FOLDER = 'uploads'
CORRECTED_FOLDER = 'corrected'
ALLOWED_EXTENSIONS = {'epub'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CORRECTED_FOLDER'] = CORRECTED_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CORRECTED_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        file = request.files.get('file')
        engine = request.form.get('engine', 'ollama')

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Extract and correct
            chunks = extract_epub_chunks(file_path)
            corrected_chunks = correct_chunks(chunks, engine=engine)

            # Save corrected EPUB
            corrected_filename = f"corrected_{filename}"
            corrected_path = os.path.join(app.config['CORRECTED_FOLDER'], corrected_filename)
            rebuild_epub_from_chunks(corrected_chunks, file_path, corrected_path)

            return redirect(url_for('review', filename=corrected_filename))

    return render_template('index.html')

@app.route('/review/<filename>')
def review(filename):
    from utils.epub_utils import extract_epub_chunks
    path = os.path.join(app.config['CORRECTED_FOLDER'], filename)
    chunks = extract_epub_chunks(path)
    corrected_html = "\n\n".join(html for _, html in chunks)

    return render_template(
        'result.html',
        corrected_html=corrected_html,
        original_file=path,
        display_name=os.path.basename(filename)
    )

@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(app.config['CORRECTED_FOLDER'], filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
