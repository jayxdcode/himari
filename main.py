# --- Imports and Setup ---
import discord
from discord.ext import commands
from discord import app_commands
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
from pytube import YouTube
from yt_dlp import YoutubeDL
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
description = "Himari wants to spin some tunes for you! 💖"
bot = commands.Bot(command_prefix="!", intents=intents, description=description)

# Initialize YouTube Music client
ytmusic = YTMusic()

# yt_dlp and FFmpeg options
YTDL_OPTIONS = {
    'format': 'bestaudio',
    'quiet': True,
    'noplaylist': True,
    'default_search': 'auto',
    'extract_flat': 'in_playlist'
}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# --- Responses ---
RESPONSES = {
    "play": ["Yay~ Now playing: {title}! Enjoy the vibes!", "Teehee~ I queued up {title} just for you!",
             "Here comes {title}! Let’s jam together!", "Spinning up {title}~ Hope it makes you smile!"],
    "pause": ["Pausey-wausey~ Let’s take a break!", "Hold on~ I’ll pause it, just for you!",
              "Alrighty~ We’re on pause now!", "Music break time~ Let me know when to resume!"],
    "resume": ["Resuming the beat~ Let’s groove!", "Back to jamming~ Let’s gooo!",
               "Yay~ Unpaused and playing again!", "No more silence~ Let’s keep the fun going!"],
    "skip": ["Oki doki~ Skipping to the next one!", "Next please~ Zooming ahead!",
              "Whoosh~ That song's gone, here comes the next!", "Let’s try something else~ Skipped!"],
    "stop": ["Stopping now~ It was fun while it lasted!", "I've stopped the music~",
              "Music paused forever~ Unless you start again!", "Okay! No more tunes for now~"],
    "end": ["Music’s all done~ That was fun!", "All stopped~ Hope you liked it!",
             "I’ve stopped the tunes for now~", "That’s a wrap~ Let me know if you want more!"]
}

def get_response(category, **kwargs):
    return random.choice(RESPONSES[category]).format(**kwargs)


def format_duration(seconds):
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02}:{secs:02}"


def parse_lrc(lrc_text):
    parsed = []
    for line in (lrc_text or "").splitlines():
        if line.startswith("["):
            parts = line.split("]")
            for part in parts[:-1]:
                ts = part.strip("[]")
                try:
                    m, s = map(float, ts.split(':'))
                    parsed.append((m * 60 + s, parts[-1]))
                except:
                    pass
    return parsed

async def fetch_lrc(query):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://lrclib.net/api/search?q={query}") as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if not data:
                return None
            tid = data[0]['id']
            async with session.get(f"https://lrclib.net/api/get?track_id={tid}") as r2:
                j = await r2.json()
                return j.get("syncedLyrics")

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

