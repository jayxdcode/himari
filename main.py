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

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

song_queues = {}

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Alt messages ---
RESPONSES = {
    "play": [
        "Yay~ Now playing: **{title}**! Enjoy the vibes!",
        "Teehee~ I queued up **{title}** just for you!",
        "Here comes **{title}**! Let’s jam together!",
        "Spinning up **{title}**~ Hope it makes you smile!",
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
					m, s = map(float, timestamp.split(":"))
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
			async with session.get(
			    f"https://lrclib.net/api/get?track_id={track_id}") as lrc_resp:
				lrc_json = await lrc_resp.json()
				return lrc_json.get("syncedLyrics")


def get_youtube_info(query: str):
    """
    Fetches information about a YouTube video.
    Accepts a direct URL or a search query.
    Does NOT support playlists.
    Returns a dictionary with URL, title, thumbnail, and duration.
    """

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

async def send_now_playing(interaction, title, thumb, duration, lrc_data):
	embed = discord.Embed(title="**Now Playing**", description=f"**{title}**", color=0xff99cc)
	if thumb:
		embed.set_thumbnail(url=thumb)
	embed.add_field(name="Progress",
	                value=f"`00:00 / {format_duration(duration)}`",
	                inline=False)
	msg = await interaction.followup.send(embed=embed)
	await msg.add_reaction("⏯")
	await msg.add_reaction("⏭")
	await msg.add_reaction("🔁")

	start = time.time()
	current_line = ""
	while interaction.guild.voice_client and interaction.guild.voice_client.is_playing(
	):
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

async def play_next(guild_id):
	if guild_id not in song_queues or not song_queues[guild_id]:
		return

	query, interaction = song_queues[guild_id].popleft()

	try:
		vc = interaction.guild.voice_client
		if not vc:
			vc = await interaction.user.voice.channel.connect()

		url, title, thumb, duration = get_youtube_info(query)
		source = await discord.FFmpegOpusAudio.from_probe(url,
		                                                  method='fallback',
		                                                  executable='./ffmpeg.bin')
		vc.play(source,
		        after=lambda e: asyncio.run_coroutine_threadsafe(
		            play_next(guild_id), bot.loop))

		await interaction.followup.send(get_response("play", title=title))

		raw_lrc = await fetch_lrc(title)
		lrc_data = parse_lrc(raw_lrc) if raw_lrc else []
		await send_now_playing(interaction, title, thumb, duration, lrc_data)
	except Exception as e:
		print(f"Error in play_next: {e}")
		await interaction.followup.send("Uwah~ I tried all I could, but I couldn't play that song... Sniffle~ Try another one, okay?")
		await play_next(guild_id)  # Try the next song

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
	await interaction.followup.send(f"Added **{query}** to the queue!")


@bot.tree.command(name="clear", description="Clear the queue")
async def clear(interaction: discord.Interaction):
	song_queues[interaction.guild.id] = deque()
	await interaction.response.send_message("Queue cleared~")


@bot.tree.command(name="stop", description="Stop current song")
async def stop(interaction: discord.Interaction):
	vc = interaction.guild.voice_client
	if vc and vc.is_playing():
		vc.stop()
		await interaction.response.send_message(get_response("stop"))
	else:
		await interaction.response.send_message("Nothing is playing right now~")


@bot.tree.command(
    name="end",
    description="Stop music, and clear queue, leave voice channel~~")
async def end(interaction: discord.Interaction):
	vc = interaction.guild.voice_client
	song_queues[interaction.guild.id] = deque()
	if vc:
		await vc.disconnect()
	await interaction.response.send_message(get_response("end"))


@bot.event
async def on_ready():
	await bot.tree.sync()
	print(f"Himari is ready~ as {bot.user}")


# --- Start everything ---
bot.run(DISCORD_TOKEN)