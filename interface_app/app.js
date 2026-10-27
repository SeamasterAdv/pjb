// App da Beatriz no celular. Conversa com o PC pelo servidor do app_celular.py (pelo Tailscale, em HTTPS).
// O visual em rede (a esfera e a montanha) é do visual_app.js, que segue as classes de estado do <body> e é avisado
// aqui (window.visualApp) da tela aberta, da resposta que vai tocar e da gravação.
// Cada chamada leva a chave deste aparelho (ganha no pareamento). O texto que vem do PC entra sempre como texto
// (textContent), nunca como HTML.
"use strict";

const CHAVE = "beatriz.chave";
const OPCOES = { som: "beatriz.som", sons: "beatriz.sons", abertura: "beatriz.abertura" };
const LIMITE_ARQUIVO_MB = 50;
const SILENCIO = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
const SECOES_DO_RESUMO = {
  "o que e": "O que é", "contexto": "Contexto", "pontos principais": "Pontos principais", "por que importa": "Por que importa",
  "exemplo": "Exemplo", "novidades": "Novidades", "para lembrar": "Para lembrar",
};

const estado = {
  chave: ler(CHAVE),
  ultimo: 0, // id da última mensagem recebida
  historico: true, // a primeira leva de mensagens é o histórico: não fala, não toca som, não avisa
  mensagens: [], // as que chegaram nesta sessão (para o "último recado")
  buscando: false,
  online: null,
  semana: 0,
  tela: "inicio",
  painel: null,
  falarAqui: ler(OPCOES.som) === "1",
  sons: ler(OPCOES.sons) !== "0",
  abertura: ler(OPCOES.abertura) !== "0",
  filaDeFala: [],
  falando: false,
};
const $ = (id) => document.getElementById(id);

function ler(nome) { try { return localStorage.getItem(nome) || ""; } catch { return ""; } }
function gravar(nome, valor) { try { valor ? localStorage.setItem(nome, valor) : localStorage.removeItem(nome); } catch { /* modo privado */ } }
const esperar = (ms) => new Promise((pronto) => setTimeout(pronto, ms));

// ------------------------------------------------------------------ servidor
async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, { ...opcoes, headers: { ...(opcoes.headers || {}), "X-Chave-Beatriz": estado.chave } });
  if (resposta.status === 401) {
    esquecerChave();
    throw new Error("nao_pareado");
  }
  if (!resposta.ok) {
    let erro = "Algo deu errado no PC.";
    try { erro = (await resposta.json()).erro || erro; } catch { /* sem corpo */ }
    throw new Error(erro);
  }
  return resposta;
}

function esquecerChave() {
  estado.chave = "";
  gravar(CHAVE, "");
  mostrarTela("parear");
}

function marcarOnline(online) {
  if (estado.online === online) return;
  estado.online = online;
  $("ind-pc").classList.toggle("ok", online);
  $("ind-pc").classList.toggle("falha", !online);
  document.body.classList.toggle("offline", !online);
  $("estado-pc").classList.toggle("ok", online);
  $("estado-pc").classList.toggle("falha", !online);
  if (!online) {
    $("estado-pc-texto").textContent = "PC fora do alcance · confira o Tailscale";
    if (!estado.historico) som("erro");
  }
}

// ------------------------------------------------------------------ estado da Beatriz (o núcleo reage)
// Missão concluída: o que é branco fica dourado por um tempo (o estilo e a esfera seguem o data-vitoria).
let fimDoDourado = null;
function celebrar() {
  document.body.dataset.vitoria = "1";
  clearTimeout(fimDoDourado);
  fimDoDourado = setTimeout(() => { delete document.body.dataset.vitoria; }, 5000);
}

function definirEstado(nome) {
  for (const classe of ["pensando", "falando", "ouvindo"]) document.body.classList.toggle(classe, classe === nome);
  $("conversa-estado").textContent =
    { pensando: "pensando…", falando: "falando…", ouvindo: "ouvindo você…" }[nome] || (estado.online === false ? "sem conexão com o PC" : "pronta");
}

// ------------------------------------------------------------------ sons da interface (gerados na hora, sem arquivos)
let audio = null;
function contextoDeSom() {
  if (!audio) {
    const Contexto = window.AudioContext || window.webkitAudioContext;
    if (!Contexto) return null;
    audio = new Contexto();
  }
  if (audio.state === "suspended") audio.resume().catch(() => {});
  return audio;
}

function som(tipo) {
  if (!estado.sons || !audio || audio.state !== "running") return;
  const notas = {
    toque: [[1500, 0, 0.035, "sine", 0.035]],
    abrir: [[740, 0, 0.05, "sine", 0.04], [1110, 0.05, 0.07, "sine", 0.04]],
    enviar: [[520, 0, 0.12, "triangle", 0.05, 1500]],
    chegou: [[880, 0, 0.22, "sine", 0.05], [1320, 0.09, 0.3, "sine", 0.04]],
    gravar: [[440, 0, 0.09, "sine", 0.06], [660, 0.08, 0.09, "sine", 0.05]],
    parar: [[660, 0, 0.08, "sine", 0.05], [440, 0.07, 0.1, "sine", 0.05]],
    erro: [[240, 0, 0.16, "square", 0.025]],
    ligando: [[180, 0, 0.7, "sine", 0.05, 900], [1200, 0.55, 0.4, "sine", 0.03]],
  }[tipo];
  const agora = audio.currentTime;
  for (const [frequencia, inicio, duracao, forma, volume, ate] of notas || []) {
    const oscilador = audio.createOscillator();
    const ganho = audio.createGain();
    oscilador.type = forma;
    oscilador.frequency.setValueAtTime(frequencia, agora + inicio);
    if (ate) oscilador.frequency.exponentialRampToValueAtTime(ate, agora + inicio + duracao);
    ganho.gain.setValueAtTime(0.0001, agora + inicio);
    ganho.gain.exponentialRampToValueAtTime(volume, agora + inicio + 0.01);
    ganho.gain.exponentialRampToValueAtTime(0.0001, agora + inicio + duracao);
    oscilador.connect(ganho).connect(audio.destination);
    oscilador.start(agora + inicio);
    oscilador.stop(agora + inicio + duracao + 0.02);
  }
}

// ------------------------------------------------------------------ telas
function mostrarTela(nome) {
  if (nome === estado.tela && nome !== "parear") return;
  estado.tela = nome;
  if (window.visualApp) window.visualApp.tela(nome);
  document.querySelectorAll(".tela").forEach((t) => t.classList.toggle("ativa", t.id === `tela-${nome}`));
  document.querySelectorAll(".doca-botao[data-tela]").forEach((b) => b.classList.toggle("ativa", b.dataset.tela === nome));
  document.querySelector(".doca").hidden = nome === "parear";
  if (nome === "agenda") carregarAgenda();
  if (nome === "controle") {
    controle.alvo = null;
    carregarControle();
  }
  if (["inicio", "missoes", "sistema"].includes(nome)) carregarPainel();
  if (nome === "inicio") carregarControle();
  acompanharAtividade(nome === "sistema");
  clearInterval(biblio.relogio);
  if (nome === "biblioteca") {
    carregarBiblioteca();
    biblio.relogio = setInterval(() => { if (!document.hidden && $("folha").hidden) carregarBiblioteca(); }, 8000); // "ainda não lida" -> "lida"
  }
  if (nome === "conversa") {
    $("ponto-conversa").hidden = true;
    rolarParaBaixo();
  }
  if (nome === "estudos" && window.JanelaDeEstudos) {
    window.JanelaDeEstudos.usar(() => estudosDoApp);
    window.JanelaDeEstudos.carregar();
  }
}

// ------------------------------------------------------------------ estudos
// A tela Estudos usa o componente da janela [ estudos ] do painel (/visual/estudos.js); aqui só a ponte com o servidor.
function escolherArquivos(aceita, multiplos = true) {
  return new Promise((pronto) => {
    const entrada = document.createElement("input");
    entrada.type = "file";
    entrada.accept = aceita;
    entrada.multiple = multiplos;
    entrada.addEventListener("change", () => pronto([...entrada.files]));
    entrada.click();  // ainda dentro do toque: o iPhone só abre a escolha assim
  });
}

async function jsonDe(caminho, opcoes = {}) {
  return (await api(caminho, opcoes)).json();
}

function postarJson(caminho, dados) {
  return jsonDe(caminho, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dados || {}) });
}

const estudosDoApp = {
  estudos: () => jsonDe("/api/estudos"),
  estudos_salvar: (dados) => postarJson("/api/estudos", dados),
  estudos_padrao: () => postarJson("/api/estudos/padrao"),
  async estudos_fotos_adicionar() {
    const fotos = await escolherArquivos("image/*");
    let ultima = { ok: true, mensagem: "", dados: await jsonDe("/api/estudos") };
    for (const foto of fotos) {
      ultima = await jsonDe("/api/estudos/foto", { method: "POST", body: foto, headers: { "X-Nome": encodeURIComponent(foto.name) } });
      if (!ultima.ok) break;
    }
    return ultima;
  },
  estudos_foto: async (id) => (await jsonDe(`/api/estudos/foto/${encodeURIComponent(id)}`)).src,
  estudos_foto_apagar: (id) => postarJson(`/api/estudos/foto/${encodeURIComponent(id)}/apagar`),
  estudos_guia: () => postarJson("/api/estudos/guia"),
  async estudos_peca_arquivos() {
    const arquivos = await escolherArquivos("image/*,application/pdf,.doc,.docx,.txt");
    estudosDoApp._arquivos = arquivos;
    return arquivos.map((a) => a.name);
  },
  async estudos_peca(tipo, fatos) {
    const ids = [];
    for (const arquivo of estudosDoApp._arquivos || []) {
      const resposta = await jsonDe("/api/estudos/peca-arquivo", {
        method: "POST", body: arquivo, headers: { "X-Nome": encodeURIComponent(arquivo.name) } });
      ids.push(resposta.id);
    }
    estudosDoApp._arquivos = [];
    const resposta = await postarJson("/api/estudos/peca", { tipo, fatos, arquivos: ids });
    if (resposta.ok) aviso("A peça entrou na fila; o resultado chega na conversa.");
    return resposta;
  },
  _arquivos: [],
};

const ATALHOS = {
  agenda: () => mostrarTela("agenda"),
  missoes: () => mostrarTela("missoes"),
  biblioteca: () => mostrarTela("biblioteca"),
  controle: () => mostrarTela("controle"),
  arquivos: () => $("escolher-arquivo").click(),
  foto: () => $("tirar-foto").click(),
  estudos: () => mostrarTela("estudos"),
  impressora: () => {
    sugerir(["Imprime minha agenda de hoje", "Imprime o último PDF", "Cancela a impressão"]);
    enviarPedido("A impressora está ligada?");
  },
  leis: () => sugerir(["Me explica o artigo 5º da Constituição", "O que diz o artigo 186 do Código Civil?",
                       "Do que se trata a LGPD?"], "Me explica o artigo "),
  clima: () => enviarPedido("Como está o tempo hoje?"),
  cinema: () => enviarPedido("Quais filmes estão em cartaz?"),
  resumo: () => enviarPedido("Resumo do dia"),
};

function sugerir(frases, comeco = "") {
  const caixa = $("sugestoes");
  caixa.textContent = "";
  for (const frase of frases) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "sugestao";
    botao.textContent = frase;
    botao.addEventListener("click", () => enviarPedido(frase));
    caixa.appendChild(botao);
  }
  mostrarTela("conversa");
  if (comeco) $("campo").value = comeco;
}

// ------------------------------------------------------------------ mensagens
async function lacoDeMensagens() {
  if (estado.buscando || !estado.chave) return;
  estado.buscando = true;
  while (estado.chave) {
    try {
      const espera = document.hidden || estado.historico ? 0 : 25;
      const resposta = await api(`/api/mensagens?depois=${estado.ultimo}&espera=${espera}`);
      marcarOnline(true);
      const { mensagens } = await resposta.json();
      mensagens.forEach(receber);
      if (estado.historico) {
        estado.historico = false;
        mostrarRecado();
      }
      if (document.hidden) break; // volta quando o app aparecer de novo
    } catch (erro) {
      if (erro.message === "nao_pareado") break;
      marcarOnline(false);
      await esperar(3000);
    }
  }
  estado.buscando = false;
}

