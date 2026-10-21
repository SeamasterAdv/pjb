"""Testes do recebimento de arquivos pelo WhatsApp: o leitor de mensagens (anexos, legendas, arquivos próprios) e, num Chromium
de verdade, a extração dos anexos da conversa e o download de documentos e fotos.

A conversa de teste imita a forma do WhatsApp Web (out/2026) com HTML próprio: valida a nossa lógica e o fluxo de download,
não prova que os seletores acertam o WhatsApp Web de verdade, que muda com o tempo (por isso o log guarda o esqueleto da
mensagem quando um download falha)."""
import os

import pytest

import canal_whatsapp as cw
from canal_whatsapp import LeitorDeMensagens

ABERTURA = 1000.0


def msg(identificador, texto=None, hora="10:41", anexo=None):
    return {"id": f"true_5511999990000@c.us_{identificador}", "texto": texto, "anexo": anexo,
            "quando": f"[{hora}, 19/10/2026] Felipe: " if texto else None}


def leitor_pronto(*historico):
    leitor = LeitorDeMensagens()
    espera = cw.AQUECIMENTO_MINIMO if historico else cw.AQUECIMENTO_SEM_MENSAGENS  # conversa vazia espera mais
    for segundo in range(espera + cw.LEITURAS_ESTAVEIS + 1):
        leitor.novas_entradas(list(historico), agora=ABERTURA + segundo)
    assert leitor.pronto
    return leitor


DOC = {"tipo": "documento", "nome": "contrato.pdf"}


# ------------------------------------------------------------------ leitor
def test_documento_novo_vira_entrada_com_legenda():
    leitor = leitor_pronto(msg("H1", "lista de compras", "09:00"))
    entradas = leitor.novas_entradas([msg("H1", "lista de compras", "09:00"), msg("D1", "vira fonte", anexo=DOC)])
    assert entradas == [{"id": "true_5511999990000@c.us_D1", "texto": "vira fonte", "anexo": DOC}]


def test_documento_sem_legenda_e_com_o_nome_do_arquivo_no_lugar_dela():
    leitor = leitor_pronto()
    entradas = leitor.novas_entradas([msg("D1", None, anexo=DOC), msg("D2", "contrato.pdf", anexo=DOC)])
    assert [e["texto"] for e in entradas] == [None, None]  # o nome do arquivo não é um pedido


def test_historico_com_arquivo_nao_e_baixado_de_novo():
    historico = [msg("D0", None, "08:00", anexo=DOC)]
    leitor = leitor_pronto(*historico)
    assert leitor.novas_entradas(historico) == []  # o que já estava na conversa ao abrir não vira arquivo novo


def test_arquivo_que_ela_mesma_mandou_nao_volta_como_seu():
    leitor = leitor_pronto()
    leitor.ignorar("true_5511999990000@c.us_PROPRIO")
    assert leitor.novas_entradas([msg("PROPRIO", None, anexo=DOC)]) == []
    assert len(leitor.novas_entradas([msg("SEU", None, anexo=DOC)])) == 1


def test_resposta_da_beatriz_com_anexo_e_ignorada_pelo_prefixo():
    leitor = leitor_pronto()
    assert leitor.novas_entradas([msg("R1", f"{cw.PREFIXO_RESPOSTA}: aqui está", anexo=DOC)]) == []


def test_novas_so_devolve_comandos_de_texto():
    leitor = leitor_pronto()
    mensagens = [msg("T1", "que horas são?"), msg("D1", "imprime", anexo=DOC), msg("T2", "e o clima?")]
    assert leitor.novas(mensagens) == ["que horas são?", "e o clima?"]  # o arquivo (e a legenda dele) vai por novas_entradas


def test_ordem_da_conversa_e_mantida():
    leitor = leitor_pronto()
    entradas = leitor.novas_entradas([msg("T1", "olha o contrato"), msg("D1", None, anexo=DOC), msg("T2", "vira fonte")])
    assert [(e["texto"], e["anexo"] is not None) for e in entradas] == [("olha o contrato", False), (None, True), ("vira fonte", False)]


def test_enviar_arquivo_marca_a_mensagem_como_propria(monkeypatch, tmp_path):
    leitor = leitor_pronto()
    arquivo = tmp_path / "resultado.pdf"
    arquivo.write_bytes(b"%PDF")
    monkeypatch.setattr(cw, "_enviar_arquivo", lambda pagina, caminho: "true_5511999990000@c.us_SAIDA1")
    monkeypatch.setattr(cw, "_enviar_texto", lambda pagina, texto: None)
    cw._enviar(object(), leitor, {"texto": "segue", "arquivos": [str(arquivo)]})
    assert leitor.novas_entradas([msg("SAIDA1", None, anexo=DOC)]) == []


