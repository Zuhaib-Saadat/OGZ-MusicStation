import discord
from discord.ext import commands
import asyncio
import yt_dlp
import os
from dotenv import load_dotenv
import os

load_dotenv()                  # reads from .env into os.environ

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN = os.environ['DISCORD_TOKEN']
PREFIX      = '!'
SONGS_FILE  = 'songs.txt'   # pre-made playlist file
# ────────────────────────────────────────────────────────────────────────────────

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
        """
        If `url` doesn't start with a URL scheme, treat it as a YouTube search term.
        """
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
    """Ensure bot is connected to author's VC."""
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
    """Play next track without auto-disconnect."""
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
    """Join VC and preload songs.txt."""
    await ensure_voice(ctx)
    await ctx.send("🔊 Joined your voice channel.")
    path = os.path.join(os.path.dirname(__file__), SONGS_FILE)
    if os.path.isfile(path):
        entries = [l.strip() for l in open(path, encoding='utf-8') if l.strip()]
        last_playlists[ctx.guild.id] = entries.copy()
        if entries:
            q = get_queue(ctx)
            for qstr in entries:
                q.append(await YTDLSource.from_url(qstr, loop=bot.loop))
            await ctx.send(f"📥 Loaded **{len(entries)}** tracks from `{SONGS_FILE}`.")
            if not ctx.voice_client.is_playing():
                await play_next(ctx)
        else:
            await ctx.send(f"⚠️ `{SONGS_FILE}` is empty.")
    else:
        await ctx.send(f"❌ `{SONGS_FILE}` not found.")

@bot.command(name='load')
async def load(ctx):
    """Clear queue & reload songs.txt."""
    await ensure_voice(ctx)
    vc = ctx.voice_client
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    queues[ctx.guild.id] = []
    path = os.path.join(os.path.dirname(__file__), SONGS_FILE)
    if os.path.isfile(path):
        entries = [l.strip() for l in open(path, encoding='utf-8') if l.strip()]
        last_playlists[ctx.guild.id] = entries.copy()
        if entries:
            q = get_queue(ctx)
            for qstr in entries:
                q.append(await YTDLSource.from_url(qstr, loop=bot.loop))
            await ctx.send(f"🔄 Reloaded **{len(entries)}** tracks from `{SONGS_FILE}`.")
            if not ctx.voice_client.is_playing():
                await play_next(ctx)
        else:
            await ctx.send(f"⚠️ `{SONGS_FILE}` is empty.")
    else:
        await ctx.send(f"❌ `{SONGS_FILE}` not found.")

@bot.command(name='reload')
async def reload_memory(ctx):
    """Reload the last in-memory playlist."""
    await ensure_voice(ctx)
    entries = last_playlists.get(ctx.guild.id)
    if not entries:
        await ctx.send("❌ No playlist in memory. Use !load or !join first.")
        return
    vc = ctx.voice_client
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    queues[ctx.guild.id] = []
    skipped = []
    for qstr in entries:
        try:
            src = await YTDLSource.from_url(qstr, loop=bot.loop)
            queues[ctx.guild.id].append(src)
        except Exception:
            skipped.append(qstr)
    await ctx.send(f"🔄 Reloaded memory playlist; skipped {len(skipped)} of {len(entries)} entries.")
    if skipped:
        await ctx.send("⚠️ Skipped: " + ", ".join(skipped))
    if not ctx.voice_client.is_playing():
        await play_next(ctx)

@bot.command(name='play')
async def play_cmd(ctx, *, query: str):
    """Queue a song; start playback only if idle."""
    await ensure_voice(ctx)
    src = await YTDLSource.from_url(query, loop=bot.loop)
    q = get_queue(ctx)
    q.append(src)
    await ctx.send(f"➕ Added to queue: **{src.title}**")
    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)

