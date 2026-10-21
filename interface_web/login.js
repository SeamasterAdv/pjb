/* Tela de login e de cadastro (a ponte é o ApiLogin do login_web.py).
 *
 * Busca o estado a cada 150 ms, a imagem da câmera a cada 70 ms e o nível do microfone a cada 50 ms. O Python decide
 * tudo (que página, que campos, que mensagem); aqui só se desenha e se chama. A esfera e a montanha são do
 * painel_visual.js, que acompanha o data-estado e o data-vitoria do <body>. Entrou: tudo fica dourado e a janela fecha.
 * Aberta num navegador comum, entra no modo demonstração (sem câmera de verdade), para dar para ver o visual.
 */
"use strict";

const $ = (seletor) => document.querySelector(seletor);
const NOMES_DOS_MODOS = { "rosto_ou_senha": "rosto ou senha", "rosto+senha": "rosto e senha", "senha": "só a senha" };
const TEMPO_DO_DOURADO = 1900; // ms dourado antes de fechar a janela

let api = null;
let atual = null; // o último estado vindo do Python
let ultimoQuadro = -1;
let fechando = false;
let paginaAnterior = null;
let mensagemLocal = null; // {texto, tom, ate}: um aviso daqui mesmo (ex.: "código copiado") por alguns segundos

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

/* ------------------------------------------------------------------ o que cada página mostra */
function passosDaPagina(t) {
  if (["credenciais", "rosto", "codigo"].includes(t.pagina)) {
    const atualCadastro = { credenciais: 0, rosto: 1, codigo: 2 }[t.pagina];
    return { titulo: "cadastro", atual: t.terminou ? 3 : atualCadastro,
             passos: ["Escolha o usuário e a senha", "Cadastre o rosto: de frente, vire para a esquerda e um teste", "Guarde o código de recuperação"] };
  }
  if (!t.mostra_video && !t.link_codigo && !t.link_de_novo) {
    return { titulo: "como entrar", atual: t.terminou ? 2 : 0, passos: ["Digite o usuário e a senha", "Entre"] };
  }
  const passos = ["Olhe de frente para a câmera", "Vire a cabeça para o lado pedido"];
  passos.push(t.modo === "rosto+senha" ? "Digite a senha" : "Pronto: entra sem senha");
  const indice = t.terminou ? 3 : t.pede_senha && t.modo === "rosto+senha" ? 2 : t.fase === "virar" ? 1 : 0;
  return { titulo: "como entrar", atual: indice, passos };
}

function estadoVisual(t) {
  if (t.terminou && t.sucesso) return ["vitoria", t.pagina === "entrar" ? "acesso liberado" : "cadastro concluído"];
  if (t.bloqueio) return ["repouso", "bloqueado"];
  if (t.pagina === "credenciais") return ["repouso", "cadastro · 1 de 3"];
  if (t.pagina === "codigo") return ["repouso", "cadastro · 3 de 3"];
  if (t.camera === "preparando") return ["pensando", "preparando a câmera"];
  if (t.pagina === "rosto") {
    const fase = { frente: "olhe de frente", esquerda: "vire para a esquerda", teste: "teste final", pronto: "rosto cadastrado" }[t.fase];
    return [t.fase === "frente" ? "ouvindo" : "pensando", `cadastro · 2 de 3 · ${fase || "rosto"}`];
  }
  if (t.fase === "procurar" && t.mostra_video) return ["ouvindo", "procurando o seu rosto"];
  if (t.fase === "virar") return ["pensando", "prova de vida"];
  if (t.fase === "falhou") return ["repouso", "não reconhecido"];
  if (t.pede_senha) return ["repouso", "aguardando a senha"];
  return ["repouso", "em espera"];
}

function barraDeTexto(valor, largura) {
  const cheio = Math.round(Math.max(0, Math.min(1, valor)) * largura);
  return "█".repeat(cheio) + "·".repeat(largura - cheio);
}

function mostrar(elemento, visivel) { elemento.hidden = !visivel; }

