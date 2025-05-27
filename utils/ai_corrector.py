import os
from openai import OpenAI

def correct_text_chunk(chunk_text):
    """
    Sends a single chunk of text to GPT-4 for OCR cleanup.
    Initializes OpenAI client locally to avoid thread/fork issues.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = (
        "This is a passage extracted from OCR. "
        "Correct common OCR issues like joined or broken characters, incorrect spellings, and odd line breaks. "
        "Preserve proper nouns and stylistic elements. Return a cleaned version of the text.\n\n"
        f"{chunk_text}"
    )

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return response.choices[0].message.content.strip()

def correct_chunks(chunks, max_length=2000):
    """
    Takes a list of (id, html_text) chunks.
    If a chunk is too long, splits it into subchunks of ~2000 characters.
    Returns a list of (id, corrected_html_text) chunks.
    """
    corrected = []

    for cid, text in chunks:
        if len(text) > max_length:
            print(f"[WARN] Chunk {cid} is too long ({len(text)} chars), splitting…")
            subchunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]
            fixed_parts = []

            for i, part in enumerate(subchunks):
                try:
                    fixed = correct_text_chunk(part)
                    fixed_parts.append(fixed)
                except Exception as e:
                    print(f"[ERROR] Subchunk {i} of {cid}: {e}")
                    fixed_parts.append(part)  # fallback to original

            corrected_text = "\n".join(fixed_parts)
        else:
            try:
                corrected_text = correct_text_chunk(text)
            except Exception as e:
                print(f"[ERROR] Failed to correct chunk {cid}: {e}")
                corrected_text = text  # fallback to original

        corrected.append((cid, corrected_text))

    return corrected
