/* Janela [ estudos ]: como saem os resumos, as fichas, as aulas e as peças (preferencias_estudo.py).
 *
 * Três abas: o jeito do resumo (opções com a prévia da folha ao lado, que muda na hora), as fotos de exemplo (o Claude
 * monta o guia de estilo) e as peças processuais (o jeito delas e uma peça nova).
 *
 * É um componente: o painel do PC (interface_web) e a tela Estudos do app do celular (interface_app, que o recebe em
 * /visual/estudos.js) usam o mesmo, com os mesmos ids. No painel ele fala com a ponte do pywebview (o `api` do app.js); o
 * app troca a ponte pela dele com `JanelaDeEstudos.usar(() => adaptador)`. Sem nenhuma das duas (aberto num navegador
 * comum), usa os dados de demonstração do fim deste arquivo.
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
let fonte = () => (typeof api !== "undefined" && api && api.estudos ? api : null);  // eslint-disable-line no-undef
const es = { dados: null, rascunho: null, salvo: "", aba: "jeito", arquivosDaPeca: [], miniaturas: {} };

const LINHAS_DO_JEITO = [
  ["ia", "quem escreve"], ["tamanho", "tamanho"], ["estilo", "estilo"], ["foco", "foco"], ["flashcards", "flashcards"],
  ["questoes", "questões"], ["formato", "formato"], ["cores", "cores"], ["letra", "letra"],
];

function apiEstudos() { return fonte() || estudosDemonstracao; }

async function abrirEstudos(abrir = true) {
  const fundo = $("#fundo-estudos");
  if (!fundo) return;
  if (!abrir && !fundo.hidden && mudouEstudos() && !confirm("Fechar sem salvar as escolhas de estudo?")) return;
  fundo.hidden = !abrir;
  $("#btn-estudos").classList.toggle("ativo", abrir);
  if (abrir) await carregarEstudos();
}

async function carregarEstudos() {
  try { preencherEstudos(await apiEstudos().estudos()); } catch (erro) { mensagemEstudos("Não consegui ler as escolhas de estudo.", true); }
  mostrarAbaEstudos(es.aba);
}

function preencherEstudos(dados) {
  es.dados = dados;
  const prefs = JSON.parse(JSON.stringify(dados.preferencias));
  es.rascunho = prefs;
  $("#es-instrucoes").value = prefs.instrucoes || "";
  $("#es-guia").value = (prefs.guia && prefs.guia.texto) || "";
  $("#es-usar-guia").checked = prefs.usar_guia !== false;
  $("#es-advogado").value = prefs.pecas.advogado || "";
  $("#es-oab").value = prefs.pecas.oab || "";
  $("#es-cidade").value = prefs.pecas.cidade || "";
  $("#es-pecas-instrucoes").value = prefs.pecas.instrucoes || "";
  const tipos = $("#es-peca-tipo");
  tipos.innerHTML = "";
  tipos.append(new Option("a peça cabível (ela escolhe)", ""));
  for (const tipo of dados.tipos_de_peca || []) tipos.append(new Option(tipo.rotulo, tipo.valor));
  es.salvo = JSON.stringify(rascunhoEstudos());
  desenharOpcoes();
  desenharFotos();
  aoMudarEstudos();
}

function rascunhoEstudos() {
  const r = es.rascunho;
  if (!r) return {};
  r.instrucoes = $("#es-instrucoes").value;
  r.usar_guia = $("#es-usar-guia").checked;
  Object.assign(r.pecas, { advogado: $("#es-advogado").value, oab: $("#es-oab").value, cidade: $("#es-cidade").value,
                           instrucoes: $("#es-pecas-instrucoes").value });
  const { guia, atualizado, ...resto } = r;
  return { ...resto, guia_texto: $("#es-guia").value };
}
function mudouEstudos() { return !!es.dados && JSON.stringify(rascunhoEstudos()) !== es.salvo; }

function aoMudarEstudos() {
  const mudou = mudouEstudos();
  $("#es-estado").textContent = mudou ? "● alterações não salvas" : es.dados && es.dados.preferencias.atualizado ? "suas escolhas · salvas" : "padrão";
  $("#es-estado").classList.toggle("mudou", mudou);
  $("#es-salvar").disabled = !mudou;
  $("#es-desfazer").disabled = !mudou;
  desenharFolha();
}

function mensagemEstudos(texto, erro = false) {
  const alvo = $("#es-mensagem");
  alvo.textContent = texto;
  alvo.classList.toggle("erro", erro);
}

function mostrarAbaEstudos(aba) {
  es.aba = aba;
  document.querySelectorAll(".aba-es").forEach((b) => b.classList.toggle("ativo", b.dataset.aba === aba));
  document.querySelectorAll(".es-aba").forEach((s) => { s.hidden = s.dataset.aba !== aba; });
  const janela = $("#janela-estudos");
  if (janela) janela.classList.toggle("sem-previa", aba === "pecas");
}

/* ------------------------------------------------------------------ opções */
function opcoesDe(chave) {
  const d = es.dados;
  const rotuloQuantidade = (n) => n === null ? "auto" : n === 0 ? (chave === "flashcards" ? "nenhum" : "nenhuma") : String(n);
  if (chave === "flashcards" || chave === "questoes") {
    const explica = chave === "flashcards" ? "auto = 12 (20 no resumo da matéria); vão também para o Anki."
                                           : "auto = 6 (10 no resumo da matéria); são o teste de estresse, com gabarito.";
    return d[chave].map((n) => ({ valor: n, rotulo: rotuloQuantidade(n), explica }));
  }
  return d.opcoes[chave];
}

