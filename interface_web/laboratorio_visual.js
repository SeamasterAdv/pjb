/* Laboratório do visual novo da Beatriz.
 *
 * A esfera recebe a voz dela (uma amostra de verdade de documentos_gerados/amostras_voz ou uma fala
 * simulada) e a montanha recebe a sua (o microfone do navegador ou uma fala simulada). As duas
 * medições saem do mesmo lugar (medidor de nível e bandas), no formato que o painel vai receber do
 * Python. Os ajustes ficam no localStorage; "copiar ajustes" dá o JSON.
 */
import { EsferaEmRede, MontanhaDePontos, AJUSTES_ESFERA, AJUSTES_MONTANHA, BANDAS_MONTANHA, falaSimulada, medidorDeAudio } from "./visual_rede.js";

const $ = (seletor) => document.querySelector(seletor);
const CHAVE_AJUSTES = "beatriz.visual.ajustes";

function lerAjustes() {
  try { return JSON.parse(localStorage.getItem(CHAVE_AJUSTES)) || {}; } catch (erro) { return {}; }
}
const salvos = lerAjustes();
const esfera = new EsferaEmRede($("#esfera"), salvos.esfera);
const montanha = new MontanhaDePontos($("#montanha"), salvos.montanha);

const PALETAS = {
  branco: { nome: "branco (fotos)", esfera: "#ffffff", montanha: "#ffffff" },
  gelo: { nome: "gelo azulado", esfera: "#cfeaff", montanha: "#e9f5ff" },
  ciano: { nome: "ciano", esfera: "#7fe9ff", montanha: "#d4f7ff" },
  roxo_dourado: { nome: "roxo e dourado", esfera: "#c7aef0", montanha: "#ffd25e" },
};
let paleta = PALETAS[salvos.paleta] ? salvos.paleta : "branco";

function gravar() {
  try {
    localStorage.setItem(CHAVE_AJUSTES, JSON.stringify({ esfera: esfera.ajustes, montanha: montanha.ajustes, paleta }));
  } catch (erro) { /* sem armazenamento: vale só nesta aba */ }
}

function aviso(texto) {
  $("#aviso").textContent = texto;
  clearTimeout(aviso._sumir);
  aviso._sumir = setTimeout(() => { $("#aviso").textContent = ""; }, 6000);
}

/* ------------------------------------------------------------------ estados */
let estado = "repouso";
function definirEstado(novo) {
  estado = novo;
  esfera.definirEstado(novo);
  montanha.definirEstado(novo);
  $("#rotulo-estado").textContent = novo === "vitoria" ? "vitória" : novo;
  for (const botao of document.querySelectorAll("[data-estado]")) botao.classList.toggle("ativo", botao.dataset.estado === novo);
}
for (const botao of document.querySelectorAll("[data-estado]")) {
  botao.addEventListener("click", () => definirEstado(botao.dataset.estado));
}

/* ------------------------------------------------------------------ medição do som */
let contexto = null;
function audio() {
  contexto = contexto || new AudioContext();
  if (contexto.state === "suspended") contexto.resume();
  return contexto;
}

const criarMedidor = (fonte) => medidorDeAudio(audio(), fonte);

/* ------------------------------------------------------------------ fontes de som */
let somDela = null, aoTerminarDela = null, elementoAudio = null;
let somMeu = null, aoTerminarMeu = null, microfone = null;

function pararFalaDela() {
  if (elementoAudio) { elementoAudio.pause(); elementoAudio = null; }
  somDela = null;
  aoTerminarDela = null;
}

function simularFalaDela(aoTerminar) {
  pararFalaDela();
  somDela = falaSimulada(4 + Math.random() * 3, 2);
  aoTerminarDela = aoTerminar || (() => definirEstado("repouso"));
  definirEstado("falando");
}

