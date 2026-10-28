/* Janela [ vídeos ]: o seu estilo dos vídeos (estilo_video.py), os exemplos e um vídeo novo.
 *
 * Quatro abas: o meu estilo (formato, ritmo, letra, cores, transições, narração, legendas...), os meus vídeos (o filtro, o
 * enquadramento e as legendas de "edita esse vídeo no meu estilo"), os exemplos (prints e vídeos; o Claude monta o guia) e
 * o vídeo novo. Ao lado, uma prévia animada que segue o estilo enquanto você escolhe (o vídeo de verdade é montado no PC
 * pelo video_animado.py, com as mesmas contas de tempo).
 *
 * É um componente: o painel do PC (interface_web) e a tela Estúdio do app do celular (interface_app, que o recebe em
 * /visual/estilo_video.js) usam o mesmo, com os mesmos ids. No painel ele fala com a ponte do pywebview (o `api` do app.js);
 * o app troca a ponte pela dele com `JanelaDeVideos.usar(() => adaptador)`. Sem nenhuma das duas, usa os dados de
 * demonstração do fim deste arquivo.
 */
(function () {
"use strict";
const $ = (seletor) => document.querySelector(seletor);
const novo = (tag, classe, texto) => {
  const el = document.createElement(tag);
  if (classe) el.className = classe;
  if (texto !== undefined) el.textContent = texto;
  return el;
};
let fonte = () => (typeof api !== "undefined" && api && api.estilo_video ? api : null);  // eslint-disable-line no-undef
const vd = { dados: null, rascunho: null, salvo: "", aba: "estilo", miniaturas: {}, formatoNovo: "" };

const LINHAS_DO_ESTILO = [
  ["formato", "formato"], ["duracao", "duração"], ["ritmo", "ritmo"], ["tom", "tom do texto"], ["letra", "letra"],
  ["cores", "cores"], ["entrada", "texto entra"], ["transicao", "transição"], ["movimento", "ilustrações"],
  ["narracao", "narração"], ["legendas", "legendas"], ["tamanho_legenda", "tamanho da legenda"],
];
const NOMES_DAS_CORES = { fundo: "fundo", fundo2: "fundo (embaixo)", texto: "texto", destaque: "destaque" };

function apiVideos() { return fonte() || videosDemonstracao; }

async function abrirVideos(abrir = true) {
  const fundo = $("#fundo-videos");
  if (!fundo) return;
  if (!abrir && !fundo.hidden && mudouVideos() && !confirm("Fechar sem salvar o seu estilo de vídeo?")) return;
  fundo.hidden = !abrir;
  $("#btn-videos").classList.toggle("ativo", abrir);
  if (abrir) await carregarVideos();
}

async function carregarVideos() {
  try { preencherVideos(await apiVideos().estilo_video()); } catch (erro) { mensagemVideos("Não consegui ler o seu estilo de vídeo.", true); }
  mostrarAbaVideos(vd.aba);
  animarPrevia();
}

function preencherVideos(dados) {
  vd.dados = dados;
  const estilo = JSON.parse(JSON.stringify(dados.estilo));
  vd.rascunho = estilo;
  $("#vd-abertura").checked = estilo.abertura !== false;
  $("#vd-assinatura").value = estilo.assinatura || "";
  $("#vd-instrucoes").value = estilo.instrucoes || "";
  $("#vd-guia").value = (estilo.guia && estilo.guia.texto) || "";
  $("#vd-usar-guia").checked = estilo.usar_guia !== false;
  $("#vd-edicao-legendas").checked = (estilo.edicao || {}).legendas !== false;
  vd.salvo = JSON.stringify(rascunhoVideos());
  desenharOpcoesVideos();
  desenharExemplos();
  desenharNovo();
  aoMudarVideos();
}

function rascunhoVideos() {
  const r = vd.rascunho;
  if (!r) return {};
  r.abertura = $("#vd-abertura").checked;
  r.assinatura = $("#vd-assinatura").value;
  r.instrucoes = $("#vd-instrucoes").value;
  r.usar_guia = $("#vd-usar-guia").checked;
  r.edicao = { ...(r.edicao || {}), legendas: $("#vd-edicao-legendas").checked };
  const { guia, atualizado, ...resto } = r;
  return { ...resto, guia_texto: $("#vd-guia").value };
}
function mudouVideos() { return !!vd.dados && JSON.stringify(rascunhoVideos()) !== vd.salvo; }

function aoMudarVideos() {
  const mudou = mudouVideos();
  const estado = $("#vd-estado");
  estado.textContent = mudou ? "● alterações não salvas" : vd.dados && vd.dados.estilo.atualizado ? "seu estilo · salvo" : "padrão";
  estado.classList.toggle("mudou", mudou);
  $("#vd-salvar").disabled = !mudou;
  $("#vd-desfazer").disabled = !mudou;
  legendaDaPrevia();
}

function mensagemVideos(texto, erro = false) {
  const alvo = $("#vd-mensagem");
  alvo.textContent = texto;
  alvo.classList.toggle("erro", erro);
}

function mostrarAbaVideos(aba) {
  vd.aba = aba;
  document.querySelectorAll(".aba-vd").forEach((b) => b.classList.toggle("ativo", b.dataset.aba === aba));
  document.querySelectorAll(".vd-aba").forEach((s) => { s.hidden = s.dataset.aba !== aba; });
}

/* ------------------------------------------------------------------ opções */
function linhaDeOpcoes(alvo, chave, rotulo, objeto, opcoes) {
  const linha = novo("div", "es-linha");
  linha.append(novo("span", "es-rotulo", rotulo));
  const grupo = novo("div", `es-opcoes${chave === "cores" ? " es-cores" : ""}`);
  const explica = novo("small", "es-explica");
  for (const opcao of opcoes) {
    const botao = novo("button", "es-opcao");
    botao.type = "button";
    botao.title = opcao.explica || "";
    if (chave === "cores") {
      const amostra = novo("span", "es-amostra vd-amostra");
      for (const nome of ["fundo", "texto", "destaque"]) {
        const cor = novo("i");
        cor.style.background = opcao.valor === "livre" ? (objeto.cores_livres || {})[nome] : (vd.dados.paletas[opcao.valor] || {})[nome];
        amostra.append(cor);
      }
      if (opcao.valor === "livre") amostra.id = "vd-amostra-livre";
      botao.append(amostra, novo("span", "", opcao.rotulo));
      if (opcao.valor === "guia" && !((vd.dados.estilo.guia || {}).cores || {}).fundo) botao.disabled = true;
    } else {
      botao.textContent = opcao.rotulo;
      if (chave === "letra") botao.style.fontFamily = vd.dados.letras[opcao.valor];
    }
    const marcar = () => {
      grupo.querySelectorAll(".es-opcao").forEach((b) => b.classList.toggle("ativo", b === botao));
      explica.textContent = opcao.explica || "";
    };
    botao.onclick = () => {
      objeto[chave] = opcao.valor;
      marcar();
      if (chave === "cores") desenharCoresLivres();
      aoMudarVideos();
    };
    grupo.append(botao);
    if (objeto[chave] === opcao.valor) marcar();
  }
  linha.append(grupo);
  if (chave === "cores") {
    const livres = novo("div", "es-livres");
    livres.id = "vd-livres";
    linha.append(livres);
  }
  linha.append(explica);
  alvo.append(linha);
}

function desenharOpcoesVideos() {
  const r = vd.rascunho;
  const grade = $("#vd-opcoes");
  grade.innerHTML = "";
  for (const [chave, rotulo] of LINHAS_DO_ESTILO) linhaDeOpcoes(grade, chave, rotulo, r, vd.dados.opcoes[chave]);
  desenharCoresLivres();
  const edicao = $("#vd-edicao");
  edicao.innerHTML = "";
  r.edicao = r.edicao || {};
  linhaDeOpcoes(edicao, "filtro", "filtro de cor", r.edicao, vd.dados.opcoes_da_edicao.filtro);
  linhaDeOpcoes(edicao, "enquadrar", "formato", r.edicao, vd.dados.opcoes_da_edicao.enquadrar);
}

function desenharCoresLivres() {
  const alvo = $("#vd-livres");
  if (!alvo) return;
  const r = vd.rascunho;
  alvo.innerHTML = "";
  alvo.hidden = r.cores !== "livre";
  if (alvo.hidden) return;
  const linha = novo("div", "es-cores-linha");
  for (const [chave, rotulo] of Object.entries(NOMES_DAS_CORES)) {
    const campo = novo("label", "es-cor");
    const entrada = novo("input");
    entrada.type = "color";
    entrada.value = (r.cores_livres[chave] || "#000000").toLowerCase();
    entrada.addEventListener("input", () => {
      r.cores_livres[chave] = entrada.value;
      const amostra = $("#vd-amostra-livre");
      if (amostra) ["fundo", "texto", "destaque"].forEach((nome, i) => { amostra.children[i].style.background = r.cores_livres[nome]; });
      aoMudarVideos();
    });
    campo.append(entrada, novo("span", "", rotulo));
    linha.append(campo);
  }
  alvo.append(linha);
}

/* ------------------------------------------------------------------ exemplos */
async function desenharExemplos() {
  const lista = $("#vd-exemplos");
  lista.innerHTML = "";
  const exemplos = vd.dados.exemplos || [];
  $("#vd-exemplos-conta").textContent = `${exemplos.length} de ${vd.dados.maximo_de_exemplos} imagens`;
  $("#vd-exemplo-adicionar").disabled = exemplos.length >= vd.dados.maximo_de_exemplos;
  $("#vd-guia-montar").disabled = !exemplos.length;
  if (!exemplos.length) lista.append(novo("p", "es-vazio", "Nenhum exemplo ainda. Adicione prints ou vídeos de que você gosta."));
  for (const exemplo of exemplos) {
    const cartao = novo("figure", "es-foto");
    const imagem = novo("img");
    imagem.alt = exemplo.nome;
    const apagar = novo("button", "es-foto-apagar", "×");
    apagar.title = "Apagar este exemplo";
    apagar.onclick = async () => {
      if (!confirm(`Apagar o exemplo “${exemplo.nome}”?`)) return;
      await acaoVideos(apiVideos().estilo_video_exemplo_apagar(exemplo.id), "Exemplo apagado.", true);
    };
    cartao.append(imagem, novo("figcaption", "", exemplo.nome), apagar);
    lista.append(cartao);
    if (!vd.miniaturas[exemplo.id]) {
      try { vd.miniaturas[exemplo.id] = await apiVideos().estilo_video_exemplo(exemplo.id); } catch (erro) { vd.miniaturas[exemplo.id] = ""; }
    }
    if (vd.miniaturas[exemplo.id]) imagem.src = vd.miniaturas[exemplo.id];
  }
  const guia = vd.dados.estilo.guia || {};
  const coresDoGuia = $("#vd-cores-guia");
  coresDoGuia.innerHTML = "";
  for (const [nome, cor] of Object.entries(guia.cores || {})) {
    const amostra = novo("i");
    amostra.style.background = cor;
    amostra.title = `${nome}: ${cor}`;
    coresDoGuia.append(amostra);
  }
  const quando = guia.atualizado ? new Date(guia.atualizado * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
  $("#vd-guia-quem").textContent = guia.quem ? `montado pelo ${guia.quem} com ${guia.exemplos} imagem(ns) · ${quando}` : "";
}

async function montarGuiaDeVideo() {
  const botao = $("#vd-guia-montar");
  if (mudouVideos() && !confirm("Montar o guia agora descarta as alterações não salvas desta janela. Continuar?")) return;
  botao.disabled = true;
  botao.textContent = "o Claude está olhando os exemplos…";
  mensagemVideos("Leva de 20 s a 1 min.");
  try {
    const resposta = await acaoVideos(apiVideos().estilo_video_guia(), "");
    if (resposta.ok && resposta.dados) {
      const guia = resposta.dados.estilo.guia;
      mensagemVideos(`Guia montado pelo ${guia.quem} a partir de ${guia.exemplos} imagem(ns). Para usar as cores dele, escolha "Do guia".`);
    }
  } finally {
    botao.textContent = "montar o guia com o Claude";
    botao.disabled = !(vd.dados.exemplos || []).length;
  }
}

/* ------------------------------------------------------------------ vídeo novo */
function desenharNovo() {
  const grupo = $("#vd-novo-formato");
  grupo.innerHTML = "";
  for (const [valor, rotulo] of [["", "o do estilo"], ["16x9", "deitado"], ["9x16", "em pé"], ["1x1", "quadrado"]]) {
    const botao = novo("button", `es-opcao${vd.formatoNovo === valor ? " ativo" : ""}`, rotulo);
    botao.type = "button";
    botao.onclick = () => { vd.formatoNovo = valor; desenharNovo(); };
    grupo.append(botao);
  }
}

async function fazerVideo() {
  const assunto = $("#vd-assunto").value.trim();
  if (!assunto) { mensagemVideos("Escreva sobre o que é o vídeo.", true); return; }
  if (mudouVideos() && !confirm("O vídeo usa o estilo salvo, e há alterações não salvas. Fazer mesmo assim?")) return;
  const opcoes = {};
  if (vd.formatoNovo) opcoes.formato = vd.formatoNovo;
  if ($("#vd-novo-sem-narracao").checked) opcoes.narracao = "nenhuma";
  const resposta = await apiVideos().estilo_video_novo(assunto, opcoes);
  mensagemVideos(resposta.mensagem, !resposta.ok);
  if (resposta.ok) $("#vd-assunto").value = "";
}

/* ------------------------------------------------------------------ ações */
async function acaoVideos(promessa, sucesso, manterRascunho = false) {
  const rascunho = manterRascunho && mudouVideos() ? rascunhoVideos() : null;
  try {
    const resposta = await promessa;
    if (!resposta.ok) { mensagemVideos(resposta.mensagem || "Não deu certo.", true); return resposta; }
    if (resposta.dados) {
      preencherVideos(resposta.dados);
      if (rascunho) {  // apagar ou adicionar um exemplo não desfaz as escolhas ainda não salvas
        const { guia_texto, ...resto } = rascunho;
        Object.assign(vd.rascunho, resto);
        $("#vd-guia").value = guia_texto;
        $("#vd-abertura").checked = resto.abertura; $("#vd-assinatura").value = resto.assinatura;
        $("#vd-instrucoes").value = resto.instrucoes; $("#vd-usar-guia").checked = resto.usar_guia;
        $("#vd-edicao-legendas").checked = resto.edicao.legendas;
        desenharOpcoesVideos();
        aoMudarVideos();
      }
    }
    mensagemVideos(resposta.mensagem || sucesso);
    return resposta;
  } catch (erro) {
    mensagemVideos(`Não deu certo: ${erro}`, true);
    return { ok: false };
  }
}

/* ------------------------------------------------------------------ a prévia animada */
// As mesmas contas de tempo do video_animado.py (ritmo, transição, entrada dos textos), num vídeo de mentira de duas cenas.
const DURACAO_BASE = { calmo: 6, normal: 4.5, rapido: 3 };
const DURACAO_DA_TRANSICAO = { calmo: 0.9, normal: 0.6, rapido: 0.35 };
const DURACAO_DA_ENTRADA = { calmo: 0.9, normal: 0.6, rapido: 0.4 };
const ATRASO_ENTRE_TEXTOS = { calmo: 0.35, normal: 0.25, rapido: 0.15 };
const PROPORCOES = { "16x9": [16, 9], "9x16": [9, 16], "1x1": [1, 1] };
const TAMANHOS_DA_LEGENDA = { pequena: 0.038, media: 0.046, grande: 0.056 };
const CENAS_DA_PREVIA = [
  { layout: "lado", titulo: "A luz é o começo", texto: "As folhas captam a luz do sol.", legenda: "Tudo começa com a luz do sol" },
  { layout: "lista", titulo: "O que entra e sai", itens: ["Entra: luz, água e gás", "Sai: açúcar e oxigênio"],
    legenda: "Entram luz e água; sai oxigênio" },
];
const suave = (p) => { p = Math.min(1, Math.max(0, p)); return p * p * (3 - 2 * p); };
const saidaCubica = (p) => { p = Math.min(1, Math.max(0, p)); return 1 - (1 - p) ** 3; };
const salto = (p) => { p = Math.min(1, Math.max(0, p)); return 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2; };
let quadroDaPrevia = 0, inicioDaPrevia = 0;

function paletaDaPrevia(r) {
  if (r.cores === "livre") return { ...r.cores_livres };
  return vd.dados.paletas[r.cores] || vd.dados.paletas.preto_dourado;
}

function cenasDaPrevia(r) {
  const cenas = [];
  if (r.abertura) cenas.push({ layout: "titulo", titulo: "Fotossíntese", texto: "como as plantas comem luz", duracao: 2.6 });
  for (const cena of CENAS_DA_PREVIA) cenas.push({ ...cena, duracao: Math.max(DURACAO_BASE[r.ritmo] * 0.8, 3.2) });
  if (r.assinatura && r.assinatura.trim()) cenas.push({ layout: "titulo", titulo: r.assinatura.trim(), texto: "Fotossíntese", duracao: 2.4 });
  return cenas;
}

function quebrarLinhas(ctx, texto, largura) {
  const linhas = [];
  let atual = "";
  for (const palavra of texto.split(/\s+/)) {
    const tentativa = atual ? `${atual} ${palavra}` : palavra;
    if (atual && ctx.measureText(tentativa).width > largura) { linhas.push(atual); atual = palavra; } else atual = tentativa;
  }
  if (atual) linhas.push(atual);
  return linhas;
}

function desenharSol(ctx, cx, cy, raio, cores) {
  const brilho = ctx.createRadialGradient(cx, cy, raio * 0.1, cx, cy, raio);
  brilho.addColorStop(0, "#fff6c8");
  brilho.addColorStop(1, cores.destaque);
  ctx.fillStyle = brilho;
  ctx.beginPath(); ctx.arc(cx, cy, raio, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = cores.destaque; ctx.lineWidth = raio * 0.09; ctx.lineCap = "round";
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * raio * 1.25, cy + Math.sin(a) * raio * 1.25);
    ctx.lineTo(cx + Math.cos(a) * raio * 1.5, cy + Math.sin(a) * raio * 1.5);
    ctx.stroke();
  }
}

// Uma cena no momento `t` (segundos desde o começo dela), no tamanho L x A.
function desenharCena(ctx, cena, t, L, A, r) {
  const cores = paletaDaPrevia(r);
  const fundo = ctx.createLinearGradient(0, 0, 0, A);
  fundo.addColorStop(0, cores.fundo); fundo.addColorStop(1, cores.fundo2);
  ctx.fillStyle = fundo; ctx.fillRect(0, 0, L, A);
  const lado = Math.min(L, A), m = lado * 0.07, emPe = A > L * 1.2;
  const rodape = r.legendas === "embaixo" ? A * 0.17 : 0, util = A - rodape;
  const letraCss = vd.dados.letras[r.letra] || "sans-serif";
  const letraTexto = r.letra === "impacto" ? vd.dados.letras.moderna : letraCss;
  const peso = ["impacto", "condensada"].includes(r.letra) ? "" : "bold ";
  const ritmo = r.ritmo, transicao = r.transicao === "corte" ? 0 : DURACAO_DA_TRANSICAO[ritmo];
  // A ilustração (o sol), com o movimento do estilo.
  let caixaTexto = [m, m, L - 2 * m, util - 2 * m];
  if (cena.layout === "lado") {
    const progresso = t / cena.duracao;
    const zoom = r.movimento === "aproximar" ? 1 + 0.08 * progresso : 1;
    const passeio = r.movimento === "passear" ? (progresso - 0.5) * 0.08 * L : 0;
    if (emPe) {
      desenharSol(ctx, L / 2 + passeio, m + util * 0.24, util * 0.13 * zoom, cores);
      caixaTexto = [m, util * 0.52, L - 2 * m, util * 0.46];
    } else {
      desenharSol(ctx, L * 0.74 + passeio, util / 2, util * 0.22 * zoom, cores);
      caixaTexto = [m, m, L * 0.5 - 1.5 * m, util - 2 * m];
    }
  }
  // Os textos, empilhados no meio da caixa, cada um entrando do jeito escolhido.
  const centro = cena.layout === "titulo";
  const blocos = [];
  const tamanhoTitulo = lado * (centro ? 0.11 : 0.082), tamanhoTexto = lado * 0.046;
  if (cena.titulo) blocos.push({ texto: cena.titulo, fonte: `${peso}${tamanhoTitulo}px ${letraCss}`, cor: centro ? cores.texto : cores.destaque, tamanho: tamanhoTitulo });
  if (cena.texto) blocos.push({ texto: cena.texto, fonte: `${tamanhoTexto}px ${letraTexto}`, cor: centro ? cores.destaque : cores.texto, tamanho: tamanhoTexto });
  for (const item of cena.itens || []) blocos.push({ texto: item, fonte: `${lado * 0.043}px ${letraTexto}`, cor: cores.texto, tamanho: lado * 0.043, item: true });
  let altura = 0;
  for (const bloco of blocos) {
    ctx.font = bloco.fonte;
    bloco.linhas = quebrarLinhas(ctx, bloco.texto, caixaTexto[2] - (bloco.item ? bloco.tamanho : 0));
    bloco.altura = bloco.linhas.length * bloco.tamanho * 1.22;
    altura += bloco.altura + tamanhoTexto * 0.5;
  }
  let y = caixaTexto[1] + Math.max(0, (caixaTexto[3] - altura) / 2);
  blocos.forEach((bloco, ordem) => {
    const inicio = 0.15 + transicao * 0.6 + ordem * ATRASO_ENTRE_TEXTOS[ritmo];
    const decorrido = t - inicio, entrada = DURACAO_DA_ENTRADA[ritmo];
    const topo = y;
    y += bloco.altura + tamanhoTexto * 0.5;
    if (decorrido < 0) return;
    let alfa = 1, dx = 0, dy = 0, escala = 1, letras = Infinity, palavras = Infinity;
    const p = decorrido / entrada;
    if (r.entrada === "digitar") letras = Math.floor(bloco.texto.length * Math.min(1, decorrido / Math.max(entrada, bloco.texto.length * 0.028)));
    else if (r.entrada === "palavra") {
      const total = bloco.texto.split(/\s+/).length;
      palavras = Math.ceil(total * Math.min(1, decorrido / Math.max(entrada, total * 0.16)));
    } else if (r.entrada === "pop") { escala = 0.82 + 0.18 * salto(p); alfa = Math.min(1, p * 2.5); }
    else {
      alfa = saidaCubica(p);
      const andar = 1 - saidaCubica(p);
      if (r.entrada === "deslizar") dx = -andar * L * 0.08; else dy = andar * A * 0.04;
    }
    ctx.save();
    ctx.globalAlpha = alfa;
    ctx.font = bloco.fonte;
    ctx.fillStyle = bloco.cor;
    ctx.textBaseline = "top";
    ctx.shadowColor = "rgba(0,0,0,.55)"; ctx.shadowBlur = bloco.tamanho * 0.2;
    if (escala !== 1) {
      const cx = caixaTexto[0] + caixaTexto[2] / 2, cy = topo + bloco.altura / 2;
      ctx.translate(cx, cy); ctx.scale(escala, escala); ctx.translate(-cx, -cy);
    }
    let restoLetras = letras, restoPalavras = palavras;
    bloco.linhas.forEach((linha, n) => {
      let parte = linha;
      if (restoLetras !== Infinity) { parte = linha.slice(0, Math.max(0, restoLetras)); restoLetras -= linha.length; }
      if (restoPalavras !== Infinity) { const pals = linha.split(" "); parte = pals.slice(0, Math.max(0, restoPalavras)).join(" "); restoPalavras -= pals.length; }
      const largura = ctx.measureText(linha).width;
      let x = caixaTexto[0] + (bloco.item ? bloco.tamanho * 0.9 : 0);
      if (centro) x = caixaTexto[0] + (caixaTexto[2] - largura) / 2;
      const linhaY = topo + n * bloco.tamanho * 1.22;
      if (bloco.item && n === 0) { ctx.fillStyle = cores.destaque; ctx.fillRect(caixaTexto[0], linhaY + bloco.tamanho * 0.38, bloco.tamanho * 0.33, bloco.tamanho * 0.33); ctx.fillStyle = bloco.cor; }
      ctx.fillText(parte, x + dx, linhaY + dy);
    });
    ctx.restore();
  });
  // A legenda da narração.
  if (cena.legenda && r.legendas !== "nenhuma") {
    const aparecer = suave((t - 0.35) / 0.15);
    if (aparecer > 0) {
      const tamanho = lado * TAMANHOS_DA_LEGENDA[r.tamanho_legenda] * (r.legendas === "centro" ? 1.35 : 1);
      ctx.save();
      ctx.globalAlpha = aparecer;
      ctx.textBaseline = "middle";
      if (r.legendas === "centro") {
        ctx.font = `${peso}${tamanho}px ${letraCss}`;
        const texto = cena.legenda.toUpperCase();
        const largura = Math.min(ctx.measureText(texto).width, L * 0.9);
        ctx.fillStyle = cores.destaque; ctx.shadowColor = "rgba(0,0,0,.7)"; ctx.shadowBlur = tamanho * 0.3;
        ctx.fillText(texto, (L - largura) / 2, A / 2, L * 0.9);
      } else {
        ctx.font = `${tamanho}px ${letraTexto}`;
        const largura = Math.min(ctx.measureText(cena.legenda).width, L * 0.82), folga = tamanho * 0.45;
        const x = (L - largura) / 2, yLegenda = A - A * 0.06 - tamanho * 0.9;
        ctx.fillStyle = cores.fundo; ctx.globalAlpha = aparecer * 0.8;
        ctx.fillRect(x - folga, yLegenda - tamanho * 0.75, largura + 2 * folga, tamanho * 1.5);
        ctx.globalAlpha = aparecer; ctx.fillStyle = cores.texto;
        ctx.fillText(cena.legenda, x, yLegenda, L * 0.82);
      }
      ctx.restore();
    }
  }
}

function animarPrevia() {
  cancelAnimationFrame(quadroDaPrevia);
  inicioDaPrevia = performance.now();
  const canvas = $("#vd-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const telas = { antes: document.createElement("canvas"), depois: document.createElement("canvas") };
  const passo = (agora) => {
    quadroDaPrevia = requestAnimationFrame(passo);
    const r = vd.rascunho;
    if (!r || !canvas.offsetParent) return;  // janela fechada ou aba escondida: não desenha
    const [pl, pa] = PROPORCOES[r.formato] || [16, 9];
    const caixa = canvas.parentElement.clientWidth || 300;
    const largura = pa > pl ? Math.round(caixa * 0.62) : caixa;
    const altura = Math.round(largura * pa / pl);
    const escala = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== Math.round(largura * escala) || canvas.height !== Math.round(altura * escala)) {
      canvas.width = Math.round(largura * escala); canvas.height = Math.round(altura * escala);
      canvas.style.width = `${largura}px`; canvas.style.height = `${altura}px`;
      for (const tela of Object.values(telas)) { tela.width = canvas.width; tela.height = canvas.height; }
    }
    const L = canvas.width, A = canvas.height;
    const cenas = cenasDaPrevia(r);
    const total = cenas.reduce((soma, c) => soma + c.duracao, 0);
    let t = ((agora - inicioDaPrevia) / 1000) % total, atual = 0;
    while (t >= cenas[atual].duracao) { t -= cenas[atual].duracao; atual++; }
    const transicao = r.transicao === "corte" ? 0 : DURACAO_DA_TRANSICAO[r.ritmo];
    const cAntes = telas.antes.getContext("2d"), cDepois = telas.depois.getContext("2d");
    desenharCena(cDepois, cenas[atual], t, L, A, r);
    ctx.clearRect(0, 0, L, A);
    if (atual > 0 && transicao && t < transicao) {
      desenharCena(cAntes, cenas[atual - 1], cenas[atual - 1].duracao + t, L, A, r);
      const p = suave(t / transicao);
      if (r.transicao === "dissolver") { ctx.drawImage(telas.antes, 0, 0); ctx.globalAlpha = p; ctx.drawImage(telas.depois, 0, 0); ctx.globalAlpha = 1; }
      else if (r.transicao === "deslizar") { ctx.drawImage(telas.antes, -p * L, 0); ctx.drawImage(telas.depois, L - p * L, 0); }
      else if (r.transicao === "zoom") {
        const z = 1.25 - 0.25 * p;
        ctx.drawImage(telas.antes, 0, 0); ctx.globalAlpha = p;
        ctx.drawImage(telas.depois, (L - L * z) / 2, (A - A * z) / 2, L * z, A * z); ctx.globalAlpha = 1;
      } else { ctx.drawImage(telas.antes, 0, 0); ctx.drawImage(telas.depois, 0, 0, p * L, A, 0, 0, p * L, A); }
    } else {
      ctx.drawImage(telas.depois, 0, 0);
    }
  };
  quadroDaPrevia = requestAnimationFrame(passo);
}

function legendaDaPrevia() {
  const alvo = $("#vd-previa-legenda");
  if (!alvo || !vd.rascunho) return;
  const r = vd.rascunho;
  const nome = (chave) => ((vd.dados.opcoes[chave] || []).find((o) => o.valor === r[chave]) || {}).rotulo || r[chave];
  const guia = r.usar_guia !== false && $("#vd-guia").value.trim() ? ", com o guia dos exemplos" : "";
  alvo.textContent = `Como vai sair: ${nome("formato").toLowerCase()}, ${nome("duracao").toLowerCase()}, ritmo ${nome("ritmo").toLowerCase()}, `
    + `${nome("narracao").toLowerCase()}${guia}. A prévia é uma amostra do visual; o vídeo de verdade é montado no PC, com as ilustrações do Claude.`;
}

/* ------------------------------------------------------------------ ligações */
function ligarVideos() {
  if (!$("#vd-opcoes")) return;  // esta página não tem a janela
  if ($("#fundo-videos")) {
    $("#btn-videos").onclick = () => abrirVideos($("#fundo-videos").hidden);
    $("#fundo-videos").addEventListener("click", (e) => { if (e.target.id === "fundo-videos") abrirVideos(false); });
    $("#fundo-videos").addEventListener("keydown", (e) => {
      if (e.key === "s" && e.ctrlKey) { e.preventDefault(); if (mudouVideos()) $("#vd-salvar").click(); }
    });
  }
  document.querySelectorAll(".aba-vd").forEach((b) => { b.onclick = () => mostrarAbaVideos(b.dataset.aba); });
  for (const id of ["#vd-assinatura", "#vd-instrucoes", "#vd-guia"]) $(id).addEventListener("input", aoMudarVideos);
  for (const id of ["#vd-abertura", "#vd-usar-guia", "#vd-edicao-legendas"]) $(id).addEventListener("change", aoMudarVideos);
  $("#vd-salvar").onclick = () => acaoVideos(apiVideos().estilo_video_salvar(rascunhoVideos()), "Salvo. Vale para os próximos vídeos.");
  $("#vd-desfazer").onclick = () => { preencherVideos(vd.dados); mensagemVideos("Voltei ao que está salvo."); };
  $("#vd-padrao").onclick = () => {
    if (confirm("Voltar o estilo ao padrão? Os exemplos e o guia continuam.")) acaoVideos(apiVideos().estilo_video_padrao(), "Pronto: o estilo padrão voltou.");
  };
  $("#vd-exemplo-adicionar").onclick = () => acaoVideos(apiVideos().estilo_video_exemplos_adicionar(), "", true);
  $("#vd-guia-montar").onclick = montarGuiaDeVideo;
  $("#vd-criar").onclick = fazerVideo;
  $("#vd-refazer").onclick = async () => {
    if (mudouVideos() && !confirm("O vídeo é montado com o estilo salvo, e há alterações não salvas. Refazer mesmo assim?")) return;
    const resposta = await apiVideos().estilo_video_refazer();
    mensagemVideos(resposta.mensagem, !resposta.ok);
  };
}
window.abrirVideos = abrirVideos;  // o app.js do painel fecha a janela com o Esc e o ×
window.JanelaDeVideos = { usar(novaFonte) { fonte = novaFonte; }, carregar: carregarVideos, mudou: mudouVideos };
if (document.readyState === "loading") addEventListener("DOMContentLoaded", ligarVideos); else ligarVideos();

/* ------------------------------------------------------------------ demonstração */
// Aberta num navegador comum (sem a Beatriz): as opções de verdade (estilo_video.para_a_janela), gravadas só na memória.
// Gerado de estilo_video.para_a_janela() (refaça se as opções mudarem).
const VD_DADOS_FIXOS = {"estilo": {"formato": "16x9", "duracao": "medio", "ritmo": "normal", "tom": "didatico", "letra": "moderna", "entrada": "surgir", "transicao": "dissolver", "movimento": "aproximar", "narracao": "thalita", "legendas": "embaixo", "tamanho_legenda": "media", "cores": "preto_dourado", "cores_livres": {"fundo": "#101010", "fundo2": "#2b2b2b", "texto": "#ffffff", "destaque": "#f5c04a"}, "abertura": true, "assinatura": "", "instrucoes": "", "usar_guia": true, "edicao": {"filtro": "", "enquadrar": "original", "legendas": true}, "guia": {"texto": "", "cores": {}, "quem": "", "exemplos": 0, "atualizado": null}, "atualizado": null}, "opcoes": {"formato": [{"valor": "16x9", "rotulo": "Deitado 16:9", "explica": "YouTube, TV e computador."}, {"valor": "9x16", "rotulo": "Em pé 9:16", "explica": "Reels, TikTok, Shorts e status do WhatsApp."}, {"valor": "1x1", "rotulo": "Quadrado", "explica": "Feed do Instagram."}], "duracao": [{"valor": "curto", "rotulo": "Curto", "explica": "Uns 20 segundos, de 3 a 5 cenas."}, {"valor": "medio", "rotulo": "Médio", "explica": "Uns 45 segundos, de 6 a 8 cenas."}, {"valor": "longo", "rotulo": "Longo", "explica": "Um minuto e meio, de 10 a 14 cenas."}], "ritmo": [{"valor": "calmo", "rotulo": "Calmo", "explica": "Cenas longas e transições lentas."}, {"valor": "normal", "rotulo": "Normal", "explica": "O de sempre."}, {"valor": "rapido", "rotulo": "Rápido", "explica": "Cenas curtas e cortes ligeiros, estilo Reels."}], "tom": [{"valor": "didatico", "rotulo": "Didático", "explica": "Explica passo a passo, com exemplos."}, {"valor": "divertido", "rotulo": "Divertido", "explica": "Leve, com humor e comparações engraçadas."}, {"valor": "inspirador", "rotulo": "Inspirador", "explica": "Frases de impacto, emoção, chamada no fim."}, {"valor": "serio", "rotulo": "Sério", "explica": "Direto, sóbrio, como um telejornal."}], "letra": [{"valor": "moderna", "rotulo": "Moderna", "explica": "Segoe UI: limpa e atual."}, {"valor": "elegante", "rotulo": "Elegante", "explica": "Georgia, com serifa."}, {"valor": "tecnica", "rotulo": "Técnica", "explica": "Consolas, de terminal."}, {"valor": "condensada", "rotulo": "Condensada", "explica": "Bahnschrift: estreita e firme."}, {"valor": "manuscrita", "rotulo": "Manuscrita", "explica": "Segoe Print, como escrita à mão."}, {"valor": "impacto", "rotulo": "Impacto", "explica": "Títulos em Impact, bem grossos."}], "entrada": [{"valor": "surgir", "rotulo": "Surgir", "explica": "O texto aparece subindo de leve."}, {"valor": "deslizar", "rotulo": "Deslizar", "explica": "Entra pela esquerda."}, {"valor": "digitar", "rotulo": "Digitar", "explica": "Letra por letra, como digitado."}, {"valor": "palavra", "rotulo": "Palavra por palavra", "explica": "Uma palavra de cada vez."}, {"valor": "pop", "rotulo": "Pop", "explica": "Cresce com um pulinho."}], "transicao": [{"valor": "corte", "rotulo": "Corte seco", "explica": "Uma cena atrás da outra."}, {"valor": "dissolver", "rotulo": "Dissolver", "explica": "Uma cena se mistura na outra."}, {"valor": "deslizar", "rotulo": "Deslizar", "explica": "A cena nova empurra a velha para o lado."}, {"valor": "zoom", "rotulo": "Zoom", "explica": "A cena nova chega de perto."}, {"valor": "cortina", "rotulo": "Cortina", "explica": "A cena nova passa por cima, da esquerda para a direita."}], "movimento": [{"valor": "parado", "rotulo": "Parado", "explica": "As ilustrações ficam paradas."}, {"valor": "aproximar", "rotulo": "Aproximar", "explica": "Um zoom lento em cada cena."}, {"valor": "passear", "rotulo": "Passear", "explica": "A ilustração anda devagar de um lado para o outro."}], "narracao": [{"valor": "thalita", "rotulo": "Voz da Thalita", "explica": "A voz de sempre da Beatriz."}, {"valor": "siri", "rotulo": "Parecida com a Siri", "explica": "A voz calma e neutra."}, {"valor": "nenhuma", "rotulo": "Sem narração", "explica": "Só texto na tela (sem som)."}], "legendas": [{"valor": "embaixo", "rotulo": "Embaixo", "explica": "Legenda da narração no pé do vídeo."}, {"valor": "centro", "rotulo": "No meio", "explica": "Legenda grande no meio, estilo Reels."}, {"valor": "nenhuma", "rotulo": "Sem legendas", "explica": ""}], "tamanho_legenda": [{"valor": "pequena", "rotulo": "Pequena", "explica": ""}, {"valor": "media", "rotulo": "Média", "explica": ""}, {"valor": "grande", "rotulo": "Grande", "explica": ""}], "cores": [{"valor": "preto_dourado", "rotulo": "Preto e dourado", "explica": "Como o painel da Beatriz."}, {"valor": "noite", "rotulo": "Noite", "explica": "Azul-escuro com destaque azul-claro."}, {"valor": "branco", "rotulo": "Branco limpo", "explica": "Fundo claro, texto escuro, destaque vermelho."}, {"valor": "neon", "rotulo": "Neon", "explica": "Roxo-escuro com rosa neon."}, {"valor": "pastel", "rotulo": "Pastel", "explica": "Tons suaves de creme e rosa."}, {"valor": "natureza", "rotulo": "Natureza", "explica": "Verde-escuro com amarelo."}, {"valor": "livre", "rotulo": "Minhas cores", "explica": "Você escolhe o fundo, o texto e o destaque."}, {"valor": "guia", "rotulo": "Do guia", "explica": "As cores que a IA tirou dos seus exemplos."}]}, "opcoes_da_edicao": {"filtro": [{"valor": "", "rotulo": "Nenhum", "explica": "As cores do vídeo como estão."}, {"valor": "pb", "rotulo": "Preto e branco", "explica": ""}, {"valor": "sepia", "rotulo": "Sépia", "explica": ""}, {"valor": "vintage", "rotulo": "Antigo", "explica": ""}, {"valor": "cinema", "rotulo": "Cinema", "explica": ""}, {"valor": "quente", "rotulo": "Quente", "explica": ""}, {"valor": "frio", "rotulo": "Frio", "explica": ""}, {"valor": "dramatico", "rotulo": "Dramático", "explica": ""}], "enquadrar": [{"valor": "original", "rotulo": "Como está", "explica": "Não muda o formato do vídeo."}, {"valor": "preencher", "rotulo": "Preencher", "explica": "Corta as bordas para caber no formato do estilo."}, {"valor": "encaixar", "rotulo": "Encaixar", "explica": "O vídeo inteiro no formato do estilo, com o fundo desfocado."}]}, "paletas": {"preto_dourado": {"fundo": "#050505", "fundo2": "#1c1c1c", "texto": "#f2f2f2", "destaque": "#f5c04a"}, "noite": {"fundo": "#0b1030", "fundo2": "#2a3a6a", "texto": "#ffffff", "destaque": "#7fd3ff"}, "branco": {"fundo": "#fafafa", "fundo2": "#e6e6ec", "texto": "#141414", "destaque": "#e0452b"}, "neon": {"fundo": "#0a0014", "fundo2": "#26004d", "texto": "#ffffff", "destaque": "#ff2bd6"}, "pastel": {"fundo": "#fdf3e7", "fundo2": "#f6d6e3", "texto": "#3a2e3f", "destaque": "#7a5af5"}, "natureza": {"fundo": "#0f2a1d", "fundo2": "#2f5d3a", "texto": "#f4f1e8", "destaque": "#e8c547"}, "livre": {"fundo": "#101010", "fundo2": "#2b2b2b", "texto": "#ffffff", "destaque": "#f5c04a"}, "guia": {"fundo": "#050505", "fundo2": "#1c1c1c", "texto": "#f2f2f2", "destaque": "#f5c04a"}}, "letras": {"moderna": "'Segoe UI', sans-serif", "elegante": "Georgia, serif", "tecnica": "Consolas, monospace", "condensada": "Bahnschrift, 'Arial Narrow', sans-serif", "manuscrita": "'Segoe Print', cursive", "impacto": "Impact, sans-serif"}, "exemplos": [], "maximo_de_exemplos": 12};
const videosDemonstracao = (() => {
  const dados = JSON.parse(JSON.stringify(VD_DADOS_FIXOS));
  const resposta = (mensagem = "") => ({ ok: true, mensagem, dados: JSON.parse(JSON.stringify(dados)) });
  return {
    estilo_video: async () => JSON.parse(JSON.stringify(dados)),
    estilo_video_salvar: async (novo) => { const { guia_texto, ...resto } = novo; Object.assign(dados.estilo, resto, { atualizado: Date.now() / 1000 }); dados.estilo.guia.texto = guia_texto; return resposta(); },
    estilo_video_padrao: async () => resposta(),
    estilo_video_exemplos_adicionar: async () => resposta("No painel de verdade, abre a escolha de arquivos."),
    estilo_video_exemplo: async () => "",
    estilo_video_exemplo_apagar: async () => resposta(),
    estilo_video_guia: async () => ({ ok: false, mensagem: "No painel de verdade, o Claude olha os exemplos." }),
    estilo_video_novo: async (assunto) => ({ ok: true, mensagem: `No painel de verdade, o Claude faria o vídeo sobre ${assunto}.` }),
    estilo_video_refazer: async () => ({ ok: true, mensagem: "No painel de verdade, o último vídeo seria montado de novo." }),
  };
})();
})();
