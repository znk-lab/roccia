import os
import json
import base64
import re
import requests
import time
import secrets
import hashlib
from io import BytesIO
from threading import Thread
from datetime import datetime, timezone, timedelta
from functools import wraps
import asyncio
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
import discord
from discord import app_commands
from discord.ext import commands
from discord import ui, Interaction, ButtonStyle
from PIL import Image, ImageDraw, ImageFont
import uuid

# ========================
# CONFIGURAÇÃO DO AMBIENTE
# ========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER", "pobonsanto-byte")
GITHUB_REPO = os.getenv("GITHUB_REPO", "imune-bot-data")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
PORT = int(os.getenv("PORT", 8080))
GUILD_ID = os.getenv("GUILD_ID")

# Configurações do site
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://seu-site.onrender.com/callback")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

if not BOT_TOKEN or not GITHUB_TOKEN:
    raise SystemExit("Defina BOT_TOKEN e GITHUB_TOKEN nas variáveis de ambiente.")

GITHUB_API_CONTENT = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATA_FILE}"

# ========================
# Sistema de ações
# ========================
acoes_fila_bot = []
processador_acoes_task = None
processador_acoes_rodando = False

# ========================
# FLASK APP
# ========================
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ========================
# BOT SETUP
# ========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# ========================
# ESTRUTURA DE DADOS
# ========================
dados = {
    "xp": {},
    "nivel": {},
    "advertencias": {},
    "reacoes_cargos": {},
    "config": {
        "canal_boas_vindas": None,
        "mensagem_boas_vindas": "Olá {member}, seja bem-vindo(a)!",
        "fundo_boas_vindas": "",
        "taxa_xp": 3,
        "canal_levelup": None,
        "canal_logs": None,
        "canal_perfil": None,
        "canal_rank": None,
        "pix_link": ""
    },
    "logs": [],
    "fila": {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    },
    "cargos_nivel": {},
    "canais_links_bloqueados": [],
    "botoes_cargos": {},
    "links_fila": {
        "discord_convite": "",
        "botoes_precos": []
    },
    "anti_spam": {
        "ativado": True,
        "limite_mensagens": 5,
        "intervalo_segundos": 5,
        "tempo_mute_minutos": 2,
        "remover_xp": True,
        "xp_penalidade": 50,
        "deletar_mensagens": True,
        "cargos_ignorados": ["Administrador", "Moderador", "Staff", "Dono"],
        "comandos_ignorados": [
            "$w", "$wa", "$wg", "$h", "$ha", "$hg",
            "$W", "$WA", "$WG", "$H", "$HA", "$HG",
            "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu",
            "$daily", "$Daily", "$rep", "$Rep", "$rep+", "$Rep+",
            "$bitesthedust", "$kb", "$Kb", "$l", "$L", "$ldk", "$Ldk",
        ]
    },
    "recompensas_fidelidade": [
        {
            "id": "quests_60",
            "nome": "1 Dia de Quests Diárias Grátis",
            "pontos": 60,
            "tipo": "servico",
            "desconto": 0
        },
        {
            "id": "desafio_100",
            "nome": "Desafio Rápido Grátis (Portinha/Hologramas)",
            "pontos": 100,
            "tipo": "servico",
            "desconto": 0
        },
        {
            "id": "cupom_5",
            "nome": "Cupom de R$ 5,00",
            "pontos": 100,
            "tipo": "cupom",
            "desconto": 5.0
        },
        {
            "id": "analise_200",
            "nome": "1 Análise de Conta / Companion Quest Grátis",
            "pontos": 200,
            "tipo": "servico",
            "desconto": 0
        },
        {
            "id": "cupom_10",
            "nome": "Cupom de R$ 10,00",
            "pontos": 200,
            "tipo": "cupom",
            "desconto": 10.0
        },
        {
            "id": "build_400",
            "nome": "1 Build Completa de Personagem Grátis",
            "pontos": 400,
            "tipo": "servico",
            "desconto": 0
        },
        {
            "id": "cupom_20",
            "nome": "Cupom de R$ 20,00",
            "pontos": 400,
            "tipo": "cupom",
            "desconto": 20.0
        }
    ],
    "credenciais": {}  # { "uid": { "hash": "sha256(salt+senha)", "salt": "..." } }
}

mensagens_recentes = {}

# ==========================================
# CONFIGURAÇÃO DO SISTEMA DE FIDELIDADE (dinâmico)
# ==========================================

def obter_recompensas():
    if "recompensas_fidelidade" not in dados:
        dados["recompensas_fidelidade"] = []
    return dados["recompensas_fidelidade"]


def obter_recompensa_por_id(recompensa_id: str):
    recs = obter_recompensas()
    for r in recs:
        if r["id"] == recompensa_id:
            return r
    return None


def obter_ou_criar_perfil_fidelidade(uid: str):
    dados.setdefault("fidelidade", {})
    uid_str = str(uid).strip()

    if uid_str not in dados["fidelidade"]:
        dados["fidelidade"][uid_str] = {
            "pontos": 0,
            "ultimo_pedido_ts": time.time(),
            "historico": [],
            "cupons": []
        }

    perfil = dados["fidelidade"][uid_str]
    agora = time.time()

    if perfil["pontos"] > 0 and (agora - perfil.get("ultimo_pedido_ts", agora)) > (60 * 86400):
        perfil["pontos"] = 0
        perfil["pontos_expirados"] = True

    for cupom in perfil.get("cupons", []):
        if not cupom.get("usado", False) and not cupom.get("expirado", False):
            if (agora - cupom.get("criado_em_ts", agora)) > (30 * 86400):
                cupom["expirado"] = True

    return perfil


# ========================
# FUNÇÕES DE HASH DE SENHA
# ========================
def hash_senha(senha: str) -> dict:
    salt = secrets.token_hex(16)
    hash_obj = hashlib.sha256((salt + senha).encode()).hexdigest()
    return {"salt": salt, "hash": hash_obj}


def verificar_senha(senha: str, cred: dict) -> bool:
    if not cred:
        return False
    hash_calculado = hashlib.sha256((cred["salt"] + senha).encode()).hexdigest()
    return hash_calculado == cred["hash"]


def validar_senha(senha: str) -> bool:
    # Mínimo 8 caracteres, pelo menos uma minúscula, uma maiúscula, um número e um caractere especial
    padrao = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z0-9]).{8,}$'
    return re.match(padrao, senha) is not None


# ========================
# FUNÇÕES UTILITÁRIAS
# ========================
def agora_br():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))


def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}


def carregar_dados_github():
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            conteudo_b64 = js.get("content", "")
            if conteudo_b64:
                raw = base64.b64decode(conteudo_b64)
                carregado = json.loads(raw.decode("utf-8"))
                dados.update(carregado)
                # Garantir campos obrigatórios
                if "fila" not in dados:
                    dados["fila"] = {
                        "nome": "Fila de Serviços",
                        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
                        "entradas": [],
                        "historico": []
                    }
                if "botoes_cargos" not in dados:
                    dados["botoes_cargos"] = {}
                if "cargos_nivel" not in dados:
                    dados["cargos_nivel"] = {}
                if "canais_links_bloqueados" not in dados:
                    dados["canais_links_bloqueados"] = []
                if "links_fila" not in dados:
                    dados["links_fila"] = {"discord_convite": "", "botoes_precos": []}
                if "anti_spam" not in dados:
                    dados["anti_spam"] = {
                        "ativado": True,
                        "limite_mensagens": 5,
                        "intervalo_segundos": 5,
                        "tempo_mute_minutos": 2,
                        "remover_xp": True,
                        "xp_penalidade": 50,
                        "deletar_mensagens": True,
                        "cargos_ignorados": ["Administrador", "Moderador", "Staff", "Dono"],
                        "comandos_ignorados": [
                            "$w", "$wa", "$wg", "$h", "$ha", "$hg",
                            "$W", "$WA", "$WG", "$H", "$HA", "$HG",
                            "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
                        ]
                    }
                if "config" not in dados:
                    dados["config"] = {
                        "canal_boas_vindas": None,
                        "mensagem_boas_vindas": "Olá {member}, seja bem-vindo(a)!",
                        "fundo_boas_vindas": "",
                        "taxa_xp": 3,
                        "canal_levelup": None,
                        "canal_logs": None,
                        "canal_perfil": None,
                        "canal_rank": None,
                        "pix_link": ""
                    }
                if "botoes_precos" not in dados.get("links_fila", {}):
                    dados["links_fila"]["botoes_precos"] = []
                if "recompensas_fidelidade" not in dados:
                    dados["recompensas_fidelidade"] = [
                        {"id": "quests_60", "nome": "1 Dia de Quests Diárias Grátis", "pontos": 60, "tipo": "servico",
                         "desconto": 0},
                        {"id": "desafio_100", "nome": "Desafio Rápido Grátis (Portinha/Hologramas)", "pontos": 100,
                         "tipo": "servico", "desconto": 0},
                        {"id": "cupom_5", "nome": "Cupom de R$ 5,00", "pontos": 100, "tipo": "cupom", "desconto": 5.0},
                        {"id": "analise_200", "nome": "1 Análise de Conta / Companion Quest Grátis", "pontos": 200,
                         "tipo": "servico", "desconto": 0},
                        {"id": "cupom_10", "nome": "Cupom de R$ 10,00", "pontos": 200, "tipo": "cupom", "desconto": 10.0},
                        {"id": "build_400", "nome": "1 Build Completa de Personagem Grátis", "pontos": 400,
                         "tipo": "servico", "desconto": 0},
                        {"id": "cupom_20", "nome": "Cupom de R$ 20,00", "pontos": 400, "tipo": "cupom", "desconto": 20.0}
                    ]
                if "credenciais" not in dados:
                    dados["credenciais"] = {}
                print("✅ Dados carregados do GitHub.")
                return True
        else:
            print(f"⚠️ GitHub GET retornou {r.status_code} — iniciando com dados limpos.")
    except Exception as e:
        print(f"❌ Erro ao carregar dados do GitHub: {e}")
    return False


def salvar_dados_github(mensagem="Atualização do bot"):
    try:
        r = requests.get(GITHUB_API_CONTENT, headers=_gh_headers(), params={"ref": BRANCH}, timeout=15)
        sha = None
        if r.status_code == 200:
            sha = r.json().get("sha")

        conteudo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
        payload = {
            "message": f"{mensagem} @ {agora_br().isoformat()}",
            "content": base64.b64encode(conteudo).decode("utf-8"),
            "branch": BRANCH
        }
        if sha:
            payload["sha"] = sha

        put = requests.put(GITHUB_API_CONTENT, headers=_gh_headers(), json=payload, timeout=30)
        if put.status_code in (200, 201):
            print("✅ Dados salvos no GitHub.")
            return True
        else:
            print(f"❌ Erro ao salvar no GitHub: {put.status_code}, {put.text[:400]}")
    except Exception as e:
        print(f"❌ Exception saving to GitHub: {e}")
    return False


def adicionar_log(entrada):
    ts = agora_br().isoformat()
    dados.setdefault("logs", []).append({"ts": ts, "entrada": entrada})
    try:
        salvar_dados_github(f"log: {entrada}")
    except Exception:
        pass


def xp_por_mensagem():
    return 15


def xp_para_nivel(xp):
    nivel = int((xp / 100) ** 0.6) + 1
    return max(nivel, 1)


def escape_html(texto):
    if not texto:
        return ""
    return (texto
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
            )


# ========================
# FUNÇÕES ANTI-SPAM E IGNORADOS
# ========================

def verificar_comando_ignorado(conteudo: str) -> bool:
    conteudo_lower = conteudo.lower().strip()
    comandos_ignorados = dados.get("anti_spam", {}).get("comandos_ignorados", [])
    for comando in comandos_ignorados:
        if conteudo_lower.startswith(comando.lower()):
            return True
        if conteudo_lower == comando.lower():
            return True
    return False


def verificar_cargo_ignorado(member: discord.Member) -> bool:
    cargos_ignorados = dados.get("anti_spam", {}).get("cargos_ignorados", [])
    cargos_membro = [role.name for role in member.roles]
    for cargo_ignorado in cargos_ignorados:
        if cargo_ignorado in cargos_membro:
            return True
    return False


def limpar_mensagens_antigas(user_id: int):
    if user_id not in mensagens_recentes:
        return
    intervalo = dados.get("anti_spam", {}).get("intervalo_segundos", 5)
    agora = time.time()
    mensagens_recentes[user_id] = [
        ts for ts in mensagens_recentes[user_id]
        if agora - ts < intervalo
    ]
    if not mensagens_recentes[user_id]:
        del mensagens_recentes[user_id]


def registrar_mensagem(user_id: int) -> int:
    agora = time.time()
    if user_id not in mensagens_recentes:
        mensagens_recentes[user_id] = []
    mensagens_recentes[user_id].append(agora)
    limpar_mensagens_antigas(user_id)
    return len(mensagens_recentes.get(user_id, []))


async def aplicar_mute(member: discord.Member, duracao_minutos: int = 2):
    guild = member.guild
    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role:
        try:
            mute_role = await guild.create_role(name="Muted", permissions=discord.Permissions.none())
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, add_reactions=False, speak=False)
                except:
                    pass
            print(f"✅ Cargo 'Muted' criado no servidor {guild.name}")
        except Exception as e:
            print(f"❌ Erro ao criar cargo de mute: {e}")
            return False

    try:
        await member.add_roles(mute_role, reason=f"Anti-spam: {duracao_minutos} minutos de mute")
        async def remover_mute():
            await asyncio.sleep(duracao_minutos * 60)
            try:
                await member.remove_roles(mute_role, reason="Fim do mute por spam")
            except:
                pass
        asyncio.create_task(remover_mute())
        return True
    except Exception as e:
        print(f"❌ Erro ao aplicar mute: {e}")
        return False


async def deletar_mensagens_spam(member: discord.Member, channel: discord.TextChannel, quantidade: int):
    if not dados.get("anti_spam", {}).get("deletar_mensagens", True):
        return
    try:
        async for msg in channel.history(limit=quantidade + 5):
            if msg.author == member:
                try:
                    await msg.delete()
                    await asyncio.sleep(0.5)
                except:
                    pass
    except:
        pass


async def remover_xp_por_spam(member: discord.Member):
    if not dados.get("anti_spam", {}).get("remover_xp", True):
        return False
    uid = str(member.id)
    penalidade = dados.get("anti_spam", {}).get("xp_penalidade", 50)
    xp_atual = dados.get("xp", {}).get(uid, 0)
    novo_xp = max(0, xp_atual - penalidade)
    dados["xp"][uid] = novo_xp
    novo_nivel = xp_para_nivel(novo_xp)
    dados["nivel"][uid] = novo_nivel
    salvar_dados_github(f"Anti-spam: {penalidade} XP removido de {member.name}")
    return True


# ========================
# SISTEMA DE FILA
# ========================

def obter_dados_fila():
    dados.setdefault("fila", {
        "nome": "Fila de Serviços",
        "configuracoes": {"tamanho_maximo": 50, "aberta": True},
        "entradas": [],
        "historico": []
    })
    return dados["fila"]


def salvar_fila():
    return salvar_dados_github("Atualização da fila")


def adicionar_fila(nome_usuario: str, servico: str, jogo: str = "", usuario_id: str = None, uid: str = None):
    fila = obter_dados_fila()
    if not fila["configuracoes"]["aberta"]:
        return False, "Fila está fechada no momento"
    if len(fila["entradas"]) >= fila["configuracoes"]["tamanho_maximo"]:
        return False, "Fila está cheia"
    for entrada in fila["entradas"]:
        if entrada["nome_usuario"].lower() == nome_usuario.lower():
            return False, f"{nome_usuario} já está na fila"
    entrada = {
        "id": str(int(datetime.now().timestamp() * 1000)),
        "nome_usuario": nome_usuario,
        "servico": servico,
        "jogo": jogo,
        "usuario_id": usuario_id or nome_usuario,
        "uid": uid or "",  # não usar fallback para nome
        "timestamp": agora_br().isoformat(),
        "status": "aguardando",
        "posicao": len(fila["entradas"]) + 1
    }
    fila["entradas"].append(entrada)
    atualizar_posicoes(fila["entradas"])
    salvar_fila()
    adicionar_log(f"fila_adicionar: {nome_usuario} - {servico} - {jogo}")
    return True, entrada


