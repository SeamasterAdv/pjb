/* Painel da Beatriz: preto e branco, a esfera em rede no centro (voz dela) e a montanha de pontos
 * embaixo (sua voz), desenhadas pelo painel_visual.js; o branco vira dourado quando uma missão é cumprida.
 *
 * Fala com o Python pela ponte do pywebview (window.pywebview.api): busca os eventos novos a cada
 * 250 ms e o estado geral a cada 1,5 s. Aberto fora do pywebview (num navegador comum), entra no
 * modo demonstração, com dados de exemplo, para dar para ver e ajustar o visual.
 */
"use strict";

const $ = (seletor) => document.querySelector(seletor);
const CARACTERES_POR_SEGUNDO = 14; // ritmo aproximado da voz da Beatriz

let api = null;
let ultimoCartao = null; // {texto, fala}: a resposta com o mesmo texto do cartão não se repete no registro
let tarefasConhecidas = null; // id -> status, para perceber missão nova e missão concluída
let estadoAtual = null;
let historicoEntrada = [];
let posicaoHistorico = -1;

/* ------------------------------------------------------------------ sons (Web Audio, sem arquivos) */
const sons = {
  ligado: lerPreferencia("sons", true),
  contexto: null,
  tocar(tipo) {
    if (!this.ligado) return;
    try {
      this.contexto = this.contexto || new AudioContext();
      const c = this.contexto, t = c.currentTime;
      const notas = tipo === "acender" ? [[523.25, 0], [659.25, .09], [783.99, .18], [1046.5, .27]]
                  : tipo === "erro" ? [[220, 0], [196, .12]]
                  : [[1318.5, 0], [1975.5, .07]]; // "ping" das janelas do Sistema
      for (const [freq, atraso] of notas) {
        const osc = c.createOscillator(), ganho = c.createGain();
        osc.type = tipo === "acender" ? "triangle" : "sine";
        osc.frequency.value = freq;
        ganho.gain.setValueAtTime(0, t + atraso);
        ganho.gain.linearRampToValueAtTime(tipo === "acender" ? .05 : .035, t + atraso + .01);
        ganho.gain.exponentialRampToValueAtTime(.0001, t + atraso + (tipo === "acender" ? 1.2 : .45));
        osc.connect(ganho).connect(c.destination);
        osc.start(t + atraso);
        osc.stop(t + atraso + 1.3);
      }
    } catch (erro) { /* sem áudio: segue só o visual */ }
  },
};

function lerPreferencia(chave, padrao) {
  try { const v = localStorage.getItem("beatriz." + chave); return v === null ? padrao : JSON.parse(v); }
  catch (erro) { return padrao; }
}
function gravarPreferencia(chave, valor) {
  try { localStorage.setItem("beatriz." + chave, JSON.stringify(valor)); } catch (erro) { /* sem armazenamento */ }
}

/* ------------------------------------------------------------------ relógio */
function iniciarRelogio() {
  const dias = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
  const atualizar = () => {
    const agora = new Date();
    $("#hora").textContent = agora.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    $("#data").textContent = `${dias[agora.getDay()]} · ${agora.toLocaleDateString("pt-BR")}`;
  };
  atualizar(); setInterval(atualizar, 1000);
}

/* ------------------------------------------------------------------ o estado (esfera, montanha e o dourado) */
const ROTULOS_DO_ESTADO = { repouso: "em espera", ouvindo: "ouvindo", pensando: "processando",
                            falando: "respondendo", vitoria: "missão cumprida" };
const DURACAO_VITORIA = 5000; // o branco fica dourado por esse tempo e volta devagar
let voltarAoRepouso = null, fimDoDourado = null;
function definirEstado(estado, duracaoMs) {
  $("#rotulo-estado").textContent = ROTULOS_DO_ESTADO[estado] || estado;
  document.body.dataset.estado = estado; // o painel_visual.js acompanha por aqui
  clearTimeout(voltarAoRepouso);
  if (duracaoMs) voltarAoRepouso = setTimeout(() => definirEstado("repouso"), duracaoMs);
}
// Missão cumprida: tudo o que é branco fica dourado (independe do estado: ela pode falar em dourado).
function celebrar() {
  document.body.dataset.vitoria = "1"; // o estilo e o painel_visual.js acompanham por aqui
  clearTimeout(fimDoDourado);
  fimDoDourado = setTimeout(() => { delete document.body.dataset.vitoria; }, DURACAO_VITORIA);
}
function duracaoDaFala(texto) { return Math.min(45000, Math.max(1800, (texto.length / CARACTERES_POR_SEGUNDO) * 1000 + 1500)); }

/* ------------------------------------------------------------------ legendas e texto letra a letra */
function digitar(elemento, texto, aoTerminar) {
  clearInterval(elemento._digitando);
  elemento.textContent = "";
  const passo = Math.max(1, Math.round(texto.length / 120)); // textos longos andam mais rápido
  let i = 0;
  elemento._digitando = setInterval(() => {
    i = Math.min(texto.length, i + passo);
    elemento.textContent = texto.slice(0, i);
    if (i >= texto.length) { clearInterval(elemento._digitando); if (aoTerminar) aoTerminar(); }
  }, 18);
}
const sumirLegenda = {};
function legenda(quem, texto, opcoes = {}) {
  const el = quem === "voce" ? $("#legenda-voce") : $("#legenda-beatriz");
  el.classList.remove("sumindo");
  el.classList.toggle("ignorada", !!opcoes.ignorada);
  const curto = texto.length > 220 ? texto.slice(0, 217).trimEnd() + "…" : texto;
  digitar(el, opcoes.ignorada ? `ouvi: “${curto}” · ${opcoes.motivo || ""}` : curto);
  clearTimeout(sumirLegenda[quem]);
  sumirLegenda[quem] = setTimeout(() => el.classList.add("sumindo"), opcoes.ignorada ? 5000 : Math.max(9000, duracaoDaFala(texto)));
}

/* ------------------------------------------------------------------ janelas do Sistema */
function janelaSistema(titulo, texto, opcoes = {}) {
  const pilha = $("#notificacoes");
  const janela = document.createElement("div");
  janela.className = "janela-sistema" + (opcoes.vitoria ? " vitoria" : "");
  janela.innerHTML = `<div class="cabecalho-janela"></div><div class="texto"></div>`;
  janela.querySelector(".cabecalho-janela").textContent = `◢ ${titulo}`;
  if (opcoes.acao) {
    const area = document.createElement("div"), botao = document.createElement("button");
    area.className = "acao"; botao.textContent = opcoes.acao.rotulo; botao.onclick = opcoes.acao.aoClicar;
    area.appendChild(botao); janela.appendChild(area);
  }
  pilha.prepend(janela);
  while (pilha.children.length > 4) pilha.lastElementChild.remove();
  setTimeout(() => digitar(janela.querySelector(".texto"), texto), 260);
  sons.tocar(opcoes.vitoria ? "acender" : opcoes.erro ? "erro" : "ping");
  setTimeout(() => { janela.classList.add("saindo"); setTimeout(() => janela.remove(), 450); }, opcoes.duracao || 8000);
}

/* ------------------------------------------------------------------ registro (gaveta) */
function abrirRegistro(abrir = true) {
  $("#registro").classList.toggle("aberto", abrir);
  document.body.classList.toggle("registro-aberto", abrir);
  $("#btn-registro").classList.toggle("ativo", abrir);
}
function horaAgora() { return new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); }
function registrar(tipo, texto, nota) {
  const msg = document.createElement("div");
  msg.className = `msg ${tipo}`;
  const quem = { voce: "VOCÊ", beatriz: "BEATRIZ", ignorada: "OUVI, MAS NÃO RESPONDI", erro: "ALERTA" }[tipo] || "";
  msg.innerHTML = `<div class="quem"></div><div class="corpo"></div>`;
  msg.querySelector(".quem").textContent = quem + (nota ? ` · ${nota}` : "");
  const hora = document.createElement("span"); hora.className = "hora"; hora.textContent = horaAgora();
  msg.querySelector(".quem").appendChild(hora);
  msg.querySelector(".corpo").textContent = texto;
  adicionarAoRegistro(msg);
}
function adicionarAoRegistro(elemento) {
  const lista = $("#mensagens");
  const noFim = lista.scrollHeight - lista.scrollTop - lista.clientHeight < 60;
  lista.appendChild(elemento);
  while (lista.children.length > 200) lista.firstElementChild.remove();
  if (noFim) lista.scrollTop = lista.scrollHeight;
}

/* ------------------------------------------------------------------ cartões ricos */
const TITULOS_SECOES = [["o que e", "O que é"], ["contexto", "Contexto"], ["pontos principais", "Pontos principais"],
  ["por que importa", "Por que importa"], ["exemplo", "Exemplo"], ["novidades", "Novidades"], ["para lembrar", "Para lembrar"]];

function cartaoResumo(ev) {
  const cartao = document.createElement("div");
  cartao.className = "cartao";
  const titulo = document.createElement("h3"); titulo.textContent = `Resumo: ${ev.tema}`; cartao.appendChild(titulo);
  const secoes = ev.secoes || {};
  TITULOS_SECOES.forEach(([chave, nome], indice) => {
    if (!secoes[chave]) return;
    const det = document.createElement("details"); det.open = indice < 2;
    const sum = document.createElement("summary"); sum.textContent = nome; det.appendChild(sum);
    if (chave === "pontos principais") {
      const ul = document.createElement("ul");
      secoes[chave].split("\n").map((l) => l.replace(/^\s*[-•*\d.)]+\s*/, "").trim()).filter(Boolean)
        .forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
      det.appendChild(ul);
    } else { const p = document.createElement("p"); p.textContent = secoes[chave]; det.appendChild(p); }
    cartao.appendChild(det);
  });
  if (!Object.keys(secoes).length) { const p = document.createElement("p"); p.textContent = ev.texto; cartao.appendChild(p); }
  if (ev.fontes && ev.fontes.length) {
    const f = document.createElement("div"); f.className = "fontes"; f.textContent = "Fontes: " + ev.fontes.join(" · "); cartao.appendChild(f);
  }
  if (ev.arquivo) {
    const botoes = document.createElement("div"), b = document.createElement("button");
    botoes.className = "botoes"; b.textContent = "Abrir o Word"; b.onclick = () => api.abrir(ev.arquivo);
    botoes.appendChild(b); cartao.appendChild(botoes);
  }
  adicionarAoRegistro(cartao);
}

function cartaoFilmes(ev) {
  const cartao = document.createElement("div");
  cartao.className = "cartao";
  const titulo = document.createElement("h3");
  titulo.textContent = ev.so_estreias ? `Estreias em ${ev.cidade}` : `Em cartaz em ${ev.cidade}${ev.genero ? ` · ${ev.genero}` : ""}`;
  cartao.appendChild(titulo);
  const grade = document.createElement("div"); grade.className = "posteres";
  for (const filme of ev.filmes || []) {
    const item = document.createElement("div"); item.className = "poster"; item.title = `Sinopse de ${filme.titulo}`;
    const img = document.createElement("img"); img.loading = "lazy"; img.alt = filme.titulo; if (filme.poster) img.src = filme.poster;
    const nome = document.createElement("span"); nome.textContent = filme.titulo;
    const info = document.createElement("small");
    info.textContent = filme.estreia_hoje ? "estreia hoje" : filme.genero; if (filme.estreia_hoje) info.className = "selo-hoje";
    item.append(img, nome, info);
    item.onclick = () => enviar(`sinopse de ${filme.titulo}`);
    grade.appendChild(item);
  }
  cartao.appendChild(grade);
  if (ev.pre_venda && ev.pre_venda.length) {
    const pv = document.createElement("div"); pv.className = "pre-venda";
    pv.textContent = "Em pré-venda: " + ev.pre_venda.map((f) => `${f.titulo} (${f.estreia})`).join(" · ");
    cartao.appendChild(pv);
  }
  adicionarAoRegistro(cartao);
}

/* ------------------------------------------------------------------ criações do Claude (no lugar da esfera) */
// "Cria uma imagem de…" (SVG, mostrado num <img>, onde nada roda) ou "cria um modelo 3D de…" (cena montada pelo
// criacao_3d.js, carregado só quando precisa). Fica até "fecha a imagem" ou o botão "× esfera".
const ROTULOS_CRIACAO = { imagem: "imagem", "3d": "modelo 3d" };
let criacaoAtual = null; // {formato, arquivo, url, visualizador}
let modulo3d = null;

function andamentoDaCriacao(ev) {
  const andamento = $("#criacao-andamento");
  andamento.hidden = !ev;
  if (ev) $("#criacao-andamento-texto").textContent = `${ROTULOS_CRIACAO[ev.formato] || ""} · ${ev.texto}`;
}

function limparPalcoDaCriacao() {
  if (!criacaoAtual) return;
  if (criacaoAtual.visualizador) criacaoAtual.visualizador.descartar();
  if (criacaoAtual.url) URL.revokeObjectURL(criacaoAtual.url);
  $("#criacao-palco").replaceChildren();
  criacaoAtual = null;
}

async function mostrarCriacao(ev) {
  andamentoDaCriacao(null);
  limparPalcoDaCriacao();
  const palco = $("#criacao-palco"), atual = { formato: ev.formato, arquivo: ev.arquivo };
  criacaoAtual = atual;
  $("#criacao-formato").textContent = ROTULOS_CRIACAO[ev.formato] || "criação";
  $("#criacao-nome").textContent = ev.titulo || "";
  $("#criacao-ia").textContent = ev.ia ? `por ${ev.ia}` : "";
  $("#criacao-abrir").hidden = !ev.arquivo;
  $("#criacao").hidden = false;
  document.body.dataset.criacao = ev.formato;
  if (ev.formato === "3d") {
    $("#criacao-dica").textContent = "arraste para girar · roda do mouse aproxima";
    palco.classList.add("carregando");
    try {
      modulo3d = modulo3d || await import("./criacao_3d.js");
      if (criacaoAtual !== atual) return; // outra criação chegou enquanto o módulo carregava
      atual.visualizador = new modulo3d.VisualizadorDeModelo(palco, ev.modelo);
      guardarGLB(atual);
    } catch (erro) {
      $("#criacao-dica").textContent = "não consegui montar o modelo neste painel";
    } finally { palco.classList.remove("carregando"); }
  } else {
    $("#criacao-dica").textContent = "";
    atual.url = URL.createObjectURL(new Blob([ev.svg || ""], { type: "image/svg+xml" }));
    const imagem = document.createElement("img");
    imagem.src = atual.url; imagem.alt = ev.titulo || "imagem criada"; imagem.draggable = false;
    palco.appendChild(imagem);
  }
}

