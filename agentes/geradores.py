"""Geração dos arquivos finais (Word, PDF e PowerPoint) a partir de conteúdo estruturado.

Nada aqui chama o LLM: os agentes produzem o conteúdo como dicionário e estas
funções cuidam só da forma. Estrutura esperada:

Documento:
    {"titulo", "subtitulo", "resumo", "secoes": [{"titulo", "paragrafos": [...],
     "topicos": [...], "tabela": {"colunas": [...], "linhas": [[...]]}}],
     "conclusao", "fontes": [...]}

Apresentação:
    {"titulo", "subtitulo", "slides": [{"titulo", "topicos": [...], "destaque", "notas"}], "fontes": [...]}
"""
import html
import os
import re
from datetime import datetime

# Cores (RGB) usadas nos três formatos, para os arquivos terem a mesma identidade.
COR_PRIMARIA = (0x0B, 0x3C, 0x5D)   # azul petróleo
COR_DESTAQUE = (0x1F, 0xA6, 0xC9)   # ciano
COR_TEXTO = (0x22, 0x2B, 0x33)
COR_SUAVE = (0x6B, 0x7A, 0x88)
COR_FUNDO_ESCURO = (0x07, 0x1A, 0x26)


def nome_arquivo_seguro(texto: str, limite: int = 60) -> str:
    texto = re.sub(r"[^\w\s-]", "", texto or "", flags=re.UNICODE).strip()
    texto = re.sub(r"[\s-]+", "_", texto)
    return (texto[:limite].strip("_") or "documento")


def _rodape_data() -> str:
    return f"Gerado pela Beatriz em {datetime.now():%d/%m/%Y às %H:%M}"


def _lista(valor) -> list:
    if not valor:
        return []
    if isinstance(valor, str):
        return [valor]
    return [str(item).strip() for item in valor if str(item).strip()]


# ------------------------------------------------------------------- Word
def gerar_docx(conteudo: dict, caminho: str) -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(11)
    estilo.font.color.rgb = RGBColor(*COR_TEXTO)
    for nivel, tamanho in (("Title", 26), ("Heading 1", 16), ("Heading 2", 13)):
        doc.styles[nivel].font.color.rgb = RGBColor(*COR_PRIMARIA)
        doc.styles[nivel].font.size = Pt(tamanho)

    doc.add_paragraph(conteudo.get("titulo") or "Documento", style="Title")
    if conteudo.get("subtitulo"):
        sub = doc.add_paragraph(conteudo["subtitulo"])
        sub.runs[0].font.color.rgb = RGBColor(*COR_SUAVE)
        sub.runs[0].font.size = Pt(13)
    data = doc.add_paragraph(_rodape_data())
    data.runs[0].font.size = Pt(9)
    data.runs[0].font.color.rgb = RGBColor(*COR_SUAVE)

    if conteudo.get("resumo"):
        doc.add_paragraph("Resumo", style="Heading 1")
        doc.add_paragraph(conteudo["resumo"]).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for secao in conteudo.get("secoes") or []:
        doc.add_paragraph(secao.get("titulo") or "", style="Heading 1")
        for paragrafo in _lista(secao.get("paragrafos")):
            doc.add_paragraph(paragrafo).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        for topico in _lista(secao.get("topicos")):
            doc.add_paragraph(topico, style="List Bullet")
        tabela = secao.get("tabela") or {}
        colunas = _lista(tabela.get("colunas"))
        linhas = [linha for linha in (tabela.get("linhas") or []) if isinstance(linha, list)]
        if colunas and linhas:
            grade = doc.add_table(rows=1, cols=len(colunas))
            grade.style = "Light Grid Accent 1"
            for celula, titulo in zip(grade.rows[0].cells, colunas):
                celula.text = titulo
            for linha in linhas:
                celulas = grade.add_row().cells
                for celula, valor in zip(celulas, linha):
                    celula.text = str(valor)
            doc.add_paragraph()

    if conteudo.get("conclusao"):
        doc.add_paragraph("Conclusão", style="Heading 1")
        doc.add_paragraph(conteudo["conclusao"]).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    fontes = _lista(conteudo.get("fontes"))
    if fontes:
        doc.add_paragraph("Fontes", style="Heading 1")
        for fonte in fontes:
            doc.add_paragraph(fonte, style="List Number")

    doc.save(caminho)
    return caminho


