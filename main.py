# --- Imports and Setup ---
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from yt_dlp import YoutubeDL
import asyncio
import random
import time
import aiohttp
from flask import Flask
from threading import Thread
from collections import deque
import os
import subprocess
from ytmusicapi import YTMusic
from keep_alive import keep_alive

# Start keep-alive server
keep_alive()

# Discord Bot Token
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Global song queues
song_queues = {}

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

# Bot instance
description = "himari <3"
bot = commands.Bot(command_prefix="/", intents=intents, description=description)

# Initialize YouTube Music client
ytmusic = YTMusic()

# --- Responses ---
RESPONSES = {
    "play": [
        "Yay~ Now playing: {title}! Enjoy the vibes!",
        "Teehee~ I queued up {title} just for you!",
        "Here comes {title}! Let’s jam together!",
        "Spinning up {title}~ Hope it makes you smile!",
    ],
    "pause": [
        "Pausey-wausey~ Let’s take a break!",
        "Hold on~ I’ll pause it, just for you!",
        "Alrighty~ We’re on pause now!",
        "Music break time~ Let me know when to resume!",
    ],
    "resume": [
        "Resuming the beat~ Let’s groove!",
        "Back to jamming~ Let’s gooo!",
        "Yay~ Unpaused and playing again!",
        "No more silence~ Let’s keep the fun going!",
    ],
    "skip": [
        "Oki doki~ Skipping to the next one!",
        "Next please~ Zooming ahead!",
        "Whoosh~ That song's gone, here comes the next!",
        "Let’s try something else~ Skipped!",
    ],
    "stop": [
        "Stopping now~ It was fun while it lasted!",
        "I've stopped the music~",
        "Music paused forever~ Unless you start again!",
        "Okay! No more tunes for now~",
    ],
    "end": [
        "Music’s all done~ That was fun!",
        "All stopped~ Hope you liked it!",
        "I’ve stopped the tunes for now~",
        "That’s a wrap~ Let me know if you want more!",
    ]
}

def get_response(category, **kwargs):
    return random.choice(RESPONSES[category]).format(**kwargs)

def format_duration(seconds):
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02}:{secs:02}"

def parse_lrc(lrc_text):
    # unchanged
    ...

async def fetch_lrc(query):
    # unchanged
    ...

# Helper to parse YTMusic duration strings
def parse_duration(dur_str):
    if not dur_str:
        return 0
    parts = [int(x) for x in dur_str.split(':')]
    if len(parts) == 3:
        return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts) == 2:
        return parts[0]*60 + parts[1]
    return parts[0]

# --- Search with YTMusicAPI, Stream with no extra downloads ---
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

def get_youtube_info(query: str):
    """
    1. Search YouTube Music via ytmusicapi for metadata.
    2. Attempt to get direct audio stream URL via pytube.
    3. Fallback to yt_dlp if pytube fails.
    """
    # 1) Metadata from YTMusicAPI
    results = ytmusic.search(query, filter='songs')
    if not results:
        subprocess.call(["echo", "[Error] No YTMusic results found."])
        return None
    track = results[0]
    video_id = track.get('videoId')
    if not video_id:
        subprocess.call(["echo", "[Error] Missing video ID."])
        return None

    music_url = f"https://music.youtube.com/watch?v={video_id}"
    subprocess.call(["echo", f"[Info] YTMusic URL: {music_url}"])

    stream_url = None
    # 2) Try pytube for direct stream URL
    try:
        from pytube import YouTube
        yt = YouTube(music_url)
        audio_stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
        stream_url = audio_stream.url
        subprocess.call(["echo", f"[Pytube Stream URL] {stream_url}"])
    except Exception as e:
        subprocess.call(["echo", f"[Pytube failed] {e}, falling back to yt_dlp"])
        # 3) Fallback to yt_dlp
        try:
            from yt_dlp import YoutubeDL
            with YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(music_url, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                stream_url = info.get('url')
                subprocess.call(["echo", f"[yt_dlp Stream URL] {stream_url}"])
        except Exception as e2:
            subprocess.call(["echo", f"[Error fallback yt_dlp] {e2}"])
            return None

    if not stream_url:
        subprocess.call(["echo", "[Error] Could not obtain stream URL."])
        return None

    # 4) Compile metadata
    title = track.get('title')
    thumbs = track.get('thumbnails') or []
    thumb_url = thumbs[-1]['url'] if thumbs else None
    duration = parse_duration(track.get('duration'))
    artist = track.get('artists', [{}])[0].get('name', 'Unknown')

    return {
        'url': stream_url,
        'title': title,
        'thumbnail': thumb_url,
        'duration': duration,
        'uploader': artist,
        'id': video_id
    }

# --- Bot Commands ---
@bot.tree.command(name="pause", description="Pause playback ⏸️")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message(get_response("pause"))

@bot.tree.command(name="resume", description="Resume playback ▶️")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message(get_response("resume"))

@bot.tree.command(name="skip", description="Skip current song ⏭️")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message(get_response("skip"))

@bot.tree.command(name="enqueue", description="Add a song to the queue ➕")
@app_commands.describe(query="Song name or link 🎵")
async def enqueue(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    gid = interaction.guild.id
    song_queues.setdefault(gid, deque())
    song_queues[gid].append((query, interaction))
    await interaction.followup.send(f"Enqueued **{query}**~ 💕")

@bot.tree.command(name="clear", description="Clear the queue 🗑️")
async def clear(interaction: discord.Interaction):
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message("Queue cleared~ 🌸")

@bot.tree.command(name="stop", description="Stop and disconnect ⏹️")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message(get_response("stop"))

@bot.tree.command(name="end", description="Stop and leave ✨")
async def end(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    song_queues[interaction.guild.id] = deque()
    if vc:
        await vc.disconnect()
    await interaction.response.send_message(get_response("end"))

@bot.event
async def on_ready():
    await bot.tree.sync()
    subprocess.call(["echo", f"Himari is awake as {bot.user}~"])

# --- Run Bot ---
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    subprocess.call(["echo", "[Fatal] DISCORD_TOKEN not set."])