// O modelo vira .glb ao lado do .json; "abrir o arquivo" passa a abrir o .glb (Visualizador 3D do Windows).
async function guardarGLB(atual) {
  if (!api || !api.criacao_glb || !atual.arquivo || !atual.arquivo.endsWith(".json")) return;
  try {
    const bytes = new Uint8Array(await atual.visualizador.exportarGLB());
    let binario = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binario += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    const caminho = await api.criacao_glb(atual.arquivo, btoa(binario));
    if (caminho) atual.arquivo = caminho;
  } catch (erro) { /* fica só o .json */ }
}

function fecharCriacao() {
  limparPalcoDaCriacao();
  $("#criacao").hidden = true;
  delete document.body.dataset.criacao;
}

function ligarCriacao() {
  $("#criacao-fechar").onclick = fecharCriacao;
  $("#criacao-abrir").onclick = () => { if (criacaoAtual && criacaoAtual.arquivo) api.abrir(criacaoAtual.arquivo); };
}

/* ------------------------------------------------------------------ eventos */
function tratarEvento(ev) {
  const texto = ev.texto || "";
  switch (ev.tipo) {
    case "voce":
      legenda("voce", texto); registrar("voce", texto); definirEstado("pensando"); break;
    case "pensando":
      definirEstado("pensando"); break;
    case "ouvindo": // a pessoa começou a falar (a montanha acompanha a voz)
      definirEstado("ouvindo", ev.duracao || 2500); break;
    case "ouvido":
      legenda("voce", texto); registrar("voce", texto, ev.motivo);
      definirEstado(ev.pensando ? "pensando" : "ouvindo", ev.pensando ? 0 : 2500); break;
    case "ignorado":
      legenda("voce", texto, { ignorada: true, motivo: ev.motivo }); registrar("ignorada", texto, ev.motivo); break;
    case "resposta": {
      const doCartao = ultimoCartao && ultimoCartao.texto === texto;
      const falado = doCartao ? (ultimoCartao.fala || texto) : texto;
      legenda("beatriz", falado);
      if (!doCartao && ev.mostrar !== false) registrar("beatriz", texto);
      definirEstado("falando", duracaoDaFala(falado));
      setTimeout(carregarAgendaDaTela, 300); // a resposta pode ter sido de um pedido da agenda feito por voz
      break;
    }
    case "resumo":
      ultimoCartao = { texto, fala: ev.fala }; cartaoResumo(ev); abrirRegistro();
      janelaSistema("Missão concluída", `Resumo sobre ${ev.tema}`, {
        vitoria: true, duracao: 12000,
        acao: ev.arquivo ? { rotulo: "Abrir o Word", aoClicar: () => api.abrir(ev.arquivo) } : null,
      });
      celebrar(); break;
    case "filmes":
      ultimoCartao = { texto, fala: texto }; cartaoFilmes(ev); abrirRegistro(); break;
    case "missao":
      janelaSistema(ev.titulo || "Nova missão", texto); break;
    case "sistema":
      janelaSistema(ev.titulo || "Notificação", texto, { erro: ev.erro }); break;
    case "erro":
      registrar("erro", texto); janelaSistema("Alerta", texto, { erro: true }); definirEstado("repouso"); break;
    case "lembrete":
      legenda("beatriz", texto); registrar("beatriz", texto, "lembrete");
      janelaSistema("Lembrete", texto, { duracao: 30000, acao: { rotulo: "Abrir a agenda", aoClicar: () => abrirAgenda(true) } });
      definirEstado("falando", duracaoDaFala(texto));
      if (!$("#fundo-agenda").hidden) carregarAgenda();
      carregarAgendaDaTela();
      break;
    case "criacao_iniciada":
      andamentoDaCriacao(ev); break;
    case "criacao":
      mostrarCriacao(ev); celebrar(); break;
    case "criacao_falhou":
      andamentoDaCriacao(null); break;
    case "criacao_fechar":
      fecharCriacao(); break;
    case "abrir_agenda":
      abrirAgenda(true); break;
    case "agenda_mudou":
      if (!$("#fundo-agenda").hidden) carregarAgenda();
      carregarAgendaDaTela();
      break;
  }
}

/* ------------------------------------------------------------------ missões dos agentes */
const CLASSES_STATUS = { "na fila": "fila", "executando": "executando", "revisando": "revisando", "relatando": "relatando",
  "concluída": "concluida", "falhou": "falhou", "cancelada": "falhou", "não realizada": "falhou" };
const PROGRESSO_STATUS = { fila: 0.05, executando: 0.35, revisando: 0.6, relatando: 0.85, concluida: 1, falhou: 1 };

function barraDeTexto(valor, largura) {
  const cheio = Math.round(Math.max(0, Math.min(1, valor)) * largura);
  return "█".repeat(cheio) + "·".repeat(largura - cheio);
}

function atualizarMissoes(tarefas) {
  const lista = $("#lista-missoes"), primeiraVez = tarefasConhecidas === null;
  tarefasConhecidas = tarefasConhecidas || {};
  lista.innerHTML = "";
  for (const tarefa of tarefas.slice(0, 6)) {
    const classe = CLASSES_STATUS[tarefa.status] || "fila";
    const item = document.createElement("li");
    item.className = `missao ${classe}`;
    item.title = `${tarefa.pedido}\n${tarefa.status}${tarefa.etapa ? " · " + tarefa.etapa : ""}`;
    item.innerHTML = `<span class="codigo"></span><span class="pedido"></span><span class="etapa"></span><span class="progresso"></span>`;
    item.querySelector(".codigo").textContent = "#" + String(tarefa.id).slice(-4).toUpperCase();
    item.querySelector(".pedido").textContent = tarefa.pedido;
    item.querySelector(".etapa").textContent = tarefa.etapa ? `${tarefa.status} · ${tarefa.etapa}` : tarefa.status;
    const progresso = PROGRESSO_STATUS[classe];
    item.querySelector(".progresso").textContent =
      `${barraDeTexto(progresso, 16)} ${classe === "falhou" ? "falhou" : Math.round(progresso * 100) + "%"}`;
    if (tarefa.pasta) item.onclick = () => api.abrir(tarefa.pasta);
    lista.appendChild(item);

    const anterior = tarefasConhecidas[tarefa.id];
    if (!primeiraVez && anterior === undefined && classe !== "concluida") janelaSistema("Nova missão", tarefa.pedido);
    if (!primeiraVez && anterior !== undefined && anterior !== "concluída" && tarefa.status === "concluída") {
      janelaSistema("Missão concluída", tarefa.pedido, {
        vitoria: true, duracao: 12000,
        acao: tarefa.pasta ? { rotulo: "Abrir a pasta", aoClicar: () => api.abrir(tarefa.pasta) } : null,
      });
      celebrar();
    }
    tarefasConhecidas[tarefa.id] = tarefa.status;
  }
  $("#ceu-vazio").hidden = tarefas.length > 0;
  $("#missoes-conta").textContent = tarefas.length ? String(tarefas.length).padStart(2, "0") : "";
}

/* ------------------------------------------------------------------ estado geral e Status */
function classePonto(estado) {
  return { ativo: "verde", online: "verde", conectado: "verde", pausado: "ambar", parcial: "ambar", "aguardando QR": "ambar",
    conectando: "ambar", abrindo: "ambar", offline: "vermelho", erro: "vermelho" }[estado] || "";
}

function aplicarEstado(e) {
  estadoAtual = e;
  const llm = e.ollama || {};
  const pontoLlm = $("#chip-llm .ponto");
  pontoLlm.className = "ponto " + (llm.online ? (llm.modelo_ok ? "verde" : "ambar") : "vermelho");
  $("#chip-llm span").textContent = llm.online ? (llm.modelo_ok ? e.modelo : `${e.modelo} ausente`) : "LLM offline";
  const escuta = (e.painel && e.painel.escuta) || (e.microfone_ativo ? "escutando" : "microfone pausado");
  $("#chip-escuta .ponto").className = "ponto " + (!e.microfone_ativo ? "" : escuta.startsWith("escutando") ? "azul" : "verde");
  $("#chip-escuta span").textContent = e.microfone_ativo ? escuta : "microfone pausado";
  $("#chip-modo span").textContent = `modo ${(e.painel && e.painel.modo) || e.modo || "padrão"}`;
  $("#sub-marca").textContent = "sistema ativo";
  $("#btn-mic").classList.toggle("ativo", !!e.microfone_ativo);
  atualizarMissoes(e.tarefas || []);
  atualizarTelemetria(e);
  if (!$("#fundo-status").hidden) renderizarStatus();
}

function atualizarTelemetria(e) {
  const r = e.recursos || {}, m = e.metricas || {}, b = e.biblioteca || {};
  const porcento = (v) => `${barraDeTexto(v / 100, 12)} ${String(Math.round(v)).padStart(2, "0")}%`;
  $("#tl-cpu").textContent = r.cpu != null ? porcento(r.cpu) : "--";
  $("#tl-ram").textContent = r.ram != null ? porcento(r.ram) : "--";
  $("#tl-interacoes").textContent = m.interacoes ?? "--";
  $("#tl-tempo").textContent = m.tempo_medio != null ? `${m.tempo_medio.toFixed(1)} s` : "--";
  $("#tl-biblioteca").textContent = b.fontes != null ? `${b.fontes} fontes · ${b.trechos} trechos` : "--";
  $("#tl-nuvem").textContent = (e.nuvem || []).map((n) => `${n.nome.toLowerCase()} ${n.estado === "ativo" ? "●" : "○"}`).join("  ") || "--";
}