function desenhar(t) {
  atual = t;
  const pagina = t.pagina;
  const [estado, rotulo] = estadoVisual(t);
  if (estado === "vitoria") document.body.dataset.vitoria = "1";
  else document.body.dataset.estado = estado;
  $("#rotulo").textContent = rotulo;
  $("#sub").textContent = ["credenciais", "rosto", "codigo"].includes(pagina) ? "cadastro" : "autenticação";

  // identificação
  $("#id-usuario").textContent = t.usuario || "--";
  $("#id-modo").textContent = NOMES_DOS_MODOS[t.modo] || t.modo || "--";
  $("#id-etapa").textContent = rotulo;
  $("#id-erros").textContent = pagina === "entrar" ? `${t.falhas} de ${t.tentativas}` : "--";
  const { titulo, atual: indice, passos } = passosDaPagina(t);
  $("#titulo-passos").textContent = titulo;
  $("#passos").replaceChildren(...passos.map((texto, i) => {
    const li = document.createElement("li");
    li.textContent = texto;
    li.className = i < indice ? "feito" : i === indice ? "atual" : "";
    return li;
  }));

  // câmera
  const camera = t.camera;
  $("#ponto-camera").className = "ponto " + (camera === "ok" ? "verde" : camera === "preparando" ? "azul" : camera === "erro" ? "vermelho" : "");
  $("#chip-camera").textContent = camera === "ok" ? "câmera ativa" : camera === "preparando" ? "câmera abrindo" : camera === "erro" ? "câmera falhou" : "câmera desligada";
  mostrar($("#visor"), !!t.mostra_video);
  posicionarCaixa(t.caixa);

  // progresso e mensagem
  const progresso = t.progresso || 0;
  $("#barra").textContent = barraDeTexto(progresso, 24);
  $("#porcento").textContent = `${Math.round(progresso * 100)}%`;
  const local = mensagemLocal && mensagemLocal.ate > Date.now() ? mensagemLocal : null;
  $("#mensagem").textContent = local ? local.texto : t.mensagem || "";
  $("#mensagem").className = "mensagem " + ((local ? local.tom : t.tom) || "");

  // formulários
  const bloqueado = t.bloqueio > 0;
  $("#titulo-acesso").textContent = pagina === "credenciais" ? "usuário e senha" : pagina === "codigo" ? "código de recuperação"
    : pagina === "rosto" ? "rosto" : "acesso";
  mostrar($("#form-senha"), pagina === "entrar" && !!t.pede_senha && !t.terminou);
  mostrar($("#form-codigo"), pagina === "entrar" && !!t.pede_codigo && !t.terminou);
  mostrar($("#form-credenciais"), pagina === "credenciais");
  mostrar($("#area-codigo-novo"), pagina === "codigo" && !t.terminou);
  for (const campo of document.querySelectorAll("#form-senha input, #form-senha button, #form-codigo input, #form-codigo button")) {
    campo.disabled = bloqueado;
  }
  if (t.codigo_novo) $("#codigo-novo").textContent = t.codigo_novo;
  mostrar($("#link-senha"), pagina === "entrar" && !!t.link_senha && !t.terminou);
  mostrar($("#link-codigo"), pagina === "entrar" && !!t.link_codigo && !t.terminou);
  mostrar($("#link-de-novo"), pagina === "entrar" && !!t.link_de_novo && !t.terminou);
  mostrar($("#link-sem-rosto"), pagina === "rosto" && !t.terminou);
  $("#dica").textContent = pagina === "entrar" && t.mostra_video && !t.pede_senha && !t.terminou
    ? "Olhe para a câmera: quando eu reconhecer o seu rosto, peço para você virar a cabeça para um lado (assim uma foto não passa)."
    : pagina === "rosto" ? "Fique de frente, com o rosto dentro do contorno. Depois vire a cabeça para a sua esquerda e faça o teste." : "";
  if (pagina !== paginaAnterior) aoTrocarDePagina(t);
  paginaAnterior = pagina;

  if (t.terminou && !fechando) {
    fechando = true;
    setTimeout(() => api.fechar(), t.sucesso ? TEMPO_DO_DOURADO : 0);
  }
}

function aoTrocarDePagina(t) {
  if (t.pagina === "entrar" && t.usuario && !$("#usuario").value) $("#usuario").value = t.usuario;
  if (t.pagina === "credenciais") $("#novo-usuario").focus();
}

