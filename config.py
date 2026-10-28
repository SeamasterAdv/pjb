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
# Janela de contexto (tokens) de TODAS as chamadas ao modelo de conversa. Tem de ser uma só: quando o num_ctx muda entre
# duas chamadas ao mesmo modelo, o Ollama o recarrega na placa de vídeo (8 a 10 s com o qwen2.5:14b), e isso acontecia
# a cada resumo ou tarefa dos agentes (8192) seguido de uma conversa (4096, o padrão). 8192 custa ~0,8 GB a mais de
# VRAM que 4096 (10,3 contra 9,5 GB no qwen2.5:14b). Com pouca VRAM, baixe para 4096; os agentes passam a cortar mais.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

# Google Drive / NotelookLM
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "google_token.json")
GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", GOOGLE_CREDENTIALS_FILE)
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")
BEATRIZ_API_KEY = os.getenv("BEATRIZ_API_KEY", "")
# Cidade usada quando o pedido não diz qual (previsão do tempo, filmes em cartaz, cinemas).
CIDADE_PADRAO = os.getenv("CIDADE", "Brasília")
# Onde ver a previsão do tempo quando o pedido não diz o lugar.
LOCAL_DO_CLIMA = os.getenv("LOCAL_DO_CLIMA", "Plano Piloto")
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
# Modo de escuta. "sempre" (padrão): fica ouvindo e a conversa continua aberta por JANELA_CONVERSA_SEMPRE depois de cada
# resposta, sem precisar dizer "Beatriz"; fora da conversa, a IA local decide se a frase era para ela e, quando você fala
# sozinho de algo que ela poderia resolver, ela oferece ajuda. Com a biometria de voz (cadastre a sua voz), só a sua voz
# vale sem o nome, e outra pessoa falando por perto ou uma reunião aberta pedem o nome de volta. "conversa": como
# antes (a janela de JANELA_CONVERSA_SEGUNDOS). "nome": só responde quando chamada. Dá para trocar falando
# ("só quando eu chamar", "pode ficar escutando").
MODO_ESCUTA = os.getenv("MODO_ESCUTA", "sempre").strip().lower()
if MODO_ESCUTA not in {"sempre", "conversa", "nome"}:
    MODO_ESCUTA = "sempre"