# ------------------------------------------------------------------ navegador de verdade
HTML = """
<style>[data-icon] { display: inline-block; min-width: 14px; min-height: 14px; background: #ccc; }</style>
<div id="main">
  <div data-id="true_5511999990000@c.us_TEXTO1"><div class="copyable-text" data-pre-plain-text="[10:41, 19/10/2026] Felipe: ">
    <span class="selectable-text">que horas são?</span></div></div>
  <div data-id="true_5511999990000@c.us_DOC1">
    <div role="button" id="doc1" title="Baixar &quot;contrato.pdf&quot;"><span data-icon="document-PDF-icon"></span>
      <span dir="auto" title="contrato.pdf">contrato.pdf</span></div>
    <div class="copyable-text" data-pre-plain-text="[10:42, 19/10/2026] Felipe: "><span class="selectable-text">vira fonte</span></div>
  </div>
  <div data-id="true_5511999990000@c.us_DOC2">
    <div role="button"><span data-icon="document-DOC-icon"></span><span dir="auto">anotacoes_da_reuniao.docx</span></div>
    <span data-icon="audio-download" id="seta2"></span>
  </div>
  <div data-id="true_5511999990000@c.us_DOC3" id="msg3"><span data-icon="document-PDF-icon"></span><span dir="auto">so_pelo_menu.pdf</span>
    <div role="button" aria-label="Encaminhar mídia"></div>
    <span data-icon="down-context" id="chevron3" style="display:none"></span></div>
  <div data-id="true_5511999990000@c.us_DOC4"><div role="button" id="doc4"><span data-icon="document-PDF-icon"></span><span dir="auto">no_visualizador.pdf</span></div></div>
  <div data-id="true_5511999990000@c.us_FOTO1"><img id="foto" src="">
    <div class="copyable-text" data-pre-plain-text="[10:43, 19/10/2026] Felipe: "><span class="selectable-text">escaneia isso</span></div></div>
  <div data-id="true_5511999990000@c.us_AUDIO1"><span data-icon="audio-play"></span></div>
  <div data-id="true_5511999990000@c.us_VIDEO1"><video></video></div>
  <div data-id="true_5511999990000@c.us_RESP1"><div data-id="true_5511999990000@c.us_CITADA"><span data-icon="document-PDF-icon"></span></div>
    <div class="copyable-text" data-pre-plain-text="[10:44, 19/10/2026] Felipe: "><span class="selectable-text">respondendo a uma mensagem</span></div></div>
  <div data-id="true_5511999990000@c.us_RESP2"><div data-testid="quoted-message"><span data-icon="document-PDF-icon"></span><img src="blob:x"></div>
    <div class="copyable-text" data-pre-plain-text="[10:45, 19/10/2026] Felipe: "><span class="selectable-text">respondendo com citação</span></div></div>
</div>
<script>
  function baixar(nome, tipo, conteudo) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([conteudo], {type: tipo}));
    a.download = nome; document.body.appendChild(a); a.click(); a.remove();
  }
  document.getElementById('doc1').onclick = () => baixar('contrato.pdf', 'application/pdf', '%PDF-1.4 teste');
  document.getElementById('seta2').onclick = () => baixar('anotacoes_da_reuniao.docx', 'application/octet-stream', 'conteudo docx');
  const msg3 = document.getElementById('msg3');
  msg3.onmouseenter = () => { document.getElementById('chevron3').style.display = 'inline-block'; };
  document.getElementById('chevron3').onclick = () => {
    const menu = document.createElement('div'); menu.id = 'menu3';
    const item = document.createElement('div'); item.textContent = 'Baixar'; item.style.padding = '8px';
    item.onclick = () => { baixar('so_pelo_menu.pdf', 'application/pdf', '%PDF menu'); menu.remove(); };
    menu.appendChild(item); document.body.appendChild(menu);
  };
  document.getElementById('doc4').onclick = () => {
    const v = document.createElement('div'); v.id = 'visualizador';
    const b = document.createElement('button'); b.setAttribute('aria-label', 'Baixar'); b.textContent = 'x';
    b.onclick = () => baixar('no_visualizador.pdf', 'application/pdf', '%PDF viewer');
    v.appendChild(b); document.body.appendChild(v);
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('#visualizador,#menu3').forEach(x => x.remove()); });
  const tela = document.createElement('canvas'); tela.width = 40; tela.height = 30;
  tela.getContext('2d').fillRect(0, 0, 40, 30);
  tela.toBlob(b => { document.getElementById('foto').src = URL.createObjectURL(b); document.title = 'pronto'; }, 'image/png');
</script>
"""