// O mostrador do formulário de senha aparece depois de a página abrir: foca o campo certo quando ele surge.
new MutationObserver(() => {
  if (!$("#form-senha").hidden && !document.activeElement.closest("form")) ($("#usuario").value ? $("#senha") : $("#usuario")).focus();
  if (!$("#form-codigo").hidden && document.activeElement === document.body) $("#codigo").focus();
}).observe(document.querySelector(".acesso"), { attributes: true, subtree: true, attributeFilter: ["hidden"] });

// A imagem (4:3) cobre a lente redonda (1:1): os lados ficam cortados, e a caixa do rosto acompanha esse corte.
function posicionarCaixa(caixa) {
  const elemento = $("#caixa"), imagem = $("#camera"), lente = $(".lente");
  if (!caixa || !imagem.naturalWidth) { elemento.hidden = true; return; }
  const lado = lente.clientWidth, proporcao = imagem.naturalWidth / imagem.naturalHeight;
  const largura = lado * Math.max(1, proporcao), altura = lado * Math.max(1, 1 / proporcao);
  const sobraX = (largura - lado) / 2, sobraY = (altura - lado) / 2;
  const [x, y, w, h] = caixa;
  Object.assign(elemento.style, { left: `${x * largura - sobraX}px`, top: `${y * altura - sobraY}px`,
                                  width: `${w * largura}px`, height: `${h * altura}px` });
  elemento.hidden = false;
}

// O visor tem o tamanho certo para caber dentro da esfera (que ocupa ~76% do lado menor do palco).
new ResizeObserver(([entrada]) => {
  const { width, height } = entrada.contentRect;
  $("#palco-esfera").style.setProperty("--lado", `${Math.min(width, height)}px`);
}).observe($("#palco-esfera"));

/* ------------------------------------------------------------------ ações */
async function chamar(promessa) {
  try { const t = await promessa; if (t) desenhar(t); } catch (erro) { /* a ponte se recupera; o próximo estado conserta */ }
}
function avisoLocal(texto, tom = "ok") { mensagemLocal = { texto, tom, ate: Date.now() + 4000 }; if (atual) desenhar(atual); }

function ligarControles() {
  $("#form-senha").addEventListener("submit", (e) => {
    e.preventDefault();
    const senha = $("#senha").value;
    $("#senha").value = "";
    chamar(api.enviar_senha($("#usuario").value, senha));
  });
  $("#form-codigo").addEventListener("submit", (e) => {
    e.preventDefault();
    const codigo = $("#codigo").value;
    $("#codigo").value = "";
    chamar(api.enviar_codigo(codigo));
  });
  $("#form-credenciais").addEventListener("submit", (e) => {
    e.preventDefault();
    chamar(api.confirmar_credenciais($("#novo-usuario").value, $("#nova-senha").value, $("#nova-senha-2").value));
  });
  $("#btn-copiar").onclick = async () => {
    try { await navigator.clipboard.writeText($("#codigo-novo").textContent); avisoLocal("Código copiado. Cole-o num gerenciador de senhas ou anote no papel."); }
    catch (erro) { avisoLocal("Não consegui copiar; anote o código à mão.", "erro"); }
  };
  $("#guardei").onchange = () => { $("#btn-concluir").disabled = !$("#guardei").checked; };
  $("#btn-concluir").onclick = () => chamar(api.concluir($("#guardei").checked));
  $("#link-senha").onclick = () => chamar(api.mostrar_senha());
  $("#link-codigo").onclick = () => chamar(api.abrir_codigo());
  $("#link-de-novo").onclick = () => chamar(api.tentar_rosto_de_novo());
  $("#link-sem-rosto").onclick = () => chamar(api.sem_rosto());
  $("#link-sair").onclick = () => api.fechar();
  addEventListener("keydown", (e) => { if (e.key === "Escape") api.fechar(); });
  $("#camera").addEventListener("load", () => atual && posicionarCaixa(atual.caixa));
}