function linhaDeOpcoes(alvo, chave, rotulo, objeto) {
  const linha = novo("div", "es-linha");
  linha.append(novo("span", "es-rotulo", rotulo));
  const grupo = novo("div", `es-opcoes${chave === "cores" ? " es-cores" : ""}`);
  const explica = novo("small", "es-explica");
  const opcoes = opcoesDe(chave);
  for (const opcao of opcoes) {
    const botao = novo("button", "es-opcao");
    botao.type = "button";
    botao.title = opcao.explica || "";
    if (chave === "cores") {
      const amostra = novo("span", "es-amostra");
      const cores = opcao.valor === "livre" ? paletaLivre() : es.dados.paletas[opcao.valor] || {};
      const a = novo("i"), b = novo("i");
      a.style.background = cores.marca; b.style.background = cores.ouro;
      amostra.append(a, b);
      if (opcao.valor === "livre") amostra.id = "es-amostra-livre";
      botao.append(amostra, novo("span", "", opcao.rotulo));
      if (opcao.valor === "guia" && !(es.dados.preferencias.guia || {}).texto) botao.disabled = true;
    } else {
      botao.textContent = opcao.rotulo;
    }
    const marcar = () => {
      grupo.querySelectorAll(".es-opcao").forEach((b) => b.classList.toggle("ativo", b === botao));
      explica.textContent = opcao.explica || "";
    };
    botao.onclick = () => { objeto[chave] = opcao.valor; marcar(); if (chave === "cores") desenharCoresLivres(); aoMudarEstudos(); };
    grupo.append(botao);
    if (objeto[chave] === opcao.valor) marcar();
  }
  linha.append(grupo);
  if (chave === "cores" && objeto === es.rascunho) {
    const livres = novo("div", "es-livres");
    livres.id = "es-livres";
    linha.append(livres);
  }
  if (chave === "foco") {
    const banca = novo("input", "es-banca");
    banca.placeholder = "banca (ex.: FGV, CESPE)";
    banca.value = objeto.banca || "";
    banca.setAttribute("aria-label", "Banca");
    banca.addEventListener("input", () => { objeto.banca = banca.value; aoMudarEstudos(); });
    grupo.append(banca);
  }
  linha.append(explica);
  alvo.append(linha);
}

function desenharOpcoes() {
  const r = es.rascunho;
  const grade = $("#es-opcoes");
  grade.innerHTML = "";
  for (const [chave, rotulo] of LINHAS_DO_JEITO) linhaDeOpcoes(grade, chave, rotulo, r);
  const partes = $("#es-partes");
  partes.innerHTML = "";
  const chip = (rotulo, explica, ligado, alternar) => {
    const botao = novo("button", `es-parte${ligado ? " ativo" : ""}`, rotulo);
    botao.type = "button";
    botao.title = explica;
    botao.onclick = () => { alternar(); botao.classList.toggle("ativo"); aoMudarEstudos(); };
    partes.append(botao);
  };
  for (const parte of es.dados.partes) {
    chip(parte.rotulo, parte.explica, r.partes[parte.chave] !== false, () => { r.partes[parte.chave] = r.partes[parte.chave] === false; });
  }
  chip("marca-texto", "As palavras que mudam o sentido (salvo, apenas, presume-se…) em amarelo.", r.marca_texto !== false,
       () => { r.marca_texto = r.marca_texto === false; });
  desenharCoresLivres();
  const pecas = $("#es-pecas");
  pecas.innerHTML = "";
  const opcoesDePeca = { modo: es.dados.modos_de_peca, formato: es.dados.opcoes.formato };
  for (const [chave, rotulo] of [["modo", "para quê"], ["formato", "formato"]]) {
    const antes = es.dados.opcoes[chave];
    es.dados.opcoes[chave] = opcoesDePeca[chave];
    linhaDeOpcoes(pecas, chave, rotulo, r.pecas);
    if (antes) es.dados.opcoes[chave] = antes; else delete es.dados.opcoes[chave];
  }
}

