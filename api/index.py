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


# --- API Endpoint for Caption / Info ---
@app.route('/info')
def get_info_endpoint():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400

    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            caption = info.get('description') or info.get('title', 'No caption found')
            return jsonify({
                "platform": info.get('extractor_key', 'Unknown'),
                "title": info.get('title'),
                "caption": caption
            })
    except Exception as e:
        return jsonify({"error": f"Could not retrieve info: {str(e)}"}), 500


# --- Streaming Endpoint ---
@app.route('/stream')
def stream_video_endpoint():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400
        
    try:
        command = [
            sys.executable, '-m', 'yt_dlp',
            '--format', 'best[ext=mp4]/best',
            '--output', '-',
            '--quiet', video_url
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


# --- API Endpoint for SRT Subtitles (Universal for YouTube & TikTok) ---
@app.route('/srt')
def get_srt_endpoint():
    video_url = request.args.get('url')
    cookie_string = request.args.get('cookie')
    lang_request = request.args.get('lang', 'en') # Defaulted to 'en' as it's standard for YT

    if not video_url:
        return jsonify({"error": "Missing 'url' query parameter"}), 400

    try:
        ydl_opts = {
            'quiet': True,
            'noplaylist': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['all']
        }
        if cookie_string:
            ydl_opts['http_headers'] = {'Cookie': cookie_string}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            # Combine manually uploaded subtitles AND auto-generated captions
            subs_manual = info.get('subtitles') or {}
            subs_auto = info.get('automatic_captions') or {}
            
            # Merge them together (manual takes precedence if both exist)
            subs = {**subs_auto, **subs_manual}

            if not subs:
                return jsonify({"error": "No subtitles or automatic captions found for this video."}), 404

            sub_url = None
            sub_ext = None
            
            # Helper to find the best format (Prefer VTT/SRT for YouTube & TikTok)
            def get_best_format(formats):
                # 1. Look for native SRT
                for fmt in formats:
                    if fmt.get('ext') == 'srt':
                        return fmt.get('url'), 'srt'
                # 2. Look for VTT (Which YouTube heavily uses and we can convert)
                for fmt in formats:
                    if fmt.get('ext') == 'vtt':
                        return fmt.get('url'), 'vtt'
                # 3. Fallback to json (TikTok) or json3 (YouTube)
                for fmt in formats:
                    if 'json' in fmt.get('ext', ''):
                        return fmt.get('url'), fmt.get('ext')
                # 4. Ultimate fallback
                if formats:
                    return formats[0].get('url'), formats[0].get('ext')
                return None, None

            # Look for English (or requested) languages first in multiple common variations
            preferred_langs = [lang_request, 'en', 'en-US', 'eng-US', 'eng', 'en-GB']
            for lang in preferred_langs:
                if lang in subs:
                    sub_url, sub_ext = get_best_format(subs[lang])
                    if sub_url:
                        break
                        
            # If no english found, grab the very first available language format
            if not sub_url:
                for lang, formats in subs.items():
                    sub_url, sub_ext = get_best_format(formats)
                    if sub_url:
                        break

            if not sub_url:
                return jsonify({
                    "error": "Subtitles exist, but no standard text download URL is available.",
                    "available_langs": list(subs.keys())
                }), 404

            # Fetch the subtitle content from the direct URL (with timeout to prevent freezing)
            req = urllib.request.Request(sub_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                content = response.read().decode('utf-8')
                
            # If it's VTT, run it through our converter to output standard SRT
            if sub_ext == 'vtt' or 'WEBVTT' in content:
                srt_content = vtt_to_srt(content)
                return Response(srt_content, mimetype='text/plain; charset=utf-8')
                
            # If YouTube/TikTok forced a JSON format, return raw JSON
            if 'json' in sub_ext:
                return Response(content, mimetype='application/json; charset=utf-8')
                
            # Return raw string if it's already srt
            return Response(content, mimetype='text/plain; charset=utf-8')

    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


# --- Root Endpoint ---
@app.route('/')
def home():
    return "Universal YouTube & TikTok Streaming & Subtitle API v11 is running."

if __name__ == "__main__":
    app.run()