@bot.command(name='pause')
async def pause(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Paused.")
    else:
        await ctx.send("❌ Nothing is playing to pause.")

@bot.command(name='resume')
async def resume(ctx):
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Resumed.")
    else:
        await ctx.send("❌ No track is paused.")

@bot.command(name='skip')
async def skip(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.send("⏭ Skipped the current track.")
    else:
        await ctx.send("❌ Nothing is playing right now.")

@bot.command(name='stop')
async def stop(ctx):
    """Stop playback and clear the queue, but remain in VC."""
    vc = ctx.voice_client
    if vc:
        queues.pop(ctx.guild.id, None)
        vc.stop()
        await ctx.send("⏹ Playback stopped and queue cleared.")
    else:
        await ctx.send("❌ I'm not in a voice channel.")

@bot.command(name='exit')
async def exit_cmd(ctx):
    """Disconnect the bot and clear state."""
    vc = ctx.voice_client
    if vc:
        await vc.disconnect()
        queues.pop(ctx.guild.id, None)
        last_playlists.pop(ctx.guild.id, None)
        await ctx.send("👋 Exiting and cleared all queues.")
    else:
        await ctx.send("❌ I'm not in a voice channel.")

@bot.command(name='queue')
async def show_queue(ctx):
    q = get_queue(ctx)
    if not q:
        return await ctx.send("📭 The queue is empty.")
    lines = [f"**{i+1}.** {s.title}" for i,s in enumerate(q)]
    await ctx.send("📃 **Queue:**\n" + "\n".join(lines))

@bot.command(name='leave')
async def leave(ctx):
    vc = ctx.voice_client
    if vc:
        await vc.disconnect()
        queues.pop(ctx.guild.id, None)
        await ctx.send("👋 Left the voice channel and cleared the queue.")
    else:
        await ctx.send("❌ I'm not in a voice channel.")

@bot.command(name='kiss-me')
async def kiss_me(ctx):
    """Secret fun command."""
    await ensure_voice(ctx)
    vc = ctx.voice_client
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    queues[ctx.guild.id] = []
    src = await YTDLSource.from_url('never gonna give you up rick astley', loop=bot.loop)
    vc.play(src)
    await ctx.send('okay ye lo mwah')

@bot.command(name='upload')
async def upload(ctx):
    """Upload a .txt playlist via Discord attachment."""
    await ensure_voice(ctx)
    attachments = ctx.message.attachments
    if not attachments:
        await ctx.send("❌ Please attach a .txt file to upload.")
        return
    attachment = attachments[0]
    if not attachment.filename.endswith('.txt'):
        await ctx.send("❌ Unsupported file type. Please upload a .txt file.")
        return
    data = await attachment.read()
    try:
        text = data.decode('utf-8')
    except:
        await ctx.send("❌ Could not decode file. Ensure it's UTF-8 encoded.")
        return
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 15:
        await ctx.send("❌ Playlist too long (max 15 songs).")
        return
    path = os.path.join(os.path.dirname(__file__), 'my_songs.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    vc = ctx.voice_client
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    queues[ctx.guild.id] = []
    skipped = []
    for qstr in lines:
        try:
            src = await YTDLSource.from_url(qstr, loop=bot.loop)
            queues[ctx.guild.id].append(src)
        except Exception:
            skipped.append(qstr)
    await ctx.send(f"📥 Loaded **{len(lines)-len(skipped)}/{len(lines)}** tracks from your uploaded playlist.")
    if skipped:
        await ctx.send("⚠️ Skipped: " + ", ".join(skipped))
    if not ctx.voice_client.is_playing():
        await play_next(ctx)

@bot.command(name='help')
async def help_command(ctx):
    help_text = (
        "**Music Bot Commands**\n"
        "`!join`     - Join & load songs.txt\n"
        "`!load`     - Clear & reload songs.txt playlist\n"
        "`!reload`   - Reload last in-memory playlist\n"
        "`!play`     - Queue a song (URL/search)\n"
        "`!pause`    - Pause current song\n"
        "`!resume`   - Resume paused song\n"
        "`!skip`     - Skip current song\n"
        "`!stop`     - Stop & clear queue (stay in VC)\n"
        "`!exit`     - Disconnect & clear all state\n"
        "`!queue`    - Show current queue\n"
        "`!leave`    - Leave VC & clear queue\n"
        "`!kiss-me`  - ; )\n"
        "`!upload`   - Upload a .txt playlist (max 15 songs)\n"
        "`!help`     - Show this message"
    )
    await ctx.send(help_text)

if __name__ == '__main__':
    bot.run(TOKEN)