@pytest.fixture(scope="module")
def pagina():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as playwright:
        try:
            navegador = playwright.chromium.launch(headless=True)
        except Exception as exc:  # o Chromium do Playwright não está instalado
            pytest.skip(f"Chromium do Playwright indisponível: {exc}")
        contexto = navegador.new_context(accept_downloads=True)
        pagina = contexto.new_page()
        pagina.set_content(HTML)
        pagina.wait_for_function("document.title === 'pronto'")
        yield pagina
        navegador.close()


def test_a_extracao_acha_documentos_fotos_audio_e_video(pagina):
    lidas = {m["id"].split("_")[-1]: m for m in pagina.evaluate(cw.JS_LER_MENSAGENS)}
    assert set(lidas) == {"TEXTO1", "DOC1", "DOC2", "DOC3", "DOC4", "FOTO1", "AUDIO1", "VIDEO1", "RESP1", "RESP2"}  # a citada dentro de outra não conta
    assert lidas["TEXTO1"]["anexo"] is None and lidas["TEXTO1"]["texto"] == "que horas são?"
    assert lidas["DOC1"]["anexo"] == {"tipo": "documento", "nome": "contrato.pdf"} and lidas["DOC1"]["texto"] == "vira fonte"
    assert lidas["DOC2"]["anexo"] == {"tipo": "documento", "nome": "anotacoes_da_reuniao.docx"} and lidas["DOC2"]["texto"] is None
    assert lidas["FOTO1"]["anexo"]["tipo"] == "imagem" and lidas["FOTO1"]["texto"] == "escaneia isso"
    assert lidas["AUDIO1"]["anexo"]["tipo"] == "audio" and lidas["VIDEO1"]["anexo"]["tipo"] == "video"
    assert lidas["RESP1"]["anexo"] is None and lidas["RESP2"]["anexo"] is None  # o documento ou a foto citados não são anexo da resposta


def test_baixa_o_documento_clicando_nele(pagina, tmp_path):
    caminho = cw._baixar_anexo(pagina, "true_5511999990000@c.us_DOC1", {"tipo": "documento", "nome": "contrato.pdf"}, str(tmp_path), 50)
    assert os.path.basename(caminho) == "contrato.pdf" and open(caminho, "rb").read() == b"%PDF-1.4 teste"
    assert os.path.dirname(os.path.dirname(caminho)) == str(tmp_path)  # dentro de uma pasta do dia


def test_se_o_clique_no_documento_nao_baixa_tenta_a_seta(pagina, tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "TEMPO_DO_PRIMEIRO_CLIQUE", 1200)
    monkeypatch.setattr(cw, "TEMPO_DO_DOWNLOAD", 8000)
    caminho = cw._baixar_anexo(pagina, "true_5511999990000@c.us_DOC2", {"tipo": "documento", "nome": ""}, str(tmp_path), 50)
    assert os.path.basename(caminho) == "anotacoes_da_reuniao.docx" and open(caminho, "rb").read() == b"conteudo docx"


def test_baixa_pelo_menu_da_mensagem_quando_nenhum_botao_baixa(pagina, tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "TEMPO_DO_PRIMEIRO_CLIQUE", 800)
    monkeypatch.setattr(cw, "TEMPO_DO_DOWNLOAD", 8000)
    caminho = cw._baixar_anexo(pagina, "true_5511999990000@c.us_DOC3", {"tipo": "documento", "nome": ""}, str(tmp_path), 50)
    assert os.path.basename(caminho) == "so_pelo_menu.pdf" and open(caminho, "rb").read() == b"%PDF menu"


def test_baixa_pelo_botao_do_visualizador_que_o_clique_abre(pagina, tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "TEMPO_DO_PRIMEIRO_CLIQUE", 800)
    monkeypatch.setattr(cw, "TEMPO_DO_DOWNLOAD", 8000)
    caminho = cw._baixar_anexo(pagina, "true_5511999990000@c.us_DOC4", {"tipo": "documento", "nome": ""}, str(tmp_path), 50)
    assert os.path.basename(caminho) == "no_visualizador.pdf" and open(caminho, "rb").read() == b"%PDF viewer"


