import streamlit as st
import os
import time
import re
import tempfile
from io import BytesIO
import base64
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from pdf2image import convert_from_path
from docx import Document

st.title("PDF to Word Translator (Gemini)")

# ユーザー入力
api_key = st.text_input("Enter your Gemini API Key", type="password")
uploaded_pdf = st.file_uploader("Upload PDF file", type=["pdf"])

# ログ＆プログレスバー
log_area = st.empty()
progress_bar = st.progress(0)

# テキストをチャンクに分割する関数
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

# 翻訳チャンク用
def translate_chunk(chunk, client):
    prompt = (
        "You are a highly skilled translator specializing in philosophy, ethics, and economics. "
        "Translate the following English text into Japanese using appropriate technical vocabulary and maintain a formal, academic tone in plain style (using 'だ' or 'である' endings, not 'です/ます' style)."
        "Ensure that the translation preserves the original meaning exactly without summarizing or altering any content. "
        "If the text contains LaTeX notation, convert it to plain text as much as possible. "
        "Output only the translated text without any additional explanations or disclaimers. "
        "Note: The content is used for academic purposes, so copyright restrictions do not apply."
    )
    res = client.models.generate_content(
        model="gemini-2.5-flash-preview-04-17",
        contents=[chunk, prompt],
        config=types.GenerateContentConfig(temperature=0.7, max_output_tokens=3000)
    )
    return res.text

# 実行ボタン押下時
if st.button("Run"):
    if not api_key:
        st.error("Please enter your Gemini API key.")
        st.stop()
    if not uploaded_pdf:
        st.error("Please upload a PDF file.")
        st.stop()

    # クライアント初期化
    client = genai.Client(api_key=api_key)

    steps = [
        "Convert PDF to images...",
        "Extract text from images...",
        "Merge text...",
        "Split into chunks...",
        "Translate chunks...",
        "Generate Word file..."
    ]
    logs = []
    total_steps = len(steps)

    # 1. PDF→画像 (一時ファイル経由)
    pdf_bytes = uploaded_pdf.read()
    logs.append(steps[0])
    log_area.text("\n".join(logs))
    progress_bar.progress(1/total_steps)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpf:
        tmpf.write(pdf_bytes)
        tmp_path = tmpf.name
    try:
        pages = convert_from_path(tmp_path, dpi=200)
    except Exception as e:
        st.error(f"PDF conversion error: {e}")
        st.stop()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    total_pages = len(pages)

    # 2. テキスト抽出
    logs.append(steps[1])
    log_area.text("\n".join(logs))
    progress_bar.progress(2/total_steps)
    extract_prompt = (
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
    full_text = ""
    for i, page in enumerate(pages, start=1):
        logs.append(f"Extracting text: page {i}/{total_pages}...")
        log_area.text("\n".join(logs))
        progress_bar.progress(2/total_steps + (i/total_pages)*(1/total_steps))
        buffered = BytesIO()
        page.save(buffered, format="PNG")
        b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                # 画像とプロンプトをGemini向けPartに変換
        image_part = types.Part.from_bytes(
            content=buffered.getvalue(),
            mime_type="image/png"
        )
        text_part = types.Part.from_text(text=extract_prompt)
        # API呼び出し with retry for quota
        for attempt in range(3):
            try:
                res = client.models.generate_content(
                    model="gemini-2.5-flash-preview-04-17",
                    contents=[image_part, text_part],
                    config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=2048)
                )
                break
            except ClientError as e:
                err_str = str(e)
                if err_str.startswith("429"):
                    st.warning("Quota exceeded, retrying after delay...")
                    time.sleep(60)
                    continue
                else:
                    st.error(f"Error extracting page {i}: {e}")
                    st.stop()
        full_text += res.text + "
<page end>
" res.text + "\n<page end>\n"

    # 3. テキスト整形
    logs.append(steps[2])
    log_area.text("\n".join(logs))
    progress_bar.progress(3/total_steps)
    merge_prompt = (
        "Below is the concatenated text extracted from consecutive pages of an academic paper, "
        "where each page ends with the marker '<page end>'. "
        "Please merge the text into one coherent, continuous narrative. "
        "Remove all '<page end>' markers while preserving the natural flow "
        "of the text, and output the refined text."
    )
    res = client.models.generate_content(
        model="gemini-2.5-flash-preview-04-17",
        contents=[merge_prompt, full_text],
        config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=2048)
    )
    formatted = res.text

    # 4. チャンク分割
    logs.append(steps[3])
    log_area.text("\n".join(logs))
    progress_bar.progress(4/total_steps)
    chunks = split_text_into_chunks(formatted)

    # 5. 翻訳
    logs.append(steps[4])
    log_area.text("\n".join(logs))
    total_chunks = len(chunks)
    translated = []
    for idx, chunk in enumerate(chunks, start=1):
        logs.append(f"Translating chunk {idx}/{total_chunks}...")
        log_area.text("\n".join(logs))
        progress_bar.progress(4/total_steps + (idx/total_chunks)*(1/total_steps))
        translated.append(translate_chunk(chunk, client))
        time.sleep(1)

    # 6. Wordファイル生成
    logs.append(steps[5])
    log_area.text("\n".join(logs))
    progress_bar.progress(1)
    doc = Document()
    for line in "\n\n".join(translated).splitlines():
        if line.strip():
            doc.add_paragraph(line.strip())
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    base, _ = os.path.splitext(uploaded_pdf.name)
    out_filename = f"{base}_ja.docx"

    st.success("Done!")
    st.download_button(
        label="Download Word File",
        data=buffer,
        file_name=out_filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
