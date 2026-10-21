/* Ponte entre o painel (app.js, script comum) e o visual em rede (visual_rede.js, módulo). O login (login.js)
 * usa a mesma ponte: lá a esfera tem a câmera dentro e a montanha mede o microfone do próprio processo do login.
 *
 * Cria a esfera (voz da Beatriz) e a montanha (sua voz) e segue o estado pelo data-estado do <body>,
 * que o app.js troca. Os níveis de voz chegam por window.visualBeatriz.vozDela / vozMinha (o app.js
 * pergunta ao Python, que mede o áudio dela e o microfone: niveis_voz.py) e marcam data-niveis.
 * Sem eles (modo demonstração, aberto no navegador), as falas são simuladas pelo estado.
 */
import { EsferaEmRede, MontanhaDePontos, BANDAS_MONTANHA, falaSimulada, tresBandas } from "./visual_rede.js";

const $ = (seletor) => document.querySelector(seletor);
const SEM_SINAL_MS = 600; // sem nível real há esse tempo: volta para a simulação

async function lerAjustes() {
  try {
    const resposta = await fetch("visual_ajustes.json", { cache: "no-store" });
    return resposta.ok ? await resposta.json() : {};
  } catch (erro) { return {}; }
}

const ajustes = await lerAjustes();
const esfera = new EsferaEmRede($("#esfera"), ajustes.esfera);
const montanha = new MontanhaDePontos($("#montanha"), ajustes.montanha);

let estado = "repouso";
const real = { dela: null, minha: null, quandoDela: 0, quandoMinha: 0 };
let simDela = null, simMinha = null;

function aoMudarEstado() {
  estado = document.body.dataset.estado || "repouso";
  esfera.definirEstado(estado);
  montanha.definirEstado(estado);
  const dourado = "vitoria" in document.body.dataset;
  if (dourado && !esfera.dourado) { // a esfera se abre um instante quando a missão é cumprida
    esfera.definirEstado("vitoria");
    setTimeout(() => esfera.definirEstado(document.body.dataset.estado || "repouso"), 1400);
  }
  esfera.definirDourado(dourado);
  montanha.definirDourado(dourado);
  if (estado !== "falando") simDela = null;
  if (estado !== "ouvindo") simMinha = null;
}
new MutationObserver(aoMudarEstado).observe(document.body, { attributes: true, attributeFilter: ["data-estado", "data-vitoria"] });
aoMudarEstado();

window.visualBeatriz = {
  esfera, montanha, // para ajustar pelo console
  // nivel de 0 a 1; bandas = BANDAS_MONTANHA valores do grave ao agudo (a esfera usa graves, médios e agudos).
  vozDela(nivel, bandas) { real.dela = { nivel, tres: tresBandas(bandas) }; real.quandoDela = performance.now(); },
  // nivel de 0 a 1; bandas = BANDAS_MONTANHA valores do grave ao agudo; db para o mostrador.
  vozMinha(nivel, bandas, db) { real.minha = { nivel, bandas, db }; real.quandoMinha = performance.now(); },
};

const simulando = () => !document.body.dataset.niveis;

function medidaDela(agora) {
  if (real.dela && agora - real.quandoDela < SEM_SINAL_MS) return real.dela;
  if (!simulando() || estado !== "falando") return null;
  simDela = simDela || falaSimulada(30, 2);
  const medida = simDela.medir();
  if (!medida) simDela = null; // a fala de mentira acabou: começa outra no próximo quadro
  return medida;
}

function medidaMinha(agora) {
  if (real.minha && agora - real.quandoMinha < SEM_SINAL_MS) return real.minha;
  if (!simulando() || !document.body.dataset.demonstracao || estado !== "ouvindo") return null; // fora da demonstração, nunca inventa a sua voz
  simMinha = simMinha || falaSimulada(30, 1);
  const medida = simMinha.medir();
  if (!medida) simMinha = null;
  return medida;
}

/* ------------------------------------------------------------------ mostradores */
const BLOCOS = "▁▂▃▄▅▆▇█";
const espectro = (bandas) => Array.from(bandas, (v) => BLOCOS[Math.min(7, Math.floor(Math.max(0, v) * 8))]).join("");
const barrinha = (valor, largura = 12) => {
  const cheio = Math.round(Math.max(0, Math.min(1, valor)) * largura);
  return "█".repeat(cheio) + "·".repeat(largura - cheio);
};
let ultimaDela = null, ultimaMinha = null;
function atualizarMostradores() {
  const dela = ultimaDela ? ultimaDela.nivel : 0, minha = ultimaMinha ? ultimaMinha.nivel : 0;
  const db = ultimaMinha && Number.isFinite(ultimaMinha.db) && minha > 0 ? ultimaMinha.db : null;
  $("#montanha-db").textContent = db === null ? "-∞" : db.toFixed(1);
  $("#montanha-espectro").textContent = espectro(ultimaMinha ? ultimaMinha.bandas : new Array(BANDAS_MONTANHA).fill(0));
  const voz = $("#tl-voz-dela"), sua = $("#tl-voz-minha");
  if (voz) voz.textContent = `${barrinha(dela)} ${dela.toFixed(2)}`;
  if (sua) sua.textContent = `${barrinha(minha)} ${minha.toFixed(2)}`;
}
setInterval(atualizarMostradores, 100);

function quadro(agora) {
  ultimaDela = medidaDela(agora);
  esfera.voz(ultimaDela ? ultimaDela.nivel : 0, ultimaDela ? ultimaDela.tres : [0, 0, 0]);
  ultimaMinha = medidaMinha(agora);
  montanha.voz(ultimaMinha ? ultimaMinha.nivel : 0, ultimaMinha ? ultimaMinha.bandas : null);
  requestAnimationFrame(quadro);
}
requestAnimationFrame(quadro);
