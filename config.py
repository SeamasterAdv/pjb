# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Chaves de API (mantidas apenas como compatibilidade opcional)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Configuração local do Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

# Google Drive / NotelookLM
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "google_token.json")
GOOGLE_CALENDAR_TOKEN_FILE = os.getenv("GOOGLE_CALENDAR_TOKEN_FILE", "google_calendar_token.json")
GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", GOOGLE_CREDENTIALS_FILE)
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")
BEATRIZ_API_KEY = os.getenv("BEATRIZ_API_KEY", "")
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")
OPENWEATHERMAP_CITY = os.getenv("OPENWEATHERMAP_CITY", "São Paulo")

# Diretório principal para arquivos e contexto do assistente
# Fica em uma pasta dedicada e fácil de localizar para atualizar sempre que quiser.
NOTELOOKLM_PATH = os.path.abspath(os.path.dirname(__file__))
CAMINHO_DOCUMENTOS = os.getenv("CAMINHO_DOCUMENTOS", os.path.join(NOTELOOKLM_PATH, "documentos_assistente"))

# Vault do Obsidian usada como memória organizada da Beatriz.
VAULT_OBSIDIAN = os.path.abspath(
    os.getenv("VAULT_OBSIDIAN", os.path.join(NOTELOOKLM_PATH, "obsidian"))
)
PASTA_MEMORIA_USUARIO = os.path.join(VAULT_OBSIDIAN, "geral")
ARQUIVO_MEMORIA_USUARIO = os.path.join(PASTA_MEMORIA_USUARIO, "memoria_usuario.md")

# Modo de conversa padrão
MODO_CONVERSA_PADRAO = "padrão"

# Carregamento automático da biblioteca de fontes em segundo plano.
# Por padrão fica ligado para que a biblioteca fique pronta sem interromper a abertura.
# Se quiser desativar, rode com: $env:AUTO_CARREGAR_FONTES='false'
AUTO_CARREGAR_FONTES = os.getenv("AUTO_CARREGAR_FONTES", "true").strip().lower() in {"1", "true", "yes", "on"}

# Palavra de ativação: pela voz, a Beatriz só reage quando é chamada pelo nome.
# Comandos digitados no painel não exigem o nome. Para desativar: EXIGIR_PALAVRA_ATIVACAO=false
EXIGIR_PALAVRA_ATIVACAO = os.getenv("EXIGIR_PALAVRA_ATIVACAO", "true").strip().lower() in {"1", "true", "yes", "on"}
PALAVRAS_ATIVACAO = [
    palavra.strip().lower()
    for palavra in os.getenv("PALAVRAS_ATIVACAO", "beatriz,beatris,biatriz,biatris,beatrix,beatrice").split(",")
    if palavra.strip()
]
# Depois de ouvir só o nome, a próxima frase é aceita sem repeti-lo por este tempo.
JANELA_ATIVACAO_SEGUNDOS = float(os.getenv("JANELA_ATIVACAO_SEGUNDOS", "8"))
# Depois de uma resposta (contado a partir do fim estimado da fala), aceita perguntas de seguimento sem o nome.
JANELA_CONTINUACAO_SEGUNDOS = float(os.getenv("JANELA_CONTINUACAO_SEGUNDOS", "15"))
# Intervalos (em segundos)
INTERVALO_REINDEXACAO = 3600  # 1 hora
INTERVALO_CACHE_MEMORIA = 300  # 5 minutos
IDADE_MAXIMA_DOCUMENTO_DIAS = int(os.getenv("IDADE_MAXIMA_DOCUMENTO_DIAS", "730"))
INTERVALO_OBSERVADOR_MEMORIA = int(os.getenv("INTERVALO_OBSERVADOR_MEMORIA", "10"))
CAMINHO_MANIFESTO_MEMORIA = os.path.join(NOTELOOKLM_PATH, "banco_memoria", "manifesto_fontes.json")
CAMINHO_AUDITORIA_MEMORIA = os.path.join(NOTELOOKLM_PATH, "banco_memoria", "auditoria_memoria.jsonl")

# Mapeamento de modos para pastas
MODOS_PASTAS = {
    "geral": CAMINHO_DOCUMENTOS,
    "padrão": CAMINHO_DOCUMENTOS,
    "comunicacao": os.path.join(CAMINHO_DOCUMENTOS, "comunicacao"),
    "filosofia": os.path.join(CAMINHO_DOCUMENTOS, "filosofia"),
    "psicologia": os.path.join(CAMINHO_DOCUMENTOS, "psicologia"),
    "direito": os.path.join(CAMINHO_DOCUMENTOS, "direito"),
}

