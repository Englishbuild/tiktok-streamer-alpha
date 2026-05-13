import subprocess
import os
import re
import urllib.request
from flask import Flask, Response, request, jsonify
import yt_dlp
import sys

app = Flask(__name__)

# --- Helper: Pure Python VTT to SRT Converter ---
def vtt_to_srt(vtt_content):
    lines = vtt_content.splitlines()
    srt_lines =[]
    counter = 1
    
    for i, line in enumerate(lines):
        if i == 0 and "WEBVTT" in line:
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
            
        if "-->" in line:
            parts = line.split("-->")
            fixed_parts =[]
            for part in parts:
                part = part.strip().replace('.', ',')
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
                if i + 1 < len(lines) and "-->" in lines[i + 1]:
                    continue
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

        return Response(generate_stream(), mimetype='video/mp4')
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


# --- API Endpoint for SRT Subtitles ---
@app.route('/srt')
def get_srt_endpoint():
    tiktok_url = request.args.get('url')
    cookie_string = request.args.get('cookie')
    lang_request = request.args.get('lang', 'eng-US')

    if not tiktok_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400

    try:
        ydl_opts = {
            'quiet': True,
            'noplaylist': True,
        }
        if cookie_string:
            ydl_opts['http_headers'] = {'Cookie': cookie_string}

        # Use the Python API to extract meta-data (no subprocess needed!)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(tiktok_url, download=False)
            
            subs = info.get('subtitles', {})
            if not subs:
                return jsonify({"error": "No subtitles found for this video."}), 404

            sub_url = None
            
            # 1. Look for English formats in order of priority (fixes missing lang bugs)
            preferred_langs = [lang_request, 'eng-US', 'en-US', 'en', 'eng']
            for lang in preferred_langs:
                if lang in subs:
                    for fmt in subs[lang]:
                        if fmt.get('ext') == 'vtt':
                            sub_url = fmt.get('url')
                            break
                if sub_url:
                    break
                    
            # 2. If no english found, fallback to the first available language!
            if not sub_url:
                for lang, formats in subs.items():
                    for fmt in formats:
                        if fmt.get('ext') == 'vtt':
                            sub_url = fmt.get('url')
                            break
                    if sub_url:
                        break

            # 3. If STILL no VTT url found, return exact details of what IS available
            if not sub_url:
                return jsonify({
                    "error": "Subtitles exist, but no VTT format is available.",
                    "available_langs": list(subs.keys())
                }), 404

            # Fetch the VTT file content directly into memory
            req = urllib.request.Request(sub_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                vtt_content = response.read().decode('utf-8')
                
            # Convert to SRT and serve
            srt_content = vtt_to_srt(vtt_content)
            return Response(srt_content, mimetype='text/plain; charset=utf-8')

    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


# --- Root Endpoint ---
@app.route('/')
def home():
    return "TikTok Streaming & Subtitle API v9 is running."

if __name__ == "__main__":
    app.run()
