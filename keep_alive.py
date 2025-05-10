from flask import Flask, send_file
from threading import Thread
import os

# --- Flask keep-alive ---
app = Flask('')

@app.route('/')
def home():
	return f"""
	Himari-chan: I'm ready ^^
	<br>
	<br>
	Build info: Himari version 4 (0.0.4) Production GitHub Codepaces version (Limited runtime!)
	"""

@app.route('/download/ffmpeg')
def download_ffmpeg():
	ffmpeg_path = os.path.join(os.getcwd(), 'ffmpeg')
	if not os.path.exists(ffmpeg_path):
		return "ffmpeg binary not found", 404
	return send_file(ffmpeg_path, as_attachment=True)

def run():
	app.run(host='0.0.0.0', port=8080)

def keep_alive():
	t = Thread(target=run)
	t.start()