function tocarAmostra() {
  pararFalaDela();
  const c = audio();
  const elemento = new Audio(`../documentos_gerados/amostras_voz/${$("#amostra").value}`);
  elementoAudio = elemento;
  const fonte = c.createMediaElementSource(elemento);
  fonte.connect(c.destination); // a amostra toca e o medidor só escuta
  const medidor = criarMedidor(fonte);
  elemento.addEventListener("ended", () => {
    if (elementoAudio !== elemento) return;
    elementoAudio = null; somDela = null; definirEstado("repouso");
  });
  elemento.addEventListener("error", () => {
    if (elementoAudio !== elemento) return;
    aviso("Não achei a amostra (abra pelo servidor na raiz do projeto); usei a fala simulada.");
    simularFalaDela();
  });
  elemento.play().then(() => { somDela = medidor; definirEstado("falando"); }).catch(() => {});
}

function simularMinhaVoz(aoTerminar) {
  somMeu = falaSimulada(3 + Math.random() * 2, 1);
  aoTerminarMeu = aoTerminar || (() => definirEstado("repouso"));
  definirEstado("ouvindo");
}

async function usarMicrofone() {
  if (microfone) {
    microfone.fluxo.getTracks().forEach((faixa) => faixa.stop());
    microfone = null;
    $("#btn-microfone").classList.remove("ativo");
    $("#btn-microfone").textContent = "usar meu microfone";
    return;
  }
  try {
    const fluxo = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    microfone = { fluxo, medidor: criarMedidor(audio().createMediaStreamSource(fluxo)) };
    $("#btn-microfone").classList.add("ativo");
    $("#btn-microfone").textContent = "microfone ligado";
    aviso("Fale qualquer coisa e veja a montanha subir.");
  } catch (erro) {
    aviso(`Sem acesso ao microfone aqui (${erro.name}). Use “simular minha voz”.`);
  }
}

// Uma volta inteira: você fala (montanha), ela pensa, ela responde (esfera), volta ao repouso.
function conversaCompleta() {
  simularMinhaVoz(() => {
    definirEstado("pensando");
    setTimeout(() => simularFalaDela(() => {
      definirEstado("vitoria");
      setTimeout(() => definirEstado("repouso"), 1600);
    }), 1600);
  });
}

$("#btn-amostra").addEventListener("click", tocarAmostra);
$("#btn-fala-dela").addEventListener("click", () => simularFalaDela());
$("#btn-minha-voz").addEventListener("click", () => simularMinhaVoz());
$("#btn-microfone").addEventListener("click", usarMicrofone);
$("#btn-ciclo").addEventListener("click", conversaCompleta);

/* ------------------------------------------------------------------ controles */
const CONTROLES = [
  ["Esfera · voz dela", esfera, [
    ["poeira", "poeira", 1000, 15000, 500],
    ["nos", "nós da rede", 80, 800, 10],
    ["agrupamento", "agrupamento", 0, 0.9, 0.05],
    ["distancia", "distância máx.", 0.12, 0.5, 0.01],
    ["alfaLinhas", "alfa das linhas", 0, 1.5, 0.05],
    ["tamanhoPoeira", "tamanho poeira", 0.5, 4, 0.1],
    ["tamanhoNos", "tamanho nós", 0.5, 6, 0.1],
    ["brilho", "brilho", 0.2, 2, 0.05],
    ["reacao", "reação à voz", 0, 3, 0.05],
    ["escalaOndas", "escala ondas", 0.3, 3, 0.05],
    ["giro", "giro", 0, 4, 0.1],
    ["espessura", "espessura", 0, 0.4, 0.01],
  ]],
  ["Montanha · sua voz", montanha, [
    ["pontos", "pontos", 8000, 80000, 2000],
    ["aspereza", "aspereza", 0, 1, 0.05],
    ["altura", "altura", 0.3, 2.5, 0.05],
    ["base", "em silêncio", 0, 0.6, 0.01],
    ["ganho", "reação à voz", 0, 3, 0.05],
    ["tamanho", "tamanho ponto", 0.5, 4, 0.1],
    ["brilho", "brilho", 0.2, 2, 0.05],
    ["tremor", "tremor", 0, 3, 0.1],
  ]],
];
// Estes refazem a geometria: só valem ao soltar o controle.
const AO_SOLTAR = ["poeira", "nos", "agrupamento", "distancia", "pontos", "aspereza"];