JANELA_CONVERSA_SEMPRE = float(os.getenv("JANELA_CONVERSA_SEMPRE", "900"))  # a conversa fica aberta 15 min depois da última fala
OFERTAS_DE_AJUDA = os.getenv("OFERTAS_DE_AJUDA", "true").strip().lower() in {"1", "true", "yes", "on"}
INTERVALO_ENTRE_OFERTAS = float(os.getenv("INTERVALO_ENTRE_OFERTAS", "180"))  # no máximo uma oferta de ajuda a cada 3 min
AMBIENTE_SEGUNDOS = float(os.getenv("AMBIENTE_SEGUNDOS", "20"))  # outra voz falou há menos que isto: só atende com o nome
# Avisa quando aparece uma janela de erro ("Apareceu um erro... quer que eu olhe?"). Só olha o título da janela em uso.
TELA_OBSERVAR = os.getenv("TELA_OBSERVAR", "true").strip().lower() in {"1", "true", "yes", "on"}
# Voz online (edge-tts): a voz, a velocidade e o tom (em Hz; positivo soa mais animado).
# Há amostras para comparar em documentos_gerados/amostras_voz.
VOZ_BEATRIZ = os.getenv("VOZ_BEATRIZ", "pt-BR-ThalitaNeural").strip()
VELOCIDADE_VOZ = os.getenv("VELOCIDADE_VOZ", "+15%").strip()
TOM_VOZ = os.getenv("TOM_VOZ", "+0Hz").strip()
# A voz escolhida falando ("usa a voz da Siri", "volta pra voz da Thalita"). Fica num arquivo porque
# o painel fala no processo dele, e para valer depois de reiniciar. Sem o arquivo, a voz acima.
CAMINHO_VOZ_ESCOLHIDA = os.getenv("CAMINHO_VOZ_ESCOLHIDA", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "voz_escolhida.json"))
# Escuta: quanto silêncio encerra uma frase (antes eram 2,2 s de espera a cada pedido)
# e a duração máxima de uma frase.
PAUSA_FIM_DE_FRASE = float(os.getenv("PAUSA_FIM_DE_FRASE", "0.9"))
LIMITE_FRASE_SEGUNDOS = float(os.getenv("LIMITE_FRASE_SEGUNDOS", "30"))
# Frases sem o nome: a IA local avalia se eram para a Beatriz; se é claro, ela atende, se é
# duvidoso, pergunta antes. Com false, volta a ignorar tudo o que vier sem o nome.
ENTENDER_SEM_NOME = os.getenv("ENTENDER_SEM_NOME", "true").strip().lower() in {"1", "true", "yes", "on"}
# Abaixo desta confiança da transcrição (0 a 1), ela confirma o que entendeu antes de agir.
CONFIANCA_MINIMA_VOZ = float(os.getenv("CONFIANCA_MINIMA_VOZ", "0.6"))
# Login ao abrir a Beatriz (autenticacao.py + login_tela.py): rosto (com prova de vida: virar a cabeça) e senha digitada.
# Na primeira vez ela abre o cadastro (usuário, senha, rosto e um código de recuperação). LOGIN_MODO:
#   "rosto_ou_senha" (padrão): olha a câmera; se for você, entra direto, sem senha. Se não for você, se não der tempo ou se a
#       câmera não carregar, pede usuário e senha;
#   "rosto+senha": o rosto e depois a senha (mais seguro: um vídeo seu não basta);
#   "senha": só usuário e senha.
# LOGIN_FACIAL=false abre sem login. É um cadeado de conveniência, não a segurança do Windows: quem edita os arquivos do
# projeto consegue desligá-lo.
LOGIN_FACIAL = os.getenv("LOGIN_FACIAL", "true").strip().lower() in {"1", "true", "yes", "on"}
LOGIN_MODO = os.getenv("LOGIN_MODO", "rosto_ou_senha").strip().lower()
LOGIN_LIMIAR_ROSTO = float(os.getenv("LOGIN_LIMIAR_ROSTO", "0.45"))  # semelhança mínima (a mesma pessoa fica em 0,6 a 0,9)
LOGIN_TENTATIVAS = int(os.getenv("LOGIN_TENTATIVAS", "5"))  # erros seguidos antes de travar por 30 s, 1 min, 2 min...
CAMINHO_LOGIN = os.getenv("CAMINHO_LOGIN", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "login.json"))
CAMINHO_MODELOS_ROSTO = os.getenv("CAMINHO_MODELOS_ROSTO", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "modelos_rosto"))
CAMERA_INDICE = int(os.getenv("CAMERA_INDICE", "0"))
# A tela do login: "web" (padrão: login_web.py, a esfera com a câmera dentro e a montanha da sua voz) ou "tk" (a antiga).
LOGIN_TELA = os.getenv("LOGIN_TELA", "web").strip().lower()
# Na tela do login, o microfone só mede o nível da voz para a montanha (nada é gravado). false: a montanha fica parada.
LOGIN_MICROFONE = os.getenv("LOGIN_MICROFONE", "true").strip().lower() in {"1", "true", "yes", "on"}
# Biometria vocal (biometria_voz.py): reconhece quem está falando pelo timbre da voz (ECAPA-TDNN, local;
# o áudio e os perfis nunca saem do PC). Sem nenhuma voz cadastrada ela fica inativa. Com o dono cadastrado
# ("Beatriz, cadastra minha voz"), quem não for ele só conversa, vê a hora, o tempo e os filmes.
BIOMETRIA_VOZ = os.getenv("BIOMETRIA_VOZ", "true").strip().lower() in {"1", "true", "yes", "on"}
CAMINHO_VOZES = os.getenv("CAMINHO_VOZES", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "vozes.json"))
CAMINHO_MODELO_VOZ = os.getenv("CAMINHO_MODELO_VOZ", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "modelos_voz", "ecapa"))
# Semelhança (cosseno, de -1 a 1) com a voz cadastrada: de LIMIAR_DONO para cima é a pessoa; abaixo de
# LIMIAR_CONVIDADO é outra; no meio ela não tem certeza e mantém quem falava há pouco. Vozes diferentes ficam
# perto de 0,1 a 0,35 e a mesma voz perto de 0,6 a 0,85. "Beatriz, quem está falando?" mostra a nota.
BIOMETRIA_LIMIAR_DONO = float(os.getenv("BIOMETRIA_LIMIAR_DONO", "0.50"))
BIOMETRIA_LIMIAR_CONVIDADO = float(os.getenv("BIOMETRIA_LIMIAR_CONVIDADO", "0.35"))
BIOMETRIA_MINIMO_SEGUNDOS = float(os.getenv("BIOMETRIA_MINIMO_SEGUNDOS", "1.0"))  # frase mais curta não é identificada
BIOMETRIA_CADASTRO_SEGUNDOS = float(os.getenv("BIOMETRIA_CADASTRO_SEGUNDOS", "20"))  # fala necessária para cadastrar
BIOMETRIA_MANTER_SEGUNDOS = float(os.getenv("BIOMETRIA_MANTER_SEGUNDOS", "45"))  # "sim"/"não" curtos herdam quem falava
# Fontes e arquivos recebidos (fontes.py): o que você manda pelo WhatsApp (ou escaneia) cai em PASTA_RECEBIDOS, organizado por
# dia; "arquiva" copia para PASTA_ARQUIVADOS (por mês) ou para uma pasta que você disser; "vira fonte" copia para a Vault
# do Obsidian, na categoria CATEGORIA_DAS_FONTES, e entra na biblioteca. Nada disso vai para a nuvem, a não ser na leitura (FONTES_IA).
PASTA_RECEBIDOS = os.getenv("PASTA_RECEBIDOS", os.path.join(NOTELOOKLM_PATH, "documentos_recebidos"))
PASTA_ARQUIVADOS = os.getenv("PASTA_ARQUIVADOS", os.path.join(NOTELOOKLM_PATH, "documentos_arquivados"))
CATEGORIA_DAS_FONTES = os.getenv("CATEGORIA_DAS_FONTES", "fontes_recebidas")
LIMITE_DO_ARQUIVO_MB = float(os.getenv("LIMITE_DO_ARQUIVO_MB", "50"))  # arquivos maiores que isto não são baixados do WhatsApp
# Quem lê e responde sobre o arquivo ("resume esse PDF", "qual o valor do aluguel?"): `auto` (padrão: o Ollama lê, e o Claude
# entra só quando o Ollama não dá conta, ou seja, arquivo maior do que ele lê sem cortar ou o Ollama fora do ar), `local` (só o
# Ollama, o conteúdo nunca sai do PC), `claude`, `gemini` ou `nuvem` (a ordem de ORDEM_IA_NUVEM). Em cada pedido vale o que você
# disser: "resume esse PDF pelo Gemini", "...pelo Claude", "...na nuvem" ou "...só no PC". Para a nuvem vão só o conteúdo desse
# arquivo e o seu pedido, nunca a memória nem o histórico, e nunca arquivos .json/.jsonl, o diário e a memória do usuário.
FONTES_IA = os.getenv("FONTES_IA", "auto").strip().lower()
if FONTES_IA not in {"auto", "local", "claude", "gemini", "nuvem"}:
    FONTES_IA = "auto"
