import os
import json
import base64
import re
import requests
import time
from io import BytesIO
from threading import Thread
from zoneinfo import ZoneInfo
from datetime import datetime

from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands
from discord import ui, Interaction, ButtonStyle
from PIL import Image, ImageDraw, ImageFont

# -------------------------
# Config / Ambiente
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
PORT = int(os.getenv("PORT", 8080))
GUILD_ID = os.getenv("GUILD_ID")

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# -------------------------
# Flask keepalive
# -------------------------
app = Flask("imunebot")

@app.route("/", methods=["GET"])
def home():
    return "🤖 Bot rodando!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_flask, daemon=True).start()

# -------------------------
# Auto ping (manter bot ativo)
# -------------------------
def auto_ping():
    while True:
        try:
            url = os.environ.get("REPLIT_URL") or os.environ.get("SELF_URL")
            if url:
                requests.get(url)
            time.sleep(300)  # ping a cada 5 minutos
        except Exception as e:
            print(f"Erro no auto-ping: {e}")

Thread(target=auto_ping, daemon=True).start()

# -------------------------
# Bot setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# -------------------------
# Função de horário BR
# -------------------------
def now_br():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))

# -------------------------
# Estrutura de dados em memória
# -------------------------
data = {
    "xp": {},
    "level": {},
    "warns": {},
    "reaction_roles": {},
    "config": {"welcome_channel": None},
    "logs": []
}

# -------------------------
# GitHub persistence
# -------------------------
def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_data_from_github():
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            content_b64 = js.get("content", "")
            if content_b64:
                raw = base64.b64decode(content_b64)
                loaded = json.loads(raw.decode("utf-8"))
                data.update(loaded)
                print("Dados carregados do GitHub.")
                return True
        else:
            print(f"GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print("Erro ao carregar dados do GitHub:", e)
    return False

def save_data_to_github(message="Bot update"):
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{message} @ {now_br().isoformat()}",
            "content": base64.b64encode(content).decode("utf-8"),
            "branch": BRANCH
        }
        if sha:
            payload["sha"] = sha

        put = requests.put(GITHUB_API_CONTENT, headers=_gh_headers(), json=payload, timeout=30)
        if put.status_code in (200, 201):
            print("Dados salvos no GitHub.")
            return True
        else:
            print("Erro ao salvar no GitHub:", put.status_code, put.text[:400])
    except Exception as e:
        print("Exception saving to GitHub:", e)
    return False

def add_log(entry):
    ts = now_br().isoformat()
    data.setdefault("logs", []).append({"ts": ts, "entry": entry})
    try:
        save_data_to_github(f"log: {entry}")
    except Exception:
        pass

# -------------------------
# XP / level
# -------------------------
def xp_for_message():
    return 15

def xp_to_level(xp):
    lvl = int((xp / 100) ** 0.6) + 1
    return max(lvl, 1)

# -------------------------
# emoji
# -------------------------
EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")

def parse_emoji_str(emoji_str, guild: discord.Guild = None):
    if not emoji_str:
        return None
    m = EMOJI_RE.match(emoji_str.strip())
    if m:
        name, id_str = m.groups()
        try:
            eid = int(id_str)
            if guild:
                e = discord.utils.get(guild.emojis, id=eid)
                if e:
                    return e
            return discord.PartialEmoji(name=name, id=eid)
        except Exception:
            pass
    return emoji_str

# -------------------------
# múltiplos botões
# -------------------------
class PersistentRoleButtonView(ui.View):
    def __init__(self, message_id: int, buttons_dict: dict):
        """
        message_id: ID da mensagem que contém os botões
        buttons_dict = {
            "Nome do Botão 1": role_id1,
            "Nome do Botão 2": role_id2,
        }
        """
        super().__init__(timeout=None)
        self.message_id = message_id
        for label, role_id in buttons_dict.items():
            self.add_item(PersistentRoleButton(label=label, role_id=role_id, message_id=message_id))

