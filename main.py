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

# Data structures to hold song queue and other info
song_queues = {}  # {guild_id: [(song_url, song_title), ...]}
volume_controls = {}  # {guild_id: volume_level}

# Normalizing YouTube links to use music.youtube.com
def normalize_youtube_link(query: str) -> str:
    """Normalizes YouTube links to the preferred format."""
    if 'music.youtube.com' in query:
        return query
    elif 'youtube.com' in query:
        return query.replace("youtube.com", "music.youtube.com")
    elif 'm.youtube.com' in query:
        return query.replace("m.youtube.com", "music.youtube.com")
    else:
        return f"https://music.youtube.com/watch?v={query.split('v=')[-1]}"

# Get YouTube video info
def get_youtube_info(query: str):
    """
    Fetches information about a YouTube video.
    Accepts a direct URL or a search query.
    Does NOT support playlists.
    Returns a dictionary with URL, title, thumbnail, and duration.
    """
    query = normalize_youtube_link(query)  # Normalize the YouTube link

    ydl_opts = {
        'quiet': True,
        'extract_flat': False,
        'format': 'bestaudio/best',
        'noplaylist': True,  # Prevent playlist extraction
        'default_search': 'ytsearch',  # Allows text search
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)

            # If a search was used, `extract_info` returns a dict with 'entries'
            if 'entries' in info:
                info = info['entries'][0]

            return {
                'url': info.get('webpage_url'),
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),  # in seconds
                'uploader': info.get('uploader'),
                'id': info.get('id'),
            }

    except yt_dlp.utils.DownloadError as e:
        print(f"[YTDL Error] {e}")
        return None
    except Exception as e:
        print(f"[Unexpected Error] {e}")
        return None

# Format duration for display
def format_duration(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02}:{secs:02}"

# Send the "Now Playing" embed with song progress and lyrics
async def send_now_playing(interaction, title, thumb, duration, lrc_data):
    embed = discord.Embed(title="**Now Playing**", description=f"**{title}**", color=0xff99cc)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.add_field(name="Progress", value=f"`00:00 / {format_duration(duration)}`", inline=False)
    
    # Buttons
    play_button = discord.ui.Button(label="⏯ Play/Pause", custom_id="play_pause")
    skip_button = discord.ui.Button(label="⏭ Skip", custom_id="skip")
    repeat_button = discord.ui.Button(label="🔁 Loop", custom_id="repeat")

    # Create a message with buttons
    action_row = discord.ui.ActionRow(play_button, skip_button, repeat_button)

    msg = await interaction.followup.send(embed=embed, components=[action_row])
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
            embed.description = f"**{title}**\n\n*{current_line}*"

        await msg.edit(embed=embed)
        await asyncio.sleep(1)

# Fetch synced lyrics from lrclib
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

# Queue system
async def play_next(guild_id):
    if guild_id not in song_queues or not song_queues[guild_id]:
        return

    query, interaction = song_queues[guild_id].popleft()

    try:
        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        url, title, thumb, duration = get_youtube_info(query)
        print(f"[YT URL] {title} - {url} ==> {duration}")
        source = await discord.FFmpegOpusAudio.from_probe(url, method='fallback', executable='./ffmpeg.bin')
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop))

        await interaction.followup.send(f"Now playing **{title}**!")

        raw_lrc = await fetch_lrc(title)
        lrc_data = parse_lrc(raw_lrc) if raw_lrc else []
        await send_now_playing(interaction, title, thumb, duration, lrc_data)

    except Exception as e:
        print(f"Error in play_next: {e}")
        await interaction.followup.send("Error while trying to play the song. Please try again.")
        await play_next(guild_id)

# Command for play
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
        await interaction.followup.send(f"Added **{query}** to the queue!")

# Command for pausing
@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("Paused playback!")

# Command for resuming
@bot.tree.command(name="resume", description="Resume playback")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("Resumed playback!")

# Command for skipping
@bot.tree.command(name="skip", description="Skip current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Skipped to the next song!")

# Command for showing the queue
@bot.tree.command(name="qlist", description="Show the current queue")
async def qlist(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in song_queues or not song_queues[guild_id]:
        await interaction.response.send_message("The queue is empty!")
        return

    queue_list = [f"**{index+1}.** {song[0]}" for index, song in enumerate(song_queues[guild_id])]
    queue_str = "\n".join(queue_list)

    await interaction.response.send_message(f"Current Queue:\n{queue_str}")

# Command for clearing the queue
@bot.tree.command(name="clear", description="Clear the queue")
async def clear(interaction: discord.Interaction):
    song_queues[interaction.guild.id] = deque()
    await interaction.response.send_message("Queue cleared!")

# Command for stopping playback and disconnecting
@bot.tree.command(name="stop", description="Stop the current song and clear the queue")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Playback stopped!")
    song_queues[interaction.guild.id] = deque()

# Command to end the music and disconnect from the channel
@bot.tree.command(name="end", description="Stop music, clear queue, and leave the channel")
async def end(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    song_queues[interaction.guild.id] = deque()
    if vc:
        await vc.disconnect()
    await interaction.response.send_message("Music stopped and bot disconnected.")

# Event when bot is ready
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot is ready as {bot.user}")

# Run the bot
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(DISCORD_TOKEN)