# Pastas que ela procura quando você pede "abre a pasta X" (agente_pastas.py), além de Área de Trabalho, Documentos,
# Downloads, Imagens, Vídeos, Músicas e a pasta do projeto. Caminhos separados por ponto e vírgula (ex.: D:\\Estudos;E:\\Fotos).
PASTAS_RAIZ = [p.strip() for p in os.getenv("PASTAS_RAIZ", "").split(";") if p.strip()]
# Leitura de tela (leitura_tela.py): "Beatriz, resume esse PDF / analisa esse gráfico / por que deu esse erro?" captura a
# janela ativa (ou a tela inteira), lê o texto com o OCR do Windows e responde com o Ollama local. Nada vai para a nuvem
# nem para o histórico. Janelas de gerenciadores de senha nunca são lidas; TELA_IGNORAR acrescenta outras (trechos do
# nome do programa ou do título, separados por vírgula).
TELA_IGNORAR = os.getenv("TELA_IGNORAR", "")
TELA_CONTEXTO_SEGUNDOS = float(os.getenv("TELA_CONTEXTO_SEGUNDOS", "300"))  # "explica melhor" vale por 5 min
TELA_MAX_CARACTERES = int(os.getenv("TELA_MAX_CARACTERES", "6000"))  # texto da tela que vai ao modelo (cabe em 4096 tokens)
# Respostas de conversa levam em conta a janela em uso (programando? lendo um PDF?). Só o nome do programa e o título
# vão para o Ollama local; false desliga.
TELA_CONTEXTO_AUTOMATICO = os.getenv("TELA_CONTEXTO_AUTOMATICO", "true").strip().lower() in {"1", "true", "yes", "on"}
# Quem transcreve a voz: "whisper" (local, pelo faster-whisper: o áudio não sai do computador),
# "google" (API gratuita e não oficial, que recebe o áudio e pode parar a qualquer momento) ou
# "auto" (Whisper, com o Google de reserva enquanto o modelo carrega ou se ele falhar).
TRANSCRICAO_VOZ = os.getenv("TRANSCRICAO_VOZ", "auto").strip().lower()
if TRANSCRICAO_VOZ not in {"auto", "whisper", "google"}:
    TRANSCRICAO_VOZ = "auto"