/* ------------------------------------------------------------------ minhas cores */
// O fundo de cada caixa sai da cor da borda (a mesma conta de preferencias_estudo.clarear).
function clarear(cor, quanto = 0.92) {
  const partes = [1, 3, 5].map((i) => parseInt(cor.slice(i, i + 2), 16));
  return "#" + partes.map((c) => Math.round(c + (255 - c) * quanto).toString(16).padStart(2, "0")).join("").toUpperCase();
}

function paletaLivre() {
  const base = JSON.parse(JSON.stringify(es.dados.paletas.padrao));
  const livres = (es.rascunho && es.rascunho.cores_livres) || {};
  base.marca = livres.marca || base.marca;
  base.ouro = livres.ouro || base.ouro;
  for (const [chave, borda] of Object.entries(livres.caixas || {})) base[chave] = [borda, clarear(borda)];
  return base;
}

function desenharCoresLivres() {
  const alvo = $("#es-livres");
  if (!alvo) return;
  const r = es.rascunho;
  alvo.innerHTML = "";
  alvo.hidden = r.cores !== "livre";
  if (alvo.hidden) return;
  r.cores_livres = r.cores_livres || { marca: "#2E1A47", ouro: "#C99A1E", caixas: {} };
  r.cores_livres.caixas = r.cores_livres.caixas || {};
  const atual = paletaLivre();
  const seletor = (rotulo, valor, aoMudar) => {
    const campo = novo("label", "es-cor");
    const entrada = novo("input");
    entrada.type = "color";
    entrada.value = valor.toLowerCase();
    entrada.addEventListener("input", () => {
      aoMudar(entrada.value.toUpperCase());
      const amostra = $("#es-amostra-livre");
      if (amostra) { const cores = paletaLivre(); amostra.children[0].style.background = cores.marca; amostra.children[1].style.background = cores.ouro; }
      aoMudarEstudos();
    });
    campo.append(entrada, novo("span", "", rotulo));
    return campo;
  };
  const principais = novo("div", "es-cores-linha");
  principais.append(seletor("título", atual.marca, (v) => { r.cores_livres.marca = v; }),
                    seletor("destaques", atual.ouro, (v) => { r.cores_livres.ouro = v; }));
  const caixas = novo("div", "es-cores-linha");
  for (const caixa of es.dados.caixas || []) {
    caixas.append(seletor(caixa.rotulo.toLowerCase(), atual[caixa.chave][0], (v) => { r.cores_livres.caixas[caixa.chave] = v; }));
  }
  const voltar = novo("button", "pequeno", "cores das caixas: padrão");
  voltar.type = "button";
  voltar.onclick = () => { r.cores_livres.caixas = {}; desenharCoresLivres(); aoMudarEstudos(); };
  caixas.append(voltar);
  alvo.append(principais, caixas);
}