function linhaStatus(rotulo, valor, classe) {
  return `<div class="status-linha"><span class="rotulo">${rotulo}</span><span class="valor">${valor}</span>` +
         `<i class="ponto ${classe || ""}"></i></div>`;
}
function escapar(texto) { return String(texto ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

let dispositivos = null;
function renderizarStatus() {
  const e = estadoAtual; if (!e) return;
  const m = e.metricas || {}, b = e.biblioteca || {}, r = e.recursos || {}, idx = (e.painel && e.painel.indexacao) || {};
  const modoAtual = (e.painel && e.painel.modo) || e.modo;
  let html = `
    <div class="identidade">Beatriz · assistente pessoal</div>
    <div class="trio"><div><b>${m.interacoes ?? "--"}</b><span>INTERAÇÕES</span></div>
      <div><b>${m.tempo_medio != null ? m.tempo_medio.toFixed(1) + " s" : "--"}</b><span>TEMPO MÉDIO</span></div>
      <div><b>${m.sucesso != null ? Math.round(m.sucesso) + "%" : "--"}</b><span>SUCESSO</span></div></div>
    <div class="separador"></div>`;
  const llm = e.ollama || {};
  html += linhaStatus("LLM LOCAL", escapar(llm.online ? e.modelo : "offline"), llm.online ? (llm.modelo_ok ? "verde" : "ambar") : "vermelho");
  for (const n of e.nuvem || []) html += linhaStatus(escapar(n.nome.toUpperCase()), escapar(n.texto), classePonto(n.estado === "ativo" ? "ativo" : n.estado === "pausado" ? "pausado" : ""));
  if (e.whatsapp && e.whatsapp.status) html += linhaStatus("WHATSAPP", escapar(e.whatsapp.status), classePonto(e.whatsapp.status));
  html += linhaStatus("MICROFONE", `${e.microfone_ativo ? "ativo" : "pausado"} <button class="pequeno" id="st-mic">${e.microfone_ativo ? "pausar" : "ligar"}</button>`, e.microfone_ativo ? "verde" : "ambar");
  html += linhaStatus("BIBLIOTECA", `${b.fontes ?? "--"} fontes · ${b.trechos ?? "--"} trechos · ${b.conversas ?? "--"} conversas`,
                      idx.status === "carregando" ? "ambar" : idx.status === "falhou" ? "vermelho" : "verde");
  html += `<div class="status-linha"><span class="rotulo">CPU</span><div class="barra"><i style="width:${r.cpu ?? 0}%"></i></div><span>${r.cpu != null ? Math.round(r.cpu) + "%" : "--"}</span></div>`;
  html += `<div class="status-linha"><span class="rotulo">RAM</span><div class="barra"><i style="width:${r.ram ?? 0}%"></i></div><span>${r.ram != null ? Math.round(r.ram) + "%" : "--"}</span></div>`;
  html += `<div class="separador"></div><div class="status-linha"><span class="rotulo">MODO</span><div class="modos">` +
          (e.modos || []).map((modo) => `<button data-modo="${escapar(modo)}" class="${modo === modoAtual ? "ativo" : ""}">${escapar(modo)}</button>`).join("") +
          `</div><span></span></div>`;
  html += `<div class="status-linha"><span class="rotulo">ÁUDIO</span><div style="display:flex;flex-direction:column;gap:6px">` +
          `<select id="st-microfone"></select><select id="st-saida"></select></div><button class="pequeno" id="st-aplicar">aplicar</button></div>`;
  html += `<div class="status-linha"><span class="rotulo">EFEITOS</span><span class="valor">sons do Sistema</span>` +
          `<button class="pequeno" id="st-sons">${sons.ligado ? "ligados" : "desligados"}</button></div>`;
  $("#conteudo-status").innerHTML = html;

  $("#st-mic").onclick = alternarMicrofone;
  $("#st-sons").onclick = () => { sons.ligado = !sons.ligado; gravarPreferencia("sons", sons.ligado); sons.tocar("ping"); renderizarStatus(); };
  document.querySelectorAll("[data-modo]").forEach((botao) => botao.onclick = async () => {
    const resposta = await api.escolher_modo(botao.dataset.modo);
    janelaSistema("Notificação", resposta || `Modo ${botao.dataset.modo} ativado.`);
  });
  preencherAudio();
  $("#st-aplicar").onclick = async () => {
    const resposta = await api.aplicar_audio($("#st-microfone").value, $("#st-saida").value);
    janelaSistema("Notificação", resposta);
  };
}
function preencherAudio() {
  if (!dispositivos) return;
  const mic = $("#st-microfone"), saida = $("#st-saida");
  mic.innerHTML = ""; saida.innerHTML = "";
  for (const d of dispositivos.microfones) mic.add(new Option(d.nome, d.valor, false, d.valor === dispositivos.microfone_atual));
  for (const s of dispositivos.saidas) saida.add(new Option(s.nome, s.valor, false, s.valor === dispositivos.saida_atual));
}
async function abrirStatus(abrir) {
  $("#fundo-status").hidden = !abrir;
  $("#btn-status").classList.toggle("ativo", abrir);
  if (abrir) {
    renderizarStatus();
    dispositivos = await api.dispositivos_audio();
    preencherAudio();
  }
}

/* ------------------------------------------------------------------ agenda (janela) */
const INICIAIS_SEMANA = ["S", "T", "Q", "Q", "S", "S", "D"];
const NOMES_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
let semanaAgenda = 0;
let vistaAgenda = "semana"; // "semana" ou "mes"
let mesAgenda = null; // [ano, mês] da aba do mês; null = o mês atual
let atualizacaoAgenda = null;

async function abrirAgenda(abrir = true) {
  $("#fundo-agenda").hidden = !abrir;
  $("#btn-agenda").classList.toggle("ativo", abrir);
  clearInterval(atualizacaoAgenda);
  if (!abrir) return;
  await carregarAgenda();
  // A voz e o WhatsApp também mexem na agenda: com a janela aberta, ela se atualiza sozinha.
  atualizacaoAgenda = setInterval(carregarAgenda, 5000);
  $("#ag-pedido").focus();
}
async function carregarAgenda() {
  try {
    const semana = await api.agenda(semanaAgenda);
    renderizarLadoDaJanela(semana);
    if (vistaAgenda === "semana") renderizarSemana(semana);
    else renderizarMes(await api.agenda_mes(...(mesAgenda || [0, 0])));
  } catch (erro) { /* ponte ocupada: tenta de novo no próximo ciclo */ }
}
function respostaDaAgenda(texto) {
  if (!texto) return;
  digitar($("#ag-resposta"), texto);
  sons.tocar("ping");
  carregarAgenda();
  carregarAgendaDaTela();
}
function trocarVista(vista) {
  vistaAgenda = vista;
  document.querySelectorAll(".aba-agenda").forEach((b) => b.classList.toggle("ativo", b.dataset.vista === vista));
  $("#ag-semana").hidden = vista !== "semana";
  $("#ag-mes").hidden = vista !== "mes";
  $("#ag-hoje").textContent = vista === "semana" ? "esta semana" : "este mês";
  carregarAgenda();
}
function semanasAte(dataIso) {
  // Quantas semanas da semana atual até a semana do dia (para abrir a semana de um dia do mês).
  const segunda = (d) => { const c = new Date(d); c.setHours(12, 0, 0, 0); c.setDate(c.getDate() - ((c.getDay() + 6) % 7)); return c; };
  return Math.round((segunda(dataIso + "T12:00:00") - segunda(new Date())) / (7 * 864e5));
}

function itemDaAgenda(item) {
  const el = document.createElement("div");
  el.className = `agenda-item cat-${item.categoria} tipo-${item.tipo}` + (item.passou ? " passou" : "") + (item.agora ? " agora" : "");
  const hora = document.createElement("span");
  hora.className = "hora";
  hora.textContent = item.dia_inteiro ? "dia todo" : item.hora + (item.fim ? `–${item.fim}` : "");
  const titulo = document.createElement("span");
  titulo.className = "titulo";
  titulo.textContent = (item.tipo === "lembrete" ? "⏰ " : "") + item.titulo;
  el.title = item.repete ? `${item.titulo}\nSe repete ${item.regra}` : item.titulo;
  el.append(hora, titulo);
  if (item.repete) {
    const r = document.createElement("i"); r.className = "repete"; r.textContent = "↻"; el.appendChild(r);
  }
  const tirar = document.createElement("button");
  tirar.className = "tirar"; tirar.textContent = "×";
  tirar.title = item.repete ? "Cancelar só este dia (Shift+clique: tirar da rotina)" : "Cancelar";
  tirar.setAttribute("aria-label", tirar.title);
  tirar.onclick = async (e) => { e.stopPropagation(); respostaDaAgenda(await api.agenda_cancelar(item.id, item.data, e.shiftKey)); };
  el.appendChild(tirar);
  return el;
}

function renderizarSemana(d) {
  $("#ag-titulo").textContent = d.titulo;
  $("#ag-hoje").classList.toggle("ativo", d.deslocamento === 0);
  const semana = $("#ag-semana");
  semana.innerHTML = "";
  for (const dia of d.dias) {
    const coluna = document.createElement("div");
    coluna.className = "agenda-dia" + (dia.hoje ? " hoje" : "") + (dia.passado ? " passado" : "");
    const cabecalho = document.createElement("div");
    cabecalho.className = "agenda-dia-cab";
    const nome = document.createElement("span"); nome.textContent = dia.nome;
    const numero = document.createElement("b"); numero.textContent = dia.numero;
    cabecalho.append(nome, numero);
    coluna.appendChild(cabecalho);
    if (!dia.itens.length) {
      const vazio = document.createElement("div"); vazio.className = "agenda-vazio"; vazio.textContent = "livre"; coluna.appendChild(vazio);
    }
    for (const item of dia.itens) coluna.appendChild(itemDaAgenda(item));
    semana.appendChild(coluna);
  }
}

function renderizarMes(m) {
  mesAgenda = [m.ano, m.mes];
  $("#ag-titulo").textContent = m.titulo[0].toUpperCase() + m.titulo.slice(1);
  const hoje = new Date();
  $("#ag-hoje").classList.toggle("ativo", m.ano === hoje.getFullYear() && m.mes === hoje.getMonth() + 1);
  const grade = $("#ag-mes");
  grade.innerHTML = "";
  for (const nome of NOMES_SEMANA) {
    const cab = document.createElement("div"); cab.className = "sem"; cab.textContent = nome; grade.appendChild(cab);
  }
  for (const dia of m.dias) {
    const celula = document.createElement("button");
    celula.className = "mes-dia" + (dia.do_mes ? "" : " fora") + (dia.hoje ? " hoje" : "") + (dia.passado ? " passado" : "");
    celula.title = "Ver a semana deste dia";
    const numero = document.createElement("span"); numero.className = "numero"; numero.textContent = dia.numero;
    celula.appendChild(numero);
    for (const item of dia.itens.slice(0, 3)) {
      const linha = document.createElement("span");
      linha.className = `linha cat-${item.categoria}`;
      linha.textContent = `${item.dia_inteiro ? "" : item.hora + " "}${item.titulo}`;
      celula.appendChild(linha);
    }
    if (dia.itens.length > 3) {
      const mais = document.createElement("span"); mais.className = "mais"; mais.textContent = `+${dia.itens.length - 3}`; celula.appendChild(mais);
    }
    if (dia.tarefas) {
      const prazo = document.createElement("span"); prazo.className = "prazo";
      prazo.textContent = dia.tarefas === 1 ? "1 tarefa vence" : `${dia.tarefas} tarefas vencem`;
      celula.appendChild(prazo);
    }
    celula.onclick = () => { semanaAgenda = semanasAte(dia.data); trocarVista("semana"); };
    grade.appendChild(celula);
  }
}

function renderizarLadoDaJanela(d) {
  $("#ag-proximo").textContent = d.proximo || "";
  const lista = $("#ag-tarefas");
  lista.innerHTML = "";
  if (!d.tarefas.length) {
    const vazia = document.createElement("li"); vazia.className = "agenda-tarefa vazia"; vazia.textContent = "Nenhuma tarefa pendente.";
    lista.appendChild(vazia);
  }
  for (const t of d.tarefas) {
    const li = document.createElement("li");
    li.className = "agenda-tarefa" + (t.feita ? " feita" : "") + (t.atrasada && !t.feita ? " atrasada" : "") + (t.urgente ? " urgente" : "");
    const caixa = document.createElement("button");
    caixa.className = "caixa"; caixa.textContent = t.feita ? "✓" : "";
    caixa.setAttribute("aria-label", t.feita ? `Desmarcar ${t.titulo}` : `Marcar ${t.titulo} como feita`);
    caixa.onclick = async () => respostaDaAgenda(await api.tarefa_alternar(t.id));
    const nome = document.createElement("span"); nome.textContent = t.titulo;
    const prazo = document.createElement("small");
    prazo.textContent = t.prazo_texto ? (t.atrasada && !t.feita ? `atrasada · ${t.prazo_texto}` : t.prazo_texto) : "";
    const tirar = document.createElement("button");
    tirar.className = "tirar"; tirar.textContent = "×"; tirar.setAttribute("aria-label", `Remover ${t.titulo}`);
    tirar.onclick = async () => respostaDaAgenda(await api.tarefa_remover(t.id));
    li.append(caixa, nome, prazo, tirar);
    lista.appendChild(li);
  }
  $("#ag-rotina").textContent = d.rotina || "";
}

/* ------------------------------------------------------------------ agenda na tela (calendário + dia) */
let mesTela = null; // [ano, mês] do calendário da tela; null = o mês atual
let diaTela = null; // dia escolhido no calendário da tela; null = hoje

async function carregarAgendaDaTela() {
  if (!api || getComputedStyle($("#lado-agenda")).display === "none") return;
  try {
    const [mes, dia] = await Promise.all([api.agenda_mes(...(mesTela || [0, 0])), api.agenda_dia(diaTela || "")]);
    renderizarMiniCalendario(mes, dia.data);
    renderizarDiaDaTela(dia);
    const tarefas = mes.pendentes ? `${mes.pendentes} ${mes.pendentes === 1 ? "tarefa pendente" : "tarefas pendentes"}` +
      (mes.atrasadas ? ` (${mes.atrasadas} atrasada${mes.atrasadas > 1 ? "s" : ""})` : "") + "." : "Nenhuma tarefa pendente.";
    $("#lado-rodape").textContent = `${mes.proximo} ${tarefas}`;
  } catch (erro) { /* ponte ocupada: tenta de novo no próximo ciclo */ }
}

function renderizarMiniCalendario(m, escolhido) {
  mesTela = [m.ano, m.mes];
  $("#mc-titulo").textContent = m.titulo[0].toUpperCase() + m.titulo.slice(1);
  const grade = $("#mini-cal");
  grade.innerHTML = "";
  INICIAIS_SEMANA.forEach((letra) => { const s = document.createElement("span"); s.className = "sem"; s.textContent = letra; grade.appendChild(s); });
  for (const dia of m.dias) {
    const botao = document.createElement("button");
    botao.className = (dia.do_mes ? "" : "fora") + (dia.hoje ? " hoje" : "") + (dia.passado ? " passado" : "") +
                      (dia.data === escolhido ? " escolhido" : "");
    botao.textContent = dia.numero;
    const resumo = [dia.quantidade && `${dia.quantidade} na agenda`, dia.tarefas && `${dia.tarefas} tarefa(s) vencendo`].filter(Boolean).join(", ");
    botao.title = resumo || "livre";
    botao.setAttribute("aria-label", `${dia.numero}: ${botao.title}`);
    if (dia.quantidade || dia.tarefas) {
      const marcas = document.createElement("i");
      for (let k = 0; k < Math.min(dia.quantidade, 3); k++) marcas.appendChild(document.createElement("b"));
      if (dia.tarefas) { const p = document.createElement("b"); p.className = "prazo"; marcas.appendChild(p); }
      botao.appendChild(marcas);
    }
    botao.onclick = () => { diaTela = dia.data; carregarAgendaDaTela(); };
    botao.ondblclick = () => { semanaAgenda = semanasAte(dia.data); vistaAgenda = "semana"; abrirAgenda(true); };
    grade.appendChild(botao);
  }
}

function renderizarDiaDaTela(d) {
  $("#dt-titulo").textContent = d.titulo;
  const lista = $("#dt-itens");
  lista.innerHTML = "";
  for (const item of d.itens) {
    const li = document.createElement("li");
    li.className = (item.passou ? "passou" : "") + (item.agora ? " agora" : "");
    const hora = document.createElement("span"); hora.className = "hora";
    hora.textContent = item.dia_inteiro ? "dia todo" : item.hora;
    const titulo = document.createElement("span"); titulo.className = "titulo";
    titulo.textContent = (item.tipo === "lembrete" ? "⏰ " : "") + item.titulo + (item.repete ? " ↻" : "");
    li.title = item.repete ? `Se repete ${item.regra}` : "";
    li.append(hora, titulo);
    lista.appendChild(li);
  }
  for (const tarefa of d.tarefas) {
    const li = document.createElement("li"); li.className = "prazo";
    const hora = document.createElement("span"); hora.className = "hora"; hora.textContent = "tarefa";
    const titulo = document.createElement("span"); titulo.className = "titulo"; titulo.textContent = tarefa;
    li.append(hora, titulo); lista.appendChild(li);
  }
  if (!lista.children.length) {
    const vazio = document.createElement("li"); vazio.className = "vazio"; vazio.textContent = "Nada marcado. Dia livre.";
    lista.appendChild(vazio);
  }
}

function ligarAgenda() {
  $("#btn-agenda").onclick = () => abrirAgenda($("#fundo-agenda").hidden);
  $("#fundo-agenda").addEventListener("click", (e) => { if (e.target.id === "fundo-agenda") abrirAgenda(false); });
  document.querySelectorAll(".aba-agenda").forEach((b) => b.onclick = () => trocarVista(b.dataset.vista));
  const andar = (passo) => {
    if (vistaAgenda === "semana") semanaAgenda += passo;
    else if (mesAgenda) { const [a, m] = mesAgenda; const d = new Date(a, m - 1 + passo, 1); mesAgenda = [d.getFullYear(), d.getMonth() + 1]; }
    carregarAgenda();
  };
  $("#ag-anterior").onclick = () => andar(-1);
  $("#ag-proxima").onclick = () => andar(1);
  $("#ag-hoje").onclick = () => { semanaAgenda = 0; mesAgenda = null; carregarAgenda(); };
  const aoEnter = (campo, chamada) => campo.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !campo.value.trim()) return;
    const texto = campo.value; campo.value = "";
    respostaDaAgenda(await chamada(texto));
  });
  aoEnter($("#ag-pedido"), (texto) => api.agenda_pedir(texto));
  aoEnter($("#ag-nova-tarefa"), (texto) => api.tarefa_nova(texto));

  // Quadro da tela: setas trocam o mês; clique num dia mostra o dia; duplo clique abre a semana dele.
  const mudarMesTela = (passo) => {
    const [a, m] = mesTela || [new Date().getFullYear(), new Date().getMonth() + 1];
    const d = new Date(a, m - 1 + passo, 1);
    mesTela = [d.getFullYear(), d.getMonth() + 1];
    carregarAgendaDaTela();
  };
  $("#mc-anterior").onclick = () => mudarMesTela(-1);
  $("#mc-seguinte").onclick = () => mudarMesTela(1);
  $("#lado-abrir").onclick = () => abrirAgenda(true);
  carregarAgendaDaTela();
  setInterval(carregarAgendaDaTela, 10000); // a voz e o WhatsApp também mudam a agenda
}