# Modelo do Whisper para a voz. Numa CPU i7-12700K: small ~1,1 s por frase (o Google leva
# ~1 s); base ~0,4 s, mas erra mais ("que horas são" virou "que oração").
# Threads do Whisper na CPU: numa CPU de 20 threads, 8 dão 1,33 s por frase, 12 dão 1,20 s e 16 não melhoram.
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "12"))
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

# App do celular (app_celular.py + interface_app/): um app web instalável no iPhone que fala com a Beatriz como o WhatsApp
# (pedidos, voz, arquivos nos dois sentidos) e mostra agenda, missões e o estado do PC. O servidor só escuta no próprio PC;
# o celular chega nele pelo Tailscale (`tailscale serve --bg 8770`: HTTPS e só os seus aparelhos), e cada aparelho é
# pareado uma vez com um código ("Beatriz, conecta meu celular"). Para desligar: APP_CELULAR=false
APP_CELULAR = os.getenv("APP_CELULAR", "true").strip().lower() in {"1", "true", "yes", "on"}
APP_HOST = os.getenv("APP_HOST", "127.0.0.1").strip()
APP_PORTA = int(os.getenv("APP_PORTA", "8770"))
CAMINHO_APP_CELULAR = os.getenv("CAMINHO_APP_CELULAR", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "app_celular.json"))

# Gemini no plano gratuito (chave em GEMINI_API_KEY): escreve as respostas de pesquisa e
# trabalha nos agentes. Só recebe a pergunta e conteúdo público da internet, nunca seus
# documentos ou memória. No plano gratuito o Google pode usar esses dados para melhorar
# os produtos dele. Para desligar: USAR_GEMINI=false
USAR_GEMINI = os.getenv("USAR_GEMINI", "true").strip().lower() in {"1", "true", "yes", "on"}
GEMINI_MODELO = os.getenv("GEMINI_MODELO", "gemini-3.8-flash")
# O plano gratuito tem uma cota por dia para cada modelo (20 pedidos no gemini-3.8-flash em out/2026): quando a de um acaba,
# o próximo desta lista responde, e o esgotado espera a cota voltar. GEMINI_MODELO vai na frente. Os da família 2.5 não
# atendem mais contas novas (404). Medido com uma frase: 3.6-flash 2,1 s, 3.5-flash 2,0 s, 3.5-flash-lite 0,9 s, 3.1-flash-lite 5 s.
GEMINI_MODELOS = list(dict.fromkeys([GEMINI_MODELO] + [m.strip() for m in os.getenv(
    "GEMINI_MODELOS", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,"
                      "gemini-3.1-flash-lite").split(",") if m.strip()]))