function receber(mensagem) {
  if (mensagem.id <= estado.ultimo) return;
  estado.ultimo = mensagem.id;
  estado.mensagens.push(mensagem);
  const daBeatriz = mensagem.tipo !== "voce";
  tirarPensando();
  $("mensagens").appendChild(caixaDaMensagem(mensagem));
  if (!daBeatriz) {
    if (!estado.historico) mostrarPensando();
  } else if (!estado.historico) {
    som("chegou");
    if (estado.falarAqui && mensagem.texto) falar(mensagem.id);
    if (estado.tela !== "conversa") {
      $("ponto-conversa").hidden = false;
      if (mensagem.texto) aviso(resumir(mensagem.texto), false, () => mostrarTela("conversa"));
    }
    mostrarRecado();
    setTimeout(carregarPainel, 1500); // a resposta pode ter mexido na agenda ou nas missões
  }
  rolarParaBaixo();
}

function caixaDaMensagem(m) {
  if (m.tipo === "cartao") return caixaDoCartao(m);
  const daBeatriz = m.tipo !== "voce";
  const caixa = document.createElement("div");
  caixa.className = `msg ${daBeatriz ? "holo msg-beatriz" : "msg-voce"}${m.tipo === "lembrete" ? " msg-lembrete" : ""}`;
  const cabeca = document.createElement("div");
  cabeca.className = "msg-cabeca";
  const quem = m.tipo === "lembrete" ? "Lembrete" : daBeatriz ? "Beatriz" : m.por_voz ? "Você · voz" : "Você";
  cabeca.textContent = `${quem} · ${hora(m.quando)}`;
  if (daBeatriz && m.texto) {
    const tocar = document.createElement("button");
    tocar.type = "button";
    tocar.className = "msg-tocar";
    tocar.setAttribute("aria-label", "Ouvir esta resposta");
    tocar.appendChild(icone("M8 5v14l11-7z"));
    tocar.addEventListener("click", () => { destravarAudio(); falar(m.id, true); });
    cabeca.appendChild(tocar);
  }
  caixa.appendChild(cabeca);
  if (m.texto) caixa.appendChild(textoFormatado(m.texto));
  if (m.arquivos && m.arquivos.length) caixa.appendChild(caixaDeAnexos(m.arquivos));
  return caixa;
}

function caixaDeAnexos(arquivos) {
  const anexos = document.createElement("div");
  anexos.className = "anexos";
  for (const anexo of arquivos) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "anexo";
    botao.appendChild(icone("M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M12 11v6M9 14l3 3 3-3"));
    const nome = document.createElement("span");
    nome.textContent = `${anexo.nome} · ${tamanho(anexo.tamanho)}`;
    botao.appendChild(nome);
    botao.addEventListener("click", () => baixar(anexo));
    anexos.appendChild(botao);
  }
  return anexos;
}

function textoFormatado(texto, classe = "msg-texto") {
  // Só negrito (*assim* ou **assim**, como no WhatsApp) e quebras de linha; o resto entra como texto puro.
  const p = document.createElement("p");
  p.className = classe;
  for (const parte of texto.split(/(\*\*[^*\n]+\*\*|\*[^*\n]+\*)/)) {
    if (/^\*{1,2}[^*\n]+\*{1,2}$/.test(parte)) {
      const forte = document.createElement("strong");
      forte.textContent = parte.replace(/^\*+|\*+$/g, "");
      p.appendChild(forte);
    } else if (parte) {
      p.appendChild(document.createTextNode(parte));
    }
  }
  return p;
}

function elemento(tag, classe, texto) {
  const el = document.createElement(tag);
  if (classe) el.className = classe;
  if (texto !== undefined) el.textContent = texto;
  return el;
}

function icone(caminho) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", caminho);
  svg.appendChild(path);
  return svg;
}

function mostrarPensando() {
  const caixa = elemento("div", "msg holo msg-beatriz pensando-pontos");
  caixa.id = "pensando";
  caixa.append(elemento("span"), elemento("span"), elemento("span"));
  $("mensagens").appendChild(caixa);
  definirEstado("pensando");
}

function tirarPensando() {
  const caixa = $("pensando");
  if (caixa) caixa.remove();
  if (document.body.classList.contains("pensando")) definirEstado(estado.falando ? "falando" : "");
}

function rolarParaBaixo() {
  const lista = $("mensagens");
  requestAnimationFrame(() => { lista.scrollTop = lista.scrollHeight; });
}

