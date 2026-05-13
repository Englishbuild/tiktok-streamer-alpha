import subprocess
import glob
import os
import tempfile
import re
from flask import Flask, Response, request, jsonify
import yt_dlp
import sys

app = Flask(__name__)

# --- Helper: Pure Python VTT to SRT Converter ---
# Vercel doesn't have FFmpeg, so yt-dlp cannot convert .vtt to .srt automatically.
# This lightweight function handles the conversion so your API still returns valid SRT.
def vtt_to_srt(vtt_content):
    lines = vtt_content.splitlines()
    srt_lines =[]
    counter = 1
    
    for i, line in enumerate(lines):
        # Skip VTT header metadata
        if i == 0 and "WEBVTT" in line:
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
            
        # Format Timestamp line
        if "-->" in line:
            parts = line.split("-->")
            fixed_parts =[]
            for part in parts:
                # SRT uses a comma for milliseconds, VTT uses a period
                part = part.strip().replace('.', ',')
                # Prepend '00:' if timestamp is missing hours (e.g. MM:SS,mmm -> 00:MM:SS,mmm)
                if part.count(':') == 1:
                    part = "00:" + part
                fixed_parts.append(part)
            
            if srt_lines and srt_lines[-1] != "":
                srt_lines.append("")
            
            srt_lines.append(str(counter))
            srt_lines.append(" --> ".join(fixed_parts))
            counter += 1
        else:
            if "WEBVTT" not in line:
                # Skip identifier lines that appear right before the timestamp
                if i + 1 < len(lines) and "-->" in lines[i + 1]:
                    continue
                
                # Remove VTT inline styling (like <c.color> or <b>)
                clean_line = re.sub(r'<[^>]+>', '', line)
                if clean_line.strip() or (srt_lines and srt_lines[-1] != ""):
                    srt_lines.append(clean_line)
                    
    return "\n".join(srt_lines).strip()


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
            '--quiet', tiktok_url
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
    # Default to TikTok's standard "eng-US" instead of "en"
    lang = request.args.get('lang', 'eng-US')

    if not tiktok_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400

    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            command =[
                sys.executable, '-m', 'yt_dlp',
                '--write-sub',        # CHANGED: Normal subs, not auto-subs
                '--sub-lang', lang,
                '--skip-download',
                # REMOVED: '--sub-format', 'srt' because Vercel lacks FFmpeg to convert it
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

            # Look for the .vtt files (or any subtitle format it grabbed)
            sub_files = glob.glob(os.path.join(tmpdir, f'*.{lang}.*'))
            if not sub_files:
                # Fallback: grab any file that isn't a video format
                all_files = glob.glob(os.path.join(tmpdir, '*.*'))
                sub_files = [f for f in all_files if not f.endswith(('.mp4', '.webm', '.mkv'))]

            if sub_files:
                with open(sub_files[0], 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # If yt-dlp downloaded a VTT file, manually convert to SRT
                if sub_files[0].endswith('.vtt'):
                    content = vtt_to_srt(content)

                return Response(content, mimetype='text/plain; charset=utf-8')
            else:
                error_message = result.stderr.decode('utf-8', 'ignore')
                print(f"Subtitle generation failed for {tiktok_url}. Stderr: {error_message}")
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

if __name__ == "__main__":
    app.run()
