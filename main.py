import os
import logging
import sys
import io
import aiohttp

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
VOICEVOX_URL = os.getenv("VOICEVOX_URL")  # Renderに設定したngrokのURL

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

# VOICEVOX スピーカーリスト (名前: ID)
SPEAKERS = {
    "四国めたん（ノーマル）": 2,
    "四国めたん（あまあま）": 0,
    "四国めたん（ツンツン）": 6,
    "四国めたん（セクシー）": 4,
    "四国めたん（ささやき）": 36,
    "四国めたん（ヒソヒソ）": 37,
    "ずんだもん（ノーマル）": 3,
    "ずんだもん（あまあま）": 1,
    "ずんだもん（ツンツン）": 7,
    "ずんだもん（セクシー）": 5,
    "ずんだもん（ささやき）": 22,
    "ずんだもん（ヒソヒソ）": 38,
    "春日部つむぎ（ノーマル）": 8,
    "雨晴はう（ノーマル）": 10,
    "波音リツ（ノーマル）": 9,
    "玄野武宏（ノーマル）": 11,
    "玄野武宏（喜び）": 39,
    "玄野武宏（ツンギレ）": 40,
    "玄野武宏（悲しみ）": 41,
    "白上虎太郎（ふつう）": 12,
    "白上虎太郎（わーい）": 32,
    "白上虎太郎（びくびく）": 33,
    "白上虎太郎（おこ）": 34,
    "白上虎太郎（びえーん）": 35,
    "青山龍星（ノーマル）": 13,
    "冥鳴ひまり（ノーマル）": 14,
    "九州そら（ノーマル）": 16,
    "九州そら（あまあま）": 15,
    "九州そら（ツンツン）": 18,
    "九州そら（セクシー）": 17,
    "九州そら（ささやき）": 19,
    "もち子さん（ノーマル）": 20,
    "剣崎雌雄（ノーマル）": 21,
    "WhiteCUL（ノーマル）": 23,
    "WhiteCUL（たのしい）": 24,
    "WhiteCUL（かなしい）": 25,
    "WhiteCUL（びえーん）": 26,
    "後鬼（人間ver.）": 27,
    "後鬼（ぬいぐるみver.）": 28,
    "No.7（ノーマル）": 29,
    "No.7（アナウンス）": 30,
    "No.7（読み聞かせ）": 31,
    "ちび式じい（ノーマル）": 42,
    "櫻歌ミコ（ノーマル）": 43,
    "櫻歌ミコ（第二形態）": 44,
    "櫻歌ミコ（ロリ）": 45,
    "小夜/SAYO（ノーマル）": 46,
    "ナースロボ＿タイプＴ（ノーマル）": 47,
    "ナースロボ＿タイプＴ（楽々）": 48,
    "ナースロボ＿タイプＴ（恐怖）": 49,
    "ナースロボ＿タイプＴ（内緒話）": 50,
}

# ユーザーごとの選択スピーカー設定 (user_id: speaker_id)
user_speaker_map = {}


class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        # 通信セッションを使い回すため保持
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()


bot = MyBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    proxy=proxy_to_use,
)


# --- VOICEVOX 音声生成処理 ---

async def generate_voice(text: str, speaker_id: int) -> io.BytesIO | None:
    if not VOICEVOX_URL or not bot.session:
        logger.error("VOICEVOX_URL または ClientSession が正しく準備されていません。")
        return None

    headers = {"ngrok-skip-browser-warning": "true"}

    try:
        # 1. 音声合成用のクエリを作成
        async with bot.session.post(
            f"{VOICEVOX_URL}/audio_query",
            params={"text": text, "speaker": speaker_id},
            headers=headers
        ) as resp:
            if resp.status != 200:
                logger.error("VOICEVOX Audio Query Failed: %s", resp.status)
                return None
            query_data = await resp.json()

        # 2. 音声データを生成
        async with bot.session.post(
            f"{VOICEVOX_URL}/synthesis",
            params={"speaker": speaker_id},
            json=query_data,
            headers=headers
        ) as resp:
            if resp.status != 200:
                logger.error("VOICEVOX Synthesis Failed: %s", resp.status)
                return None
            voice_bytes = await resp.read()
            return io.BytesIO(voice_bytes)
            
    except Exception as e:
        logger.exception("VOICEVOX との通信に失敗しました: %s", e)
        return None


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info("App commands synced.")
    except Exception as e:
        logger.exception("Failed to sync app commands: %s", e)