const hora = (quando) => new Date(quando * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
const resumir = (texto) => (texto || "").replace(/\s+/g, " ").slice(0, 90) + ((texto || "").length > 90 ? "…" : "");
function tamanho(bytes) {
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// ------------------------------------------------------------------ cartões (clima, filmes, resumo)
function caixaDoCartao(m) {
  const d = m.dados || {};
  const caixa = elemento("div", `msg holo cartao cartao-${m.cartao}`);
  const rotulo = elemento("div", "holo-rotulo");
  const titulos = { clima: ["Clima", d.lugar], filmes: ["Em cartaz", d.cidade], resumo: ["Resumo", d.tema] };
  const [titulo, codigo] = titulos[m.cartao] || ["Beatriz", ""];
  rotulo.append(elemento("span", "", titulo), elemento("span", "holo-codigo", codigo || hora(m.quando)));
  caixa.appendChild(rotulo);
  if (m.cartao === "clima") preencherClima(caixa, d);
  if (m.cartao === "filmes") preencherFilmes(caixa, d);
  if (m.cartao === "resumo") preencherResumo(caixa, d);
  if (m.arquivos && m.arquivos.length) caixa.appendChild(caixaDeAnexos(m.arquivos));
  return caixa;
}

const ICONES_DO_TEMPO = {
  sol: "M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  parcial: "M8 2.5v1.5M3 7.5h1.5M4.5 4l1 1M12 4l-1 1M5.5 9.5a3 3 0 0 1 5.3-2.2M8 20h9a3.5 3.5 0 0 0 0-7 4.5 4.5 0 0 0-8.6 1.4A2.8 2.8 0 0 0 8 20z",
  nuvem: "M7 19h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6 1.6A3.2 3.2 0 0 0 7 19z",
  chuva: "M7 16h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6 1.6A3.2 3.2 0 0 0 7 16zM8 19l-1 2.5M12 19l-1 2.5M16 19l-1 2.5",
  tempestade: "M7 15h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6 1.6A3.2 3.2 0 0 0 7 15zM12.5 15.5l-2 3.5h3l-2 3.5",
  neve: "M7 15h10a4 4 0 0 0 0-8 5 5 0 0 0-9.6 1.6A3.2 3.2 0 0 0 7 15zM8 19h.01M12 20h.01M16 19h.01M10 22h.01M14 22h.01",
};
function tipoDoTempo(codigo) {
  if (codigo <= 0) return "sol";
  if (codigo <= 2) return "parcial";
  if (codigo <= 48) return "nuvem";
  if (codigo >= 95) return "tempestade";
  if ((codigo >= 71 && codigo <= 77) || codigo === 85 || codigo === 86) return "neve";
  return "chuva";
}
function iconeDoTempo(alvo, codigo) {
  const tipo = tipoDoTempo(codigo);
  alvo.textContent = "";
  alvo.appendChild(icone(ICONES_DO_TEMPO[tipo]));
  alvo.classList.toggle("frio", tipo !== "sol" && tipo !== "parcial");
}
function nomeDoDia(data, indice) {
  if (indice === 0) return "Hoje";
  return new Date(`${data}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "");
}

function preencherClima(caixa, d) {
  const agora = elemento("div", "clima-agora");
  const figura = elemento("span", "clima-icone");
  iconeDoTempo(figura, d.codigo);
  agora.append(figura, elemento("span", "clima-temp", `${d.temperatura}°`));
  caixa.appendChild(agora);
  caixa.appendChild(elemento("p", "msg-texto", d.descricao ? d.descricao[0].toUpperCase() + d.descricao.slice(1) : ""));
  const detalhes = elemento("div", "clima-detalhes");
  detalhes.append(elemento("span", "", `Sensação ${d.sensacao}°`), elemento("span", "", `Umidade ${d.umidade}%`),
                  elemento("span", "", `Vento ${d.vento} km/h`));
  caixa.appendChild(detalhes);
  const dias = elemento("div", "clima-dias");
  (d.dias || []).slice(0, 3).forEach((dia, i) => {
    const bloco = elemento("div", "clima-dia");
    const figuraDia = elemento("div", "clima-icone");
    iconeDoTempo(figuraDia, dia.codigo);
    bloco.append(elemento("div", "clima-dia-nome", nomeDoDia(dia.data, i)), figuraDia,
                 elemento("div", "clima-dia-temp", `${dia.maxima}° / ${dia.minima}°`));
    if (dia.chuva !== null && dia.chuva !== undefined) bloco.appendChild(elemento("div", "clima-dia-chuva", `${dia.chuva}% chuva`));
    dias.appendChild(bloco);
  });
  caixa.appendChild(dias);
}

function preencherFilmes(caixa, d) {
  const lista = elemento("div", "filmes");
  for (const filme of d.filmes || []) {
    const item = elemento("div", "filme");
    const poster = elemento("div", "filme-poster");
    if (filme.estreia_hoje) poster.appendChild(elemento("span", "filme-selo", "Estreia"));
    if (filme.poster) carregarPoster(poster, filme.poster, filme.titulo);
    item.append(poster, elemento("p", "filme-titulo", filme.titulo), elemento("span", "filme-genero", filme.genero || ""));
    lista.appendChild(item);
  }
  caixa.appendChild(lista);
  if ((d.pre_venda || []).length) {
    const lista2 = elemento("ul", "lista");
    for (const filme of d.pre_venda) {
      const linha = elemento("li");
      linha.append(elemento("span", "hora", "Pré-venda"), elemento("span", "", `${filme.titulo}${filme.estreia ? ` · ${filme.estreia}` : ""}`));
      lista2.appendChild(linha);
    }
    caixa.appendChild(lista2);
  }
}

async function carregarPoster(alvo, imagemId, titulo) {
  try {
    const resposta = await api(`/api/imagem/${encodeURIComponent(imagemId)}`);
    const imagem = new Image();
    imagem.alt = titulo;
    imagem.onload = () => imagem.classList.add("pronta");
    imagem.src = URL.createObjectURL(await resposta.blob());
    alvo.prepend(imagem);
  } catch { /* sem pôster: fica o fundo */ }
}

function preencherResumo(caixa, d) {
  if (d.fala) caixa.appendChild(textoFormatado(d.fala, "msg-texto resumo-fala"));
  for (const [chave, titulo] of Object.entries(SECOES_DO_RESUMO)) {
    const conteudo = (d.secoes || {})[chave];
    if (!conteudo) continue;
    const secao = elemento("details", "resumo-secao");
    secao.append(elemento("summary", "", titulo), textoFormatado(conteudo, ""));
    caixa.appendChild(secao);
  }
  if ((d.fontes || []).length) caixa.appendChild(elemento("p", "resumo-fontes", `Fontes: ${d.fontes.join(" · ")}`));
}

// ------------------------------------------------------------------ pedidos
async function enviarPedido(texto) {
  texto = (texto || "").trim();
  if (!texto) return;
  mostrarTela("conversa");
  $("campo").value = "";
  som("enviar");
  try {
    await api("/api/pedido", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ texto }) });
  } catch (erro) {
    if (erro.message !== "nao_pareado") falhou(`Não consegui mandar: ${erro.message === "Failed to fetch" ? "o PC está fora do alcance." : erro.message}`);
  }
}

function falhou(texto) {
  som("erro");
  aviso(texto, true);
}

// ------------------------------------------------------------------ arquivos e fotos
async function mandarArquivo(arquivo) {
  if (arquivo.size > LIMITE_ARQUIVO_MB * 1024 * 1024) return falhou(`${arquivo.name} passa de ${LIMITE_ARQUIVO_MB} MB.`);
  const legenda = $("campo").value.trim(); // o que estiver escrito vira o pedido do arquivo ("vira fonte", "imprime")
  aviso(`Mandando ${arquivo.name} ao PC…`);
  som("enviar");
  try {
    await api("/api/arquivo", {
      method: "POST",
      headers: { "Content-Type": arquivo.type || "application/octet-stream", "X-Nome": encodeURIComponent(arquivo.name),
                 "X-Legenda": encodeURIComponent(legenda) },
      body: arquivo,
    });
    $("campo").value = "";
    mostrarTela("conversa");
  } catch (erro) {
    if (erro.message !== "nao_pareado") falhou(`${arquivo.name}: ${erro.message}`);
  }
}

async function baixar(anexo) {
  try {
    const resposta = await api(`/api/arquivo/${encodeURIComponent(anexo.id)}`);
    const endereco = URL.createObjectURL(await resposta.blob());
    const link = document.createElement("a");
    link.href = endereco;
    link.download = anexo.nome;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(endereco), 60000);
  } catch (erro) {
    if (erro.message !== "nao_pareado") falhou(`Não consegui baixar: ${erro.message}`);
  }
}

// ------------------------------------------------------------------ voz: segure para falar
const gravacao = { gravador: null, fluxo: null, partes: [], inicio: 0, y: 0, cancelar: false, relogio: null, soltou: false };

function formatoDeGravacao() {
  if (!window.MediaRecorder) return "";
  return ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((tipo) => MediaRecorder.isTypeSupported(tipo)) || "";
}

async function comecarGravacao(evento) {
  evento.preventDefault();
  // O quadro da gravação cobre o botão: soltar tem de chegar ao botão (no toque já chega; com o mouse, não).
  try { evento.currentTarget.setPointerCapture(evento.pointerId); } catch { /* sem captura: o toque já prende */ }
  destravarAudio();
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    return falhou("Este navegador não grava áudio. No iPhone, abra pelo endereço https do Tailscale.");
  }
  gravacao.y = evento.clientY;
  gravacao.cancelar = false;
  gravacao.soltou = false;
  try {
    if (!gravacao.fluxo || !gravacao.fluxo.active) {
      gravacao.fluxo = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      if (gravacao.soltou) return; // soltou enquanto o iPhone pedia permissão: a próxima vez já grava
    }
  } catch {
    return falhou("Sem acesso ao microfone. Libere em Ajustes > Safari > Microfone.");
  }
  if (tocador && !tocador.paused) tocador.pause(); // ela para de falar quando você começa
  const tipo = formatoDeGravacao();
  gravacao.partes = [];
  gravacao.gravador = new MediaRecorder(gravacao.fluxo, tipo ? { mimeType: tipo } : undefined);
  gravacao.gravador.ondataavailable = (e) => { if (e.data && e.data.size) gravacao.partes.push(e.data); };
  gravacao.gravador.onstop = enviarGravacao;
  gravacao.gravador.start();
  gravacao.inicio = Date.now();
  som("gravar");
  definirEstado("ouvindo");
  $("gravando").hidden = false;
  $("gravando").classList.remove("cancelar");
  if (window.visualApp) window.visualApp.ouvir(gravacao.fluxo, contextoDeSom());
  $("botao-microfone").classList.add("gravando");
  gravacao.relogio = setInterval(() => {
    const segundos = Math.floor((Date.now() - gravacao.inicio) / 1000);
    $("gravando-tempo").textContent = `${Math.floor(segundos / 60)}:${String(segundos % 60).padStart(2, "0")}`;
    if (segundos >= 60) terminarGravacao();
  }, 250);
}

function moverGravacao(evento) {
  if (!gravacao.gravador) return;
  gravacao.cancelar = gravacao.y - evento.clientY > 90;
  $("gravando").classList.toggle("cancelar", gravacao.cancelar);
}

function terminarGravacao() {
  gravacao.soltou = true;
  if (!gravacao.gravador || gravacao.gravador.state !== "recording") return;
  clearInterval(gravacao.relogio);
  $("gravando").hidden = true;
  $("gravando-tempo").textContent = "0:00";
  if (window.visualApp) window.visualApp.pararDeOuvir();
  $("botao-microfone").classList.remove("gravando");
  som("parar");
  definirEstado("");
  gravacao.gravador.stop();
}

async function enviarGravacao() {
  const gravador = gravacao.gravador;
  gravacao.gravador = null;
  const duracao = Date.now() - gravacao.inicio;
  if (gravacao.cancelar) return aviso("Gravação cancelada.");
  if (duracao < 600) return aviso("Segure o botão enquanto fala.");
  const audioGravado = new Blob(gravacao.partes, { type: gravador.mimeType || "audio/mp4" });
  mostrarTela("conversa");
  mostrarPensando();
  try {
    const resposta = await api("/api/voz", { method: "POST", headers: { "Content-Type": audioGravado.type }, body: audioGravado });
    const { texto } = await resposta.json();
    if (!texto) {
      tirarPensando();
      falhou("Não entendi o áudio. Tenta de novo, mais perto do microfone.");
    }
  } catch (erro) {
    tirarPensando();
    if (erro.message !== "nao_pareado") falhou(`Não consegui mandar o áudio: ${erro.message}`);
  }
}

// O iPhone para o microfone quando o app sai da tela; soltar o fluxo apaga o aviso laranja de microfone.
document.addEventListener("visibilitychange", () => {
  if (document.hidden && gravacao.fluxo && !gravacao.gravador) {
    gravacao.fluxo.getTracks().forEach((t) => t.stop());
    gravacao.fluxo = null;
  }
});

// ------------------------------------------------------------------ respostas faladas no celular
const tocador = new Audio();
let audioDestravado = false;
tocador.addEventListener("playing", () => { if (tocador.src !== SILENCIO) definirEstado("falando"); });
tocador.addEventListener("pause", () => { if (document.body.classList.contains("falando")) definirEstado(""); });

function destravarAudio() {
  // O iPhone só toca som depois de um toque: o primeiro toque toca um silêncio e libera a voz e os sons.
  contextoDeSom();
  if (audioDestravado) return;
  audioDestravado = true;
  tocador.src = SILENCIO;
  tocador.play().catch(() => { audioDestravado = false; });
}

function falar(mensagemId, agora = false) {
  if (agora) {
    estado.filaDeFala = [mensagemId];
    tocador.pause();
    estado.falando = false;
  } else {
    estado.filaDeFala.push(mensagemId);
  }
  if (!estado.falando) proximaFala();
}

async function proximaFala() {
  const mensagemId = estado.filaDeFala.shift();
  if (mensagemId === undefined) {
    estado.falando = false;
    definirEstado("");
    return;
  }
  estado.falando = true;
  try {
    const resposta = await api(`/api/fala/${mensagemId}`);
    const arquivo = await resposta.blob();
    const endereco = URL.createObjectURL(arquivo);
    if (window.visualApp) window.visualApp.falar(tocador, arquivo, contextoDeSom()); // a esfera vibra com esta fala
    tocador.src = endereco;
    tocador.onended = () => { URL.revokeObjectURL(endereco); proximaFala(); };
    await tocador.play();
  } catch {
    proximaFala();
  }
}

function definirFalarAqui(ligado, avisar = true) {
  estado.falarAqui = ligado;
  gravar(OPCOES.som, ligado ? "1" : "");
  $("botao-som").setAttribute("aria-pressed", String(ligado));
  $("opcao-fala").checked = ligado;
  destravarAudio();
  if (avisar) aviso(ligado ? "Vou falar as respostas aqui no celular." : "Respostas só por escrito.");
}

// ------------------------------------------------------------------ painel: visor, widgets, missões e sistema
async function carregarPainel() {
  if (!estado.chave) return;
  let dados;
  try {
    dados = await (await api("/api/painel")).json();
  } catch {
    return;
  }
  estado.painel = dados;
  marcarOnline(true);
  const sistemas = dados.sistemas || {};
  const iaOk = sistemas.ia_local === "online";
  $("ind-ia").classList.toggle("ok", iaOk);
  $("ind-ia").classList.toggle("falha", !!sistemas.ia_local && !iaOk);
  $("estado-pc-texto").textContent = `PC online · IA local ${iaOk ? "pronta" : sistemas.ia_local || "…"}`;
  atualizarVisor();
  mostrarClima(dados.clima);
  mostrarProximo(dados);
  const missoes = dados.tarefas || [];
  const concluidas = missoes.filter((m) => /conclu/i.test(m.status || "")).map((m) => m.id);
  if (estado.concluidas && concluidas.some((id) => !estado.concluidas.includes(id))) celebrar();
  estado.concluidas = concluidas;
  const ativas = missoes.filter((m) => !terminou(m.status));
  $("selo-missoes").hidden = !ativas.length;
  $("selo-missoes").textContent = String(ativas.length);
  mostrarMissaoAtiva(ativas);
  mostrarMissoes(missoes);
  mostrarSistema(sistemas, dados.aparelho);
}

function atualizarVisor() {
  const agora = new Date();
  const h = agora.getHours();
  const nome = estado.painel && estado.painel.nome ? `, ${estado.painel.nome}` : "";
  $("saudacao").textContent = `${h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite"}${nome}`;
  $("visor-hora").textContent = agora.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  $("visor-data").textContent = agora.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
  $("relogio").textContent = $("visor-hora").textContent;
  $("visor-codigo").textContent = `B-${String(agora.getDate()).padStart(2, "0")}${String(agora.getMonth() + 1).padStart(2, "0")}`;
}

function mostrarClima(clima) {
  if (!clima) return;
  iconeDoTempo($("clima-icone"), clima.codigo);
  $("clima-temp").textContent = `${clima.temperatura}°`;
  $("clima-lugar").textContent = clima.lugar || "";
  $("clima-desc").textContent = clima.descricao ? clima.descricao[0].toUpperCase() + clima.descricao.slice(1) : "";
  const hoje = (clima.dias || [])[0];
  $("clima-minmax").textContent = hoje ? `${hoje.minima}° a ${hoje.maxima}°${hoje.chuva ? ` · ${hoje.chuva}% chuva` : ""}` : "";
}

function mostrarProximo(dados) {
  const itens = ((dados.hoje || {}).itens || []).filter((i) => !i.passou);
  const proximo = itens[0];
  $("proximo-conta").textContent = itens.length ? `${itens.length} hoje` : "";
  if (proximo) {
    $("proximo-hora").textContent = proximo.agora ? "Agora" : proximo.dia_inteiro ? "Hoje" : proximo.hora;
    $("proximo-titulo").textContent = proximo.titulo;
    $("proximo-extra").textContent = itens[1] ? `depois: ${itens[1].hora || ""} ${itens[1].titulo}`.trim() : "último de hoje";
  } else {
    $("proximo-hora").textContent = "Livre";
    $("proximo-titulo").textContent = "Nada mais hoje";
    $("proximo-extra").textContent = dados.proximo || "";
  }
}

function mostrarMissaoAtiva(ativas) {
  const missao = ativas[0];
  $("w-missao").hidden = !missao;
  if (!missao) return;
  $("missao-conta").textContent = ativas.length > 1 ? `+${ativas.length - 1}` : missao.status || "";
  $("missao-pedido").textContent = missao.pedido;
  $("missao-etapa").textContent = missao.etapa || "na fila";
  preencherEtapas($("missao-etapas"), missao);
}

function mostrarRecado() {
  const ultima = [...estado.mensagens].reverse().find((m) => m.tipo !== "voce" && m.texto);
  $("w-recado").hidden = !ultima;
  if (!ultima) return;
  $("recado-hora").textContent = hora(ultima.quando);
  $("recado-texto").textContent = ultima.texto.replace(/\*/g, "");
}

const terminou = (status) => /conclu|falh|cancel|realizad/i.test(status || "");

function preencherEtapas(alvo, m) {
  alvo.textContent = "";
  const plano = m.plano || [];
  const atual = terminou(m.status) ? plano.length : (typeof m.etapa_indice === "number" ? m.etapa_indice : 0);
  plano.forEach((_, i) => alvo.appendChild(elemento("span", i < atual ? "feita" : i === atual ? "atual" : "")));
}

function mostrarMissoes(missoes) {
  const lista = $("lista-missoes");
  lista.textContent = "";
  $("missoes-vazio").hidden = missoes.length > 0;
  missoes.forEach((m, i) => {
    const caixa = elemento("div", "missao holo");
    caixa.dataset.i = String(i);
    const status = elemento("div", `missao-status${/conclu/i.test(m.status) ? " concluida" : /falh|realizad/i.test(m.status) ? " falhou" : ""}`,
                            m.status || "na fila");
    caixa.append(status, elemento("p", "missao-pedido", m.pedido));
    if (m.etapa && !terminou(m.status)) caixa.appendChild(elemento("p", "missao-etapa", m.etapa));
    if ((m.plano || []).length) {
      const etapas = elemento("div", "etapas");
      preencherEtapas(etapas, m);
      caixa.appendChild(etapas);
    }
    if ((m.arquivos || []).length) caixa.appendChild(caixaDeAnexos(m.arquivos));
    lista.appendChild(caixa);
  });
  aplicarAtrasos(lista);
}

// ------------------------------------------------------------------ diagnóstico (tela Sistema)
function mostrarSistema(sistemas, aparelho) {
  estado.sistemas = sistemas;
  mostrarRecursos(sistemas);
  const fontes = sistemas.fontes || {};
  const porcentagem = fontes.total ? Math.round((100 * fontes.lidas) / fontes.total) : null;
  definirAnel("fontes", porcentagem, fontes.total ? `${fontes.lidas}/${fontes.total}` : "--");

  // colmeia: um hexágono por sistema, com a cor do estado
  const celulas = [
    ["IA local", sistemas.ia_local, sistemas.ia_local === "online" ? "ok" : "erro"],
    ...(sistemas.nuvem || []).map((n) => [n.nome, n.estado === "ativo" ? "ativo" : n.texto, n.estado === "ativo" ? "ok" : n.estado === "pausado" ? "alerta" : ""]),
    ["WhatsApp", sistemas.whatsapp, sistemas.whatsapp === "conectado" ? "ok" : sistemas.whatsapp === "desligado" ? "" : "alerta"],
    ["Biblioteca", sistemas.biblioteca, sistemas.biblioteca === "pronta" ? "ok" : sistemas.biblioteca === "falhou" ? "erro" : "alerta"],
    ["Voz", sistemas.voz, "ok"],
    ["Escuta", sistemas.escuta, "ok"],
    ["App", estado.online ? "conectado" : "offline", estado.online ? "ok" : "erro"],
  ];
  // em favo: linhas de 3 e 2 alternadas, encaixadas umas nas outras
  const colmeia = $("colmeia");
  colmeia.textContent = "";
  const visiveis = celulas.filter((c) => c[1]);
  for (let i = 0, linha = 0; i < visiveis.length; linha++) {
    const quantos = linha % 2 ? 2 : 3;
    const fileira = elemento("div", "hex-linha");
    for (const [nome, valor, classe] of visiveis.slice(i, i + quantos)) {
      const hex = elemento("div", `hex ${classe}`);
      hex.title = `${nome}: ${valor}`;
      hex.append(elemento("span", "hex-nome", nome), elemento("span", "hex-estado", valor));
      fileira.appendChild(hex);
    }
    colmeia.appendChild(fileira);
    i += quantos;
  }
  const problemas = celulas.filter((c) => c[1] && (c[2] === "alerta" || c[2] === "erro")).length;
  $("colmeia-resumo").textContent = problemas ? `${problemas} com atenção` : "tudo certo";

  // avisos no estilo "Temp Warning"
  const avisos = [];
  if (sistemas.ia_local && sistemas.ia_local !== "online") avisos.push(["erro", "IA local fora do ar"]);
  for (const n of sistemas.nuvem || []) if (n.estado === "pausado") avisos.push(["", `${n.nome} em pausa`]);
  if (sistemas.biblioteca === "falhou") avisos.push(["erro", "Biblioteca com falha"]);
  if (sistemas.cpu >= 85) avisos.push(["", "CPU alta"]);
  if (sistemas.ram >= 85) avisos.push(["", "Memória alta"]);
  if (sistemas.disco >= 90) avisos.push(["", "Disco quase cheio"]);
  const caixa = $("avisos");
  caixa.textContent = "";
  for (const [classe, texto] of avisos.length ? avisos : [["ok", "Tudo em ordem"]]) caixa.appendChild(elemento("span", `aviso-hud ${classe}`, texto));
  $("aparelho-nome").textContent = aparelho ? `Pareado como “${aparelho}”.` : "";
}

function definirAnel(nome, porcentagem, texto) {
  $(`numero-${nome}`).textContent = texto;
  $(`anel-${nome}`).style.strokeDasharray = `${((porcentagem || 0) / 100) * 251} 252`;
}

function mostrarRecursos(r) {
  const texto = (v) => (typeof v === "number" ? `${v}%` : "--");
  $("diag-cpu").textContent = texto(r.cpu);
  $("diag-ram").textContent = texto(r.ram);
  $("diag-disco").textContent = texto(r.disco);
  $("diag-codigo").textContent = estado.online ? "online" : "sem sinal";
  $("arco-cpu").style.strokeDasharray = `${((r.cpu || 0) / 100) * 590} 700`;
  $("arco-ram").style.strokeDasharray = `${((r.ram || 0) / 100) * 653} 700`;
  definirAnel("cpu", r.cpu, texto(r.cpu));
  definirAnel("ram", r.ram, texto(r.ram));
  definirAnel("disco", r.disco, texto(r.disco));
}

// O gráfico de atividade: a tela Sistema pergunta CPU e memória a cada 3 s enquanto está aberta (40 pontos = 2 minutos).
const atividade = { cpu: [], ram: [], relogio: null };
function acompanharAtividade(ligar) {
  clearInterval(atividade.relogio);
  if (!ligar) return;
  const medir = async () => {
    if (document.hidden || !estado.chave) return;
    try {
      const r = await (await api("/api/recursos")).json();
      mostrarRecursos({ ...(estado.sistemas || {}), ...r });
      if (typeof r.cpu === "number") {
        atividade.cpu.push(r.cpu);
        atividade.ram.push(typeof r.ram === "number" ? r.ram : 0);
        atividade.cpu = atividade.cpu.slice(-40);
        atividade.ram = atividade.ram.slice(-40);
        desenharGrafico();
        $("atividade-agora").textContent = `CPU ${r.cpu}% · MEM ${r.ram ?? "--"}%`;
      }
    } catch { /* sem conexão: o laço de mensagens avisa */ }
  };
  medir();
  atividade.relogio = setInterval(medir, 3000);
}

function desenharGrafico() {
  // os pontos novos entram pela direita, como num monitor ao vivo
  const pontos = (valores) => valores.map((v, i) => [300 - (300 * (valores.length - 1 - i)) / 39, 88 - (v / 100) * 84]);
  const linha = (lista) => lista.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const cpu = pontos(atividade.cpu);
  if (!cpu.length) return;
  $("grafico-cpu").setAttribute("d", linha(cpu));
  $("grafico-ram").setAttribute("d", linha(pontos(atividade.ram)));
  $("grafico-area").setAttribute("d", `${linha(cpu)} L300 90 L${cpu[0][0].toFixed(1)} 90 Z`);
}

// ------------------------------------------------------------------ folha de ações (escolhas, nomes, confirmações)
function folha({ titulo, codigo = "", texto = "", campo = null, botoes = [] }) {
  return new Promise((pronto) => {
    const fundo = $("folha");
    $("folha-titulo").textContent = titulo;
    $("folha-codigo").textContent = codigo;
    $("folha-texto").textContent = texto;
    const entrada = $("folha-campo");
    entrada.hidden = !campo;
    if (campo) {
      entrada.value = campo.valor || "";
      entrada.placeholder = campo.placeholder || "";
    }
    const lista = $("folha-botoes");
    lista.textContent = "";
    const fechar = (escolha) => {
      fundo.hidden = true;
      fundo.onclick = null;
      pronto({ escolha, valor: entrada.value.trim() });
    };
    for (const botao of [...botoes, { rotulo: "Cancelar", valor: null, classe: "voltar" }]) {
      const b = elemento("button", `folha-botao ${botao.classe || ""}`);
      b.type = "button";
      if (botao.recuo) {
        const recuo = elemento("span", "recuo");
        recuo.style.width = `${botao.recuo * 14}px`;
        b.appendChild(recuo);
      }
      b.appendChild(document.createTextNode(botao.rotulo));
      b.addEventListener("click", () => fechar(botao.valor));
      lista.appendChild(b);
    }
    fundo.onclick = (e) => { if (e.target === fundo) fechar(null); };
    fundo.hidden = false;
    if (campo) setTimeout(() => entrada.focus(), 50);
  });
}

// ------------------------------------------------------------------ biblioteca (fontes, só arquivos e recebidos)
const biblio = { dados: null, abertas: null, destinoDoUpload: "", relogio: null };
const SIGLAS_DE_TIPO = { pdf: "PDF", apresentacao: "PPT", documento: "DOC", planilha: "XLS", imagem: "IMG", nota: "TXT", pagina: "WEB" };
const semAcento = (texto) => texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

async function carregarBiblioteca() {
  try {
    biblio.dados = await (await api("/api/biblioteca")).json();
  } catch (erro) {
    if (erro.message !== "nao_pareado") $("biblio-resumo").textContent = `Não consegui abrir a biblioteca: ${erro.message}`;
    return;
  }
  if (!biblio.abertas) biblio.abertas = new Set(biblio.dados.pastas.filter((p) => p.arquivos.length).map((p) => p.id));
  mostrarBiblioteca();
}

function mostrarBiblioteca() {
  const dados = biblio.dados;
  if (!dados || !biblio.abertas) return;
  $("biblio-contagem").textContent = `${dados.total} arquivos`;
  $("biblio-resumo").textContent = dados.total
    ? `${dados.fontes} fontes (${dados.lidas} lidas) · ${dados.so_arquivos} só arquivos · ${dados.recebidos} recebidos. Toque num arquivo para organizar.`
    : "Nenhuma fonte ainda. Use o + de uma pasta para mandar arquivos.";
  // cada palavra digitada tem de estar no nome, em qualquer ordem e sem acento ("embargos terceiro")
  const palavras = semAcento($("biblio-busca").value.trim()).split(/\s+/).filter(Boolean);
  const busca = palavras.length > 0;
  const lista = $("biblio-pastas");
  lista.textContent = "";
  let algum = false, areaAtual = "";
  dados.pastas.forEach((pasta, i) => {
    const arquivos = busca ? pasta.arquivos.filter((a) => palavras.every((p) => semAcento(a.nome).includes(p))) : pasta.arquivos;
    if (busca && !arquivos.length) return;
    algum = true;
    if (pasta.area && pasta.area !== areaAtual) { // FONTES, SÓ ARQUIVOS, RECEBIDOS
      areaAtual = pasta.area;
      const area = (dados.areas || []).find((a) => a.id === pasta.area);
      if (area) {
        const cabeca = elemento("div", `biblio-area area-${area.id}`);
        cabeca.append(elemento("span", "biblio-area-nome", `${area.nome} · ${area.arquivos}`), elemento("span", "biblio-area-desc", area.descricao));
        lista.appendChild(cabeca);
      }
    }
    const caixa = elemento("div", `pasta holo${busca || biblio.abertas.has(pasta.id) ? " aberta" : ""}`);
    caixa.dataset.i = String(i + 2);
    const cabeca = elemento("div", "pasta-cabeca");
    cabeca.style.paddingLeft = `${12 + pasta.profundidade * 14}px`;
    cabeca.appendChild(icone("M3 6h6l2 2h10v11H3z"));
    const nome = elemento("span", "pasta-nome", pasta.nome);
    if (pasta.profundidade) nome.appendChild(elemento("span", "pasta-caminho", pasta.caminho));
    const seta = icone("M9 6l6 6-6 6");
    seta.classList.add("pasta-seta");
    const mais = elemento("button", "pasta-mais");
    mais.type = "button";
    mais.setAttribute("aria-label", `Opções da pasta ${pasta.nome}`);
    mais.appendChild(icone("M12 5v14M5 12h14"));
    mais.addEventListener("click", (e) => { e.stopPropagation(); acoesDaPasta(pasta); });
    cabeca.append(nome, elemento("span", "pasta-conta", String(pasta.arquivos.length)), mais, seta);
    cabeca.addEventListener("click", () => {
      biblio.abertas.has(pasta.id) ? biblio.abertas.delete(pasta.id) : biblio.abertas.add(pasta.id);
      caixa.classList.toggle("aberta");
    });
    const itens = elemento("ul", "pasta-arquivos");
    for (const arquivo of arquivos) itens.appendChild(linhaDoArquivo(arquivo));
    if (!arquivos.length) {
      itens.appendChild(elemento("li", "pasta-vazia", pasta.raiz ? "Os arquivos estão nas pastas de dentro." : "Pasta vazia. Toque no + para mandar arquivos."));
    }
    caixa.append(cabeca, itens);
    lista.appendChild(caixa);
  });
  $("biblio-vazio").hidden = algum || !busca;
  aplicarAtrasos(lista);
}

function linhaDoArquivo(arquivo) {
  const item = elemento("li");
  const botao = elemento("button", "arquivo");
  botao.type = "button";
  botao.append(elemento("span", `arquivo-icone ${arquivo.tipo}`, SIGLAS_DE_TIPO[arquivo.tipo] || "ARQ"));
  const texto = elemento("span", "arquivo-texto");
  const data = new Date(arquivo.modificado * 1000).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" });
  texto.append(elemento("span", "arquivo-nome", arquivo.nome), elemento("span", "arquivo-meta", `${tamanho(arquivo.tamanho)} · ${data}`));
  const classeEstado = semAcento(arquivo.estado).replace(/\s+/g, "-");
  botao.append(texto, elemento("span", `selo-estado ${classeEstado}`, arquivo.estado));
  botao.addEventListener("click", () => acoesDoArquivo(arquivo));
  item.appendChild(botao);
  return item;
}

async function acoesDoArquivo(arquivo) {
  const botoes = [
    { rotulo: "Baixar / abrir no celular", valor: "baixar" },
    { rotulo: "Renomear", valor: "renomear" },
    { rotulo: "Mover para outra pasta", valor: "mover" },
  ];
  if (arquivo.area !== "fontes") botoes.push({ rotulo: "Virar fonte (a Beatriz passa a ler)", valor: "virar_fonte", classe: "principal" });
  if (arquivo.area !== "arquivos") botoes.push({ rotulo: "Só arquivo (fica guardado; ela acha pelo nome, mas não lê)", valor: "so_arquivo" });
  botoes.push({ rotulo: "Apagar (vai para a Lixeira do PC)", valor: "apagar", classe: "perigo" });
  const { escolha } = await folha({ titulo: arquivo.nome, codigo: arquivo.estado, botoes });
  if (escolha === "baixar") return baixarDaBiblioteca(arquivo);
  if (escolha === "renomear") {
    const base = arquivo.nome.replace(/\.[^.]+$/, "");
    const { escolha: ok, valor } = await folha({ titulo: "Renomear", texto: arquivo.nome, campo: { valor: base },
                                                 botoes: [{ rotulo: "Salvar nome", valor: true, classe: "principal" }] });
    if (ok && valor) return acaoDaBiblioteca("renomear", { arquivo: arquivo.id, nome: valor });
  }
  if (escolha === "mover") {
    const pastas = biblio.dados.pastas;
    const { escolha: destino } = await folha({
      titulo: "Mover para…", texto: arquivo.nome,
      botoes: pastas.map((p) => ({ rotulo: p.caminho, valor: p.id, recuo: p.profundidade })),
    });
    if (destino) return acaoDaBiblioteca("mover", { arquivo: arquivo.id, pasta: destino });
  }
  if (escolha === "virar_fonte" || escolha === "so_arquivo") return acaoDaBiblioteca(escolha, { arquivo: arquivo.id }); // dá para desfazer
  if (escolha === "apagar") {
    const { escolha: ok } = await folha({ titulo: "Apagar?", texto: `“${arquivo.nome}” vai para a Lixeira do Windows. Dá para restaurar de lá.`,
                                          botoes: [{ rotulo: "Apagar", valor: true, classe: "perigo" }] });
    if (ok) return acaoDaBiblioteca("apagar", { arquivo: arquivo.id });
  }
}

async function acoesDaPasta(pasta) {
  const botoes = [{ rotulo: "Mandar arquivos para esta pasta", valor: "enviar" }];
  if (pasta.pode_criar_pasta) botoes.push({ rotulo: "Nova pasta dentro desta", valor: "nova" });
  if (!pasta.raiz && !pasta.arquivos.length) botoes.push({ rotulo: "Apagar esta pasta (vazia)", valor: "apagar", classe: "perigo" });
  const { escolha } = await folha({ titulo: pasta.nome, codigo: pasta.caminho, botoes });
  if (escolha === "enviar") {
    biblio.destinoDoUpload = pasta.id;
    $("biblio-arquivo").click();
  }
  if (escolha === "nova") {
    const { escolha: ok, valor } = await folha({ titulo: "Nova pasta", texto: `Dentro de ${pasta.caminho}`, campo: { placeholder: "Ex.: OAB 2ª fase" },
                                                 botoes: [{ rotulo: "Criar pasta", valor: true, classe: "principal" }] });
    if (ok && valor) {
      biblio.abertas.add(pasta.id);
      return acaoDaBiblioteca("pasta", { pasta: pasta.id, nome: valor });
    }
  }
  if (escolha === "apagar") return acaoDaBiblioteca("apagar_pasta", { pasta: pasta.id });
}

async function acaoDaBiblioteca(acao, corpo) {
  try {
    const resposta = await (await api(`/api/biblioteca/${acao}`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                                 body: JSON.stringify(corpo) })).json();
    resposta.ok ? (som("chegou"), aviso(resposta.mensagem)) : falhou(resposta.mensagem);
  } catch (erro) {
    if (erro.message !== "nao_pareado") falhou(erro.message);
  }
  carregarBiblioteca();
}

async function mandarParaBiblioteca(arquivo) {
  if (arquivo.size > LIMITE_ARQUIVO_MB * 1024 * 1024) return falhou(`${arquivo.name} passa de ${LIMITE_ARQUIVO_MB} MB.`);
  aviso(`Mandando ${arquivo.name}…`);
  try {
    const resposta = await (await api("/api/biblioteca/adicionar", {
      method: "POST",
      headers: { "Content-Type": arquivo.type || "application/octet-stream", "X-Nome": encodeURIComponent(arquivo.name),
                 "X-Pasta": encodeURIComponent(biblio.destinoDoUpload) },
      body: arquivo,
    })).json();
    resposta.ok ? (som("chegou"), aviso(resposta.mensagem)) : falhou(resposta.mensagem);
  } catch (erro) {
    if (erro.message !== "nao_pareado") falhou(`${arquivo.name}: ${erro.message}`);
  }
}

async function baixarDaBiblioteca(arquivo) {
  try {
    const resposta = await api(`/api/biblioteca-arquivo?id=${encodeURIComponent(arquivo.id)}`);
    const endereco = URL.createObjectURL(await resposta.blob());
    const link = document.createElement("a");
    link.href = endereco;
    link.download = arquivo.nome;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(endereco), 60000);
  } catch (erro) {
    if (erro.message !== "nao_pareado") falhou(`Não consegui baixar: ${erro.message}`);
  }
}

// ------------------------------------------------------------------ controle remoto (a TV e o ar pelo Ekaza)
// Cada botão vai direto ao agente do controle (/api/controle), sem passar pela conversa. O infravermelho não tem volta:
// o estado mostrado é o do último comando. Com o ar desligado, mexer na temperatura e no modo só prepara, e o "Ligar"
// manda tudo junto; com ele ligado, cada mudança vai na hora (a temperatura espera você parar de tocar).
const NOMES_DOS_MODOS = { frio: "Frio", quente: "Quente", auto: "Auto", ventilar: "Ventilar", seco: "Seco" };
const NOMES_DOS_VENTOS = { auto: "Auto", baixa: "Baixa", media: "Média", alta: "Alta" };
const controle = { painel: null, alvo: null, relogio: null, canal: "" };

async function carregarControle() {
  try {
    const resposta = await api("/api/controle");
    mostrarControle(await resposta.json());
    marcarOnline(true);
  } catch (erro) {
    if (erro.message === "nao_pareado") return;
    $("controle-vazio").hidden = false;
    $("controle-vazio").textContent = erro.message === "Failed to fetch" ? "Não consegui falar com o PC." : erro.message;
  }
}

function horaCurta(segundos) {
  return segundos ? new Date(segundos * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "";
}

function mostrarControle(p) {
  controle.painel = p;
  const tv = p.tv || {}, ar = p.ar || {};
  const vazio = $("controle-vazio");
  vazio.hidden = p.configurado && (tv.disponivel || ar.disponivel);
  vazio.textContent = !p.configurado ? p.motivo || "O controle remoto não está configurado."
    : "Cadastre a TV e o ar no app Smart Life, dentro do Ekaza, e diga à Beatriz “atualiza os controles da nuvem”.";
  $("controle-tv").hidden = !p.configurado || !tv.disponivel;
  $("controle-ar").hidden = !p.configurado || !ar.disponivel;
  $("controle-aviso").textContent = p.configurado ? p.aviso || "" : "";

  $("tv-nome").textContent = tv.nome || "TV";
  $("tv-estado").textContent = tv.ligada == null ? "sem comando ainda" : `${tv.ligada ? "ligada" : "desligada"} · ${horaCurta(tv.quando)}`;
  const botoes = new Set(tv.botoes || []);
  document.querySelectorAll("[data-tv]").forEach((b) => { b.hidden = !botoes.has(b.dataset.tv); });
  const separados = tv.energia_separada;  // códigos só de ligar e só de desligar (a Sony tem): dois botões
  document.querySelector('[data-tv="ligar"]').hidden = !separados;
  document.querySelector('[data-tv="desligar"]').hidden = !separados;
  document.querySelector('[data-tv="energia"]').hidden = separados || !botoes.has("energia");
  $("tv-cruz").hidden = !["cima", "baixo", "esquerda", "direita", "ok"].some((b) => botoes.has(b));
  $("tv-numeros").hidden = !botoes.has("digito_1");

  $("ar-nome").textContent = ar.nome || "Ar-condicionado";
  if (!controle.alvo) controle.alvo = { temperatura: ar.temperatura, modo: ar.modo, ventilacao: ar.ventilacao };
  desenharAr();
  mostrarCasa(p);
}

// O quadro "TV e ar" da tela inicial: o estado do último comando e um botão que faz o contrário do que está.
function mostrarCasa(p) {
  const tv = p.tv || {}, ar = p.ar || {};
  $("w-casa").hidden = !p.configurado || !(tv.disponivel || ar.disponivel);
  $("casa-ar").hidden = !ar.disponivel;
  $("casa-ar").classList.toggle("ligado", !!ar.ligado);
  $("casa-ar-nome").textContent = ar.nome || "Ar";
  const semTemperatura = ar.modos && ar.modos[ar.modo] && !ar.modos[ar.modo].temps;
  const ajuste = semTemperatura ? NOMES_DOS_MODOS[ar.modo] : `${ar.temperatura} °C · ${(NOMES_DOS_MODOS[ar.modo] || "").toLowerCase()}`;
  $("casa-ar-estado").textContent = ar.ligado ? `${ajuste} · ligado` : ar.quando ? `desligado · último: ${ajuste}` : "sem comando ainda";
  $("casa-ar-botao").textContent = ar.ligado ? "Desligar" : "Ligar";
  $("casa-tv").hidden = !tv.disponivel;
  $("casa-tv").classList.toggle("ligado", !!tv.ligada);
  $("casa-tv-nome").textContent = tv.nome || "TV";
  $("casa-tv-estado").textContent = tv.ligada == null ? "sem comando ainda" : `${tv.ligada ? "ligada" : "desligada"} · ${horaCurta(tv.quando)}`;
  $("casa-tv-botao").textContent = !tv.energia_separada ? "Liga / desliga" : tv.ligada ? "Desligar" : "Ligar";
}

function tocarCasa(aparelho) {
  const p = controle.painel || {};
  if (aparelho === "ar") {
    apertarControle({ aparelho: "ar", acao: p.ar && p.ar.ligado ? "desligar" : "ligar" }, $("casa-ar-botao"));
  } else {
    const tv = p.tv || {};
    const corpo = !tv.energia_separada ? { aparelho: "tv", acao: "botao", botao: "energia" }
      : { aparelho: "tv", acao: tv.ligada ? "desligar" : "ligar" };
    apertarControle(corpo, $("casa-tv-botao"));
  }
}

function desenharAr() {
  const ar = (controle.painel || {}).ar;
  if (!ar || !ar.modos) return;
  const alvo = controle.alvo;
  if (!ar.modos[alvo.modo]) alvo.modo = Object.keys(ar.modos)[0];
  const modo = ar.modos[alvo.modo] || {};
  const temps = modo.temps;
  $("ar-graus").textContent = temps ? alvo.temperatura : "--";
  $("ar-unidade").hidden = !temps;
  $("ar-modo-nome").textContent = NOMES_DOS_MODOS[alvo.modo] || alvo.modo;
  $("ar-menos").disabled = !temps || alvo.temperatura <= temps[0];
  $("ar-mais").disabled = !temps || alvo.temperatura >= temps[1];
  $("ar-estado").textContent = ar.ligado ? `ligado · ${horaCurta(ar.quando)}`
    : ar.quando ? `desligado · ${horaCurta(ar.quando)}` : "sem comando ainda";
  $("controle-ar").classList.toggle("ligado", ar.ligado);
  segmentos("ar-modos", Object.keys(ar.modos), NOMES_DOS_MODOS, alvo.modo, (m) => mudarAr({ modo: m }));
  segmentos("ar-ventos", modo.ventos || [], NOMES_DOS_VENTOS, alvo.ventilacao, (v) => mudarAr({ ventilacao: v }));
}

function segmentos(id, chaves, nomes, atual, aoEscolher) {
  const caixa = $(id);
  caixa.textContent = "";
  for (const chave of chaves) {
    const botao = elemento("button", `segmento${chave === atual ? " ativo" : ""}`, nomes[chave] || chave);
    botao.type = "button";
    botao.addEventListener("click", () => aoEscolher(chave));
    caixa.appendChild(botao);
  }
}

function mudarAr(mudanca) {
  const ar = controle.painel.ar;
  Object.assign(controle.alvo, mudanca);
  const modo = ar.modos[controle.alvo.modo] || {};
  if (modo.ventos && modo.ventos.length && !modo.ventos.includes(controle.alvo.ventilacao)) controle.alvo.ventilacao = modo.ventos[0];
  if (modo.temps) controle.alvo.temperatura = Math.max(modo.temps[0], Math.min(modo.temps[1], controle.alvo.temperatura));
  desenharAr();
  if (!ar.ligado) return;  // desligado: só prepara; o "Ligar" manda
  clearTimeout(controle.relogio);
  controle.relogio = setTimeout(() => { controle.relogio = null; mandarAr(); }, "temperatura" in mudanca ? 800 : 150);
}

function mandarAr(ligar = false) {
  const ar = controle.painel.ar, alvo = controle.alvo;
  const corpo = { aparelho: "ar", acao: "ajustar" };
  if (ligar || alvo.temperatura !== ar.temperatura) corpo.temperatura = alvo.temperatura;
  if (ligar || alvo.modo !== ar.modo) corpo.modo = alvo.modo;
  if (ligar || alvo.ventilacao !== ar.ventilacao) corpo.ventilacao = alvo.ventilacao;
  if (Object.keys(corpo).length > 2) apertarControle(corpo, ligar ? $("ar-ligar") : $("ar-visor"));
}

async function apertarControle(corpo, botao = null) {
  if (botao) botao.classList.add("enviando");
  try {
    const resposta = await api("/api/controle", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
    });
    const dados = await resposta.json();
    if (!controle.relogio) controle.alvo = null;  // volta para o estado que o PC guardou
    mostrarControle(dados.painel);
    if (dados.ok) {
      som("enviar");
      if (botao) { botao.classList.add("feito"); setTimeout(() => botao.classList.remove("feito"), 600); }
    } else {
      som("erro");
      aviso(dados.texto, true);
    }
  } catch (erro) {
    if (erro.message !== "nao_pareado") {
      som("erro");
      aviso(erro.message === "Failed to fetch" ? "Não consegui falar com o PC." : erro.message, true);
    }
  } finally {
    if (botao) botao.classList.remove("enviando");
  }
}

function digitarCanal(texto) {
  controle.canal = texto.slice(0, 4);
  $("tv-canal").textContent = controle.canal;
}

async function carregarAgenda() {
  let semana;
  try {
    semana = await (await api(`/api/agenda?deslocamento=${estado.semana}`)).json();
  } catch {
    return;
  }
  $("semana-titulo").textContent = estado.semana === 0 ? "Esta semana" : semana.titulo;
  const dias = $("dias");
  dias.textContent = "";
  for (const dia of semana.dias || []) {
    const caixa = elemento("div", `dia${dia.hoje ? " hoje" : ""}${dia.passado ? " passado" : ""}`);
    caixa.appendChild(elemento("div", "dia-nome", `${dia.nome} ${dia.numero}${dia.hoje ? " · hoje" : ""}`));
    if (!dia.itens.length) {
      caixa.appendChild(elemento("div", "dia-vazio", "Livre"));
    } else {
      const lista = elemento("ul", "lista");
      for (const item of dia.itens) {
        const linha = elemento("li", item.agora ? "agora" : item.passou ? "passou" : "");
        linha.append(elemento("span", "hora", item.dia_inteiro ? "dia todo" : item.hora),
                     elemento("span", "", item.titulo + (item.repete ? " ↻" : "")));
        lista.appendChild(linha);
      }
      caixa.appendChild(lista);
    }
    dias.appendChild(caixa);
  }
  const tarefas = $("lista-tarefas");
  tarefas.textContent = "";
  for (const t of semana.tarefas || []) {
    const linha = elemento("li", t.feita ? "passou" : "");
    linha.appendChild(elemento("span", "", `${t.feita ? "✓ " : ""}${t.titulo}`));
    if (t.prazo_texto) linha.appendChild(elemento("span", `estado${t.atrasada ? " atrasada" : ""}`, t.prazo_texto));
    tarefas.appendChild(linha);
  }
  if (!tarefas.children.length) tarefas.appendChild(elemento("li", "passou", "Nenhuma tarefa pendente."));
}

// ------------------------------------------------------------------ pareamento
function nomeDoAparelho() {
  const agente = navigator.userAgent;
  return /iPhone/.test(agente) ? "iPhone" : /iPad/.test(agente) ? "iPad" : /Android/.test(agente) ? "Android" : "Navegador";
}

async function parear(evento) {
  evento.preventDefault();
  destravarAudio();
  const codigo = $("campo-codigo").value.replace(/\D/g, "");
  if (codigo.length !== 6) {
    $("aviso-parear").textContent = "O código tem 6 números.";
    return;
  }
  $("aviso-parear").textContent = "";
  try {
    const resposta = await fetch("/api/parear", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ codigo, nome: nomeDoAparelho() }),
    });
    const corpo = await resposta.json();
    if (!resposta.ok) {
      som("erro");
      $("aviso-parear").textContent = corpo.erro || "Não deu certo. Peça um código novo.";
      return;
    }
    estado.chave = corpo.chave;
    gravar(CHAVE, estado.chave);
    $("campo-codigo").value = "";
    som("ligando");
    conectar();
  } catch {
    $("aviso-parear").textContent = "Não alcancei o PC. Confira o Tailscale no iPhone.";
  }
}