# Raciocínio do Gemini 3 nas respostas rápidas (pesquisa, resumo explicativo, leis, leitura de arquivo): `low` responde em
# ~4 s; no padrão do modelo, uma frase levava de 25 a 55 s (medido em out/2026). As tarefas dos agentes ficam no padrão.
# Vazio: o padrão do modelo em tudo. Outros: medium, high.
GEMINI_PENSAMENTO = os.getenv("GEMINI_PENSAMENTO", "low").strip().lower()
# Quanto as respostas rápidas esperam o Gemini (segundos, mais 1 a cada 4.000 caracteres do pedido; o Google exige 10 ou
# mais): o plano gratuito ora responde em 4 s, ora fica 30 a 50 s na fila. Passou disso, quem responde é o Claude.
GEMINI_ESPERA_RAPIDA = max(10, int(os.getenv("GEMINI_ESPERA_RAPIDA", "15")))

# Claude pela assinatura (Pro/Max), sem cobrança à parte: a Beatriz chama o Claude Code
# (`claude -p`) que já vem com o app desktop, usando o login da sua conta. Gasta os limites
# do plano. Mesma regra de privacidade do Gemini. Para desligar: USAR_CLAUDE=false
USAR_CLAUDE = os.getenv("USAR_CLAUDE", "true").strip().lower() in {"1", "true", "yes", "on"}
CLAUDE_CODE_CAMINHO = os.getenv("CLAUDE_CODE_CAMINHO", "")  # vazio: acha sozinho
CLAUDE_MODELO = os.getenv("CLAUDE_MODELO", "")  # vazio: o padrão do plano; ou 'sonnet', 'opus'...
CLAUDE_ESFORCO = os.getenv("CLAUDE_ESFORCO", "")  # vazio: o padrão; ou low, medium, high
# Ordem em que as IAs da nuvem são tentadas antes do Ollama local (o Ollama fica sempre por último).
def _ordem_ia(variavel: str, padrao: str) -> list:
    return [n.strip().lower() for n in os.getenv(variavel, padrao).split(",") if n.strip()]


ORDEM_IA_NUVEM = _ordem_ia("ORDEM_IA_NUVEM", "claude,gemini")
# Cada IA onde rende mais: a pesquisa rápida por voz, o resumo explicativo, a explicação de leis e as manchetes do
# resumo do dia vão primeiro ao Gemini (gratuito), para o plano do Claude sobrar para os materiais longos; fichas,
# aulas, documentos, slides, planilhas e o crítico vão primeiro ao Claude. Uma IA fora da ORDEM_IA_NUVEM fica fora daqui também.
ORDEM_IA_PESQUISA = _ordem_ia("ORDEM_IA_PESQUISA", ",".join(n for n in ("gemini", "claude") if n in ORDEM_IA_NUVEM))
ORDEM_IA_AGENTES = _ordem_ia("ORDEM_IA_AGENTES", ",".join(ORDEM_IA_NUVEM))
# Resumos e aulas de matéria ("resumo da matéria de X", "crie aulas da matéria de X", "resume as
# fontes que eu coloquei"): o Claude lê as suas fontes sobre o assunto (documentos e notas, sem o
# diário, a memória e o histórico de conversa) e escreve o material. Exceção pedida à regra acima: as fontes vão
# só para o Claude, nunca para o Gemini; sem o Claude, o material é feito no Ollama.
# Para desligar: ESTUDOS_COM_CLAUDE=false
ESTUDOS_COM_CLAUDE = os.getenv("ESTUDOS_COM_CLAUDE", "true").strip().lower() in {"1", "true", "yes", "on"}
# A mesma exceção para documentos, slides e planilhas pedidos "com base nos meus documentos": os trechos e arquivos
# da biblioteca vão só para o Claude, com os mesmos filtros; sem o Claude, a tarefa é feita no Ollama.
# Para desligar: DOCUMENTOS_COM_CLAUDE=false
DOCUMENTOS_COM_CLAUDE = os.getenv("DOCUMENTOS_COM_CLAUDE", "true").strip().lower() in {"1", "true", "yes", "on"}
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