/* ------------------------------------------------------------------ laços */
async function buscarEstado() {
  try { desenhar(await api.estado()); } catch (erro) { /* idem */ }
  setTimeout(buscarEstado, 150);
}
async function buscarQuadro() {
  try {
    if (atual && atual.mostra_video) {
      const quadro = await api.quadro(ultimoQuadro);
      if (quadro.imagem) $("#camera").src = quadro.imagem;
      ultimoQuadro = quadro.n;
    }
  } catch (erro) { /* idem */ }
  setTimeout(buscarQuadro, 70);
}
async function buscarNiveis() {
  try {
    const niveis = await api.niveis(), visual = window.visualBeatriz;
    $("#ponto-microfone").className = "ponto " + (niveis.voce ? "verde" : "");
    if (visual && niveis.voce) visual.vozMinha(niveis.voce.nivel, niveis.voce.bandas, niveis.voce.db);
  } catch (erro) { /* idem */ }
  setTimeout(buscarNiveis, 50);
}

async function comecar(ponte, demonstracao) {
  api = ponte;
  if (demonstracao) document.body.dataset.demonstracao = "1"; // a montanha simula a sua voz ao "ouvir"
  else document.body.dataset.niveis = "1"; // o microfone de verdade manda na montanha
  ligarControles();
  desenhar(await api.inicio());
  buscarEstado(); buscarQuadro(); buscarNiveis();
}

iniciarRelogio();
if (window.pywebview && window.pywebview.api && window.pywebview.api.inicio) comecar(window.pywebview.api, false);
else {
  let pronto = false;
  addEventListener("pywebviewready", () => { pronto = true; comecar(window.pywebview.api, false); });
  setTimeout(() => { if (!pronto) comecar(criarApiDemonstracao(), true); }, 1500);
}