def remover_fila(entrada_id: str):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["removido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            if len(fila["historico"]) > 100:
                fila["historico"] = fila["historico"][-100:]
            atualizar_posicoes(fila["entradas"])
            salvar_fila()
            adicionar_log(f"fila_remover: {removido['nome_usuario']}")
            return True, removido
    return False, None


def atualizar_posicoes(entradas):
    for i, entrada in enumerate(entradas):
        entrada["posicao"] = i + 1
        entrada["status"] = "aguardando"


def mover_cima(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i > 0:
            entradas[i], entradas[i - 1] = entradas[i - 1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_fila()
            return True, entrada
    return False, None


def mover_baixo(entrada_id: str):
    fila = obter_dados_fila()
    entradas = fila["entradas"]
    for i, entrada in enumerate(entradas):
        if entrada["id"] == entrada_id and i < len(entradas) - 1:
            entradas[i], entradas[i + 1] = entradas[i + 1], entradas[i]
            atualizar_posicoes(entradas)
            salvar_fila()
            return True, entrada
    return False, None


def concluir_servico(entrada_id: str):
    fila = obter_dados_fila()
    for i, entrada in enumerate(fila["entradas"]):
        if entrada["id"] == entrada_id:
            removido = fila["entradas"].pop(i)
            removido["status"] = "concluido"
            removido["concluido_em"] = agora_br().isoformat()
            fila["historico"].append(removido)
            atualizar_posicoes(fila["entradas"])
            salvar_fila()
            adicionar_log(f"fila_concluir: {removido['nome_usuario']}")
            return True, removido
    return False, None


def limpar_fila():
    fila = obter_dados_fila()
    for entrada in fila["entradas"]:
        entrada["status"] = "limpo"
        entrada["limpo_em"] = agora_br().isoformat()
        fila["historico"].append(entrada)
    fila["entradas"] = []
    salvar_fila()
    adicionar_log("fila_limpa")
    return True


def alternar_fila(aberto: bool = None):
    fila = obter_dados_fila()
    if aberto is None:
        fila["configuracoes"]["aberta"] = not fila["configuracoes"]["aberta"]
    else:
        fila["configuracoes"]["aberta"] = aberto
    salvar_fila()
    return fila["configuracoes"]["aberta"]


def definir_tamanho_maximo(tamanho: int):
    fila = obter_dados_fila()
    fila["configuracoes"]["tamanho_maximo"] = max(1, min(tamanho, 100))
    salvar_fila()
    return fila["configuracoes"]["tamanho_maximo"]


def definir_nome_fila(nome: str):
    fila = obter_dados_fila()
    fila["nome"] = nome[:50]
    salvar_fila()
    return fila["nome"]


# ========================
# FUNÇÕES PARA LINKS DA FILA (MÚLTIPLOS BOTÕES)
# ========================
def obter_links_fila():
    dados.setdefault("links_fila", {"discord_convite": "", "botoes_precos": []})
    return dados["links_fila"]


def salvar_links_fila(discord_convite: str):
    dados["links_fila"]["discord_convite"] = discord_convite or ""
    return salvar_dados_github("Links da fila atualizados")


def adicionar_botao_preco(nome: str, url: str):
    if not nome or not url:
        return False
    dados["links_fila"].setdefault("botoes_precos", [])
    dados["links_fila"]["botoes_precos"].append({"nome": nome[:30], "url": url[:500]})
    return salvar_dados_github(f"Botão de preço adicionado: {nome}")


def remover_botao_preco(index: int):
    botoes = dados["links_fila"].get("botoes_precos", [])
    if 0 <= index < len(botoes):
        removido = botoes.pop(index)
        salvar_dados_github(f"Botão de preço removido: {removido['nome']}")
        return True
    return False


def atualizar_botao_preco(index: int, nome: str, url: str):
    botoes = dados["links_fila"].get("botoes_precos", [])
    if 0 <= index < len(botoes):
        botoes[index] = {"nome": nome[:30], "url": url[:500]}
        salvar_dados_github(f"Botão de preço atualizado: {nome}")
        return True
    return False


# ========================
# SISTEMA DE AÇÕES DO SITE
# ========================
def executar_acao_bot(tipo_acao, **kwargs):
    acoes_fila_bot.append({
        "tipo": tipo_acao,
        "dados": kwargs,
        "timestamp": agora_br().isoformat()
    })
    print(f"🤖 [AÇÃO BOT] Adicionada ação: {tipo_acao}")
    return True


async def executar_acao_bot_interno(acao):
    tipo_acao = acao["tipo"]
    dados_acao = acao["dados"]

    print(f"\n{'='*50}")
    print(f"🤖 EXECUTANDO AÇÃO: {tipo_acao}")
    print(f"{'='*50}")

    if not bot.is_ready():
        print("❌ Bot não está pronto!")
        return False

    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if not guild:
        print(f"❌ Servidor {GUILD_ID} não encontrado!")
        return False

    try:
        if tipo_acao == "criar_embed":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            cor = discord.Color.blue()
            if dados_acao.get('cor'):
                try:
                    cor_hex = dados_acao['cor'].replace('#', '')
                    cor = discord.Color(int(cor_hex, 16))
                except:
                    pass
            embed = discord.Embed(
                title=dados_acao["titulo"],
                description=dados_acao["corpo"],
                color=cor
            )
            if dados_acao.get('url_imagem'):
                embed.set_image(url=dados_acao['url_imagem'])
            texto_mencao = ""
            if dados_acao.get('mencao') == 'everyone':
                texto_mencao = "@everyone"
            elif dados_acao.get('mencao') == 'here':
                texto_mencao = "@here"
            await canal.send(content=texto_mencao, embed=embed)
            print(f"✅ Embed enviada para #{canal.name}")
            return True

        elif tipo_acao == "criar_reacao_cargo":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            mensagem = await canal.send(dados_acao["conteudo"])
            mensagem_id = str(mensagem.id)

            pares_str = dados_acao.get("emoji_cargo", "")
            pares = []
            par_atual = ""
            contador_chaves = 0
            for char in pares_str:
                if char == '<':
                    contador_chaves += 1
                elif char == '>':
                    contador_chaves -= 1
                if char == ',' and contador_chaves == 0:
                    if par_atual.strip():
                        pares.append(par_atual.strip())
                        par_atual = ""
                else:
                    par_atual += char
            if par_atual.strip():
                pares.append(par_atual.strip())

            EMOJI_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):([0-9]+)>")
            EMOJI_NOME_RE = re.compile(r":([a-zA-Z0-9_]+):")

            def processar_emoji_str(emoji_str, guild):
                if not emoji_str:
                    return None
                emoji_str = emoji_str.strip()
                m = EMOJI_RE.match(emoji_str)
                if m:
                    nome, id_str = m.groups()
                    try:
                        eid = int(id_str)
                        animado = emoji_str.startswith('<a:')
                        if guild:
                            e = discord.utils.get(guild.emojis, id=eid)
                            if e:
                                return e
                        return discord.PartialEmoji(name=nome, id=eid, animated=animado)
                    except:
                        pass
                m2 = EMOJI_NOME_RE.match(emoji_str)
                if m2:
                    nome_emoji = m2.group(1)
                    if guild:
                        emoji = discord.utils.get(guild.emojis, name=nome_emoji)
                        if emoji:
                            return emoji
                    emojis_padrao = {
                        "thumbsup": "👍", "thumbsdown": "👎", "check": "✅", "x": "❌",
                        "warning": "⚠️", "exclamation": "❗", "question": "❓", "star": "⭐",
                        "heart": "❤️", "fire": "🔥", "rocket": "🚀", "tada": "🎉"
                    }
                    if nome_emoji.lower() in emojis_padrao:
                        return emojis_padrao[nome_emoji.lower()]
                    return emoji_str
                return emoji_str

            dados_reacoes = {}
            for par in pares:
                par = par.strip()
                if not par:
                    continue
                if ":" in par:
                    try:
                        emoji_str, nome_cargo = par.split(":", 1)
                        cargo = discord.utils.get(guild.roles, name=nome_cargo.strip())
                        if not cargo:
                            continue
                        emoji_processado = processar_emoji_str(emoji_str.strip(), guild)
                        if not emoji_processado:
                            continue
                        if isinstance(emoji_processado, (discord.Emoji, discord.PartialEmoji)):
                            await mensagem.add_reaction(emoji_processado)
                            chave = str(emoji_processado.id)
                        else:
                            await mensagem.add_reaction(emoji_processado)
                            chave = str(emoji_processado)
                        dados_reacoes[chave] = str(cargo.id)
                    except:
                        continue

            if dados_reacoes:
                dados.setdefault("reacoes_cargos", {})[mensagem_id] = dados_reacoes
                salvar_dados_github("Reação cargo via site")
                return True
            else:
                try:
                    await mensagem.delete()
                except:
                    pass
                return False

        elif tipo_acao == "criar_botoes_cargo":
            canal_id = int(dados_acao["canal_id"])
            canal = guild.get_channel(canal_id)
            if not canal:
                return False
            pares = dados_acao.get("cargos", "").split(",")
            dicionario_botoes = {}
            for par in pares:
                if ":" in par:
                    try:
                        nome_botao, nome_cargo = par.split(":", 1)
                        cargo = discord.utils.get(guild.roles, name=nome_cargo.strip())
                        if cargo:
                            dicionario_botoes[nome_botao.strip()] = cargo.id
                    except:
                        pass
            if dicionario_botoes:
                class PersistentRoleButton(ui.Button):
                    def __init__(self, label: str, cargo_id: int, mensagem_id: int):
                        super().__init__(label=label, style=ButtonStyle.primary)
                        self.cargo_id = cargo_id
                        self.mensagem_id = mensagem_id

                    async def callback(self, interaction: Interaction):
                        guild = interaction.guild
                        membro = interaction.user
                        cargo = guild.get_role(self.cargo_id)
                        if not cargo:
                            await interaction.response.send_message("Cargo não encontrado.", ephemeral=True)
                            return
                        if cargo in membro.roles:
                            await membro.remove_roles(cargo, reason="Botão de cargo")
                            await interaction.response.send_message(f"Você **removeu** o cargo {cargo.mention}.",
                                                                    ephemeral=True)
                        else:
                            await membro.add_roles(cargo, reason="Botão de cargo")
                            await interaction.response.send_message(f"Você **recebeu** o cargo {cargo.mention}.",
                                                                    ephemeral=True)
                        adicionar_log(f"botao_cargo: usuario={membro.id} cargo={cargo.id}")

                class PersistentRoleButtonView(ui.View):
                    def __init__(self, mensagem_id: int, dicionario_botoes: dict):
                        super().__init__(timeout=None)
                        self.mensagem_id = mensagem_id
                        for label, cargo_id in dicionario_botoes.items():
                            self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id, mensagem_id=mensagem_id))

                view = PersistentRoleButtonView(0, dicionario_botoes)
                enviado = await canal.send(dados_acao["conteudo"], view=view)
                view.mensagem_id = enviado.id
                for item in view.children:
                    if isinstance(item, PersistentRoleButton):
                        item.mensagem_id = enviado.id
                dados.setdefault("botoes_cargos", {})[str(enviado.id)] = dicionario_botoes
                salvar_dados_github("Botões de cargo via site")
                return True
            return False

        elif tipo_acao == "advertir_membro":
            membro_id = int(dados_acao["membro_id"])
            membro = guild.get_member(membro_id)
            if not membro:
                return False
            entrada = {
                "por": "admin_site",
                "motivo": dados_acao["motivo"],
                "ts": agora_br().strftime("%d/%m/%Y %H:%M"),
                "admin": dados_acao.get('admin', 'Admin')
            }
            dados.setdefault("advertencias", {}).setdefault(str(membro.id), []).append(entrada)
            salvar_dados_github(f"Advertência via site: {membro.display_name}")
            return True

        elif tipo_acao == "configurar_boas_vindas":
            config = dados.setdefault("config", {})
            if 'canal_id' in dados_acao:
                config["canal_boas_vindas"] = dados_acao['canal_id']
            if 'mensagem' in dados_acao:
                config["mensagem_boas_vindas"] = dados_acao['mensagem']
            if 'imagem_url' in dados_acao:
                config["fundo_boas_vindas"] = dados_acao['imagem_url']
            salvar_dados_github("Config boas-vindas atualizada")
            return True

        elif tipo_acao == "configurar_xp":
            config = dados.setdefault("config", {})
            if 'taxa' in dados_acao:
                config["taxa_xp"] = dados_acao['taxa']
            if 'canal_id' in dados_acao:
                config["canal_levelup"] = dados_acao['canal_id']
            salvar_dados_github("Config XP atualizada")
            return True

        elif tipo_acao == "configurar_comandos":
            config = dados.setdefault("config", {})
            if 'canal_perfil' in dados_acao:
                canal_perfil_atual = config.get("canal_perfil")
                novo_canal_perfil = dados_acao['canal_perfil']
                if novo_canal_perfil and canal_perfil_atual == novo_canal_perfil:
                    config["canal_perfil"] = None
                else:
                    config["canal_perfil"] = novo_canal_perfil if novo_canal_perfil else None
            if 'canal_rank' in dados_acao:
                canal_rank_atual = config.get("canal_rank")
                novo_canal_rank = dados_acao['canal_rank']
                if novo_canal_rank and canal_rank_atual == novo_canal_rank:
                    config["canal_rank"] = None
                else:
                    config["canal_rank"] = novo_canal_rank if novo_canal_rank else None
            salvar_dados_github("Config canais de comandos atualizada")
            return True

        elif tipo_acao == "adicionar_cargo_nivel":
            dados.setdefault("cargos_nivel", {})[str(dados_acao['nivel'])] = dados_acao['cargo_id']
            salvar_dados_github(f"Cargo para nível {dados_acao['nivel']} adicionado")
            return True

        elif tipo_acao == "remover_cargo_nivel":
            nivel = str(dados_acao['nivel'])
            if nivel in dados.get("cargos_nivel", {}):
                del dados["cargos_nivel"][nivel]
                salvar_dados_github(f"Cargo do nível {nivel} removido")
            return True

        elif tipo_acao == "alternar_bloqueio_links":
            canal_id = int(dados_acao["canal_id"])
            canais = dados.setdefault("canais_links_bloqueados", [])
            if canal_id in canais:
                canais.remove(canal_id)
            else:
                canais.append(canal_id)
            salvar_dados_github(f"Bloqueio de links alternado no canal {canal_id}")
            return True

        elif tipo_acao == "configurar_anti_spam":
            anti_spam = dados.setdefault("anti_spam", {})
            if 'ativado' in dados_acao:
                anti_spam["ativado"] = dados_acao['ativado']
            if 'limite_mensagens' in dados_acao:
                anti_spam["limite_mensagens"] = dados_acao['limite_mensagens']
            if 'intervalo_segundos' in dados_acao:
                anti_spam["intervalo_segundos"] = dados_acao['intervalo_segundos']
            if 'tempo_mute_minutos' in dados_acao:
                anti_spam["tempo_mute_minutos"] = dados_acao['tempo_mute_minutos']
            if 'remover_xp' in dados_acao:
                anti_spam["remover_xp"] = dados_acao['remover_xp']
            if 'xp_penalidade' in dados_acao:
                anti_spam["xp_penalidade"] = dados_acao['xp_penalidade']
            if 'deletar_mensagens' in dados_acao:
                anti_spam["deletar_mensagens"] = dados_acao['deletar_mensagens']
            if 'cargos_ignorados' in dados_acao:
                anti_spam["cargos_ignorados"] = [c.strip() for c in dados_acao['cargos_ignorados'].split(",") if c.strip()]
            if 'comandos_ignorados' in dados_acao:
                anti_spam["comandos_ignorados"] = [c.strip() for c in dados_acao['comandos_ignorados'].split(",") if
                                                   c.strip()]
            salvar_dados_github("Config anti-spam atualizada")
            return True

        else:
            print(f"❌ Tipo de ação desconhecido: {tipo_acao}")
            return False

    except Exception as e:
        print(f"❌ Erro: {e}")
        return False


async def processar_acoes_bot_continuo():
    global processador_acoes_rodando
    print("\n" + "=" * 60)
    print("🚀 PROCESSADOR DE AÇÕES INICIADO")
    print("=" * 60)
    processador_acoes_rodando = True
    if not bot.is_ready():
        await bot.wait_until_ready()
        await asyncio.sleep(2)
    while processador_acoes_rodando and not bot.is_closed():
        try:
            if acoes_fila_bot:
                acao = acoes_fila_bot.pop(0)
                await executar_acao_bot_interno(acao)
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠️ Erro no processador: {e}")
            await asyncio.sleep(5)
    print("⏹️ PROCESSADOR DE AÇÕES ENCERRADO")


def iniciar_processador_acoes():
    global processador_acoes_task, processador_acoes_rodando
    if processador_acoes_rodando:
        return False
    try:
        processador_acoes_task = bot.loop.create_task(processar_acoes_bot_continuo())
        print("✅ Processador de ações iniciado!")
        return True
    except Exception as e:
        print(f"❌ Erro ao iniciar processador: {e}")
        return False


# ========================
# ROTAS DO SITE
# ========================

