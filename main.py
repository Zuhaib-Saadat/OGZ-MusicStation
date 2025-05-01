import discord
from discord.ext import commands
import asyncio
import yt_dlp
from yt_dlp.utils import DownloadError
import os
from dotenv import load_dotenv

# ─── LOAD ENV ──────────────────────────────────────────────────────────────────
load_dotenv()                     # reads .env into os.environ
TOKEN = os.environ.get('DISCORD_TOKEN')
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN is missing in environment!")
    exit(1)
# ────────────────────────────────────────────────────────────────────────────────

PREFIX     = '!'
SONGS_FILE = 'songs.txt'   # pre-made playlist file

# yt_dlp options
YTDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}
# FFmpeg options
FFMPEG_OPTS = {
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 '
        '-reconnect_delay_max 5 -re -nostdin'
    ),
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)
bot.remove_command('help')

# guild_id → queue, last playlist memory per guild
queues = {}
last_playlists = {}

def get_queue(ctx):
    return queues.setdefault(ctx.guild.id, [])


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        query = url
        if not any(url.startswith(proto) for proto in ('http://', 'https://', 'ytsearch:')):
            query = f"ytsearch:{url}"
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(query, download=not stream)
        )
        if 'entries' in data:
            data = data['entries'][0]
        stream_url = data['url'] if stream else ytdl.prepare_filename(data)
        source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTS)
        return cls(source, data=data)


@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Game(name="OGZ MusicStation"))


async def ensure_voice(ctx):
    author_vc = ctx.author.voice.channel if ctx.author.voice else None
    if not author_vc:
        await ctx.send("❌ You’re not in a voice channel.")
        raise commands.CommandError("Author not in VC")
    vc = ctx.voice_client
    if vc is None or not getattr(vc, 'is_connected', lambda: False)():
        await author_vc.connect()
    elif vc.channel != author_vc:
        await vc.move_to(author_vc)


async def play_next(ctx):
    queue = get_queue(ctx)
    if not queue:
        await ctx.send("⏹ Queue is empty.")
        return
    source = queue.pop(0)
    ctx.voice_client.play(
        source,
        after=lambda e: bot.loop.create_task(play_next(ctx))
    )
    await ctx.send(f"🎶 Now playing: **{source.title}**")


# ─── COMMANDS ──────────────────────────────────────────────────────────────────

@bot.command(name='join')
async def join(ctx):
    await ensure_voice(ctx)
    await ctx.send("🔊 Joined your voice channel.")
    path = os.path.join(os.path.dirname(__file__), SONGS_FILE)
    if not os.path.isfile(path):
        return await ctx.send(f"❌ `{SONGS_FILE}` not found.")
    entries = [l.strip() for l in open(path, encoding='utf-8') if l.strip()]
    last_playlists[ctx.guild.id] = entries.copy()
    if not entries:
        return await ctx.send(f"⚠️ `{SONGS_FILE}` is empty.")
    q = get_queue(ctx)
    skipped = []
    for qstr in entries:
        try:
            src = await YTDLSource.from_url(qstr, loop=bot.loop)
            q.append(src)
        except DownloadError:
            skipped.append(qstr)
    await ctx.send(f"📥 Loaded **{len(q)}** tracks; skipped **{len(skipped)}**.")
    if skipped:
        await ctx.send("⚠️ Skipped: " + ", ".join(skipped))
    if not ctx.voice_client.is_playing():
        await play_next(ctx)


@bot.command(name='load')
async def load(ctx):
    await ensure_voice(ctx)
    vc = ctx.voice_client
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    queues[ctx.guild.id] = []
    path = os.path.join(os.path.dirname(__file__), SONGS_FILE)
    if not os.path.isfile(path):
        return await ctx.send(f"❌ `{SONGS_FILE}` not found.")
    entries = [l.strip() for l in open(path, encoding='utf-8') if l.strip()]
    last_playlists[ctx.guild.id] = entries.copy()
    if not entries:
        return await ctx.send(f"⚠️ `{SONGS_FILE}` is empty.")
    q = get_queue(ctx)
    skipped = []
    for qstr in entries:
        try:
            src = await YTDLSource.from_url(qstr, loop=bot.loop)
            q.append(src)
        except DownloadError:
            skipped.append(qstr)
    await ctx.send(f"🔄 Reloaded **{len(q)}** tracks; skipped **{len(skipped)}**.")
    if skipped:
        await ctx.send("⚠️ Skipped: " + ", ".join(skipped))
    if not ctx.voice_client.is_playing():
        await play_next(ctx)


