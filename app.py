import os
from pathlib import Path
from flask import Flask, request, render_template, send_file
from utils.epub_utils import extract_epub_chunks, rebuild_epub_from_chunks
from utils.ai_corrector import correct_chunks
import threading
import time
from flask import Response, stream_with_context
from queue import Queue


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CORRECTED_FOLDER'] = 'corrected'

progress_queue = Queue()
last_output_file = None  # Track output file for redirect


os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CORRECTED_FOLDER'], exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    global last_output_file

    if request.method == 'POST':
        epub_file = request.files['file']
        if not epub_file.filename.endswith('.epub'):
            return "Only EPUB files are supported", 400

        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], epub_file.filename)
        epub_file.save(temp_path)

        def background_job():
            global last_output_file

            chunks = extract_epub_chunks(temp_path)
            total = len(chunks)

            corrected = correct_chunks(chunks)

            for i in range(len(corrected)):
                progress_queue.put(f"Processed chunk {i+1} of {total}")

            output_path = os.path.join(app.config['CORRECTED_FOLDER'], 'corrected_' + epub_file.filename)
            rebuild_epub_from_chunks(temp_path, corrected, output_path)

            last_output_file = os.path.basename(output_path)
            progress_queue.put("DONE")

        threading.Thread(target=background_job).start()

        return render_template('processing.html')

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

progress_queue = Queue()

@app.route('/progress-stream')
def progress_stream():
    def event_stream():
        while True:
            try:
                # Wait up to 5 seconds for a new message
                message = progress_queue.get(timeout=5)

                if message == 'DONE':
                    yield f"data: REDIRECT:/download/{last_output_file}\n\n"
                    return

                yield f"data: {message}\n\n"

            except:
                # If no message, send a keep-alive comment to prevent browser timeout
                yield ": keep-alive\n\n"

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream')