# -------------------------------------------------------------------- PDF
def _html_documento(conteudo: dict) -> str:
    e = lambda texto: html.escape(str(texto or ""))
    primaria = "#%02x%02x%02x" % COR_PRIMARIA
    destaque = "#%02x%02x%02x" % COR_DESTAQUE
    partes = [
        f'<h1 style="color:{primaria}">{e(conteudo.get("titulo") or "Documento")}</h1>',
    ]
    if conteudo.get("subtitulo"):
        partes.append(f'<p class="sub">{e(conteudo["subtitulo"])}</p>')
    partes.append(f'<p class="data">{e(_rodape_data())}</p><hr/>')
    if conteudo.get("resumo"):
        partes.append(f'<div class="resumo"><b>Resumo.</b> {e(conteudo["resumo"])}</div>')
    for secao in conteudo.get("secoes") or []:
        partes.append(f'<h2 style="color:{primaria};border-bottom:1px solid {destaque}">{e(secao.get("titulo"))}</h2>')
        partes.extend(f"<p>{e(p)}</p>" for p in _lista(secao.get("paragrafos")))
        topicos = _lista(secao.get("topicos"))
        if topicos:
            partes.append("<ul>" + "".join(f"<li>{e(t)}</li>" for t in topicos) + "</ul>")
        tabela = secao.get("tabela") or {}
        colunas = _lista(tabela.get("colunas"))
        linhas = [linha for linha in (tabela.get("linhas") or []) if isinstance(linha, list)]
        if colunas and linhas:
            cabecalho = "".join(f'<th style="background:{primaria};color:white">{e(c)}</th>' for c in colunas)
            corpo = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in linha) + "</tr>" for linha in linhas)
            partes.append(f"<table><tr>{cabecalho}</tr>{corpo}</table>")
    if conteudo.get("conclusao"):
        partes.append(f'<h2 style="color:{primaria};border-bottom:1px solid {destaque}">Conclusão</h2><p>{e(conteudo["conclusao"])}</p>')
    fontes = _lista(conteudo.get("fontes"))
    if fontes:
        partes.append(f'<h2 style="color:{primaria}">Fontes</h2><ol class="fontes">' + "".join(f"<li>{e(f)}</li>" for f in fontes) + "</ol>")
    return "".join(partes)


_CSS_PDF = """
body { font-family: sans-serif; font-size: 11pt; color: #222b33; line-height: 1.45; }
h1 { font-size: 22pt; margin-bottom: 2pt; }
h2 { font-size: 14pt; margin-top: 14pt; padding-bottom: 2pt; }
p { text-align: justify; margin: 4pt 0; }
.sub { font-size: 13pt; color: #6b7a88; text-align: left; }
.data { font-size: 8pt; color: #6b7a88; text-align: left; }
.resumo { background-color: #eef7fb; padding: 6pt; margin: 6pt 0; }
li { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; }
th, td { border: 1px solid #c9d3dc; padding: 3pt 5pt; font-size: 10pt; text-align: left; }
.fontes { font-size: 9pt; color: #44515c; }
"""


def gerar_pdf(conteudo: dict, caminho: str) -> str:
    import pymupdf

    historia = pymupdf.Story(html=_html_documento(conteudo), user_css=_CSS_PDF)
    pagina = pymupdf.paper_rect("a4")
    area = pagina + (54, 54, -54, -60)  # margens
    escritor = pymupdf.DocumentWriter(caminho)
    mais = True
    while mais:
        dispositivo = escritor.begin_page(pagina)
        mais, _ = historia.place(area)
        historia.draw(dispositivo)
        escritor.end_page()
    escritor.close()

    # Numeração de páginas no rodapé.
    documento = pymupdf.open(caminho)
    total = documento.page_count
    for indice, pagina_pdf in enumerate(documento, start=1):
        pagina_pdf.insert_text(
            (pagina_pdf.rect.width - 110, pagina_pdf.rect.height - 30),
            f"Página {indice} de {total}", fontsize=8, color=tuple(c / 255 for c in COR_SUAVE),
        )
    documento.saveIncr()
    documento.close()
    return caminho