function deslizante(objeto, chave, rotulo, minimo, maximo, passo) {
  const el = document.createElement("label");
  el.className = "deslizante";
  el.innerHTML = `<span class="barra"><span class="cheio"></span><span class="valor"></span>` +
                 `<input type="range" min="${minimo}" max="${maximo}" step="${passo}"></span><span class="nome"></span>`;
  el.querySelector(".nome").textContent = rotulo;
  const entrada = el.querySelector("input");
  entrada.setAttribute("aria-label", rotulo);
  const mostrar = () => {
    const valor = Number(entrada.value);
    el.querySelector(".cheio").style.width = `${((valor - minimo) / (maximo - minimo)) * 100}%`;
    el.querySelector(".valor").textContent = passo >= 1 ? String(valor) : valor.toFixed(2);
  };
  entrada.addEventListener("input", () => {
    mostrar();
    if (!AO_SOLTAR.includes(chave)) { objeto.aplicarAjustes({ [chave]: Number(entrada.value) }); gravar(); }
  });
  entrada.addEventListener("change", () => { objeto.aplicarAjustes({ [chave]: Number(entrada.value) }); gravar(); });
  el.sincronizar = () => { entrada.value = objeto.ajustes[chave]; mostrar(); };
  el.sincronizar();
  return el;
}

function secao(titulo, conteudo) {
  const el = document.createElement("section");
  el.className = "secao";
  const cabecalho = document.createElement("button");
  cabecalho.className = "secao-cab";
  cabecalho.textContent = titulo;
  cabecalho.addEventListener("click", () => el.classList.toggle("fechada"));
  const corpo = document.createElement("div");
  corpo.className = "secao-corpo";
  corpo.append(...conteudo);
  el.append(cabecalho, corpo);
  return el;
}

const deslizantes = [];
for (const [titulo, objeto, itens] of CONTROLES) {
  const elementos = itens.map(([chave, rotulo, minimo, maximo, passo]) => deslizante(objeto, chave, rotulo, minimo, maximo, passo));
  deslizantes.push(...elementos);
  $("#controles").append(secao(titulo, elementos));
}

const escolhaPaleta = document.createElement("select");
escolhaPaleta.setAttribute("aria-label", "Cores");
for (const [chave, { nome }] of Object.entries(PALETAS)) escolhaPaleta.add(new Option(nome, chave, false, chave === paleta));
function aplicarPaleta() {
  esfera.definirCor(PALETAS[paleta].esfera);
  montanha.definirCor(PALETAS[paleta].montanha);
}
escolhaPaleta.addEventListener("change", () => { paleta = escolhaPaleta.value; aplicarPaleta(); gravar(); });

const botaoCopiar = document.createElement("button");
botaoCopiar.textContent = "copiar ajustes";
botaoCopiar.addEventListener("click", async () => {
  const texto = JSON.stringify({ esfera: esfera.ajustes, montanha: montanha.ajustes, paleta }, null, 2);
  try { await navigator.clipboard.writeText(texto); aviso("Ajustes copiados."); }
  catch (erro) { console.log(texto); aviso("Não deu para copiar; o JSON está no console."); }
});
const botaoPadrao = document.createElement("button");
botaoPadrao.textContent = "voltar ao padrão";
botaoPadrao.addEventListener("click", () => {
  esfera.aplicarAjustes({ ...AJUSTES_ESFERA });
  montanha.aplicarAjustes({ ...AJUSTES_MONTANHA });
  paleta = "branco";
  escolhaPaleta.value = paleta;
  aplicarPaleta();
  deslizantes.forEach((d) => d.sincronizar());
  gravar();
});
const linhaCores = document.createElement("div");
linhaCores.className = "linha-botoes";
linhaCores.append(escolhaPaleta, botaoCopiar, botaoPadrao);
$("#controles").append(secao("Cores e ajustes", [linhaCores]));
aplicarPaleta();

