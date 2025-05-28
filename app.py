import os
from flask import Flask, request, render_template, redirect, url_for, session
from werkzeug.utils import secure_filename
from utils.epub_utils import extract_epub_chunks, rebuild_epub_from_chunks
from ai_corrector import correct_chunks
from uuid import uuid4

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", str(uuid4()))
UPLOAD_FOLDER = "uploads"
CORRECTED_FOLDER = "corrected"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CORRECTED_FOLDER, exist_ok=True)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file = request.files["epub_file"]
        engine = request.form.get("engine", "ollama")
        if not file:
            return "No file uploaded", 400

        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(file_path)

        # Extract and store chunks
        chunks = extract_epub_chunks(file_path)
        session["chunks"] = chunks  # store for review later

        # Correct the chunks
        corrected_chunks = correct_chunks(chunks, engine=engine)
        if not corrected_chunks:
            return render_template("error.html", message="Correction failed or returned no output. Check logs.")

        # Save corrected epub
        corrected_filename = f"corrected_{filename}"
        corrected_path = os.path.join(CORRECTED_FOLDER, corrected_filename)

        try:
            rebuild_epub_from_chunks(file_path, corrected_chunks, corrected_path)
        except Exception as e:
            return render_template("error.html", message=f"Error rebuilding EPUB: {e}")

        return redirect(url_for("review", filename=corrected_filename))

    return render_template("index.html")

@app.route("/review/<filename>")
def review(filename):
    chunks = session.get("chunks", [])
    if not chunks:
        return render_template("error.html", message="No corrected chunks found in session.")

    try:
        corrected_html = "\n\n".join(html for _, html in chunks)
    except Exception as e:
        return render_template("error.html", message=f"Error rendering HTML: {e}")

    return render_template("result.html", corrected_html=corrected_html, filename=filename)

@app.route("/save", methods=["POST"])
def save():
    filename = request.form.get("filename")
    updated_text = request.form.get("corrected_html")
    if not filename or not updated_text:
        return "Missing data", 400

    output_path = os.path.join(CORRECTED_FOLDER, filename)

    try:
        # TODO: Future implementation: re-embed corrected HTML back into EPUB
        with open(output_path + ".txt", "w", encoding="utf-8") as f:
            f.write(updated_text)
        return "Saved successfully. EPUB output not yet regenerated."
    except Exception as e:
        return f"Save failed: {e}", 500

if __name__ == "__main__":
    app.run(debug=True)