class PersistentRoleButton(ui.Button):
    def __init__(self, label: str, role_id: int, message_id: int):
        super().__init__(label=label, style=ButtonStyle.primary)
        self.role_id = role_id
        self.message_id = message_id

    async def callback(self, interaction: Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
            return

        if role in member.roles:
            await member.remove_roles(role, reason="Role button")
            await interaction.response.send_message(f"Você **removeu** o cargo {role.mention}.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Role button")
            await interaction.response.send_message(f"Você **recebeu** o cargo {role.mention}.", ephemeral=True)

        # Log
        add_log(f"role_button_click: user={member.id} role={role.id} message={self.message_id}")

# -------------------------
# Eventos
# -------------------------
@bot.event
async def on_ready():
    print(f"Logado como {bot.user} (id: {bot.user.id})")
    load_data_from_github()

    # Sincronizar comandos slash
    try:
        if GUILD_ID:
            gid = int(GUILD_ID)
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
            print(f"Comandos slash sincronizados no servidor {gid}.")
        else:
            await tree.sync()
            print("Comandos slash globais sincronizados.")
    except Exception as e:
        print("Erro ao sincronizar comandos:", e)

    # ---------- Reconstruir botões persistentes ----------
    for msg_id_str, buttons_dict in data.get("role_buttons", {}).items():
        try:
            msg_id = int(msg_id_str)
            message = None
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    try:
                        message = await channel.fetch_message(msg_id)
                        break
                    except Exception:
                        continue
                if message:
                    break
            if message:
                view = PersistentRoleButtonView(msg_id, buttons_dict)
                await message.edit(view=view)
                print(f"Role Buttons restaurados para mensagem {msg_id}")
        except Exception as e:
            print(f"Erro ao restaurar role buttons para a mensagem {msg_id_str}: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    ch_id = data.get("config", {}).get("welcome_channel")
    channel = None
    if ch_id:
        channel = member.guild.get_channel(int(ch_id))
    if not channel:
        channel = discord.utils.get(member.guild.text_channels, name="boas-vindas")
    if not channel:
        return

    # Mensagem de boas-vindas customizada
    welcome_msg = data.get("config", {}).get("welcome_message", "Olá {member}, seja bem-vindo(a)!")
    welcome_msg = welcome_msg.replace("{member}", member.mention)

    # ----- Imagem de fundo personalizada -----
    background_path = data.get("config", {}).get(
    "welcome_background")


    width, height = 900, 300
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))

    # Fundo (baixa via URL)
    try:
        import requests
        response = requests.get(background_path)
        bg = Image.open(BytesIO(response.content)).convert("RGBA")
        bg = bg.resize((width, height))
        img.paste(bg, (0, 0))
    except Exception as e:
        print(f"Erro ao carregar imagem de fundo: {e}")

    # Overlay cinza translúcido para melhorar contraste do texto
    overlay = Image.new("RGBA", (width, height), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    # Avatar do usuário centralizado com borda roxa clara e sem pixelar
    try:
        user_bytes = await member.avatar.read()
        user_avatar = Image.open(BytesIO(user_bytes)).convert("RGBA")

        avatar_size = 150
        border_size = 5
        upscale = 4
        big_size = (avatar_size + border_size * 2) * upscale

        user_avatar = user_avatar.resize((avatar_size * upscale, avatar_size * upscale))
        mask = Image.new("L", (avatar_size * upscale, avatar_size * upscale), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, avatar_size * upscale, avatar_size * upscale), fill=255)

        border_color = (200, 150, 255, 255)  # roxo mais claro
        border = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
        draw_border = ImageDraw.Draw(border)
        draw_border.ellipse((0, 0, big_size, big_size), fill=border_color)

        border.paste(user_avatar, (border_size * upscale, border_size * upscale), mask)
        border = border.resize((avatar_size + border_size * 2, avatar_size + border_size * 2), Image.Resampling.LANCZOS)

        x = (width - border.width) // 2
        y = 30
        img.paste(border, (x, y), border)
    except Exception as e:
        print(f"Erro ao carregar avatar do usuário: {e}")

    # ----- Texto -----
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    text_color = (200, 150, 255)
    shadow_color = (0, 0, 0, 180)

    # Nome do usuário
    text_name = member.display_name
    bbox_name = draw.textbbox((0, 0), text_name, font=font_b)
    text_w = bbox_name[2] - bbox_name[0]
    text_x = (width - text_w) // 2
    text_y = y + border.height + 10

    draw.text((text_x + 2, text_y + 2), text_name, font=font_b, fill=shadow_color)
    draw.text((text_x, text_y), text_name, font=font_b, fill=text_color)

    # Contagem de membros
    text_count = f"Membro #{len(member.guild.members)}"
    bbox_count = draw.textbbox((0, 0), text_count, font=font_s)
    text_w2 = bbox_count[2] - bbox_count[0]
    text_x2 = (width - text_w2) // 2
    text_y2 = text_y + 50

    draw.text((text_x2 + 1, text_y2 + 1), text_count, font=font_s, fill=shadow_color)
    draw.text((text_x2, text_y2), text_count, font=font_s, fill=text_color)

    # ----- Enviar mensagem -----
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="welcome.png")

    await channel.send(content=welcome_msg, file=file)
    add_log(f"member_join: {member.id} - {member}")