/* ------------------------------------------------------------------ prévia da folha */
function desenharFolha() {
  const r = es.rascunho;
  if (!r) return;
  const cores = r.cores === "livre" ? paletaLivre() : es.dados.paletas[r.cores] || es.dados.paletas.padrao;
  const parte = (chave) => r.partes[chave] !== false;
  const folha = $("#es-folha");
  folha.innerHTML = "";
  folha.classList.toggle("grande", r.letra === "grande");
  const linhas = (alvo, n, curta = false) => {
    for (let i = 0; i < n; i++) alvo.append(novo("i", `ef-linha${curta || i === n - 1 ? " curta" : ""}${r.estilo === "esquematico" ? " com-marcador" : ""}`));
  };
  const caixa = (nome, rotulo, preencher) => {
    const [borda, fundo] = cores[nome];
    const el = novo("div", "ef-caixa");
    el.style.background = fundo; el.style.borderLeftColor = borda;
    const rot = novo("b", "", rotulo);
    rot.style.color = borda;
    el.append(rot);
    preencher(el);
    folha.append(el);
  };
  const topo = novo("div", "ef-topo");
  topo.style.background = cores.marca;
  const sub = novo("span", "", "Ficha tática de estudo · análise focada na prova");
  sub.style.color = cores.ouro;
  topo.append(novo("strong", "", "Responsabilidade civil do Estado"), sub);
  folha.append(topo);
  if (parte("banca")) caixa("banca", "COMO A BANCA COBRA", (el) => linhas(el, 2));
  if (parte("mapa")) {
    const mapa = novo("div", "ef-mapa");
    const cab = novo("span", "ef-mapa-cab", "MAPA DE INCIDÊNCIA");
    cab.style.color = cores.marca; cab.style.borderBottomColor = cores.marca;
    mapa.append(cab);
    for (const [titulo, peso, cor] of [["Risco administrativo", "ALTA", "#B91C1C"], ["Omissão estatal", "MÉDIA", "#B45309"], ["Ação de regresso", "BAIXA", "#64748B"]]) {
      const linha = novo("span", "ef-mapa-linha", titulo);
      const selo = novo("em", "", peso);
      selo.style.background = cor;
      linha.append(selo);
      mapa.append(linha);
    }
    folha.append(mapa);
  }
  const titulo = novo("div", "ef-h2");
  const numero = novo("span", "", "1");
  numero.style.background = cores.marca;
  titulo.append(numero, document.createTextNode(" Teoria do risco administrativo"));
  titulo.style.color = cores.marca; titulo.style.borderBottomColor = cores.ouro;
  folha.append(titulo);
  if (parte("banca")) {
    const cai = novo("p", "ef-cai", "Como cai: a banca troca “independentemente de culpa” por “mediante culpa”.");
    cai.style.color = cores.banca[0];
    folha.append(cai);
  }
  const explicacao = novo("div", "ef-texto");
  const tamanho = { enxuto: 2, normal: 4, completo: 7 }[r.tamanho] || 4;
  linhas(explicacao, r.estilo === "revisao" ? Math.min(2, tamanho) : r.estilo === "didatico" ? tamanho + 1 : tamanho);
  folha.append(explicacao);
  caixa("regra", "REGRA", (el) => el.append(novo("p", "ef-regra", "O Estado responde objetivamente pelos danos de seus agentes.")));
  caixa("excecoes", "EXCEÇÕES", (el) => linhas(el, r.tamanho === "enxuto" ? 2 : 3, true));
  if (parte("prazos")) caixa("prazos", "PRAZOS E EFEITOS", (el) => linhas(el, 2, true));
  if (parte("lei_seca")) {
    caixa("lei_seca", "LEI SECA", (el) => {
      const p = novo("p", "ef-lei");
      p.append(novo("b", "", "art. 37, § 6º, CF: "), document.createTextNode("responderão pelos danos… "));
      const palavra = novo("mark", "", "independentemente");
      if (r.marca_texto !== false) palavra.style.background = cores.destaque; else palavra.classList.add("sem");
      p.append(palavra, document.createTextNode(" de culpa"));
      el.append(p);
    });
  }
  if (parte("jurisprudencia")) caixa("jurisprudencia", "STF E STJ", (el) => linhas(el, 2, true));
  if (parte("pegadinhas")) caixa("pegadinha", "PEGADINHAS", (el) => linhas(el, 1, true));
  if (parte("exemplo")) caixa("exemplo", "EXEMPLO", (el) => linhas(el, r.estilo === "didatico" ? 3 : 2, true));
  const rodape = novo("div", "ef-rodape");
  const nFlash = r.flashcards === null ? "12" : String(r.flashcards);
  const nQuest = r.questoes === null ? "6" : String(r.questoes);
  rodape.append(novo("span", "", `${nFlash} flashcards · ${nQuest} questões${parte("glossario") ? " · glossário" : ""}`));
  for (const formato of r.formato === "ambos" ? ["PDF", "WORD"] : [r.formato === "docx" ? "WORD" : "PDF"]) {
    const selo = novo("em", "", formato);
    selo.style.background = cores.marca;
    rodape.append(selo);
  }
  folha.append(rodape);
  const nome = (chave, valor) => ((es.dados.opcoes[chave] || []).find((o) => o.valor === valor) || {}).rotulo || valor;
  $("#es-folha-legenda").textContent = `Como vai sair: ${nome("tamanho", r.tamanho).toLowerCase()}, ${nome("estilo", r.estilo).toLowerCase()}, `
    + `${nome("cores", r.cores).toLowerCase()}${r.usar_guia !== false && $("#es-guia").value.trim() ? ", com o guia das fotos" : ""}. `
    + "É uma prévia do visual: o texto de verdade é o da IA.";
}