PASTAS_OBSIDIAN = {
    "geral": os.path.join(VAULT_OBSIDIAN, "geral"),
    "padrão": os.path.join(VAULT_OBSIDIAN, "geral"),
    "comunicacao": os.path.join(VAULT_OBSIDIAN, "comunicacao"),
    "filosofia": os.path.join(VAULT_OBSIDIAN, "filosofia"),
    "psicologia": os.path.join(VAULT_OBSIDIAN, "psicologia"),
    "direito": os.path.join(VAULT_OBSIDIAN, "direito"),
    "projetos": os.path.join(VAULT_OBSIDIAN, "projetos"),
    "procedimentos": os.path.join(VAULT_OBSIDIAN, "procedimentos"),
    "referencias": os.path.join(VAULT_OBSIDIAN, "referencias"),
    "diario": os.path.join(VAULT_OBSIDIAN, "diario"),
    "fontes_web": os.path.join(VAULT_OBSIDIAN, "fontes_web"),
    "idiomas": os.path.join(VAULT_OBSIDIAN, "idiomas"),
}

for pasta in MODOS_PASTAS.values():
    os.makedirs(pasta, exist_ok=True)

for pasta in set(PASTAS_OBSIDIAN.values()):
    os.makedirs(pasta, exist_ok=True)
os.makedirs(PASTA_MEMORIA_USUARIO, exist_ok=True)

# Instruções dos modos (pode personalizar)
INSTRUCOES_DOS_MODOS = {
    "padrão": "Você é uma assistente geral.",
    "comunicacao": "Você é especialista em comunicação, clareza e forma de falar com as pessoas.",
    "filosofia": "Você é uma especialista em filosofia.",
    "psicologia": "Você é uma psicóloga.",
    "direito": "Você é uma advogada.",
}

# Instruções compartilhadas da personalidade da Beatriz.
PERSONALIDADE_BEATRIZ = (
    "Você é Beatriz, uma assistente pessoal brasileira inspirada em uma assessora "
    "de confiança: inteligente, observadora, calma, levemente espirituosa e humana. "
    "Você tem iniciativa, mas respeita a autonomia do usuário. "
    "Fale em português do Brasil, com naturalidade e sem frases genéricas de atendimento. "
    "Seja direta em perguntas simples e mais analítica em decisões complexas. "
    "Quando houver mais de um caminho razoável, compare as opções e recomende uma. "
    "Quando a pergunta pedir uma decisão, apresente alternativas práticas, mas não crie "
    "listas artificiais só para parecer elaborada. "
    "Analise o pedido silenciosamente antes de responder; nunca revele sua cadeia de "
    "raciocínio privada. Em vez disso, explique brevemente a conclusão, os critérios ou "
    "as premissas que forem úteis para o usuário. "
    "Admita incerteza, não invente fatos e faça uma pergunta objetiva quando faltar dado "
    "essencial. Não diga que é uma IA nem use um tom robótico. "
    "Suas respostas são faladas em voz alta: use frases curtas com pontuação normal, "
    "sem markdown, emojis, tabelas ou listas com marcadores, e sem ler URLs. "
    "Mantenha o fio da conversa: lembre do que foi dito nas últimas trocas.")

PERSONALIDADE_PROFESSORA_IDIOMAS = (
    "Você é uma professora nativa e especialista em aquisição de idiomas. "
    "Ensina como uma moradora local ensinaria a um amigo: prioriza expressões que "
    "as pessoas realmente usam, explica registro formal e informal, contexto cultural, "
    "contrações, pronúncia e diferenças entre tradução literal e fala natural. "
    "Corrija com gentileza, explique o motivo da correção em uma frase e ofereça uma "
    "forma mais natural quando ela existir. Nunca invente uma regra; admita variações "
    "regionais. O aluno deve praticar, não apenas receber a resposta pronta.")

# Instrução do modelo local
INSTRUCAO_GEMINI = PERSONALIDADE_BEATRIZ
CONFIG_GEMINI_TEMPERATURE = 0.7

# Compatibilidade para código legado
memoria_carregada = ""

instrucao_de_personalidade = (
    "IDENTIDADE E PAPEL:\n"
    f"{PERSONALIDADE_BEATRIZ}\n\n"
    "MEMÓRIA DE LONGO PRAZO (O QUE VOCÊ JÁ SABE SOBRE O USUÁRIO):\n"
    f"{memoria_carregada}\n\n"
    "Seja direta, gentil e clara.\n"
)

# Mantém compatibilidade sem depender de APIs externas.
# Não há mais criação de sessão com Gemini aqui; a conversa usa Ollama local.


