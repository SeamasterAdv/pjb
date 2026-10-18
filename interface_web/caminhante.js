/* A mascote que anda pela tela.
 *
 * Quem decide para onde ela vai é o Python (mascote_tela.py): ele move a janela e chama
 * definirPose(modo, direcao). Aqui ficam a animação, o balão de fala e as sugestões, com os
 * eventos da voz e do painel buscados a cada 300 ms.
 */
"use strict";

const CARACTERES_POR_SEGUNDO = 14;
let api = null;
let estado = "repouso";
let pose = { modo: "parada", direcao: -1 };
let voltarAoRepouso = null;
let esconderBalao = null;
let comandoSugerido = "";

function aplicarClasses() {
  const classes = ["mascote", `estado-${estado}`];
  if (pose.modo === "andando" && estado === "repouso") classes.push("andando");
  if (pose.modo === "dormindo") classes.push("dormindo");
  if (pose.direcao < 0) classes.push("virada");
  const caixa = document.getElementById("mascote");
  caixa.className = classes.join(" ") + (caixa.querySelector(".tela-3d") ? " em-3d" : "");
  // A mascote 3D (mascote3d_tela.js), quando carregou, troca a animação junto.
  if (window.mascote3d) window.mascote3d.definir(estado, pose);
}

// Chamado pelo Python quando ela começa ou para de andar, vira de lado ou cochila.
window.definirPose = (modo, direcao) => { pose = { modo, direcao }; aplicarClasses(); };

function definirEstado(novo, duracaoMs) {
  estado = novo;
  aplicarClasses();
  clearTimeout(voltarAoRepouso);
  if (duracaoMs) voltarAoRepouso = setTimeout(() => definirEstado("repouso"), duracaoMs);
}

function balao(texto, opcoes = {}) {
  const caixa = document.getElementById("balao");
  document.getElementById("balao-texto").textContent = texto.length > 170 ? texto.slice(0, 167).trimEnd() + "…" : texto;
  comandoSugerido = opcoes.comando || "";
  document.getElementById("balao-botoes").hidden = !comandoSugerido;
  caixa.classList.add("visivel");
  clearTimeout(esconderBalao);
  esconderBalao = setTimeout(() => caixa.classList.remove("visivel"), opcoes.duracao || 7000);
}
function fecharBalao() { clearTimeout(esconderBalao); document.getElementById("balao").classList.remove("visivel"); }

function tratar(ev) {
  const texto = ev.texto || "";
  switch (ev.tipo) {
    case "ouvido":
      definirEstado(ev.pensando ? "pensando" : "ouvindo", ev.pensando ? 0 : 2500); break;
    case "voce":
    case "pensando":
      definirEstado("pensando"); break;
    case "resposta": {
      const duracao = Math.min(40000, Math.max(2500, (texto.length / CARACTERES_POR_SEGUNDO) * 1000 + 1500));
      if (!texto.startsWith("Resumo:")) balao(texto, { duracao });
      definirEstado("falando", duracao); break;
    }
    case "resumo":
      balao(`Pronto! O resumo sobre ${ev.tema} está no painel.`, { duracao: 8000 });
      definirEstado("vitoria", 1800); break;
    case "sugestao":
      balao(texto, { comando: ev.comando, duracao: 20000 }); definirEstado("ouvindo", 2500); break;
  }
}

async function comecar(ponte) {
  api = ponte;
  document.getElementById("mascote").onclick = () => api.abrir_painel();
  document.getElementById("btn-sim").onclick = () => { api.aceitar_sugestao(comandoSugerido); fecharBalao(); };
  document.getElementById("btn-nao").onclick = fecharBalao;
  const buscar = async () => {
    try { for (const ev of await api.eventos()) tratar(ev); } catch (erro) { /* a ponte se recupera sozinha */ }
    setTimeout(buscar, 300);
  };
  buscar();
}

desenharMascote(document.getElementById("mascote"));
aplicarClasses();
if (window.pywebview && window.pywebview.api) comecar(window.pywebview.api);
else addEventListener("pywebviewready", () => comecar(window.pywebview.api));