# -------------------------
# Reaction roles
# -------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        msgmap = data.get("reaction_roles", {}).get(str(payload.message_id))
        if not msgmap:
            return

        # Resolver o emoji
        role_id = None
        # Checa pelo ID (custom emoji)
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            role_id = msgmap[str(payload.emoji.id)]
        # Checa pelo nome (custom emoji)
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            role_id = msgmap[payload.emoji.name]
        # Checa unicode
        elif str(payload.emoji) in msgmap:
            role_id = msgmap[str(payload.emoji)]

        if not role_id:
            return

        # Busca guild e member
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            member = await guild.fetch_member(payload.user_id)
        role = guild.get_role(int(role_id))
        if member and role:
            await member.add_roles(role, reason="reaction role add")
            add_log(f"reaction add: user={member.id} role={role.id} msg={payload.message_id}")

    except Exception as e:
        print("on_raw_reaction_add error:", e)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    try:
        msgmap = data.get("reaction_roles", {}).get(str(payload.message_id))
        if not msgmap:
            return

        role_id = None
        if payload.emoji.id and str(payload.emoji.id) in msgmap:
            role_id = msgmap[str(payload.emoji.id)]
        elif payload.emoji.id is not None and payload.emoji.name in msgmap:
            role_id = msgmap[payload.emoji.name]
        elif str(payload.emoji) in msgmap:
            role_id = msgmap[str(payload.emoji)]

        if not role_id:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            member = await guild.fetch_member(payload.user_id)
        role = guild.get_role(int(role_id))
        if member and role:
            await member.remove_roles(role, reason="reaction role remove")
            add_log(f"reaction remove: user={member.id} role={role.id} msg={payload.message_id}")

    except Exception as e:
        print("on_raw_reaction_remove error:", e)


# -------------------------
# Warn helper
# -------------------------
async def add_warn(member: discord.Member, reason=""):
    uid = str(member.id)
    entry = {
        "by": bot.user.id,
        "reason": reason,
        "ts": now_br().strftime("%d/%m/%Y %H:%M")  # dia/mês/ano hora:minuto
    }
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("Auto-warn")
    add_log(f"warn: user={uid} by=bot reason={reason}")


