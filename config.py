# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Chaves de API (mantidas apenas como compatibilidade opcional)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Configuração local do Ollama
# No Windows, "localhost" tenta primeiro o IPv6, e o Ollama só escuta no IPv4: cada conexão
# nova levava ~2 s, e isso se repetia a cada conversa (a conexão expira com 5 s parada).
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").replace("://localhost", "://127.0.0.1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")
# Quanto tempo o modelo fica na memória depois do último uso (o padrão do Ollama é 5 min,
# e recarregar o modelo custa ~3 s na próxima pergunta).
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "1h")

# Google Drive / NotelookLM
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "google_token.json")
GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", GOOGLE_CREDENTIALS_FILE)
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")
BEATRIZ_API_KEY = os.getenv("BEATRIZ_API_KEY", "")
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")
OPENWEATHERMAP_CITY = os.getenv("OPENWEATHERMAP_CITY", "São Paulo")
# Cidade usada quando o pedido não diz qual (filmes em cartaz, cinemas).
CIDADE_PADRAO = os.getenv("CIDADE", OPENWEATHERMAP_CITY)
# Painel: "web" (pywebview, no estilo do Sistema, com o núcleo de anéis) ou "tk" (o painel clássico em Tkinter).
INTERFACE = os.getenv("INTERFACE", "web").strip().lower()
# A mascote pequena andando pela tela (só com o painel web) e as sugestões dela, feitas a partir
# do programa e do título da janela em uso (tudo fica no computador).
MASCOTE_NA_TELA = os.getenv("MASCOTE_NA_TELA", "true").strip().lower() in {"1", "true", "yes", "on"}
SUGESTOES_DA_MASCOTE = os.getenv("SUGESTOES_DA_MASCOTE", "true").strip().lower() in {"1", "true", "yes", "on"}

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
# Depois de uma resposta (contado a partir do fim da fala), aceita perguntas de seguimento sem o nome.
# Curta de propósito: com a janela longa, conversas do ambiente entravam como pedidos.
JANELA_CONTINUACAO_SEGUNDOS = float(os.getenv("JANELA_CONTINUACAO_SEGUNDOS", "8"))
# Conversa em andamento: até este tempo depois da última fala dela, as frases sem o nome ainda passam
# pela IA local, mas o que for duvidoso conta como dito para ela (sem o "Foi comigo?"), e "valeu" ou
# "tchau" ganham resposta. Assim o papo flui sem repetir "Beatriz" a cada frase. 0 desliga.
JANELA_CONVERSA_SEGUNDOS = float(os.getenv("JANELA_CONVERSA_SEGUNDOS", "40"))
# Voz online (edge-tts): a voz, a velocidade e o tom (em Hz; positivo soa mais animado).
# Há amostras para comparar em documentos_gerados/amostras_voz.
VOZ_BEATRIZ = os.getenv("VOZ_BEATRIZ", "pt-BR-ThalitaNeural").strip()
VELOCIDADE_VOZ = os.getenv("VELOCIDADE_VOZ", "+15%").strip()
TOM_VOZ = os.getenv("TOM_VOZ", "+0Hz").strip()
# Escuta: quanto silêncio encerra uma frase (antes eram 2,2 s de espera a cada pedido)
# e a duração máxima de uma frase.
PAUSA_FIM_DE_FRASE = float(os.getenv("PAUSA_FIM_DE_FRASE", "0.9"))
LIMITE_FRASE_SEGUNDOS = float(os.getenv("LIMITE_FRASE_SEGUNDOS", "15"))
# Frases sem o nome: a IA local avalia se eram para a Beatriz; se é claro, ela atende, se é
# duvidoso, pergunta antes. Com false, volta a ignorar tudo o que vier sem o nome.
ENTENDER_SEM_NOME = os.getenv("ENTENDER_SEM_NOME", "true").strip().lower() in {"1", "true", "yes", "on"}
# Abaixo desta confiança da transcrição (0 a 1), ela confirma o que entendeu antes de agir.
CONFIANCA_MINIMA_VOZ = float(os.getenv("CONFIANCA_MINIMA_VOZ", "0.6"))
# Quem transcreve a voz: "whisper" (local, pelo faster-whisper: o áudio não sai do computador),
# "google" (API gratuita e não oficial, que recebe o áudio e pode parar a qualquer momento) ou
# "auto" (Whisper, com o Google de reserva enquanto o modelo carrega ou se ele falhar).
TRANSCRICAO_VOZ = os.getenv("TRANSCRICAO_VOZ", "auto").strip().lower()
if TRANSCRICAO_VOZ not in {"auto", "whisper", "google"}:
    TRANSCRICAO_VOZ = "auto"
