# Production-ready single-file Discord Music Bot (yt-dlp)
# Requires:
# discord.py[voice]
# yt-dlp
# python-dotenv
#
# FFmpeg must be installed and available in PATH.

import os
import time
import random
import asyncio
from collections import defaultdict, deque

import discord
from discord import guild
from discord.ext import commands
# from discord import app_commands  # not used
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": False,
    "extract_flat": False,
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

queues = defaultdict(deque)
current_track = {}
start_times = {}
guild_volume = defaultdict(lambda: 0.5)
loop_song = defaultdict(bool)
loop_queue = defaultdict(bool)
controller_messages = {}

class MusicControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        emoji="⏯️",
        style=discord.ButtonStyle.secondary,
        custom_id="pause_resume"
    )
    async def pause_resume(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        vc = interaction.guild.voice_client

        if not vc:
            return await interaction.response.send_message(
                "Not connected.",
                ephemeral=True
            )

        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message(
                "Paused",
                ephemeral=True
            )

        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message(
                "Resumed",
                ephemeral=True
            )
    
    @discord.ui.button(
        emoji="⏭️",
        style=discord.ButtonStyle.primary,
        custom_id="skip"
    )
    async def skip_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        vc = interaction.guild.voice_client

        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

        await interaction.response.send_message(
            "Skipped.",
            ephemeral=True
        )

    @discord.ui.button(
        emoji="⏹️",
        style=discord.ButtonStyle.danger,
        custom_id="stop"
    )
    async def stop_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        # Ensure this command is used in a guild
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "This command must be used in a server (guild).",
                ephemeral=True
            )

        gid = guild.id
        queues[gid].clear()
        current_track.pop(gid, None)

        vc = guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect()

        await interaction.response.send_message(
            "Stopped.",
            ephemeral=True
        )
    
    @discord.ui.button(
        emoji="🔀",
        style=discord.ButtonStyle.secondary,
        custom_id="shuffle"
    )
    async def shuffle_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        q = list(queues[interaction.guild.id])
        random.shuffle(q)
        queues[interaction.guild.id] = deque(q)

        await interaction.response.send_message(
            "Queue shuffled.",
            ephemeral=True
        )
    

    
    @discord.ui.button(
        emoji="📜",
        style=discord.ButtonStyle.secondary,
        custom_id="queue"
    )
    async def queue_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        q = queues[interaction.guild.id]

        if not q:
            return await interaction.response.send_message(
                "Queue is empty.",
                ephemeral=True
            )

        text = "\n".join(
            f"{i+1}. {song['title']}"
            for i, song in enumerate(list(q)[:10])
        )

        await interaction.response.send_message(
            text,
            ephemeral=True
        )
    # buttons here

class YTDL:

    @staticmethod
    async def search(query):
        loop = asyncio.get_event_loop()

        def run():
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                if query.startswith(("http://", "https://")):
                    return ydl.extract_info(query, download=False)

                return ydl.extract_info(
                    f"ytsearch:{query}",
                    download=False
                )

        return await loop.run_in_executor(None, run)

    @staticmethod
    async def get_stream(webpage_url):
        loop = asyncio.get_event_loop()

        def run():
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(
                    webpage_url,
                    download=False
                )

                if info.get("formats"):
                    stream = next(
                        (f for f in reversed(info["formats"]) if f.get("acodec") != "none"),
                        info["formats"][-1]
                    )
                    return {
                        "url": info["url"],
                        "codec": info.get("acodec", "Unknown"),
                        "container": info.get("ext", "Unknown"),
                        "bitrate": info.get("abr") or info.get("tbr") or "Unknown"
                    }

              
        return await loop.run_in_executor(None, run)

def fmt(sec):
    if not sec:
        return "Unknown"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"

def progress(current, total):
    if not total:
        return "Live"
    length = 16
    filled = min(length, int(length * current / total))
    return "▬" * filled + "🔘" + "▬" * (length - filled)


async def update_controller(guild):
    print("UPDATE CONTROLLER FIRED")

    message = controller_messages.get(guild.id)
    print("MESSAGE =", message)

    track = current_track.get(guild.id)
    print("TRACK =", track)

    if not message:
        print("NO MESSAGE")
        return

    if not track:
        print("NO TRACK")
        return

    try:
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=track["title"],
            color=discord.Color.blurple()
        )

        if track.get("thumbnail"):
            embed.set_thumbnail(url=track["thumbnail"])

        embed.add_field(
            name="Requester",
            value=track.get("requester", "Unknown"),
            inline=True
        )

        embed.add_field(
            name="Duration",
            value=fmt(track.get("duration")),
            inline=True
        )
        embed.set_footer(
    text=(
        f"{track.get('container', '?').upper()} • "
        f"{track.get('codec', '?')} • "
        f"{track.get('bitrate', '?')} kbps | Reso v1.0"
    )
)
        print("EDITING MESSAGE")

        await message.edit(
            embed=embed,
            view=MusicControls()
        )

        print("SUCCESS")

    except Exception as e:
        print("ERROR:", repr(e))