@bot.command(name='reload')
async def reload_memory(ctx):
    await ensure_voice(ctx)
    entries = last_playlists.get(ctx.guild.id)
    if not entries:
        return await ctx.send("❌ No playlist in memory. Use !load or !join first.")
    vc = ctx.voice_client
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    queues[ctx.guild.id] = []
    skipped = []
    for qstr in entries:
        try:
            src = await YTDLSource.from_url(qstr, loop=bot.loop)
            queues[ctx.guild.id].append(src)
        except DownloadError:
            skipped.append(qstr)
    await ctx.send(f"🔄 Reloaded memory playlist; skipped **{len(skipped)}** of **{len(entries)}**.")
    if skipped:
        await ctx.send("⚠️ Skipped: " + ", ".join(skipped))
    if not ctx.voice_client.is_playing():
        await play_next(ctx)


@bot.command(name='play')
async def play_cmd(ctx, *, query: str):
    await ensure_voice(ctx)
    try:
        src = await YTDLSource.from_url(query, loop=bot.loop)
    except DownloadError:
        return await ctx.send("❌ Couldn’t fetch that track (age-gated or login required).")
    q = get_queue(ctx)
    q.append(src)
    await ctx.send(f"➕ Added to queue: **{src.title}**")
    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command(name='upload')
async def upload(ctx):
    await ensure_voice(ctx)
    attachments = ctx.message.attachments
    if not attachments:
        return await ctx.send("❌ Please attach a .txt file.")
    attachment = attachments[0]
    if not attachment.filename.endswith('.txt'):
        return await ctx.send("❌ Unsupported file type. Please upload .txt.")
    text = (await attachment.read()).decode('utf-8', errors='ignore')
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 15:
        return await ctx.send("❌ Playlist too long (max 15 songs).")
    queues[ctx.guild.id] = []
    skipped = []
    for qstr in lines:
        try:
            src = await YTDLSource.from_url(qstr, loop=bot.loop)
            queues[ctx.guild.id].append(src)
        except DownloadError:
            skipped.append(qstr)
    await ctx.send(f"📥 Loaded **{len(queues[ctx.guild.id])}/{len(lines)}** tracks.")
    if skipped:
        await ctx.send("⚠️ Skipped: " + ", ".join(skipped))
    if not ctx.voice_client.is_playing():
        await play_next(ctx)


# (rest of your simple commands: pause, resume, skip, stop, exit, queue, leave, kiss-me, help...)

@bot.command(name='pause')
async def pause(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Paused.")
    else:
        await ctx.send("❌ Nothing is playing.")

# … your other commands here …

@bot.command(name='help')
async def help_command(ctx):
    help_text = (
        "**Music Bot Commands**\n"
        "`!join`    - Join & load songs.txt\n"
        "`!load`    - Clear & reload songs.txt\n"
        "`!reload`  - Reload last playlist memory\n"
        "`!play`    - Queue a song (URL/search)\n"
        "`!pause`   - Pause current song\n"
        "`!resume`  - Resume paused song\n"
        "`!skip`    - Skip current song\n"
        "`!stop`    - Stop & clear queue\n"
        "`!exit`    - Disconnect bot\n"
        "`!queue`   - Show current queue\n"
        "`!leave`   - Leave voice channel\n"
        "`!kiss-me` - Surprise!\n"
        "`!upload`  - Upload .txt playlist (max 15)\n"
        "`!help`    - Show this message"
    )
    await ctx.send(help_text)


if __name__ == '__main__':
    bot.run(TOKEN)