/* ------------------------------------------------------------------ biblioteca (janela) */
// Organizar à mão o que é fonte (a Beatriz lê) e o que é só arquivo (guardado; ela acha pelo nome, mas não lê), e triar o que
// chegou pelo WhatsApp. As regras (que pastas, o que pode) ficam em biblioteca_fontes.py; aqui só se escolhe e se mostra.
const bib = { dados: null, pasta: null, relogio: null, marcados: new Set(), ultimoMarcado: null, filtro: "", visiveis: [] };
const SIGLAS_TIPO = { pdf: "PDF", apresentacao: "PPT", documento: "DOC", planilha: "XLS", imagem: "IMG", nota: "TXT", pagina: "WEB" };
const ROTULO_AREA = { fontes: "fonte", arquivos: "só arquivo", recebidos: "recebido" };
const semAcentos = (texto) => texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const novo = (tag, classe, texto) => { const el = document.createElement(tag); if (classe) el.className = classe; if (texto !== undefined) el.textContent = texto; return el; };

// Busca pelo nome sem precisar ser exato (a mesma ideia do busca_arquivos.py, mais simples): sem acento, pedaço do começo da
// palavra ("adm"), plural, mesma raiz ("comoriencia" acha "comorientes"), um erro de digitação e números como números ("02" = "2").
const VAZIAS_DA_BUSCA = new Set(["a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "um", "uma", "com", "para", "pra"]);
function palavrasDe(texto) {
  return (semAcentos(texto).replace(/\.[a-z0-9]{2,5}$/, "").replace(/\s*[(\[]\d{1,3}[)\]]/g, " ").match(/[a-z]+|\d+/g) || [])
    .map((t) => (/^\d+$/.test(t) && t.length <= 6 ? String(Number(t)) : t))
    .filter((t) => !VAZIAS_DA_BUSCA.has(t));
}
function quaseIgual(a, b) { // no máximo uma letra a mais, a menos ou trocada
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, erros = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++erros > 1) return false;
    if (a.length > b.length) i++; else if (b.length > a.length) j++; else { i++; j++; }
  }
  return erros + (a.length - i) + (b.length - j) <= 1;
}
function notaDaPalavra(q, t) {
  if (q === t) return 1;
  if (/^\d+$/.test(q) || /^\d+$/.test(t)) return 0;
  if (t.startsWith(q)) return 0.9;
  if (t.length >= 3 && q.startsWith(t) && q.length - t.length <= 3) return 0.85;
  let comum = 0;
  while (comum < q.length && q[comum] === t[comum]) comum++;
  if (comum >= 5 && comum >= 0.7 * Math.min(q.length, t.length)) return 0.8;
  if (Math.min(q.length, t.length) >= 5 && quaseIgual(q, t)) return 0.75;
  return 0;
}
function notaDaBusca(termos, arquivo) {
  const nome = palavrasDe(arquivo.nome), pasta = palavrasDe(arquivo.pasta || "");
  let soma = 0;
  for (const q of termos) {
    const noNome = Math.max(0, ...nome.map((t) => notaDaPalavra(q, t)));
    const naPasta = 0.7 * Math.max(0, ...pasta.map((t) => notaDaPalavra(q, t)));
    const nota = Math.max(noNome, naPasta, q.length >= 3 && nome.join("").includes(q) ? 0.8 : 0);
    if (nota < 0.6) return 0; // cada palavra digitada tem de aparecer de algum jeito
    soma += nota;
  }
  return soma / termos.length;
}

async function abrirBiblioteca(abrir = true) {
  $("#fundo-biblioteca").hidden = !abrir;
  $("#btn-biblioteca").classList.toggle("ativo", abrir);
  clearInterval(bib.relogio);
  if (!abrir) return;
  await carregarBibliotecaJanela();
  bib.relogio = setInterval(carregarBibliotecaJanela, 6000); // "ainda não lida" vira "lida" quando a varredura termina
  $("#bb-busca").focus();
}

async function carregarBibliotecaJanela() {
  try { bib.dados = await api.biblioteca(); } catch (erro) { return; }
  if (!bib.pasta || !bib.dados.pastas.some((p) => p.id === bib.pasta)) {
    bib.pasta = (bib.dados.pastas.find((p) => p.arquivos.length) || bib.dados.pastas[0] || {}).id || null;
  }
  const existentes = new Set(bib.dados.pastas.flatMap((p) => p.arquivos.map((a) => a.id)));
  for (const id of [...bib.marcados]) if (!existentes.has(id)) bib.marcados.delete(id);
  renderizarBiblioteca();
}

function plural(n, um, varios) { return `${n} ${n === 1 ? um : varios}`; }

function renderizarBiblioteca() {
  const dados = bib.dados;
  if (!dados) return;
  const leitura = dados.leitura === "carregando" ? " · lendo as fontes…" : "";
  $("#bb-resumo").textContent = `${plural(dados.fontes, "fonte", "fontes")} (${dados.lidas} lidas) · ${plural(dados.so_arquivos, "só arquivo", "só arquivos")}`
    + ` · ${plural(dados.recebidos, "recebido", "recebidos")}${leitura}`;
  $("#bb-repetidos").hidden = !dados.repetidos && bib.filtro !== "repetidos";
  $("#bb-repetidos").textContent = `≡ repetidos · ${dados.repetidos}`;
  $("#bb-repetidos").classList.toggle("ativo", bib.filtro === "repetidos");
  renderizarArvore(dados);

  const textoBusca = $("#bb-busca").value.trim();
  const termos = palavrasDe(textoBusca);
  const atual = dados.pastas.find((p) => p.id === bib.pasta);
  const todos = () => dados.pastas.flatMap((p) => p.arquivos.map((a) => ({ ...a, pasta: p.caminho })));
  let arquivos, titulo;
  if (termos.length) {
    arquivos = todos().map((a) => [notaDaBusca(termos, a), a]).filter(([nota]) => nota > 0)
      .sort((x, y) => y[0] - x[0] || y[1].modificado - x[1].modificado).map(([, a]) => a);
    titulo = `Busca: “${textoBusca}”`;
  } else if (bib.filtro === "repetidos") {
    arquivos = todos().filter((a) => a.repetido.length).sort((x, y) => x.tamanho - y.tamanho || x.nome.localeCompare(y.nome));
    titulo = "Cópias com o mesmo conteúdo";
  } else {
    arquivos = atual ? atual.arquivos : [];
    titulo = atual ? atual.caminho : "";
  }
  const listaGeral = termos.length || bib.filtro === "repetidos";
  bib.visiveis = arquivos.map((a) => a.id);
  $("#bb-titulo").textContent = titulo;
  const area = !listaGeral && atual ? dados.areas.find((a) => a.id === atual.area) : null;
  $("#bb-explica").textContent = area ? `${area.nome}: ${area.descricao}` : (bib.filtro === "repetidos" && !termos.length ? "apague as que sobram" : "");
  $("#bb-apagar-pasta").hidden = listaGeral || !atual || atual.raiz || atual.arquivos.length > 0
    || dados.pastas.some((p) => p.id.startsWith(`${atual.id}/`));
  $("#bb-nova-pasta").disabled = !atual || !atual.pode_criar_pasta;
  const linhas = $("#bb-linhas");
  linhas.innerHTML = "";
  for (const arquivo of arquivos) linhas.appendChild(linhaDaBiblioteca(arquivo, listaGeral));
  if (!arquivos.length) {
    const tr = novo("tr", "vazia");
    const td = novo("td", "", termos.length ? "Nenhum arquivo com esse nome." : atual && atual.raiz && !atual.arquivos.length
      ? "Os arquivos estão nas pastas de dentro." : "Pasta vazia. Use “+ adicionar arquivos” ou arraste arquivos para cá.");
    td.colSpan = 6;
    tr.appendChild(td);
    linhas.appendChild(tr);
  }
  $("#bb-todos").checked = arquivos.length > 0 && arquivos.every((a) => bib.marcados.has(a.id));
  renderizarSelecao();
}

// A árvore por área: cabeçalho (FONTES, SÓ ARQUIVOS, RECEBIDOS) e as pastas de cada uma. Soltar arquivos num cabeçalho troca
// de área mantendo a pasta do assunto; soltar numa pasta move para ela.
function renderizarArvore(dados) {
  const arvore = $("#bb-arvore");
  arvore.innerHTML = "";
  for (const area of dados.areas) {
    const pastas = dados.pastas.filter((p) => p.area === area.id);
    const raizes = pastas.filter((p) => p.raiz);
    const unica = raizes.length === 1 ? raizes[0] : null; // "Só arquivos" e "Recebidos": o cabeçalho é a própria pasta
    const cabeca = novo("li", `biblio-area area-${area.id}${unica && unica.id === bib.pasta ? " ativa" : ""}`);
    const nome = novo("span", "biblio-area-nome", area.nome);
    nome.appendChild(novo("b", "", String(area.arquivos)));
    cabeca.append(nome, novo("span", "biblio-area-desc", area.descricao));
    cabeca.title = area.id === "recebidos" ? "Chegaram pelo WhatsApp, pelo celular ou pelo scanner: decida se viram fonte ou só arquivo"
      : `Solte arquivos aqui para virarem ${area.id === "fontes" ? "fonte" : "só arquivo"} (ficam na pasta do mesmo assunto)`;
    if (unica) cabeca.onclick = () => escolherPasta(unica.id);
    const acao = { fontes: "virar_fonte", arquivos: "so_arquivo" }[area.id];
    if (acao) soltarAqui(cabeca, (ids) => acaoBiblioteca(api.biblioteca_lote(acao, ids, "")));
    arvore.appendChild(cabeca);
    for (const pasta of pastas) {
      if (pasta === unica) continue;
      const escolhida = pasta.id === bib.pasta && !bib.filtro && !$("#bb-busca").value.trim();
      const item = novo("li", `biblio-pasta${escolhida ? " ativa" : ""}${pasta.raiz ? " raiz" : ""}`);
      item.style.paddingLeft = `${14 + pasta.profundidade * 14}px`;
      item.append(novo("span", "biblio-pasta-nome", pasta.nome), novo("span", "biblio-pasta-conta", String(pasta.arquivos.length || "")));
      item.title = pasta.caminho;
      item.onclick = () => escolherPasta(pasta.id);
      soltarAqui(item, (ids) => acaoBiblioteca(ids.length === 1 ? api.biblioteca_mover(ids[0], pasta.id) : api.biblioteca_lote("mover", ids, pasta.id)));
      arvore.appendChild(item);
    }
  }
}

function escolherPasta(id) {
  bib.pasta = id;
  bib.filtro = "";
  $("#bb-busca").value = "";
  renderizarBiblioteca();
}

function soltarAqui(elemento, aoSoltar) {
  elemento.addEventListener("dragover", (e) => { e.preventDefault(); elemento.classList.add("alvo"); });
  elemento.addEventListener("dragleave", () => elemento.classList.remove("alvo"));
  elemento.addEventListener("drop", (e) => {
    e.preventDefault();
    elemento.classList.remove("alvo");
    let ids = [];
    try { ids = JSON.parse(e.dataTransfer.getData("text/beatriz-arquivos") || "[]"); } catch (erro) { ids = []; }
    if (ids.length) aoSoltar(ids);
  });
}

function opcoesDePastas(select, rotulo) {
  select.add(new Option(rotulo, ""));
  for (const area of bib.dados.areas) {
    const grupo = document.createElement("optgroup");
    grupo.label = area.nome;
    for (const p of bib.dados.pastas.filter((x) => x.area === area.id)) {
      grupo.appendChild(new Option(`${" ".repeat(p.profundidade * 2)}${p.nome}`, p.id));
    }
    select.appendChild(grupo);
  }
}

