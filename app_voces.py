import os
import tempfile
import subprocess
import glob
import threading
import uuid
from flask import Flask, render_template, request, send_file, jsonify
from groq import Groq
from docx import Document
import gdown
from pytubefix import YouTube
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from xml.sax.saxutils import escape

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

ESTADOS_TAREAS = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/iniciar_transcripcion', methods=['POST'])
def iniciar_transcripcion():
    task_id = str(uuid.uuid4())
    ESTADOS_TAREAS[task_id] = {'estado': 'Iniciando...', 'progreso': 5, 'archivo_listo': None}

    tipo_procesamiento = request.form.get('tipo', 'rapida')
    formato = request.form.get('formato', 'docx')
    enlace = request.form.get('enlace', '').strip()

    temp_dir = tempfile.gettempdir()
    ruta_archivo = ""

    try:
        if enlace:
            ruta_archivo = enlace
        elif 'archivo_subido' in request.files and request.files['archivo_subido'].filename != '':
            archivo = request.files['archivo_subido']
            ruta_archivo = os.path.join(temp_dir, f"input_{task_id}_{archivo.filename}")
            archivo.save(ruta_archivo)
        else:
            return jsonify({'error': 'No se recibió ningún archivo o enlace.'}), 400

        hilo = threading.Thread(target=procesar_en_fondo, args=(task_id, ruta_archivo, tipo_procesamiento, formato))
        hilo.start()

        return jsonify({'task_id': task_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/estado/<task_id>', methods=['GET'])
def chequear_estado(task_id):
    if task_id in ESTADOS_TAREAS:
        return jsonify(ESTADOS_TAREAS[task_id])
    return jsonify({'error': 'Tarea no encontrada'}), 404

@app.route('/descargar/<task_id>', methods=['GET'])
def descargar_archivo(task_id):
    tarea = ESTADOS_TAREAS.get(task_id)
    if tarea and tarea.get('progreso') == 100:
        return send_file(tarea['archivo_listo'], as_attachment=True)
    return "El archivo no está listo o hubo un error", 400

def procesar_en_fondo(task_id, ruta_original, tipo_procesamiento, formato):
    try:
        temp_dir = tempfile.gettempdir()
        ruta_audio = ruta_original

        # PASO 1: DRIVE O YOUTUBE
        if ruta_original.startswith('http'):
            if 'drive.google.com' in ruta_original:
                ESTADOS_TAREAS[task_id]['estado'] = 'Descargando de Google Drive...'
                ESTADOS_TAREAS[task_id]['progreso'] = 10
                ruta_descarga = os.path.join(temp_dir, f"drive_{task_id}")
                gdown.download(ruta_original, ruta_descarga, quiet=True)
                if not os.path.exists(ruta_descarga):
                    raise Exception("Error en Drive. ¿Está en 'Cualquier persona con el enlace'?")
                ruta_audio = ruta_descarga
            elif 'youtube.com' in ruta_original or 'youtu.be' in ruta_original:
                ESTADOS_TAREAS[task_id]['estado'] = 'Evadiendo seguridad de YouTube...'
                ESTADOS_TAREAS[task_id]['progreso'] = 10
                # Disfrazamos la petición como si viniera de YouTube Music en Android
                yt = YouTube(ruta_original, client='ANDROID_MUSIC')
                audio_stream = yt.streams.filter(only_audio=True).first()
                if not audio_stream:
                    raise Exception("No se pudo extraer el audio de YouTube.")
                ruta_descarga = audio_stream.download(output_path=temp_dir, filename=f"yt_{task_id}.mp4")
                ruta_audio = ruta_descarga
            else:
                raise Exception("Enlace no soportado. Usa YouTube o Google Drive.")

        # PASO 2: CORTAR
        ESTADOS_TAREAS[task_id]['estado'] = 'Optimizando formato del audio...'
        ESTADOS_TAREAS[task_id]['progreso'] = 20
        chunk_pattern = os.path.join(temp_dir, f"chunk_{task_id}_%03d.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-i", ruta_audio, "-f", "segment", "-segment_time", "600",
            "-c:a", "libmp3lame", chunk_pattern
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # PASO 3: TRANSCRIBIR
        chunks_generados = sorted(glob.glob(os.path.join(temp_dir, f"chunk_{task_id}_*.mp3")))
        total_chunks = len(chunks_generados)
        
        if total_chunks == 0:
            raise Exception("No se pudo extraer el audio del archivo aportado.")

        texto_crudo_sin = ""
        texto_crudo_con = ""
        offset = 0

        for i, chunk_path in enumerate(chunks_generados):
            ESTADOS_TAREAS[task_id]['estado'] = f'Transcribiendo bloque {i+1} de {total_chunks}...'
            ESTADOS_TAREAS[task_id]['progreso'] = 20 + int(40 * (i / total_chunks)) 
            
            with open(chunk_path, "rb") as af:
                t = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), af.read()),
                    model="whisper-large-v3",
                    response_format="verbose_json"
                )
                
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

        # PASO 4: IA AVANZADA
        if tipo_procesamiento == 'rapida':
            ESTADOS_TAREAS[task_id]['estado'] = 'Unificando textos...'
            ESTADOS_TAREAS[task_id]['progreso'] = 80
            texto_final = texto_crudo_sin
            titulo = 'Transcripción Rápida'
        else:
            ESTADOS_TAREAS[task_id]['estado'] = 'Pensando... Aplicando Inteligencia Artificial...'
            ESTADOS_TAREAS[task_id]['progreso'] = 75
            
            if tipo_procesamiento == 'voces':
                prompt = "Eres un transcriptor experto. Toma el texto, que incluye marcas de tiempo, y sepáralo por hablantes. Conserva estrictamente los minutos y segundos al inicio de cada intervención. No resumas."
            elif tipo_procesamiento == 'profesional':
                prompt = "Eres un asistente ejecutivo. Toma el texto con sus marcas de tiempo, sepáralo por hablantes y corrige lenguaje vulgar o coloquial pasándolo a un registro formal. Conserva los minutos."
            elif tipo_procesamiento == 'resumen':
                prompt = "Eres un analista de negocios. Toma el texto crudo y crea un Resumen Ejecutivo estructurado. Extrae: 1. Tema Principal, 2. Puntos Clave, 3. Decisiones, 4. Próximos Pasos."
            elif tipo_procesamiento == 'traduccion':
                prompt = "Eres un traductor experto. Traduce todo al Español Latino. Separa a los diferentes hablantes y conserva las marcas de tiempo en las intervenciones."
            
            texto_base = texto_crudo_sin if tipo_procesamiento == 'resumen' else texto_crudo_con

            chat_completion = client.chat.completions.create(
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": texto_base}],
                model="openai/gpt-oss-120b",
                temperature=0.2,
            )
            texto_final = chat_completion.choices[0].message.content
            titulo = f'Documento - Modo {tipo_procesamiento.capitalize()}'

        # PASO 5: EXPORTAR DOCUMENTO
        ESTADOS_TAREAS[task_id]['estado'] = 'Generando archivo final...'
        ESTADOS_TAREAS[task_id]['progreso'] = 95
        
        if formato == 'pdf':
            temp_path = os.path.join(temp_dir, f"resultado_{task_id}.pdf")
            pdf = SimpleDocTemplate(temp_path, pagesize=letter)
            estilos = getSampleStyleSheet()
            historia = [Paragraph(titulo, estilos['Heading1']), Spacer(1, 12)]
            
            for linea in texto_final.split('\n'):
                if linea.strip():
                    linea_limpia = escape(linea.encode('latin-1', 'replace').decode('latin-1'))
                    historia.append(Paragraph(linea_limpia, estilos['Normal']))
                    historia.append(Spacer(1, 6))
            pdf.build(historia)
        else:
            temp_path = os.path.join(temp_dir, f"resultado_{task_id}.docx")
            doc = Document()
            doc.add_heading(titulo, 0)
            for linea in texto_final.split('\n'):
                if linea.strip():
                    doc.add_paragraph(linea)
            doc.save(temp_path)

        # FINALIZAR
        ESTADOS_TAREAS[task_id]['archivo_listo'] = temp_path
        ESTADOS_TAREAS[task_id]['estado'] = '¡Proceso completado con éxito!'
        ESTADOS_TAREAS[task_id]['progreso'] = 100

    except Exception as e:
        ESTADOS_TAREAS[task_id]['estado'] = str(e)
        ESTADOS_TAREAS[task_id]['progreso'] = -1

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)