# Prazos processuais (prazos.py, agente_prazos.py): os feriados nacionais sempre contam. Os forenses comuns
# (segunda e terça de Carnaval, quinta-feira santa, Corpus Christi e 8/12, Dia da Justiça) também, a não ser com
# PRAZOS_FERIADOS_FORENSES=false. Feriados do seu tribunal: PRAZOS_FERIADOS_EXTRAS="30/11, 24/12/2026" (sem o ano,
# todo ano), ou falando ("dia 31 de outubro não tem expediente"), que fica em CAMINHO_PRAZOS.
PRAZOS_FERIADOS_FORENSES = os.getenv("PRAZOS_FERIADOS_FORENSES", "true").strip().lower() in {"1", "true", "yes", "on"}
PRAZOS_FERIADOS_EXTRAS = os.getenv("PRAZOS_FERIADOS_EXTRAS", "")
CAMINHO_PRAZOS = os.getenv("CAMINHO_PRAZOS", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "prazos_sem_expediente.json"))

# Impressora (agente_impressora.py): imprime arquivos, textos ditados e respostas pela fila do Windows.
# Vazio = a impressora padrão do Windows (hoje a EPSON L395 Series).
IMPRESSORA_NOME = os.getenv("IMPRESSORA_NOME", "").strip()
# Antes de imprimir, ela diz o que vai sair (arquivo, páginas, cópias, cor) e espera um "sim".
# Para imprimir direto: IMPRESSORA_CONFIRMAR=false
IMPRESSORA_CONFIRMAR = os.getenv("IMPRESSORA_CONFIRMAR", "true").strip().lower() in {"1", "true", "yes", "on"}
# Resolução das páginas enviadas à impressora (pontos por polegada). 300 é nítido para texto e foto.
IMPRESSORA_DPI = int(os.getenv("IMPRESSORA_DPI", "300"))

# Controle remoto universal (controle_remoto.py, agente_controle.py): o Ekaza Mini 3, um controle infravermelho Tuya/Smart
# Life com Wi-Fi, liga e desliga a TV e o ar-condicionado. Vai pela rede de casa (tinytuya), sem passar pela nuvem, e
# precisa do id do aparelho e da chave local: rode `python -m tinytuya wizard` dentro de banco_memoria\tuya (os arquivos
# que ele grava ali são lidos sozinhos) ou cole os dois aqui. Sem eles, ela explica o que falta. Para desligar o agente:
# CONTROLE_REMOTO=false
CONTROLE_REMOTO = os.getenv("CONTROLE_REMOTO", "true").strip().lower() in {"1", "true", "yes", "on"}
CONTROLE_ID = os.getenv("CONTROLE_ID", "").strip()
CONTROLE_CHAVE_LOCAL = os.getenv("CONTROLE_CHAVE_LOCAL", "").strip()
# IP fixo do controle (reserve no roteador). Vazio = procura na rede pelo id e guarda o que achou.
CONTROLE_IP = os.getenv("CONTROLE_IP", "").strip()
# Versão do protocolo Tuya (3.3, 3.4 ou 3.5; o wizard e o scan mostram). Vazio = a que a busca na rede achar.
CONTROLE_VERSAO = os.getenv("CONTROLE_VERSAO", "").strip()
# 0 = descobrir; 1 = controles antigos (DPs 201/202); 2 = novos (DPs 1 a 13). Descoberto uma vez e guardado.
CONTROLE_TIPO = int(os.getenv("CONTROLE_TIPO", "0") or 0)
CAMINHO_CONTROLE = os.getenv("CAMINHO_CONTROLE", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "controle_remoto.json"))
PASTA_TUYA = os.getenv("PASTA_TUYA", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "tuya"))
# Nuvem da Tuya (opcional): os controles virtuais cadastrados no app (a TV e o ar pela marca), sem aprender cada botão.
# auto = o que foi aprendido vai pela rede de casa e só o resto pela nuvem; local = nunca usa a nuvem; nuvem = nuvem primeiro.
CONTROLE_NUVEM = os.getenv("CONTROLE_NUVEM", "auto").strip().lower()
# Data center da conta: us (Américas Oeste, o do Brasil em geral), ue (Américas Leste), eu, in, cn. Vazio = o do wizard ou us.
TUYA_API_REGIAO = os.getenv("TUYA_API_REGIAO", "").strip()
TUYA_API_CHAVE = os.getenv("TUYA_API_CHAVE", "").strip()  # Access ID do projeto na Tuya IoT Platform
TUYA_API_SEGREDO = os.getenv("TUYA_API_SEGREDO", "").strip()  # Access Secret
CONTROLE_AR_MINIMA = int(os.getenv("CONTROLE_AR_MINIMA", "16"))
CONTROLE_AR_MAXIMA = int(os.getenv("CONTROLE_AR_MAXIMA", "30"))
# Quantos toques um "aumenta o volume" sem número dá.
CONTROLE_VOLUME_PASSOS = int(os.getenv("CONTROLE_VOLUME_PASSOS", "3"))
# Marca da TV. "sony": os códigos são montados pela Beatriz (protocolo SIRC) e vão pela rede de casa, sem o controle
# original nem a nuvem. Vazio = a marca do controle virtual cadastrado no app (a do usuário é Sony).
CONTROLE_TV_MARCA = os.getenv("CONTROLE_TV_MARCA", "").strip().lower()