async function desconectarEste() {
  try { await api("/api/esquecer", { method: "POST" }); } catch { /* sem conexão: esquece só aqui */ }
  esquecerChave();
}

// ------------------------------------------------------------------ abertura: "sistema ligando"
async function abertura() {
  let pular = false;
  try {
    if (!estado.abertura || !estado.chave || sessionStorage.getItem("beatriz.abriu")) return;
    sessionStorage.setItem("beatriz.abriu", "1");
  } catch { return; }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const tela = $("abertura");
  const linhas = $("abertura-linhas");
  tela.hidden = false;
  tela.addEventListener("click", () => { pular = true; }, { once: true });
  const linha = (texto) => { const li = elemento("li", "", texto); linhas.appendChild(li); return li; };
  const passos = [["Iniciando sistema", 450], ["Canal seguro · Tailscale", 450]];
  for (const [texto, tempo] of passos) {
    if (pular) break;
    const li = linha(texto);
    await esperar(tempo);
    li.classList.add("ok");
  }
  if (!pular) {
    const li = linha("Procurando o PC");
    const limite = Date.now() + 2500;
    while (estado.online === null && Date.now() < limite && !pular) await esperar(100);
    if (estado.online) {
      li.classList.add("ok");
      linha("Beatriz online");
    } else if (!pular) {
      li.textContent = "PC fora do alcance";
    }
    await esperar(pular ? 0 : 500);
  }
  tela.classList.add("saindo");
  setTimeout(() => { tela.hidden = true; }, 520);
}

