import os
import discord
from discord.ext import commands
from discord.ext import tasks
import datetime
from discord import app_commands
import yt_dlp
import asyncio
import random
import time
import aiohttp
from collections import deque
import logging
from ytmusicapi import YTMusic
from keep_alive import keep_alive
#from  import load_dotenv
from dataclasses import dataclass
from lrclib import LrcLibAPI
from typing import Optional, Tuple, Union, List 

# ----------------- Configuration -----------------

# Keep-alive (Flask) server setup  [uncomment 13 and 22 if local]
keep_alive()

# Load environment variables [uncomment 25~27 if local]
#dotenv_path = '../.env'
#if os.path.exists(dotenv_path):
#    load_dotenv(dotenv_path=dotenv_path)
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Ensure ffmpeg is installed/available in PATH (for mobile, adapt as needed) 
# os.system('pkg install ffmpeg -y')

# -------------------------------------------------

# Initialize  APIs
ytmusic = YTMusic()
_api = LrcLibAPI(user_agent="jayxdcode.Himari/0.0.5")

# ————— Uptime globals —————
startup_time = datetime.datetime.utcnow()
total_runtime = datetime.timedelta(hours=3, minutes=55)
announcement_sent = False
general_channel = None

# Per-guild data stores
song_queues   = {}  # guild_id -> deque of (url, title, artist, album, thumb, dur, interaction, secret)
play_history  = {}  # guild_id -> deque of (url, title, artist, album, thumb, dur, interaction, secret)
auto_play     = {}  # guild_id -> bool

@dataclass
class Track:
    stream_url: str
    title: str
    artist: str
    album: str
    thumbnail: str
    duration: float
    secret: bool

# Bot intents and setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds          = True
intents.members         = True
intents.voice_states    = True

bot = commands.Bot(command_prefix='/', intents=intents)

# ------------------ Responses ------------------

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
    "stop": [
        "oki~ i stopped it :)",
        "Something's playing and you asked me to stop it.",
        "stopped (you told me)",
        "You're reading this? This means you asked me to stop the track :/",
    ],
    "end": [
        "Music’s all done~ That was fun!",
        "All stopped~ Hope you liked it!",
        "I’ve stopped the tunes for now~",
        "That’s a wrap~ Let me know if you want more!",
        "ggs, imma sleep now. bye!",
    ],
    "enqueue": [
        "Queued up **{title}**! Let's get this party started~",
        "Your track **{title}** is now in line~",
        "**{title}** has joined the party!",
        "**{title}** has reached the queue! Stay tuned!",
    ],
    "secret_enqueue": [
        "Hehe~ **{title}** is a secret queue. Shh~",
        "Top secret track **{title}** has been tucked away~",
        "Your private tune **{title}** has been hidden till its time~",
    ],
    "notify_secret_enqueue": [
        "Added a hidden song :)",
        "A secret has entered the queue!",
        "stay tuned for a secret LoL",
    ],
    "queue_secret": [
        "🔒 Secret track at position **{pos}**. Patience!",
        "🤫 There's something secret at **{pos}**. Just wait!",
        "❔ Hidden song number **{pos}** — suspense!",
    ],
}

def get_response(key, **kwargs):
    return random.choice(RESPONSES[key]).format(**kwargs)

def format_duration(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02}:{s:02}"

# ---------------- Lyrics Fetching ----------------