/* ------------------------------------------------------------------ telemetria */
const BLOCOS = "▁▂▃▄▅▆▇█";
const barrinha = (valor, largura = 10) => {
  const cheio = Math.round(Math.max(0, Math.min(1, valor)) * largura);
  return "█".repeat(cheio) + "·".repeat(largura - cheio);
};
const espectro = (bandas) => bandas.map((v) => BLOCOS[Math.min(7, Math.floor(Math.max(0, v) * 8))]).join("");
const hex = () => Math.floor(Math.random() * 0xffffff).toString(16).toUpperCase().padStart(6, "0");

let medidaDela = null, medidaMinha = null, quadros = 0, fps = 0, ultimaContagem = performance.now();
function atualizarTelemetria() {
  const d = medidaDela || { nivel: 0, tres: [0, 0, 0] }, m = medidaMinha || { nivel: 0, bandas: new Array(BANDAS_MONTANHA).fill(0), db: -Infinity };
  const linhas = [
    ["voz.beatriz", d.nivel.toFixed(3)], ["", barrinha(d.nivel), "barrinha"],
    ["graves", d.tres[0].toFixed(2), "sub"], ["médios", d.tres[1].toFixed(2), "sub"], ["agudos", d.tres[2].toFixed(2), "sub"],
    ["voz.você", m.nivel.toFixed(3)], ["", espectro(m.bandas), "barrinha"],
    ["estado", estado === "vitoria" ? "vitória" : estado],
    ["poeira", String(esfera.ajustes.poeira)], ["nós / linhas", `${esfera.ajustes.nos} / ${esfera.pares.length / 2}`],
    ["montanha", String(montanha.ajustes.pontos)], ["quadros", `${fps} fps`],
  ];
  $("#leituras").innerHTML = "";
  for (const [rotulo, valor, classe] of linhas) {
    const tr = $("#leituras").insertRow();
    if (classe === "sub") tr.className = "sub";
    tr.insertCell().textContent = rotulo;
    const celula = tr.insertCell();
    celula.textContent = valor;
    if (classe === "barrinha") celula.className = "barrinha";
  }
  $("#db-voce").textContent = Number.isFinite(m.db) && m.nivel > 0 ? m.db.toFixed(1) : "-∞";
}
setInterval(atualizarTelemetria, 100);
setInterval(() => { $("#hex").textContent = ""; $("#hex").append(...Array.from({ length: 54 }, () => Object.assign(document.createElement("span"), { textContent: hex() }))); }, 900);
setInterval(() => {
  const agora = new Date();
  $("#relogio").textContent = `${agora.toLocaleDateString("pt-BR")} · ${agora.toLocaleTimeString("pt-BR")}`;
}, 1000);

/* ------------------------------------------------------------------ laço */
function quadro(agora) {
  medidaDela = somDela ? somDela.medir() : null;
  if (somDela && !medidaDela) { // a fala simulada acabou
    const fim = aoTerminarDela;
    somDela = null; aoTerminarDela = null;
    if (fim) fim();
  }
  esfera.voz(medidaDela ? medidaDela.nivel : 0, medidaDela ? medidaDela.tres : [0, 0, 0]);

  medidaMinha = microfone ? microfone.medidor.medir() : somMeu ? somMeu.medir() : null;
  if (!microfone && somMeu && !medidaMinha) {
    const fim = aoTerminarMeu;
    somMeu = null; aoTerminarMeu = null;
    if (fim) fim();
  }
  montanha.voz(medidaMinha ? medidaMinha.nivel : 0, medidaMinha ? medidaMinha.bandas : null);

  quadros++;
  if (agora - ultimaContagem > 1000) { fps = quadros; quadros = 0; ultimaContagem = agora; }
  requestAnimationFrame(quadro);
}
requestAnimationFrame(quadro);
