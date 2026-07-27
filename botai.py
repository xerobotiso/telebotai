import os
import io
import requests
import google.generativeai as genai
from PyPDF2 import PdfReader
from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------------------------------------------
# CONFIGURATION (Loaded from Environment Variables)
# ----------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("8855589778:AAFHaWxgmF6padGMiWA0eALJ7CW3Lg68xdo")
GEMINI_API_KEY = os.environ.get("AQ.Ab8RN6IzKCYxmAF2ninHRktKKNyKPS3j3pM2IgGIiq_4oKWC_Q")

# Initialize Gemini API
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Memory store for ongoing conversations
USER_CONVERSATIONS = {}

# ----------------------------------------------------
# COMMAND HANDLERS
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 **Your Free Personal AI Assistant is Live!**\n\n"
        "• **Chat:** Send any text message to talk.\n"
        "• **Read Docs:** Upload a `.pdf` or `.docx` file and ask questions about it.\n"
        "• **Generate PDF:** Type `/pdf <topic/content>` to create a downloadable document.\n"
        "• **Generate Images:** Type `/image <description>` for FLUX image creation.\n"
        "• **Reset Chat:** Type `/reset` to clear chat context."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_CONVERSATIONS[chat_id] = []
    await update.message.reply_text("🧹 Memory reset successfully.")

# ----------------------------------------------------
# IMAGE GENERATION (/image <prompt>)
# ----------------------------------------------------
async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("⚠️ Usage: `/image <description>`", parse_mode="Markdown")
        return

    status_msg = await update.message.reply_text("🎨 Generating image via FLUX...")

    try:
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux&nologo=true"
        
        response = requests.get(image_url, timeout=60)
        if response.status_code == 200:
            image_bytes = io.BytesIO(response.content)
            image_bytes.name = "generated_image.jpg"
            
            await update.message.reply_photo(
                photo=image_bytes, 
                caption=f"✨ Prompt: {prompt}"
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Image backend failed to respond.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error generating image: {str(e)}")

# ----------------------------------------------------
# DOCUMENT GENERATION (/pdf <prompt>)
# ----------------------------------------------------
async def generate_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("⚠️ Usage: `/pdf <topic or content for document>`", parse_mode="Markdown")
        return

    status_msg = await update.message.reply_text("📄 Writing content and generating PDF...")

    try:
        # 1. Generate text using Gemini 1.5 Flash
        response = model.generate_content(
            f"Write a comprehensive, well-structured document or report about: {prompt}. "
            "Do not include raw Markdown symbols like ### or **, just clean paragraphs."
        )
        content_text = response.text

        # 2. Convert text to PDF in-memory using ReportLab
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        
        body_style = ParagraphStyle(
            'Body',
            parent=styles['Normal'],
            fontSize=11,
            leading=15,
            spaceAfter=10
        )
        
        story = [Paragraph(f"<b>Document Topic:</b> {prompt}", styles['Title']), Spacer(1, 18)]
        
        for paragraph in content_text.split("\n\n"):
            clean_p = paragraph.replace("\n", " ").strip()
            if clean_p:
                story.append(Paragraph(clean_p, body_style))
        
        doc.build(story)
        buffer.seek(0)
        buffer.name = "Generated_Document.pdf"

        # 3. Send PDF back to Telegram
        await update.message.reply_document(
            document=buffer, 
            caption="📄 Here is your generated PDF document."
        )
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ PDF generation failed: {str(e)}")

# ----------------------------------------------------
# DOCUMENT INGESTION (Read PDF / DOCX)
# ----------------------------------------------------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    file_name = document.file_name.lower()
    status_msg = await update.message.reply_text("📥 Reading document content...")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        file_bytes = await tg_file.download_as_bytearray()
        extracted_text = ""

        if file_name.endswith('.pdf'):
            pdf_reader = PdfReader(io.BytesIO(file_bytes))
            for page in pdf_reader.pages:
                text = page.extract_text()
                if text:
                    extracted_text += text + "\n"
        
        elif file_name.endswith('.docx'):
            doc = Document(io.BytesIO(file_bytes))
            for p in doc.paragraphs:
                extracted_text += p.text + "\n"
        else:
            await status_msg.edit_text("❌ Please send a `.pdf` or `.docx` file.")
            return

        chat_id = update.effective_chat.id
        if chat_id not in USER_CONVERSATIONS:
            USER_CONVERSATIONS[chat_id] = []
            
        # Add document text into active memory
        USER_CONVERSATIONS[chat_id].append({
            "role": "user", 
            "parts": [f"I am providing this document titled '{document.file_name}'. Content:\n{extracted_text[:15000]}"]
        })
        
        await status_msg.edit_text(
            f"✅ **Loaded:** `{document.file_name}`\n\n"
            "You can now ask me any question about this file!",
            parse_mode="Markdown"
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Error reading file: {str(e)}")

# ----------------------------------------------------
# CHAT HANDLER (Gemini AI Multi-turn QA)
# ----------------------------------------------------
async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    if chat_id not in USER_CONVERSATIONS:
        USER_CONVERSATIONS[chat_id] = []

    USER_CONVERSATIONS[chat_id].append({"role": "user", "parts": [user_text]})
    
    # Keep last 10 exchanges
    if len(USER_CONVERSATIONS[chat_id]) > 20:
        USER_CONVERSATIONS[chat_id] = USER_CONVERSATIONS[chat_id][-20:]

    status_msg = await update.message.reply_text("🤔 Thinking...")

    try:
        chat = model.start_chat(history=USER_CONVERSATIONS[chat_id][:-1])
        response = chat.send_message(user_text)
        
        USER_CONVERSATIONS[chat_id].append({"role": "model", "parts": [response.text]})
        await status_msg.edit_text(response.text)
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Gemini Error: {str(e)}")

# ----------------------------------------------------
# MAIN EXECUTION
# ----------------------------------------------------
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("image", generate_image))
    app.add_handler(CommandHandler("pdf", generate_pdf))
    
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_chat))

    print("🚀 Bot is running...")
    app.run_polling()