# ------------------------------------------------------------- PowerPoint
def gerar_pptx(conteudo: dict, caminho: str) -> str:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt

    apresentacao = Presentation()
    apresentacao.slide_width, apresentacao.slide_height = Inches(13.333), Inches(7.5)
    largura, altura = apresentacao.slide_width, apresentacao.slide_height
    vazio = apresentacao.slide_layouts[6]

    def retangulo(slide, x, y, w, h, cor):
        forma = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
        forma.fill.solid()
        forma.fill.fore_color.rgb = RGBColor(*cor)
        forma.line.fill.background()
        return forma

    def caixa_texto(slide, x, y, w, h, texto, tamanho, cor, negrito=False):
        caixa = slide.shapes.add_textbox(x, y, w, h)
        quadro = caixa.text_frame
        quadro.word_wrap = True
        quadro.text = texto
        fonte = quadro.paragraphs[0].runs[0].font
        fonte.size, fonte.bold, fonte.name = Pt(tamanho), negrito, "Segoe UI"
        fonte.color.rgb = RGBColor(*cor)
        return quadro

    # Capa escura com faixa de destaque.
    capa = apresentacao.slides.add_slide(vazio)
    retangulo(capa, 0, 0, largura, altura, COR_FUNDO_ESCURO)
    retangulo(capa, Inches(0.8), Inches(3.55), Inches(1.6), Emu(54000), COR_DESTAQUE)
    caixa_texto(capa, Inches(0.8), Inches(1.9), Inches(11.5), Inches(1.6), conteudo.get("titulo") or "Apresentação", 40, (255, 255, 255), True)
    if conteudo.get("subtitulo"):
        caixa_texto(capa, Inches(0.8), Inches(3.8), Inches(11.5), Inches(1.0), conteudo["subtitulo"], 20, (0xBF, 0xE9, 0xF5))
    caixa_texto(capa, Inches(0.8), Inches(6.6), Inches(8), Inches(0.4), _rodape_data(), 11, (0x8A, 0xA4, 0xB4))

    slides = conteudo.get("slides") or []
    for numero, dados in enumerate(slides, start=1):
        slide = apresentacao.slides.add_slide(vazio)
        retangulo(slide, 0, 0, Inches(0.25), altura, COR_PRIMARIA)
        retangulo(slide, Inches(0.25), 0, Inches(0.06), altura, COR_DESTAQUE)
        caixa_texto(slide, Inches(0.8), Inches(0.45), Inches(11.8), Inches(1.0), dados.get("titulo") or "", 30, COR_PRIMARIA, True)
        retangulo(slide, Inches(0.8), Inches(1.45), Inches(1.2), Emu(40000), COR_DESTAQUE)

        topicos = _lista(dados.get("topicos"))[:7]
        if topicos:
            corpo = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.8), Inches(5.0)).text_frame
            corpo.word_wrap = True
            tamanho = 22 if len(topicos) <= 4 else 19
            for indice, topico in enumerate(topicos):
                paragrafo = corpo.paragraphs[0] if indice == 0 else corpo.add_paragraph()
                marcador = paragrafo.add_run()
                marcador.text = "▪  "
                marcador.font.color.rgb = RGBColor(*COR_DESTAQUE)
                marcador.font.size = Pt(tamanho)
                texto = paragrafo.add_run()
                texto.text = topico
                texto.font.size, texto.font.name = Pt(tamanho), "Segoe UI"
                texto.font.color.rgb = RGBColor(*COR_TEXTO)
                paragrafo.space_after = Pt(12)
        if dados.get("destaque"):
            # Mensagem-chave do slide numa faixa clara no rodapé.
            faixa = retangulo(slide, Inches(0.8), Inches(5.75), Inches(11.7), Inches(0.95), (0xE6, 0xF5, 0xFA))
            faixa.line.color.rgb = RGBColor(*COR_DESTAQUE)
            quadro = faixa.text_frame
            quadro.word_wrap = True
            quadro.margin_left = Inches(0.3)
            quadro.text = str(dados["destaque"])
            fonte = quadro.paragraphs[0].runs[0].font
            fonte.size, fonte.bold, fonte.name = Pt(17), True, "Segoe UI"
            fonte.color.rgb = RGBColor(*COR_PRIMARIA)
        caixa_texto(slide, largura - Inches(1.3), altura - Inches(0.6), Inches(1.0), Inches(0.4), f"{numero}/{len(slides)}", 11, COR_SUAVE)
        if dados.get("notas"):
            slide.notes_slide.notes_text_frame.text = str(dados["notas"])

    fontes = _lista(conteudo.get("fontes"))
    if fontes:
        slide = apresentacao.slides.add_slide(vazio)
        retangulo(slide, 0, 0, Inches(0.25), altura, COR_PRIMARIA)
        caixa_texto(slide, Inches(0.8), Inches(0.45), Inches(11.8), Inches(1.0), "Fontes", 30, COR_PRIMARIA, True)
        caixa_texto(slide, Inches(0.8), Inches(1.7), Inches(11.8), Inches(5.2), "\n".join(fontes[:10]), 13, COR_SUAVE)

    apresentacao.save(caminho)
    return caminho