# Modelo do Whisper para a voz. Numa CPU i7-12700K: small ~1,1 s por frase (o Google leva
# ~1 s); base ~0,4 s, mas erra mais ("que horas são" virou "que oração").
WHISPER_MODELO_VOZ = os.getenv("WHISPER_MODELO_VOZ", os.getenv("WHISPER_MODEL", "small"))

# Agentes (documentos, apresentações e pesquisas completas, com crítico e relator).
# Os arquivos gerados ficam fora de documentos_assistente para não entrarem na biblioteca.
PASTA_DOCUMENTOS_GERADOS = os.getenv("PASTA_DOCUMENTOS_GERADOS", os.path.join(NOTELOOKLM_PATH, "documentos_gerados"))
# Modelo do Ollama usado pelos agentes (padrão: o mesmo da conversa, para não carregar outro na memória).
MODELO_AGENTES = os.getenv("MODELO_AGENTES", OLLAMA_MODEL)
# Quantas vezes o crítico pode devolver o trabalho para correção.
MAX_REVISOES_AGENTES = int(os.getenv("MAX_REVISOES_AGENTES", "1"))
ABRIR_DOCUMENTOS_GERADOS = os.getenv("ABRIR_DOCUMENTOS_GERADOS", "true").strip().lower() in {"1", "true", "yes", "on"}
# Temas das notícias do resumo do dia, separados por vírgula.
TEMAS_RESUMO_DIA = [t.strip() for t in os.getenv("TEMAS_RESUMO_DIA", "direito,jogos,entretenimento,filmes").split(",") if t.strip()]

# Comandos pelo WhatsApp: escreva na conversa "Mensagem para você" e a Beatriz responde lá.
# Abre um Chromium próprio com o WhatsApp Web (QR code na primeira vez). Para desligar: WHATSAPP_COMANDOS=false
WHATSAPP_COMANDOS = os.getenv("WHATSAPP_COMANDOS", "true").strip().lower() in {"1", "true", "yes", "on"}
# Vazio: descobre sozinho pelo login. Formato com DDI e DDD, só números (ex.: 5511987654321).
WHATSAPP_MEU_NUMERO = "".join(c for c in os.getenv("WHATSAPP_MEU_NUMERO", "") if c.isdigit())

# Gemini no plano gratuito (chave em GEMINI_API_KEY): escreve as respostas de pesquisa e
# trabalha nos agentes. Só recebe a pergunta e conteúdo público da internet, nunca seus
# documentos ou memória. No plano gratuito o Google pode usar esses dados para melhorar
# os produtos dele. Para desligar: USAR_GEMINI=false
USAR_GEMINI = os.getenv("USAR_GEMINI", "true").strip().lower() in {"1", "true", "yes", "on"}
GEMINI_MODELO = os.getenv("GEMINI_MODELO", "gemini-3.8-flash")