@app.route("/", methods=["GET"])
def home():
    status_bot = "✅ Bot Online" if bot.is_ready() else "❌ Bot Offline"
    classe_bot = "online" if bot.is_ready() else "offline"
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel de Controle</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0a0a0a, #1a1a1a); margin: 0; padding: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #e0e0e0; }
            .container { background: #121212; border-radius: 20px; padding: 40px; text-align: center; max-width: 500px; width: 90%; border: 1px solid #333; }
            h1 { color: #5865F2; margin-bottom: 10px; }
            .status { padding: 10px; border-radius: 10px; margin: 20px 0; font-weight: bold; }
            .online { background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }
            .offline { background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }
            .btn { display: inline-block; background: #5865F2; color: white; padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 10px; transition: all 0.3s; }
            .btn:hover { background: #4752C4; transform: translateY(-2px); }
            .features { text-align: left; margin: 20px 0; padding: 15px; background: #1a1a1a; border-radius: 10px; border: 1px solid #333; }
            .features h3 { color: #5865F2; }
            .features li { margin: 8px 0; padding-left: 10px; list-style: none; }
            .features li:before { content: "✅"; margin-right: 10px; color: #5865F2; }
            code { background: #1a1a1a; padding: 2px 6px; border-radius: 4px; color: #4ade80; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1> Painel de Controle</h1>
            <div class="status {{ classe_bot }}">{{ status_bot }}</div>
            <div class="features">
                <h3> Funcionalidades:</h3>
                <ul>
                    <li>Sistema de XP e Níveis</li>
                    <li>Reação com Cargos</li>
                    <li>Boas-vindas Personalizadas</li>
                    <li>Sistema de Moderação</li>
                    <li>Botões de Cargos</li>
                    <li>Sistema de Fila de Serviços</li>
                    <li>Anti-Spam Automático</li>
                    <li>Comandos da Mudae NÃO ganham XP</li>
                    <li>Comandos /perfil e /rank podem ser configurados para canais específicos</li>
                </ul>
            </div>
            {% if 'usuario' not in session %}
                <a href='/login' class='btn'>🔐 Login com Discord</a>
            {% else %}
                <p>Olá, {{ session['usuario']['nome_usuario'] }}!</p>
                <a href="/dashboard" class="btn">🚀 Painel</a>
                <a href="/fila" class="btn">📋 Fila</a>
                <a href="/logout" class="btn">🚪 Sair</a>
            {% endif %}
            <p style="margin-top: 20px; color: #888;">Use <code>/perfil</code> e <code>/rank</code> no Discord (apenas nos canais configurados)</p>
        </div>
    </body>
    </html>
    """, status_bot=status_bot, classe_bot=classe_bot, session=session)


@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro: CLIENT_ID ou CLIENT_SECRET não configurados.", 500
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
    return redirect(url)


@app.route("/callback")
def callback():
    if not CLIENT_ID or not CLIENT_SECRET:
        return "Erro de configuração.", 500
    code = request.args.get('code')
    if not code:
        return "Erro: código não recebido", 400
    try:
        dados_req = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'scope': 'identify guilds'
        }
        r = requests.post('https://discord.com/api/oauth2/token', data=dados_req)
        if r.status_code != 200:
            return f"Erro ao obter token: {r.text[:100]}", 400
        access_token = r.json()['access_token']
        user_r = requests.get('https://discord.com/api/users/@me', headers={'Authorization': f'Bearer {access_token}'})
        if user_r.status_code != 200:
            return "Erro ao obter informações", 400
        user_data = user_r.json()
        guilds_r = requests.get('https://discord.com/api/users/@me/guilds', headers={'Authorization': f'Bearer {access_token}'})
        guilds = guilds_r.json() if guilds_r.status_code == 200 else []
        is_admin = False
        for guild in guilds:
            if str(guild['id']) == GUILD_ID and (guild['permissions'] & 0x8):
                is_admin = True
                break
        if not is_admin:
            return "<h2>⚠️ Acesso Restrito</h2><p>Apenas administradores podem acessar.</p><a href='/'>Voltar</a>", 403
        session['usuario'] = {
            'id': user_data['id'],
            'nome_usuario': user_data['username'],
            'avatar': user_data.get('avatar'),
            'eh_admin': True
        }
        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Erro interno: {str(e)}", 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))


# ==========================================
# ROTAS PÚBLICAS: PORTAL DO CLIENTE (/pedido)
# ==========================================

@app.route("/pedido")
def pagina_fidelidade():
    config = dados.get("config", {})
    pix_link = config.get("pix_link", "")
    pix_html = f'<a href="{pix_link}" target="_blank" class="btn-pix">💳 Pagar via PIX</a>' if pix_link else ''

    # Verifica se o cliente está logado
    cliente = session.get('cliente')
    uid_logado = cliente.get('uid') if cliente else None
    is_logado = bool(cliente)

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Fidelidade & Pedidos - ZankonYTB</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #121212; color: #e0e0e0; margin: 0; padding: 20px; }
            .container { max-width: 900px; margin: 0 auto; }
            .card { background: #1e1e1e; border-radius: 10px; padding: 20px; margin-bottom: 20px; border: 1px solid #333; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }
            h1, h2, h3 { color: #00d2d3; margin-top: 0; }
            .points-badge { font-size: 2.2rem; font-weight: bold; color: #ff9ff3; background: #2d2d2d; padding: 10px 20px; border-radius: 8px; display: inline-block; }
            input, select, button { width: 100%; padding: 12px; margin-top: 8px; margin-bottom: 15px; border-radius: 5px; border: 1px solid #444; background: #2a2a2a; color: white; box-sizing: border-box; }
            button { background: #10ac84; font-weight: bold; cursor: pointer; border: none; transition: 0.2s; }
            button:hover { background: #1dd1a1; }
            .reward-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin-top: 15px; }
            .reward-card { background: #282828; padding: 15px; border-radius: 8px; border: 1px solid #444; text-align: center; }
            .reward-card h4 { margin: 0 0 10px 0; color: #feca57; }
            .alert { padding: 12px; border-radius: 5px; display: none; margin-bottom: 15px; font-weight: bold; }
            .alert-success { background: #2ed573; color: #000; }
            .alert-error { background: #ff4757; color: #fff; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            th, td { padding: 10px; border-bottom: 1px solid #333; text-align: left; }
            th { background: #252525; color: #00d2d3; }
            .rules { background: #1a252f; border-left: 4px solid #3498db; padding: 15px; font-size: 0.9rem; line-height: 1.5; }
            .btn-pix { display: inline-block; background: #00b894; color: #000; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 10px 0; }
            .btn-pix:hover { background: #00d2d3; }
            .validade { color: #feca57; font-size: 0.9rem; margin-top: 5px; }
            .login-box { max-width: 400px; margin: 20px auto; }
            .login-box input { margin-bottom: 10px; }
            .login-box .btn-login { background: #5865F2; }
            .login-box .btn-login:hover { background: #4752C4; }
            .btn-sair { background: #dc3545; }
            .btn-sair:hover { background: #c82333; }
            .btn-voltar { background: #6c757d; }
            .btn-voltar:hover { background: #5a6268; }
            .link-pedido { margin-top: 20px; display: inline-block; background: #00b894; color: #000; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <center><h1>Services & Pontos ZankonYTB</h1></center>

            <div id="msg-alert" class="alert"></div>

            {% if not is_logado %}
            <!-- TELA DE LOGIN / CADASTRO -->
            <div class="card login-box">
                <h2>🔐 Acesso Cliente</h2>
                <div id="login-status" style="color:#feca57; margin-bottom:10px;"></div>
                <input type="text" id="login-uid" placeholder="Seu UID" style="margin-bottom:5px;">
                <input type="password" id="login-senha" placeholder="Senha">
                <button onclick="loginCliente()" class="btn-login">Entrar</button>
                <hr style="border-color:#333;">
                <h3>Novo cliente? Cadastre-se</h3>
                <input type="text" id="cad-uid" placeholder="UID">
                <input type="password" id="cad-senha" placeholder="Senha (mín 8 caracteres, com maiúscula, minúscula, número e caractere especial)">
                <input type="password" id="cad-senha2" placeholder="Confirmar senha">
                <button onclick="cadastrarCliente()">Cadastrar</button>
                <div id="cad-msg" style="margin-top:10px; color:#aaa;"></div>
            </div>
            {% else %}
            <!-- PAINEL DO CLIENTE LOGADO -->
            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h2>Bem-vindo, <span id="disp-uid" style="color:#00d2d3;">{{ uid_logado }}</span></h2>
                    <button onclick="logoutCliente()" class="btn-sair" style="width:auto; padding:8px 16px;">🚪 Sair</button>
                </div>
            </div>

            <div id="painel-cliente">
                <div class="card">
                    <h2>Seu Saldo de Pontos</h2>
                    <div class="points-badge"><span id="disp-pontos">0</span> Pontos</div>
                    <p style="font-size:0.85rem; color:#aaa; margin-top:10px;">* R$ 1,00 gasto em serviços = 1 Ponto acumulado.</p>
                    <div id="validade-pontos" class="validade"></div>
                </div>

                <div class="card">
                    <h2>Trocar Pontos por Vantagens</h2>
                    <div id="recompensas-container" class="reward-grid"></div>
                    <h3>Seus Cupons Resgatados Ativos</h3>
                    <div id="lista-cupons"><p>Nenhum cupom ativo no momento.</p></div>
                </div>

                <div class="card">
                    <h2>Solicitar Novo Serviço</h2>
                    <label>Nome do Serviço Desejado:</label>
                    <input type="text" id="ped-servico" placeholder="Ex: Farm de eco, Quests, Exploração">
                    <label>Jogo:</label>
                    <input type="text" id="ped-jogo" placeholder="Ex: Wuthering Waves, Mongil">
                    <label>Valor Combinado (R$):</label>
                    <input type="number" id="ped-valor" step="0.00" placeholder="Ex: 25.00">
                    <label>Nick do Youtube ou Discord:</label>
                    <input type="text" id="ped-discord" placeholder="Ex: usuario_discord">
                    <label>Possui Cupom de Desconto? (Opcional):</label>
                    <input type="text" id="ped-cupom" placeholder="Insira seu Token ex: ZNK-XXXXXX">
                    <button onclick="enviarPedidoServico()">Enviar Pedido para Aprovação</button>
                </div>

                <!-- BOTÃO PIX movido para cá (entre Solicitar Serviço e Histórico) -->
                {{ pix_html|safe }}

                <div class="card">
                    <h2>Seu Histórico de Pedidos</h2>
                    <table>
                        <thead><tr><th>Data</th><th>Serviço</th><th>Jogo</th><th>Valor</th><th>Pontos Ganhos</th></tr></thead>
                        <tbody id="lista-historico"><tr><td colspan="5">Nenhum serviço concluído ainda.</td></tr></tbody>
                    </table>
                </div>
            </div>
            {% endif %}

            <div class="card rules">
                <h3>📌 Regras de Uso - Sistema de Fidelidade ZankonYTB</h3>
                <ul>
                    <li><strong>Pontos Pessoais:</strong> Atrelados diretamente ao seu UID. Não podem ser transferidos entre contas.</li>
                    <li><strong>Cupons de Uso Único:</strong> Cada cupom gerado possui um token exclusivo que é queimado ao ser utilizado em um pedido.</li>
                    <li><strong>1 Benefício por Pedido:</strong> Não é permitido acumular múltiplos cupons em uma mesma compra.</li>
                    <li><strong>Validade dos Pontos:</strong> Expiram após <strong>60 dias</strong> sem a realização de novos pedidos.</li>
                    <li><strong>Validade dos Cupons:</strong> Cupons resgatados devem ser utilizados em até <strong>30 dias</strong>.</li>
                </ul>
            </div>
        </div>

        <script>
            let currentUID = '';
            let recompensas = [];
            let isLoggedIn = {{ 'true' if is_logado else 'false' }};

            // Se já estiver logado, carregar os dados
            if (isLoggedIn) {
                currentUID = '{{ uid_logado }}';
                consultarPerfil();
            }

            async function carregarRecompensas() {
                try {
                    const resp = await fetch('/api/fidelidade/recompensas');
                    const data = await resp.json();
                    if (data.sucesso) {
                        recompensas = data.recompensas;
                    }
                } catch(e) {
                    console.error('Erro ao carregar recompensas:', e);
                }
            }

            function renderizarRecompensas() {
                const container = document.getElementById('recompensas-container');
                if (!container) return;
                if (recompensas.length === 0) {
                    container.innerHTML = '<p>Nenhuma recompensa disponível no momento.</p>';
                    return;
                }
                let html = '';
                recompensas.forEach(r => {
                    html += `
                        <div class="reward-card">
                            <h4>${r.pontos} Pontos</h4>
                            <p>${r.nome}</p>
                            <button onclick="resgatarItem('${r.id}')">Resgatar</button>
                        </div>
                    `;
                });
                container.innerHTML = html;
            }

            async function consultarPerfil() {
                if (!currentUID) return;
                try {
                    const resp = await fetch('/api/fidelidade/consultar?uid=' + encodeURIComponent(currentUID));
                    const data = await resp.json();
                    if (data.sucesso) {
                        document.getElementById('disp-pontos').textContent = data.perfil.pontos;
                        const validadeDiv = document.getElementById('validade-pontos');
                        if (data.perfil.pontos > 0 && data.validade_pontos) {
                            validadeDiv.innerHTML = `📅 Pontos válidos até: <strong>${data.validade_pontos}</strong> (60 dias de inatividade)`;
                        } else if (data.perfil.pontos === 0) {
                            validadeDiv.innerHTML = '⚠️ Você não possui pontos ativos.';
                        } else {
                            validadeDiv.innerHTML = '';
                        }
                        const cuponsDiv = document.getElementById('lista-cupons');
                        if (data.perfil.cupons && data.perfil.cupons.length > 0) {
                            let html = '<ul>';
                            data.perfil.cupons.forEach(c => {
                                if (!c.usado && !c.expirado) {
                                    html += `<li>Token: <strong style="color:#feca57">${c.token}</strong> - ${c.nome} (Expira em: ${c.validez_str})</li>`;
                                }
                            });
                            html += '</ul>';
                            cuponsDiv.innerHTML = html;
                        } else {
                            cuponsDiv.innerHTML = '<p>Nenhum cupom ativo no momento.</p>';
                        }
                        const histBody = document.getElementById('lista-historico');
                        if (data.perfil.historico && data.perfil.historico.length > 0) {
                            histBody.innerHTML = data.perfil.historico.map(h => `
                                <tr>
                                    <td>${h.data}</td>
                                    <td>${h.servico}</td>
                                    <td>${h.jogo || '-'}</td>
                                    <td>R$ ${parseFloat(h.valor).toFixed(2)}</td>
                                    <td style="color:#1dd1a1;">+${h.pontos} pts</td>
                                </tr>
                            `).join('');
                        } else {
                            histBody.innerHTML = '<tr><td colspan="5">Nenhum serviço concluído ainda.</td></tr>';
                        }
                    }
                } catch(e) {
                    mostrarAlerta('Erro ao consultar perfil: ' + e.message, false);
                }
            }

            async function resgatarItem(recompensaId) {
                if (!currentUID) return;
                const rec = recompensas.find(r => r.id === recompensaId);
                if (!rec) { alert('Recompensa não encontrada!'); return; }
                if (!confirm(`Deseja trocar ${rec.pontos} pontos por "${rec.nome}"?`)) return;
                try {
                    const resp = await fetch('/api/fidelidade/resgatar', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ uid: currentUID, recompensa: recompensaId })
                    });
                    const res = await resp.json();
                    mostrarAlerta(res.mensagem, res.sucesso);
                    if (res.sucesso) consultarPerfil();
                } catch(e) { mostrarAlerta('Erro: ' + e.message, false); }
            }

            async function enviarPedidoServico() {
                if (!currentUID) return;
                const servico = document.getElementById('ped-servico').value.trim();
                const jogo = document.getElementById('ped-jogo').value.trim();
                const valor = parseFloat(document.getElementById('ped-valor').value);
                const discord = document.getElementById('ped-discord').value.trim();
                const cupom = document.getElementById('ped-cupom').value.trim();
                if (!servico || isNaN(valor) || valor <= -1 || !discord) {
                    alert('Preencha o serviço, jogo (opcional), valor válido e seu Nick.');
                    return;
                }
                try {
                    const resp = await fetch('/api/fidelidade/solicitar_servico', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            uid: currentUID,
                            servico: servico,
                            jogo: jogo,
                            valor: valor,
                            discord: discord,
                            cupom_token: cupom
                        })
                    });
                    const res = await resp.json();
                    mostrarAlerta(res.mensagem, res.sucesso);
                    if (res.sucesso) {
                        document.getElementById('ped-servico').value = '';
                        document.getElementById('ped-jogo').value = '';
                        document.getElementById('ped-valor').value = '';
                        document.getElementById('ped-cupom').value = '';
                        consultarPerfil();
                    }
                } catch(e) { mostrarAlerta('Erro: ' + e.message, false); }
            }

            // Funções de login/cadastro
            async function cadastrarCliente() {
                const uid = document.getElementById('cad-uid').value.trim();
                const senha = document.getElementById('cad-senha').value;
                const senha2 = document.getElementById('cad-senha2').value;
                const msg = document.getElementById('cad-msg');

                if (!uid || !senha || !senha2) {
                    msg.textContent = 'Preencha todos os campos.';
                    msg.style.color = '#ff4757';
                    return;
                }
                if (senha !== senha2) {
                    msg.textContent = 'As senhas não coincidem.';
                    msg.style.color = '#ff4757';
                    return;
                }
                try {
                    const resp = await fetch('/api/cliente/cadastrar', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ uid, senha })
                    });
                    const data = await resp.json();
                    msg.textContent = data.mensagem;
                    msg.style.color = data.sucesso ? '#2ed573' : '#ff4757';
                    if (data.sucesso) {
                        // Limpa campos
                        document.getElementById('cad-uid').value = '';
                        document.getElementById('cad-senha').value = '';
                        document.getElementById('cad-senha2').value = '';
                        // Faz login automático
                        await loginCliente(uid, senha);
                    }
                } catch(e) {
                    msg.textContent = 'Erro: ' + e.message;
                    msg.style.color = '#ff4757';
                }
            }

            async function loginCliente(uid, senha) {
                if (!uid) uid = document.getElementById('login-uid').value.trim();
                if (!senha) senha = document.getElementById('login-senha').value;
                const status = document.getElementById('login-status');
                if (!uid || !senha) {
                    status.textContent = 'Preencha UID e senha.';
                    status.style.color = '#ff4757';
                    return;
                }
                try {
                    const resp = await fetch('/api/cliente/login', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ uid, senha })
                    });
                    const data = await resp.json();
                    if (data.sucesso) {
                        window.location.reload(); // recarrega a página para mostrar o painel
                    } else {
                        status.textContent = data.mensagem;
                        status.style.color = '#ff4757';
                    }
                } catch(e) {
                    status.textContent = 'Erro: ' + e.message;
                    status.style.color = '#ff4757';
                }
            }

            async function logoutCliente() {
                const resp = await fetch('/api/cliente/logout', { method: 'POST' });
                const data = await resp.json();
                if (data.sucesso) {
                    window.location.reload();
                }
            }

            function mostrarAlerta(msg, sucesso) {
                const el = document.getElementById('msg-alert');
                if (!el) return;
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 4000);
            }

            document.addEventListener('DOMContentLoaded', async function() {
                await carregarRecompensas();
                renderizarRecompensas();
                if (isLoggedIn) {
                    consultarPerfil();
                }
            });
        </script>
    </body>
    </html>
    """, pix_html=pix_html, is_logado=is_logado, uid_logado=uid_logado)


# ==========================================
# ROTAS DE AUTENTICAÇÃO DO CLIENTE
# ==========================================

@app.route("/api/cliente/cadastrar", methods=["POST"])
def api_cliente_cadastrar():
    req = request.get_json() or {}
    uid = str(req.get("uid", "")).strip()
    senha = req.get("senha", "")

    if not uid or not senha:
        return jsonify({"sucesso": False, "mensagem": "UID e senha são obrigatórios."})

    # Verificar se senha atende aos requisitos
    if not validar_senha(senha):
        return jsonify({
            "sucesso": False,
            "mensagem": "A senha deve ter no mínimo 8 caracteres, com letra maiúscula, minúscula, número e caractere especial."
        })

    credenciais = dados.setdefault("credenciais", {})
    if uid in credenciais:
        return jsonify({"sucesso": False, "mensagem": "Este UID já possui cadastro."})

    # Criar hash
    cred = hash_senha(senha)
    credenciais[uid] = cred
    salvar_dados_github(f"Novo cadastro de cliente: {uid}")
    return jsonify({"sucesso": True, "mensagem": "Cadastro realizado com sucesso! Faça login."})


@app.route("/api/cliente/login", methods=["POST"])
def api_cliente_login():
    req = request.get_json() or {}
    uid = str(req.get("uid", "")).strip()
    senha = req.get("senha", "")

    if not uid or not senha:
        return jsonify({"sucesso": False, "mensagem": "UID e senha são obrigatórios."})

    credenciais = dados.get("credenciais", {})
    cred = credenciais.get(uid)
    if not cred:
        return jsonify({"sucesso": False, "mensagem": "UID não cadastrado."})

    if not verificar_senha(senha, cred):
        return jsonify({"sucesso": False, "mensagem": "Senha incorreta."})

    session['cliente'] = {"uid": uid}
    return jsonify({"sucesso": True, "mensagem": "Login realizado com sucesso."})


@app.route("/api/cliente/logout", methods=["POST"])
def api_cliente_logout():
    session.pop('cliente', None)
    return jsonify({"sucesso": True, "mensagem": "Logout realizado."})


# ==========================================
# ROTAS DA API DE FIDELIDADE (AGORA COM AUTENTICAÇÃO)
# ==========================================

@app.route("/api/fidelidade/consultar")
def api_fidelidade_consultar():
    # Verifica se o cliente está logado
    cliente = session.get('cliente')
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Você precisa estar logado."}), 401

    uid_sessao = cliente.get('uid')
    uid_param = request.args.get('uid')
    if not uid_param:
        return jsonify({"sucesso": False, "mensagem": "UID não informado"})

    # Garantir que o cliente só consulte seu próprio UID
    if uid_sessao != uid_param:
        return jsonify({"sucesso": False, "mensagem": "Acesso negado."}), 403

    perfil = obter_ou_criar_perfil_fidelidade(uid_param)
    validade_pontos = None
    if perfil.get("pontos", 0) > 0:
        ultimo_pedido = perfil.get("ultimo_pedido_ts", time.time())
        data_validade = datetime.fromtimestamp(ultimo_pedido + 60 * 86400).strftime("%d/%m/%Y")
        validade_pontos = data_validade
    return jsonify({"sucesso": True, "uid": uid_param, "perfil": perfil, "validade_pontos": validade_pontos})


@app.route("/api/fidelidade/resgatar", methods=["POST"])
def api_fidelidade_resgatar():
    cliente = session.get('cliente')
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado."}), 401

    req = request.get_json() or {}
    uid = req.get("uid")
    recompensa_id = req.get("recompensa")

    if not uid or not recompensa_id:
        return jsonify({"sucesso": False, "mensagem": "Dados inválidos"})

    if cliente['uid'] != uid:
        return jsonify({"sucesso": False, "mensagem": "Acesso negado."}), 403

    rec = obter_recompensa_por_id(recompensa_id)
    if not rec:
        return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada"})

    perfil = obter_ou_criar_perfil_fidelidade(uid)
    if perfil["pontos"] < rec["pontos"]:
        return jsonify({"sucesso": False, "mensagem": f"Pontos insuficientes! Você precisa de {rec['pontos']} pontos."})

    perfil["pontos"] -= rec["pontos"]
    token = f"ZNK-{secrets.token_hex(3).upper()}"
    agora = time.time()
    novo_cupom = {
        "token": token,
        "recompensa_id": rec["id"],
        "nome": rec["nome"],
        "tipo": rec["tipo"],
        "desconto": rec.get("desconto", 0),
        "criado_em_ts": agora,
        "validez_str": time.strftime("%d/%m/%Y", time.localtime(agora + 30 * 86400)),
        "usado": False,
        "expirado": False
    }
    perfil["cupons"].append(novo_cupom)
    salvar_dados_github("Resgate de fidelidade")
    return jsonify({
        "sucesso": True,
        "mensagem": f"Resgate concluído! Seu código de cupom gerado é: {token}",
        "token": token
    })


@app.route("/api/fidelidade/solicitar_servico", methods=["POST"])
def api_fidelidade_solicitar_servico():
    cliente = session.get('cliente')
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado."}), 401

    req = request.get_json() or {}
    uid = req.get("uid")
    servico = req.get("servico")
    jogo = req.get("jogo", "")
    valor = float(req.get("valor", 0))
    discord = req.get("discord")
    cupom_token = req.get("cupom_token", "").strip().upper()

    if not uid or not servico or valor <= -1:
        return jsonify({"sucesso": False, "mensagem": "Preencha todos os campos corretamente"})

    if cliente['uid'] != uid:
        return jsonify({"sucesso": False, "mensagem": "Acesso negado."}), 403

    perfil = obter_ou_criar_perfil_fidelidade(uid)
    cupom_aplicado = None
    if cupom_token:
        encontrado = None
        for c in perfil.get("cupons", []):
            if c["token"] == cupom_token:
                encontrado = c
                break
        if not encontrado:
            return jsonify({"sucesso": False, "mensagem": "Cupom não encontrado ou não pertence a este UID!"})
        if encontrado.get("usado"):
            return jsonify({"sucesso": False, "mensagem": "Este cupom já foi utilizado!"})
        if encontrado.get("expirado"):
            return jsonify({"sucesso": False, "mensagem": "Este cupom expirou (prazo de 30 dias)!"})
        if encontrado["tipo"] == "cupom":
            valor = max(0.0, valor - encontrado["desconto"])
        encontrado["usado"] = True
        cupom_aplicado = encontrado["nome"]

    dados.setdefault("pedidos_fidelidade_pendentes", [])
    novo_pedido = {
        "id": str(uuid.uuid4())[:8],
        "uid": uid,
        "discord": discord,
        "servico": servico,
        "jogo": jogo,
        "valor": valor,
        "cupom_usado": cupom_aplicado,
        "timestamp": time.time(),
        "data_str": time.strftime("%d/%m/%Y %H:%M"),
        "status": "aguardando_aprovacao"
    }
    dados["pedidos_fidelidade_pendentes"].append(novo_pedido)
    salvar_dados_github("Novo pedido de serviço solicitado")
    return jsonify({
        "sucesso": True,
        "mensagem": "Pedido enviado com sucesso! Aguarde a aprovação do Administrador."
    })


# ==========================================
# ROTAS DE ADMIN PARA GERENCIAR RECOMPENSAS (mantidas)
# ==========================================

@app.route("/api/fidelidade/recompensas", methods=["GET"])
def api_fidelidade_recompensas():
    recs = obter_recompensas()
    return jsonify({"sucesso": True, "recompensas": recs})


@app.route("/api/fidelidade/recompensas", methods=["POST"])
def api_fidelidade_recompensas_adicionar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
    req = request.get_json() or {}
    nome = req.get("nome", "").strip()
    pontos = int(req.get("pontos", 0))
    tipo = req.get("tipo", "servico")
    desconto = float(req.get("desconto", 0))
    if not nome or pontos <= 0:
        return jsonify({"sucesso": False, "mensagem": "Nome e pontos são obrigatórios"})
    recs = obter_recompensas()
    new_id = f"rec_{int(time.time())}"
    nova = {
        "id": new_id,
        "nome": nome,
        "pontos": pontos,
        "tipo": tipo,
        "desconto": desconto if tipo == "cupom" else 0
    }
    recs.append(nova)
    salvar_dados_github(f"Recompensa adicionada: {nome}")
    return jsonify({"sucesso": True, "mensagem": "Recompensa adicionada!", "recompensa": nova})


@app.route("/api/fidelidade/recompensas/<recompensa_id>", methods=["PUT"])
def api_fidelidade_recompensas_editar(recompensa_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
    req = request.get_json() or {}
    recs = obter_recompensas()
    for i, r in enumerate(recs):
        if r["id"] == recompensa_id:
            recs[i]["nome"] = req.get("nome", r["nome"]).strip()
            recs[i]["pontos"] = int(req.get("pontos", r["pontos"]))
            recs[i]["tipo"] = req.get("tipo", r["tipo"])
            recs[i]["desconto"] = float(req.get("desconto", r.get("desconto", 0))) if recs[i]["tipo"] == "cupom" else 0
            salvar_dados_github(f"Recompensa editada: {recs[i]['nome']}")
            return jsonify({"sucesso": True, "mensagem": "Recompensa atualizada!", "recompensa": recs[i]})
    return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada"})


@app.route("/api/fidelidade/recompensas/<recompensa_id>", methods=["DELETE"])
def api_fidelidade_recompensas_remover(recompensa_id):
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
    recs = obter_recompensas()
    for i, r in enumerate(recs):
        if r["id"] == recompensa_id:
            recs.pop(i)
            salvar_dados_github(f"Recompensa removida: {r['nome']}")
            return jsonify({"sucesso": True, "mensagem": "Recompensa removida!"})
    return jsonify({"sucesso": False, "mensagem": "Recompensa não encontrada"})


# ==========================================
# ROTAS DO ADMINISTRADOR PARA GESTÃO DE PEDIDOS (mantidas)
# ==========================================

@app.route("/api/fidelidade/admin/pendentes")
def api_fidelidade_admin_pendentes():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
    pendentes = dados.get("pedidos_fidelidade_pendentes", [])
    return jsonify({"sucesso": True, "pedidos": pendentes})


@app.route("/api/fidelidade/admin/aprovar", methods=["POST"])
def api_fidelidade_admin_aprovar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
    req = request.get_json() or {}
    pedido_id = req.get("pedido_id")
    pendentes = dados.get("pedidos_fidelidade_pendentes", [])
    pedido = next((p for p in pendentes if p["id"] == pedido_id), None)
    if not pedido:
        return jsonify({"sucesso": False, "mensagem": "Pedido não encontrado"})
    fila = obter_dados_fila()
    if not fila["configuracoes"]["aberta"]:
        return jsonify({"sucesso": False, "mensagem": "A fila está fechada no momento."})
    if len(fila["entradas"]) >= fila["configuracoes"]["tamanho_maximo"]:
        return jsonify({"sucesso": False, "mensagem": "A fila está cheia."})
    nova_entrada_fila = {
        "id": str(uuid.uuid4()),
        "posicao": len(fila["entradas"]) + 1,
        "nome_usuario": f"{pedido['discord']}",
        "servico": pedido["servico"],
        "jogo": pedido.get("jogo", ""),
        "valor": pedido["valor"],
        "uid": pedido["uid"],
        "timestamp": agora_br().isoformat(),
        "status": "aguardando"
    }
    fila["entradas"].append(nova_entrada_fila)
    dados["pedidos_fidelidade_pendentes"] = [p for p in pendentes if p["id"] != pedido_id]
    salvar_dados_github("Pedido aprovado e enviado para a fila")
    return jsonify({"sucesso": True, "mensagem": "Pedido aprovado e inserido na Fila com sucesso!"})


@app.route("/api/fidelidade/admin/recusar", methods=["POST"])
def api_fidelidade_admin_recusar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
    req = request.get_json() or {}
    pedido_id = req.get("pedido_id")
    pendentes = dados.get("pedidos_fidelidade_pendentes", [])
    dados["pedidos_fidelidade_pendentes"] = [p for p in pendentes if p["id"] != pedido_id]
    salvar_dados_github("Pedido recusado")
    return jsonify({"sucesso": True, "mensagem": "Pedido recusado e removido."})


# ========================
# ROTAS DA FILA (com link para /pedido)
# ========================

@app.route("/fila")
def fila_publica():
    fila = obter_dados_fila()
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    botoes_html = ""
    for botao in botoes_precos:
        botoes_html += f'<a href="{escape_html(botao["url"])}" target="_blank" class="btn-link btn-link-precos">💰 {escape_html(botao["nome"])}</a>'
    # Adiciona botão para /pedido
    link_pedido = '<a href="/pedido" class="btn-link btn-link-pedido">📝 Solicitar Serviço</a>'
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="30">
        <title>{{ fila.nome }}</title>
        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height:100vh; padding:20px; color:#fff; }
            .container { max-width:800px; margin:0 auto; }
            .header { text-align:center; margin-bottom:30px; padding:20px; background:rgba(0,0,0,0.5); border-radius:20px; }
            h1 { background: linear-gradient(135deg, #ff6b6b, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
            .status { display:inline-block; padding:5px 15px; border-radius:20px; }
            .status-aberta { background:#00b894; }
            .status-fechada { background:#d63031; }
            .links-container { display: flex; justify-content: center; gap: 20px; margin: 20px 0; flex-wrap: wrap; }
            .btn-link { display: inline-flex; align-items: center; gap: 10px; padding: 12px 24px; border-radius: 30px; text-decoration: none; font-weight: bold; transition: all 0.3s; }
            .btn-link-discord { background: #5865F2; color: white; }
            .btn-link-discord:hover { background: #4752C4; transform: translateY(-2px); }
            .btn-link-precos { background: #f59e0b; color: white; }
            .btn-link-precos:hover { background: #d97706; transform: translateY(-2px); }
            .btn-link-pedido { background: #00b894; color: white; }
            .btn-link-pedido:hover { background: #00a381; transform: translateY(-2px); }
            .lista-fila { background:rgba(0,0,0,0.4); border-radius:20px; overflow:hidden; }
            .cabecalho-fila { display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:15px; background:rgba(255,255,255,0.1); font-weight:bold; }
            .item-fila { display:grid; grid-template-columns:60px 1fr 1fr 1fr 80px; padding:12px 15px; border-bottom:1px solid rgba(255,255,255,0.1); }
            .posicao { font-weight:bold; color:#ffd93d; }
            .servico { color:#a8e6cf; }
            .jogo { color:#ffb347; }
            .vazio { text-align:center; padding:40px; }
            .footer { text-align:center; margin-top:20px; font-size:0.8rem; color:#888; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 {{ fila.nome }}</h1>
                <span class="status status-{{ 'aberta' if fila.configuracoes.aberta else 'fechada' }}">{{ '🟢 ABERTA' if fila.configuracoes.aberta else '🔴 FECHADA' }}</span>
                <div>📊 {{ fila.entradas|length }} / {{ fila.configuracoes.tamanho_maximo }} pessoas</div>
            </div>
            <div class="links-container">
                {% if links.discord_convite %}
                    <a href="{{ links.discord_convite }}" target="_blank" class="btn-link btn-link-discord">💬 Entrar no Discord</a>
                {% endif %}
                {{ botoes_html|safe }}
                {{ link_pedido|safe }}
            </div>
            <div class="lista-fila">
                <div class="cabecalho-fila"><span>#</span><span>Jogador</span><span>Serviço</span><span>Jogo</span><span></span></div>
                {% if fila.entradas %}
                    {% for e in fila.entradas %}
                        <div class="item-fila">
                            <span class="posicao">{{ e.posicao }}</span>
                            <span>{{ e.nome_usuario }}</span>
                            <span class="servico">{{ e.servico }}</span>
                            <span class="jogo">{{ e.jogo or '' }}</span>
                            <span>⏳</span>
                        </div>
                    {% endfor %}
                {% else %}
                    <div class="vazio">✨ Ninguém na fila</div>
                {% endif %}
            </div>
            <div class="footer">Atualizado a cada 30s • {{ agora_br().strftime("%d/%m/%Y %H:%M:%S") }}</div>
        </div>
    </body>
    </html>
    """, fila=fila, links=links, botoes_html=botoes_html, link_pedido=link_pedido, agora_br=agora_br)


# ========================
# ROTAS DA FILA (embed, api, etc) - inalteradas
# ========================

@app.route("/fila/embed")
def fila_embed():
    fila = obter_dados_fila()
    entradas_html = ""
    for e in fila["entradas"][:10]:
        entradas_html += f'<div style="display:flex;justify-content:space-between;padding:5px 0;"><span style="color:#ffd93d;">#{e["posicao"]}</span><span>{escape_html(e["nome_usuario"])}</span><span style="color:#a8e6cf;">{escape_html(e["servico"])}</span><span style="color:#ffb347;">{escape_html(e.get("jogo", ""))}</span></div>'
    if not entradas_html:
        entradas_html = '<div style="text-align:center;padding:20px;">✨ Fila vazia</div>'
    return f'''
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15"><style>body{{margin:0;padding:10px;background:transparent;color:white;font-size:14px;}}.container{{background:rgba(0,0,0,0.7);border-radius:10px;padding:10px;}}</style></head>
    <body><div class="container"><div style="text-align:center;margin-bottom:10px;"><strong>📋 {escape_html(fila["nome"])}</strong><span style="background:{'#00b894' if fila['configuracoes']['aberta'] else '#d63031'};padding:2px 8px;border-radius:10px;margin-left:5px;">{'ABERTA' if fila['configuracoes']['aberta'] else 'FECHADA'}</span></div>{entradas_html}<div style="text-align:center;margin-top:8px;font-size:10px;color:#888;">Total: {len(fila["entradas"])}</div></div></body>
    </html>
    '''


@app.route("/fila/api")
def fila_api():
    fila = obter_dados_fila()
    return jsonify({
        "sucesso": True,
        "fila": {
            "nome": fila["nome"],
            "aberta": fila["configuracoes"]["aberta"],
            "tamanho_maximo": fila["configuracoes"]["tamanho_maximo"],
            "contagem": len(fila["entradas"]),
            "entradas": [{"posicao": e["posicao"], "nome_usuario": e["nome_usuario"], "servico": e["servico"],
                          "jogo": e.get("jogo", ""), "timestamp": e["timestamp"], "id": e["id"], "uid": e.get("uid", "")} for e in
                         fila["entradas"]],
            "historico": fila["historico"]
        }
    })


# ========================
# APIs DA FILA (mantidas)
# ========================

@app.route("/api/fila/adicionar", methods=["POST"])
def api_fila_adicionar():
    dados_req = request.json
    nome = dados_req.get("nome_usuario", "").strip()
    servico = dados_req.get("servico", "").strip()
    jogo = dados_req.get("jogo", "").strip()
    uid = dados_req.get("uid", "").strip()
    if not nome or not servico:
        return jsonify({"sucesso": False, "mensagem": "Nome e serviço são obrigatórios"})
    sucesso, resultado = adicionar_fila(nome, servico, jogo, usuario_id=nome, uid=uid)
    return jsonify({"sucesso": sucesso, "mensagem": f"{nome} adicionado!" if sucesso else resultado})


@app.route("/api/fila/remover", methods=["POST"])
def api_fila_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = remover_fila(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})


@app.route("/api/fila/mover-cima", methods=["POST"])
def api_fila_mover_cima():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_cima(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})


@app.route("/api/fila/mover-baixo", methods=["POST"])
def api_fila_mover_baixo():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    sucesso, _ = mover_baixo(request.json.get("entrada_id"))
    return jsonify({"sucesso": sucesso})


@app.route("/api/fila/concluir", methods=["POST"])
def api_fila_concluir():
    req = request.get_json() or {}
    entrada_id = req.get("entrada_id")
    fila = dados.get("fila", {}).get("entradas", [])
    item_concluido = next((e for e in fila if e["id"] == entrada_id), None)
    if item_concluido:
        uid = item_concluido.get("uid")
        valor = float(item_concluido.get("valor", 0))
        if uid and valor > 0:
            perfil = obter_ou_criar_perfil_fidelidade(uid)
            pontos_ganhos = int(valor)
            perfil["pontos"] += pontos_ganhos
            perfil["ultimo_pedido_ts"] = time.time()
            perfil["historico"].insert(0, {
                "servico": item_concluido.get("servico", "Serviço"),
                "jogo": item_concluido.get("jogo", ""),
                "valor": valor,
                "pontos": pontos_ganhos,
                "data": time.strftime("%d/%m/%Y")
            })
        sucesso, removido = concluir_servico(entrada_id)
        if sucesso:
            return jsonify({"sucesso": True, "mensagem": "Serviço concluído e pontos creditados ao cliente!"})
        else:
            return jsonify({"sucesso": False, "mensagem": "Erro ao concluir serviço"})
    return jsonify({"sucesso": False, "mensagem": "Entrada não encontrada na fila"})


@app.route("/api/fila/limpar", methods=["POST"])
def api_fila_limpar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    limpar_fila()
    return jsonify({"sucesso": True})


@app.route("/api/fila/configuracoes", methods=["GET", "POST"])
def api_fila_configuracoes():
    if request.method == "GET":
        fila = obter_dados_fila()
        links = obter_links_fila()
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "configuracoes": fila["configuracoes"],
            "nome": fila["nome"],
            "links": links,
            "pix_link": config.get("pix_link", "")
        })
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    if "aberta" in req:
        alternar_fila(req["aberta"])
    if "tamanho_maximo" in req:
        definir_tamanho_maximo(int(req["tamanho_maximo"]))
    if "nome" in req:
        definir_nome_fila(req["nome"])
    if "discord_convite" in req:
        salvar_links_fila(req.get("discord_convite", ""))
    if "pix_link" in req:
        dados.setdefault("config", {})["pix_link"] = req["pix_link"]
        salvar_dados_github("PIX link atualizado")
    return jsonify({"sucesso": True})


# ========================
# APIs DOS BOTÕES DE PREÇO (mantidas)
# ========================

@app.route("/api/fila/botoes", methods=["GET"])
def api_fila_botoes():
    links = obter_links_fila()
    return jsonify({"sucesso": True, "botoes": links.get("botoes_precos", [])})


@app.route("/api/fila/botoes/adicionar", methods=["POST"])
def api_fila_botoes_adicionar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Nome e URL são obrigatórios"})
    adicionar_botao_preco(nome, url)
    return jsonify({"sucesso": True, "mensagem": f"Botão '{nome}' adicionado!"})


@app.route("/api/fila/botoes/remover", methods=["POST"])
def api_fila_botoes_remover():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    index = request.json.get("index")
    if index is None:
        return jsonify({"sucesso": False, "mensagem": "Índice não informado"})
    remover_botao_preco(int(index))
    return jsonify({"sucesso": True, "mensagem": "Botão removido!"})


@app.route("/api/fila/botoes/atualizar", methods=["POST"])
def api_fila_botoes_atualizar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    index = req.get("index")
    nome = req.get("nome", "").strip()
    url = req.get("url", "").strip()
    if index is None or not nome or not url:
        return jsonify({"sucesso": False, "mensagem": "Dados incompletos"})
    atualizar_botao_preco(int(index), nome, url)
    return jsonify({"sucesso": True, "mensagem": "Botão atualizado!"})


# ========================
# APIs DE CONFIGURAÇÃO (mantidas)
# ========================

@app.route("/api/servidor/canais")
def api_servidor_canais():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "canais": []})
    return jsonify({"sucesso": True, "canais": [{"id": str(c.id), "nome": c.name} for c in guild.text_channels]})


@app.route("/api/servidor/cargos")
def api_servidor_cargos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "cargos": []})
    return jsonify({"sucesso": True, "cargos": [{"id": str(r.id), "nome": r.name} for r in guild.roles if
                                                 r.name != "@everyone"]})


@app.route("/api/servidor/membros")
def api_servidor_membros():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID and bot.is_ready() else None
    if not guild:
        return jsonify({"sucesso": False, "membros": []})
    membros = [{"id": str(m.id), "nome": m.display_name} for m in guild.members if not m.bot][:100]
    return jsonify({"sucesso": True, "membros": membros})


@app.route("/api/anti_spam", methods=["GET", "POST"])
def api_anti_spam():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    if request.method == "GET":
        anti_spam = dados.get("anti_spam", {})
        return jsonify({
            "sucesso": True,
            "config": {
                "ativado": anti_spam.get("ativado", True),
                "limite_mensagens": anti_spam.get("limite_mensagens", 5),
                "intervalo_segundos": anti_spam.get("intervalo_segundos", 5),
                "tempo_mute_minutos": anti_spam.get("tempo_mute_minutos", 2),
                "remover_xp": anti_spam.get("remover_xp", True),
                "xp_penalidade": anti_spam.get("xp_penalidade", 50),
                "deletar_mensagens": anti_spam.get("deletar_mensagens", True),
                "cargos_ignorados": ",".join(anti_spam.get("cargos_ignorados",
                                                           ["Administrador", "Moderador", "Staff", "Dono"])),
                "comandos_ignorados": ",".join(anti_spam.get("comandos_ignorados", [
                    "$w", "$wa", "$wg", "$h", "$ha", "$hg",
                    "$W", "$WA", "$WG", "$H", "$HA", "$HG",
                    "$tu", "$TU", "$dk", "$mmi", "$vote", "$rolls", "$k", "$mu"
                ]))
            }
        })
    req = request.json
    executar_acao_bot("configurar_anti_spam", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração anti-spam salva!"})


@app.route("/api/config/boasvindas", methods=["GET", "POST"])
def api_config_boasvindas():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal": config.get("canal_boas_vindas", ""),
            "mensagem": config.get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!"),
            "imagem": config.get("fundo_boas_vindas", "")
        })
    req = request.json
    executar_acao_bot("configurar_boas_vindas", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})


@app.route("/api/config/xp", methods=["GET", "POST"])
def api_config_xp():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "taxa": config.get("taxa_xp", 3),
            "canal": config.get("canal_levelup", "")
        })
    req = request.json
    executar_acao_bot("configurar_xp", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})


@app.route("/api/config/comandos", methods=["GET", "POST"])
def api_config_comandos():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    if request.method == "GET":
        config = dados.get("config", {})
        return jsonify({
            "sucesso": True,
            "canal_perfil": config.get("canal_perfil", ""),
            "canal_rank": config.get("canal_rank", "")
        })
    req = request.json
    executar_acao_bot("configurar_comandos", **req)
    return jsonify({"sucesso": True, "mensagem": "Configuração de comandos salva!"})


@app.route("/api/cargos/nivel", methods=["GET", "POST", "DELETE"])
def api_cargos_nivel():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    if request.method == "GET":
        return jsonify({"sucesso": True, "cargos": dados.get("cargos_nivel", {})})
    elif request.method == "POST":
        req = request.json
        executar_acao_bot("adicionar_cargo_nivel", nivel=req.get('nivel'), cargo_id=req.get('cargo_id'))
        return jsonify({"sucesso": True, "mensagem": "Cargo adicionado!"})
    elif request.method == "DELETE":
        nivel = request.args.get('nivel')
        if nivel:
            executar_acao_bot("remover_cargo_nivel", nivel=nivel)
        return jsonify({"sucesso": True, "mensagem": "Cargo removido!"})


@app.route("/api/config/links", methods=["GET", "POST"])
def api_config_links():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    if request.method == "GET":
        return jsonify({"sucesso": True, "canais": dados.get("canais_links_bloqueados", [])})
    req = request.json
    executar_acao_bot("alternar_bloqueio_links", canal_id=req.get('canal_id'))
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})


# ========================
# APIs DE COMANDOS (mantidas)
# ========================

@app.route("/api/comando/embed", methods=["POST"])
def api_comando_embed():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_embed", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Embed criada!" if sucesso else "❌ Falha"})


@app.route("/api/comando/advertir", methods=["POST"])
def api_comando_advertir():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("advertir_membro", membro_id=req.get('membro_id'), motivo=req.get('motivo'),
                                admin=session['usuario']['nome_usuario'])
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Advertência aplicada!" if sucesso else "❌ Falha"})


@app.route("/api/comando/limpar_advertencias", methods=["POST"])
def api_comando_limpar_advertencias():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    membro_id = str(request.json.get('membro_id'))
    if membro_id in dados.get("advertencias", {}):
        dados["advertencias"].pop(membro_id)
        salvar_dados_github(f"Advertências limpas: {membro_id}")
        return jsonify({"sucesso": True, "mensagem": "✅ Advertências removidas!"})
    return jsonify({"sucesso": False, "mensagem": "❌ Membro sem advertências"})


@app.route("/api/reacao_cargo/criar", methods=["POST"])
def api_reacao_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_reacao_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Reaction role criada!" if sucesso else "❌ Falha"})


@app.route("/api/botoes_cargo/criar", methods=["POST"])
def api_botoes_cargo_criar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401
    req = request.json
    sucesso = executar_acao_bot("criar_botoes_cargo", **req)
    return jsonify({"sucesso": sucesso, "mensagem": "✅ Botões criados!" if sucesso else "❌ Falha"})


# ========================
# DASHBOARD PRINCIPAL (mantido)
# ========================

@app.route("/dashboard")
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = session['usuario']
    config = dados.get("config", {})
    fila = obter_dados_fila()
    anti_spam = dados.get("anti_spam", {})
    links = obter_links_fila()
    botoes_precos = links.get("botoes_precos", [])
    recompensas = obter_recompensas()
    historico = fila.get("historico", [])
    pix_link = config.get("pix_link", "")

    total_usuarios_xp = len(dados.get("xp", {}))
    total_advertencias = sum(len(w) for w in dados.get("advertencias", {}).values())
    total_fila = len(fila["entradas"])
    status_bot = "✅ Online" if bot.is_ready() else "❌ Offline"
    processador_status = "✅ Ativo" if processador_acoes_rodando else "❌ Inativo"
    anti_spam_status = "✅ Ativo" if anti_spam.get('ativado', True) else "❌ Desativado"
    total_recompensas = len(recompensas)

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel - Bot</title>
        <style>
            :root { --primary: #5865F2; --primary-dark: #4752C4; --success: #10b981; --danger: #ef4444; --warning: #f59e0b; --dark: #1a1a1a; --darker: #121212; --light: #e0e0e0; --gray: #333; }
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', sans-serif; background: var(--darker); color: var(--light); }
            header { background: var(--dark); padding: 1rem 2rem; border-bottom: 1px solid var(--gray); }
            .header-content { display: flex; justify-content: space-between; align-items: center; max-width: 1400px; margin: 0 auto; }
            h1 { color: var(--primary); }
            .user-info { display: flex; align-items: center; gap: 1rem; }
            .avatar { width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--primary); }
            .btn { padding: 0.5rem 1rem; border: none; border-radius: 5px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; transition: all 0.2s; }
            .btn-primary { background: var(--primary); color: white; }
            .btn-primary:hover { background: var(--primary-dark); }
            .btn-success { background: var(--success); color: white; }
            .btn-danger { background: var(--danger); color: white; }
            .btn-warning { background: var(--warning); color: white; }
            .btn-sm { padding: 0.25rem 0.5rem; font-size: 0.8rem; }
            .container { max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }
            .tab-nav { display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid var(--gray); flex-wrap: wrap; }
            .tab-btn { padding: 0.75rem 1.5rem; background: var(--gray); border: none; border-radius: 5px 5px 0 0; cursor: pointer; font-weight: 600; color: var(--light); }
            .tab-btn:hover { background: #444; }
            .tab-btn.active { background: var(--primary); color: white; }
            .tab { display: none; animation: fadeIn 0.3s; }
            .tab.active { display: block; }
            @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
            .card { background: var(--dark); border-radius: 10px; padding: 1.5rem; margin: 1rem 0; border: 1px solid var(--gray); }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; }
            .stat-card { background: linear-gradient(135deg, var(--primary), var(--primary-dark)); color: white; padding: 1.5rem; border-radius: 10px; text-align: center; }
            .stat-card h3 { font-size: 2rem; }
            .form-group { margin-bottom: 1.5rem; }
            label { display: block; margin-bottom: 0.5rem; font-weight: 600; color: var(--primary); }
            .form-control { width: 100%; padding: 0.75rem; background: var(--darker); border: 1px solid var(--gray); border-radius: 5px; color: var(--light); }
            .form-control:focus { outline: none; border-color: var(--primary); }
            .alert { padding: 1rem; border-radius: 5px; margin: 1rem 0; display: none; }
            .alert-success { background: #1a472a; color: #4ade80; border: 1px solid #2ecc71; }
            .alert-error { background: #7f1d1d; color: #f87171; border: 1px solid #ef4444; }
            table { width: 100%; border-collapse: collapse; }
            th, td { text-align: left; padding: 12px; border-bottom: 1px solid var(--gray); }
            th { background: var(--gray); }
            .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; }
            .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
            @media (max-width: 768px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
            .switch { position: relative; display: inline-block; width: 60px; height: 34px; }
            .switch input { opacity: 0; width: 0; height: 0; }
            .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 34px; }
            .slider:before { position: absolute; content: ""; height: 26px; width: 26px; left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%; }
            input:checked + .slider { background-color: #2196F3; }
            input:checked + .slider:before { transform: translateX(26px); }
            .info-box { background: #1a1a2e; border-left: 4px solid #5865F2; padding: 1rem; margin: 1rem 0; border-radius: 5px; }
            .config-badge { display: inline-block; background: #2196F3; color: white; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-left: 5px; }
            .config-removed { background: #f44336; }
            .botoes-lista { display: flex; flex-direction: column; gap: 10px; margin-top: 15px; }
            .botao-item { background: #1a1a1a; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
            .botao-info { flex: 1; }
            .botao-nome { font-weight: bold; color: #f59e0b; }
            .botao-url { font-size: 12px; color: #888; word-break: break-all; }
            .botao-acoes { display: flex; gap: 8px; }
            .recompensa-item { background: #1a1a1a; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
            .recompensa-info { flex: 1; }
            .recompensa-nome { font-weight: bold; color: #feca57; }
            .recompensa-detalhes { font-size: 12px; color: #aaa; }
            .recompensa-acoes { display: flex; gap: 8px; }
            .historico-fila { margin-top: 20px; }
            .historico-fila .busca-uid { margin-bottom: 10px; display: flex; gap: 10px; align-items: center; }
            .historico-fila .busca-uid input { flex: 1; padding: 8px; border-radius: 5px; border: 1px solid var(--gray); background: var(--darker); color: white; }
        </style>
    </head>
    <body>
        <header>
            <div class="header-content">
                <h1> Painel de Controle</h1>
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{{ usuario.id }}/{{ usuario.get('avatar', '') }}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <span>{{ usuario.nome_usuario }}</span>
                    <a href="/" class="btn btn-primary">🏠 Início</a>
                    <a href="/fila" class="btn btn-primary">📋 Fila</a>
                    <a href="/logout" class="btn btn-danger">🚪 Sair</a>
                </div>
            </div>
        </header>
        
        <div class="container">
            <div class="tab-nav">
                <button class="tab-btn active" onclick="showTab(event, 'inicio')">🏠 Início</button>
                <button class="tab-btn" onclick="showTab(event, 'comandos_canais')">📢 Canais</button>
                <button class="tab-btn" onclick="showTab(event, 'antispam')">🛡️ Anti-Spam</button>
                <button class="tab-btn" onclick="showTab(event, 'boasvindas')">👋 Boas-vindas</button>
                <button class="tab-btn" onclick="showTab(event, 'xp')">⭐ XP</button>
                <button class="tab-btn" onclick="showTab(event, 'cargos')">🪪 Cargos</button>
                <button class="tab-btn" onclick="showTab(event, 'moderacao')">🛡️ Moderação</button>
                <button class="tab-btn" onclick="showTab(event, 'fila')">📋 Fila</button>
                <button class="tab-btn" onclick="showTab(event, 'comandos')">⚡ Comandos</button>
                <button class="tab-btn" onclick="showTab(event, 'recompensas')">🎁 Recompensas</button>
            </div>
            
            <!-- Aba Início -->
            <div id="inicio" class="tab active">
                <div class="grid-2">
                    <div class="card">
                        <h2>📊 Estatísticas</h2>
                        <div class="stats-grid">
                            <div class="stat-card"><h3>{{ total_usuarios_xp }}</h3><p>Usuários com XP</p></div>
                            <div class="stat-card"><h3>{{ total_advertencias }}</h3><p>Advertências</p></div>
                            <div class="stat-card"><h3>{{ total_fila }}</h3><p>Na Fila</p></div>
                        </div>
                    </div>
                    <div class="card">
                        <h2>⚡ Status</h2>
                        <p><strong>Bot:</strong> {{ status_bot }}</p>
                        <p><strong>Processador:</strong> {{ processador_status }}</p>
                        <p><strong>Ações na fila:</strong> {{ acoes_fila_bot|length }}</p>
                        <p><strong>Anti-Spam:</strong> {{ anti_spam_status }}</p>
                        <p><strong>Recompensas cadastradas:</strong> {{ total_recompensas }}</p>
                    </div>
                </div>
            </div>
            
            <!-- Aba Canais de Comandos -->
            <div id="comandos_canais" class="tab">
                <div class="card">
                    <h2>📢 Configurar Canais dos Comandos</h2>
                    <div class="info-box">
                        💡 <strong>Como funciona:</strong><br>
                        • Selecione um canal → O comando só funcionará naquele canal<br>
                        • Selecione o <strong>mesmo canal novamente</strong> → Remove a restrição (volta a funcionar em todos os canais)<br>
                        • Deixe em "🔓 Todos os canais" → O comando funciona em qualquer lugar
                    </div>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Canal para o comando /perfil</label>
                            <select id="canal-perfil" class="form-control">
                                <option value="">🔓 Todos os canais</option>
                            </select>
                            <small>Clique no mesmo canal duas vezes para remover a restrição.</small>
                            <div id="perfil-status" style="margin-top: 8px;"></div>
                        </div>
                        <div class="form-group">
                            <label>Canal para o comando /rank</label>
                            <select id="canal-rank" class="form-control">
                                <option value="">🔓 Todos os canais</option>
                            </select>
                            <small>Clique no mesmo canal duas vezes para remover a restrição.</small>
                            <div id="rank-status" style="margin-top: 8px;"></div>
                        </div>
                    </div>
                    <button onclick="salvarConfigComandos()" class="btn btn-primary">💾 Salvar Configurações</button>
                    <div id="comandos-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h2>ℹ️ Informações</h2>
                    <p>Comandos disponíveis no Discord:</p>
                    <ul style="margin-left: 20px; margin-top: 10px;">
                        <li><code>/perfil [membro]</code> - Mostra o perfil com XP e nível</li>
                        <li><code>/rank</code> - Mostra o ranking de XP do servidor</li>
                    </ul>
                    <p style="margin-top: 10px; color: #a8e6cf;">Os comandos só funcionarão nos canais que você configurar acima!</p>
                    <p style="margin-top: 5px; color: #ffd93d;">🔄 <strong>Toggle:</strong> Selecione o mesmo canal duas vezes para remover a configuração.</p>
                </div>
            </div>
            
            <!-- Aba Anti-Spam -->
            <div id="antispam" class="tab">
                <div class="card">
                    <h2>🛡️ Configuração Anti-Spam</h2>
                    <div class="info-box">
                        💡 <strong>Comandos da Mudae são ignorados automaticamente</strong> - Eles NÃO contam como spam e NÃO ganham XP!
                    </div>
                    <div class="grid-2">
                        <div class="form-group">
                            <label>Status do Anti-Spam</label>
                            <label class="switch">
                                <input type="checkbox" id="as-ativado" {{ 'checked' if anti_spam.get('ativado', True) else '' }}>
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Remover XP por Spam</label>
                            <label class="switch">
                                <input type="checkbox" id="as-remover-xp" {{ 'checked' if anti_spam.get('remover_xp', True) else '' }}>
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Deletar Mensagens de Spam</label>
                            <label class="switch">
                                <input type="checkbox" id="as-deletar" {{ 'checked' if anti_spam.get('deletar_mensagens', True) else '' }}>
                                <span class="slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Limite de Mensagens</label>
                            <input type="number" id="as-limite" class="form-control" value="{{ anti_spam.get('limite_mensagens', 5) }}" min="2" max="20">
                        </div>
                        <div class="form-group">
                            <label>Intervalo (segundos)</label>
                            <input type="number" id="as-intervalo" class="form-control" value="{{ anti_spam.get('intervalo_segundos', 5) }}" min="2" max="30">
                        </div>
                        <div class="form-group">
                            <label>Tempo de Mute (minutos)</label>
                            <input type="number" id="as-mute" class="form-control" value="{{ anti_spam.get('tempo_mute_minutos', 2) }}" min="1" max="60">
                        </div>
                        <div class="form-group">
                            <label>Penalidade de XP</label>
                            <input type="number" id="as-xp-penalidade" class="form-control" value="{{ anti_spam.get('xp_penalidade', 50) }}" min="10" max="500">
                        </div>
                        <div class="form-group">
                            <label>Cargos Ignorados (separar por vírgula)</label>
                            <input type="text" id="as-cargos" class="form-control" value="{{ ','.join(anti_spam.get('cargos_ignorados', ['Administrador', 'Moderador', 'Staff', 'Dono'])) }}">
                        </div>
                        <div class="form-group">
                            <label>Comandos Ignorados (separar por vírgula)</label>
                            <input type="text" id="as-comandos" class="form-control" value="{{ ','.join(anti_spam.get('comandos_ignorados', ['$w','$wa','$wg','$h','$ha','$hg','$tu','$dk','$mmi','$vote','$rolls','$k','$mu'])) }}">
                        </div>
                    </div>
                    <button onclick="salvarAntiSpam()" class="btn btn-primary">💾 Salvar Configurações</button>
                    <div id="as-alert" class="alert"></div>
                </div>
                <div class="card">
                    <h2>📋 Comandos Ignorados (NÃO ganham XP e NÃO contam como spam)</h2>
                    <p>Estes comandos são ignorados completamente pelo sistema:</p>
                    <div id="lista-comandos" style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px;"></div>
                </div>
            </div>
            
            <!-- Aba Boas-vindas -->
            <div id="boasvindas" class="tab">
                <div class="card">
                    <h2>👋 Configurar Boas-vindas</h2>
                    <div class="form-group">
                        <label>Canal de Boas-vindas</label>
                        <select id="welcome-canal" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label>Mensagem de Boas-vindas</label>
                        <textarea id="welcome-mensagem" class="form-control" rows="3"></textarea>
                        <small>Use {{member}} para mencionar o membro</small>
                    </div>
                    <div class="form-group">
                        <label>Imagem de Fundo (URL)</label>
                        <input type="url" id="welcome-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                    </div>
                    <button onclick="salvarBoasVindas()" class="btn btn-primary">💾 Salvar</button>
                    <div id="welcome-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- Aba XP -->
            <div id="xp" class="tab">
                <div class="card">
                    <h2>⭐ Sistema de XP</h2>
                    <div class="info-box">
                        💡 <strong>Atenção:</strong> Comandos da Mudae NÃO ganham XP!
                    </div>
                    <div class="form-group">
                        <label>Taxa de XP (1=fácil, 10=difícil)</label>
                        <input type="number" id="xp-taxa" class="form-control" min="1" max="10">
                    </div>
                    <div class="form-group">
                        <label>Canal de Level Up</label>
                        <select id="xp-canal" class="form-control"></select>
                    </div>
                    <button onclick="salvarXP()" class="btn btn-primary">💾 Salvar</button>
                    <div id="xp-alert" class="alert"></div>
                </div>
                
                <div class="card">
                    <h2>🪪 Cargos por Nível</h2>
                    <div id="cargos-nivel-lista"></div>
                    <div class="form-group">
                        <label>Adicionar Cargo por Nível</label>
                        <div style="display: flex; gap: 1rem;">
                            <input type="number" id="novo-nivel" class="form-control" placeholder="Nível" min="1" style="width: 100px;">
                            <select id="novo-cargo" class="form-control" style="flex:1;"></select>
                            <button onclick="adicionarCargoNivel()" class="btn btn-primary">➕ Adicionar</button>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Aba Cargos -->
            <div id="cargos" class="tab">
                <div class="grid-2">
                    <div class="card">
                        <h2>🪪 Reação com Cargo</h2>
                        <div class="form-group">
                            <label>Canal</label>
                            <select id="rr-canal" class="form-control"></select>
                        </div>
                        <div class="form-group">
                            <label>Mensagem</label>
                            <textarea id="rr-conteudo" class="form-control" rows="3" placeholder="Reaja para receber cargos!"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Emoji:Cargo (separar por vírgula)</label>
                            <input type="text" id="rr-pares" class="form-control" placeholder="✅:Verificado,👍:Aprovado,⭐:VIP">
                            <small>Ex: ✅:Verificado, 👍:Aprovado, &lt;:custom:123456789&gt;:VIP</small>
                        </div>
                        <button onclick="criarReacaoCargo()" class="btn btn-primary">✨ Criar</button>
                        <div id="rr-alert" class="alert"></div>
                    </div>
                    
                    <div class="card">
                        <h2>🔄 Botões de Cargos</h2>
                        <div class="form-group">
                            <label>Canal</label>
                            <select id="btn-canal" class="form-control"></select>
                        </div>
                        <div class="form-group">
                            <label>Mensagem</label>
                            <textarea id="btn-conteudo" class="form-control" rows="3" placeholder="Clique nos botões para receber cargos!"></textarea>
                        </div>
                        <div class="form-group">
                            <label>Botão:Cargo (separar por vírgula)</label>
                            <input type="text" id="btn-pares" class="form-control" placeholder="Notícias:Notícias,Eventos:Eventos,VIP:VIP">
                        </div>
                        <button onclick="criarBotoesCargo()" class="btn btn-success">🔄 Criar Botões</button>
                        <div id="btn-alert" class="alert"></div>
                    </div>
                </div>
            </div>
            
            <!-- Aba Moderação -->
            <div id="moderacao" class="tab">
                <div class="grid-2">
                    <div class="card">
                        <h2>🛡️ Advertências</h2>
                        <div class="form-group">
                            <label>Membro</label>
                            <select id="warn-membro" class="form-control"></select>
                        </div>
                        <div class="form-group">
                            <label>Motivo</label>
                            <input type="text" id="warn-motivo" class="form-control" placeholder="Motivo da advertência">
                        </div>
                        <button onclick="aplicarAdvertencia()" class="btn btn-warning">⚠️ Advertir</button>
                        <button onclick="limparAdvertencias()" class="btn btn-danger">🧹 Limpar Advertências</button>
                        <div id="warn-alert" class="alert"></div>
                    </div>
                    
                    <div class="card">
                        <h2>🔗 Bloqueio de Links</h2>
                        <div class="form-group">
                            <label>Canal para bloquear links</label>
                            <select id="links-canal" class="form-control"></select>
                        </div>
                        <button onclick="alternarBloqueioLinks()" class="btn btn-danger">🔒 Alternar Bloqueio</button>
                        <div id="links-status" style="margin-top: 1rem; padding: 0.5rem; background: #1a1a1a; border-radius: 5px;"></div>
                        <div id="links-alert" class="alert"></div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Advertências</h2>
                    <div class="form-group">
                        <label>Ver advertências de</label>
                        <select id="ver-warns" class="form-control" onchange="carregarAdvertencias()"></select>
                    </div>
                    <div id="lista-warns" style="margin-top: 1rem; padding: 1rem; background: #1a1a1a; border-radius: 5px; border: 1px solid var(--gray);"></div>
                </div>
            </div>
            
            <!-- Aba Fila (COM MÚLTIPLOS BOTÕES, PIX E HISTÓRICO) -->
            <div id="fila" class="tab">
                <div class="card">
                    <h2>📋 Configurações da Fila</h2>
                    <div class="grid-2">
                        <div><label>Nome da Fila</label><input type="text" id="fila-nome" class="form-control" value="{{ fila.nome }}"></div>
                        <div><label>Tamanho Máximo</label><input type="number" id="fila-max" class="form-control" value="{{ fila.configuracoes.tamanho_maximo }}" min="1" max="100"></div>
                    </div>

                    <div class="form-group">
                        <label>Link do PIX (no /pedido)</label>
                        <input type="url" id="pix-link" class="form-control" value="{{ pix_link }}" placeholder="https://... ou chave pix">
                    </div>
                    
                    <div class="card" style="margin-top: 20px; background: #1e1e1e; padding: 15px; border-radius: 8px;">
                        <h3>⏳ Pedidos de Serviços Pendentes (Fidelidade)</h3>
                        <div id="pedidos-pendentes-container"><p>Carregando pedidos...</p></div>
                    </div>
                    
                    <h3 style="margin-top: 20px;">🔗 Links do Discord (convite)</h3>
                    <div class="form-group">
                        <label>Link do Discord (convite)</label>
                        <input type="url" id="link-discord" class="form-control" placeholder="https://discord.gg/seuconvite" value="{{ links.get('discord_convite', '') }}">
                    </div>
                    
                    <h3 style="margin-top: 20px;">💰 Botões de Preço (Múltiplos)</h3>
                    <div class="info-box">
                        💡 <strong>Adicione quantos botões quiser!</strong> Cada botão terá um nome personalizado e um link diferente.
                    </div>
                    
                    <div class="form-group">
                        <label>Novo Botão - Nome</label>
                        <input type="text" id="novo-botao-nome" class="form-control" placeholder="Ex: Tabela de Preços, Preços WuWa, Preços Mongil">
                    </div>
                    <div class="form-group">
                        <label>Novo Botão - URL</label>
                        <input type="url" id="novo-botao-url" class="form-control" placeholder="https://docs.google.com/...">
                    </div>
                    <button onclick="adicionarBotaoPreco()" class="btn btn-success">➕ Adicionar Botão</button>
                    
                    <div id="botoes-precos-lista" class="botoes-lista" style="margin-top: 20px;"></div>
                    
                    <div style="display: flex; gap: 1rem; margin-top: 1rem;">
                        <button onclick="salvarConfigFila()" class="btn btn-primary">💾 Salvar Configurações</button>
                        <button onclick="alternarStatusFila()" id="toggle-fila-btn" class="btn {{ 'btn-success' if fila.configuracoes.aberta else 'btn-danger' }}">{{ '🔓 Fechar Fila' if fila.configuracoes.aberta else '🔒 Abrir Fila' }}</button>
                        <button onclick="limparFila()" class="btn btn-danger">🗑️ Limpar Fila</button>
                    </div>
                    <div id="fila-status" style="margin-top: 1rem; padding: 0.5rem; background: #1a1a1a; border-radius: 5px;">Status: {{ '🟢 ABERTA' if fila.configuracoes.aberta else '🔴 FECHADA' }} | {{ fila.entradas|length }}/{{ fila.configuracoes.tamanho_maximo }}</div>
                </div>
                
                <div class="card">
                    <h2>➕ Adicionar à Fila</h2>
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <input type="text" id="add-nome" class="form-control" placeholder="Nome do jogador" style="flex:1;">
                        <input type="text" id="add-servico" class="form-control" placeholder="Serviço" style="flex:1;">
                        <input type="text" id="add-jogo" class="form-control" placeholder="Jogo" style="flex:1;">
                        <input type="text" id="add-uid" class="form-control" placeholder="UID (opcional)" style="flex:1;">
                        <button onclick="adicionarFila()" class="btn btn-primary">➕ Adicionar</button>
                    </div>
                    <div id="add-result" class="alert" style="margin-top: 10px; display: none;"></div>
                </div>
                
                <div class="card">
                    <h2>📋 Lista de Espera</h2>
                    <div style="overflow-x: auto;">
                        <table style="width:100%">
                            <thead>
                                <tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Jogo</th><th>UID</th><th>Data</th><th>Ações</th></tr>
                            </thead>
                            <tbody id="fila-tabela"><tr><td colspan="7">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                    <div style="margin-top: 10px;"><button onclick="atualizarFila()" class="btn btn-primary">🔄 Atualizar</button></div>
                </div>
                
                <!-- HISTÓRICO DA FILA (COM BUSCA POR UID) -->
                <div class="card historico-fila">
                    <h2>📜 Histórico da Fila</h2>
                    <div class="busca-uid">
                        <input type="text" id="historico-filtro-uid" placeholder="Filtrar por UID..." oninput="filtrarHistorico()">
                        <button onclick="filtrarHistorico()" class="btn btn-primary">🔍 Filtrar</button>
                        <button onclick="document.getElementById('historico-filtro-uid').value=''; filtrarHistorico();" class="btn btn-secondary">Limpar</button>
                    </div>
                    <div style="overflow-x: auto;">
                        <table style="width:100%">
                            <thead>
                                <tr><th>#</th><th>Jogador</th><th>Serviço</th><th>Jogo</th><th>UID</th><th>Status</th><th>Data</th></tr>
                            </thead>
                            <tbody id="historico-tabela"><tr><td colspan="7">Carregando...</td></tr></tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <!-- Aba Comandos Rápidos -->
            <div id="comandos" class="tab">
                <div class="card">
                    <h2>📝 Criar Embed Personalizada</h2>
                    <div class="form-group">
                        <label>Canal</label>
                        <select id="embed-canal" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label>Título</label>
                        <input type="text" id="embed-titulo" class="form-control" placeholder="Título da mensagem">
                    </div>
                    <div class="form-group">
                        <label>Corpo da Mensagem</label>
                        <textarea id="embed-corpo" class="form-control" rows="3" placeholder="Conteúdo da mensagem"></textarea>
                    </div>
                    <div class="form-group">
                        <label>Cor (hexadecimal)</label>
                        <input type="text" id="embed-cor" class="form-control" value="#5865F2" placeholder="#5865F2">
                    </div>
                    <div class="form-group">
                        <label>Imagem (URL opcional)</label>
                        <input type="url" id="embed-imagem" class="form-control" placeholder="https://exemplo.com/imagem.jpg">
                    </div>
                    <div class="form-group">
                        <label>Menção</label>
                        <select id="embed-mencao" class="form-control"><option value="">Nenhuma</option><option value="everyone">@everyone</option><option value="here">@here</option></select>
                    </div>
                    <button onclick="criarEmbed()" class="btn btn-primary">📝 Criar Embed</button>
                    <div id="embed-alert" class="alert"></div>
                </div>
            </div>
            
            <!-- NOVA ABA: RECOMPENSAS FIDELIDADE -->
            <div id="recompensas" class="tab">
                <div class="card">
                    <h2>🎁 Gerenciar Recompensas de Pontos</h2>
                    <div class="info-box">
                        💡 <strong>Recompensas:</strong> Os clientes podem trocar seus pontos por esses benefícios. 
                        Cada recompensa deve ter um nome, custo em pontos, tipo (serviço ou cupom) e, se for cupom, um valor de desconto.
                    </div>
                    <div id="recompensas-lista" style="margin: 15px 0;"></div>
                    <hr>
                    <h3>➕ Adicionar Nova Recompensa</h3>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Nome</label>
                            <input type="text" id="nova-rec-nome" class="form-control" placeholder="Ex: 1 Dia de Quests Grátis">
                        </div>
                        <div class="form-group">
                            <label>Pontos Necessários</label>
                            <input type="number" id="nova-rec-pontos" class="form-control" placeholder="60" min="1">
                        </div>
                        <div class="form-group">
                            <label>Tipo</label>
                            <select id="nova-rec-tipo" class="form-control">
                                <option value="servico">Serviço</option>
                                <option value="cupom">Cupom</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Desconto (R$) - Apenas para cupons</label>
                        <input type="number" step="0.50" id="nova-rec-desconto" class="form-control" placeholder="5.00" value="0">
                    </div>
                    <button onclick="adicionarRecompensa()" class="btn btn-success">➕ Adicionar Recompensa</button>
                    <div id="rec-alert" class="alert"></div>
                </div>
            </div>
        </div>
        
        <script>
            let canais = [];
            let cargos = [];
            let membros = [];
            let configAtual = {};
            let botoesPrecos = {{ botoes_precos_json|safe }};
            let recompensas = {{ recompensas_json|safe }};
            let historicoCompleto = {{ historico_json|safe }};
            
            // ========== FUNÇÕES DE RECOMPENSAS ==========
            function carregarRecompensas() {
                const container = document.getElementById('recompensas-lista');
                if (!container) return;
                if (recompensas.length === 0) {
                    container.innerHTML = '<p>Nenhuma recompensa cadastrada.</p>';
                    return;
                }
                let html = '';
                recompensas.forEach((r, idx) => {
                    html += `
                        <div class="recompensa-item">
                            <div class="recompensa-info">
                                <div class="recompensa-nome">${escapeHtml(r.nome)}</div>
                                <div class="recompensa-detalhes">${r.pontos} pontos | Tipo: ${r.tipo} ${r.tipo === 'cupom' ? '| Desconto: R$ '+r.desconto.toFixed(2) : ''}</div>
                            </div>
                            <div class="recompensa-acoes">
                                <button onclick="editarRecompensa('${r.id}')" class="btn btn-primary btn-sm">✏️ Editar</button>
                                <button onclick="removerRecompensa('${r.id}')" class="btn btn-danger btn-sm">🗑️ Remover</button>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            }

            async function adicionarRecompensa() {
                const nome = document.getElementById('nova-rec-nome').value.trim();
                const pontos = parseInt(document.getElementById('nova-rec-pontos').value);
                const tipo = document.getElementById('nova-rec-tipo').value;
                const desconto = parseFloat(document.getElementById('nova-rec-desconto').value) || 0;

                if (!nome || isNaN(pontos) || pontos <= 0) {
                    showAlert('rec-alert', 'Preencha nome e pontos corretamente.', false);
                    return;
                }

                try {
                    const resp = await fetch('/api/fidelidade/recompensas', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ nome, pontos, tipo, desconto })
                    });
                    const data = await resp.json();
                    if (data.sucesso) {
                        recompensas = await carregarRecompensasAPI();
                        carregarRecompensas();
                        document.getElementById('nova-rec-nome').value = '';
                        document.getElementById('nova-rec-pontos').value = '';
                        document.getElementById('nova-rec-desconto').value = '0';
                        showAlert('rec-alert', data.mensagem, true);
                    } else {
                        showAlert('rec-alert', data.mensagem, false);
                    }
                } catch(e) {
                    showAlert('rec-alert', 'Erro: ' + e.message, false);
                }
            }

            async function removerRecompensa(id) {
                if (!confirm('Remover esta recompensa?')) return;
                try {
                    const resp = await fetch(`/api/fidelidade/recompensas/${id}`, { method: 'DELETE' });
                    const data = await resp.json();
                    if (data.sucesso) {
                        recompensas = await carregarRecompensasAPI();
                        carregarRecompensas();
                        showAlert('rec-alert', data.mensagem, true);
                    } else {
                        showAlert('rec-alert', data.mensagem, false);
                    }
                } catch(e) {
                    showAlert('rec-alert', 'Erro: ' + e.message, false);
                }
            }

            async function editarRecompensa(id) {
                const rec = recompensas.find(r => r.id === id);
                if (!rec) return;
                const novoNome = prompt('Novo nome:', rec.nome);
                if (novoNome === null) return;
                const novosPontos = parseInt(prompt('Novos pontos:', rec.pontos));
                if (isNaN(novosPontos) || novosPontos <= 0) return;
                const novoTipo = prompt('Novo tipo (servico ou cupom):', rec.tipo);
                if (novoTipo === null) return;
                let novoDesconto = rec.desconto;
                if (novoTipo === 'cupom') {
                    novoDesconto = parseFloat(prompt('Novo desconto (R$):', rec.desconto)) || 0;
                }
                try {
                    const resp = await fetch(`/api/fidelidade/recompensas/${id}`, {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ nome: novoNome, pontos: novosPontos, tipo: novoTipo, desconto: novoDesconto })
                    });
                    const data = await resp.json();
                    if (data.sucesso) {
                        recompensas = await carregarRecompensasAPI();
                        carregarRecompensas();
                        showAlert('rec-alert', data.mensagem, true);
                    } else {
                        showAlert('rec-alert', data.mensagem, false);
                    }
                } catch(e) {
                    showAlert('rec-alert', 'Erro: ' + e.message, false);
                }
            }

            async function carregarRecompensasAPI() {
                const resp = await fetch('/api/fidelidade/recompensas');
                const data = await resp.json();
                if (data.sucesso) return data.recompensas;
                return [];
            }

            // ========== FUNÇÕES DE HISTÓRICO DA FILA ==========
            function renderizarHistorico(historico) {
                const tbody = document.getElementById('historico-tabela');
                if (!historico || historico.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7">Nenhum registro no histórico.</td></tr>';
                    return;
                }
                let html = '';
                historico.forEach((e, idx) => {
                    const dataStr = e.concluido_em || e.removido_em || e.limpo_em || e.timestamp || '';
                    const dataFormatada = dataStr ? new Date(dataStr).toLocaleDateString('pt-BR') : '-';
                    const status = e.status || 'concluido';
                    const uid = e.uid || e.usuario_id || '';
                    html += `
                        <tr>
                            <td>${idx + 1}</td>
                            <td>${escapeHtml(e.nome_usuario || '')}</td>
                            <td>${escapeHtml(e.servico || '')}</td>
                            <td>${escapeHtml(e.jogo || '')}</td>
                            <td>${escapeHtml(uid)}</td>
                            <td>${status}</td>
                            <td>${dataFormatada}</td>
                        </tr>
                    `;
                });
                tbody.innerHTML = html;
            }

            function filtrarHistorico() {
                const filtro = document.getElementById('historico-filtro-uid').value.trim().toLowerCase();
                if (!filtro) {
                    renderizarHistorico(historicoCompleto);
                    return;
                }
                const filtrados = historicoCompleto.filter(e => {
                    const uid = (e.uid || e.usuario_id || '').toString().toLowerCase();
                    return uid.includes(filtro);
                });
                renderizarHistorico(filtrados);
            }

            // ========== FUNÇÕES EXISTENTES ==========
            function showTab(event, tabId) {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.currentTarget.classList.add('active');
                if (tabId === 'fila') carregarFila();
                if (tabId === 'moderacao') carregarAdvertencias();
                if (tabId === 'recompensas') carregarRecompensas();
            }

            async function carregarDados() {
                try {
                    const [canaisRes, cargosRes, membrosRes, configBoasVindas, configXP, linksRes, antiSpamRes, configComandosRes, filaConfigRes] = await Promise.all([
                        fetch('/api/servidor/canais'),
                        fetch('/api/servidor/cargos'),
                        fetch('/api/servidor/membros'),
                        fetch('/api/config/boasvindas'),
                        fetch('/api/config/xp'),
                        fetch('/api/config/links'),
                        fetch('/api/anti_spam'),
                        fetch('/api/config/comandos'),
                        fetch('/api/fila/configuracoes')
                    ]);
                    
                    const canaisData = await canaisRes.json();
                    const cargosData = await cargosRes.json();
                    const membrosData = await membrosRes.json();
                    const configBV = await configBoasVindas.json();
                    const configXPdata = await configXP.json();
                    const linksData = await linksRes.json();
                    const antiSpamData = await antiSpamRes.json();
                    const configComandosData = await configComandosRes.json();
                    const filaConfig = await filaConfigRes.json();
                    
                    if (canaisData.sucesso) canais = canaisData.canais;
                    if (cargosData.sucesso) cargos = cargosData.cargos;
                    if (membrosData.sucesso) membros = membrosData.membros;
                    
                    popularSelects();
                    
                    if (configBV.sucesso) {
                        document.getElementById('welcome-mensagem').value = configBV.mensagem || '';
                        document.getElementById('welcome-imagem').value = configBV.imagem || '';
                        const welcomeCanal = document.getElementById('welcome-canal');
                        if (welcomeCanal) welcomeCanal.value = configBV.canal || '';
                    }
                    
                    if (configXPdata.sucesso) {
                        document.getElementById('xp-taxa').value = configXPdata.taxa || 3;
                        const xpCanal = document.getElementById('xp-canal');
                        if (xpCanal) xpCanal.value = configXPdata.canal || '';
                    }
                    
                    if (configComandosData.sucesso) {
                        configAtual = configComandosData;
                        const canalPerfil = document.getElementById('canal-perfil');
                        const canalRank = document.getElementById('canal-rank');
                        if (canalPerfil) {
                            canalPerfil.value = configComandosData.canal_perfil || '';
                            atualizarStatusPerfil(configComandosData.canal_perfil);
                        }
                        if (canalRank) {
                            canalRank.value = configComandosData.canal_rank || '';
                            atualizarStatusRank(configComandosData.canal_rank);
                        }
                    }
                    
                    if (linksData.sucesso && linksData.canais) {
                        const linksStatus = document.getElementById('links-status');
                        if (linksStatus) {
                            const nomes = linksData.canais.map(c => {
                                const canal = canais.find(ca => ca.id == c);
                                return canal ? '#' + canal.nome : c;
                            }).join(', ');
                            linksStatus.innerHTML = nomes ? 'Canais bloqueados: ' + nomes : 'Nenhum canal bloqueado';
                        }
                    }
                    
                    if (antiSpamData.sucesso && antiSpamData.config) {
                        document.getElementById('as-ativado').checked = antiSpamData.config.ativado;
                        document.getElementById('as-remover-xp').checked = antiSpamData.config.remover_xp;
                        document.getElementById('as-deletar').checked = antiSpamData.config.deletar_mensagens;
                        document.getElementById('as-limite').value = antiSpamData.config.limite_mensagens;
                        document.getElementById('as-intervalo').value = antiSpamData.config.intervalo_segundos;
                        document.getElementById('as-mute').value = antiSpamData.config.tempo_mute_minutos;
                        document.getElementById('as-xp-penalidade').value = antiSpamData.config.xp_penalidade;
                        document.getElementById('as-cargos').value = antiSpamData.config.cargos_ignorados;
                        document.getElementById('as-comandos').value = antiSpamData.config.comandos_ignorados;
                        
                        const listaDiv = document.getElementById('lista-comandos');
                        const comandos = antiSpamData.config.comandos_ignorados.split(',');
                        listaDiv.innerHTML = comandos.map(c => `<span style="background:#333; padding:4px 12px; border-radius:20px;">${c.trim()}</span>`).join('');
                    }
                    
                    if (filaConfig.sucesso) {
                        if (filaConfig.links) {
                            document.getElementById('link-discord').value = filaConfig.links.discord_convite || '';
                            if (filaConfig.links.botoes_precos) {
                                botoesPrecos = filaConfig.links.botoes_precos;
                            }
                        }
                        if (filaConfig.pix_link) {
                            document.getElementById('pix-link').value = filaConfig.pix_link;
                        }
                    }
                    
                    carregarCargosNivel();
                    carregarFila();
                    carregarBotoesPrecos();
                    carregarRecompensas();
                    renderizarHistorico(historicoCompleto);
                } catch(e) { console.error(e); }
            }
            
            function carregarBotoesPrecos() {
                const container = document.getElementById('botoes-precos-lista');
                if (!container) return;
                
                if (botoesPrecos.length === 0) {
                    container.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">Nenhum botão de preço configurado. Adicione um acima!</div>';
                    return;
                }
                
                let html = '';
                botoesPrecos.forEach((botao, index) => {
                    html += `
                        <div class="botao-item">
                            <div class="botao-info">
                                <div class="botao-nome">💰 ${escapeHtml(botao.nome)}</div>
                                <div class="botao-url">${escapeHtml(botao.url)}</div>
                            </div>
                            <div class="botao-acoes">
                                <button onclick="editarBotaoPreco(${index})" class="btn btn-primary btn-sm">✏️ Editar</button>
                                <button onclick="removerBotaoPreco(${index})" class="btn btn-danger btn-sm">🗑️ Remover</button>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            }
            
            async function adicionarBotaoPreco() {
                const nome = document.getElementById('novo-botao-nome').value.trim();
                const url = document.getElementById('novo-botao-url').value.trim();
                
                if (!nome || !url) {
                    showAlert('fila-status', 'Preencha nome e URL do botão', false);
                    return;
                }
                
                try {
                    const resp = await fetch('/api/fila/botoes/adicionar', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({nome, url})
                    });
                    const result = await resp.json();
                    if (result.sucesso) {
                        document.getElementById('novo-botao-nome').value = '';
                        document.getElementById('novo-botao-url').value = '';
                        await carregarBotoesNovamente();
                        showAlert('fila-status', result.mensagem, true);
                    } else {
                        showAlert('fila-status', result.mensagem, false);
                    }
                } catch(e) {
                    showAlert('fila-status', 'Erro: ' + e.message, false);
                }
            }
            
            async function carregarBotoesNovamente() {
                try {
                    const resp = await fetch('/api/fila/botoes');
                    const data = await resp.json();
                    if (data.sucesso) {
                        botoesPrecos = data.botoes;
                        carregarBotoesPrecos();
                    }
                } catch(e) {
                    console.error(e);
                }
            }
            
            async function removerBotaoPreco(index) {
                if (!confirm('Remover este botão?')) return;
                try {
                    const resp = await fetch('/api/fila/botoes/remover', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({index})
                    });
                    const result = await resp.json();
                    if (result.sucesso) {
                        await carregarBotoesNovamente();
                        showAlert('fila-status', result.mensagem, true);
                    } else {
                        showAlert('fila-status', result.mensagem, false);
                    }
                } catch(e) {
                    showAlert('fila-status', 'Erro: ' + e.message, false);
                }
            }
            
            function editarBotaoPreco(index) {
                const botao = botoesPrecos[index];
                const novoNome = prompt('Digite o novo nome do botão:', botao.nome);
                if (!novoNome) return;
                const novaUrl = prompt('Digite a nova URL:', botao.url);
                if (!novaUrl) return;
                
                fetch('/api/fila/botoes/atualizar', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({index, nome: novoNome, url: novaUrl})
                }).then(async (resp) => {
                    const result = await resp.json();
                    if (result.sucesso) {
                        await carregarBotoesNovamente();
                        showAlert('fila-status', result.mensagem, true);
                    } else {
                        showAlert('fila-status', result.mensagem, false);
                    }
                }).catch(e => showAlert('fila-status', 'Erro: ' + e.message, false));
            }
            
            function atualizarStatusPerfil(canalId) {
                const div = document.getElementById('perfil-status');
                if (!canalId) {
                    div.innerHTML = '<span class="config-badge" style="background:#00b894;">🔓 Funciona em TODOS os canais</span>';
                } else {
                    const canal = canais.find(c => c.id == canalId);
                    div.innerHTML = `<span class="config-badge">📢 /perfil funciona apenas em <strong>#${canal ? canal.nome : canalId}</strong></span> <span style="color:#ffd93d;">(Clique novamente para remover)</span>`;
                }
            }
            
            function atualizarStatusRank(canalId) {
                const div = document.getElementById('rank-status');
                if (!canalId) {
                    div.innerHTML = '<span class="config-badge" style="background:#00b894;">🔓 Funciona em TODOS os canais</span>';
                } else {
                    const canal = canais.find(c => c.id == canalId);
                    div.innerHTML = `<span class="config-badge">📢 /rank funciona apenas em <strong>#${canal ? canal.nome : canalId}</strong></span> <span style="color:#ffd93d;">(Clique novamente para remover)</span>`;
                }
            }
            
            function popularSelects() {
                const selects = ['welcome-canal', 'xp-canal', 'rr-canal', 'btn-canal', 'embed-canal', 'links-canal', 'canal-perfil', 'canal-rank'];
                selects.forEach(id => {
                    const select = document.getElementById(id);
                    if (select) {
                        select.innerHTML = '<option value="">🔓 Todos os canais</option>';
                        canais.forEach(c => {
                            const option = document.createElement('option');
                            option.value = c.id;
                            option.textContent = '#' + c.nome;
                            select.appendChild(option);
                        });
                    }
                });
                
                const cargoSelect = document.getElementById('novo-cargo');
                if (cargoSelect) {
                    cargoSelect.innerHTML = '<option value="">Selecione um cargo</option>';
                    cargos.forEach(c => {
                        const option = document.createElement('option');
                        option.value = c.id;
                        option.textContent = c.nome;
                        cargoSelect.appendChild(option);
                    });
                }
                
                const membroSelects = ['warn-membro', 'ver-warns'];
                membroSelects.forEach(id => {
                    const select = document.getElementById(id);
                    if (select) {
                        select.innerHTML = '<option value="">Selecione um membro</option>';
                        membros.forEach(m => {
                            const option = document.createElement('option');
                            option.value = m.id;
                            option.textContent = m.nome;
                            select.appendChild(option);
                        });
                    }
                });
            }
            
            async function salvarAntiSpam() {
                const data = {
                    ativado: document.getElementById('as-ativado').checked,
                    remover_xp: document.getElementById('as-remover-xp').checked,
                    deletar_mensagens: document.getElementById('as-deletar').checked,
                    limite_mensagens: parseInt(document.getElementById('as-limite').value),
                    intervalo_segundos: parseInt(document.getElementById('as-intervalo').value),
                    tempo_mute_minutos: parseInt(document.getElementById('as-mute').value),
                    xp_penalidade: parseInt(document.getElementById('as-xp-penalidade').value),
                    cargos_ignorados: document.getElementById('as-cargos').value,
                    comandos_ignorados: document.getElementById('as-comandos').value
                };
                try {
                    const resp = await fetch('/api/anti_spam', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('as-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        const comandos = data.comandos_ignorados.split(',');
                        document.getElementById('lista-comandos').innerHTML = comandos.map(c => `<span style="background:#333; padding:4px 12px; border-radius:20px;">${c.trim()}</span>`).join('');
                    }
                } catch(e) { showAlert('as-alert', 'Erro: ' + e.message, false); }
            }
            
            async function salvarBoasVindas() {
                const data = {
                    canal_id: document.getElementById('welcome-canal').value,
                    mensagem: document.getElementById('welcome-mensagem').value,
                    imagem_url: document.getElementById('welcome-imagem').value
                };
                try {
                    const resp = await fetch('/api/config/boasvindas', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('welcome-alert', result.mensagem, result.sucesso);
                } catch(e) { showAlert('welcome-alert', 'Erro: ' + e.message, false); }
            }
            
            async function salvarXP() {
                const data = { taxa: parseInt(document.getElementById('xp-taxa').value), canal_id: document.getElementById('xp-canal').value };
                try {
                    const resp = await fetch('/api/config/xp', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('xp-alert', result.mensagem, result.sucesso);
                } catch(e) { showAlert('xp-alert', 'Erro: ' + e.message, false); }
            }
            
            async function salvarConfigComandos() {
                const canalPerfil = document.getElementById('canal-perfil').value;
                const canalRank = document.getElementById('canal-rank').value;
                
                let perfilFinal = canalPerfil;
                let rankFinal = canalRank;
                
                if (canalPerfil && configAtual.canal_perfil === canalPerfil) {
                    perfilFinal = '';
                }
                if (canalRank && configAtual.canal_rank === canalRank) {
                    rankFinal = '';
                }
                
                const data = {
                    canal_perfil: perfilFinal,
                    canal_rank: rankFinal
                };
                try {
                    const resp = await fetch('/api/config/comandos', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('comandos-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        if (perfilFinal !== canalPerfil) {
                            document.getElementById('canal-perfil').value = '';
                            atualizarStatusPerfil('');
                            configAtual.canal_perfil = '';
                        } else {
                            atualizarStatusPerfil(perfilFinal);
                            configAtual.canal_perfil = perfilFinal;
                        }
                        if (rankFinal !== canalRank) {
                            document.getElementById('canal-rank').value = '';
                            atualizarStatusRank('');
                            configAtual.canal_rank = '';
                        } else {
                            atualizarStatusRank(rankFinal);
                            configAtual.canal_rank = rankFinal;
                        }
                    }
                } catch(e) { showAlert('comandos-alert', 'Erro: ' + e.message, false); }
            }
            
            async function carregarCargosNivel() {
                try {
                    const resp = await fetch('/api/cargos/nivel');
                    const data = await resp.json();
                    const container = document.getElementById('cargos-nivel-lista');
                    if (data.sucesso && Object.keys(data.cargos).length > 0) {
                        let html = '<div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">';
                        for (const [nivel, cargoId] of Object.entries(data.cargos)) {
                            const cargo = cargos.find(c => c.id == cargoId);
                            html += `<div style="background: #333; padding: 0.5rem 1rem; border-radius: 5px;">Nível ${nivel}: ${cargo ? cargo.nome : 'Cargo não encontrado'} <button onclick="removerCargoNivel(${nivel})" style="background:#dc3545;color:white;border:none;border-radius:3px;padding:0.25rem 0.5rem;cursor:pointer;">×</button></div>`;
                        }
                        html += '</div>';
                        container.innerHTML = html;
                    } else {
                        container.innerHTML = '<p>Nenhum cargo por nível configurado.</p>';
                    }
                } catch(e) { console.error(e); }
            }
            
            async function adicionarCargoNivel() {
                const nivel = document.getElementById('novo-nivel').value;
                const cargoId = document.getElementById('novo-cargo').value;
                if (!nivel || !cargoId) {
                    showAlert('xp-alert', 'Preencha nível e cargo', false);
                    return;
                }
                try {
                    const resp = await fetch('/api/cargos/nivel', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({nivel, cargo_id: cargoId})});
                    const result = await resp.json();
                    showAlert('xp-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        document.getElementById('novo-nivel').value = '';
                        carregarCargosNivel();
                    }
                } catch(e) { showAlert('xp-alert', 'Erro: ' + e.message, false); }
            }
            
            async function removerCargoNivel(nivel) {
                if (!confirm('Remover cargo do nível ' + nivel + '?')) return;
                try {
                    const resp = await fetch(`/api/cargos/nivel?nivel=${nivel}`, {method: 'DELETE'});
                    const result = await resp.json();
                    showAlert('xp-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) carregarCargosNivel();
                } catch(e) { showAlert('xp-alert', 'Erro: ' + e.message, false); }
            }
            
            async function criarReacaoCargo() {
                const data = {
                    canal_id: document.getElementById('rr-canal').value,
                    conteudo: document.getElementById('rr-conteudo').value,
                    emoji_cargo: document.getElementById('rr-pares').value
                };
                if (!data.canal_id || !data.conteudo || !data.emoji_cargo) {
                    showAlert('rr-alert', 'Preencha todos os campos', false);
                    return;
                }
                try {
                    const resp = await fetch('/api/reacao_cargo/criar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('rr-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        document.getElementById('rr-conteudo').value = '';
                        document.getElementById('rr-pares').value = '';
                    }
                } catch(e) { showAlert('rr-alert', 'Erro: ' + e.message, false); }
            }
            
            async function criarBotoesCargo() {
                const data = {
                    canal_id: document.getElementById('btn-canal').value,
                    conteudo: document.getElementById('btn-conteudo').value,
                    cargos: document.getElementById('btn-pares').value
                };
                if (!data.canal_id || !data.conteudo || !data.cargos) {
                    showAlert('btn-alert', 'Preencha todos os campos', false);
                    return;
                }
                try {
                    const resp = await fetch('/api/botoes_cargo/criar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('btn-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        document.getElementById('btn-conteudo').value = '';
                        document.getElementById('btn-pares').value = '';
                    }
                } catch(e) { showAlert('btn-alert', 'Erro: ' + e.message, false); }
            }
            
            async function aplicarAdvertencia() {
                const membroId = document.getElementById('warn-membro').value;
                const motivo = document.getElementById('warn-motivo').value;
                if (!membroId || !motivo) {
                    alert('Selecione um membro e digite um motivo');
                    return;
                }
                try {
                    const resp = await fetch('/api/comando/advertir', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({membro_id: membroId, motivo})});
                    const result = await resp.json();
                    alert(result.mensagem);
                    if (result.sucesso) document.getElementById('warn-motivo').value = '';
                } catch(e) { alert('Erro: ' + e.message); }
            }
            
            async function limparAdvertencias() {
                const membroId = document.getElementById('warn-membro').value;
                if (!membroId) { alert('Selecione um membro'); return; }
                if (!confirm('Tem certeza?')) return;
                try {
                    const resp = await fetch('/api/comando/limpar_advertencias', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({membro_id: membroId})});
                    const result = await resp.json();
                    alert(result.mensagem);
                } catch(e) { alert('Erro: ' + e.message); }
            }
            
            async function carregarAdvertencias() {
                const membroId = document.getElementById('ver-warns').value;
                if (!membroId) {
                    document.getElementById('lista-warns').innerHTML = '<p>Selecione um membro</p>';
                    return;
                }
                try {
                    const resp = await fetch(`/api/membro/advertencias?membro_id=${membroId}`);
                    const data = await resp.json();
                    if (data.sucesso && data.advertencias.length > 0) {
                        let html = '<h4>Advertências:</h4><ul>';
                        data.advertencias.forEach(w => {
                            html += `<li><strong>${w.motivo}</strong> - ${w.ts} (por ${w.admin || w.por})</li>`;
                        });
                        html += '</ul>';
                        document.getElementById('lista-warns').innerHTML = html;
                    } else {
                        document.getElementById('lista-warns').innerHTML = '<p>Nenhuma advertência encontrada.</p>';
                    }
                } catch(e) { console.error(e); }
            }
            
            async function alternarBloqueioLinks() {
                const canalId = document.getElementById('links-canal').value;
                if (!canalId) { 
                    showAlert('links-alert', 'Selecione um canal', false);
                    return; 
                }
                try {
                    const resp = await fetch('/api/config/links', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({canal_id: canalId})});
                    const result = await resp.json();
                    if (result.sucesso) {
                        const linksRes = await fetch('/api/config/links');
                        const linksData = await linksRes.json();
                        const nomes = linksData.canais.map(c => {
                            const canal = canais.find(ca => ca.id == c);
                            return canal ? '#' + canal.nome : c;
                        }).join(', ');
                        document.getElementById('links-status').innerHTML = nomes ? 'Canais bloqueados: ' + nomes : 'Nenhum canal bloqueado';
                        showAlert('links-alert', result.mensagem, true);
                    } else {
                        showAlert('links-alert', 'Erro ao alternar bloqueio', false);
                    }
                } catch(e) { 
                    showAlert('links-alert', 'Erro: ' + e.message, false);
                }
            }
            
            async function criarEmbed() {
                const data = {
                    canal_id: document.getElementById('embed-canal').value,
                    titulo: document.getElementById('embed-titulo').value,
                    corpo: document.getElementById('embed-corpo').value,
                    cor: document.getElementById('embed-cor').value,
                    url_imagem: document.getElementById('embed-imagem').value,
                    mencao: document.getElementById('embed-mencao').value
                };
                if (!data.canal_id || !data.titulo || !data.corpo) {
                    alert('Preencha canal, título e corpo');
                    return;
                }
                try {
                    const resp = await fetch('/api/comando/embed', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
                    const result = await resp.json();
                    showAlert('embed-alert', result.mensagem, result.sucesso);
                    if (result.sucesso) {
                        document.getElementById('embed-titulo').value = '';
                        document.getElementById('embed-corpo').value = '';
                        document.getElementById('embed-imagem').value = '';
                    }
                } catch(e) { showAlert('embed-alert', 'Erro: ' + e.message, false); }
            }
            
            // ========== FUNÇÕES DA FILA (COM ATUALIZAÇÃO DO HISTÓRICO) ==========
            async function carregarFila() {
                try {
                    const resp = await fetch('/fila/api');
                    const data = await resp.json();
                    if (data.sucesso) {
                        const fila = data.fila;
                        const tbody = document.getElementById('fila-tabela');
                        if (fila.entradas.length === 0) {
                            tbody.innerHTML = '<tr><td colspan="7">📭 Ninguém na fila</td></tr>';
                        } else {
                            tbody.innerHTML = fila.entradas.map(e => {
                                const dataFormatada = new Date(e.timestamp).toLocaleDateString('pt-BR');
                                return `
                                    <tr>
                                        <td><strong style="color:#ffd93d;">#${e.posicao}</strong></td>
                                        <td>${escapeHtml(e.nome_usuario)}</td>
                                        <td style="color:#a8e6cf;">${escapeHtml(e.servico)}</td>
                                        <td style="color:#ffb347;">${escapeHtml(e.jogo || '')}</td>
                                        <td>${escapeHtml(e.uid || '')}</td>
                                        <td>${dataFormatada}</td>
                                        <td>
                                            <button onclick="moverCima('${e.id}')" class="btn btn-primary btn-sm">⬆️</button>
                                            <button onclick="moverBaixo('${e.id}')" class="btn btn-primary btn-sm">⬇️</button>
                                            <button onclick="concluir('${e.id}')" class="btn btn-success btn-sm">✅</button>
                                            <button onclick="remover('${e.id}')" class="btn btn-danger btn-sm">❌</button>
                                        </td>
                                    </tr>
                                `;
                            }).join('');
                        }
                        if (fila.historico) {
                            historicoCompleto = fila.historico;
                            renderizarHistorico(historicoCompleto);
                        }
                        const filaStatus = document.getElementById('fila-status');
                        if (filaStatus) {
                            filaStatus.innerHTML = `Status: ${fila.aberta ? '🟢 ABERTA' : '🔴 FECHADA'} | ${fila.contagem}/${fila.tamanho_maximo}`;
                        }
                        const toggleBtn = document.getElementById('toggle-fila-btn');
                        if (toggleBtn) {
                            toggleBtn.className = fila.aberta ? 'btn btn-danger' : 'btn btn-success';
                            toggleBtn.textContent = fila.aberta ? '🔓 Fechar Fila' : '🔒 Abrir Fila';
                        }
                    }
                } catch(e) { console.error(e); }
            }
            
            async function adicionarFila() {
                const nome = document.getElementById('add-nome').value.trim();
                const servico = document.getElementById('add-servico').value.trim();
                const jogo = document.getElementById('add-jogo').value.trim();
                const uid = document.getElementById('add-uid').value.trim();
                if (!nome || !servico) {
                    showAlert('add-result', 'Preencha nome e serviço', false);
                    return;
                }
                try {
                    const resp = await fetch('/api/fila/adicionar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({nome_usuario: nome, servico, jogo, uid})});
                    const data = await resp.json();
                    showAlert('add-result', data.mensagem, data.sucesso);
                    if (data.sucesso) {
                        document.getElementById('add-nome').value = '';
                        document.getElementById('add-servico').value = '';
                        document.getElementById('add-jogo').value = '';
                        document.getElementById('add-uid').value = '';
                        carregarFila();
                    }
                } catch(e) { showAlert('add-result', 'Erro: ' + e.message, false); }
            }
            
            async function remover(id) { if (confirm('Remover?')) { await fetch('/api/fila/remover', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entrada_id:id})}); carregarFila(); } }
            async function moverCima(id) { await fetch('/api/fila/mover-cima', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entrada_id:id})}); carregarFila(); }
            async function moverBaixo(id) { await fetch('/api/fila/mover-baixo', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entrada_id:id})}); carregarFila(); }
            async function concluir(id) { if (confirm('Concluir serviço?')) { await fetch('/api/fila/concluir', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entrada_id:id})}); carregarFila(); } }
            async function limparFila() { if (confirm('LIMPAR TODA A FILA?')) { await fetch('/api/fila/limpar', {method:'POST'}); carregarFila(); } }
            async function salvarConfigFila() { 
                const data = {
                    nome: document.getElementById('fila-nome').value,
                    tamanho_maximo: parseInt(document.getElementById('fila-max').value),
                    discord_convite: document.getElementById('link-discord').value,
                    pix_link: document.getElementById('pix-link').value
                };
                await fetch('/api/fila/configuracoes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
                carregarFila();
                showAlert('fila-status', 'Configurações salvas!', true);
            }
            async function alternarStatusFila() { await fetch('/api/fila/configuracoes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aberta:null})}); carregarFila(); }
            function atualizarFila() { carregarFila(); }
            
            function showAlert(id, msg, sucesso) {
                const el = document.getElementById(id);
                if (!el) return;
                el.textContent = msg;
                el.className = 'alert ' + (sucesso ? 'alert-success' : 'alert-error');
                el.style.display = 'block';
                setTimeout(() => el.style.display = 'none', 3000);
            }
            
            function escapeHtml(texto) { if (!texto) return ''; return texto.replace(/[&<>]/g, function(m) { if (m === '&') return '&amp;'; if (m === '<') return '&lt;'; if (m === '>') return '&gt;'; return m; }); }

            async function carregarPedidosPendentes() {
                try {
                    const resp = await fetch('/api/fidelidade/admin/pendentes');
                    const data = await resp.json();
                    const container = document.getElementById('pedidos-pendentes-container');
                    
                    if (data.sucesso && data.pedidos.length > 0) {
                        let html = '<table style="width:100%; color:white; border-collapse: collapse;">' +
                                   '<tr><th style="padding:8px; border-bottom:1px solid #444;">UID</th>' +
                                   '<th style="padding:8px; border-bottom:1px solid #444;">Discord</th>' +
                                   '<th style="padding:8px; border-bottom:1px solid #444;">Serviço</th>' +
                                   '<th style="padding:8px; border-bottom:1px solid #444;">Jogo</th>' +
                                   '<th style="padding:8px; border-bottom:1px solid #444;">Valor</th>' +
                                   '<th style="padding:8px; border-bottom:1px solid #444;">Cupom</th>' +
                                   '<th style="padding:8px; border-bottom:1px solid #444;">Ações</th></tr>';
                        data.pedidos.forEach(p => {
                            html += `<tr>
                                <td style="padding:8px; border-bottom:1px solid #333;">${p.uid}</td>
                                <td style="padding:8px; border-bottom:1px solid #333;">${escapeHtml(p.discord)}</td>
                                <td style="padding:8px; border-bottom:1px solid #333;">${escapeHtml(p.servico)}</td>
                                <td style="padding:8px; border-bottom:1px solid #333;">${escapeHtml(p.jogo || '-')}</td>
                                <td style="padding:8px; border-bottom:1px solid #333; color:#1dd1a1;">R$ ${p.valor.toFixed(2)}</td>
                                <td style="padding:8px; border-bottom:1px solid #333; color:#feca57;">${p.cupom_usado || 'Nenhum'}</td>
                                <td style="padding:8px; border-bottom:1px solid #333;">
                                    <button onclick="aprovarPedido('${p.id}')" style="background:#2ed573; color:black; border:none; border-radius:3px; padding:5px 10px; cursor:pointer; font-weight:bold;">Aprovar</button>
                                    <button onclick="recusarPedido('${p.id}')" style="background:#ff4757; color:white; border:none; border-radius:3px; padding:5px 10px; cursor:pointer;">Recusar</button>
                                </td>
                            </tr>`;
                        });
                        html += '</table>';
                        container.innerHTML = html;
                    } else {
                        container.innerHTML = '<p>Nenhum pedido pendente de aprovação.</p>';
                    }
                } catch(e) { console.error(e); }
            }

            async function aprovarPedido(id) {
                if(!confirm('Aprovar pedido e enviar para a Fila?')) return;
                await fetch('/api/fidelidade/admin/aprovar', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({pedido_id: id})
                });
                carregarPedidosPendentes();
                carregarFila();
            }

            async function recusarPedido(id) {
                if(!confirm('Recusar pedido?')) return;
                await fetch('/api/fidelidade/admin/recusar', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({pedido_id: id})
                });
                carregarPedidosPendentes();
            }
            
            document.addEventListener('DOMContentLoaded', function() {
                carregarDados();
                carregarPedidosPendentes();
                carregarRecompensas();
            });
        </script>
    </body>
    </html>
    """,
    usuario=usuario,
    total_usuarios_xp=total_usuarios_xp,
    total_advertencias=total_advertencias,
    total_fila=total_fila,
    status_bot=status_bot,
    processador_status=processador_status,
    anti_spam_status=anti_spam_status,
    total_recompensas=total_recompensas,
    anti_spam=anti_spam,
    fila=fila,
    links=links,
    pix_link=pix_link,
    botoes_precos_json=json.dumps(botoes_precos),
    recompensas_json=json.dumps(recompensas),
    historico_json=json.dumps(historico),
    acoes_fila_bot=acoes_fila_bot,
    config=config,
    escape_html=escape_html)


@app.route("/api/membro/advertencias")
def api_membro_advertencias():
    membro_id = request.args.get('membro_id')
    if not membro_id:
        return jsonify({"sucesso": False, "advertencias": []})
    warns = dados.get("advertencias", {}).get(str(membro_id), [])
    return jsonify({"sucesso": True, "advertencias": warns})


# ========================
# FUNÇÃO PARA VERIFICAR CANAL PERMITIDO
# ========================

async def verificar_canal_permitido(interaction: discord.Interaction, comando: str) -> bool:
    config = dados.get("config", {})
    canal_permitido = config.get(f"canal_{comando}", None)
    if not canal_permitido:
        return True
    if str(interaction.channel_id) == str(canal_permitido):
        return True
    return False


# ========================
# COMANDOS SLASH DO DISCORD (COM VERIFICAÇÃO DE CANAL)
# ========================

@tree.command(name="perfil", description="Mostra o seu perfil com XP e nível")
@app_commands.describe(membro="Membro para ver o perfil (opcional)")
async def slash_perfil(interaction: discord.Interaction, membro: discord.Member = None):
    if not await verificar_canal_permitido(interaction, "perfil"):
        config = dados.get("config", {})
        canal_permitido = config.get("canal_perfil")
        if canal_permitido:
            canal_menção = f"<#{canal_permitido}>"
        else:
            canal_menção = "nenhum canal configurado"
        await interaction.response.send_message(
            f"❌ O comando `/perfil` só pode ser usado no canal {canal_menção}!",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    alvo = membro or interaction.user
    uid = str(alvo.id)
    xp = dados.get("xp", {}).get(uid, 0)
    nivel = dados.get("nivel", {}).get(uid, xp_para_nivel(xp))

    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)
    pos = next((i + 1 for i, (u, _) in enumerate(ranking) if u == uid), len(ranking))

    largura, altura = 900, 200
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    font_b = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf"), 32)
    font_s = ImageFont.truetype(os.path.join(BASE_DIR, "DejaVuSans.ttf"), 22)

    try:
        avatar_bytes = await alvo.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((120, 120))
        mask = Image.new("L", (120, 120), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 120, 120), fill=255)
        img.paste(avatar, (20, 40), mask)
    except:
        pass

    draw.text((160, 50), alvo.display_name, font=font_b, fill=(0, 255, 255))
    draw.text((largura - 220, 40), f"CLASSIFICAÇÃO #{pos}", font=font_s, fill=(0, 255, 255))
    draw.text((largura - 220, 80), f"NÍVEL {nivel}", font=font_s, fill=(255, 0, 255))

    proximo_xp = 100 + nivel * 50
    atual = xp % proximo_xp
    barra_total_w, barra_h = 560, 36
    x0, y0 = 160, 140
    raio = barra_h // 2

    draw.rounded_rectangle([x0, y0, x0 + barra_total_w, y0 + barra_h], radius=raio, fill=(50, 50, 50))

    preenchimento_w = int(barra_total_w * min(1.0, atual / proximo_xp))
    if preenchimento_w > 0:
        barra_preenchida = Image.new("RGBA", (preenchimento_w, barra_h), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(barra_preenchida)
        fill_draw.rounded_rectangle([0, 0, preenchimento_w, barra_h], radius=raio, fill=(0, 200, 255))
        img.paste(barra_preenchida, (x0, y0), barra_preenchida)

    texto_xp = f"{atual} / {proximo_xp} XP"
    bbox = draw.textbbox((0, 0), texto_xp, font=font_s)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = x0 + (barra_total_w - text_w) // 2
    text_y = y0 + (barra_h - text_h) // 2
    draw.text((text_x, text_y), texto_xp, font=font_s, fill=(255, 255, 255))

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="perfil.png")
    await interaction.followup.send(file=arquivo)


@tree.command(name="rank", description="Mostra o ranking dos 10 maiores XP")
async def slash_rank(interaction: discord.Interaction):
    if not await verificar_canal_permitido(interaction, "rank"):
        config = dados.get("config", {})
        canal_permitido = config.get("canal_rank")
        if canal_permitido:
            canal_menção = f"<#{canal_permitido}>"
        else:
            canal_menção = "nenhum canal configurado"
        await interaction.response.send_message(
            f"❌ O comando `/rank` só pode ser usado no canal {canal_menção}!",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    ranking = sorted(dados.get("xp", {}).items(), key=lambda t: t[1], reverse=True)[:10]
    linhas = []
    for i, (uid, xp) in enumerate(ranking, 1):
        user = interaction.guild.get_member(int(uid))
        nome = user.display_name if user else f"Usuário {uid}"
        nivel = dados.get("nivel", {}).get(uid, xp_para_nivel(xp))
        linhas.append(f"{i}. **{nome}** — {xp} XP (Nível {nivel})")

    texto = "\n".join(linhas) if linhas else "Sem dados ainda."

    embed = discord.Embed(
        title="🏆 Top 10 Ranking de XP",
        description=texto,
        color=discord.Color.gold()
    )
    await interaction.followup.send(embed=embed)


# ========================
# AUTO PING
# ========================
def auto_ping():
    while True:
        try:
            url = os.environ.get("REPLIT_URL") or os.environ.get("SELF_URL")
            if url:
                requests.get(url)
            time.sleep(300)
        except:
            pass


Thread(target=auto_ping, daemon=True).start()


# ========================
# EVENTOS DO BOT
# ========================

@bot.event
async def on_ready():
    print(f"\n{'=' * 50}")
    print(f"🤖 BOT INICIADO: {bot.user}")
    print(f"{'=' * 50}")

    print("📂 Carregando dados do GitHub...")
    carregar_dados_github()

    print("⚙️ Sincronizando comandos slash...")
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            print(f"✅ Comandos sincronizados no servidor")
        else:
            await tree.sync()
            print("✅ Comandos globais sincronizados")
    except Exception as e:
        print(f"❌ Erro ao sincronizar: {e}")

    print("🔄 Restaurando botões persistentes...")
    botoes_cargos = dados.get("botoes_cargos", {})
    restaurados = 0
    for msg_id_str, dicionario_botoes in botoes_cargos.items():
        try:
            msg_id = int(msg_id_str)
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    try:
                        mensagem = await channel.fetch_message(msg_id)
                        if mensagem:
                            class PersistentRoleButton(ui.Button):
                                def __init__(self, label: str, cargo_id: int, mensagem_id: int):
                                    super().__init__(label=label, style=ButtonStyle.primary)
                                    self.cargo_id = cargo_id
                                    self.mensagem_id = mensagem_id

                                async def callback(self, interaction: Interaction):
                                    guild = interaction.guild
                                    membro = interaction.user
                                    cargo = guild.get_role(self.cargo_id)
                                    if not cargo:
                                        await interaction.response.send_message("Cargo não encontrado.",
                                                                                ephemeral=True)
                                        return
                                    if cargo in membro.roles:
                                        await membro.remove_roles(cargo, reason="Botão de cargo")
                                        await interaction.response.send_message(
                                            f"Você **removeu** o cargo {cargo.mention}.",
                                            ephemeral=True)
                                    else:
                                        await membro.add_roles(cargo, reason="Botão de cargo")
                                        await interaction.response.send_message(
                                            f"Você **recebeu** o cargo {cargo.mention}.",
                                            ephemeral=True)
                                    adicionar_log(f"botao_cargo: usuario={membro.id} cargo={cargo.id}")

                            class PersistentRoleButtonView(ui.View):
                                def __init__(self, mensagem_id: int, dicionario_botoes: dict):
                                    super().__init__(timeout=None)
                                    self.mensagem_id = mensagem_id
                                    for label, cargo_id in dicionario_botoes.items():
                                        self.add_item(PersistentRoleButton(label=label, cargo_id=cargo_id,
                                                                           mensagem_id=mensagem_id))

                            view = PersistentRoleButtonView(msg_id, dicionario_botoes)
                            await mensagem.edit(view=view)
                            restaurados += 1
                            break
                    except:
                        continue
                if restaurados > 0:
                    break
        except:
            pass
    print(f"✅ {restaurados}/{len(botoes_cargos)} botões restaurados")

    await asyncio.sleep(2)
    iniciar_processador_acoes()

    config = dados.get("config", {})
    links = obter_links_fila()
    print(f"{'=' * 50}")
    print(f"✨ BOT PRONTO! Comandos: /perfil e /rank")
    print(f"🛡️ Anti-Spam: {'ATIVADO' if dados.get('anti_spam', {}).get('ativado', True) else 'DESATIVADO'}")
    print(f"🚫 Comandos da Mudae: NÃO ganham XP e NÃO contam como spam")
    print(f"📢 Canal do /perfil: {config.get('canal_perfil') or 'TODOS OS CANAIS'}")
    print(f"📢 Canal do /rank: {config.get('canal_rank') or 'TODOS OS CANAIS'}")
    botoes_qtd = len(links.get("botoes_precos", []))
    if links.get('discord_convite') or botoes_qtd > 0:
        print(f"🔗 Links da fila configurados: {botoes_qtd} botão(ões) de preço")
    print(f"💡 Dica: Selecione o mesmo canal duas vezes no painel para remover a restrição!")
    print(f"{'=' * 50}\n")


@bot.event
async def on_member_join(member: discord.Member):
    ch_id = dados.get("config", {}).get("canal_boas_vindas")
    canal = None
    if ch_id:
        canal = member.guild.get_channel(int(ch_id))
    if not canal:
        canal = discord.utils.get(member.guild.text_channels, name="boas-vindas")
    if not canal:
        return

    msg = dados.get("config", {}).get("mensagem_boas_vindas", "Olá {member}, seja bem-vindo(a)!")
    msg = msg.replace("{member}", member.mention)

    fundo_url = dados.get("config", {}).get("fundo_boas_vindas", "")

    largura, altura = 900, 300
    img = Image.new("RGBA", (largura, altura), (0, 0, 0, 255))

    if fundo_url:
        try:
            response = requests.get(fundo_url)
            bg = Image.open(BytesIO(response.content)).convert("RGBA")
            bg = bg.resize((largura, altura))
            img.paste(bg, (0, 0))
        except:
            pass

    overlay = Image.new("RGBA", (largura, altura), (50, 50, 50, 150))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    try:
        avatar_bytes = await member.avatar.read()
        avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((150, 150))
        mask = Image.new("L", (150, 150), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 150, 150), fill=255)
        img.paste(avatar, (375, 30), mask)
    except:
        pass

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font = ImageFont.load_default()
        font_s = ImageFont.load_default()

    nome = member.display_name
    bbox = draw.textbbox((0, 0), nome, font=font)
    text_x = (largura - (bbox[2] - bbox[0])) // 2
    draw.text((text_x, 200), nome, font=font, fill=(0, 255, 255))

    texto_membro = f"Membro #{len(member.guild.members)}"
    bbox2 = draw.textbbox((0, 0), texto_membro, font=font_s)
    text_x2 = (largura - (bbox2[2] - bbox2[0])) // 2
    draw.text((text_x2, 250), texto_membro, font=font_s, fill=(255, 255, 255))

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    arquivo = discord.File(buf, filename="welcome.png")

    await canal.send(content=msg, file=arquivo)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
    if not msgmap:
        return

    role_id = None
    if payload.emoji.id and str(payload.emoji.id) in msgmap:
        role_id = msgmap[str(payload.emoji.id)]
    elif str(payload.emoji) in msgmap:
        role_id = msgmap[str(payload.emoji)]

    if not role_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    role = guild.get_role(int(role_id))
    if role:
        await member.add_roles(role, reason="Reaction role")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    msgmap = dados.get("reacoes_cargos", {}).get(str(payload.message_id))
    if not msgmap:
        return

    role_id = None
    if payload.emoji.id and str(payload.emoji.id) in msgmap:
        role_id = msgmap[str(payload.emoji.id)]
    elif str(payload.emoji) in msgmap:
        role_id = msgmap[str(payload.emoji)]

    if not role_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    role = guild.get_role(int(role_id))
    if role:
        await member.remove_roles(role, reason="Reaction role")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    conteudo = message.content.strip()
    anti_spam_config = dados.get("anti_spam", {})

    eh_comando_ignorado = verificar_comando_ignorado(conteudo)

    if eh_comando_ignorado:
        await bot.process_commands(message)
        return

    if anti_spam_config.get("ativado", True):
        if not verificar_cargo_ignorado(message.author):
            quantidade = registrar_mensagem(message.author.id)
            limite = anti_spam_config.get("limite_mensagens", 5)

            if quantidade > limite:
                duracao = anti_spam_config.get("tempo_mute_minutos", 2)
                sucesso = await aplicar_mute(message.author, duracao)

                if sucesso:
                    if anti_spam_config.get("deletar_mensagens", True):
                        await deletar_mensagens_spam(message.author, message.channel, quantidade)

                    xp_removido = False
                    if anti_spam_config.get("remover_xp", True):
                        xp_removido = await remover_xp_por_spam(message.author)

                    xp_msg = f" e teve **{anti_spam_config.get('xp_penalidade', 50)} XP removido**" if xp_removido else ""
                    try:
                        await message.author.send(
                            f"⚠️ **Você foi mutado por {duracao} minutos** devido a spam no servidor {message.guild.name}!{xp_msg}\nPor favor, evite enviar muitas mensagens repetidas em um curto período.\n")
                    except:
                        await message.channel.send(
                            f"⚠️ {message.author.mention}, você foi mutado por **{duracao} minutos** por spam!{xp_msg}")

                    adicionar_log(
                        f"anti_spam: {message.author.name} mutado por {duracao} min | {quantidade} msgs em {anti_spam_config.get('intervalo_segundos', 5)}s | XP removido: {xp_removido}")

                return

    canais_bloqueados = dados.get("canais_links_bloqueados", [])
    if message.channel.id in canais_bloqueados:
        url_pattern = r"https?://[^\s]+"
        if re.search(url_pattern, conteudo):
            cargos_ignorados = {"Administrador", "Moderador"}
            if not any(r.name in cargos_ignorados for r in message.author.roles):
                try:
                    await message.delete()
                    await message.channel.send(f"⚠️ {message.author.mention}, links não são permitidos aqui!")
                except:
                    pass
                return

    dados.setdefault("xp", {})
    dados.setdefault("nivel", {})

    taxa_xp = dados.get("config", {}).get("taxa_xp", 3)
    ganho_xp = max(1, xp_por_mensagem() // taxa_xp)
    dados["xp"][str(message.author.id)] = dados["xp"].get(str(message.author.id), 0) + ganho_xp

    xp_atual = dados["xp"][str(message.author.id)]
    nivel_atual = xp_para_nivel(xp_atual)
    nivel_anterior = dados["nivel"].get(str(message.author.id), 1)

    if nivel_atual > nivel_anterior:
        dados["nivel"][str(message.author.id)] = nivel_atual

        canal_levelup_id = dados.get("config", {}).get("canal_levelup")
        if canal_levelup_id:
            canal = message.guild.get_channel(int(canal_levelup_id))
            if canal:
                await canal.send(f"🎉 {message.author.mention} subiu para o nível **{nivel_atual}**!")

        cargo_id = dados.get("cargos_nivel", {}).get(str(nivel_atual))
        if cargo_id:
            cargo = message.guild.get_role(int(cargo_id))
            if cargo:
                try:
                    await message.author.add_roles(cargo, reason=f"Nível {nivel_atual}")
                except:
                    pass

    try:
        salvar_dados_github("XP update")
    except:
        pass

    await bot.process_commands(message)


# ========================
# INICIAR BOT E FLASK
# ========================

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("Erro ao iniciar o bot:", e)