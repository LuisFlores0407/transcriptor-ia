import os
import tempfile
import subprocess
import glob
from flask import Flask, render_template, request, send_file
from groq import Groq
from docx import Document

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/transcribir', methods=['POST'])
def transcribir():
    if 'audio' not in request.files:
        return "No hay archivo", 400
        
    archivo = request.files['audio']
    tipo_procesamiento = request.form.get('tipo', 'rapida')
    
    if archivo.filename == '':
        return "Archivo vacío", 400

    temp_dir = tempfile.gettempdir()
    ruta_audio = os.path.join(temp_dir, archivo.filename)
    archivo.save(ruta_audio)

    try:
        # --- PASO 1: PICAR EL AUDIO SIEMPRE (Evita el Error 413) ---
        chunk_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-i", ruta_audio,
            "-f", "segment", "-segment_time", "600",
            "-c:a", "libmp3lame", chunk_pattern
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # --- PASO 2: OBTENER EL TEXTO BASE DE TODOS LOS PEDAZOS ---
        texto_crudo = ""
        chunks_generados = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.mp3")))
        for chunk_path in chunks_generados:
            with open(chunk_path, "rb") as af:
                t = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), af.read()),
                    model="whisper-large-v3",
                    response_format="text"
                )
            texto_crudo += t + "\n"
            os.remove(chunk_path)

        # --- PASO 3: ENTREGAR SEGÚN EL MODO ELEGIDO ---
        if tipo_procesamiento == 'rapida':
            ruta_txt = os.path.join(temp_dir, "transcripcion_rapida.txt")
            with open(ruta_txt, "w", encoding="utf-8") as f:
                f.write(texto_crudo)
            return send_file(ruta_txt, as_attachment=True)
            
        else:
            prompt_sistema = (
                "Eres un asistente experto. Toma el texto y dale formato separando a los diferentes hablantes. "
                "Deduce los cambios de turno. Devuelve únicamente la conversación formateada."
            )
            chat_completion = client.chat.completions.create(
                messages=[{"role": "system", "content": prompt_sistema}, {"role": "user", "content": texto_crudo}],
                model="openai/gpt-oss-120b",
                temperature=0.2,
            )
            
            temp_docx_path = os.path.join(temp_dir, "transcripcion_voces.docx")
            doc = Document()
            doc.add_heading('Transcripción con Voces', 0)
            for linea in chat_completion.choices[0].message.content.split('\n'):
                if linea.strip():
                    doc.add_paragraph(linea)
            doc.save(temp_docx_path)
            
            return send_file(temp_docx_path, as_attachment=True)

    except Exception as e:
        return f"Error procesando el audio: {str(e)}", 500

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)
