import discord
from discord.ext import commands
import yt_dlp
import asyncio
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
from discord import app_commands
import threading
from flask import Flask

# === DUMMY WEB SERVER TO KEEP RENDER ALIVE ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Discord bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Start the dummy server in a separate thread
threading.Thread(target=run_web).start()

# === YOUR DISCORD BOT SETUP ===
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)  # Using discord.Bot for slash commands

queues = {}

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

def get_youtube_url(query):
    ytdl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'default_search': 'ytsearch1',
        'noplaylist': True,
    }
    with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        video = info['entries'][0] if 'entries' in info else info
        return video['url'], video.get('title', 'Unknown'), video.get('thumbnail', None), video.get('duration', 0)

def parse_spotify_url(url):
    if "track" in url:
        track = sp.track(url)
        return [(f"{track['name']} by {track['artists'][0]['name']}",)]
    elif "playlist" in url:
        tracks = sp.playlist_tracks(url)
        return [(f"{t['track']['name']} by {t['track']['artists'][0]['name']}",) for t in tracks['items']]
    return []

async def play_next(ctx):
    queue = queues.get(ctx.guild.id, {}).get("queue", [])
    if not queue:
        await ctx.voice_client.disconnect()
        return

    song = queue.pop(0)
    url, title, thumb, duration = get_youtube_url(song)
    source = await discord.FFmpegOpusAudio.from_probe(url, method='fallback')
    vc = ctx.voice_client
    vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))

    queues[ctx.guild.id]['now_playing'] = {
        "title": title, "url": url, "thumb": thumb, "duration": duration
    }
    await send_now_playing(ctx, title, thumb, duration)

async def send_now_playing(ctx, title, thumb, duration):
    embed = discord.Embed(title="**Now Playing**", description=f"**{title}**", color=0x1DB954)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.set_footer(text="© Source: YouTube")
    embed.add_field(name="Progress", value=f"`00:00 / {format_duration(duration)}`")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("⏯")
    await msg.add_reaction("⏭")
    await msg.add_reaction("🔁")

def format_duration(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m):02}:{int(s):02}"

# === Slash Command Definitions ===

@bot.tree.command(name="play", description="Play a song or playlist")
@app_commands.describe(args="Search term or URL")
async def play(interaction: discord.Interaction, args: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("You're not in a voice channel.", ephemeral=True)

    if interaction.guild.voice_client is None:
        await interaction.user.voice.channel.connect()

    is_playlist = args.startswith("-p ")
    query = args[3:] if is_playlist else args

    # Spotify detection
    if "spotify.com" in query:
        queries = parse_spotify_url(query)
    else:
        queries = [(query,)]

    queues.setdefault(interaction.guild.id, {"queue": [], "loop": False})
    for q in queries:
        queues[interaction.guild.id]['queue'].append(q[0])

    if not interaction.guild.voice_client.is_playing():
        await play_next(interaction)
    else:
        await interaction.response.send_message(f"Queued {len(queries)} track(s).")

@bot.tree.command(name="pause", description="Pause the current track")
async def pause(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.response.send_message("Paused.")

@bot.tree.command(name="stop", description="Stop the current track and disconnect")
async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        queues.pop(interaction.guild.id, None)
        await interaction.response.send_message("Stopped and disconnected.")

@bot.tree.command(name="loop", description="Toggle looping of the queue")
async def loop(interaction: discord.Interaction):
    g = interaction.guild.id
    queues.setdefault(g, {"queue": [], "loop": False})
    queues[g]['loop'] = not queues[g]['loop']
    await interaction.response.send_message("Looping is now " + ("enabled" if queues[g]['loop'] else "disabled"))

@bot.tree.command(name="enqueue", description="Add a song to the queue")
async def enqueue(interaction: discord.Interaction, query: str):
    queues.setdefault(interaction.guild.id, {"queue": [], "loop": False})
    queues[interaction.guild.id]['queue'].append(query)
    await interaction.response.send_message(f"Added {query} to queue.")

@bot.tree.command(name="clear", description="Clear the queue")
async def clear(interaction: discord.Interaction):
    queues[interaction.guild.id]['queue'].clear()
    await interaction.response.send_message("Queue cleared.")

@bot.tree.command(name="lyrics", description="Get the lyrics of the current song")
async def lyrics(interaction: discord.Interaction):
    # Placeholder for future integration with lrclib + translation + romanization
    await interaction.response.send_message("Lyrics feature coming soon with translation, romanization, and synced display!")

# === Syncing Commands ===
@bot.event
async def on_ready():
    await bot.tree.sync()  # Sync slash commands when bot is ready
    print(f"Logged in as {bot.user}")

# === Start the bot ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(DISCORD_TOKEN)
