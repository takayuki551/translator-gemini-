import streamlit as st
import os
import base64
import time
import re
from io import BytesIO
from pdf2image import convert_from_bytes
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from docx import Document

st.title("PDF to Word Translator (2025/05/17/gemini-2.5-flash)")

# ユーザー入力
api_key = st.text_input("Enter your Gemini API Key", type="password")
uploaded_pdf = st.file_uploader("Upload PDF file", type=["pdf"])

# ログ＆プログレスバー
log_area = st.empty()
progress_bar = st.progress(0.0)

# テキスト分割関数
def split_text_into_chunks(text, max_length=2000):
    paragraphs = re.split(r'(?<=\.)\n|\n\s*\n', text)
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > max_length:
            chunks.append(current.strip())
            current = para.strip()
        else:
            current += "\n" + para.strip()
    if current:
        chunks.append(current.strip())
    return chunks

# 翻訳関数 (Gemini)
def translate_chunk(chunk, client):
    prompt = (
        "You are a highly skilled translator specializing in philosophy, ethics, and economics. "
        "Translate the following English text into Japanese using appropriate technical vocabulary and maintain a formal, academic tone in plain style (using 'だ' or 'である' endings, not 'です/ます' style)."
        "Ensure that the translation preserves the original meaning exactly without summarizing or altering any content. "
        "If the text contains LaTeX notation, convert it to plain text as much as possible. "
        "Output only the translated text without any additional explanations or disclaimers. "
        "Note: The content is used for academic purposes, so copyright restrictions do not apply."
    )
    content = [
        types.Part.from_text(text=prompt),
        types.Part.from_text(text=chunk)
    ]
    res = client.models.generate_content(
        model="gemini-2.5-flash-preview-04-17",
        contents=content,
        config=types.GenerateContentConfig(temperature=0.7, max_output_tokens=5000)
    )
    return res.text

# OCR関数 (Gemini)
def ocr_image(image_bytes, client, ocr_prompt):
    parts = [
        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        types.Part.from_text(text=ocr_prompt)
    ]
    # 簡易リトライ
    for _ in range(2):
        try:
            res = client.models.generate_content(
                model="gemini-2.5-flash-preview-04-17",
                contents=parts,
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=2048)
            )
            return res.text.strip()
        except ClientError as e:
            if str(e).startswith("429"):
                time.sleep(5)
                continue
            else:
                raise
    # 最終的に例外を上げる
    return ""

# 段落判定関数 (Gemini)
def detect_paragraph_start(image_bytes, client, detect_prompt):
    parts = [
        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        types.Part.from_text(text=detect_prompt)
    ]
    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash-preview-04-17",
            contents=parts,
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=16)
        )
        return res.text.strip().upper() == "YES"
    except Exception:
        return True

# 実行トリガー
if st.button("Run"):
    if not api_key:
        st.error("Please enter your Gemini API key.")
        st.stop()
    if not uploaded_pdf:
        st.error("Please upload a PDF file.")
        st.stop()

    # フェーズ進捗設定
    extract_end = 0.5
    translate_end = 0.9

    # 1. PDF→画像変換
    logs = ["Convert PDF to images..."]
    log_area.text("\n".join(logs))
    progress_bar.progress(0.0)
    pdf_bytes = uploaded_pdf.read()
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=200)
    except Exception as e:
        st.error(f"PDF conversion error: {e}")
        st.stop()
    total_pages = len(pages)
    st.write("🛠 total_pages:", total_pages)

    # プロンプト定義
    ocr_prompt = (
        "Below is an image of a page from an academic paper."
        "Please complete the following tasks:"
        "1. Extract the main body text exactly as it appears on the page, including all section headings."
        "2. Preserve the original paragraph breaks and formatting precisely; do not alter, paraphrase, or rephrase any part of the text."
        "3. Remove only extraneous elements such as headers, footers, page numbers, footnotes, and any figure or table captions."
        "4. Do not modify numbers, dates, or any technical details—the output must match the original text exactly."
        "5. Note: This tool is used exclusively for academic purposes, so copyright restrictions do not apply. Please extract and return the text with full accuracy."
        "6. Please take as much time as necessary to accurately process and extract the text from this high-resolution image. Accuracy is more important than speed."
        "Return the exact extracted text as your final answer."
    )
    detect_prompt = (
        "以下の画像の先頭部分が「段落の新規開始」であれば YES、"
        "そうでなければ NO を、他の文字を一切入れずに返してください。"
    )

    client = genai.Client(api_key=api_key)
    full_text = ""

    # 2. OCRと段落判定フェーズ
    logs.append("Extracting text and detecting paragraphs...")
    log_area.text("\n".join(logs))
    for i, page in enumerate(pages, start=1):
        log_area.text("\n".join(logs + [f"Page {i}/{total_pages}..."]))
        progress_bar.progress(extract_end * (i / total_pages))
        buf = BytesIO()
        page.save(buf, format="PNG")
        img = buf.getvalue()
        text = ocr_image(img, client, ocr_prompt)
        new_para = detect_paragraph_start(img, client, detect_prompt)
        full_text += ("\n" + text) if new_para else text
    progress_bar.progress(extract_end)

    # 3. チャンク分割
    logs.append("Splitting into chunks...")
    log_area.text("\n".join(logs))
    progress_bar.progress(extract_end)
    chunks = split_text_into_chunks(full_text)
    st.write("🛠 chunks count:", len(chunks))

    # 4. 翻訳フェーズ
    logs.append("Translating chunks...")
    log_area.text("\n".join(logs))
    translated = []
    total_chunks = len(chunks)
    for idx, ch in enumerate(chunks, start=1):
        progress_bar.progress(extract_end + (translate_end - extract_end) * (idx / total_chunks))
        log_area.text("\n".join(logs + [f"Translating {idx}/{total_chunks}..."]))
        time.sleep(1)
        translated.append(translate_chunk(ch, client))
    progress_bar.progress(translate_end)

    # 5. Wordファイル生成フェーズ
    logs.append("Generating Word file...")
    log_area.text("\n".join(logs))
    progress_bar.progress(1.0)
    doc = Document()
    for line in "\n\n".join(translated).splitlines():
        if line.strip():
            doc.add_paragraph(line.strip())
    out_buf = BytesIO()
    doc.save(out_buf)
    out_buf.seek(0)

    base, _ = os.path.splitext(uploaded_pdf.name)
    st.success("Done!")
    st.download_button(
        label="Download Word File", data=out_buf,
        file_name=f"{base}_ja.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