# -------------------------
# on_message
# -------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)
    content = message.content.strip()
    delete_message = False

    # -------- IGNORAR COMANDOS DO MUDAE --------
    mudae_commands = [
        "$w", "$wa", "$wg", "$h", "$ha", "$hg",
        "$W", "$WA", "$WG", "$H", "$HA", "$HG",
        "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
    ]
    if any(content.lower().startswith(cmd) for cmd in mudae_commands):
        await bot.process_commands(message)
        return

    # -------- IGNORAR ADVERTÊNCIAS PARA ADM E MOD --------
    ignored_roles = {"Administrador", "Moderador"}
    member_roles = {r.name for r in message.author.roles}
    is_staff = any(role in ignored_roles for role in member_roles)

    # -------- IGNORAR MÍDIA (imagem, vídeo, gif, sticker, arquivo) --------
    has_media = False

    # Imagens/vídeos/arquivos
    if message.attachments:
        has_media = True

    # Stickers
    if message.stickers:
        has_media = True

    # GIFs de sites conhecidos
    gif_domains = ["tenor.com", "media.tenor.com", "giphy.com", "imgur.com"]
    if any(domain in content.lower() for domain in gif_domains):
        has_media = True

    if has_media:
        await bot.process_commands(message)
        return

    # -------- BLOQUEIO DE LINKS --------
    blocked_channels = data.get("blocked_links_channels", [])
    if message.channel.id in blocked_channels:
        import re
        url_pattern = r"https?://[^\s]+"
        if re.search(url_pattern, content):
            if not is_staff:
                try:
                    await message.delete()
                except discord.Forbidden:
                    pass
                await message.channel.send(f"⚠️ {message.author.mention}, links não são permitidos aqui!")
                await add_warn(message.author, reason="Enviou link em canal bloqueado")
                return

    # -------- ANTI-SPAM --------
    user_msgs = data.setdefault("last_messages_content", {}).setdefault(uid, [])
    if len(user_msgs) >= 5:
        user_msgs.pop(0)

    if user_msgs and content == user_msgs[-1]:
        if not is_staff:
            delete_message = True
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            await message.channel.send(f"⚠️ {message.author.mention}, evite enviar mensagens repetidas!")
            await add_warn(message.author, reason="Spam detectado")
            return
    else:
        user_msgs.append(content)
    data["last_messages_content"][uid] = user_msgs

    # -------- DETECÇÃO DE MAIÚSCULAS --------
    #if len(content) > 5 and content.isupper():
        #if not is_staff:
            #delete_message = True
           # try:
            #    await message.delete()
           # except discord.Forbidden:
            #    pass
           # await message.channel.send(f"⚠️ {message.author.mention}, evite escrever tudo em maiúsculas!")
          #  await add_warn(message.author, reason="Uso excessivo de maiúsculas")
           # return

    # -------- SISTEMA DE XP --------
    if not delete_message:
        data.setdefault("xp", {})
        data.setdefault("level", {})

        xp_rate = data.get("config", {}).get("xp_rate", 3)
        xp_gain = max(1, xp_for_message() // xp_rate)
        data["xp"][uid] = data["xp"].get(uid, 0) + xp_gain

        xp_now = data["xp"][uid]
        lvl_now = xp_to_level(xp_now)
        prev_lvl = data["level"].get(uid, 1)

        if lvl_now > prev_lvl:
            data["level"][uid] = lvl_now

            levelup_channel_id = data.get("config", {}).get("levelup_channel")
            channel_to_send = None

            if levelup_channel_id:
                channel_to_send = message.guild.get_channel(int(levelup_channel_id))
            if not channel_to_send:
                channel_to_send = message.channel

            try:
                await channel_to_send.send(f"🎉 {message.author.mention} subiu para o nível **{lvl_now}**!")
            except Exception as e:
                print(f"Erro ao enviar mensagem de level up: {e}")

            level_roles = data.get("level_roles", {})
            role_id = level_roles.get(str(lvl_now))
            if role_id:
                role = message.guild.get_role(int(role_id))
                if role:
                    try:
                        await message.author.add_roles(role, reason=f"Alcançou nível {lvl_now}")
                    except discord.Forbidden:
                        await channel_to_send.send(
                            f"⚠️ Não consegui dar o cargo {role.mention}, verifique minhas permissões."
                        )

            add_log(f"level_up: user={uid} level={lvl_now}")

    # -------- SALVAR DADOS --------
    try:
        save_data_to_github("XP update")
    except Exception as e:
        print(f"Erro ao salvar XP: {e}")

    await bot.process_commands(message)


# -------------------------
# Slash commands
# -------------------------
def is_admin_check(interaction: discord.Interaction) -> bool:
    try:
        perms = interaction.user.guild_permissions
        return perms.administrator or perms.manage_guild or perms.manage_roles
    except Exception:
        return False
        
def is_command_allowed(interaction: discord.Interaction, command_name: str) -> bool:
    allowed = data.get("command_channels", {}).get(command_name, [])
    # Se nenhum canal estiver configurado, o comando é liberado em todos
    if not allowed:
        return True
    return interaction.channel_id in allowed


#/cargo_xp

@tree.command(name="cargo_xp", description="Define um cargo para ser atribuído ao atingir certo nível (admin)")
@app_commands.describe(level="Nível em que o cargo será dado", role="Cargo a ser atribuído")
async def set_level_role(interaction: discord.Interaction, level: int, role: discord.Role):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão.", ephemeral=True)
        return

    if level < 1:
        await interaction.response.send_message("⚠️ O nível deve ser maior que 0.", ephemeral=True)
        return

    data.setdefault("level_roles", {})[str(level)] = str(role.id)
    save_data_to_github("Set level role")

    await interaction.response.send_message(
        f"✅ Cargo {role.mention} será atribuído ao atingir o **nível {level}**.",
        ephemeral=False
    )


# -------------------------
# /setxprate — ajusta a taxa de ganho de XP
# -------------------------
@tree.command(name="xp_rate", description="Define a taxa de ganho de XP (admin)")
@app_commands.describe(rate="Taxa de XP — valores menores tornam o up mais lento (ex: 1 = normal, 2 = 2x mais difícil, 4 = 4x mais difícil)")
async def set_xp_rate(interaction: discord.Interaction, rate: int):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    if rate < 1:
        await interaction.response.send_message("⚠️ O valor mínimo é 1.", ephemeral=True)
        return

    data.setdefault("config", {})["xp_rate"] = rate
    save_data_to_github("Set XP rate")

    await interaction.response.send_message(f"✅ Taxa de XP ajustada para **x{rate}**. Agora é **{rate}x mais difícil** subir de nível.", ephemeral=False)


#/mensagem_personalizada
@tree.command(name="mensagem_personalizada", description="Cria uma mensagem personalizada (admin)")
@app_commands.describe(
    canal="Canal onde a mensagem será enviada",
    titulo="Título da mensagem",
    corpo="Texto interno (use \n para quebra de linha)",
    imagem="Link da imagem (opcional)",
    cor="Cor em hexadecimal (ex: #5865F2)",
    mencionar="Mencionar @everyone (opcional)"
)
async def criar_embed(
    interaction: discord.Interaction,
    canal: discord.TextChannel,
    titulo: str,
    corpo: str,
    imagem: str = None,
    cor: str = "#5865F2",
    mencionar: str = None
):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    # Converter a cor de string para objeto Color
    try:
        color = discord.Color(int(cor.replace("#", ""), 16))
    except:
        color = discord.Color.blurple()

    # 🔹 Formatar o texto da descrição
    formatted_text = corpo.replace("\\n", "\n").strip()

    # Substitui marcadores por ● (grande e sólido)
    formatted_text = formatted_text.replace("- ", "● ").replace("• ", "● ")

    # Adiciona espaçamento entre linhas
    lines = formatted_text.split("\n")
    formatted_text = "\n\n".join(line.strip() for line in lines if line.strip())

    # Cria a embed
    embed = discord.Embed(
        title=f"**{titulo}**",  # sem emoji
        description=formatted_text,
        color=color
    )

    # Imagem (se fornecida)
    if imagem:
        embed.set_image(url=imagem)

    # Envia a embed
    mention_text = mencionar if mencionar in ["@everyone", "@here"] else ""
    await canal.send(content=mention_text, embed=embed)
    await interaction.response.send_message(f"✅ Embed enviada para {canal.mention}.", ephemeral=True)


# -------------------------
# /setwelcomeimage - Define ou remove a imagem de fundo da mensagem de boas-vindas
# -------------------------
@tree.command(name="selecionar_imagem_boas-vindas", description="Define ou remove a imagem de fundo da mensagem de boas-vindas (admin)")
@app_commands.describe(url="URL da imagem que será usada no fundo (deixe vazio para remover)")
async def slash_setwelcomeimage(interaction: discord.Interaction, url: str = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    config = data.setdefault("config", {})

    # --- Remover imagem personalizada ---
    if not url:
        if "welcome_background" in config:
            del config["welcome_background"]
            save_data_to_github("Unset welcome background")
            await interaction.response.send_message("🧹 Imagem de fundo personalizada removida. Voltará a usar a padrão.", ephemeral=False)
        else:
            await interaction.response.send_message("ℹ️ Nenhuma imagem personalizada estava configurada.", ephemeral=True)
        return

    # --- Validar URL ---
    if not (url.startswith("http://") or url.startswith("https://")):
        await interaction.response.send_message("❌ Forneça uma URL válida começando com http:// ou https://", ephemeral=True)
        return

    # --- Salvar nova imagem ---
    config["welcome_background"] = url
    save_data_to_github("Set welcome background")
    await interaction.response.send_message(f"✅ Imagem de fundo definida com sucesso!\n{url}", ephemeral=False)


#/definir_canal_comando
@tree.command(name="definir_canal_comando", description="Define canais onde um comando pode ser usado (admin)")
@app_commands.describe(
    command="Nome do comando (ex: rank, top, aviso)",
    channel="Canal de texto para permitir o comando"
)
async def slash_setcommandchannel(interaction: discord.Interaction, command: str, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        return

    cmd_channels = data.setdefault("command_channels", {})
    channels = cmd_channels.setdefault(command.lower(), [])

    if channel.id in channels:
        channels.remove(channel.id)
        msg = f"❌ O canal {channel.mention} **foi removido** da lista do comando `{command}`."
    else:
        channels.append(channel.id)
        msg = f"✅ O canal {channel.mention} **foi adicionado** para o comando `{command}`."

    save_data_to_github(f"Set command channel for {command}")
    await interaction.response.send_message(msg, ephemeral=False)

# -------------------------
# Comando para criar mensagem com botões
# -------------------------
@tree.command(name="criar_reação_com_botao", description="Cria uma mensagem com botões de cargos")
@app_commands.describe(
    channel="Canal para enviar a mensagem",
    content="Texto da mensagem",
    roles="Botão:Cargo separados por vírgula (ex: Aceitar:Regra,VIP:VIP)"
)
async def create_role_buttons(interaction: Interaction, channel: discord.TextChannel, content: str, roles: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    # Criar dicionário de botões
    buttons_dict = {}
    for pair in [r.strip() for r in roles.split(",")]:
        try:
            button_name, role_name = pair.split(":")
        except ValueError:
            await interaction.response.send_message(f"Formato inválido: `{pair}`. Use Botão:Cargo", ephemeral=True)
            return
        
        role = discord.utils.get(interaction.guild.roles, name=role_name.strip())
        if not role:
            await interaction.response.send_message(f"Cargo `{role_name}` não encontrado.", ephemeral=True)
            return
        
        buttons_dict[button_name.strip()] = role.id

    # Envia mensagem
    view = PersistentRoleButtonView(0, buttons_dict)  # temporário, substituiremos depois pelo ID
    sent = await channel.send(content=content, view=view)

    # Atualiza view com ID real da mensagem
    view.message_id = sent.id
    for item in view.children:
        if isinstance(item, PersistentRoleButton):
            item.message_id = sent.id

    # Salva no data.json
    data.setdefault("role_buttons", {})[str(sent.id)] = buttons_dict
    save_data_to_github("Create role buttons")

    await interaction.response.send_message(f"Mensagem criada em {channel.mention} com {len(buttons_dict)} botões.", ephemeral=True)


# Comando para bloquear/desbloquear links em um canal
@tree.command(name="bloquear_links", description="Bloqueia ou desbloqueia links em um canal (admin)")
@app_commands.describe(channel="Canal para bloquear/desbloquear links")
async def block_links(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("blocked_links_channels", [])
    
    if channel.id in data["blocked_links_channels"]:
        # Remove o bloqueio
        data["blocked_links_channels"].remove(channel.id)
        save_data_to_github("Unblock links channel")
        await interaction.response.send_message(f"✅ Links desbloqueados no canal {channel.mention}.")
    else:
        # Adiciona o bloqueio
        data["blocked_links_channels"].append(channel.id)
        save_data_to_github("Block links channel")
        await interaction.response.send_message(f"✅ Links bloqueados no canal {channel.mention}.")


# /perfil
@tree.command(name="perfil", description="mostra o seu perfil")
@app_commands.describe(member="Membro a ver o rank (opcional)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    if not is_command_allowed(interaction, "rank"):
        await interaction.response.send_message("❌ Este comando só pode ser usado em canais autorizados.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    target = member or interaction.user
    uid = str(target.id)
    xp = data.get("xp", {}).get(uid, 0)
    lvl = data.get("level", {}).get(uid, xp_to_level(xp))

    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i+1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))

    # Imagem base
    width, height = 900, 200
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))  # fundo preto
    draw = ImageDraw.Draw(img)

    # Fontes
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except:
        font_b = ImageFont.load_default()
        font_s = ImageFont.load_default()

    # Avatar circular
    try:
        avatar_bytes = await target.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((120, 120))
        mask = Image.new("L", (120, 120), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 120, 120), fill=255)
        img.paste(avatar, (20, 40), mask)
    except Exception as e:
        print("Erro avatar:", e)

    # Nome do usuário
    draw.text((160, 50), target.display_name, font=font_b, fill=(0, 255, 255))

    # Classificação e nível no canto direito
    draw.text((width - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((width - 220, 80), f"NÍVEL {lvl}", font=font_s, fill=(255, 0, 255))

    # Barra XP arredondada
    next_xp = 100 + lvl*50
    cur = xp % next_xp
    bar_total_w, bar_h = 560, 36
    x0, y0 = 160, 140
    radius = bar_h // 2

    # Fundo da barra
    draw.rounded_rectangle([x0, y0, x0+bar_total_w, y0+bar_h], radius=radius, fill=(50, 50, 50))

    
    # Barra preenchida (gradiente azul neon) arredondada
    fill_w = int(bar_total_w * min(1.0, cur / next_xp))
    if fill_w > 0:
    # Cria a barra preenchida com mesmo raio que o fundo
        filled_bar = Image.new("RGBA", (fill_w, bar_h), (0,0,0,0))
        fill_draw = ImageDraw.Draw(filled_bar)
        fill_draw.rounded_rectangle([0, 0, fill_w, bar_h], radius=radius, fill=(0, 200, 255))
    
    # Se quiser gradiente, pode substituir fill por um gradiente similar ao que já fazia
        img.paste(filled_bar, (x0, y0), filled_bar)
        


    # Texto XP dentro da barra, centralizado verticalmente
    xp_text = f"{cur} / {next_xp} XP"
    bbox = draw.textbbox((0, 0), xp_text, font=font_s)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = x0 + (bar_total_w - text_w) // 2
    text_y = y0 + (bar_h - text_h) // 2
    draw.text((text_x, text_y), xp_text, font=font_s, fill=(255, 255, 255))

    # Enviar imagem
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="rank.png")
    await interaction.followup.send(file=file)

# /definir_boas-vindas
@tree.command(name="definir_boas-vindas", description="Define a mensagem de boas-vindas (admin)")
@app_commands.describe(message="Mensagem (use {member} para mencionar)")
async def slash_setwelcome(interaction: discord.Interaction, message: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["welcome_message"] = message
    save_data_to_github("Set welcome message")
    await interaction.response.send_message(f"Mensagem de boas-vindas definida!\n{message}")


# /rank
@tree.command(name="rank", description="Mostra top 10 de XP")
async def slash_top(interaction: discord.Interaction):
    if not is_command_allowed(interaction, "top"):
        await interaction.response.send_message("❌ Este comando só pode ser usado em canais autorizados.", ephemeral=True)
        return
    await interaction.response.defer()
    ranking = sorted(data.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    lines = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        name = user.display_name if user else f"Usuário {uid}"
        lines.append(f"{i}. {name} — {xp} XP")
    text = "\n".join(lines) if lines else "Sem dados ainda."
    await interaction.followup.send(f"🏆 **Top 10 XP**\n{text}")

# /advertir
@tree.command(name="advertir", description="Advertir um membro (admin)")
@app_commands.describe(member="Membro a ser advertido", reason="Motivo da advertência")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "Sem motivo informado"):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão para usar este comando.", ephemeral=True)
        return
    uid = str(member.id)
    entry = {
        "by": interaction.user.id,
        "reason": reason,
        "ts": datetime.utcnow().strftime("%d/%m/%Y %H:%M")  # formato dia/mês/ano hora:minuto
    }
    data.setdefault("warns", {}).setdefault(uid, []).append(entry)
    save_data_to_github("New warn")
    add_log(f"warn: user={uid} by={interaction.user.id} reason={reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} advertido.\nMotivo: {reason}")

# /lista_de_advertência
@tree.command(name="lista_de_advertência", description="Mostra advertências de um membro")
@app_commands.describe(member="Membro (opcional)")
async def slash_warns(interaction: discord.Interaction, member: discord.Member = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão para usar este comando.", ephemeral=True)
        return
    target = member or interaction.user
    arr = data.get("warns", {}).get(str(target.id), [])
    if not arr:
        await interaction.response.send_message(f"{target.mention} não tem advertências.", ephemeral=False)
        return
    text = "\n".join([f"- {w['reason']} (por <@{w['by']}>) em {w['ts']}" for w in arr])
    await interaction.response.send_message(f"⚠️ Advertências de {target.mention}:\n{text}")

# /savedata (admin)
@tree.command(name="savedata", description="Força salvar dados no GitHub (admin)")
async def slash_savedata(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    ok = save_data_to_github("Manual save via /savedata")
    await interaction.response.send_message("Dados salvos no GitHub." if ok else "Falha ao salvar (veja logs).")

# /definir_canal_boas-vindas (admin)
@tree.command(name="definir_canal_boas-vindas", description="Define canal de boas-vindas para o bot (admin)")
@app_commands.describe(channel="Canal de texto")
async def slash_setwelcome(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    if channel is None:
        data.setdefault("config", {})["welcome_channel"] = None
        save_data_to_github("Unset welcome channel")
        await interaction.response.send_message("Canal de boas-vindas removido.")
    else:
        data.setdefault("config", {})["welcome_channel"] = str(channel.id)
        save_data_to_github("Set welcome channel")
        await interaction.response.send_message(f"Canal de boas-vindas definido: {channel.mention}")
        
#/Canal_xp
@tree.command(name="canal_xp", description="Define o canal onde serão enviadas as mensagens de level up (admin)")
@app_commands.describe(channel="Canal onde o bot vai enviar as mensagens de level up")
async def set_levelup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    data.setdefault("config", {})["levelup_channel"] = channel.id
    save_data_to_github("Set level up channel")

    await interaction.response.send_message(f"✅ Canal de level up definido para {channel.mention}.", ephemeral=False)


# reajir_com_emoji
reactionrole_group = app_commands.Group(name="reajir_com_emoji", description="Gerenciar reaction roles (admin)")

@reactionrole_group.command(name="criar", description="Cria mensagem com reação e mapeia para um cargo (admin)")
@app_commands.describe(channel="Canal para enviar a mensagem", content="Conteúdo da mensagem", emoji="Emoji (custom <:_name_:id> ou unicode)", role="Cargo a ser atribuído")
async def rr_create(interaction: discord.Interaction, channel: discord.TextChannel, content: str, emoji: str, role: discord.Role):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    # Parse emoji (custom or unicode)
    parsed = parse_emoji_str(emoji, guild=interaction.guild)
    # Send message
    try:
        sent = await channel.send(content)
    except Exception as e:
        await interaction.followup.send(f"Falha ao enviar mensagem: {e}")
        return
    # Add reaction
    try:
        if isinstance(parsed, discord.Emoji) or isinstance(parsed, discord.PartialEmoji):
            await sent.add_reaction(parsed)
            # store mapping by emoji id string
            key = str(parsed.id)
        else:
            # unicode char
            await sent.add_reaction(parsed)
            key = str(parsed)
    except Exception as e:
        # cleanup: delete message if reaction failed?
        await interaction.followup.send(f"Falha ao reagir com o emoji: {e}")
        return
    # store mapping
    data.setdefault("reaction_roles", {}).setdefault(str(sent.id), {})[key] = str(role.id)
    save_data_to_github("reactionrole create")
    add_log(f"reactionrole created msg={sent.id} emoji={key} role={role.id}")
    await interaction.followup.send(f"Mensagem criada em {channel.mention} com ID `{sent.id}`. Reaja para receber o cargo {role.mention}.")
    
@reactionrole_group.command(name="multi", description="Adiciona vários emojis e cargos a uma mesma mensagem (admin)")
@app_commands.describe(
    message_id="ID da mensagem existente para adicionar as reações",
    emoji_cargo="Lista de emoji:cargo separados por vírgula."
)
async def rr_multi(interaction: discord.Interaction, message_id: str, emoji_cargo: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return

    guild = interaction.guild
    try:
        msg = await guild.get_channel(interaction.channel_id).fetch_message(int(message_id))
    except Exception:
        await interaction.response.send_message("❌ Mensagem não encontrada. Verifique o ID.", ephemeral=True)
        return

    # Processa os pares emoji:cargo
    pairs = [x.strip() for x in emoji_cargo.split(",") if ":" in x]
    if not pairs:
        await interaction.response.send_message("❌ Formato inválido. Use emoji:cargo separados por vírgula.", ephemeral=True)
        return

    data.setdefault("reaction_roles", {}).setdefault(str(msg.id), {})

    added = []
    for pair in pairs:
        emoji_str, role_name = pair.split(":", 1)
        emoji_str, role_name = emoji_str.strip(), role_name.strip()

        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            await interaction.followup.send(f"⚠️ Cargo `{role_name}` não encontrado.")
            continue

        parsed = parse_emoji_str(emoji_str, guild)
        if not parsed:
            await interaction.followup.send(f"⚠️ Emoji `{emoji_str}` inválido.")
            continue

        # Adiciona reação e salva
        try:
            await msg.add_reaction(parsed)
            key = str(parsed.id) if isinstance(parsed, (discord.Emoji, discord.PartialEmoji)) else str(parsed)
            data["reaction_roles"][str(msg.id)][key] = str(role.id)
            added.append(f"{emoji_str} → {role.name}")
        except Exception as e:
            await interaction.followup.send(f"Erro ao adicionar {emoji_str}: {e}")

    save_data_to_github("ReactionRole multi")
    if added:
        await interaction.response.send_message(f"✅ Adicionados:\n" + "\n".join(added))
    else:
        await interaction.response.send_message("Nenhum emoji/cargo válido foi adicionado.")


@reactionrole_group.command(name="remover", description="Remove uma emoji com reação de uma mensagem (admin)")
@app_commands.describe(message_id="ID da mensagem", emoji="Emoji usado quando criado")
async def rr_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    mapping = data.get("reaction_roles", {}).get(str(message_id), {})
    if not mapping:
        await interaction.response.send_message("Nenhum mapeamento encontrado para essa mensagem.", ephemeral=True)
        return
    # normalize emoji keys: try id and raw string
    parsed = parse_emoji_str(emoji, guild=interaction.guild)
    key_candidates = [str(parsed)]
    if isinstance(parsed, (discord.Emoji, discord.PartialEmoji)):
        key_candidates.append(str(parsed.id))
        if parsed.name:
            key_candidates.append(parsed.name)
    found = None
    for k in key_candidates:
        if k in mapping:
            found = k
            break
    if not found:
        await interaction.response.send_message("Emoji não encontrado no mapeamento da mensagem.", ephemeral=True)
        return
    del mapping[found]
    # if message mapping empty, remove message key
    if not mapping:
        data["reaction_roles"].pop(str(message_id), None)
    save_data_to_github("reactionrole remove")
    add_log(f"reactionrole removed msg={message_id} emoji={found}")
    await interaction.response.send_message("Removido com sucesso.", ephemeral=False)

@reactionrole_group.command(name="lista", description="Lista de reação de emoji configuradas")
async def rr_list(interaction: discord.Interaction):
    if not is_admin_check(interaction):
        await interaction.response.send_message("Você não tem permissão.", ephemeral=True)
        return
    rr = data.get("reaction_roles", {})
    if not rr:
        await interaction.response.send_message("Nenhuma reação com emoji configurada.", ephemeral=True)
        return
    lines = []
    for msgid, mapping in rr.items():
        parts = []
        for ekey, rid in mapping.items():
            parts.append(f"{ekey}→<@&{rid}>")
        lines.append(f"Msg `{msgid}`: " + ", ".join(parts))
    content = "\n".join(lines)
    # if too long, send as file
    if len(content) > 1900:
        await interaction.response.send_message("Resultado muito grande, enviando arquivo...", ephemeral=True)
        await interaction.followup.send(file=discord.File(BytesIO(content.encode()), filename="reactionroles.txt"))
    else:
        await interaction.response.send_message(f"Reaction roles:\n{content}", ephemeral=False)

# add the group to the tree
tree.add_command(reactionrole_group)

# -------------------------
# Start bot
# -------------------------
if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)