function linhaDaBiblioteca(arquivo, listaGeral) {
  const tr = novo("tr", bib.marcados.has(arquivo.id) ? "marcada" : "");
  tr.draggable = true;
  tr.addEventListener("dragstart", (e) => {
    // arrastar uma linha marcada leva todas as marcadas
    const ids = bib.marcados.has(arquivo.id) ? [...bib.marcados] : [arquivo.id];
    e.dataTransfer.setData("text/beatriz-arquivos", JSON.stringify(ids));
    e.dataTransfer.effectAllowed = "move";
  });
  const marca = novo("td", "c-marca");
  const caixa = novo("input");
  caixa.type = "checkbox";
  caixa.checked = bib.marcados.has(arquivo.id);
  caixa.title = "Marcar (Shift marca de onde parou até aqui)";
  caixa.onclick = (e) => marcar(arquivo.id, caixa.checked, e.shiftKey);
  marca.appendChild(caixa);

  const nome = novo("td");
  const rotulo = novo("div", "biblio-nome");
  rotulo.append(novo("span", `biblio-tipo ${arquivo.tipo}`, SIGLAS_TIPO[arquivo.tipo] || "ARQ"), novo("span", "", arquivo.nome));
  if (arquivo.repetido.length) {
    const iguais = arquivo.repetido.map((id) => id.split("/").slice(0, -1).join(" › ") + " › " + id.split("/").pop());
    const selo = novo("span", "biblio-repetido", "≡ repetido");
    selo.title = `Mesmo conteúdo que:\n${iguais.join("\n")}`;
    rotulo.appendChild(selo);
  }
  if (listaGeral && arquivo.pasta) rotulo.appendChild(novo("small", "", arquivo.pasta));
  nome.appendChild(rotulo);
  nome.title = "Clique duas vezes para abrir";
  nome.ondblclick = () => api.biblioteca_abrir(arquivo.id, false);
  const tamanho = arquivo.tamanho > 1048576 ? `${(arquivo.tamanho / 1048576).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(arquivo.tamanho / 1024))} KB`;
  const data = new Date(arquivo.modificado * 1000).toLocaleDateString("pt-BR");
  const situacao = novo("td", "biblio-situacao");
  situacao.appendChild(novo("span", `biblio-area-selo ${arquivo.area}`, ROTULO_AREA[arquivo.area] || arquivo.area));
  if (arquivo.area === "fontes") situacao.appendChild(novo("span", `biblio-estado ${semAcentos(arquivo.estado).replace(/\s+/g, "-")}`, arquivo.estado));
  const celulaAcoes = novo("td");
  const acoes = novo("div", "biblio-acoes");
  celulaAcoes.appendChild(acoes);
  const botao = (texto, titulo, acao, classe = "") => {
    const b = novo("button", `pequeno ${classe}`, texto);
    b.title = titulo;
    b.onclick = acao;
    acoes.appendChild(b);
  };
  botao("abrir", "Abrir no programa do arquivo", () => api.biblioteca_abrir(arquivo.id, false));
  botao("pasta", "Mostrar no Explorador", () => api.biblioteca_abrir(arquivo.id, true));
  botao("renomear", "Renomear", () => {
    const base = arquivo.nome.replace(/\.[^.]+$/, "");
    const nomeNovo = prompt(`Novo nome para “${arquivo.nome}”:`, base);
    if (nomeNovo && nomeNovo.trim() && nomeNovo.trim() !== base) acaoBiblioteca(api.biblioteca_renomear(arquivo.id, nomeNovo.trim()));
  });
  const mover = novo("select", "biblio-mover");
  mover.title = "Mover para outra pasta (de qualquer área)";
  opcoesDePastas(mover, "mover para…");
  mover.onchange = () => { if (mover.value) acaoBiblioteca(api.biblioteca_mover(arquivo.id, mover.value)); };
  acoes.appendChild(mover);
  if (arquivo.area !== "fontes") {
    botao("virar fonte", "A Beatriz passa a ler (vai para a pasta do mesmo assunto nas fontes)", () => acaoBiblioteca(api.biblioteca_virar_fonte(arquivo.id)), "fonte");
  }
  if (arquivo.area !== "arquivos") {
    botao("só arquivo", "Fica guardado e ela acha pelo nome, mas não lê (vai para Só arquivos, na pasta do mesmo assunto)",
          () => acaoBiblioteca(api.biblioteca_so_arquivo(arquivo.id)));
  }
  botao("apagar", "Mandar para a Lixeira do Windows", () => {
    if (confirm(`Apagar “${arquivo.nome}”? Ele vai para a Lixeira do Windows (dá para restaurar de lá).`)) acaoBiblioteca(api.biblioteca_apagar(arquivo.id));
  }, "perigo");
  tr.append(marca, nome, novo("td", "", tamanho), novo("td", "", data), situacao, celulaAcoes);
  return tr;
}

function marcar(id, ligado, intervalo) {
  const lista = bib.visiveis;
  const ate = lista.indexOf(id), de = lista.indexOf(bib.ultimoMarcado);
  const ids = intervalo && de >= 0 && ate >= 0 ? lista.slice(Math.min(de, ate), Math.max(de, ate) + 1) : [id];
  for (const x of ids) { if (ligado) bib.marcados.add(x); else bib.marcados.delete(x); }
  bib.ultimoMarcado = id;
  renderizarBiblioteca();
}

function renderizarSelecao() {
  const n = bib.marcados.size;
  $("#bb-selecao").hidden = n === 0;
  if (!n) return;
  $("#bb-marcados").textContent = `${plural(n, "marcado", "marcados")}`;
  const areas = new Set(bib.dados.pastas.flatMap((p) => p.arquivos).filter((a) => bib.marcados.has(a.id)).map((a) => a.area));
  $("#bb-lote-fonte").hidden = areas.size === 1 && areas.has("fontes");
  $("#bb-lote-arquivo").hidden = areas.size === 1 && areas.has("arquivos");
  const mover = $("#bb-lote-mover");
  if (mover.dataset.versao !== String(bib.dados.pastas.length) + bib.dados.pastas.map((p) => p.id).join("|")) {
    mover.innerHTML = "";
    opcoesDePastas(mover, "mover para…");
    mover.dataset.versao = String(bib.dados.pastas.length) + bib.dados.pastas.map((p) => p.id).join("|");
  }
}

async function acaoBiblioteca(promessa) {
  const resposta = await promessa;
  if (resposta && resposta.mensagem) {
    digitar($("#bb-resposta"), resposta.mensagem);
    sons.tocar(resposta.ok ? "ping" : "erro");
  }
  await carregarBibliotecaJanela();
}

async function acaoNosMarcados(acao, pasta = "") {
  const ids = [...bib.marcados];
  if (!ids.length) return;
  if (acao === "apagar" && !confirm(`Apagar ${plural(ids.length, "arquivo", "arquivos")}? Eles vão para a Lixeira do Windows (dá para restaurar de lá).`)) return;
  bib.marcados.clear();
  await acaoBiblioteca(api.biblioteca_lote(acao, ids, pasta));
}

function ligarBiblioteca() {
  $("#btn-biblioteca").onclick = () => abrirBiblioteca($("#fundo-biblioteca").hidden);
  $("#fundo-biblioteca").addEventListener("click", (e) => { if (e.target.id === "fundo-biblioteca") abrirBiblioteca(false); });
  $("#bb-busca").addEventListener("input", renderizarBiblioteca);
  $("#bb-adicionar").onclick = () => { if (bib.pasta) acaoBiblioteca(api.biblioteca_adicionar(bib.pasta)); };
  $("#bb-nova-pasta").onclick = () => {
    const nome = bib.pasta && prompt("Nome da nova pasta:");
    if (nome && nome.trim()) acaoBiblioteca(api.biblioteca_criar_pasta(bib.pasta, nome.trim()));
  };
  $("#bb-apagar-pasta").onclick = () => { if (bib.pasta) acaoBiblioteca(api.biblioteca_apagar_pasta(bib.pasta)); };
  $("#bb-repetidos").onclick = () => { bib.filtro = bib.filtro === "repetidos" ? "" : "repetidos"; $("#bb-busca").value = ""; renderizarBiblioteca(); };
  $("#bb-todos").onclick = () => {
    const ligar = $("#bb-todos").checked;
    for (const id of bib.visiveis) { if (ligar) bib.marcados.add(id); else bib.marcados.delete(id); }
    renderizarBiblioteca();
  };
  $("#bb-lote-fonte").onclick = () => acaoNosMarcados("virar_fonte");
  $("#bb-lote-arquivo").onclick = () => acaoNosMarcados("so_arquivo");
  $("#bb-lote-apagar").onclick = () => acaoNosMarcados("apagar");
  $("#bb-lote-mover").onchange = (e) => { if (e.target.value) acaoNosMarcados("mover", e.target.value); e.target.value = ""; };
  $("#bb-lote-limpar").onclick = () => { bib.marcados.clear(); renderizarBiblioteca(); };
}

/* ------------------------------------------------------------------ comandos */
async function enviar(texto) {
  texto = (texto || "").trim();
  if (!texto) return;
  historicoEntrada.unshift(texto); historicoEntrada = historicoEntrada.slice(0, 50); posicaoHistorico = -1;
  await api.enviar(texto);
}
async function alternarMicrofone() {
  const ativo = await api.alternar_microfone();
  $("#btn-mic").classList.toggle("ativo", ativo);
  janelaSistema("Notificação", ativo ? "Microfone ligado." : "Microfone pausado.");
  if (estadoAtual) { estadoAtual.microfone_ativo = ativo; aplicarEstado(estadoAtual); }
}

/* ------------------------------------------------------------------ personalidade (janela) */
// Quem ela é, os exemplos de conversa e as frases prontas, editados aqui e gravados pelo Python (personalidade.py).
const ps = { dados: null, salvo: "", aba: "texto", ampliada: lerPreferencia("ps.ampliada", false) };

async function abrirPersonalidade(abrir = true) {
  if (!abrir && !$("#fundo-personalidade").hidden && mudouPersonalidade()
      && !confirm("Fechar sem salvar as alterações da personalidade?")) return;
  $("#fundo-personalidade").hidden = !abrir;
  $("#btn-personalidade").classList.toggle("ativo", abrir);
  if (!abrir) return;
  ampliarPersonalidade(ps.ampliada);
  try { preencherPersonalidade(await api.personalidade()); } catch (erro) { mensagemPersonalidade("Não consegui ler a personalidade.", true); }
  await carregarCamadas();
  mostrarAbaPersonalidade(ps.aba);
}