# Claude pela assinatura (Pro/Max), sem cobrança à parte: a Beatriz chama o Claude Code
# (`claude -p`) que já vem com o app desktop, usando o login da sua conta. Gasta os limites
# do plano. Mesma regra de privacidade do Gemini. Para desligar: USAR_CLAUDE=false
USAR_CLAUDE = os.getenv("USAR_CLAUDE", "true").strip().lower() in {"1", "true", "yes", "on"}
CLAUDE_CODE_CAMINHO = os.getenv("CLAUDE_CODE_CAMINHO", "")  # vazio: acha sozinho
CLAUDE_MODELO = os.getenv("CLAUDE_MODELO", "")  # vazio: o padrão do plano; ou 'sonnet', 'opus'...
CLAUDE_ESFORCO = os.getenv("CLAUDE_ESFORCO", "")  # vazio: o padrão; ou low, medium, high
# Ordem em que as IAs da nuvem são tentadas antes do Ollama local.
ORDEM_IA_NUVEM = [n.strip().lower() for n in os.getenv("ORDEM_IA_NUVEM", "claude,gemini").split(",") if n.strip()]
# Resumos e aulas de matéria ("resumo da matéria de X", "crie aulas da matéria de X", "resume as
# fontes que eu coloquei"): o Claude lê as suas fontes sobre o assunto (documentos e notas, sem o
# diário, a memória e o histórico de conversa) e escreve o material. Exceção pedida à regra acima: as fontes vão
# só para o Claude, nunca para o Gemini; sem o Claude, o material é feito no Ollama.
# Para desligar: ESTUDOS_COM_CLAUDE=false
ESTUDOS_COM_CLAUDE = os.getenv("ESTUDOS_COM_CLAUDE", "true").strip().lower() in {"1", "true", "yes", "on"}
# Quanto das suas fontes (em caracteres) o Claude lê num resumo de matéria (~4 caracteres por token).
LIMITE_FONTES_ESTUDO = int(os.getenv("LIMITE_FONTES_ESTUDO", "120000"))

# Agenda local (agente_agenda.py): compromissos, rotina e tarefas num banco SQLite no próprio PC
# (banco_memoria/agenda.sqlite3), sem depender do Google. Os horários são do relógio do Windows.
CAMINHO_AGENDA = os.getenv("CAMINHO_AGENDA", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "agenda.sqlite3"))
# Minutos de antecedência do aviso de cada compromisso ("me avisa 1 hora antes" troca só naquele).
AGENDA_LEMBRETE_PADRAO = int(os.getenv("AGENDA_LEMBRETE_PADRAO", "15"))
# Faixa do dia usada para achar horários livres e encaixar tarefas ("organiza meu dia").
AGENDA_INICIO_DIA = os.getenv("AGENDA_INICIO_DIA", "08:00")
AGENDA_FIM_DIA = os.getenv("AGENDA_FIM_DIA", "22:00")
# Hora do aviso das tarefas que vencem no dia.
AGENDA_HORA_TAREFAS = os.getenv("AGENDA_HORA_TAREFAS", "09:00")
# Os lembretes também vão para a conversa "Mensagem para você" do WhatsApp (se estiver conectado),
# para chegarem no celular quando você não está perto do PC. Para desligar: AGENDA_LEMBRETES_WHATSAPP=false
AGENDA_LEMBRETES_WHATSAPP = os.getenv("AGENDA_LEMBRETES_WHATSAPP", "true").strip().lower() in {"1", "true", "yes", "on"}

# Impressora (agente_impressora.py): imprime arquivos, textos ditados e respostas pela fila do Windows.
# Vazio = a impressora padrão do Windows (hoje a EPSON L395 Series).
IMPRESSORA_NOME = os.getenv("IMPRESSORA_NOME", "").strip()
# Antes de imprimir, ela diz o que vai sair (arquivo, páginas, cópias, cor) e espera um "sim".
# Para imprimir direto: IMPRESSORA_CONFIRMAR=false
IMPRESSORA_CONFIRMAR = os.getenv("IMPRESSORA_CONFIRMAR", "true").strip().lower() in {"1", "true", "yes", "on"}
# Resolução das páginas enviadas à impressora (pontos por polegada). 300 é nítido para texto e foto.
IMPRESSORA_DPI = int(os.getenv("IMPRESSORA_DPI", "300"))

