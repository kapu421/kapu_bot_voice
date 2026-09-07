import os
import sys
import re
import json
import logging
import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from dotenv import load_dotenv

# ロギング設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN_SUB") or os.getenv("BOT_TOKEN")
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://127.0.1:50021") # ngrokのURLまたはローカルIP

if not BOT_TOKEN:
    logger.error("BOT_TOKEN が設定されていません。")
    sys.exit("BOT_TOKEN is required in .env")

# ---------------------------------------------------------
# JSONデータ永続化（設定・ユーザー声・辞書）
# ---------------------------------------------------------
def load_json(filename: str, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.exception(f"{filename} の読み込みエラー: %s", e)
    return default

def save_json(filename: str, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.exception(f"{filename} の保存エラー: %s", e)

USER_SPEAKERS_FILE = "user_speakers.json"
DICTIONARY_FILE = "dictionary.json"

user_speakers: dict[str, int] = load_json(USER_SPEAKERS_FILE, {})
word_dictionary: dict[str, str] = load_json(DICTIONARY_FILE, {
    "lol": "わら",
    "w": "わら",
    "vc": "ブイシー"
})

# デフォルトの声（四国めたん・ノーマル等）
DEFAULT_SPEAKER_ID = 2

# ---------------------------------------------------------
# 音声生成キューと状態管理
# ---------------------------------------------------------
class VoiceQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None

    async def add(self, text: str, speaker_id: int, vc: discord.VoiceClient):
        await self.queue.put((text, speaker_id, vc))

    def clear(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break

voice_queues: dict[int, VoiceQueue] = {}

# ---------------------------------------------------------
# 読み上げテキスト整形処理
# ---------------------------------------------------------
def sanitize_text_for_tts(text: str) -> str:
    # 1. URLの短縮
    text = re.sub(r"https?://\S+", "URL省略", text)

    # 2. メンション・チャンネル参照の変換/除去
    text = re.sub(r"<@!?\d+>", "メンション", text)
    text = re.sub(r"<#\d+>", "チャンネル", text)
    text = re.sub(r"<@&\d+>", "ロール", text)

    # 3. 絵文字の除去（<:name:id> や <a:name:id>）
    text = re.sub(r"<a?:[a-zA-Z0-9_]+:\d+>", "", text)

    # 4. ユーザー辞書による置換
    for word, reading in word_dictionary.items():
        text = text.replace(word, reading)

    # 5. 長文カット（50文字以上は省略）
    max_len = 50
    if len(text) > max_len:
        text = text[:max_len] + " 以下省略"

    return text.strip()

# ---------------------------------------------------------
# VOICEVOX 音声合成 API 呼び出し
# ---------------------------------------------------------
async def generate_speech_file(text: str, speaker_id: int, output_path: str) -> bool:
    clean_url = VOICEVOX_URL.rstrip("/")
    try:
        async with aiohttp.ClientSession() as session:
            # 1. audio_query の作成
            async with session.post(
                f"{clean_url}/audio_query",
                params={"text": text, "speaker": speaker_id},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"audio_query エラー: status {resp.status}")
                    return False
                query_data = await resp.json()

            # 2. 音声波形合成 (synthesis)
            async with session.post(
                f"{clean_url}/synthesis",
                params={"speaker": speaker_id},
                json=query_data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"synthesis エラー: status {resp.status}")
                    return False
                audio_bytes = await resp.read()

            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            return True

    except Exception as e:
        logger.error(f"VOICEVOX通信エラー ({clean_url}): {e}")
        return False

# ---------------------------------------------------------
# 再生ループ処理
# ---------------------------------------------------------
async def speech_worker(guild_id: int):
    vq = voice_queues.get(guild_id)
    if not vq:
        return

    while True:
        try:
            text, speaker_id, vc = await vq.queue.get()
            if not vc.is_connected():
                vq.queue.task_done()
                break

            filename = f"temp_{guild_id}.wav"
            success = await generate_speech_file(text, speaker_id, filename)

            if success and os.path.exists(filename):
                audio_source = discord.FFmpegPCMAudio(filename)
                
                def after_playing(error):
                    if error:
                        logger.error(f"再生エラー: {error}")
                    if os.path.exists(filename):
                        try:
                            os.remove(filename)
                        except Exception:
                            pass

                vc.play(audio_source, after=after_playing)

                # 再生完了まで待機
                while vc.is_playing():
                    await asyncio.sleep(0.2)

            vq.queue.task_done()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"speech_worker エラー: {e}")
            await asyncio.sleep(1)

# ---------------------------------------------------------
# Bot本体の設定
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 接続中の読み上げ対象テキストチャンネル
target_text_channels: dict[int, int] = {}

@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    logger.info(f"読み上げBot起動完了: {bot.user} (同期コマンド: {len(synced)}個)")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    guild_id = message.guild.id
    vc = message.guild.voice_client

    # VCに接続中かつ設定されたチャンネルでの発言のみ読み上げる
    if vc and vc.is_connected() and target_text_channels.get(guild_id) == message.channel.id:
        clean_text = sanitize_text_for_tts(message.content)
        if clean_text:
            speaker_id = user_speakers.get(str(message.author.id), DEFAULT_SPEAKER_ID)

            if guild_id not in voice_queues:
                voice_queues[guild_id] = VoiceQueue()

            vq = voice_queues[guild_id]
            await vq.add(clean_text, speaker_id, vc)

            if vq.task is None or vq.task.done():
                vq.task = asyncio.create_task(speech_worker(guild_id))

    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    vc = member.guild.voice_client
    if not vc or not vc.is_connected():
        return

    # Bot以外の人が全員退出した場合、自動で切断
    human_members = [m for m in vc.channel.members if not m.bot]
    if len(human_members) == 0:
        guild_id = member.guild.id
        if guild_id in voice_queues:
            voice_queues[guild_id].clear()
            if voice_queues[guild_id].task:
                voice_queues[guild_id].task.cancel()
            del voice_queues[guild_id]

        target_text_channels.pop(guild_id, None)
        await vc.disconnect()

# ---------------------------------------------------------
# スラッシュコマンド
# ---------------------------------------------------------

@bot.tree.command(name="join", description="現在参加中のボイスチャンネルに読み上げBotを呼び出します")
async def join(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("先にボイスチャンネルに参加してください。", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel
    guild_id = interaction.guild_id

    try:
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(voice_channel)
        else:
            await voice_channel.connect()

        target_text_channels[guild_id] = interaction.channel_id
        await interaction.response.send_message(f"🔊 {voice_channel.mention} に接続しました！このチャンネルのメッセージを読み上げます。")
    except Exception as e:
        logger.exception("VC接続エラー: %s", e)
        await interaction.response.send_message("ボイスチャンネルへの接続に失敗しました。", ephemeral=True)

@bot.tree.command(name="leave", description="読み上げBotをボイスチャンネルから切断します")
async def leave(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("Botはボイスチャンネルに参加していません。", ephemeral=True)
        return

    if guild_id in voice_queues:
        voice_queues[guild_id].clear()
        if voice_queues[guild_id].task:
            voice_queues[guild_id].task.cancel()
        del voice_queues[guild_id]

    target_text_channels.pop(guild_id, None)
    await vc.disconnect()
    await interaction.response.send_message("👋 切断しました。")

@bot.tree.command(name="setvoice", description="あなたの読み上げキャラクター（Speaker ID）を変更します")
@app_commands.describe(speaker_id="VOICEVOXのスタイルID（例: 四国めたんノーマル=2, ずんだもんノーマル=3）")
async def set_voice(interaction: discord.Interaction, speaker_id: int):
    user_id = str(interaction.user.id)
    user_speakers[user_id] = speaker_id
    save_json(USER_SPEAKERS_FILE, user_speakers)

    await interaction.response.send_message(f"✅ 読み上げ声を ID: `{speaker_id}` に変更しました！", ephemeral=True)

@bot.tree.command(name="dict-add", description="単語の読み方を辞書に登録・更新します")
@app_commands.describe(word="変換前の単語", reading="読み方（ひらがな推奨）")
async def dict_add(interaction: discord.Interaction, word: str, reading: str):
    word_dictionary[word] = reading
    save_json(DICTIONARY_FILE, word_dictionary)
    await interaction.response.send_message(f"📖 辞書に登録しました: `{word}` ➔ `{reading}`")

@bot.tree.command(name="dict-delete", description="辞書から単語を削除します")
@app_commands.describe(word="削除したい単語")
async def dict_delete(interaction: discord.Interaction, word: str):
    if word in word_dictionary:
        del word_dictionary[word]
        save_json(DICTIONARY_FILE, word_dictionary)
        await interaction.response.send_message(f"🗑 辞書から削除しました: `{word}`")
    else:
        await interaction.response.send_message(f"単語 `{word}` は辞書に登録されていません。", ephemeral=True)

@bot.tree.command(name="dict-list", description="現在登録されている単語辞書の一覧を表示します")
async def dict_list(interaction: discord.Interaction):
    if not word_dictionary:
        await interaction.response.send_message("現在登録されている単語はありません。", ephemeral=True)
        return

    lines = [f"・`{w}` ➔ `{r}`" for w, r in word_dictionary.items()]
    content = "**📖 単語辞書一覧:**\n" + "\n".join(lines[:30])
    await interaction.response.send_message(content, ephemeral=True)

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        logger.exception("読み上げBotの起動に失敗しました: %s", e)