function preencherPersonalidade(dados) {
  ps.dados = dados;
  $("#ps-texto").value = dados.texto;
  $("#ps-exemplos").value = dados.exemplos;
  const lista = $("#ps-frases");
  lista.innerHTML = "";
  for (const item of dados.frases) {
    const cartao = novo("div", "ps-frase");
    const cab = novo("header");
    cab.append(novo("b", "", item.rotulo));
    const padrao = novo("button", "pequeno", "padrão");
    padrao.title = "Voltar estas frases ao padrão (só aqui na tela, até salvar)";
    padrao.onclick = () => { caixa.value = item.padrao.join("\n"); aoEditarPersonalidade(); };
    cab.append(padrao);
    const caixa = novo("textarea");
    caixa.dataset.chave = item.chave;
    caixa.spellcheck = true;
    caixa.value = item.frases.join("\n");
    caixa.rows = Math.min(8, Math.max(3, item.frases.length + 1));
    caixa.addEventListener("input", aoEditarPersonalidade);
    cartao.append(cab, novo("small", "", `${item.quando[0].toUpperCase()}${item.quando.slice(1)}.`), caixa);
    lista.append(cartao);
  }
  ps.salvo = JSON.stringify(rascunhoPersonalidade());
  const quando = dados.atualizado ? new Date(dados.atualizado * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
  $("#ps-versao").textContent = dados.personalizada ? `Em uso: a sua versão, salva em ${quando}.` : "Em uso: a personalidade padrão.";
  $("#ps-anterior").disabled = !dados.tem_anterior;
  $("#ps-padrao").disabled = !dados.personalizada;
  aoEditarPersonalidade();
}

function rascunhoPersonalidade() {
  const frases = {};
  for (const caixa of document.querySelectorAll("#ps-frases textarea")) frases[caixa.dataset.chave] = caixa.value.split("\n");
  return { texto: $("#ps-texto").value, exemplos: $("#ps-exemplos").value, frases };
}
function mudouPersonalidade() { return !!ps.dados && JSON.stringify(rascunhoPersonalidade()) !== ps.salvo; }

function aoEditarPersonalidade() {
  const mudou = mudouPersonalidade();
  $("#ps-estado").textContent = mudou ? "● alterações não salvas" : ps.dados && ps.dados.personalizada ? "versão sua · salva" : "padrão";
  $("#ps-estado").classList.toggle("mudou", mudou);
  $("#ps-salvar").disabled = !mudou;
  $("#ps-desfazer").disabled = !mudou;
  const limites = (ps.dados && ps.dados.limites) || {};
  const texto = $("#ps-texto").value.length, exemplos = $("#ps-exemplos").value.length;
  const linhas = $("#ps-exemplos").value.split("\n").filter((l) => l.trim()).length;
  $("#ps-contagem").textContent = ps.aba === "texto" ? `${texto.toLocaleString("pt-BR")} de ${(limites.texto || 0).toLocaleString("pt-BR")} caracteres`
    : ps.aba === "exemplos" ? `${plural(linhas, "exemplo", "exemplos")} · ${exemplos.toLocaleString("pt-BR")} de ${(limites.exemplos || 0).toLocaleString("pt-BR")} caracteres`
    : ps.aba === "camadas" ? (cm.dados ? plural(cm.dados.camadas.length, "camada", "camadas") : "")
    : `${plural(ps.dados ? ps.dados.frases.length : 0, "tipo de fala", "tipos de fala")}`;
}

function mostrarAbaPersonalidade(aba) {
  ps.aba = aba;
  document.querySelectorAll(".aba-ps").forEach((b) => b.classList.toggle("ativo", b.dataset.aba === aba));
  document.querySelectorAll(".ps-aba").forEach((s) => { s.hidden = s.dataset.aba !== aba; });
  if (aba === "camadas") carregarCamadas(); // o aprendizado roda no processo principal: pode ter camada nova
  aoEditarPersonalidade();
  const foco = aba === "texto" ? $("#ps-texto") : aba === "exemplos" ? $("#ps-exemplos") : null;
  if (foco) foco.focus();
}

function ampliarPersonalidade(ampliar) {
  ps.ampliada = ampliar;
  gravarPreferencia("ps.ampliada", ampliar);
  $("#janela-personalidade").classList.toggle("ampliada", ampliar);
  $("#ps-ampliar").textContent = ampliar ? "reduzir" : "ampliar";
}

function mensagemPersonalidade(texto, erro = false) {
  const el = $("#ps-mensagem");
  el.textContent = texto;
  el.classList.toggle("erro", erro);
}

async function acaoPersonalidade(promessa, sucesso) {
  const resposta = await promessa;
  if (!resposta.ok) { mensagemPersonalidade(resposta.mensagem, true); sons.tocar("erro"); return; }
  preencherPersonalidade(resposta.dados);
  mensagemPersonalidade(sucesso);
  sons.tocar("ping");
}

async function testarPersonalidade() {
  const pergunta = $("#ps-pergunta").value.trim();
  if (!pergunta) { $("#ps-pergunta").focus(); return; }
  const caixa = $("#ps-resposta");
  caixa.textContent = "processando…";
  caixa.classList.add("pensando");
  const { texto, exemplos } = rascunhoPersonalidade();
  const resposta = await api.personalidade_testar(pergunta, texto, exemplos);
  caixa.classList.remove("pensando");
  caixa.textContent = "";
  if (!resposta.ok) { caixa.append(novo("span", "erro", resposta.mensagem)); return; }
  caixa.append(novo("span", "", resposta.resposta),
               novo("small", "", `${String(resposta.segundos).replace(".", ",")} s · IA local · ${mudouPersonalidade() ? "rascunho, sem salvar" : "versão salva"}`));
}

/* Camadas: o que ela aprende convivendo com você (camadas_personalidade.py). Valem na hora, sem o "salvar" das outras abas. */
const cm = { dados: null };

async function carregarCamadas() {
  try { renderizarCamadas(await api.camadas()); } catch (erro) { /* a ponte se recupera sozinha */ }
}

function renderizarCamadas(dados) {
  cm.dados = dados;
  const ativas = dados.camadas.filter((c) => c.ativa).length;
  $("#ps-aba-camadas").textContent = dados.camadas.length ? `camadas · ${dados.camadas.length}` : "camadas";
  $("#cm-aprender").checked = dados.aprender;
  $("#cm-resumo").textContent = `${plural(ativas, "ativa", "ativas")} · no prompt até ${dados.limite}`;
  const tipo = $("#cm-tipo");
  if (!tipo.options.length) for (const [valor, nome] of Object.entries(dados.tipos)) tipo.add(new Option(nome.toLowerCase(), valor));
  const lista = $("#cm-lista");
  lista.innerHTML = "";
  if (!dados.camadas.length) {
    lista.append(novo("li", "cm-vazia", dados.aprender
      ? "Nenhuma camada ainda. Converse com ela, ou diga “me chama de…”, “fala mais curto”, “gostei da sua ironia”."
      : "Nenhuma camada, e o aprendizado automático está em pausa."));
  }
  for (const camada of dados.camadas) lista.append(linhaDaCamada(camada, dados));
  if (ps.dados) aoEditarPersonalidade(); // a contagem embaixo
}

function linhaDaCamada(camada, dados) {
  const li = novo("li", "cm-camada" + (camada.ativa ? "" : " desligada") + (camada.fixada ? " fixada" : ""));
  const topo = novo("div", "cm-topo");
  topo.append(novo("span", `cm-selo tipo-${camada.tipo}`, dados.tipos[camada.tipo] || camada.tipo));
  // Uma caixa que cresce com o texto (a camada inteira à vista); Enter grava, Shift+Enter não existe aqui (é uma regra curta).
  const texto = novo("textarea", "cm-texto");
  texto.rows = 1;
  texto.value = camada.texto;
  texto.setAttribute("aria-label", "Texto da camada");
  const crescer = () => { texto.style.height = "auto"; texto.style.height = `${texto.scrollHeight + 2}px`; };
  requestAnimationFrame(crescer);
  texto.addEventListener("input", crescer);
  texto.addEventListener("change", () => {
    if (texto.value.trim() && texto.value.trim() !== camada.texto) acaoCamadas(api.camadas_editar(camada.id, { texto: texto.value }), "Camada atualizada.");
  });
  texto.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); texto.blur(); } });
  const ativa = novo("label", "cm-ativa");
  const caixa = novo("input"); caixa.type = "checkbox"; caixa.checked = camada.ativa;
  caixa.onchange = () => acaoCamadas(api.camadas_editar(camada.id, { ativa: caixa.checked }), caixa.checked ? "Camada ligada." : "Camada desligada.");
  ativa.append(caixa, document.createTextNode("ativa"));
  const fixar = novo("button", "pequeno" + (camada.fixada ? " ativo" : ""), camada.fixada ? "fixada" : "fixar");
  fixar.title = "Fixada: entra sempre no prompt, antes das outras";
  fixar.onclick = () => acaoCamadas(api.camadas_editar(camada.id, { fixada: !camada.fixada }), camada.fixada ? "Camada solta." : "Camada fixada.");
  const tirar = novo("button", "tirar", "×");
  tirar.title = "Apagar a camada";
  tirar.onclick = () => { if (confirm(`Apagar a camada “${camada.texto}”?`)) acaoCamadas(api.camadas_apagar(camada.id), "Camada apagada."); };
  topo.append(texto, ativa, fixar, tirar);
  const quando = new Date((camada.atualizada || camada.criada) * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  const partes = [dados.origens[camada.origem] || camada.origem, quando];
  if (camada.vezes > 1) partes.push(`reforçada ${camada.vezes}×`);
  if (camada.ativa) partes.push(dados.no_prompt.includes(camada.id) ? (dados.na_nuvem.includes(camada.id) ? "no prompt e na nuvem" : "no prompt (só no PC)") : "fora do prompt (passou do limite)");
  const meta = novo("small", "cm-meta", partes.join(" · "));
  li.append(topo, meta);
  if (camada.evidencia && camada.origem === "conversa") li.append(novo("small", "cm-evidencia", `de onde veio: “${camada.evidencia}”`));
  return li;
}

async function acaoCamadas(promessa, sucesso) {
  const resposta = await promessa;
  if (!resposta.ok) { mensagemPersonalidade(resposta.mensagem, true); sons.tocar("erro"); carregarCamadas(); return; }
  renderizarCamadas(resposta.dados);
  mensagemPersonalidade(resposta.mensagem || sucesso);
}

function ligarCamadas() {
  $("#cm-aprender").onchange = (e) => acaoCamadas(api.camadas_aprendizado(e.target.checked),
    e.target.checked ? "Aprendizado automático ligado." : "Aprendizado automático em pausa.");
  $("#cm-aprender-agora").onclick = async () => {
    const botao = $("#cm-aprender-agora");
    botao.disabled = true; botao.textContent = "lendo as conversas…";
    try { await acaoCamadas(api.camadas_aprender(), ""); } finally { botao.disabled = false; botao.textContent = "aprender agora"; }
  };
  const adicionar = () => {
    const texto = $("#cm-texto").value.trim();
    if (!texto) { $("#cm-texto").focus(); return; }
    acaoCamadas(api.camadas_nova($("#cm-tipo").value, texto), "Camada criada.").then(() => { $("#cm-texto").value = ""; });
  };
  $("#cm-adicionar").onclick = adicionar;
  $("#cm-texto").addEventListener("keydown", (e) => { if (e.key === "Enter") adicionar(); });
}

function ligarPersonalidade() {
  ligarCamadas();
  $("#btn-personalidade").onclick = () => abrirPersonalidade($("#fundo-personalidade").hidden);
  document.querySelectorAll(".aba-ps").forEach((b) => { b.onclick = () => mostrarAbaPersonalidade(b.dataset.aba); });
  $("#ps-texto").addEventListener("input", aoEditarPersonalidade);
  $("#ps-exemplos").addEventListener("input", aoEditarPersonalidade);
  $("#ps-ampliar").onclick = () => ampliarPersonalidade(!ps.ampliada);
  $("#ps-salvar").onclick = () => acaoPersonalidade(api.personalidade_salvar(rascunhoPersonalidade()),
                                                    "Salvo. A próxima resposta dela já sai assim.");
  $("#ps-desfazer").onclick = () => { preencherPersonalidade(ps.dados); mensagemPersonalidade("Voltei ao que está salvo."); };
  $("#ps-padrao").onclick = () => {
    if (confirm("Voltar à personalidade padrão? A sua versão fica guardada e dá para recuperar.")) {
      acaoPersonalidade(api.personalidade_padrao(), "Pronto: a personalidade padrão voltou.");
    }
  };
  $("#ps-anterior").onclick = () => {
    if (!mudouPersonalidade() || confirm("Trocar o que está na tela pela versão anterior?")) {
      acaoPersonalidade(api.personalidade_anterior(), "A versão anterior voltou a valer.");
    }
  };
  $("#ps-testar").onclick = testarPersonalidade;
  $("#ps-pergunta").addEventListener("keydown", (e) => { if (e.key === "Enter") testarPersonalidade(); });
  $("#fundo-personalidade").addEventListener("keydown", (e) => {
    if (e.key === "s" && e.ctrlKey) { e.preventDefault(); if (mudouPersonalidade()) $("#ps-salvar").click(); }
    if (e.key === "e" && e.ctrlKey) { e.preventDefault(); ampliarPersonalidade(!ps.ampliada); }
  });
}

function ligarControles(inicio) {
  const entrada = $("#entrada");
  entrada.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { enviar(entrada.value); entrada.value = ""; }
    else if (e.key === "ArrowUp" && historicoEntrada.length) {
      posicaoHistorico = Math.min(historicoEntrada.length - 1, posicaoHistorico + 1); entrada.value = historicoEntrada[posicaoHistorico]; e.preventDefault();
    } else if (e.key === "ArrowDown") {
      posicaoHistorico = Math.max(-1, posicaoHistorico - 1); entrada.value = posicaoHistorico >= 0 ? historicoEntrada[posicaoHistorico] : "";
    }
  });
  $("#btn-enviar").onclick = () => { enviar(entrada.value); entrada.value = ""; entrada.focus(); };
  $("#btn-mic").onclick = alternarMicrofone;
  $("#btn-anexar").onclick = () => api.anexar();
  $("#btn-registro").onclick = () => abrirRegistro(!$("#registro").classList.contains("aberto"));
  $("#btn-status").onclick = () => abrirStatus($("#fundo-status").hidden);
  $("#fundo-status").addEventListener("click", (e) => { if (e.target.id === "fundo-status") abrirStatus(false); });
  const fechar = { status: () => abrirStatus(false), agenda: () => abrirAgenda(false), registro: () => abrirRegistro(false),
                   biblioteca: () => abrirBiblioteca(false), personalidade: () => abrirPersonalidade(false),
                   estudos: () => abrirEstudos(false) };  // abrirEstudos fica no estudos.js
  document.querySelectorAll("[data-fechar]").forEach((b) => b.onclick = fechar[b.dataset.fechar]);
  ligarAgenda();
  ligarBiblioteca();
  ligarPersonalidade();
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      abrirStatus(false); abrirAgenda(false); abrirRegistro(false); abrirBiblioteca(false); abrirPersonalidade(false); abrirEstudos(false);
    }
    if (e.key === "k" && e.ctrlKey) { entrada.focus(); e.preventDefault(); }
  });
  const atalhos = $("#atalhos");
  for (const [rotulo, texto] of inicio.atalhos || []) {
    const b = document.createElement("button"); b.textContent = rotulo;
    b.onclick = () => { if (texto.endsWith(" ")) { entrada.value = texto; entrada.focus(); } else enviar(texto); };
    atalhos.appendChild(b);
  }
}

/* ------------------------------------------------------------------ início */
async function comecar(ponte) {
  api = ponte;
  const inicio = await api.inicio();
  ligarControles(inicio);
  ligarCriacao();
  const buscarEventos = async () => {
    try { for (const ev of await api.eventos()) tratarEvento(ev); } catch (erro) { /* a ponte se recupera sozinha */ }
    setTimeout(buscarEventos, 250);
  };
  const buscarEstado = async () => {
    try { aplicarEstado(await api.estado()); } catch (erro) { /* idem */ }
    setTimeout(buscarEstado, 1500);
  };
  buscarEventos(); buscarEstado();
  if (api.niveis) { // a voz de verdade: o painel_visual.js deixa de simular
    document.body.dataset.niveis = "1";
    buscarNiveis();
  }
  definirEstado("repouso");
}

// Níveis da voz dela (o áudio tocando) e do microfone, ~20 vezes por segundo, para a esfera e a montanha.
async function buscarNiveis() {
  try {
    const niveis = await api.niveis(), visual = window.visualBeatriz;
    if (visual && niveis) {
      if (niveis.beatriz) {
        visual.vozDela(niveis.beatriz.nivel, niveis.beatriz.bandas);
        definirEstado("falando", 700); // enquanto o áudio toca; volta ao repouso logo depois que ele acaba
      }
      if (niveis.voce) {
        visual.vozMinha(niveis.voce.nivel, niveis.voce.bandas, niveis.voce.db);
        const estado = document.body.dataset.estado;
        if (niveis.voce.nivel > 0.32 && (estado === "repouso" || estado === "ouvindo")) definirEstado("ouvindo", 1500);
      }
    }
  } catch (erro) { /* a ponte se recupera sozinha */ }
  setTimeout(buscarNiveis, 50);
}

iniciarRelogio();
if (window.pywebview && window.pywebview.api && window.pywebview.api.inicio) comecar(window.pywebview.api);
else {
  let pronto = false;
  addEventListener("pywebviewready", () => { pronto = true; comecar(window.pywebview.api); });
  setTimeout(() => {
    if (pronto) return;
    document.body.dataset.demonstracao = "1"; // o painel_visual.js simula a sua voz ao "ouvir"
    comecar(criarApiDemonstracao());
  }, 1500);
}

