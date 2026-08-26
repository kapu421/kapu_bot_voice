import os
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from keep_alive import keep_alive

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN が .env に設定されていません。")
    sys.exit("BOT_TOKEN is required in .env")

# インテントの設定（VC状態とメッセージコンテンツを取得）
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# --- プロキシ設定 (Webshare等のHTTP/HTTPSプロキシ用) ---
USE_PROXY = os.getenv("USE_PROXY", "false").lower() in ("true", "1", "yes")
PROXY_URL = os.getenv("PROXY_URL")

proxy_to_use = PROXY_URL if (USE_PROXY and PROXY_URL) else None

if USE_PROXY:
    if PROXY_URL:
        logger.info("HTTP/HTTPSプロキシ経由でDiscordに接続します: %s", PROXY_URL)
    else:
        logger.warning("USE_PROXY=true ですが PROXY_URL が設定されていないため、プロキシなしで起動します。")
else:
    logger.info("プロキシなしでDiscordに接続します。")

class MyBot(commands.Bot):
    pass

bot = MyBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    proxy=proxy_to_use,  # Webshareプロキシを適用
)


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info("App commands synced.")
    except Exception as e:
        logger.exception("Failed to sync app commands: %s", e)


# --- VC参加・切断の共通処理 ---

async def join_vc_logic(member: discord.Member, guild: discord.Guild) -> str:
    if not member.voice or not member.voice.channel:
        return "先にボイスチャンネルに入ってからコマンドを実行してください。"

    target_channel = member.voice.channel
    voice_client = guild.voice_client

    if voice_client:
        if voice_client.channel.id == target_channel.id:
            return f"すでに『{target_channel.name}』に参加しています。"
        else:
            await voice_client.move_to(target_channel)
            return f"『{target_channel.name}』に移動しました！"
    else:
        await target_channel.connect()
        return f"『{target_channel.name}』に参加しました！"


async def leave_vc_logic(guild: discord.Guild) -> str:
    voice_client = guild.voice_client
    if voice_client:
        channel_name = voice_client.channel.name
        await voice_client.disconnect()
        return f"『{channel_name}』から切断しました。"
    else:
        return "ボットはどのボイスチャンネルにも参加していません。"


# --- テキストコマンド (!join / !leave) ---

@bot.command(name="join")
async def join_command(ctx: commands.Context):
    if not ctx.guild:
        return
    res = await join_vc_logic(ctx.author, ctx.guild)
    await ctx.send(res)


@bot.command(name="leave")
async def leave_command(ctx: commands.Context):
    if not ctx.guild:
        return
    res = await leave_vc_logic(ctx.guild)
    await ctx.send(res)


# --- スラッシュコマンド (/join / /leave) ---

@bot.tree.command(name="join", description="実行者が参加しているボイスチャンネルに参加します")
async def join_slash(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    res = await join_vc_logic(interaction.user, interaction.guild)
    await interaction.response.send_message(res)


@bot.tree.command(name="leave", description="ボイスチャンネルから切断します")
async def leave_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    res = await leave_vc_logic(interaction.guild)
    await interaction.response.send_message(res)


if __name__ == "__main__":
    try:
        keep_alive()  # Renderの休眠対策（Flaskサーバー起動）
        bot.run(BOT_TOKEN)
    except Exception as e:
        logger.exception("Bot の実行に失敗しました: %s", e)