import os
from openai import OpenAI

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def correct_text_chunk(chunk_text):
    """
    Sends a single chunk of text to GPT-4 for OCR cleanup.
    Limits to 2000 characters per prompt to stay within token limits.
    """
    prompt = (
        "This is a passage extracted from OCR. "
        "Correct common OCR issues like joined or broken characters, incorrect spellings, and odd line breaks. "
        "Preserve proper nouns and stylistic elements. Return a cleaned version of the text.\n\n"
        f"{chunk_text[:2000]}"
    )

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return response.choices[0].message.content.strip()

def correct_chunks(chunks):
    """
    Takes a list of (id, html_text) chunks.
    Returns a list of (id, corrected_html_text) chunks.
    """
    corrected = []
    for cid, text in chunks:
        try:
            fixed = correct_text_chunk(text)
            corrected.append((cid, fixed))
        except Exception as e:
            print(f"[ERROR] Failed to correct chunk {cid}: {e}")
            corrected.append((cid, text))  # fallback to original
    return corrected
