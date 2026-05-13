import subprocess
import glob
import os
import tempfile
from flask import Flask, Response, request, jsonify
import yt_dlp
import sys

app = Flask(__name__)

# --- API Endpoint for Caption ---
@app.route('/info')
def get_info_endpoint():
    tiktok_url = request.args.get('url')
    if not tiktok_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
            info = ydl.extract_info(tiktok_url, download=False)
            caption = info.get('description') or info.get('title', 'No caption found')
            return jsonify({"caption": caption})
    except Exception as e:
        return jsonify({"error": f"Could not retrieve info: {str(e)}"}), 500

# --- Streaming Endpoint ---
@app.route('/stream')
def stream_video_endpoint():
    tiktok_url = request.args.get('url')
    if not tiktok_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400
    try:
        command = [
            sys.executable, '-m', 'yt_dlp',
            '--format', 'best[ext=mp4]/best',
            '--output', '-',
            '--quiet',
            tiktok_url
        ]
        
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def generate_stream():
            try:
                while True:
                    chunk = process.stdout.read(4096)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if process.poll() is None:
                    process.terminate()
                print("Stream process finished.")

        return Response(generate_stream(), mimetype='video/mp4')
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

# --- API Endpoint for SRT Subtitles ---
@app.route('/srt')
def get_srt_endpoint():
    tiktok_url = request.args.get('url')
    cookie_string = request.args.get('cookie')
    lang = request.args.get('lang', 'en')

    if not tiktok_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            command = [
                sys.executable, '-m', 'yt_dlp',
                '--write-auto-subs',
                '--sub-lang', lang,
                '--skip-download',
                '--sub-format', 'srt',
                '--output', os.path.join(tmpdir, '%(id)s.%(ext)s'),
                '--quiet',
            ]

            if cookie_string:
                print("Using provided cookie for authentication.")
                command.extend(['--add-header', f'Cookie: {cookie_string}'])
            else:
                print("No cookie provided, making an anonymous request.")

            command.append(tiktok_url)

            result = subprocess.run(command, capture_output=True, timeout=25)

            # Try exact lang match first, then fallback to any .srt
            srt_files = glob.glob(os.path.join(tmpdir, f'*.{lang}*.srt'))
            if not srt_files:
                srt_files = glob.glob(os.path.join(tmpdir, '*.srt'))

            if srt_files:
                with open(srt_files[0], 'r', encoding='utf-8') as f:
                    return Response(f.read(), mimetype='text/plain; charset=utf-8')
            else:
                error_message = result.stderr.decode('utf-8', 'ignore')
                print(f"SRT generation failed for {tiktok_url}. Stderr: {error_message}")
                return jsonify({
                    "error": f"Subtitles not found for language '{lang}'",
                    "detail": error_message
                }), 404

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Request timed out while fetching subtitles"}), 504
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

# --- Root Endpoint ---
@app.route('/')
def home():
    return "TikTok Streaming & Subtitle API v8 is running."