// ------------------------------------------------------------------ avisos, toques e animações
let relogioDoAviso = null;
function aviso(texto, erro = false, aoTocar = null) {
  const caixa = $("toast");
  caixa.textContent = texto;
  caixa.classList.toggle("erro", erro);
  caixa.hidden = false;
  caixa.onclick = aoTocar ? () => { caixa.hidden = true; aoTocar(); } : () => { caixa.hidden = true; };
  clearTimeout(relogioDoAviso);
  relogioDoAviso = setTimeout(() => { caixa.hidden = true; }, erro ? 5000 : 3500);
}

function aplicarAtrasos(raiz = document) {
  // O atraso de entrada de cada item (--i) vai pelo JavaScript: a política de segurança da página não aceita style="" no HTML.
  raiz.querySelectorAll("[data-i]").forEach((el) => el.style.setProperty("--i", el.dataset.i));
}

function ondaDeLuz(evento) {
  const alvo = evento.target.closest(".bloco, .widget, .sugestao, .botao, .anexo, .tecla, .segmento");
  if (!alvo) return;
  const superficie = alvo.classList.contains("bloco") ? alvo.querySelector(".bloco-icone") : alvo;
  const caixa = superficie.getBoundingClientRect();
  const onda = elemento("span", "onda");
  onda.style.left = `${evento.clientX - caixa.left}px`;
  onda.style.top = `${evento.clientY - caixa.top}px`;
  if (getComputedStyle(superficie).position === "static") superficie.style.position = "relative";
  superficie.style.overflow = "hidden";
  superficie.appendChild(onda);
  setTimeout(() => onda.remove(), 600);
}