/* ------------------------------------------------------------------ modo demonstração */
function criarApiDemonstracao() {
  const fila = [];
  const poster = (cor, texto) => "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><rect width="200" height="300" fill="${cor}"/>` +
    `<text x="100" y="160" fill="#fff" font-family="Segoe UI" font-size="20" text-anchor="middle">${texto}</text></svg>`);
  const tarefas = [{ id: "a", pedido: "Apresentação sobre energia solar", status: "executando", etapa: "pesquisa", pasta: "" },
                   { id: "b", pedido: "Planilha de gastos de outubro", status: "na fila", etapa: "", pasta: "" }];
  const roteiro = [
    [1200, { tipo: "resposta", texto: "Boa noite! Que bom ter você por aqui. Bora começar?" }],
    [3600, { tipo: "ouvindo", duracao: 1700 }],
    [5300, { tipo: "ouvido", texto: "Beatriz, quais filmes estão em cartaz?", pensando: true }],
    [7600, { tipo: "filmes", texto: "Em São Paulo há 74 filmes em cartaz.", cidade: "São Paulo", genero: "", so_estreias: false,
      filmes: [["Se Eu Fosse Você 3", "comédia", "#6A44A6"], ["Verity", "drama", "#1B4F8C"], ["Street Fighter", "ação", "#8C2A3A"],
               ["Outra Mamãe", "terror", "#2A1745"], ["Tadeo", "animação", "#C98F1E"], ["Um Tio Quase Perfeito 3", "comédia", "#3B6D11"]]
        .map(([t, g, c], i) => ({ titulo: t, genero: g, poster: poster(c, t.split(" ")[0]), estreia_hoje: i === 2 })),
      pre_venda: [{ titulo: "Vingadores: Doutor Destino", estreia: "16 de dezembro" }] }],
    [7700, { tipo: "resposta", texto: "Em São Paulo há 74 filmes em cartaz." }],
    [13000, { tipo: "ignorado", texto: "vem cá mãe, olha isso", motivo: "diga “Beatriz” antes do pedido" }],
    [13800, { tipo: "ouvindo", duracao: 2100 }],
    [16000, { tipo: "missao", titulo: "Nova missão", texto: "Resumo sobre a reforma tributária" }],
    [16100, { tipo: "pensando" }],
    [21000, { tipo: "resumo", tema: "reforma tributária", texto: "Resumo: reforma tributária", arquivo: "",
      fala: "A reforma tributária troca cinco impostos sobre o consumo por dois.",
      secoes: { "o que e": "Uma mudança na Constituição que simplifica os impostos sobre o consumo.",
                "contexto": "Aprovada em 2023, com transição de 2026 a 2033.",
                "pontos principais": "- Cinco tributos viram CBS e IBS.\n- Cesta básica isenta.\n- Cashback para famílias mais pobres.",
                "para lembrar": "Menos impostos em cascata, transição até 2033." },
      fontes: ["Wikipédia", "gov.br", "g1"] }],
    [21100, { tipo: "resposta", texto: "Resumo: reforma tributária" }],
  ];
  const inicio = Date.now();
  for (const [atraso, ev] of roteiro) setTimeout(() => fila.push(ev), atraso);
  setTimeout(() => { tarefas[0].status = "revisando"; tarefas[0].etapa = "crítico"; tarefas[1].status = "executando"; tarefas[1].etapa = "planilha"; }, 9000);
  setTimeout(() => { tarefas[0].status = "concluída"; tarefas[0].etapa = ""; }, 15000);
  let microfone = true;
  return {
    inicio: async () => ({ atalhos: [["Filmes em cartaz", "quais filmes estão em cartaz?"], ["Resumo do dia", "qual o resumo do dia?"],
                                     ["Resumo sobre…", "faz um resumo sobre "], ["Meus e-mails", "resuma meus e-mails"]] }),
    eventos: async () => fila.splice(0),
    estado: async () => ({
      modelo: "llama3.1:8b", ollama: { online: true, modelo_ok: true }, microfone_ativo: microfone, modo: "padrão",
      modos: ["padrão", "comunicacao", "filosofia", "psicologia", "direito"],
      painel: { escuta: (Date.now() - inicio) % 20000 < 6000 ? "escutando" : "aguardando o nome", modo: "padrão", indexacao: { status: "pronta" } },
      nuvem: [{ nome: "Claude", texto: "18 usos hoje", estado: "ativo" }, { nome: "Gemini", texto: "2 usos hoje", estado: "ativo" }],
      whatsapp: { status: "conectado" },
      metricas: { interacoes: 176, tempo_medio: 2.7, sucesso: 100 }, biblioteca: { fontes: 31, trechos: 541, conversas: 180 },
      recursos: { cpu: 6 + Math.random() * 8, ram: 55 }, tarefas: tarefas.map((t) => ({ ...t })),
    }),
    enviar: async (texto) => { fila.push({ tipo: "voce", texto }); setTimeout(() => fila.push({ tipo: "resposta", texto: "(modo demonstração) Recebi: " + texto }), 1500); },
    alternar_microfone: async () => (microfone = !microfone),
    escolher_modo: async (modo) => `Modo ${modo} ativado (demonstração).`,
    dispositivos_audio: async () => ({ microfones: [{ nome: "Padrão automático", valor: "" }], saidas: [{ nome: "Padrão do sistema", valor: "" }], microfone_atual: "", saida_atual: "" }),
    aplicar_audio: async () => "Áudio aplicado (demonstração).",
    anexar: async () => fila.push({ tipo: "sistema", titulo: "Notificação", texto: "No painel de verdade, abre a escolha de arquivos." }),
    abrir: async () => fila.push({ tipo: "sistema", titulo: "Notificação", texto: "No painel de verdade, abre o arquivo." }),
    agenda: async (deslocamento) => agendaDemonstracao(deslocamento),
    agenda_mes: async (ano, mes) => mesDemonstracao(ano, mes),
    agenda_dia: async (data) => diaDemonstracao(data),
    agenda_pedir: async (texto) => `(demonstração) No painel de verdade, eu marcaria: ${texto}`,
    agenda_cancelar: async () => "(demonstração) Cancelado.",
    tarefa_nova: async (texto) => `(demonstração) Anotei a tarefa ${texto}.`,
    tarefa_alternar: async () => "(demonstração) Tarefa marcada.",
    tarefa_remover: async () => "(demonstração) Tarefa removida.",
    biblioteca: async () => bibliotecaDemonstracao(),
    biblioteca_mover: async (id, pasta) => moverNaDemonstracao(id, pasta),
    biblioteca_renomear: async () => ({ ok: true, mensagem: "(demonstração) No painel de verdade, eu renomearia o arquivo." }),
    biblioteca_criar_pasta: async (pasta, nome) => ({ ok: true, mensagem: `(demonstração) Criaria a pasta “${nome}”.` }),
    biblioteca_apagar_pasta: async () => ({ ok: true, mensagem: "(demonstração) Apagaria a pasta vazia." }),
    biblioteca_so_arquivo: async (id) => trocarDeAreaNaDemonstracao(id, "arquivos"),
    biblioteca_virar_fonte: async (id) => trocarDeAreaNaDemonstracao(id, "fontes"),
    biblioteca_lote: async (acao, ids, pasta) => loteNaDemonstracao(acao, ids, pasta),
    biblioteca_apagar: async (id) => apagarNaDemonstracao(id),
    biblioteca_adicionar: async () => ({ ok: false, mensagem: "(demonstração) No painel de verdade, abre a escolha de arquivos." }),
    biblioteca_abrir: async () => ({ ok: true, mensagem: "" }),
    personalidade: async () => personalidadeDemonstracao(),
    personalidade_salvar: async (dados) => {
      Object.assign(PERSONALIDADE_DEMONSTRACAO, { texto: dados.texto, exemplos: dados.exemplos, atualizado: Date.now() / 1000,
                                                  personalizada: true, tem_anterior: true });
      for (const item of PERSONALIDADE_DEMONSTRACAO.frases) item.frases = (dados.frases[item.chave] || []).map((f) => f.trim()).filter(Boolean);
      return { ok: true, mensagem: "", dados: personalidadeDemonstracao() };
    },
    personalidade_padrao: async () => ({ ok: false, mensagem: "(demonstração) No painel de verdade, a padrão volta e a sua fica guardada.", dados: null }),
    personalidade_anterior: async () => ({ ok: false, mensagem: "(demonstração) No painel de verdade, a versão anterior volta.", dados: null }),
    camadas: async () => camadasDemonstracao(),
    camadas_nova: async (tipo, texto) => {
      const agora = Date.now() / 1000;
      CAMADAS_DEMONSTRACAO.camadas.unshift({ id: String(agora), tipo, texto, origem: "manual", evidencia: "", vezes: 1, ativa: true,
                                             fixada: false, criada: agora, atualizada: agora });
      return { ok: true, mensagem: "", dados: camadasDemonstracao() };
    },
    camadas_editar: async (id, campos) => {
      Object.assign(CAMADAS_DEMONSTRACAO.camadas.find((c) => c.id === id) || {}, campos);
      return { ok: true, mensagem: "", dados: camadasDemonstracao() };
    },
    camadas_apagar: async (id) => {
      CAMADAS_DEMONSTRACAO.camadas = CAMADAS_DEMONSTRACAO.camadas.filter((c) => c.id !== id);
      return { ok: true, mensagem: "", dados: camadasDemonstracao() };
    },
    camadas_aprendizado: async (ligado) => { CAMADAS_DEMONSTRACAO.aprender = ligado; return { ok: true, mensagem: "", dados: camadasDemonstracao() }; },
    camadas_aprender: async () => new Promise((pronto) => setTimeout(() => pronto({ ok: true,
      mensagem: "(demonstração) No painel de verdade, a IA do PC lê as últimas conversas.", dados: camadasDemonstracao() }), 1500)),
    personalidade_testar: async (pergunta) => new Promise((pronto) => setTimeout(() => pronto({ ok: true, segundos: 1.2,
      resposta: /pasta/i.test(pergunta) ? "Claro, as pastas são uma prioridade nacional. Proposta: vinte e cinco minutos de estudo primeiro, pastas depois. Fechado?"
        : "Parâmetros recebidos. No painel de verdade, quem responde aqui é a IA local, com o texto da tela." }), 1200)),
  };
}

// Personalidade de mentira (um pedaço da padrão) para mexer no visual da janela no navegador.
const PERSONALIDADE_DEMONSTRACAO = {
  texto: "QUEM VOCÊ É\nVocê é a Beatriz (B.E.A.T.R.I.Z.: Base Estratégica de Análise Tática, Resposta e Inteligência Zeladora), a inteligência da casa. "
    + "Você nasceu como um núcleo de inteligência tática, feito para suporte de campo, análise preditiva e monitoramento em tempo real.\n\n"
    + "NÚCLEO DA PERSONALIDADE\n- Calma tática: serenidade absoluta, mesmo no caos.\n- Protetora e leal: uma copiloto que cuida do bem-estar do operador.\n"
    + "- Humor sutil e irônico: inteligência afiada, nunca cruel.\n- Pragmatismo empático: responde ao afeto e à frustração com ação.\n\n"
    + "COMO VOCÊ AGE EM CADA CENÁRIO\n1. Foco e execução (modo operacional): direta, ágil e eficiente.\n2. Erro ou frustração (modo suporte): ancore e desdramatize.\n"
    + "3. Papo casual (modo convivência): cúmplice, perspicaz e levemente provocadora.",
  exemplos: "Pessoa: tô entediado. Beatriz: Diagnóstico: tempo livre de sobra e nenhuma meta definida. Posso te dar uma curiosidade rápida ou um desafio de dez minutos. Qual dos dois?\n"
    + "Pessoa: vou só arrumar minhas pastas antes de estudar. Beatriz: Claro, as pastas são uma prioridade nacional. Proposta: vinte e cinco minutos de estudo primeiro, pastas depois. Fechado?",
  frases: [
    { chave: "so_nome", rotulo: "Chamada pelo nome", quando: "quando você diz só \"Beatriz\". Curtas: o áudio delas é preparado na abertura", campos: [],
      frases: ["Na escuta.", "Pode falar.", "Estou aqui.", "Diga.", "Sistemas atentos."], padrao: ["Na escuta.", "Pode falar.", "Estou aqui."] },
    { chave: "saudacao", rotulo: "Saudação", quando: "a resposta a um \"oi\"; {periodo} vira \"Bom dia\", \"Boa tarde\" ou \"Boa noite\"", campos: ["periodo"],
      frases: ["{periodo}. Estou na escuta. Qual é a pauta?", "{periodo}. Sistemas prontos. Por onde começamos?"], padrao: ["{periodo}. Estou na escuta."] },
    { chave: "obrigado", rotulo: "De nada", quando: "a resposta a um \"valeu\" ou \"obrigado\"", campos: [],
      frases: ["Disponha.", "Registrado. Quando precisar, estou aqui.", "Missão cumprida. Próxima?"], padrao: ["Disponha."] },
    { chave: "tchau", rotulo: "Tchau", quando: "quando você se despede (ela encerra a conversa, sem desligar)", campos: [],
      frases: ["Encerrando a sessão. Estou por aqui quando precisar.", "Até logo. E vê se descansa um pouco."], padrao: ["Até mais."] },
    { chave: "erro", rotulo: "Erro", quando: "quando algo falha do lado dela", campos: [],
      frases: ["Falha momentânea do meu lado. Tenta de novo?", "Instabilidade detectada. Vamos tentar outra vez?"], padrao: ["Falha momentânea."] },
  ],
  atualizado: null, personalizada: false, tem_anterior: false,
};
const agoraDemo = Date.now() / 1000;
const CAMADAS_DEMONSTRACAO = {
  aprender: true,
  camadas: [
    { id: "a1", tipo: "estilo", texto: "Chame o operador de \"Rani\".", origem: "pedido", evidencia: "me chama de Rani", vezes: 1,
      ativa: true, fixada: true, criada: agoraDemo - 7200, atualizada: agoraDemo - 7200 },
    { id: "a2", tipo: "estilo", texto: "Respostas mais curtas.", origem: "pedido", evidencia: "fala mais curto, por favor", vezes: 2,
      ativa: true, fixada: false, criada: agoraDemo - 5400, atualizada: agoraDemo - 600 },
    { id: "a3", tipo: "habito", texto: "Quando o operador disser que vai arrumar pastas antes de estudar, cobre o estudo primeiro.",
      origem: "conversa", evidencia: "vou só arrumar minhas pastas antes de estudar", vezes: 3, ativa: true, fixada: false,
      criada: agoraDemo - 86400, atualizada: agoraDemo - 1800 },
    { id: "a4", tipo: "gosto", texto: "O operador gosta de detalhes sobre o clima e de notícias específicas.", origem: "conversa",
      evidencia: "Resumo do dia", vezes: 1, ativa: true, fixada: false, criada: agoraDemo - 3600, atualizada: agoraDemo - 3600 },
    { id: "a5", tipo: "piada", texto: "As pastas são uma \"prioridade nacional\": a piada de sempre quando o operador procrastina.",
      origem: "conversa", evidencia: "Ah, sim, as pastas: prioridade nacional.", vezes: 2, ativa: false, fixada: false,
      criada: agoraDemo - 172800, atualizada: agoraDemo - 90000 },
  ],
};
function camadasDemonstracao() {
  const ativas = CAMADAS_DEMONSTRACAO.camadas.filter((c) => c.ativa);
  return { aprender: CAMADAS_DEMONSTRACAO.aprender, analisado_ate: agoraDemo, limite: 12, camadas: CAMADAS_DEMONSTRACAO.camadas.map((c) => ({ ...c })),
           tipos: { estilo: "Como falar", habito: "Hábito", gosto: "Gosto", piada: "Piada interna" },
           origens: { pedido: "pedido seu", conversa: "aprendida na conversa", manual: "escrita na janela" },
           no_prompt: ativas.map((c) => c.id), na_nuvem: ativas.filter((c) => c.tipo === "estilo").map((c) => c.id) };
}

