import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from yt_dlp import YoutubeDL
import yt_dlp.utils
import asyncio
import random
import time
import aiohttp
from flask import Flask
from threading import Thread
from collections import deque
import os
from keep_alive import keep_alive
keep_alive()

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Data structures
song_queues = {}
volume_controls = {}
looping = {}

# Throttle control
last_request_time = 0
THROTTLE_DELAY = 1.0

# Proxy list
PROXIES = [
    'socks5://146.190.245.171:1080',
    'socks5://192.46.226.150:1080',
    'socks5://137.184.103.224:1080'
]
proxy_index = 0

def normalize_youtube_link(query: str) -> str:
    if 'music.youtube.com' in query:
        return query
    elif 'youtube.com' in query:
        return query.replace("youtube.com", "music.youtube.com")
    elif 'm.youtube.com' in query:
        return query.replace("m.youtube.com", "music.youtube.com")
    elif 'youtu.be' in query:
        return query.replace("youtu.be/", "music.youtube.com/watch?v=")
    else:
        return f"https://music.youtube.com/watch?v={query.split('v=')[-1]}"

def get_youtube_info(query: str):
    global last_request_time, proxy_index
    elapsed = time.time() - last_request_time
    if elapsed < THROTTLE_DELAY:
        time.sleep(THROTTLE_DELAY - elapsed)
    last_request_time = time.time()

    query = normalize_youtube_link(query)
    proxy = PROXIES[proxy_index]
    proxy_index = (proxy_index + 1) % len(PROXIES)

    ydl_opts = {
        'quiet': True,
        'extract_flat': False,
        'format': 'bestaudio/best',
        'noplaylist': True,
        'cookiefile': 'cookies.txt',
        'proxy': proxy,
        'default_search': 'ytsearch',
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return {
                'url': info.get('webpage_url'),
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'id': info.get('id'),
            }
    except yt_dlp.utils.DownloadError as e:
        print(f"[YTDL Error] {e}")
    except Exception as e:
        print(f"[Error] {e}")
    return None

def format_duration(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02}:{secs:02}"

def parse_lrc(lrc_string):
    result = []
    if not lrc_string:
        return result
    for line in lrc_string.strip().splitlines():
        if line.startswith('[') and ']' in line:
            timestamp = line[1:line.index(']')]
            text = line[line.index(']')+1:]
            parts = timestamp.split(":")
            seconds = int(parts[0]) * 60 + float(parts[1])
            result.append((seconds, text))
    return result

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

async def send_now_playing(interaction, title, thumb, duration, lrc_data):
    embed = discord.Embed(title="**Now Playing**", description=f"**{title}**", color=0xff99cc)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.add_field(name="Progress", value=f"`00:00 / {format_duration(duration)}`", inline=False)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="⏯ Pause", style=discord.ButtonStyle.secondary, custom_id="pause"))
    view.add_item(discord.ui.Button(label="▶ Resume", style=discord.ButtonStyle.secondary, custom_id="resume"))
    view.add_item(discord.ui.Button(label="⏭ Skip", style=discord.ButtonStyle.secondary, custom_id="skip"))
    view.add_item(discord.ui.Button(label="🔁 Loop", style=discord.ButtonStyle.secondary, custom_id="loop"))

    msg = await interaction.followup.send(embed=embed, view=view)
    start = time.time()
    current_line = ""

    while interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        elapsed = time.time() - start
        progress = f"`{format_duration(elapsed)} / {format_duration(duration)}`"
        embed.set_field_at(0, name="Progress", value=progress, inline=False)

        if lrc_data:
            for i in range(len(lrc_data)):
                if elapsed >= lrc_data[i][0]:
                    current_line = lrc_data[i][1]
                else:
                    break
            embed.description = f"""**{title}**
            
            *{current_line}*"""

        await msg.edit(embed=embed)
        await asyncio.sleep(1)

async def play_next(guild_id):
    if guild_id not in song_queues or not song_queues[guild_id]:
        return

    query, interaction = song_queues[guild_id].popleft()

    try:
        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        info = get_youtube_info(query)
        if not info:
            await interaction.followup.send("Failed to get video info.")
            return

        url, title, thumb, duration = info["url"], info["title"], info["thumbnail"], info["duration"]
        source = await discord.FFmpegOpusAudio.from_probe(url, method='fallback', executable='./ffmpeg.bin')
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop) if not looping.get(guild_id, False) else vc.play(source))

        await interaction.followup.send(f"Now playing **{title}**!")
        raw_lrc = await fetch_lrc(title)
        lrc_data = parse_lrc(raw_lrc) if raw_lrc else []
        await send_now_playing(interaction, title, thumb, duration, lrc_data)

    except Exception as e:
        print(f"Error in play_next: {e}")
        await interaction.followup.send("Playback error.")
        await play_next(guild_id)

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
        await interaction.followup.send(f"Queued: **{query}**")

@bot.tree.command(name="enqueue", description="Add a song to the queue without interrupting current playback")
@app_commands.describe(query="Search or link")
async def enqueue(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    guild_id = interaction.guild.id
    if guild_id not in song_queues:
        song_queues[guild_id] = deque()
    song_queues[guild_id].append((query, interaction))
    await interaction.followup.send(f"Enqueued: **{query}**")

@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("Playback paused!")

@bot.tree.command(name="resume", description="Resume playback")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("Playback resumed!")

@bot.tree.command(name="skip", description="Skip song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Skipped!")

@bot.tree.command(name="loop", description="Toggle loop")
async def loop(interaction: discord.Interaction):
    gid = interaction.guild.id
    looping[gid] = not looping.get(gid, False)
    await interaction.response.send_message(f"Looping {'enabled' if looping[gid] else 'disabled'}!")

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(DISCORD_TOKEN)