// ------------------------------------------------------------------ editar: fotos, vídeos e conversões (no PC, pelos apps da ArtCraft)
// A prévia da foto e do vídeo usa os filtros do navegador (aproximada, na hora); o resultado de verdade vem do PC: a foto em
// ~2 s, o vídeo em segundo plano (o celular pergunta pela situação e ele também chega na conversa).
const FILTROS_DA_EDICAO = [
  ["", "Original", ""],
  ["pb", "P&B", "grayscale(1)"],
  ["sepia", "Sépia", "sepia(0.9)"],
  ["vintage", "Antiga", "sepia(0.35) contrast(0.9) saturate(0.8) brightness(1.06)"],
  ["cinema", "Cinema", "contrast(1.15) saturate(0.85) hue-rotate(-10deg)"],
  ["quente", "Quente", "sepia(0.25) saturate(1.25) brightness(1.03)"],
  ["frio", "Frio", "hue-rotate(12deg) saturate(0.9) brightness(1.02)"],
  ["vivo", "Vivas", "saturate(1.6) contrast(1.05)"],
  ["dramatico", "Drama", "contrast(1.35) brightness(0.85) saturate(0.8)"],
  ["suave", "Suave", "contrast(0.85) saturate(0.75) brightness(1.08)"],
];
const SO_PARA_FOTO = new Set(["vivo", "suave"]);  // o FilmCraft não tem esses dois
const RECORTES = [["", "Original", 0], ["1x1", "Quadrado", 1], ["9x16", "Stories", 9 / 16], ["16x9", "16:9", 16 / 9], ["4x5", "4:5", 4 / 5]];
const TAMANHOS = [[0, "Original"], [2560, "Grande"], [1600, "Médio"], [1080, "Leve"]];
const VELOCIDADES = [[50, "0,5×"], [100, "1×"], [200, "2×"], [400, "4×"]];
const SAIDAS_DO_VIDEO = [[".mp4", "Vídeo"], [".gif", "GIF"], [".aac", "Só o som"], [".jpg", "Imagem"]];
const EXTENSOES_DE_VIDEO = [".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".3gp"];
const CONVERSOES = [
  [[".docx", ".doc", ".odt", ".rtf", ".md", ".html", ".htm", ".txt"], [[".pdf", "PDF"], [".png", "Imagens"], [".docx", "Word"], [".txt", "Texto"], [".html", "HTML"]], "WordCraft"],
  [[".pptx"], [[".pdf", "PDF"], [".png", "Imagens"], [".gif", "GIF"], [".txt", "Texto"]], "DeckCraft"],
  [[".xlsx", ".csv", ".tsv"], [[".pdf", "PDF"], [".xlsx", "Excel"], [".csv", "CSV"], [".html", "HTML"]], "GridCraft"],
  [[".pdf"], [[".png", "Imagens"], [".txt", "Texto"]], "PdfCraft"],
  [[".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".psd", ".bmp"], [[".pdf", "PDF"], [".png", "PNG"], [".jpg", "JPG"], [".webp", "WebP"], [".psd", "PSD"]], "PhotoCraft"],
  [[".svg", ".ai", ".eps"], [[".pdf", "PDF"], [".png", "PNG"], [".svg", "SVG"]], "VectorCraft"],
  [[".dxf", ".dwg"], [[".pdf", "PDF"], [".png", "PNG"], [".svg", "SVG"]], "CADCraft"],
  [EXTENSOES_DE_VIDEO, [[".mp4", "MP4"], [".gif", "GIF"], [".aac", "Só o som"]], "FilmCraft"],
];
const LIMITE_EDICAO_MB = 1024;
const fotoPadrao = () => ({ filtro: "", automatico: false, luz: 0, contraste: 0, cor: 0, nitidez: 0, girar: 0, espelhar: false, recorte: "", tamanho: 0 });
const videoPadrao = () => ({ inicio: 0, fim: null, duracao: 0, velocidade: 100, filtro: "", sem_som: false, metade: false, saida: ".mp4" });
const editor = { modo: "foto", arquivo: null, url: "", tipo: "", foto: fotoPadrao(), video: videoPadrao(), formato: "", miniatura: "", tarefa: null, enviando: false };

function extensaoDe(nome) {
  const ponto = (nome || "").lastIndexOf(".");
  return ponto < 0 ? "" : nome.slice(ponto).toLowerCase();
}

function tipoDoArquivo(arquivo) {
  if ((arquivo.type || "").startsWith("video/") || EXTENSOES_DE_VIDEO.includes(extensaoDe(arquivo.name))) return "video";
  if ((arquivo.type || "").startsWith("image/") && !/svg/.test(arquivo.type)) return "foto";
  return "outro";
}

function tempoCurto(segundos) {
  const s = Math.max(0, Math.round(segundos || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function abrirEditor(modo) {
  mostrarTela("editar");
  trocarModo(modo);
}

function trocarModo(modo) {
  editor.modo = modo;
  $("editar-titulo").textContent = { foto: "Fotos", video: "Vídeos", converter: "Converter" }[modo];
  document.querySelectorAll("#editar-modos [data-modo]").forEach((b) => b.classList.toggle("ativo", b.dataset.modo === modo));
  $("editar-foto").hidden = modo !== "foto";
  $("editar-video").hidden = modo !== "video";
  $("editar-converter").hidden = modo !== "converter";
  $("editar-escolher-texto").textContent = { foto: "Escolher uma foto", video: "Escolher um vídeo", converter: "Escolher um arquivo" }[modo];
  $("editar-camera").hidden = modo === "converter";
  $("editar-zerar").hidden = modo === "converter";
  // O arquivo escolhido continua se servir para o modo novo (uma foto também se converte; um vídeo também)
  if (editor.arquivo && modo !== "converter" && editor.tipo !== modo) limparArquivo();
  desenharEditor();
}

function escolherArquivo(camera = false) {
  const entrada = $("editar-arquivo-escolha");
  entrada.accept = { foto: "image/*", video: "video/*", converter: "" }[editor.modo];
  if (camera) entrada.setAttribute("capture", "environment"); else entrada.removeAttribute("capture");
  entrada.click();
}

function limparArquivo() {
  if (editor.url) URL.revokeObjectURL(editor.url);
  Object.assign(editor, { arquivo: null, url: "", tipo: "", miniatura: "", formato: "", foto: fotoPadrao(), video: videoPadrao() });
  $("previa-foto").removeAttribute("src");
  $("previa-video").removeAttribute("src");
  $("editar-resultado").hidden = true;
  mostrarEstadoDaEdicao("");
}

function receberArquivoParaEditar(arquivo) {
  if (!arquivo) return;
  if (arquivo.size > LIMITE_EDICAO_MB * 1024 * 1024) return falhou(`${arquivo.name} passa de ${LIMITE_EDICAO_MB} MB.`);
  limparArquivo();
  editor.arquivo = arquivo;
  editor.tipo = tipoDoArquivo(arquivo);
  if (editor.modo !== "converter" && editor.tipo !== "outro" && editor.tipo !== editor.modo) trocarModo(editor.tipo);
  if (editor.modo !== "converter" && editor.tipo === "outro") {
    trocarModo("converter");
    aviso("Esse arquivo não é foto nem vídeo: abri a conversão.");
  }
  if (editor.tipo !== "outro") editor.url = URL.createObjectURL(arquivo);
  const foto = $("previa-foto"), video = $("previa-video");
  if (editor.tipo === "foto") {
    foto.onload = () => { editor.miniatura = miniaturaDe(foto); desenharEditor(); };
    foto.src = editor.url;
  } else if (editor.tipo === "video") {
    video.onloadedmetadata = () => {
      editor.video.duracao = video.duration || 0;
      video.currentTime = Math.min(0.5, (video.duration || 1) / 2);
      desenharEditor();
    };
    video.onseeked = () => { if (!editor.miniatura) { editor.miniatura = miniaturaDe(video); desenharEditor(); } };
    video.src = editor.url;
  }
  som("abrir");
  desenharEditor();
}

function miniaturaDe(midia) {
  // Uma imagem pequena da foto (ou de um quadro do vídeo) para as amostras dos filtros
  try {
    const largura = midia.videoWidth || midia.naturalWidth, altura = midia.videoHeight || midia.naturalHeight;
    if (!largura || !altura) return "";
    const lado = Math.min(largura, altura), tela = document.createElement("canvas");
    tela.width = tela.height = 120;
    tela.getContext("2d").drawImage(midia, (largura - lado) / 2, (altura - lado) / 2, lado, lado, 0, 0, 120, 120);
    return tela.toDataURL("image/jpeg", 0.7);
  } catch { return ""; }
}

function filtroCss(opcoes, foto) {
  const base = (FILTROS_DA_EDICAO.find(([chave]) => chave === opcoes.filtro) || ["", "", ""])[2];
  if (!foto) return base || "none";
  const partes = [base];
  if (opcoes.automatico) partes.push("contrast(1.08) saturate(1.1)");
  if (opcoes.luz) partes.push(`brightness(${(1 + opcoes.luz * 0.32).toFixed(2)})`);
  if (opcoes.contraste) partes.push(`contrast(${(1 + opcoes.contraste / 100).toFixed(2)})`);
  if (opcoes.cor) partes.push(`saturate(${(1 + opcoes.cor / 100).toFixed(2)})`);
  return partes.filter(Boolean).join(" ") || "none";
}

function desenharFiltros(id, opcoes, foto) {
  const caixa = $(id);
  caixa.textContent = "";
  for (const [chave, nome, css] of FILTROS_DA_EDICAO) {
    if (!foto && SO_PARA_FOTO.has(chave)) continue;
    const botao = elemento("button", `filtro${chave === opcoes.filtro ? " ativo" : ""}`);
    botao.type = "button";
    const amostra = elemento("span", "filtro-amostra");
    if (editor.miniatura) amostra.style.backgroundImage = `url("${editor.miniatura}")`;
    amostra.style.filter = css || "none";
    botao.append(amostra, elemento("span", "", nome));
    botao.addEventListener("click", () => { opcoes.filtro = chave; desenharEditor(); });
    caixa.appendChild(botao);
  }
}

function desenharEditor() {
  const tem = !!editor.arquivo;
  $("editar-escolher").hidden = tem;
  $("previa-moldura").hidden = !tem;
  $("editar-trocar").hidden = !tem;
  $("previa-foto").hidden = editor.tipo !== "foto";
  $("previa-video").hidden = editor.tipo !== "video";
  $("previa-arquivo").hidden = !tem || editor.tipo !== "outro";
  $("previa-arquivo-nome").textContent = tem ? editor.arquivo.name : "";
  $("editar-info").textContent = tem ? `${editor.arquivo.name.slice(0, 26)} · ${tamanho(editor.arquivo.size)}` : "nenhum arquivo";
  $("editar-rotulo").textContent = editor.modo === "converter" ? "Arquivo" : "Prévia";
  if (editor.modo === "foto") desenharFoto();
  if (editor.modo === "video") desenharVideo();
  if (editor.modo === "converter") desenharConversao();
  const pronto = tem && !editor.enviando && (editor.modo !== "converter" || !!editor.formato);
  $("editar-enviar").disabled = !pronto;
  $("editar-enviar-texto").textContent = editor.enviando ? "Enviando…" : editor.modo === "converter" ? "Converter no PC" : "Editar no PC";
}

function desenharFoto() {
  const f = editor.foto, imagem = $("previa-foto"), moldura = $("previa-moldura");
  desenharFiltros("foto-filtros", f, true);
  $("foto-auto").checked = f.automatico;
  for (const [nome, valor, texto] of [["luz", f.luz, f.luz ? (f.luz > 0 ? "+" : "") + f.luz.toFixed(1) : "0"],
                                      ["contraste", f.contraste, String(f.contraste)], ["cor", f.cor, String(f.cor)],
                                      ["nitidez", f.nitidez, String(f.nitidez)]]) {
    $(`foto-${nome}`).value = valor;
    $(`foto-${nome}-valor`).textContent = texto;
  }
  $("foto-espelhar").setAttribute("aria-pressed", String(f.espelhar));
  segmentos("foto-recortes", RECORTES.map((r) => r[0]), Object.fromEntries(RECORTES.map((r) => [r[0], r[1]])), f.recorte,
            (r) => { f.recorte = r; desenharEditor(); });
  segmentos("foto-tamanhos", TAMANHOS.map((t) => String(t[0])), Object.fromEntries(TAMANHOS.map((t) => [String(t[0]), t[1]])),
            String(f.tamanho), (t) => { f.tamanho = Number(t); desenharEditor(); });
  if (editor.tipo !== "foto") return;
  imagem.style.filter = filtroCss(f, true);
  const proporcao = (RECORTES.find((r) => r[0] === f.recorte) || [0, 0, 0])[2];
  moldura.classList.toggle("recortada", !!proporcao);
  moldura.style.aspectRatio = proporcao ? String(proporcao) : "";
  // Girada de lado, a foto troca largura por altura: encolhe para caber no quadro
  let escala = 1;
  if (f.girar % 2 && !proporcao && imagem.clientWidth && imagem.clientHeight) {
    escala = Math.min(1, moldura.clientWidth / imagem.clientHeight, (window.innerHeight * 0.52) / imagem.clientWidth);
  }
  imagem.style.transform = `rotate(${f.girar * 90}deg) scaleX(${f.espelhar ? -1 : 1}) scale(${escala.toFixed(3)})`;
}

function desenharVideo() {
  const v = editor.video, video = $("previa-video");
  desenharFiltros("video-filtros", v, false);
  if (SO_PARA_FOTO.has(v.filtro)) v.filtro = "";
  video.style.filter = filtroCss(v, false);
  video.playbackRate = v.velocidade / 100;
  $("video-duracao").textContent = v.duracao ? tempoCurto(v.duracao) : "--";
  $("video-inicio-texto").textContent = tempoCurto(v.inicio);
  $("video-fim-texto").textContent = v.fim == null ? (v.duracao ? tempoCurto(v.duracao) : "fim") : tempoCurto(v.fim);
  const total = v.duracao || 1, fim = v.fim == null ? total : v.fim;
  $("trecho-escolhido").style.left = `${(v.inicio / total) * 100}%`;
  $("trecho-escolhido").style.right = `${100 - (fim / total) * 100}%`;
  $("video-sem-som").checked = v.sem_som;
  $("video-metade").checked = v.metade;
  segmentos("video-velocidades", VELOCIDADES.map((x) => String(x[0])), Object.fromEntries(VELOCIDADES.map((x) => [String(x[0]), x[1]])),
            String(v.velocidade), (x) => { v.velocidade = Number(x); desenharEditor(); });
  segmentos("video-saidas", SAIDAS_DO_VIDEO.map((x) => x[0]), Object.fromEntries(SAIDAS_DO_VIDEO), v.saida,
            (x) => { v.saida = x; desenharEditor(); if (x === ".jpg") aviso("A imagem sai do momento marcado como início."); });
}

function desenharConversao() {
  const ext = editor.arquivo ? extensaoDe(editor.arquivo.name) : "";
  const regra = CONVERSOES.find(([entradas]) => entradas.includes(ext));
  const mesma = (formato) => formato === ext || (formato === ".jpg" && ext === ".jpeg");
  const opcoes = regra ? regra[1].filter(([formato]) => !mesma(formato)) : [];
  if (!opcoes.some(([formato]) => formato === editor.formato)) editor.formato = "";
  segmentos("converter-formatos", opcoes.map((o) => o[0]), Object.fromEntries(opcoes), editor.formato,
            (formato) => { editor.formato = formato; desenharEditor(); });
  $("converter-app").textContent = regra ? regra[2] : "--";
  $("converter-ajuda").textContent = !editor.arquivo ? "Escolha um Word, PDF, apresentação, planilha, imagem, desenho ou vídeo."
    : regra ? "Escolha o formato. O arquivo pronto volta para você baixar." : `Arquivos ${ext || "sem extensão"} eu ainda não converto.`;
}

function pedidoDaEdicao() {
  if (editor.modo === "foto") return { tipo: "foto", ...editor.foto };
  if (editor.modo === "converter") return { tipo: "converter", formato: editor.formato };
  const v = editor.video;
  const inteiro = v.inicio <= 0.05 && (v.fim == null || v.fim >= v.duracao - 0.05);
  return { tipo: "video", inicio: inteiro && v.saida !== ".jpg" ? null : v.inicio, fim: inteiro ? null : v.fim, sem_som: v.sem_som,
           metade: v.metade, velocidade: v.velocidade, filtro: v.filtro, saida: v.saida };
}

function enviarComProgresso(arquivo, pedido, aoProgredir) {
  // XMLHttpRequest, e não fetch: um vídeo grande leva um tempo para subir, e só ele diz quanto já foi
  return new Promise((pronto, falha) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/editar");
    xhr.setRequestHeader("X-Chave-Beatriz", estado.chave);
    xhr.setRequestHeader("X-Nome", encodeURIComponent(arquivo.name));
    xhr.setRequestHeader("X-Edicao", encodeURIComponent(JSON.stringify(pedido)));
    xhr.setRequestHeader("Content-Type", arquivo.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) aoProgredir(e.loaded / e.total); };
    xhr.onload = () => {
      let corpo = {};
      try { corpo = JSON.parse(xhr.responseText || "{}"); } catch { /* sem corpo */ }
      if (xhr.status === 401) { esquecerChave(); return falha(new Error("nao_pareado")); }
      if (xhr.status >= 400) return falha(new Error(corpo.erro || "Algo deu errado no PC."));
      pronto(corpo);
    };
    xhr.onerror = () => falha(new Error("Não consegui falar com o PC."));
    xhr.send(arquivo);
  });
}

function mostrarEstadoDaEdicao(texto, erro = false, trabalhando = false, fracao = null) {
  $("editar-estado").textContent = texto;
  $("editar-estado").classList.toggle("erro", erro);
  const barra = $("editar-progresso");
  barra.hidden = !trabalhando;
  barra.classList.toggle("indo", trabalhando && fracao == null);
  $("editar-progresso-barra").style.width = fracao == null ? "" : `${Math.round(fracao * 100)}%`;
}

async function editarNoPc() {
  if (!editor.arquivo || editor.enviando) return;
  editor.enviando = true;
  $("editar-resultado").hidden = true;
  desenharEditor();
  som("enviar");
  mostrarEstadoDaEdicao("Mandando ao PC…", false, true, 0);
  try {
    const resposta = await enviarComProgresso(editor.arquivo, pedidoDaEdicao(), (fracao) => {
      mostrarEstadoDaEdicao(fracao < 1 ? `Mandando ao PC… ${Math.round(fracao * 100)}%` : "No PC: editando…", false, true, fracao < 1 ? fracao : null);
    });
    await acompanharEdicao(resposta);
  } catch (erro) {
    if (erro.message !== "nao_pareado") {
      som("erro");
      mostrarEstadoDaEdicao(erro.message, true);
    }
  } finally {
    editor.enviando = false;
    desenharEditor();
  }
}

async function acompanharEdicao(situacao) {
  while (!situacao.pronto) {
    mostrarEstadoDaEdicao(situacao.texto || "Editando no PC…", false, true);
    await esperar(2000);
    situacao = await (await api(`/api/editar/${encodeURIComponent(situacao.tarefa)}`)).json();
  }
  if (!situacao.ok) {
    som("erro");
    return mostrarEstadoDaEdicao(situacao.texto || "Não deu certo.", true);
  }
  som("chegou");
  celebrar();
  mostrarEstadoDaEdicao(situacao.texto || "Pronto.");
  await mostrarResultado(situacao.arquivos || []);
}

async function mostrarResultado(arquivos) {
  const caixa = $("resultado-previa");
  caixa.textContent = "";
  $("resultado-anexos").textContent = "";
  if (!arquivos.length) return;
  $("resultado-info").textContent = arquivos.length > 1 ? `${arquivos.length} arquivos` : tamanho(arquivos[0].tamanho);
  $("resultado-anexos").appendChild(caixaDeAnexos(arquivos));
  $("editar-resultado").hidden = false;
  const primeiro = arquivos[0], ext = extensaoDe(primeiro.nome);
  const tag = [".jpg", ".jpeg", ".png", ".webp", ".gif"].includes(ext) ? "img" : [".mp4", ".mov"].includes(ext) ? "video"
    : [".aac", ".m4a", ".wav"].includes(ext) ? "audio" : "";
  if (!tag) return;
  try {
    const resposta = await api(`/api/arquivo/${encodeURIComponent(primeiro.id)}`);
    const midia = document.createElement(tag);
    midia.src = URL.createObjectURL(await resposta.blob());
    if (tag !== "img") { midia.controls = true; midia.setAttribute("playsinline", ""); }
    if (tag === "img") midia.alt = primeiro.nome;
    caixa.appendChild(midia);
    $("editar-resultado").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch { /* sem prévia: os botões de baixar continuam */ }
}

function ligarEditor() {
  document.querySelectorAll("[data-editar]").forEach((b) => b.addEventListener("click", () => { abrirEditor(b.dataset.editar); som("abrir"); }));
  document.querySelectorAll("#editar-modos [data-modo]").forEach((b) => b.addEventListener("click", () => trocarModo(b.dataset.modo)));
  const entrada = document.createElement("input");
  entrada.type = "file";
  entrada.id = "editar-arquivo-escolha";
  entrada.hidden = true;
  document.body.appendChild(entrada);
  entrada.addEventListener("change", (e) => { receberArquivoParaEditar(e.target.files[0]); e.target.value = ""; });
  $("editar-escolher").addEventListener("click", () => escolherArquivo());
  $("editar-outro").addEventListener("click", () => escolherArquivo());
  $("editar-camera").addEventListener("click", () => escolherArquivo(true));
  $("editar-zerar").addEventListener("click", () => { editor.foto = fotoPadrao(); Object.assign(editor.video, { ...videoPadrao(), duracao: editor.video.duracao }); desenharEditor(); });
  $("foto-auto").addEventListener("change", (e) => { editor.foto.automatico = e.target.checked; desenharEditor(); });
  for (const nome of ["luz", "contraste", "cor", "nitidez"]) {
    $(`foto-${nome}`).addEventListener("input", (e) => { editor.foto[nome] = Number(e.target.value); desenharEditor(); });
  }
  $("foto-girar-esquerda").addEventListener("click", () => { editor.foto.girar = (editor.foto.girar + 3) % 4; desenharEditor(); });
  $("foto-girar-direita").addEventListener("click", () => { editor.foto.girar = (editor.foto.girar + 1) % 4; desenharEditor(); });
  $("foto-espelhar").addEventListener("click", () => { editor.foto.espelhar = !editor.foto.espelhar; desenharEditor(); });
  const video = $("previa-video");
  $("video-marcar-inicio").addEventListener("click", () => {
    const v = editor.video;
    v.inicio = video.currentTime || 0;
    if (v.fim != null && v.fim <= v.inicio) v.fim = null;
    desenharEditor();
  });
  $("video-marcar-fim").addEventListener("click", () => {
    const v = editor.video, agora = video.currentTime || 0;
    if (agora <= v.inicio) return aviso("O fim tem de vir depois do início.", true);
    v.fim = agora;
    desenharEditor();
  });
  $("video-inteiro").addEventListener("click", () => { editor.video.inicio = 0; editor.video.fim = null; desenharEditor(); });
  $("trecho-barra").addEventListener("click", (e) => {
    const caixa = e.currentTarget.getBoundingClientRect();
    if (editor.video.duracao) video.currentTime = ((e.clientX - caixa.left) / caixa.width) * editor.video.duracao;
  });
  $("video-sem-som").addEventListener("change", (e) => { editor.video.sem_som = e.target.checked; });
  $("video-metade").addEventListener("change", (e) => { editor.video.metade = e.target.checked; });
  $("editar-enviar").addEventListener("click", editarNoPc);
  window.addEventListener("resize", () => { if (estado.tela === "editar" && editor.modo === "foto") desenharFoto(); });
  trocarModo("foto");
}

// ------------------------------------------------------------------ começo
function conectar() {
  estado.ultimo = 0;
  estado.historico = true;
  estado.mensagens = [];
  $("mensagens").textContent = "";
  estado.tela = "";
  mostrarTela("inicio");
  lacoDeMensagens();
}

function iniciar() {
  aplicarAtrasos();
  atualizarVisor();
  setInterval(atualizarVisor, 10000);
  $("botao-som").setAttribute("aria-pressed", String(estado.falarAqui));
  $("opcao-fala").checked = estado.falarAqui;
  $("opcao-sons").checked = estado.sons;
  $("opcao-abertura").checked = estado.abertura;
  $("botao-som").addEventListener("click", () => definirFalarAqui(!estado.falarAqui));
  $("opcao-fala").addEventListener("change", (e) => definirFalarAqui(e.target.checked, false));
  $("opcao-sons").addEventListener("change", (e) => { estado.sons = e.target.checked; gravar(OPCOES.sons, estado.sons ? "" : "0"); som("toque"); });
  $("opcao-abertura").addEventListener("change", (e) => { estado.abertura = e.target.checked; gravar(OPCOES.abertura, estado.abertura ? "" : "0"); });

  // Primeiro toque: libera o som (exigência do iPhone); todo toque ganha luz e um clique discreto.
  document.addEventListener("pointerdown", (e) => {
    destravarAudio();
    ondaDeLuz(e);
    if (e.target.closest("button, .chave-opcao") && !e.target.closest("#botao-microfone")) som("toque");
  }, { passive: true });

  document.querySelectorAll("[data-acao]").forEach((b) => b.addEventListener("click", () => ATALHOS[b.dataset.acao]()));
  // Na tela Estudos, os atalhos de pedido levam à conversa com o começo da frase escrito.
  document.querySelectorAll("#tela-estudos [data-pedido]").forEach((b) => b.addEventListener("click", () => {
    mostrarTela("conversa");
    $("campo").value = b.dataset.pedido;
    $("campo").focus();
  }));
  document.querySelectorAll("[data-tela]").forEach((b) => b.addEventListener("click", () => { mostrarTela(b.dataset.tela); som("abrir"); }));

  $("form-pedido").addEventListener("submit", (e) => { e.preventDefault(); enviarPedido($("campo").value); });
  $("form-agenda").addEventListener("submit", (e) => {
    e.preventDefault();
    const texto = $("campo-agenda").value.trim();
    if (!texto) return;
    $("campo-agenda").value = "";
    enviarPedido(/^(marca|agenda|me lembra|lembra)/i.test(texto) ? texto : `marca ${texto}`);
  });
  $("semana-antes").addEventListener("click", () => { estado.semana -= 1; carregarAgenda(); });
  $("semana-depois").addEventListener("click", () => { estado.semana += 1; carregarAgenda(); });
  for (const id of ["escolher-arquivo", "tirar-foto"]) {
    $(id).addEventListener("change", async (e) => {
      for (const arquivo of Array.from(e.target.files)) await mandarArquivo(arquivo);
      e.target.value = "";
    });
  }
  $("biblio-busca").addEventListener("input", mostrarBiblioteca);
  $("biblio-arquivo").addEventListener("change", async (e) => {
    for (const arquivo of Array.from(e.target.files)) await mandarParaBiblioteca(arquivo);
    e.target.value = "";
    biblio.abertas.add(biblio.destinoDoUpload);
    carregarBiblioteca();
  });
  $("form-parear").addEventListener("submit", parear);
  $("campo-codigo").addEventListener("input", (e) => {
    const numeros = e.target.value.replace(/\D/g, "").slice(0, 6);
    e.target.value = numeros.length > 3 ? `${numeros.slice(0, 3)} ${numeros.slice(3)}` : numeros;
  });
  $("botao-esquecer").addEventListener("click", desconectarEste);

  // TV e ar: cada botão manda o seu pedido ao controle remoto
  document.querySelectorAll("[data-tv]").forEach((b) => b.addEventListener("click", () => {
    const nome = b.dataset.tv;
    apertarControle({ aparelho: "tv", acao: ["ligar", "desligar"].includes(nome) ? nome : "botao", botao: nome }, b);
  }));
  document.querySelectorAll("[data-digito]").forEach((b) => b.addEventListener("click", () => digitarCanal(controle.canal + b.dataset.digito)));
  $("tv-apagar").addEventListener("click", () => digitarCanal(controle.canal.slice(0, -1)));
  $("tv-ir").addEventListener("click", () => {
    if (!controle.canal) return;
    apertarControle({ aparelho: "tv", acao: "canal", canal: controle.canal }, $("tv-ir"));
    digitarCanal("");
  });
  $("ar-menos").addEventListener("click", () => mudarAr({ temperatura: controle.alvo.temperatura - 1 }));
  $("ar-mais").addEventListener("click", () => mudarAr({ temperatura: controle.alvo.temperatura + 1 }));
  $("ar-ligar").addEventListener("click", () => mandarAr(true));
  $("casa-ar-botao").addEventListener("click", () => tocarCasa("ar"));
  $("casa-tv-botao").addEventListener("click", () => tocarCasa("tv"));
  $("w-casa").addEventListener("click", (e) => { if (!e.target.closest("button")) { mostrarTela("controle"); som("abrir"); } });
  $("ar-desligar").addEventListener("click", () => {
    clearTimeout(controle.relogio);
    controle.relogio = null;
    apertarControle({ aparelho: "ar", acao: "desligar" }, $("ar-desligar"));
  });
  ligarEditor();

  const microfone = $("botao-microfone");
  microfone.addEventListener("pointerdown", comecarGravacao);
  microfone.addEventListener("pointermove", moverGravacao);
  microfone.addEventListener("pointerup", terminarGravacao);
  microfone.addEventListener("pointercancel", () => { gravacao.cancelar = true; terminarGravacao(); });
  microfone.addEventListener("contextmenu", (e) => e.preventDefault());

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && estado.chave) {
      lacoDeMensagens();
      carregarPainel();
    }
  });
  setInterval(() => {
    if (document.hidden || !estado.chave || estado.tela === "conversa") return;
    carregarPainel();
    if (["inicio", "controle"].includes(estado.tela) && !controle.relogio) carregarControle();  // "liga o ar" pela voz também aparece
  }, 20000);

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  if (estado.chave) {
    abertura();
    conectar();
  } else {
    mostrarTela("parear");
  }
}

iniciar();