# --- Search & Stream Logic ---
def get_youtube_info(query: str):
    # 1) Metadata via ytmusicapi
    results = ytmusic.search(query, filter='songs')
    if not results:
        subprocess.call(["echo", "[Error] No YTMusic results found."])
        return None
    track = results[0]
    vid = track.get('videoId')
    if not vid:
        subprocess.call(["echo", "[Error] Missing video ID."])
        return None

    music_url = f"https://music.youtube.com/watch?v={vid}"
    subprocess.call(["echo", f"[Info] YTMusic URL: {music_url}"])

    # 2) Try pytube
    stream_url = None
    try:
        yt = YouTube(music_url)
        asp = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
        stream_url = asp.url
        subprocess.call(["echo", f"[Pytube Stream URL] {stream_url}"])
    except Exception as e:
        subprocess.call(["echo", f"[Pytube failed] {e}, falling back to yt_dlp"])
        try:
            with YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(music_url, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                stream_url = info.get('url')
                subprocess.call(["echo", f"[yt_dlp Stream URL] {stream_url}"])
        except Exception as e2:
            subprocess.call(["echo", f"[Error fallback] {e2}"])
            return None

    if not stream_url:
        subprocess.call(["echo", "[Error] Could not obtain stream URL."])
        return None

    # 3) Assemble metadata
    title = track.get('title')
    thumbs = track.get('thumbnails') or []
    thumb = thumbs[-1]['url'] if thumbs else None
    dur = parse_duration(track.get('duration'))
    artist = track.get('artists', [{}])[0].get('name', 'Unknown')

    return {'url': stream_url, 'title': title, 'thumbnail': thumb, 'duration': dur, 'uploader': artist}

async def send_now_playing(interaction, title, thumb, duration, lrc_data):
    embed = discord.Embed(title="Now Playing", description=f"**{title}**", color=0xff99cc)
    if thumb: embed.set_thumbnail(url=thumb)
    embed.add_field(name="Progress", value=f"00:00 / {format_duration(duration)}", inline=False)
    msg = await interaction.followup.send(embed=embed)
    await msg.add_reaction("⏯"); await msg.add_reaction("⏭"); await msg.add_reaction("🔁")

    start = time.time(); current = ""
    while interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        el = time.time() - start
        prog = f"`{format_duration(el)} / {format_duration(duration)}`"
        embed.set_field_at(0, name="Progress", value=prog, inline=False)
        if lrc_data:
            for ts, line in lrc_data:
                if el >= ts: current = line
                else: break
            embed.description = f"**{title}**\n\n*{current}*"
        await msg.edit(embed=embed)
        await asyncio.sleep(1)

# --- Core Commands ---
@bot.tree.command(name="play", description="Play a song 🌟")
@app_commands.describe(query="Song name or link 🎵")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("Uwah~ please hop into a voice channel first~ 🎧")

    gid = interaction.guild.id
    song_queues.setdefault(gid, deque())
    first = not song_queues[gid]
    song_queues[gid].append((query, interaction))
    if first: await play_next(gid)
    else: await interaction.followup.send(f"Teehee~ Added **{query}** to the queue~ 💖")

async def play_next(gid):
    if not song_queues.get(gid): return
    query, interaction = song_queues[gid].popleft()
    try:
        vc = interaction.guild.voice_client or await interaction.user.voice.channel.connect()
        info = get_youtube_info(query)
        if not info: raise Exception("No info")
        src = await discord.FFmpegOpusAudio.from_probe(info['url'], method='fallback', executable='ffmpeg', **FFMPEG_OPTIONS)
        vc.play(src, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(gid), bot.loop))
        await interaction.followup.send(get_response("play", title=info['title']))
        raw = await fetch_lrc(info['title']); lrc = parse_lrc(raw) if raw else []
        await send_now_playing(interaction, info['title'], info['thumbnail'], info['duration'], lrc)
    except Exception as e:
        subprocess.call(["echo", f"[Error] play_next failed: {e}"])
        await interaction.followup.send("Uwah~ couldn't play that one, moving on~")
        await play_next(gid)

@bot.tree.command(name="pause", description="Pause playback ⏸️")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause(); await interaction.response.send_message(get_response("pause"))

@bot.tree.command(name="resume", description="Resume playback ▶️")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume(); await interaction.response.send_message(get_response("resume"))

@bot.tree.command(name="skip", description="Skip current song ⏭️")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop(); await interaction.response.send_message(get_response("skip"))

@bot.tree.command(name="enqueue", description="Add a song to the queue ➕")
@app_commands.describe(query="Song name or link 🎵")
async def enqueue(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    gid = interaction.guild.id; song_queues.setdefault(gid, deque())
    song_queues[gid].append((query, interaction))
    await interaction.followup.send(f"Enqueued **{query}**~ 💕")

@bot.tree.command(name="clear", description="Clear the queue 🗑️")
async def clear(interaction: discord.Interaction):
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message("Queue cleared~ 🌸")

@bot.tree.command(name="stop", description="Stop and disconnect ⏹️")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc: vc.stop(); await vc.disconnect()
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message(get_response("stop"))

@bot.tree.command(name="end", description="Stop and leave ✨")
async def end(interaction: discord.Interaction):
    vc = interaction.guild.voice_client; song_queues[interaction.guild.id] = deque()
    if vc: await vc.disconnect()
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