# --- メッセージ受信・自動読み上げ処理 ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.content.startswith("!"):
        return

    voice_client = message.guild.voice_client if message.guild else None

    if voice_client and voice_client.is_connected():
        if message.author.voice and message.author.voice.channel.id == voice_client.channel.id:
            # ユーザーが指定した話者IDを取得（デフォルトは「ずんだもん (ノーマル): 3」）
            speaker_id = user_speaker_map.get(message.author.id, 3)
            
            audio_stream = await generate_voice(message.content, speaker_id)
            if audio_stream:
                try:
                    source = discord.FFmpegPCMAudio(audio_stream, pipe=True)
                    if not voice_client.is_playing():
                        voice_client.play(source)
                except Exception as e:
                    logger.exception("音声再生に失敗しました: %s", e)

    await bot.process_commands(message)


# --- 自動切断処理 ---

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    voice_state = before if before.channel else after
    if not voice_state or not voice_state.channel:
        return

    channel = voice_state.channel
    guild = channel.guild
    voice_client = guild.voice_client

    if voice_client and voice_client.channel.id == channel.id:
        human_members = [m for m in channel.members if not m.bot]
        if len(human_members) == 0:
            await voice_client.disconnect()
            logger.info(f"チャンネル『{channel.name}』から全員が退出したため、Botが自動切断しました。")


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


# --- 声（キャラクター）変更機能の追加 ---

async def speaker_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    # 候補の一致検索（最大25個まで表示）
    choices = [
        app_commands.Choice(name=name, value=name)
        for name in SPEAKERS.keys()
        if current.lower() in name.lower()
    ]
    return choices[:25]


@bot.tree.command(name="speaker", description="読み上げ音声のキャラクターを変更します")
@app_commands.autocomplete(name=speaker_autocomplete)
async def set_speaker_slash(interaction: discord.Interaction, name: str):
    if name not in SPEAKERS:
        await interaction.response.send_message("指定されたキャラクターが見つかりません。", ephemeral=True)
        return

    speaker_id = SPEAKERS[name]
    user_speaker_map[interaction.user.id] = speaker_id
    await interaction.response.send_message(f"読み上げキャラクターを**『{name}』**に変更しました！")


@bot.command(name="speaker")
async def set_speaker_command(ctx: commands.Context, *, name: str = None):
    if not name or name not in SPEAKERS:
        speaker_list_str = "\n".join([f"・{k}" for k in list(SPEAKERS.keys())[:10]])
        await ctx.send(f"使い方: `!speaker <キャラ名>`\n例: `!speaker ずんだもん（あまあま）`\n\n【指定可能なキャラ例（抜粋）】\n{speaker_list_str}\n...他多数")
        return

    speaker_id = SPEAKERS[name]
    user_speaker_map[ctx.author.id] = speaker_id
    await ctx.send(f"読み上げキャラクターを**『{name}』**に変更しました！")


# --- テキスト・スラッシュコマンド ---

@bot.command(name="join")
async def join_command(ctx: commands.Context):
    if not ctx.guild or not isinstance(ctx.author, discord.Member):
        return
    res = await join_vc_logic(ctx.author, ctx.guild)
    await ctx.send(res)


@bot.command(name="leave")
async def leave_command(ctx: commands.Context):
    if not ctx.guild:
        return
    res = await leave_vc_logic(ctx.guild)
    await ctx.send(res)


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
        keep_alive()
        bot.run(BOT_TOKEN)
    except Exception as e:
        logger.exception("Bot の実行に失敗しました: %s", e)
