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
description = "Himari Music Bot"
bot = commands.Bot(command_prefix="!", intents=intents, description=description)

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
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02}:{secs:02}"


def parse_lrc(lrc_text):
    parsed = []
    lines = lrc_text.splitlines()
    for line in lines:
        if line.startswith("["):
            parts = line.split("]")
            for part in parts[:-1]:
                timestamp = part.strip("[]")
                try:
                    m, s = map(float, timestamp.split(':'))
                    parsed.append((m * 60 + s, parts[-1]))
                except:
                    continue
    return parsed

async def fetch_lrc(query):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://lrclib.net/api/search?q={query}") as resp:
            if resp.status != 200:
                return None
            search_data = await resp.json()
            if not search_data:
                return None
            best = search_data[0]
            track_id = best['id']
            async with session.get(f"https://lrclib.net/api/get?track_id={track_id}") as lrc_resp:
                lrc_json = await lrc_resp.json()
                return lrc_json.get("syncedLyrics")

# --- Search on YouTube Music and Stream Audio ---
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

def get_youtube_info(query: str):
    """
    1. Search YouTube Music using ytmusicapi for song results.
    2. Take first result's videoId.
    3. Use yt_dlp to extract direct audio stream URL.
    """
    # Search on YouTube Music
    results = ytmusic.search(query, filter='songs')
    if not results:
        subprocess.call(["echo", "[Error] No music results found."])
        return None
    track = results[0]
    video_id = track.get('videoId')
    if not video_id:
        subprocess.call(["echo", "[Error] No video ID."])
        return None

    youtube_url = f"https://music.youtube.com/watch?v={video_id}"
    subprocess.call(["echo", f"[Info] YouTube Music URL: {youtube_url}"])

    try:
        with YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            # Echo the direct stream URL
            stream_url = info.get('url')
            subprocess.call(["echo", f"[Stream URL] {stream_url}"])
            return {
                'url': stream_url,
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'id': info.get('id'),
            }
    except Exception as e:
        subprocess.call(["echo", f"[Error in get_youtube_info] {e}"])
        return None

async def send_now_playing(interaction, title, thumb, duration, lrc_data):
    embed = discord.Embed(title="Now Playing", description=f"**{title}**", color=0xff99cc)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.add_field(name="Progress", value=f"00:00 / {format_duration(duration)}", inline=False)
    msg = await interaction.followup.send(embed=embed)
    await msg.add_reaction("⏯")
    await msg.add_reaction("⏭")
    await msg.add_reaction("🔁")

    start = time.time()
    current_line = ""
    while interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        elapsed = time.time() - start
        progress = f"`{format_duration(elapsed)} / {format_duration(duration)}`"
        embed.set_field_at(0, name="Progress", value=progress, inline=False)

        if lrc_data:
            for ts, line in lrc_data:
                if elapsed >= ts:
                    current_line = line
                else:
                    break
            embed.description = f"**{title}**\n\n*{current_line}*"

        await msg.edit(embed=embed)
        await asyncio.sleep(1)

# --- Slash Commands ---
@bot.tree.command(name="play", description="Play a song")
@app_commands.describe(query="Search or link")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("Join a voice channel first!")

    guild_id = interaction.guild.id
    if guild_id not in song_queues:
        song_queues[guild_id] = deque()

    queue_empty = not song_queues[guild_id]
    song_queues[guild_id].append((query, interaction))

    if queue_empty:
        await play_next(guild_id)
    else:
        await interaction.followup.send(f"Enqueued **{query}**!")

async def play_next(guild_id):
    if guild_id not in song_queues or not song_queues[guild_id]:
        return

    query, interaction = song_queues[guild_id].popleft()
    try:
        vc = interaction.guild.voice_client or await interaction.user.voice.channel.connect()
        info = get_youtube_info(query)
        if not info:
            raise Exception("Could not get YouTube info")

        source = await discord.FFmpegOpusAudio.from_probe(
            info['url'],
            method='fallback',
            executable='./ffmpeg.bin' if os.path.exists('./ffmpeg.bin') else 'ffmpeg',
            **FFMPEG_OPTIONS
        )
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop))

        await interaction.followup.send(get_response("play", title=info['title']))
        raw_lrc = await fetch_lrc(info['title'])
        lrc_data = parse_lrc(raw_lrc) if raw_lrc else []
        await send_now_playing(interaction, info['title'], info['thumbnail'], info['duration'], lrc_data)

    except Exception as e:
        subprocess.call(["echo", f"[Error in play_next] {e}"])
        await interaction.followup.send("Oops, couldn't play that song. Moving on...")
        await play_next(guild_id)

@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message(get_response("pause"))

@bot.tree.command(name="resume", description="Resume playback")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message(get_response("resume"))

@bot.tree.command(name="skip", description="Skip current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message(get_response("skip"))

@bot.tree.command(name="enqueue", description="Add a song to the queue")
@app_commands.describe(query="Search or link")
async def enqueue(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    guild_id = interaction.guild.id
    if guild_id not in song_queues:
        song_queues[guild_id] = deque()
    song_queues[guild_id].append((query, interaction))
    await interaction.followup.send(f"Enqueued **{query}**!")

@bot.tree.command(name="clear", description="Clear the queue")
async def clear(interaction: discord.Interaction):
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message("Queue cleared~")

@bot.tree.command(name="stop", description="Stop music and disconnect")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message(get_response("stop"))

@bot.tree.command(name="end", description="Stop music and leave")
async def end(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    song_queues[interaction.guild.id] = deque()
    if vc:
        await vc.disconnect()
    await interaction.response.send_message(get_response("end"))

@bot.event
async def on_ready():
    await bot.tree.sync()
    subprocess.call(["echo", f"Himari is ready as {bot.user}"])

# --- Run Bot ---
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    subprocess.call(["echo", "DISCORD_TOKEN not set."])