async def play_next(guild):
    vc = guild.voice_client
    if not vc:
        return

    if guild.id in current_track:
        track = current_track[guild.id]

        if loop_song[guild.id]:
            queues[guild.id].appendleft(track)
        elif loop_queue[guild.id]:
            queues[guild.id].append(track)

    if not queues[guild.id]:
        current_track.pop(guild.id, None)
        await asyncio.sleep(120)
        vc = guild.voice_client
        if vc and not vc.is_playing() and not queues[guild.id]:
            await vc.disconnect()
            controller_messages.pop(guild.id, None)
        return

    track = queues[guild.id].popleft()
    current_track[guild.id] = track
    
    start_times[guild.id] = time.time()

    stream_info = await YTDL.get_stream(track["webpage_url"])

    stream_url = stream_info["url"]

    track["codec"] = stream_info.get("codec", "Unknown")
    track["container"] = stream_info.get("container", "Unknown")
    track["bitrate"] = stream_info.get("bitrate", "Unknown")
    await update_controller(guild)

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            stream_url,
            executable=r"C:\ffmpeg\bin\ffmpeg.exe",
            **FFMPEG_OPTIONS
        ),
        volume=guild_volume[guild.id]
    )

    def after(error):
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    vc.play(source, after=after)

@bot.event
async def on_ready():
    bot.add_view(MusicControls())
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="play")
async def play(interaction: discord.Interaction, search: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        return await interaction.followup.send("Join a voice channel first.")

    vc = interaction.guild.voice_client

    if not vc:
        vc = await interaction.user.voice.channel.connect()

        await vc.guild.change_voice_state(
            channel=vc.channel,
            self_deaf=True
        )

    info = await YTDL.search(search)

    entries = info.get("entries")
    added = 0

    if entries:
        for e in entries:
            if not e:
                continue
            entry = {
                "title": e.get("title"),
                "duration": e.get("duration"),
                "thumbnail": e.get("thumbnail"),
                "webpage_url": e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                "requester": interaction.user.name,
                "channel_id": interaction.channel.id,
            }
            queues[interaction.guild.id].append(entry)
            added += 1
    else:
        entry = {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url"),
            "requester": interaction.user.name,
            "channel_id": interaction.channel.id,
        }

        queues[interaction.guild.id].append(entry)
        added = 1

    await interaction.followup.send(f"Added {added} track(s) to queue.")
    if interaction.guild.id not in controller_messages:
        embed = discord.Embed(
            title="🎵 Music Controller",
            description="Waiting for music..."
        )

        msg = await interaction.channel.send(
            embed=embed,
            view=MusicControls()
        )

        controller_messages[interaction.guild.id] = msg

    if not vc.is_playing():
        await play_next(interaction.guild)

@bot.tree.command(name="pause")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("Paused.")

@bot.tree.command(name="resume")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("Resumed.")

@bot.tree.command(name="skip")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("Skipped.") 

@bot.tree.command(name="stop")
async def stop(interaction: discord.Interaction):
    queues[interaction.guild.id].clear()
    current_track.pop(interaction.guild.id, None)
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
    await interaction.response.send_message("Stopped.")

@bot.tree.command(name="volume")
async def volume(interaction: discord.Interaction, percent: int):
    percent = max(0, min(200, percent))
    guild_volume[interaction.guild.id] = percent / 100
    await interaction.response.send_message(f"Volume set to {percent}%")

@bot.tree.command(name="shuffle")
async def shuffle(interaction: discord.Interaction):
    q = list(queues[interaction.guild.id])
    random.shuffle(q)
    queues[interaction.guild.id] = deque(q)
    await interaction.response.send_message("Queue shuffled.")

@bot.tree.command(name="remove")
async def remove(interaction: discord.Interaction, position: int):
    q = queues[interaction.guild.id]
    if position < 1 or position > len(q):
        return await interaction.response.send_message("Invalid position.")
    del q[position - 1]
    await interaction.response.send_message("Removed.")

@bot.tree.command(name="loop")
async def loop(interaction: discord.Interaction):
    loop_song[interaction.guild.id] = not loop_song[interaction.guild.id]
    await interaction.response.send_message(f"Song loop: {loop_song[interaction.guild.id]}")

@bot.tree.command(name="loopqueue")
async def loopqueue(interaction: discord.Interaction):
    loop_queue[interaction.guild.id] = not loop_queue[interaction.guild.id]
    await interaction.response.send_message(f"Queue loop: {loop_queue[interaction.guild.id]}")

@bot.tree.command(name="queue")
async def queue(interaction: discord.Interaction):
    embed = discord.Embed(title="Music Queue")
    q = queues[interaction.guild.id]

    if not q:
        embed.description = "Queue is empty."
    else:
        embed.description = "\n".join(
            f"{i+1}. {song['title']}"
            for i, song in enumerate(list(q)[:20])
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying")
async def nowplaying(interaction: discord.Interaction):
    track = current_track.get(interaction.guild.id)

    if not track:
        return await interaction.response.send_message("Nothing playing.")

    elapsed = int(time.time() - start_times.get(interaction.guild.id, time.time()))
    duration = track.get("duration", 0)

    embed = discord.Embed(
        title="Now Playing",
        description=track["title"]
    )

    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])

    embed.add_field(
        name="Progress",
        value=f"{fmt(elapsed)} / {fmt(duration)}\n{progress(elapsed, duration)}",
        inline=False
    )

    embed.add_field(name="Requester", value=track["requester"])

    await interaction.response.send_message(embed=embed)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing from .env")

bot.run(TOKEN)
