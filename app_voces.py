import os
import tempfile
import subprocess
import glob
from flask import Flask, render_template, request, send_file
from groq import Groq
from docx import Document
import yt_dlp
import gdown
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from xml.sax.saxutils import escape

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/transcribir', methods=['POST'])
def transcribir():
    tipo_procesamiento = request.form.get('tipo', 'rapida')
    formato = request.form.get('formato', 'docx')
    enlace = request.form.get('enlace', '').strip()
    
    temp_dir = tempfile.gettempdir()
    ruta_archivo = None

    try:
        # --- 1: OBTENER EL ARCHIVO ---
        if enlace:
            if 'drive.google.com' in enlace:
                ruta_archivo = os.path.join(temp_dir, 'archivo_drive')
                gdown.download(enlace, ruta_archivo, quiet=True, fuzzy=True)
                if not os.path.exists(ruta_archivo):
                    return "Error descargando de Drive. ¿Está Público?", 400
            else:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(temp_dir, 'yt_audio.%(ext)s'),
                    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
                    'quiet': True
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([enlace])
                ruta_archivo = os.path.join(temp_dir, 'yt_audio.mp3')
        elif 'archivo_subido' in request.files and request.files['archivo_subido'].filename != '':
            archivo = request.files['archivo_subido']
            ruta_archivo = os.path.join(temp_dir, archivo.filename)
            archivo.save(ruta_archivo)
        else:
            return "Por favor, sube un archivo o pega un enlace.", 400

        # --- 2: CORTAR AUDIO ---
        chunk_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-i", ruta_archivo, "-f", "segment", "-segment_time", "600",
            "-c:a", "libmp3lame", chunk_pattern
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # --- 3: TRANSCRIBIR CON CRONÓMETRO (Timestamps) ---
        texto_crudo_sin = ""
        texto_crudo_con = ""
        offset = 0
        chunks_generados = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.mp3")))
        
        for chunk_path in chunks_generados:
            with open(chunk_path, "rb") as af:
                t = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), af.read()),
                    model="whisper-large-v3",
                    response_format="verbose_json"
                )
                
                # Extraer segmentos precisos
                segments = getattr(t, 'segments', [])
                if not segments and isinstance(t, dict):
                    segments = t.get('segments', [])
                    
                if segments:
                    for seg in segments:
                        inicio = seg.get('start', 0) + offset
                        minutos = int(inicio // 60)
                        segundos = int(inicio % 60)
                        texto_seg = seg.get('text', '')
                        
                        texto_crudo_con += f"[{minutos:02d}:{segundos:02d}] {texto_seg}\n"
                        texto_crudo_sin += texto_seg + " "
                else:
                    texto = getattr(t, 'text', str(t))
                    texto_crudo_con += f"[{int(offset//60):02d}:00] {texto}\n"
                    texto_crudo_sin += texto + " "
                    
            offset += 600
            os.remove(chunk_path)

        # --- 4: PROCESAR CON INTELIGENCIA ARTIFICIAL ---
        if tipo_procesamiento == 'rapida':
            texto_final = texto_crudo_sin
            titulo = 'Transcripción Rápida'
        else:
            if tipo_procesamiento == 'voces':
                prompt = "Eres un transcriptor experto. Toma el texto, que incluye marcas de tiempo, y sepáralo por hablantes. Conserva estrictamente los minutos y segundos (ej: [04:20]) al inicio de cada intervención. No resumas nada, transcribe todo."
            elif tipo_procesamiento == 'profesional':
                prompt = "Eres un asistente ejecutivo. Toma el texto con sus marcas de tiempo, sepáralo por hablantes y corrige lenguaje vulgar o coloquial pasándolo a un registro profesional. Conserva los minutos (ej: [04:20]) en cada intervención."
            elif tipo_procesamiento == 'resumen':
                prompt = "Eres un analista de negocios. Toma el texto crudo y crea un Resumen Ejecutivo estructurado. Extrae y formatea claramente: 1. Tema Principal, 2. Puntos Clave discutidos, 3. Decisiones Tomadas y 4. Próximos Pasos. Usa un estilo muy limpio."
            elif tipo_procesamiento == 'traduccion':
                prompt = "Eres un traductor experto. Este texto está en un idioma extranjero e incluye marcas de tiempo. Traduce absolutamente todo al Español Latino. Separa a los diferentes hablantes y conserva las marcas de tiempo (ej: [01:15]) en las intervenciones."
            
            # El resumen procesa mejor sin los números de los minutos distrayéndolo
            texto_base = texto_crudo_sin if tipo_procesamiento == 'resumen' else texto_crudo_con

            chat_completion = client.chat.completions.create(
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": texto_base}],
                model="openai/gpt-oss-120b",
                temperature=0.2,
            )
            texto_final = chat_completion.choices[0].message.content
            titulo = f'Documento - Modo {tipo_procesamiento.capitalize()}'

        # --- 5: DESCARGAR (WORD O PDF) ---
        if formato == 'pdf':
            temp_path = os.path.join(temp_dir, f"resultado_{tipo_procesamiento}.pdf")
            pdf = SimpleDocTemplate(temp_path, pagesize=letter)
            estilos = getSampleStyleSheet()
            historia = [Paragraph(titulo, estilos['Heading1']), Spacer(1, 12)]
            
            for linea in texto_final.split('\n'):
                if linea.strip():
                    # Escapamos caracteres para que el PDF no falle con emojis o símbolos raros
                    linea_limpia = escape(linea.encode('latin-1', 'replace').decode('latin-1'))
                    historia.append(Paragraph(linea_limpia, estilos['Normal']))
                    historia.append(Spacer(1, 6))
            
            pdf.build(historia)
            return send_file(temp_path, as_attachment=True)
            
        else:
            temp_path = os.path.join(temp_dir, f"resultado_{tipo_procesamiento}.docx")
            doc = Document()
            doc.add_heading(titulo, 0)
            for linea in texto_final.split('\n'):
                if linea.strip():
                    doc.add_paragraph(linea)
            doc.save(temp_path)
            return send_file(temp_path, as_attachment=True)

    except Exception as e:
        return f"Error procesando: {str(e)}", 500

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)