async def fetch_lrc(
    title: str,
    artist: str,
    album: Optional[str],
    duration: int,
    *,
    mode: str = "synced",       # "synced" | "plain" | "both"
    translate: bool = False,
    romanize: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (plain_lyrics, synced_lyrics) according to mode.
    """
    loop = asyncio.get_event_loop()

    # Helper to call the four-arg API synchronously
    def _call_api(t, a, d, alb):
        return _api.get_lyrics(
            track_name=t,
            artist_name=a,
            duration=d,
            album_name=alb
        )

    # Post-process hook
    def _post(txt: str) -> str:
        out = txt
        if translate:
            out = your_translate_function(out)
        if romanize:
            out = your_romanize_function(out)
        return out

    # 1) If we have an album, go straight to fetch_lyrics(...)
    if album:
        res = await loop.run_in_executor(None, _call_api,
                                         title, artist, duration, album)

    else:
        # 2) No album: search by title+artist
        async with aiohttp.ClientSession() as session:
            params = {"q": f"{title} {artist}"}
            async with session.get("https://lrclib.net/api/search", params=params) as r:
                if r.status != 200:
                    return None, None
                candidates = await r.json(content_type=None)

        # pick one within ±3 s
        best = next(
            (c for c in candidates
             if abs(c.get("duration", duration) - duration) <= 3),
            None
        ) or (candidates[0] if candidates else None)

        if not best:
            return None, None

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://lrclib.net/api/get",
                params={"track_id": best["id"]}
            ) as r2:
                if r2.status != 200:
                    return None, None
                j = await r2.json(content_type=None)

        # wrap into a dummy result
        class R:
            plain_lyrics  = j.get("plainLyrics")
            synced_lyrics = j.get("syncedLyrics")
        res = R()

    # 3) Extract
    raw_plain  = getattr(res, "plain_lyrics", None)
    raw_synced = getattr(res, "synced_lyrics", None)

    # 4) Apply mode + post-processing
    plain  = _post(raw_plain)  if raw_plain  and mode in ("plain","both")  else None
    synced = _post(raw_synced) if raw_synced and mode in ("synced","both") else None

    # 5) If they only wanted synced but none found, fall back to plain
    if mode == "synced" and not synced:
        return plain, None

    return plain, synced


def parse_lrc(raw: Union[str, list]) -> List[Tuple[float, str]]:
    """
    Turn either a plain LRC string or synced list into [(timestamp, word), ...].
    """
    parsed = []
    if isinstance(raw, list):
        for e in raw:
            ts = float(e.get("timestamp", 0))
            wr = e.get("word") or e.get("words", "")
            parsed.append((ts, wr))
    else:
        for line in raw.splitlines():
            if not line.startswith('['):
                continue
            parts = line.split(']')
            for tag in parts[:-1]:
                t = tag.strip('[]')
                try:
                    m, s = map(float, t.split(':'))
                    parsed.append((m*60 + s, parts[-1].strip()))
                except:
                    pass
    return sorted(parsed, key=lambda x: x[0])
    
#  >>> unified lrc call <<<

async def fetch_and_parse_lrc(track: Track, mode="synced") -> List[Tuple[float, str]]:
    #fetch plain and synced
    splitted_title = track.title.split(' - ')
    print()
    print('#####')
    print(splitted_title)
    print('#####')
    print()
    if len(splitted_title) == 2:
        safe_title = splitted_title[1]
    elif len(splitted_title) == 1:
        safe_title = track.title
    else:
        raise ValueError(f'Processing `{track.title}` resulted into {len(splitted_title)} split parts. Perhaps the title has more than 1 ` - `?')
    
    plain, synced = await fetch_lrc(safe_title, track.artist, track.album, track.duration, mode=mode)
    raw = synced or plain or ''
    return parse_lrc(raw)

# ---------------- Track Fetching ----------------

async def fetch_track_info(query: str) -> Track:
    loop = asyncio.get_event_loop()
    def search():
        results = ytmusic.search(query, filter='songs', limit=1)
        if not results:
            return None
        item = results[0]
        duration = item.get('duration') or '0:00'
        secs = sum(x * int(t) for x, t in zip([60,1], duration.split(':')))
        return Track(
            stream_url=f"https://www.youtube.com/watch?v={item['videoId']}",
            title=item['title'],
            artist=item['artists'][0]['name'] if item.get('artists') else 'Unknown',
            album=item['album']['name'] if item.get('album') else '',
            thumbnail=item.get('thumbnail'),
            duration=secs,
            secret=False
        )
    track = await loop.run_in_executor(None, search)
    if not track:
        raise ValueError("Track not found")
    # get direct audio
    opts = {
            'format': 'bestaudio/best',
            'cookiefile': './cf.txt',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'ytsearch',
            'http_headers': {
               'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
               'Accept-Language': 'en-US,en;q=0.9',
               'Referer': 'https://www.youtube.com/',
               'Connection': 'keep-alive',
            },
        }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.stream_url, download=False)
    track.stream_url = info['url']
    track.thumbnail= info['thumbnail']
    return track

# --- Uptime notifs logic ---
@tasks.loop(minutes=30)
async def update_time_left():
    if not general_channel:
        return

    elapsed   = datetime.datetime.utcnow() - startup_time
    remaining = total_runtime - elapsed

    if remaining <= datetime.timedelta(seconds=0):
        await shutdown()
        return

    if remaining <= datetime.timedelta(minutes=5):
        await general_channel.send(
            "I'm about to sleep in 5 minutes. want me to be here for longer? pay up! jk hehe~"
        )
    else:
        h, rem = divmod(remaining.seconds, 3600)
        m = rem // 60
        await general_channel.send(f"Himari’s time left: **{h}h {m}m**")


async def shutdown():
    if general_channel:
        await general_channel.send("My time's up... See you again next boot~")
    await bot.close()
    
# ------------- Playback Controls -------------

@dataclass
class ControlsView(discord.ui.View):
    vc: discord.VoiceClient
    guild_id: int
    start_time: float = None
    paused_time: float = 0.0

    def __post_init__(self):
        super().__init__(timeout=None)
        self.start_time = time.time()
        self.paused_time = 0.0

    @discord.ui.button(label='⏯ Pause/Resume', style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc.is_playing():
            self.vc.pause()
            self.paused_time = time.time() - self.start_time
        elif self.vc.is_paused():
            self.vc.resume()
            self.start_time = time.time() - self.paused_time
        await interaction.response.defer()

    @discord.ui.button(label='⏭ Next', style=discord.ButtonStyle.danger)
    async def nxt(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.vc.stop()
        await interaction.response.defer()

async def send_now_playing(interaction, track: Track):
    embed = discord.Embed(title='Now Playing', description=f"**{track.artist}** - **{track.title}**", color=0xff99cc)
    # embed.set_thumbnail(url="https://uxwing.com/wp-content/themes/uxwing/download/brands-and-social-media/youtube-music-icon.png")
    
    if track.thumbnail:
        embed.set_image(url=track.thumbnail)
    embed.add_field(name='Progress', value=f'`00:00 / {format_duration(track.duration)}`', inline=False)
    embed.add_field(name='Lyrics', value='Loading lyrics...', inline=False)
    view = ControlsView(interaction.guild.voice_client, interaction.guild.id)
    msg = await interaction.followup.send(embed=embed, view=view)

    lrc = await fetch_and_parse_lrc(track) or [('0', 'No lyrics available. I think the track\' instrumental. LoL')]
    idx = 0
    start_time = time.time()
    while interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        elapsed = interaction.guild.voice_client.is_paused() and view.paused_time or time.time() - start_time
        embed.set_field_at(0, name='Progress', value=f'`{format_duration(elapsed)} / {format_duration(track.duration)}`')
        if lrc:
            while idx + 1 < len(lrc) and elapsed - 0.25 >= lrc[idx+1][0]:
                idx += 1
            prev_line = lrc[idx-1][1] if idx > 0 else '♪'
            curr_line = lrc[idx][1] if lrc else '♪'
            next_line = lrc[idx+1][1] if idx+1 < len(lrc) else '♪'
            embed.set_field_at(1, name='Lyrics', value=f"Powered by **LrcLib**\n {prev_line}\n> **{curr_line}**\n {next_line}")
        try:
            await msg.edit(embed=embed)
        except discord.errors.HTTPException:
            pass
        await asyncio.sleep(0.2)

@bot.tree.command(name='play', description='Play or enqueue a song')
@app_commands.describe(query='Search term or YouTube URL', secret='Queue privately')
async def play_cmd(interaction: discord.Interaction, query: str, secret: bool = False):
    # 1) Pre-check: only response here
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message(
            'Join a voice channel first!', ephemeral=True
        )

    # 2) Acknowledge interaction (thinking)
    await interaction.response.defer(thinking=True)

    # 3) Fetch track metadata (followups from here)
    try:
        track = await fetch_track_info(query)
        track.secret = secret
    except Exception:
        return await interaction.followup.send(
            'Could not find track.', ephemeral=True
        )

    # 4) Enqueue logic
    dq = song_queues.setdefault(interaction.guild.id, deque())
    was_empty = not dq and not interaction.guild.voice_client
    dq.append(track)

    if was_empty:
        auto_play[interaction.guild.id] = True
        asyncio.create_task(play_next(interaction.guild.id, interaction))
    else:
        if secret:
            await interaction.followup.send(get_response('notify_secret_enqueue'))
            await interaction.followup.send(
                get_response('secret_enqueue', title=track.title),
                ephemeral=True
            )
        else:
            await interaction.followup.send(get_response('enqueue', title=track.title))

async def play_next(gid: int, ctx_interaction: discord.Interaction):
    dq = song_queues.get(gid)
    if not dq or not auto_play.get(gid):
        return
    track = dq.popleft()
    vc = ctx_interaction.guild.voice_client or await ctx_interaction.user.voice.channel.connect()
    play_history.setdefault(gid, deque(maxlen=50)).append(track)

    source = await discord.FFmpegOpusAudio.from_probe(
        track.stream_url,
        #executable='./ffmpeg',
        before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        options='-vn -sn -dn -c:a libopus -b:a 320k -vbr on -bufsize 512k'
    )
    await ctx_interaction.followup.send(get_response('play', title=track.title))
    send_task = asyncio.create_task(send_now_playing(ctx_interaction, track))
    vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(gid, ctx_interaction), bot.loop))
    await send_task

@bot.tree.command(name='pause', description='Pause playback')
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        return await interaction.response.send_message(get_response('pause'), ephemeral=True)
    vc.pause()
    await interaction.response.send_message(get_response('pause'), ephemeral=True)

@bot.tree.command(name='resume', description='Resume playback')
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_paused():
        return await interaction.response.send_message(get_response('resume'), ephemeral=True)
    vc.resume()
    await interaction.response.send_message(get_response('resume'), ephemeral=True)

#@bot.tree.command(name='skip', description='Skip the current song')
#async def skip(interaction: discord.Interaction):
#    vc = interaction.guild.voice_client
#    if not vc or not vc.is_playing():
#        return await interaction.response.send_message(get_response('skip'), ephemeral=True)
#    vc.stop()
#    await interaction.response.send_message(get_response('skip'), ephemeral=True)
    
#@bot.tree.command(name="enqueue", description="Add a song to the queue")
#@app_commands.describe(query="Search or link", secret="Queue privately (secret)")
#async def enqueue(interaction: discord.Interaction, query: str, secret: bool = False):
#    gid = interaction.guild.id
#    dq = song_queues.setdefault(gid, deque())
#    dq.append((None, query, None, None, None, None, interaction, secret))
#    if secret:
#        await interaction.response.send_message(
#            get_response("notify_secret_enqueue")
#        )
#        await interaction.response.send_message(
#            get_response("secret_enqueue", title=query),
#            ephemeral=True
#        )
#    else:
#        await interaction.response.send_message(
#            get_response("enqueue", title=query)
#        )

@bot.tree.command(name='queue', description='Show the queue')
async def queue_list(interaction: discord.Interaction):
    gid = interaction.guild.id
    dq  = song_queues.get(gid, deque())
    if not dq:
        return await interaction.response.send_message('Queue is empty.', ephemeral=True)

    # Safely index the title from each tuple:
    desc = '\n'.join(f"{i+1}. {track[1]}" for i, track in enumerate(dq))
    embed = discord.Embed(title='Up Next!', description=desc, color=0x00ff00)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='clear', description='Clear the queue')
async def clear(interaction: discord.Interaction):
    song_queues.get(interaction.guild.id, deque()).clear()
    await interaction.response.send_message(get_response('end'), ephemeral=True)

@bot.tree.command(name='end', description='Stop playback and leave')
async def end(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
    song_queues.get(interaction.guild.id, deque()).clear()
    await interaction.response.send_message(get_response('end'), ephemeral=True)
    
@bot.tree.command(name='status', description='Check remaining server runtime (ʜɪᴍᴀʀɪ)')
async def status(interaction: discord.Interaction):
    elapsed   = datetime.datetime.utcnow() - startup_time
    remaining = total_runtime - elapsed
    if remaining <= datetime.timedelta(seconds=0):
        await interaction.response.send_message("I'm already out of time! See you later~")
    else:
        h, rem = divmod(remaining.seconds, 3600)
        m = rem // 60
        await interaction.response.send_message(f"I’ve got **{h}h {m}m** left~")
# ----------------- Events -----------------

@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

    global announcement_sent, general_channel

    # (after await bot.tree.sync() and logger)
    # pick a “general” channel
    guild = bot.guilds[0]
    for ch in guild.text_channels:
        if "general" in ch.name.lower():
            general_channel = ch
            break
    if not general_channel and guild.text_channels:
        general_channel = guild.text_channels[0]

    # check for prior announcement
    if general_channel:
        async for msg in general_channel.history(limit=100):
            if msg.author == bot.user and msg.embeds:
                if msg.embeds[0].title.startswith("ʜɪᴍᴀʀɪ is back"):
                    announcement_sent = True
                    break
        # send initial announcement
        if not announcement_sent:
            embed = discord.Embed(
                title="ʜɪᴍᴀʀɪ is back — for now!",
                description=(
                    "Hiyaa~\n\n"
                    "I’m awake for the next **3 hours, 55 minutes** and ready to help! …\n"
                    "Run `/remaining` anytime to see how much time I’ve got left.\n\n"
                    "Let’s make the most of it, okay~?\n\n\n"
                    "`Build info (for debugging): jayxdcode/ʜɪᴍᴀʀɪ version 0.0.5 (Docker Image release, keys=BETA-unstable-5, arch='Linux AMD64', imageversion=latest)`"
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="© jayxcode")
            await general_channel.send(embed=embed)
            announcement_sent = True

        update_time_left.start()

# ----------------- Run Bot -----------------


bot.run(DISCORD_TOKEN)

