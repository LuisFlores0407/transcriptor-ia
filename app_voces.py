import os
import tempfile
import subprocess
import glob
from flask import Flask, render_template, request, send_file
from groq import Groq
from docx import Document
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024 

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/transcribe', methods=['POST'])
def transcribe():
    if 'audio_file' not in request.files:
        return "No se envió ningún archivo", 400
    
    file = request.files['audio_file']
    if file.filename == '':
        return "No seleccionaste ningún archivo", 400

    temp_dir = tempfile.gettempdir()
    original_ext = os.path.splitext(file.filename)[1]
    temp_audio_path = os.path.join(temp_dir, f"input_audio{original_ext}")
    temp_docx_path = os.path.join(temp_dir, "transcripcion_voces.docx")
    
    try:
        file.save(temp_audio_path)
        
        # 1. Picar el audio en trozos de 10 minutos con FFmpeg
        chunk_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")
        comando = [
            "ffmpeg", "-y", "-i", temp_audio_path,
            "-f", "segment", "-segment_time", "600",
            "-c:a", "libmp3lame", chunk_pattern
        ]
        subprocess.run(comando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        texto_crudo = ""
        chunks_generados = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.mp3")))
        
        # 2. Transcripción rápida con Whisper
        for chunk_path in chunks_generados:
            with open(chunk_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), audio_file.read()),
                    model="whisper-large-v3",
                    response_format="text"
                )
            texto_crudo += transcription + "\n"
            os.remove(chunk_path)
            
        # 3. Separación de hablantes mediante Llama 3
        prompt_sistema = (
            "Eres un asistente experto en transcripciones. Tu tarea es tomar el texto transcrito "
            "de una conversación y darle formato separando a los diferentes hablantes (por ejemplo: "
            "'Hablante 1:', 'Hablante 2:', o por roles si son evidentes como 'Entrevistador:', 'Entrevistado:'). "
            "Deduce los cambios de turno según la coherencia del diálogo, preguntas y respuestas. "
            "No inventes información, no resumas ni agregues comentarios introductorios. "
            "Devuelve únicamente la conversación formateada con los hablantes identificados."
        )
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto_crudo}
            ],
            model="openai/gpt-oss-120b",
            temperature=0.2,
        )
        
        texto_final = chat_completion.choices[0].message.content

        # 4. Crear el archivo Word
        doc = Document()
        doc.add_heading('Transcripción con Identificación de Hablantes', 0)
        for linea in texto_final.split('\n'):
            if linea.strip():
                doc.add_paragraph(linea)
        doc.save(temp_docx_path)
        
        return send_file(temp_docx_path, as_attachment=True, download_name="transcripcion_con_voces.docx")
        
    except Exception as e:
        return f"Ocurrió un error interno: {str(e)}", 500
        
    finally:
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)