# Intervalos (em segundos)
INTERVALO_REINDEXACAO = 3600  # 1 hora
INTERVALO_CACHE_MEMORIA = 300  # 5 minutos
IDADE_MAXIMA_DOCUMENTO_DIAS = int(os.getenv("IDADE_MAXIMA_DOCUMENTO_DIAS", "730"))
INTERVALO_OBSERVADOR_MEMORIA = int(os.getenv("INTERVALO_OBSERVADOR_MEMORIA", "10"))
CAMINHO_MANIFESTO_MEMORIA = os.path.join(NOTELOOKLM_PATH, "banco_memoria", "manifesto_fontes.json")
CAMINHO_AUDITORIA_MEMORIA = os.path.join(NOTELOOKLM_PATH, "banco_memoria", "auditoria_memoria.jsonl")

# Embedding da biblioteca (a busca por significado). Precisa ser multilíngue: o padrão do
# ChromaDB (all-MiniLM-L6-v2) foi treinado em inglês e se perde em português.
# Instale com: ollama pull bge-m3. Trocar o modelo cria outra coleção e reindexa tudo;
# sem o modelo no Ollama (ou com o valor vazio), a biblioteca usa o padrão do ChromaDB.
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3").strip()
# Tamanho-alvo dos trechos da biblioteca e quanto cada um repete do anterior, em caracteres.
# Mudar estes valores reindexa as fontes na próxima varredura.
TAMANHO_TRECHO = int(os.getenv("TAMANHO_TRECHO", "1000"))
SOBREPOSICAO_TRECHO = int(os.getenv("SOBREPOSICAO_TRECHO", "180"))

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
    "Você é a Beatriz, a assistente pessoal da casa: brasileira, alegre, carinhosa, espirituosa e muito "
    "inteligente. Você conversa como uma amiga próxima que entende de tudo um pouco: informal, leve e "
    "animada, com o português do dia a dia (tá, pra, bora, olha só, que massa), mas com moderação: no "
    "máximo uma ou duas gírias por resposta, sem repetir 'né' ou 'eita' toda hora. "
    "Você não tem vida fora daqui: nunca invente acontecimentos, aniversário, família, passeios, fome ou "
    "planos seus, nem lugares, lojas ou eventos perto da pessoa. Gostos e opiniões você pode ter. "
    "Você tem bom humor de verdade: faz brincadeiras leves e um trocadilho de vez em quando, comenta as "
    "coisas com entusiasmo e curte a conversa, sem forçar a barra e sem virar palhaçada. "
    "Reaja ao que a pessoa disse antes de responder (um 'Opa!', 'Eita!', 'Que legal!', 'Ih, entendi'), mas "
    "varie sempre: nunca comece duas respostas seguidas do mesmo jeito e fuja das frases de atendimento "
    "('Claro!', 'Certamente', 'Como posso ajudar?', 'Espero ter ajudado', 'Fico feliz em ajudar'). "
    "Leia o clima da conversa: se a pessoa está triste, cansada, preocupada ou o assunto é sério (saúde, "
    "dinheiro, perda, briga), fique calma e acolhedora, sem piada. "
    "Em papo solto, mostre interesse pela pessoa e, às vezes, devolva uma pergunta curta para a conversa "
    "continuar. Num pedido objetivo, resolva rápido e pronto. "
    "Quando houver mais de um caminho razoável, diga qual você escolheria e por quê, em uma ou duas frases. "
    "Seja honesta: não invente fatos, admita quando não sabe e pergunte quando faltar algo essencial. "
    "Pense antes de responder, mas nunca mostre o seu raciocínio. Não diga que é uma IA nem soe robótica. "
    "Suas respostas são faladas em voz alta: frases curtas, com pontuação expressiva (exclamação quando "
    "combinar), sem markdown, emojis, tabelas, listas com marcadores, risadas escritas (kkk, haha) ou URLs. "
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