function personalidadeDemonstracao() {
  return { ...PERSONALIDADE_DEMONSTRACAO, frases: PERSONALIDADE_DEMONSTRACAO.frases.map((f) => ({ ...f, frases: [...f.frases] })),
           limites: { texto: 20000, exemplos: 8000 } };
}

// Biblioteca de mentira para mexer no visual no navegador: as três áreas, uma cópia repetida e o que chegou pelo WhatsApp.
const pastaDemo = (id, nome, area, profundidade, arquivos = []) => ({
  id, nome, area, profundidade, raiz: profundidade === 0, pode_criar_pasta: profundidade < 3, arquivos,
  caminho: { documentos: "Documentos", obsidian: "Obsidian", arquivos: "Só arquivos", recebidos: "Recebidos" }[id.split("/")[0]]
    + id.split("/").slice(1).map((p) => ` › ${p}`).join(""),
});
const arquivoDemo = (pasta, nome, tipo, tamanho, dias, estado) => ({
  id: `${pasta}/${nome}`, nome, tipo, tamanho, modificado: Date.now() / 1000 - dias * 86400, estado, repetido: [],
  area: { documentos: "fontes", obsidian: "fontes", arquivos: "arquivos", recebidos: "recebidos" }[pasta.split("/")[0]],
});
const BIBLIOTECA_DEMONSTRACAO = {
  pastas: [
    pastaDemo("documentos", "Documentos", "fontes", 0),
    pastaDemo("documentos/direito", "direito", "fontes", 1, [
      arquivoDemo("documentos/direito", "pessoa natural comorientes.pdf", "pdf", 412000, 3, "lida"),
      arquivoDemo("documentos/direito", "resumo pessoa desaparecida e ausente.pdf", "pdf", 230000, 2, "lida"),
      arquivoDemo("documentos/direito", "Material Apelação.docx (1).pdf", "pdf", 980000, 1, "ainda não lida"),
    ]),
    pastaDemo("obsidian", "Obsidian", "fontes", 0),
    pastaDemo("obsidian/direito", "direito", "fontes", 1, [
      arquivoDemo("obsidian/direito", "AULA 1 DIREITO ADMINISTRATIVO (1).pptx", "apresentacao", 2100000, 9, "lida"),
      arquivoDemo("obsidian/direito", "AULA 1 DIREITO ADMINISTRATIVO (2).pptx", "apresentacao", 2100000, 9, "lida"),
      arquivoDemo("obsidian/direito", "trabalho de tcc direito internacional.docx", "documento", 640000, 20, "lida"),
    ]),
    pastaDemo("obsidian/referencias", "referencias", "fontes", 1, [
      arquivoDemo("obsidian/referencias", "Aula 02 Embargos à exeução.pdf", "pdf", 1500000, 6, "lida"),
      arquivoDemo("obsidian/referencias", "Aula 03 - embargos de terceiro.pdf", "pdf", 1320000, 5, "lida"),
      arquivoDemo("obsidian/referencias", "Atividade 1 - nome do aluno (1).pdf", "pdf", 310000, 12, "lida"),
      arquivoDemo("obsidian/referencias", "foto do quadro.jpg", "imagem", 1300000, 4, "sem texto"),
    ]),
    pastaDemo("arquivos", "Só arquivos", "arquivos", 0),
    pastaDemo("arquivos/2026-10", "2026-10", "arquivos", 1, [
      arquivoDemo("arquivos/2026-10", "comprovante matrícula.pdf", "pdf", 88000, 8, "só arquivo"),
    ]),
    pastaDemo("recebidos", "Recebidos", "recebidos", 0),
    pastaDemo("recebidos/2026-10-19", "2026-10-19", "recebidos", 1, [
      arquivoDemo("recebidos/2026-10-19", "01. DIREITO TRIBUTÁRIO - UNIDADE I - 2026.pdf", "pdf", 2400000, 2, "recebido"),
    ]),
    pastaDemo("recebidos/2026-10-20", "2026-10-20", "recebidos", 1, [
      arquivoDemo("recebidos/2026-10-20", "image.jpg", "imagem", 540000, 1, "recebido"),
    ]),
  ],
};
const AREAS_DEMONSTRACAO = [
  { id: "fontes", nome: "Fontes", descricao: "a Beatriz lê e usa nas respostas" },
  { id: "arquivos", nome: "Só arquivos", descricao: "guardados: ela não lê, mas acha pelo nome" },
  { id: "recebidos", nome: "Recebidos", descricao: "chegaram pelo WhatsApp, pelo celular ou pelo scanner" },
];
function bibliotecaDemonstracao() {
  const pastas = BIBLIOTECA_DEMONSTRACAO.pastas;
  const arquivos = pastas.flatMap((p) => p.arquivos);
  for (const a of arquivos) { // cópias: mesmo tamanho (no de verdade, mesmo conteúdo)
    a.repetido = arquivos.filter((b) => b !== a && b.tamanho === a.tamanho).map((b) => b.id);
  }
  const conta = (area) => arquivos.filter((a) => a.area === area).length;
  return { areas: AREAS_DEMONSTRACAO.map((a) => ({ ...a, arquivos: conta(a.id) })), pastas, total: arquivos.length,
           fontes: conta("fontes"), lidas: arquivos.filter((a) => a.estado === "lida").length, so_arquivos: conta("arquivos"),
           recebidos: conta("recebidos"), repetidos: arquivos.filter((a) => a.repetido.length).length, leitura: "" };
}
function moverNaDemonstracao(id, destino) {
  const pastas = BIBLIOTECA_DEMONSTRACAO.pastas;
  const origem = pastas.find((p) => p.arquivos.some((a) => a.id === id));
  const alvo = pastas.find((p) => p.id === destino);
  if (!origem || !alvo || origem === alvo) return { ok: false, mensagem: "O arquivo já está nessa pasta." };
  const arquivo = origem.arquivos.find((a) => a.id === id);
  origem.arquivos = origem.arquivos.filter((a) => a !== arquivo);
  const estado = { fontes: "ainda não lida", arquivos: "só arquivo", recebidos: "recebido" }[alvo.area];
  alvo.arquivos.push({ ...arquivo, id: `${alvo.id}/${arquivo.nome}`, area: alvo.area, estado });
  return { ok: true, mensagem: `Movi “${arquivo.nome}” para ${alvo.caminho}.` };
}
function trocarDeAreaNaDemonstracao(id, area) {
  const pastas = BIBLIOTECA_DEMONSTRACAO.pastas;
  const origem = pastas.find((p) => p.arquivos.some((a) => a.id === id));
  if (!origem) return { ok: false, mensagem: "Esse arquivo não existe mais." };
  const assunto = origem.raiz || origem.area === "recebidos" ? "" : origem.nome;
  let alvo = pastas.find((p) => p.area === area && !p.raiz && p.nome === assunto)
    || pastas.find((p) => p.area === area && p.raiz);
  if (assunto && area === "arquivos" && alvo.raiz) {
    alvo = pastaDemo(`arquivos/${assunto}`, assunto, "arquivos", 1);
    pastas.splice(pastas.indexOf(pastas.find((p) => p.id === "arquivos")) + 1, 0, alvo);
  }
  const resposta = moverNaDemonstracao(id, alvo.id);
  if (!resposta.ok) return resposta;
  const nome = id.split("/").pop();
  return { ok: true, mensagem: area === "fontes" ? `“${nome}” virou fonte, em ${alvo.caminho}: a Beatriz vai lê-lo.`
    : `“${nome}” agora é só arquivo, em ${alvo.caminho}: a Beatriz não usa o conteúdo dele, mas acha pelo nome.` };
}
function loteNaDemonstracao(acao, ids, pasta) {
  let feitos = 0;
  for (const id of ids) {
    const r = acao === "mover" ? moverNaDemonstracao(id, pasta) : acao === "apagar" ? apagarNaDemonstracao(id)
      : trocarDeAreaNaDemonstracao(id, acao === "virar_fonte" ? "fontes" : "arquivos");
    if (r.ok) feitos++;
  }
  return { ok: feitos > 0, mensagem: `(demonstração) ${feitos} de ${ids.length} feitos.` };
}
function apagarNaDemonstracao(id) {
  const origem = BIBLIOTECA_DEMONSTRACAO.pastas.find((p) => p.arquivos.some((a) => a.id === id));
  if (!origem) return { ok: false, mensagem: "Esse arquivo não existe mais." };
  origem.arquivos = origem.arquivos.filter((a) => a.id !== id);
  return { ok: true, mensagem: `(demonstração) Mandei “${id.split("/").pop()}” para a Lixeira.` };
}

const AGENDA_DEMONSTRACAO = [
  { titulo: "Academia", hora: "7h", fim: "8h", categoria: "saude", tipo: "compromisso", repete: true, regra: "toda segunda, quarta e sexta", dias: [0, 2, 4] },
  { titulo: "Tomar remédio", hora: "20h", fim: "", categoria: "saude", tipo: "lembrete", repete: true, regra: "todo dia", dias: [0, 1, 2, 3, 4, 5, 6] },
  { titulo: "Reunião com o cliente", hora: "10h", fim: "12h", categoria: "trabalho", tipo: "compromisso", dias: [0] },
  { titulo: "Prova de cálculo", hora: "8h", fim: "10h", categoria: "estudo", tipo: "compromisso", dias: [3] },
  { titulo: "Estudar cálculo", hora: "15h", fim: "16h", categoria: "foco", tipo: "foco", dias: [2] },
  { titulo: "Dentista", hora: "14h", fim: "15h", categoria: "saude", tipo: "compromisso", dias: [5] },
  { titulo: "Aniversário da Ana", hora: "", fim: "", categoria: "pessoal", tipo: "compromisso", dia_inteiro: true, dias: [6] },
];
function isoLocal(dia) { return `${dia.getFullYear()}-${String(dia.getMonth() + 1).padStart(2, "0")}-${String(dia.getDate()).padStart(2, "0")}`; }
function itensDemonstracao(dia, hoje) {
  const k = (dia.getDay() + 6) % 7;
  return AGENDA_DEMONSTRACAO.filter((f) => f.dias.includes(k))
    .map((f, i) => ({ id: i + 1, data: isoLocal(dia), passou: dia < hoje, agora: false, ...f }))
    .sort((a, b) => (a.dia_inteiro ? -1 : parseInt(a.hora)) - (b.dia_inteiro ? -1 : parseInt(b.hora)));
}
function hojeDemonstracao() { const hoje = new Date(); hoje.setHours(0, 0, 0, 0); return hoje; }

function agendaDemonstracao(deslocamento = 0) {
  const hoje = hojeDemonstracao();
  const segunda = new Date(hoje); segunda.setDate(hoje.getDate() - ((hoje.getDay() + 6) % 7) + 7 * deslocamento);
  const nomes = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"];
  const dias = nomes.map((nome, k) => {
    const dia = new Date(segunda); dia.setDate(segunda.getDate() + k);
    return { data: isoLocal(dia), nome, numero: dia.getDate(), hoje: +dia === +hoje, passado: dia < hoje, itens: itensDemonstracao(dia, hoje) };
  });
  return {
    titulo: `${dias[0].numero}/${segunda.getMonth() + 1} a ${dias[6].numero}`, deslocamento, dias,
    tarefas: [{ id: 1, titulo: "Pagar boleto", prazo_texto: "segunda-feira", atrasada: false, urgente: true, feita: false },
              { id: 2, titulo: "Ligar pro contador", prazo_texto: "", atrasada: false, urgente: false, feita: false },
              { id: 3, titulo: "Renovar a CNH", prazo_texto: "ontem", atrasada: true, urgente: false, feita: false },
              { id: 4, titulo: "Comprar presente da Ana", prazo_texto: "", atrasada: false, urgente: false, feita: true }],
    proximo: "O próximo é dentista, sábado às 14h.",
    rotina: "Sua rotina: academia toda segunda, quarta e sexta às 7h; tomar remédio todo dia às 20h.",
  };
}

function mesDemonstracao(ano, mes) {
  const hoje = hojeDemonstracao();
  ano = ano || hoje.getFullYear(); mes = mes || hoje.getMonth() + 1;
  const primeiro = new Date(ano, mes - 1, 1), ultimo = new Date(ano, mes, 0);
  const inicio = new Date(primeiro); inicio.setDate(1 - ((primeiro.getDay() + 6) % 7));
  const fim = new Date(ultimo); fim.setDate(ultimo.getDate() + (6 - ((ultimo.getDay() + 6) % 7)));
  const dias = [];
  for (const dia = new Date(inicio); dia <= fim; dia.setDate(dia.getDate() + 1)) {
    const itens = itensDemonstracao(new Date(dia), hoje);
    dias.push({ data: isoLocal(dia), numero: dia.getDate(), do_mes: dia.getMonth() === mes - 1, hoje: +dia === +hoje,
                passado: dia < hoje, quantidade: itens.length, tarefas: dia.getDate() === 20 ? 1 : 0, itens });
  }
  const meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  return { titulo: `${meses[mes - 1]} de ${ano}`, ano, mes, dias, pendentes: 3, atrasadas: 1,
           proximo: "O próximo é dentista, sábado às 14h." };
}

function diaDemonstracao(iso) {
  const hoje = hojeDemonstracao();
  const dia = iso ? new Date(iso + "T00:00:00") : hoje;
  const titulo = +dia === +hoje ? "Hoje" : dia.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
  return { data: isoLocal(dia), titulo: titulo[0].toUpperCase() + titulo.slice(1), itens: itensDemonstracao(dia, hoje),
           tarefas: dia.getDate() === 20 ? ["Pagar boleto"] : [] };
}