/* ------------------------------------------------------------------ modo demonstração */
// ?tela=cadastro mostra o cadastro; ?tela=senha, a entrada só com a senha. Senão, a entrada pelo rosto (~8 s).
function criarApiDemonstracao() {
  const tela = new URLSearchParams(location.search).get("tela") || "rosto";
  const t = { pagina: tela === "cadastro" ? "credenciais" : "entrar", usuario: "felipe", modo: "rosto_ou_senha", falhas: 0, tentativas: 5,
              mostra_video: tela === "rosto", pede_senha: tela === "senha", pede_codigo: false, link_senha: tela === "rosto",
              link_codigo: tela === "rosto", link_de_novo: false, progresso: 0, fase: "", caixa: null, camera: "",
              mensagem: "", tom: "normal", bloqueio: 0, sucesso: false, terminou: false, codigo_novo: "" };
  if (tela === "cadastro") Object.assign(t, { usuario: "", mensagem: "Escolha o usuário e uma senha (mínimo 8 caracteres)." });
  if (tela === "senha") Object.assign(t, { mensagem: "Digite o usuário e a senha." });
  const inicio = Date.now();
  let numero = 0;

  // Um "rosto" desenhado (cabeça e ombros) no lugar da câmera.
  const tela2d = Object.assign(document.createElement("canvas"), { width: 480, height: 360 });
  function desenharRosto(virar) {
    const c = tela2d.getContext("2d");
    const fundo = c.createRadialGradient(240, 150, 30, 240, 180, 330);
    fundo.addColorStop(0, "#5a5a5a"); fundo.addColorStop(1, "#141414");
    c.fillStyle = fundo; c.fillRect(0, 0, 480, 360);
    c.fillStyle = "#1c1c1c";
    c.beginPath(); c.ellipse(240, 400, 170, 110, 0, 0, Math.PI * 2); c.fill();
    const rosto = c.createRadialGradient(240 - virar * 30, 150, 10, 240, 165, 95);
    rosto.addColorStop(0, "#c8c8c8"); rosto.addColorStop(1, "#4a4a4a");
    c.fillStyle = rosto;
    c.beginPath(); c.ellipse(240 + virar * 8, 165, 70 - Math.abs(virar) * 10, 92, 0, 0, Math.PI * 2); c.fill();
    c.fillStyle = "#2a2a2a";
    for (const lado of [-1, 1]) { c.beginPath(); c.ellipse(240 + lado * 25 + virar * 22, 150, 9, 5, 0, 0, Math.PI * 2); c.fill(); }
    c.fillRect(226 + virar * 26, 205, 28, 4);
    for (let i = 0; i < 2500; i++) { c.fillStyle = `rgba(255,255,255,${Math.random() * 0.06})`; c.fillRect(Math.random() * 480, Math.random() * 360, 1, 1); }
    return tela2d.toDataURL("image/jpeg", 0.7);
  }

  function avancar() {
    if (tela !== "rosto" || t.terminou) return;
    const s = (Date.now() - inicio) / 1000;
    if (s < 1.4) Object.assign(t, { camera: "preparando", mensagem: "Preparando o reconhecimento facial..." });
    else if (s < 4.2) Object.assign(t, { camera: "ok", fase: "procurar", progresso: 0.5 * Math.min(1, (s - 1.4) / 2.8),
                                         mensagem: s < 2.2 ? "Procurando o seu rosto..." : "Confirmando o seu rosto...",
                                         caixa: s < 2.2 ? null : [0.35, 0.2, 0.3, 0.51] });
    else if (s < 7.4) Object.assign(t, { fase: "virar", progresso: 0.5 + 0.5 * Math.min(1, (s - 4.6) / 2.8),
                                         mensagem: "Agora vire a cabeça devagar para o seu lado esquerdo.",
                                         caixa: [0.35 - Math.min(0.06, (s - 4.2) * 0.025), 0.2, 0.28, 0.51] });
    else Object.assign(t, { fase: "ok", progresso: 1, terminou: true, sucesso: true, mensagem: "Bem-vindo, Felipe.", tom: "ok", link_senha: false, link_codigo: false });
  }

  return {
    inicio: async () => { avancar(); return { ...t }; },
    estado: async () => { avancar(); return { ...t }; },
    quadro: async (ultimo) => {
      if (t.camera !== "ok") return { n: numero };
      numero++;
      const s = (Date.now() - inicio) / 1000;
      return { n: numero, imagem: desenharRosto(t.fase === "virar" ? -Math.min(1, (s - 4.2) / 2) : 0) };
    },
    niveis: async () => ({ voce: null }),
    mostrar_senha: async () => Object.assign(t, { pede_senha: true, link_senha: false }) && { ...t },
    abrir_codigo: async () => Object.assign(t, { pede_codigo: true, link_codigo: false }) && { ...t },
    enviar_codigo: async () => Object.assign(t, { mensagem: "(demonstração) Código aceito. Agora digite a senha.", tom: "ok", pede_codigo: false, pede_senha: true }) && { ...t },
    enviar_senha: async (usuario, senha) => (senha
      ? Object.assign(t, { terminou: true, sucesso: true, progresso: 1, mensagem: `Bem-vindo, ${usuario || "Felipe"}.`, tom: "ok" })
      : Object.assign(t, { mensagem: "Usuário ou senha incorretos.", tom: "erro", falhas: t.falhas + 1 })) && { ...t },
    tentar_rosto_de_novo: async () => ({ ...t }),
    confirmar_credenciais: async (usuario, senha, repetida) => {
      if (usuario.trim().length < 3) Object.assign(t, { mensagem: "O usuário precisa ter pelo menos 3 letras.", tom: "erro" });
      else if (senha.length < 8) Object.assign(t, { mensagem: "A senha precisa ter pelo menos 8 caracteres.", tom: "erro" });
      else if (senha !== repetida) Object.assign(t, { mensagem: "As duas senhas não são iguais.", tom: "erro" });
      else Object.assign(t, { pagina: "codigo", usuario, codigo_novo: "K7QHT-M3WXP-9RZ4D-UVB2N", progresso: 1, tom: "aviso",
                              mensagem: "Anote este código de recuperação em um lugar seguro. Ele entra no lugar do rosto se a câmera falhar e só aparece agora." });
      return { ...t };
    },
    sem_rosto: async () => ({ ...t }),
    concluir: async () => Object.assign(t, { terminou: true, sucesso: true, mensagem: "Pronto! O seu login foi criado.", tom: "ok" }) && { ...t },
    fechar: async () => {
      // na demonstração, recomeça em vez de fechar
      setTimeout(() => location.reload(), 2500);
    },
  };
}
