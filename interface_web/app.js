/* Painel da Beatriz no estilo "Sistema sobre a sombra".
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

/* ------------------------------------------------------------------ partículas de sombra */
function iniciarParticulas() {
  const tela = $("#particulas"), ctx = tela.getContext("2d");
  const cores = ["154,123,208", "199,174,240", "63,169,255", "90,66,133", "53,228,255"];
  let particulas = [];
  function ajustar() {
    tela.width = innerWidth * devicePixelRatio; tela.height = innerHeight * devicePixelRatio;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    particulas = Array.from({ length: Math.round(innerWidth / 18) }, () => nova(true));
  }
  function nova(espalhada) {
    const ouro = Math.random() < .06;
    return {
      x: Math.random() * innerWidth, y: espalhada ? Math.random() * innerHeight : innerHeight + 10,
      r: Math.random() * 1.8 + .4, vy: Math.random() * .35 + .08, vx: (Math.random() - .5) * .15,
      a: Math.random() * .5 + .15, cor: ouro ? "255,210,94" : cores[Math.floor(Math.random() * cores.length)],
    };
  }
  function quadro() {
    if (!document.hidden) {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      for (const p of particulas) {
        p.y -= p.vy; p.x += p.vx;
        if (p.y < -10) Object.assign(p, nova(false));
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${p.cor},${p.a})`; ctx.fill();
      }
    }
    requestAnimationFrame(quadro);
  }
  addEventListener("resize", ajustar);
  ajustar(); quadro();
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

/* ------------------------------------------------------------------ o núcleo */
let voltarAoRepouso = null;
function definirEstado(estado, duracaoMs) {
  estadoDoNucleo($("#nucleo"), estado);
  document.body.dataset.estado = estado;
  clearTimeout(voltarAoRepouso);
  if (duracaoMs) voltarAoRepouso = setTimeout(() => definirEstado("repouso"), duracaoMs);
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
  janela.querySelector(".cabecalho-janela").textContent = `[ ${titulo.toUpperCase()} ]`;
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

/* ------------------------------------------------------------------ eventos */
function tratarEvento(ev) {
  const texto = ev.texto || "";
  switch (ev.tipo) {
    case "voce":
      legenda("voce", texto); registrar("voce", texto); definirEstado("pensando"); break;
    case "pensando":
      definirEstado("pensando"); break;
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
      definirEstado("vitoria", 1800); break;
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
    case "abrir_agenda":
      abrirAgenda(true); break;
    case "agenda_mudou":
      if (!$("#fundo-agenda").hidden) carregarAgenda();
      carregarAgendaDaTela();
      break;
  }
}

/* ------------------------------------------------------------------ lanternas (missões dos agentes) */
const CLASSES_STATUS = { "na fila": "fila", "executando": "executando", "revisando": "revisando", "relatando": "relatando",
  "concluída": "concluida", "falhou": "falhou", "cancelada": "falhou", "não realizada": "falhou" };
const ALTURA_STATUS = { fila: 6, executando: 30, revisando: 48, relatando: 62, concluida: 80, falhou: 2 };
const lanternas = new Map();

function atualizarLanternas(tarefas) {
  const ceu = $("#ceu"), vistos = new Set(), primeiraVez = tarefasConhecidas === null;
  tarefasConhecidas = tarefasConhecidas || {};
  tarefas.slice(0, 6).forEach((tarefa, indice) => {
    vistos.add(tarefa.id);
    let lanterna = lanternas.get(tarefa.id);
    if (!lanterna) {
      lanterna = document.createElement("div");
      lanterna.innerHTML = `<div class="bulbo"></div><div class="rotulo"><span></span><small></small></div>`;
      lanterna.style.bottom = "0%";
      ceu.appendChild(lanterna); lanternas.set(tarefa.id, lanterna);
    }
    const classe = CLASSES_STATUS[tarefa.status] || "fila";
    lanterna.className = `lanterna ${classe}`;
    lanterna.style.left = `${22 + (indice % 2) * 46}px`;
    lanterna.style.animationDelay = `${indice * -.7}s`;
    requestAnimationFrame(() => { lanterna.style.bottom = `${ALTURA_STATUS[classe] + (indice % 3) * 3}%`; });
    lanterna.title = `${tarefa.pedido}\n${tarefa.status}${tarefa.etapa ? " · " + tarefa.etapa : ""}`;
    lanterna.querySelector(".rotulo span").textContent = tarefa.pedido;
    lanterna.querySelector(".rotulo small").textContent = tarefa.etapa ? `${tarefa.status} · ${tarefa.etapa}` : tarefa.status;
    lanterna.onclick = () => tarefa.pasta && api.abrir(tarefa.pasta);

    const anterior = tarefasConhecidas[tarefa.id];
    if (!primeiraVez && anterior === undefined && classe !== "concluida") janelaSistema("Nova missão", tarefa.pedido);
    if (!primeiraVez && anterior !== undefined && anterior !== "concluída" && tarefa.status === "concluída") {
      janelaSistema("Missão concluída", tarefa.pedido, {
        vitoria: true, duracao: 12000,
        acao: tarefa.pasta ? { rotulo: "Abrir a pasta", aoClicar: () => api.abrir(tarefa.pasta) } : null,
      });
      definirEstado("vitoria", 1800);
    }
    tarefasConhecidas[tarefa.id] = tarefa.status;
  });
  for (const [id, lanterna] of lanternas) if (!vistos.has(id)) { lanterna.remove(); lanternas.delete(id); }
  $("#ceu-vazio").hidden = lanternas.size > 0;
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
  $("#chip-escuta .ponto").className = "ponto " + (!e.microfone_ativo ? "" : escuta === "escutando" ? "azul" : "verde");
  $("#chip-escuta span").textContent = e.microfone_ativo ? escuta : "microfone pausado";
  $("#chip-modo span").textContent = `modo ${(e.painel && e.painel.modo) || e.modo || "padrão"}`;
  $("#sub-marca").textContent = "Sistema ativo · Guardiã do Sol";
  $("#btn-mic").classList.toggle("ativo", !!e.microfone_ativo);
  atualizarLanternas(e.tarefas || []);
  if (!$("#fundo-status").hidden) renderizarStatus();
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
    <div class="identidade">Beatriz · Guardiã do Sol · assistente pessoal</div>
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
  const fechar = { status: () => abrirStatus(false), agenda: () => abrirAgenda(false), registro: () => abrirRegistro(false) };
  document.querySelectorAll("[data-fechar]").forEach((b) => b.onclick = fechar[b.dataset.fechar]);
  ligarAgenda();
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") { abrirStatus(false); abrirAgenda(false); abrirRegistro(false); }
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
  const buscarEventos = async () => {
    try { for (const ev of await api.eventos()) tratarEvento(ev); } catch (erro) { /* a ponte se recupera sozinha */ }
    setTimeout(buscarEventos, 250);
  };
  const buscarEstado = async () => {
    try { aplicarEstado(await api.estado()); } catch (erro) { /* idem */ }
    setTimeout(buscarEstado, 1500);
  };
  buscarEventos(); buscarEstado();
  definirEstado("repouso");
}

desenharNucleo($("#nucleo"));
iniciarParticulas();
iniciarRelogio();
if (window.pywebview && window.pywebview.api) comecar(window.pywebview.api);
else {
  let pronto = false;
  addEventListener("pywebviewready", () => { pronto = true; comecar(window.pywebview.api); });
  setTimeout(() => { if (!pronto) comecar(criarApiDemonstracao()); }, 1500);
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
    [5200, { tipo: "ouvido", texto: "Beatriz, quais filmes estão em cartaz?", pensando: true }],
    [7600, { tipo: "filmes", texto: "Em São Paulo há 74 filmes em cartaz.", cidade: "São Paulo", genero: "", so_estreias: false,
      filmes: [["Se Eu Fosse Você 3", "comédia", "#6A44A6"], ["Verity", "drama", "#1B4F8C"], ["Street Fighter", "ação", "#8C2A3A"],
               ["Outra Mamãe", "terror", "#2A1745"], ["Tadeo", "animação", "#C98F1E"], ["Um Tio Quase Perfeito 3", "comédia", "#3B6D11"]]
        .map(([t, g, c], i) => ({ titulo: t, genero: g, poster: poster(c, t.split(" ")[0]), estreia_hoje: i === 2 })),
      pre_venda: [{ titulo: "Vingadores: Doutor Destino", estreia: "16 de dezembro" }] }],
    [7700, { tipo: "resposta", texto: "Em São Paulo há 74 filmes em cartaz." }],
    [13000, { tipo: "ignorado", texto: "vem cá mãe, olha isso", motivo: "diga “Beatriz” antes do pedido" }],
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
  };
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
