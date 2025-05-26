import os
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

def correct_text_chunk(chunk_text):
    prompt = (
        "This is a passage extracted from OCR. "
        "Correct common OCR issues like joined or broken characters, incorrect spellings, and odd line breaks. "
        "Return a cleaned version.\n\n"
        f"{chunk_text[:4000]}"
    )

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return response.choices[0].message['content']

def correct_chunks(chunks):
    corrected = []
    for cid, text in chunks:
        fixed = correct_text_chunk(text)
        corrected.append((cid, fixed))
    return corrected