/* ------------------------------------------------------------------ fotos */
async function desenharFotos() {
  const lista = $("#es-fotos");
  lista.innerHTML = "";
  const fotos = es.dados.fotos || [];
  $("#es-fotos-conta").textContent = `${fotos.length} de ${es.dados.maximo_de_fotos} fotos`;
  $("#es-foto-adicionar").disabled = fotos.length >= es.dados.maximo_de_fotos;
  $("#es-guia-montar").disabled = !fotos.length;
  if (!fotos.length) lista.append(novo("p", "es-vazio", "Nenhuma foto ainda. Adicione de 1 a 8 fotos de resumos de que você gosta."));
  for (const foto of fotos) {
    const cartao = novo("figure", "es-foto");
    const imagem = novo("img");
    imagem.alt = foto.nome;
    const apagar = novo("button", "es-foto-apagar", "×");
    apagar.title = "Apagar esta foto";
    apagar.onclick = async () => {
      if (!confirm(`Apagar a foto “${foto.nome}”?`)) return;
      await acaoEstudos(apiEstudos().estudos_foto_apagar(foto.id), "Foto apagada.", true);
    };
    cartao.append(imagem, novo("figcaption", "", foto.nome), apagar);
    lista.append(cartao);
    if (!es.miniaturas[foto.id]) {
      try { es.miniaturas[foto.id] = await apiEstudos().estudos_foto(foto.id); } catch (erro) { es.miniaturas[foto.id] = ""; }
    }
    if (es.miniaturas[foto.id]) imagem.src = es.miniaturas[foto.id];
  }
  const guia = es.dados.preferencias.guia || {};
  const coresDoGuia = $("#es-cores-guia");
  coresDoGuia.innerHTML = "";
  for (const [nome, cor] of Object.entries(guia.cores || {})) {
    const amostra = novo("i");
    amostra.style.background = cor;
    amostra.title = `${nome}: ${cor}`;
    coresDoGuia.append(amostra);
  }
  const quando = guia.atualizado ? new Date(guia.atualizado * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
  $("#es-guia-quem").textContent = guia.quem ? `montado pelo ${guia.quem} com ${guia.fotos} foto(s) · ${quando}` : "";
}

/* ------------------------------------------------------------------ ações */
async function acaoEstudos(promessa, sucesso, manterRascunho = false) {
  const rascunho = manterRascunho && mudouEstudos() ? rascunhoEstudos() : null;
  try {
    const resposta = await promessa;
    if (!resposta.ok) { mensagemEstudos(resposta.mensagem || "Não deu certo.", true); return resposta; }
    if (resposta.dados) {
      preencherEstudos(resposta.dados);
      if (rascunho) {  // apagar ou adicionar uma foto não desfaz as escolhas ainda não salvas
        const { guia_texto, ...resto } = rascunho;
        Object.assign(es.rascunho, resto);
        $("#es-guia").value = guia_texto;
        desenharOpcoes();
        aoMudarEstudos();
      }
    }
    mensagemEstudos(resposta.mensagem || sucesso);
    return resposta;
  } catch (erro) {
    mensagemEstudos(`Não deu certo: ${erro}`, true);
    return { ok: false };
  }
}

async function montarGuia() {
  const botao = $("#es-guia-montar");
  if (mudouEstudos() && !confirm("Montar o guia agora descarta as alterações não salvas desta janela. Continuar?")) return;
  botao.disabled = true;
  botao.textContent = "o Claude está olhando as fotos…";
  mensagemEstudos("Leva de 20 s a 1 min.");
  try {
    const resposta = await acaoEstudos(apiEstudos().estudos_guia(), "");
    if (resposta.ok && resposta.dados) {
      const guia = resposta.dados.preferencias.guia;
      mensagemEstudos(`Guia montado pelo ${guia.quem} a partir de ${guia.fotos} foto(s). Confira e ajuste o texto se quiser.`);
    }
  } finally {
    botao.textContent = "montar o guia com o Claude";
    botao.disabled = !(es.dados.fotos || []).length;
  }
}

async function redigirPeca() {
  const fatos = $("#es-peca-fatos").value.trim();
  if (!fatos && !es.arquivosDaPeca.length) { mensagemEstudos("Escreva os fatos do caso ou adicione os arquivos dele.", true); return; }
  const resposta = await apiEstudos().estudos_peca(($("#es-peca-tipo").value || ""), fatos, es.arquivosDaPeca);
  mensagemEstudos(resposta.mensagem, !resposta.ok);
  if (resposta.ok) { $("#es-peca-fatos").value = ""; es.arquivosDaPeca = []; $("#es-peca-arquivos-nomes").textContent = ""; }
}

function ligarEstudos() {
  if (!$("#es-opcoes")) return;  // esta página não tem a janela
  if ($("#fundo-estudos")) {
    $("#btn-estudos").onclick = () => abrirEstudos($("#fundo-estudos").hidden);
    $("#fundo-estudos").addEventListener("click", (e) => { if (e.target.id === "fundo-estudos") abrirEstudos(false); });
    $("#fundo-estudos").addEventListener("keydown", (e) => {
      if (e.key === "s" && e.ctrlKey) { e.preventDefault(); if (mudouEstudos()) $("#es-salvar").click(); }
    });
  }
  document.querySelectorAll(".aba-es").forEach((b) => { b.onclick = () => mostrarAbaEstudos(b.dataset.aba); });
  for (const id of ["#es-instrucoes", "#es-guia", "#es-advogado", "#es-oab", "#es-cidade", "#es-pecas-instrucoes"]) {
    $(id).addEventListener("input", aoMudarEstudos);
  }
  $("#es-usar-guia").addEventListener("change", aoMudarEstudos);
  $("#es-salvar").onclick = () => acaoEstudos(apiEstudos().estudos_salvar(rascunhoEstudos()), "Salvo. Vale para os próximos pedidos.");
  $("#es-desfazer").onclick = () => { preencherEstudos(es.dados); mensagemEstudos("Voltei ao que está salvo."); };
  $("#es-padrao").onclick = () => {
    if (confirm("Voltar as escolhas ao padrão? As fotos e o guia continuam.")) {
      acaoEstudos(apiEstudos().estudos_padrao(), "Pronto: o jeito padrão voltou.");
    }
  };
  $("#es-foto-adicionar").onclick = () => acaoEstudos(apiEstudos().estudos_fotos_adicionar(), "", true);
  $("#es-guia-montar").onclick = montarGuia;
  $("#es-peca-arquivos").onclick = async () => {
    const caminhos = await apiEstudos().estudos_peca_arquivos();
    if (!caminhos || !caminhos.length) return;
    es.arquivosDaPeca = caminhos;
    $("#es-peca-arquivos-nomes").textContent = caminhos.map((c) => c.split(/[\\/]/).pop()).join(", ");
  };
  $("#es-peca-redigir").onclick = redigirPeca;
}
window.abrirEstudos = abrirEstudos;  // o app.js do painel fecha a janela com o Esc e o ×
window.JanelaDeEstudos = { usar(novaFonte) { fonte = novaFonte; }, carregar: carregarEstudos, mudou: mudouEstudos };
if (document.readyState === "loading") addEventListener("DOMContentLoaded", ligarEstudos); else ligarEstudos();

/* ------------------------------------------------------------------ demonstração */
// Aberta num navegador comum (sem a Beatriz): as opções de verdade (preferencias_estudo.para_a_janela), gravadas só na memória.
const ESTUDOS_DEMONSTRACAO = {
  preferencias: { ia: "auto", tamanho: "normal", estilo: "tatico", foco: "oab", banca: "", flashcards: null, questoes: null,
    partes: { banca: true, mapa: true, lei_seca: true, jurisprudencia: true, pegadinhas: true, exemplo: true, prazos: true,
              glossario: true, como_estudar: true },
    formato: "pdf", cores: "padrao", letra: "normal", marca_texto: true, instrucoes: "", usar_guia: true,
    cores_livres: { marca: "#2E1A47", ouro: "#C99A1E", caixas: {} },
    guia: { texto: "", cores: {}, quem: "", fotos: 0, atualizado: null },
    pecas: { modo: "oab", advogado: "", oab: "", cidade: "", formato: "ambos", instrucoes: "" }, atualizado: null },
  fotos: [], maximo_de_fotos: 8,
  tipos_de_peca: [{ valor: "peticao_inicial", rotulo: "Petição inicial (procedimento comum)" }, { valor: "contestacao", rotulo: "Contestação" },
                  { valor: "agravo_de_instrumento", rotulo: "Agravo de instrumento" }, { valor: "apelacao", rotulo: "Apelação" },
                  { valor: "embargos_de_declaracao", rotulo: "Embargos de declaração" }, { valor: "habeas_corpus", rotulo: "Habeas corpus" }],
};
const estudosDemonstracao = {
  _dados() { return { ...ESTUDOS_DADOS_FIXOS, ...JSON.parse(JSON.stringify(ESTUDOS_DEMONSTRACAO)) }; },
  async estudos() { return this._dados(); },
  async estudos_salvar(dados) {
    const { guia_texto, ...resto } = dados;
    Object.assign(ESTUDOS_DEMONSTRACAO.preferencias, resto, { atualizado: Date.now() / 1000 });
    ESTUDOS_DEMONSTRACAO.preferencias.guia.texto = guia_texto;
    return { ok: true, mensagem: "", dados: this._dados() };
  },
  async estudos_padrao() {
    const { guia, pecas } = ESTUDOS_DEMONSTRACAO.preferencias;
    Object.assign(ESTUDOS_DEMONSTRACAO.preferencias, { ia: "auto", tamanho: "normal", estilo: "tatico", foco: "oab", banca: "",
      flashcards: null, questoes: null, formato: "pdf", cores: "padrao", letra: "normal", marca_texto: true, instrucoes: "",
      usar_guia: true, guia, pecas, atualizado: null });
    for (const chave of Object.keys(ESTUDOS_DEMONSTRACAO.preferencias.partes)) ESTUDOS_DEMONSTRACAO.preferencias.partes[chave] = true;
    return { ok: true, mensagem: "", dados: this._dados() };
  },
  async estudos_fotos_adicionar() {
    const n = ESTUDOS_DEMONSTRACAO.fotos.length + 1;
    ESTUDOS_DEMONSTRACAO.fotos.push({ id: `demo_${n}.png`, nome: `resumo do caderno ${n}.jpg`, tamanho: 120000, quando: Date.now() / 1000 });
    return { ok: true, mensagem: "(demonstração) No painel de verdade, abre a escolha de fotos.", dados: this._dados() };
  },
  async estudos_foto(id) {
    const cor = ["#1d4ed8", "#be185d", "#15803d", "#c2410c"][(parseInt(id.replace(/\D/g, ""), 10) || 0) % 4];
    return "data:image/svg+xml," + encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 160"><rect width="120" height="160" fill="#fbfaf6"/>`
      + `<rect x="10" y="10" width="100" height="14" fill="${cor}"/><rect x="10" y="34" width="80" height="5" fill="#bbb"/>`
      + `<rect x="10" y="44" width="92" height="5" fill="#bbb"/><rect x="10" y="60" width="100" height="34" fill="${cor}" opacity=".15"/>`
      + `<rect x="10" y="102" width="70" height="5" fill="#bbb"/><rect x="10" y="112" width="96" height="5" fill="#bbb"/></svg>`);
  },
  async estudos_foto_apagar(id) {
    ESTUDOS_DEMONSTRACAO.fotos = ESTUDOS_DEMONSTRACAO.fotos.filter((f) => f.id !== id);
    return { ok: true, mensagem: "", dados: this._dados() };
  },
  async estudos_guia() {
    await new Promise((pronto) => setTimeout(pronto, 1600));
    Object.assign(ESTUDOS_DEMONSTRACAO.preferencias.guia, {
      texto: "Esquemas curtos, com uma caixa colorida por tipo de informação.\nEstrutura:\n- Título do tópico numerado, com a regra logo embaixo\n"
        + "- Exceções em lista, cada uma com o artigo\nVisual:\n- Caixas com borda à esquerda\n- Palavras-chave em negrito\n"
        + "Texto:\n- Frases de uma linha, sem parágrafos longos",
      cores: { principal: "#1d3a8a", destaque: "#e11d48" }, quem: "Claude", fotos: ESTUDOS_DEMONSTRACAO.fotos.length, atualizado: Date.now() / 1000 });
    ESTUDOS_DADOS_FIXOS.paletas.guia = { ...ESTUDOS_DADOS_FIXOS.paletas.padrao, marca: "#1d3a8a", ouro: "#e11d48" };
    return { ok: true, mensagem: "", dados: this._dados() };
  },
  async estudos_peca_arquivos() { return ["C:\\Users\\voce\\Downloads\\caso_kamila.jpg"]; },
  async estudos_peca(tipo, fatos) {
    return { ok: true, mensagem: `(demonstração) Começaria a ${tipo ? "peça escolhida" : "peça cabível"} com os fatos (${fatos.length} letras).` };
  },
};
// Gerado de preferencias_estudo.para_a_janela() (as opções, as partes e as cores de cada tema).
const ESTUDOS_DADOS_FIXOS = {
  opcoes: {
    ia: [{"valor": "auto", "rotulo": "Automático", "explica": "O Claude escreve; sem ele, o Gemini (só quando não há arquivos seus) e por último o PC."}, {"valor": "claude", "rotulo": "Claude", "explica": "O Claude primeiro, sempre que puder."}, {"valor": "gemini", "rotulo": "Gemini", "explica": "O Gemini primeiro. Com arquivos seus no material, eles continuam indo só para o Claude."}, {"valor": "local", "rotulo": "Só no PC", "explica": "Nada sai do computador; o material fica mais simples."}],
    tamanho: [{"valor": "enxuto", "rotulo": "Enxuto", "explica": "Menos tópicos e explicação curta: para revisar."}, {"valor": "normal", "rotulo": "Normal", "explica": "O de sempre."}, {"valor": "completo", "rotulo": "Completo", "explica": "Mais tópicos e explicações longas: para aprender do zero."}],
    estilo: [{"valor": "tatico", "rotulo": "Ficha de prova", "explica": "Como cai, regra numa linha, exceções, lei seca, pegadinhas."}, {"valor": "didatico", "rotulo": "Didático", "explica": "Explica do zero, com mais exemplos e comparações do dia a dia."}, {"valor": "esquematico", "rotulo": "Esquemático", "explica": "Frases curtas, tópicos e tabelas; pouco texto corrido."}, {"valor": "revisao", "rotulo": "Revisão rápida", "explica": "Só o essencial: regra, exceções, prazos e palavras-chave."}],
    foco: [{"valor": "oab", "rotulo": "OAB", "explica": "Questões no estilo da FGV, 1ª e 2ª fase."}, {"valor": "concurso", "rotulo": "Concurso", "explica": "Estilo das bancas de concurso (diga qual em 'banca')."}, {"valor": "faculdade", "rotulo": "Faculdade", "explica": "Prova da faculdade: conceitos, doutrina e casos."}, {"valor": "geral", "rotulo": "Geral", "explica": "Sem uma prova em vista: entender o assunto."}],
    formato: [{"valor": "pdf", "rotulo": "PDF", "explica": "Pronto para ler e imprimir."}, {"valor": "docx", "rotulo": "Word", "explica": "Para editar depois."}, {"valor": "ambos", "rotulo": "PDF e Word", "explica": "Os dois arquivos."}],
    cores: [{"valor": "padrao", "rotulo": "Roxo e dourado", "explica": "O padrão da Beatriz."}, {"valor": "preto_dourado", "rotulo": "Preto e dourado", "explica": "Como o painel."}, {"valor": "azul", "rotulo": "Azul-marinho", "explica": "Sóbrio."}, {"valor": "verde", "rotulo": "Verde", "explica": "Tons de verde."}, {"valor": "vinho", "rotulo": "Vinho", "explica": "Vinho e âmbar."}, {"valor": "cinza", "rotulo": "Econômico", "explica": "Cinza, para imprimir gastando pouca tinta."}, {"valor": "livre", "rotulo": "Minhas cores", "explica": "Você escolhe a cor do título, a dos destaques e a de cada caixa."}, {"valor": "guia", "rotulo": "Do guia das fotos", "explica": "As cores que a IA tirou das suas fotos de exemplo."}],
    letra: [{"valor": "normal", "rotulo": "Normal", "explica": ""}, {"valor": "grande", "rotulo": "Grande", "explica": "Mais fácil de ler e imprimir."}],
  },
  partes: [{"chave": "banca", "rotulo": "Como a banca cobra", "explica": "A análise de como o assunto cai e o \"como cai\" de cada tópico."}, {"chave": "mapa", "rotulo": "Mapa de incidência", "explica": "A tabela dos tópicos com o peso de cada um."}, {"chave": "lei_seca", "rotulo": "Lei seca", "explica": "Os artigos com a palavra-chave e o texto oficial conferido."}, {"chave": "jurisprudencia", "rotulo": "STF e STJ", "explica": "Súmulas e teses, conferidas no vade-mécum."}, {"chave": "pegadinhas", "rotulo": "Pegadinhas", "explica": "As confusões que a banca explora."}, {"chave": "exemplo", "rotulo": "Exemplo", "explica": "Um caso concreto por tópico."}, {"chave": "prazos", "rotulo": "Quadro de prazos", "explica": "Prazos e efeitos de cada tópico e o quadro geral."}, {"chave": "glossario", "rotulo": "Glossário", "explica": "Os termos técnicos."}, {"chave": "como_estudar", "rotulo": "Como estudar", "explica": "O passo a passo de estudo no fim."}],
  caixas: [{"chave": "banca", "rotulo": "Como cai"}, {"chave": "natureza", "rotulo": "Requisitos"}, {"chave": "regra", "rotulo": "Regra"}, {"chave": "excecoes", "rotulo": "Exceções"}, {"chave": "prazos", "rotulo": "Prazos"}, {"chave": "lei_seca", "rotulo": "Lei seca"}, {"chave": "jurisprudencia", "rotulo": "STF/STJ"}, {"chave": "pegadinha", "rotulo": "Pegadinhas"}, {"chave": "exemplo", "rotulo": "Exemplo"}, {"chave": "estresse", "rotulo": "Teste de estresse"}],
  flashcards: [null, 0, 5, 10, 12, 15, 20, 30, 40, 60],
  questoes: [null, 0, 3, 5, 6, 10, 15, 20, 30],
  modos_de_peca: [{"valor": "oab", "rotulo": "Prova da OAB", "explica": "Sem identificar ninguém: \"Nome…\", \"Advogado…, OAB…\", como pede o exame."}, {"valor": "real", "rotulo": "Caso real", "explica": "Com os dados que você der; o que faltar fica entre colchetes para completar."}],
  paletas: {
    padrao: {"marca": "#2E1A47", "ouro": "#C99A1E", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
    preto_dourado: {"marca": "#111111", "ouro": "#C99A1E", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
    azul: {"marca": "#14305A", "ouro": "#2563EB", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
    verde: {"marca": "#14532D", "ouro": "#65A30D", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
    vinho: {"marca": "#5B1A2E", "ouro": "#D97706", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
    cinza: {"marca": "#222222", "ouro": "#666666", "texto": "#1F2937", "suave": "#64748B", "banca": ["#444444", "#F3F3F3"], "natureza": ["#444444", "#F3F3F3"], "regra": ["#444444", "#F3F3F3"], "excecoes": ["#444444", "#F3F3F3"], "prazos": ["#444444", "#F3F3F3"], "lei_seca": ["#444444", "#F3F3F3"], "jurisprudencia": ["#444444", "#F3F3F3"], "pegadinha": ["#444444", "#F3F3F3"], "exemplo": ["#444444", "#F3F3F3"], "estresse": ["#444444", "#F3F3F3"], "destaque": "#D9D9D9"},
    livre: {"marca": "#2E1A47", "ouro": "#C99A1E", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
    guia: {"marca": "#2E1A47", "ouro": "#C99A1E", "texto": "#1F2937", "suave": "#64748B", "banca": ["#BE185D", "#FDF0F6"], "natureza": ["#0F766E", "#ECFDF9"], "regra": ["#1D4ED8", "#EFF4FF"], "excecoes": ["#C2410C", "#FFF4ED"], "prazos": ["#7C3AED", "#F5F0FF"], "lei_seca": ["#334155", "#F1F5F9"], "jurisprudencia": ["#15803D", "#EFFBF3"], "pegadinha": ["#A16207", "#FEFCE8"], "exemplo": ["#475569", "#F8FAFC"], "estresse": ["#B91C1C", "#FEF2F2"], "destaque": "#FDE047"},
  },
};
})();