# ----------------------------------------------------------------- Excel
FORMATOS_EXCEL = {
    "moeda": 'R$ #,##0.00',
    "numero": '#,##0.##',
    "inteiro": '#,##0',
    "percentual": '0.0%',
    "data": 'dd/mm/yyyy',
}


def _numero(valor):
    """'R$ 1.234,56' -> 1234.56; números passam direto; o resto volta como veio."""
    if isinstance(valor, (int, float)) or valor is None:
        return valor
    texto = str(valor).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})*(,\d+)?|-?\d+(,\d+)?", texto):
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto) if "." in texto else int(texto)
    except ValueError:
        return valor


def _valor_celula(valor, tipo):
    if isinstance(valor, str) and valor.startswith("="):
        return valor  # fórmula do Excel
    if tipo == "data" and isinstance(valor, str):
        try:
            return datetime.strptime(valor[:10], "%Y-%m-%d")
        except ValueError:
            return valor
    if tipo in ("moeda", "numero", "inteiro", "percentual"):
        numero = _numero(valor)
        if tipo == "percentual" and isinstance(numero, (int, float)) and abs(numero) > 1:
            numero = numero / 100  # "15" ou "15%" viram 15%
        return numero
    return valor


def gerar_xlsx(conteudo: dict, caminho: str) -> str:
    """Planilha com cabeçalho formatado, filtros, formatos por tipo, linha de total e gráfico.

    Estrutura: {"titulo", "abas": [{"nome", "colunas": [...], "tipos": [...], "linhas": [[...]],
    "total": bool, "grafico": {"tipo": "barras|linhas|pizza", "titulo", "categorias", "valores": [...]}}],
    "observacoes", "fontes"}
    """
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    cor = lambda rgb: "%02X%02X%02X" % rgb
    livro = Workbook()
    livro.remove(livro.active)
    borda = Border(bottom=Side(style="thin", color="C9D3DC"))

    for indice, aba in enumerate(conteudo.get("abas") or [], start=1):
        colunas = _lista(aba.get("colunas"))
        if not colunas:
            continue
        nome = re.sub(r"[\[\]:*?/\\]", "", str(aba.get("nome") or f"Planilha {indice}"))[:31] or f"Planilha {indice}"
        folha = livro.create_sheet(nome)
        tipos = (_lista(aba.get("tipos")) + ["texto"] * len(colunas))[:len(colunas)]
        linhas = [linha for linha in (aba.get("linhas") or []) if isinstance(linha, list)]

        folha.append(colunas)
        for celula in folha[1]:
            celula.font = Font(bold=True, color="FFFFFF")
            celula.fill = PatternFill("solid", fgColor=cor(COR_PRIMARIA))
            celula.alignment = Alignment(vertical="center", wrap_text=True)
        for linha in linhas:
            folha.append([_valor_celula(v, t) for v, t in zip(linha, tipos)])
        ultima = len(linhas) + 1
        for numero_coluna, tipo in enumerate(tipos, start=1):
            for linha_excel in range(2, ultima + 1):
                celula = folha.cell(row=linha_excel, column=numero_coluna)
                celula.border = borda
                if tipo in FORMATOS_EXCEL:
                    celula.number_format = FORMATOS_EXCEL[tipo]

        numericas = [i for i, t in enumerate(tipos, start=1) if t in ("moeda", "numero", "inteiro")]
        if aba.get("total") and numericas and linhas:
            total = ultima + 1
            folha.cell(row=total, column=1, value="Total").font = Font(bold=True)
            for numero_coluna in numericas:
                letra = get_column_letter(numero_coluna)
                celula = folha.cell(row=total, column=numero_coluna, value=f"=SUM({letra}2:{letra}{ultima})")
                celula.font = Font(bold=True)
                celula.number_format = FORMATOS_EXCEL[tipos[numero_coluna - 1]]
                celula.fill = PatternFill("solid", fgColor="E6F5FA")

        for numero_coluna, titulo in enumerate(colunas, start=1):
            largura = max([len(str(titulo))] + [len(str(l[numero_coluna - 1])) for l in linhas if len(l) >= numero_coluna])
            folha.column_dimensions[get_column_letter(numero_coluna)].width = min(max(10, largura + 3), 50)
        folha.freeze_panes = "A2"
        folha.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{ultima}"

        grafico = aba.get("grafico") or {}
        if grafico and linhas:
            tipo_grafico = str(grafico.get("tipo", "barras")).lower()
            classe = PieChart if "pizza" in tipo_grafico else LineChart if "linha" in tipo_grafico else BarChart
            figura = classe()
            figura.title = grafico.get("titulo") or nome
            figura.height, figura.width = 8, 16
            categorias = colunas.index(grafico["categorias"]) + 1 if grafico.get("categorias") in colunas else 1
            valores = [colunas.index(v) + 1 for v in _lista(grafico.get("valores")) if v in colunas] or numericas[:1]
            for numero_coluna in valores:
                figura.add_data(Reference(folha, min_col=numero_coluna, min_row=1, max_row=ultima), titles_from_data=True)
            figura.set_categories(Reference(folha, min_col=categorias, min_row=2, max_row=ultima))
            folha.add_chart(figura, f"{get_column_letter(len(colunas) + 2)}2")

    sobre = livro.create_sheet("Sobre")
    sobre["A1"] = conteudo.get("titulo") or "Planilha"
    sobre["A1"].font = Font(bold=True, size=16, color=cor(COR_PRIMARIA))
    sobre["A2"] = _rodape_data()
    sobre["A2"].font = Font(italic=True, color=cor(COR_SUAVE))
    linha_sobre = 4
    if conteudo.get("observacoes"):
        sobre.cell(row=linha_sobre, column=1, value=str(conteudo["observacoes"])).alignment = Alignment(wrap_text=True)
        linha_sobre += 2
    for fonte in _lista(conteudo.get("fontes")):
        sobre.cell(row=linha_sobre, column=1, value=fonte)
        linha_sobre += 1
    sobre.column_dimensions["A"].width = 100
    if not livro.sheetnames[:-1]:  # nenhuma aba de dados válida
        raise ValueError("A planilha não tem nenhuma aba com colunas.")
    livro.save(caminho)
    return caminho


# --------------------------------------------------------------- flashcards
def gerar_flashcards_anki(flashcards: list, caminho: str) -> str:
    """Arquivo de texto separado por tabulação, que o Anki importa direto (Arquivo > Importar)."""
    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write("#separator:tab\n#html:false\n")
        for cartao in flashcards:
            frente = str(cartao.get("frente", "")).replace("\t", " ").replace("\n", " ").strip()
            verso = str(cartao.get("verso", "")).replace("\t", " ").replace("\n", " ").strip()
            if frente and verso:
                arquivo.write(f"{frente}\t{verso}\n")
    return caminho


GERADORES = {"docx": gerar_docx, "pdf": gerar_pdf, "pptx": gerar_pptx, "xlsx": gerar_xlsx}


def gerar_arquivo(formato: str, conteudo: dict, pasta: str) -> str:
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, f"{nome_arquivo_seguro(conteudo.get('titulo'))}.{formato}")
    return GERADORES[formato](conteudo, caminho)