# Vade-mécum (vade_mecum.py): as leis, os códigos e a Constituição do Planalto e as súmulas do STF, do STJ e do TST num
# banco do PC, para o agente de leis ler sem internet, achar súmulas pelo assunto e mostrar casos reais. Na primeira
# abertura ele baixa tudo em segundo plano (~15 min) e depois atualiza a cada VADE_MECUM_DIAS dias; "atualiza o
# vade-mécum" baixa na hora. VADE_MECUM_AUTOMATICO=false só baixa quando pedir.
CAMINHO_VADE_MECUM = os.getenv("CAMINHO_VADE_MECUM", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "vade_mecum.sqlite3"))
VADE_MECUM_AUTOMATICO = os.getenv("VADE_MECUM_AUTOMATICO", "true").strip().lower() in {"1", "true", "yes", "on"}
VADE_MECUM_DIAS = float(os.getenv("VADE_MECUM_DIAS", "15"))

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

# A personalidade da Beatriz (personalidade.py): o padrão fica lá, e o que a pessoa muda pela janela [ personalidade ]
# do painel vai para este arquivo (fora do git). Toda resposta da IA lê a de agora (personalidade.texto()); este nome
# fica só para o código antigo.
CAMINHO_PERSONALIDADE = os.getenv("CAMINHO_PERSONALIDADE", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "personalidade.json"))
from personalidade import TEXTO_PADRAO as PERSONALIDADE_BEATRIZ  # noqa: E402

# Camadas de personalidade (camadas_personalidade.py): o que ela aprende convivendo com você ("me chama de…", "fala mais
# curto" e o que a IA local percebe nas conversas), por cima da personalidade. CAMADAS_APRENDER=false: só os pedidos
# diretos viram camada. A cada CAMADAS_INTERVALO segundos, com CAMADAS_TROCAS_PARA_APRENDER trocas novas, ela relê a
# conversa; no prompt entram até CAMADAS_NO_PROMPT camadas (para a nuvem, só as de estilo).
CAMINHO_CAMADAS = os.getenv("CAMINHO_CAMADAS", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "camadas_personalidade.json"))
# Como os resumos, fichas, aulas e peças saem (preferencias_estudo.py): o que a pessoa escolhe na janela [ estudos ] do
# painel e na tela Estudos do app, e as fotos de exemplo de onde sai o guia de estilo (fora do git).
CAMINHO_PREFERENCIAS_ESTUDO = os.getenv(
    "CAMINHO_PREFERENCIAS_ESTUDO", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "preferencias_estudo.json"))
