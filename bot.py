import discord
from discord.ext import commands
import yt_dlp
import asyncio
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import aiohttp
import os

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
intents.members = True  # Only if you're using member events

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

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
    embed = discord.Embed(title="Now Playing", description=title, color=0x1DB954)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.set_footer(text="© Source: YouTube")
    embed.add_field(name="Progress", value=f"`00:00 / {format_duration(duration)}`")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("⏯")
    await msg.add_reaction("⏭")
    await msg.add_reaction("🔁")

@bot.command()
async def play(ctx, *, args):
    if not ctx.author.voice:
        return await ctx.send("You're not in a voice channel.")

    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()

    is_playlist = args.startswith("-p ")
    query = args[3:] if is_playlist else args

    # Spotify detection
    if "spotify.com" in query:
        queries = parse_spotify_url(query)
    else:
        queries = [(query,)]

    queues.setdefault(ctx.guild.id, {"queue": [], "loop": False})
    for q in queries:
        queues[ctx.guild.id]['queue'].append(q[0])

    if not ctx.voice_client.is_playing():
        await play_next(ctx)
    else:
        await ctx.send(f"Queued {len(queries)} track(s).")

@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused.")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        queues.pop(ctx.guild.id, None)
        await ctx.send("Stopped and disconnected.")

@bot.command()
async def loop(ctx):
    g = ctx.guild.id
    queues.setdefault(g, {"queue": [], "loop": False})
    queues[g]['loop'] = not queues[g]['loop']
    await ctx.send("Looping is now " + ("enabled" if queues[g]['loop'] else "disabled"))

@bot.command()
async def enqueue(ctx, *, query):
    queues.setdefault(ctx.guild.id, {"queue": [], "loop": False})
    queues[ctx.guild.id]['queue'].append(query)
    await ctx.send("Added to queue.")

@bot.command()
async def clear(ctx):
    queues[ctx.guild.id]['queue'].clear()
    await ctx.send("Queue cleared.")

@bot.command()
async def lyrics(ctx):
    # Placeholder for future integration with lrclib + translation + romanization
    await ctx.send("Lyrics feature coming soon with translation, romanization, and synced display!")

def format_duration(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m):02}:{int(s):02}"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(DISCORD_TOKEN)