def test_baixa_a_foto_da_conversa(pagina, tmp_path):
    from PIL import Image

    caminho = cw._baixar_anexo(pagina, "true_5511999990000@c.us_FOTO1", {"tipo": "imagem", "nome": ""}, str(tmp_path), 50)
    assert caminho.endswith(".png") and Image.open(caminho).size == (40, 30)


def test_mensagem_inexistente_da_erro_claro(pagina, tmp_path):
    with pytest.raises(RuntimeError, match="não achei a foto"):
        cw._baixar_anexo(pagina, "true_5511999990000@c.us_NAO_EXISTE", {"tipo": "imagem", "nome": ""}, str(tmp_path), 50)
    with pytest.raises(RuntimeError, match="não começou o download"):
        cw._baixar_anexo(pagina, "true_5511999990000@c.us_AUDIO1", {"tipo": "documento", "nome": ""}, str(tmp_path), 50)


def test_programa_e_arquivo_grande_demais_sao_recusados_e_apagados(pagina, tmp_path):
    pagina.evaluate("""() => { document.getElementById('doc1').onclick = () => {
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['MZ'], {type: 'application/octet-stream'}));
        a.download = 'instalador.exe'; document.body.appendChild(a); a.click(); a.remove(); }; }""")
    with pytest.raises(cw.AnexoRecusado, match="exe"):
        cw._baixar_anexo(pagina, "true_5511999990000@c.us_DOC1", {"tipo": "documento", "nome": ""}, str(tmp_path), 50)
    assert not [f for _, _, arquivos in os.walk(tmp_path) for f in arquivos]  # nada ficou no disco
    pagina.evaluate("""() => { document.getElementById('doc1').onclick = () => {
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['x'.repeat(2 * 1024 * 1024)], {type: 'application/pdf'}));
        a.download = 'enorme.pdf'; document.body.appendChild(a); a.click(); a.remove(); }; }""")
    with pytest.raises(cw.AnexoRecusado, match="passa de 1 MB"):
        cw._baixar_anexo(pagina, "true_5511999990000@c.us_DOC1", {"tipo": "documento", "nome": ""}, str(tmp_path), 1)


def test_processar_anexo_entrega_o_arquivo_com_a_legenda(pagina, tmp_path, monkeypatch):
    pagina.evaluate("""() => { document.getElementById('doc1').onclick = () => {
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['%PDF-1.4'], {type: 'application/pdf'}));
        a.download = 'contrato.pdf'; document.body.appendChild(a); a.click(); a.remove(); }; }""")
    entregas, avisos = [], []

    class Fila:
        def put(self, item):
            entregas.append(item)

    monkeypatch.setattr(cw, "_enviar_texto", lambda pagina_, texto: avisos.append(texto))
    leitor = LeitorDeMensagens()
    cw._processar_anexo(pagina, leitor, {"id": "true_5511999990000@c.us_DOC1", "texto": "vira fonte", "anexo": DOC}, Fila(), str(tmp_path), 50)
    assert len(entregas) == 1 and avisos == []
    entrega = entregas[0]
    assert entrega["tipo"] == "arquivo" and entrega["nome"] == "contrato.pdf" and entrega["legenda"] == "vira fonte"
    assert entrega["origem"] == "whatsapp" and os.path.isfile(entrega["caminho"])


def test_audio_video_e_falha_de_download_viram_aviso_nao_excecao(pagina, tmp_path, monkeypatch):
    entregas, avisos = [], []

    class Fila:
        def put(self, item):
            entregas.append(item)

    monkeypatch.setattr(cw, "_enviar_texto", lambda pagina_, texto: avisos.append(texto))
    monkeypatch.setattr(cw, "TEMPO_DO_PRIMEIRO_CLIQUE", 500)
    monkeypatch.setattr(cw, "TEMPO_DO_DOWNLOAD", 500)
    leitor = LeitorDeMensagens()
    cw._processar_anexo(pagina, leitor, {"id": "true_x_AUDIO1", "texto": None, "anexo": {"tipo": "audio", "nome": ""}}, Fila(), str(tmp_path), 50)
    assert "Só recebo documentos e fotos" in avisos[-1]
    cw._processar_anexo(pagina, leitor, {"id": "true_5511999990000@c.us_AUDIO1", "texto": None, "anexo": DOC}, Fila(), str(tmp_path), 50)
    assert "não consegui baixar" in avisos[-1] and "Documento" in avisos[-1] and entregas == []
    assert all(a.startswith(cw.PREFIXO_RESPOSTA) for a in avisos)  # os avisos nunca voltam como comando