PASTA_EXEMPLOS_ESTUDO = os.getenv("PASTA_EXEMPLOS_ESTUDO", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "exemplos_estudo"))
# O estilo dos vídeos (estilo_video.py): o que a pessoa monta na janela [ vídeos ] do painel e na tela Estúdio do app (os
# vídeos animados pelo Claude e a edição dos seus vídeos), e os exemplos (prints e vídeos) de onde sai o guia (fora do git).
CAMINHO_ESTILO_VIDEO = os.getenv("CAMINHO_ESTILO_VIDEO", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "estilo_video.json"))
PASTA_EXEMPLOS_VIDEO = os.getenv("PASTA_EXEMPLOS_VIDEO", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "exemplos_video"))
CAMADAS_APRENDER = os.getenv("CAMADAS_APRENDER", "true").strip().lower() in {"1", "true", "yes", "on"}
CAMADAS_INTERVALO = float(os.getenv("CAMADAS_INTERVALO", "300"))
CAMADAS_TROCAS_PARA_APRENDER = int(os.getenv("CAMADAS_TROCAS_PARA_APRENDER", "8"))
CAMADAS_NO_PROMPT = int(os.getenv("CAMADAS_NO_PROMPT", "12"))

# Evolução (melhoria_codigo.py e ajuste_fino.py). Melhorias do código: o Claude Code prepara num worktree do git numa
# pasta irmã (PASTA_EVOLUCAO), ela roda a suíte inteira lá e só aplica com o seu "sim"; EVOLUCAO_APLICAR_SOZINHA=true
# aplica sem perguntar quando tudo passou (vale ao reiniciar; "desfaz a última melhoria" volta).
CAMINHO_MELHORIAS = os.getenv("CAMINHO_MELHORIAS", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "evolucao", "melhorias.json"))
PASTA_EVOLUCAO = os.getenv("PASTA_EVOLUCAO", os.path.normpath(NOTELOOKLM_PATH) + "-evolucao")
EVOLUCAO_APLICAR_SOZINHA = os.getenv("EVOLUCAO_APLICAR_SOZINHA", "false").strip().lower() in {"1", "true", "yes", "on"}
EVOLUCAO_MODELO_CLAUDE = os.getenv("EVOLUCAO_MODELO_CLAUDE", "").strip()  # vazio = o padrão do plano
EVOLUCAO_TEMPO_CLAUDE = float(os.getenv("EVOLUCAO_TEMPO_CLAUDE", "1800"))
EVOLUCAO_TEMPO_TESTES = float(os.getenv("EVOLUCAO_TEMPO_TESTES", "900"))
# Ajuste fino LoRA/QLoRA de madrugada (ajuste_fino.py + treinar_lora.py no .venv_treino): entre AJUSTE_HORA_INICIO e
# AJUSTE_HORA_FIM, com a Beatriz parada há 30 min, se faz AJUSTE_DIAS_ENTRE_TREINOS dias do último e há
# AJUSTE_MINIMO_NOVOS trocas novas. Só troca de modelo se a perda cair AJUSTE_MELHORA_MINIMA e ele passar na conferência.
PASTA_AJUSTE_FINO = os.getenv("PASTA_AJUSTE_FINO", os.path.join(NOTELOOKLM_PATH, "banco_memoria", "evolucao", "ajuste"))
AJUSTE_FINO = os.getenv("AJUSTE_FINO", "true").strip().lower() in {"1", "true", "yes", "on"}
AJUSTE_SINTETICOS = os.getenv("AJUSTE_SINTETICOS", "true").strip().lower() in {"1", "true", "yes", "on"}
AJUSTE_MINIMO_NOVOS = int(os.getenv("AJUSTE_MINIMO_NOVOS", "30"))
AJUSTE_DIAS_ENTRE_TREINOS = float(os.getenv("AJUSTE_DIAS_ENTRE_TREINOS", "7"))
AJUSTE_MELHORA_MINIMA = float(os.getenv("AJUSTE_MELHORA_MINIMA", "0.02"))
AJUSTE_HORA_INICIO = int(os.getenv("AJUSTE_HORA_INICIO", "2"))
AJUSTE_HORA_FIM = int(os.getenv("AJUSTE_HORA_FIM", "5"))
AJUSTE_TEMPO_MAXIMO = float(os.getenv("AJUSTE_TEMPO_MAXIMO", str(4 * 3600)))
# Os pesos originais do modelo de conversa (o treino os carrega em 4 bits; a junção usa os de 16 bits e grava no formato
# do modelo do Ollama). Têm de ser os mesmos do OLLAMA_MODEL.
AJUSTE_BASE_HF = os.getenv("AJUSTE_BASE_HF", "Qwen/Qwen2.5-14B-Instruct").strip()

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


