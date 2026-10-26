"""Artigos de lei e súmulas: lê o texto oficial e explica com o Gemini, de outro jeito se não entender, com exemplos.

Exemplos: "dita o artigo 5º da Constituição", "o que diz o artigo 49 do Código de Defesa do Consumidor?",
"me explica o artigo 121 do Código Penal", "do que se trata a LGPD?", "lê o inciso XI do artigo 5º",
"artigo 7 da lei 13.709", "artigo 5 do ADCT". Súmulas: "súmula 7 do STJ", "explica a súmula vinculante 11",
"a súmula 331 do TST ainda vale?", "OJ 191 da SDI-1". Pelo assunto: "tem súmula sobre dano moral?", "qual artigo do
Código Civil fala de usucapião?", "procura no vade-mécum prisão em flagrante" (depois: "explica a segunda").
Depois: "não entendi" / "explica de outro jeito" (nova explicação, com outra abordagem a cada vez), "me dá um
exemplo" (o caso real que o STF pôs na súmula ou um exemplo prático), "continua" (segue lendo), "lê de novo".

O texto vem do vade-mécum do PC (vade_mecum.py: as normas do catálogo NORMAS e as súmulas do STF, do STJ e do TST,
baixadas em segundo plano) e, se faltar lá, do site do Planalto (e fica guardado). O texto da lei é público, então
pode ir para a nuvem: a explicação sai do Gemini (o Claude e o Ollama ficam de reserva; a ordem é a de
ORDEM_IA_PESQUISA). Nada da memória, das notas ou do histórico vai junto.
`AgenteLeis.responder(texto)` devolve uma RespostaLei (o que ler agora e a explicação, que pode
demorar alguns segundos) ou None (o roteamento segue).
"""
import html as html_util
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field

import vade_mecum
from agenda_linguagem import preparar

logger = logging.getLogger(__name__)

PLANALTO = "https://www.planalto.gov.br/ccivil_03"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"}
LIMITE_LEITURA = 1200  # caracteres lidos de cada vez (~1 min e meio de fala); o resto vem com "continua"
SEGUNDOS_DO_ASSUNTO = 15 * 60  # "não entendi" vale até 15 min depois da última explicação
SEGUNDOS_CACHE = 6 * 3600


@dataclass(frozen=True)
class Norma:
    chave: str
    nome: str  # como ela fala: "a Constituição Federal"
    url: str
    apelidos: str  # regex no texto sem acentos
    numero: str = ""  # "8078" (para "lei 8.078")
    ano: str = ""  # só nas leis fora do catálogo, quando a pessoa diz ("lei 14.181 de 2021")
    tipo: str = "lei"  # lei | decreto-lei


NORMAS = [
    Norma("cf", "a Constituição Federal", f"{PLANALTO}/constituicao/constituicao.htm",
          r"\bconstituic\w*(?: federal| da republica| brasileira)?\b|\bcf\b|\bcrfb\b|\bcarta magna\b"),
    Norma("adct", "o ADCT", f"{PLANALTO}/constituicao/constituicao.htm",
          r"\badct\b|\bato das disposicoes constitucionais transitorias\b|\bdisposicoes (?:constitucionais )?transitorias\b"),
    # ---- códigos
    Norma("cp", "o Código Penal", f"{PLANALTO}/decreto-lei/del2848compilado.htm", r"\bcodigo penal\b|\bcp\b", "2848",
          tipo="decreto-lei"),
    Norma("cc", "o Código Civil", f"{PLANALTO}/leis/2002/l10406compilada.htm", r"\bcodigo civil\b|\bcc\b", "10406"),
    Norma("cpc", "o Código de Processo Civil", f"{PLANALTO}/_ato2015-2018/2015/lei/l13105.htm",
          r"\bcodigo de processo civil\b|\bcpc\b", "13105"),
    Norma("cpp", "o Código de Processo Penal", f"{PLANALTO}/decreto-lei/del3689compilado.htm",
          r"\bcodigo de processo penal\b|\bcpp\b", "3689", tipo="decreto-lei"),
    Norma("clt", "a CLT", f"{PLANALTO}/decreto-lei/del5452.htm",
          r"\bclt\b|\bconsolidacao das leis do trabalho\b|\bleis? trabalhistas?\b", "5452", tipo="decreto-lei"),
    Norma("cdc", "o Código de Defesa do Consumidor", f"{PLANALTO}/leis/l8078compilado.htm",
          r"\bcodigo de defesa do consumidor\b|\bcodigo do consumidor\b|\bcdc\b", "8078"),
    Norma("ctb", "o Código de Trânsito Brasileiro", f"{PLANALTO}/leis/l9503compilado.htm",
          r"\bcodigo de transito\w*(?: brasileiro)?\b|\bctb\b", "9503"),
    Norma("ctn", "o Código Tributário Nacional", f"{PLANALTO}/leis/l5172compilado.htm",
          r"\bcodigo tributario\w*(?: nacional)?\b|\bctn\b", "5172"),
    Norma("cpm", "o Código Penal Militar", f"{PLANALTO}/decreto-lei/del1001.htm", r"\bcodigo penal militar\b|\bcpm\b",
          "1001", tipo="decreto-lei"),
    Norma("cppm", "o Código de Processo Penal Militar", f"{PLANALTO}/decreto-lei/del1002.htm",
          r"\bcodigo de processo penal militar\b|\bcppm\b", "1002", tipo="decreto-lei"),
    Norma("eleitoral", "o Código Eleitoral", f"{PLANALTO}/leis/l4737compilado.htm", r"\bcodigo eleitoral\b", "4737"),
    Norma("florestal", "o Código Florestal", f"{PLANALTO}/_ato2011-2014/2012/lei/l12651.htm",
          r"\b(?:novo )?codigo florestal\b", "12651"),
    Norma("aeronautica", "o Código Brasileiro de Aeronáutica", f"{PLANALTO}/leis/l7565compilado.htm",
          r"\bcodigo (?:brasileiro )?de aeronautica\b", "7565"),
    Norma("comercial", "o Código Comercial", f"{PLANALTO}/leis/lim/lim556.htm", r"\bcodigo comercial\b", "556"),
    # ---- estatutos
    Norma("eca", "o Estatuto da Criança e do Adolescente", f"{PLANALTO}/leis/l8069.htm",
          r"\beca\b|\bestatuto da crianca\w*(?: e do adolescente)?\b", "8069"),
    Norma("idoso", "o Estatuto da Pessoa Idosa", f"{PLANALTO}/leis/2003/l10.741.htm",
          r"\bestatuto do idoso\b|\bestatuto da pessoa idosa\b", "10741"),
    Norma("oab", "o Estatuto da Advocacia", f"{PLANALTO}/leis/l8906.htm",
          r"\bestatuto da (?:advocacia|oab)\b|\bestatuto dos advogados\b", "8906"),
    Norma("cidade", "o Estatuto da Cidade", f"{PLANALTO}/leis/leis_2001/l10257.htm", r"\bestatuto da cidade\b", "10257"),
    Norma("desarmamento", "o Estatuto do Desarmamento", f"{PLANALTO}/leis/2003/l10.826.htm",
          r"\bestatuto do desarmamento\b", "10826"),
    Norma("pcd", "o Estatuto da Pessoa com Deficiência", f"{PLANALTO}/_ato2015-2018/2015/lei/l13146.htm",
          r"\bestatuto da pessoa com deficiencia\b|\blei brasileira de inclusao\b|\blbi\b", "13146"),
    Norma("igualdade_racial", "o Estatuto da Igualdade Racial", f"{PLANALTO}/_ato2007-2010/2010/lei/l12288.htm",
          r"\bestatuto da igualdade racial\b", "12288"),
    Norma("juventude", "o Estatuto da Juventude", f"{PLANALTO}/_ato2011-2014/2013/lei/l12852.htm",
          r"\bestatuto da juventude\b", "12852"),
    Norma("terra", "o Estatuto da Terra", f"{PLANALTO}/leis/l4504.htm", r"\bestatuto da terra\b", "4504"),
    Norma("servidores", "o Estatuto dos Servidores Federais (Lei 8.112)", f"{PLANALTO}/leis/l8112cons.htm",
          r"\bestatuto dos servidores\w*(?: publicos)?(?: federais| civis da uniao)?\b|\bregime juridico unico\b", "8112"),
    Norma("militares", "o Estatuto dos Militares", f"{PLANALTO}/leis/l6880.htm", r"\bestatuto dos militares\b", "6880"),
    Norma("indio", "o Estatuto do Índio", f"{PLANALTO}/leis/l6001.htm", r"\bestatuto do indio\b", "6001"),
    Norma("migracao", "a Lei de Migração", f"{PLANALTO}/_ato2015-2018/2017/lei/l13445.htm",
          r"\blei de migracao\b|\bestatuto do estrangeiro\b", "13445"),
    Norma("microempresa", "o Estatuto da Microempresa (LC 123)", f"{PLANALTO}/leis/lcp/lcp123.htm",
          r"\bestatuto da (?:micro ?empresa|me e epp)\b|\blei do simples(?: nacional)?\b|\bsimples nacional\b", "123",
          tipo="lc"),
    # ---- constitucional e processo
    Norma("lindb", "a LINDB", f"{PLANALTO}/decreto-lei/del4657compilado.htm",
          r"\blindb\b|\blei de introducao\w*(?: as normas do direito brasileiro| ao codigo civil)?\b", "4657",
          tipo="decreto-lei"),
    Norma("mandado_de_seguranca", "a Lei do Mandado de Segurança", f"{PLANALTO}/_ato2007-2010/2009/lei/l12016.htm",
          r"\blei do mandado de seguranca\b|\blei do ms\b", "12016"),
    Norma("acao_civil_publica", "a Lei da Ação Civil Pública", f"{PLANALTO}/leis/l7347orig.htm",
          r"\blei da acao civil publica\b|\blacp\b", "7347"),
    Norma("acao_popular", "a Lei da Ação Popular", f"{PLANALTO}/leis/l4717.htm", r"\blei da acao popular\b", "4717"),
    Norma("habeas_data", "a Lei do Habeas Data", f"{PLANALTO}/leis/l9507.htm", r"\blei do habeas data\b", "9507"),
    Norma("adi", "a Lei da ADI e da ADC", f"{PLANALTO}/leis/l9868.htm",
          r"\blei da adi\b|\blei da adc\b|\blei das acoes diretas\b", "9868"),
    Norma("adpf", "a Lei da ADPF", f"{PLANALTO}/leis/l9882.htm", r"\blei da adpf\b", "9882"),
    Norma("mandado_de_injuncao", "a Lei do Mandado de Injunção", f"{PLANALTO}/_ato2015-2018/2016/lei/l13300.htm",
          r"\blei do mandado de injuncao\b", "13300"),
    Norma("juizados", "a Lei dos Juizados Especiais", f"{PLANALTO}/leis/l9099.htm",
          r"\blei dos juizados(?: especiais)?(?: civeis e criminais)?\b|\bjuizados especiais civeis\b", "9099"),
    Norma("juizados_federais", "a Lei dos Juizados Especiais Federais", f"{PLANALTO}/leis/leis_2001/l10259.htm",
          r"\bjuizados especiais federais\b|\blei dos juizados federais\b", "10259"),
    Norma("juizados_fazenda", "a Lei dos Juizados da Fazenda Pública", f"{PLANALTO}/_ato2007-2010/2009/lei/l12153.htm",
          r"\bjuizados (?:especiais )?da fazenda publica\b", "12153"),
    Norma("execucao_fiscal", "a Lei de Execução Fiscal", f"{PLANALTO}/leis/l6830.htm",
          r"\blei de execuc\w* fisca\w*\b|\blef\b", "6830"),
    Norma("arbitragem", "a Lei de Arbitragem", f"{PLANALTO}/leis/l9307.htm", r"\blei de arbitragem\b", "9307"),
    Norma("mediacao", "a Lei de Mediação", f"{PLANALTO}/_ato2015-2018/2015/lei/l13140.htm", r"\blei de mediacao\b",
          "13140"),
    Norma("alimentos", "a Lei de Alimentos", f"{PLANALTO}/leis/l5478.htm", r"\blei de alimentos\b", "5478"),
    Norma("gratuidade", "a Lei da Assistência Judiciária", f"{PLANALTO}/leis/l1060.htm",
          r"\blei da (?:assistencia judiciaria|gratuidade da justica|justica gratuita)\b", "1060"),
    # ---- penal
    Norma("execucao_penal", "a Lei de Execução Penal", f"{PLANALTO}/leis/l7210.htm",
          r"\blei de execuc\w* pena\w*\b|\blep\b", "7210"),
    Norma("hediondos", "a Lei dos Crimes Hediondos", f"{PLANALTO}/leis/l8072.htm",
          r"\blei dos crimes hediondos\b|\bcrimes hediondos\b", "8072"),
    Norma("abuso_de_autoridade", "a Lei de Abuso de Autoridade", f"{PLANALTO}/_ato2019-2022/2019/lei/l13869.htm",
          r"\blei d[eo] abuso de autoridade\b|\babuso de autoridade\b", "13869"),
    Norma("lavagem", "a Lei de Lavagem de Dinheiro", f"{PLANALTO}/leis/l9613.htm",
          r"\blei de lavagem(?: de dinheiro| de capitais)?\b", "9613"),
    Norma("organizacao_criminosa", "a Lei das Organizações Criminosas", f"{PLANALTO}/_ato2011-2014/2013/lei/l12850.htm",
          r"\blei d[ae]s? organizac\w* criminos\w*\b|\blei do crime organizado\b", "12850"),
    Norma("interceptacao", "a Lei de Interceptação Telefônica", f"{PLANALTO}/leis/l9296.htm",
          r"\blei de interceptac\w*(?: telefonica)?\b|\blei das escutas\b", "9296"),
    Norma("crimes_ambientais", "a Lei de Crimes Ambientais", f"{PLANALTO}/leis/l9605.htm",
          r"\blei d[eo]s? crimes ambientais\b", "9605"),
    Norma("tortura", "a Lei de Tortura", f"{PLANALTO}/leis/l9455.htm", r"\blei d[ae] tortura\b", "9455"),
    Norma("contravencoes", "a Lei das Contravenções Penais", f"{PLANALTO}/decreto-lei/del3688.htm",
          r"\blei das contravenc\w*(?: penais)?\b|\bcontravencoes penais\b", "3688", tipo="decreto-lei"),
    Norma("racismo", "a Lei do Racismo", f"{PLANALTO}/leis/l7716.htm", r"\blei do racismo\b|\blei de crimes raciais\b",
          "7716"),
    Norma("prisao_temporaria", "a Lei da Prisão Temporária", f"{PLANALTO}/leis/l7960.htm",
          r"\blei da prisao temporaria\b", "7960"),
    Norma("crimes_tributarios", "a Lei dos Crimes contra a Ordem Tributária", f"{PLANALTO}/leis/l8137.htm",
          r"\bcrimes contra a ordem tributaria\b", "8137"),
    Norma("sistema_financeiro", "a Lei dos Crimes contra o Sistema Financeiro", f"{PLANALTO}/leis/l7492.htm",
          r"\bcrimes contra o sistema financeiro\b|\blei do colarinho branco\b", "7492"),
    Norma("crimes_de_responsabilidade", "a Lei dos Crimes de Responsabilidade", f"{PLANALTO}/leis/l1079.htm",
          r"\blei dos crimes de responsabilidade\b|\blei do impeachment\b", "1079"),
    Norma("prefeitos", "o Decreto-Lei 201 (crimes dos prefeitos)", f"{PLANALTO}/decreto-lei/del0201.htm",
          r"\bcrimes? de responsabilidade dos prefeitos\b|\bdecreto[- ]lei 201\b", "201", tipo="decreto-lei"),
    Norma("anticrime", "o Pacote Anticrime", f"{PLANALTO}/_ato2019-2022/2019/lei/l13964.htm",
          r"\bpacote anticrime\b|\blei anticrime\b", "13964"),
    Norma("protecao_a_testemunhas", "a Lei de Proteção a Testemunhas", f"{PLANALTO}/leis/l9807.htm",
          r"\blei de protecao a (?:vitimas e )?testemunhas\b", "9807"),
    Norma("terrorismo", "a Lei Antiterrorismo", f"{PLANALTO}/_ato2015-2018/2016/lei/l13260.htm",
          r"\blei antiterror\w*\b|\blei de terrorismo\b", "13260"),
    Norma("henry_borel", "a Lei Henry Borel", f"{PLANALTO}/_ato2019-2022/2022/lei/l14344.htm", r"\bhenry borel\b",
          "14344"),
    Norma("sinase", "a Lei do Sinase", f"{PLANALTO}/_ato2011-2014/2012/lei/l12594.htm", r"\bsinase\b", "12594"),
    Norma("maria_da_penha", "a Lei Maria da Penha", f"{PLANALTO}/_ato2004-2006/2006/lei/l11340.htm",
          r"\bmaria da penha\b", "11340"),
    Norma("drogas", "a Lei de Drogas", f"{PLANALTO}/_ato2004-2006/2006/lei/l11343.htm",
          r"\blei de drogas\b|\blei antidrogas\b", "11343"),
    # ---- administrativo
    Norma("improbidade", "a Lei de Improbidade Administrativa", f"{PLANALTO}/leis/l8429.htm",
          r"\blei de improbidade(?: administrativa)?\b", "8429"),
    Norma("processo_administrativo", "a Lei do Processo Administrativo", f"{PLANALTO}/leis/l9784.htm",
          r"\blei do processo administrativo(?: federal)?\b|\blpa\b", "9784"),
    Norma("anticorrupcao", "a Lei Anticorrupção", f"{PLANALTO}/_ato2011-2014/2013/lei/l12846.htm",
          r"\blei anticorrupcao\b|\blei da empresa limpa\b", "12846"),
    Norma("acesso_a_informacao", "a Lei de Acesso à Informação", f"{PLANALTO}/_ato2011-2014/2011/lei/l12527.htm",
          r"\blei de acesso a informacao\b|\blai\b", "12527"),
    Norma("responsabilidade_fiscal", "a Lei de Responsabilidade Fiscal", f"{PLANALTO}/leis/lcp/lcp101.htm",
          r"\blei de responsabilidade fiscal\b|\blrf\b", "101", tipo="lc"),
    Norma("licitacoes", "a Lei de Licitações", f"{PLANALTO}/_ato2019-2022/2021/lei/l14133.htm",
          r"\b(?:nova )?lei de licitac\w*(?: e contratos)?\b", "14133"),
    Norma("concessoes", "a Lei de Concessões", f"{PLANALTO}/leis/l8987cons.htm",
          r"\blei d[ae]s? concess\w*(?: e permiss\w*)?\b", "8987"),
    Norma("ppp", "a Lei das PPPs", f"{PLANALTO}/_ato2004-2006/2004/lei/l11079.htm",
          r"\blei das ppps?\b|\bparcerias? publico[- ]privadas?\b", "11079"),
    Norma("estatais", "a Lei das Estatais", f"{PLANALTO}/_ato2015-2018/2016/lei/l13303.htm", r"\blei das estatais\b",
          "13303"),
    Norma("desapropriacao", "a Lei de Desapropriação", f"{PLANALTO}/decreto-lei/del3365.htm",
          r"\blei (?:geral )?de desapropriac\w*\b", "3365", tipo="decreto-lei"),
    Norma("prescricao_fazenda", "o Decreto 20.910", f"{PLANALTO}/decreto/antigos/d20910.htm",
          r"\bprescricao (?:quinquenal )?contra a fazenda\b", "20910", tipo="decreto"),
    # ---- civil e empresarial
    Norma("inquilinato", "a Lei do Inquilinato", f"{PLANALTO}/leis/l8245.htm",
          r"\blei do inquilinato\b|\blei de locac\w*\b", "8245"),
    Norma("falencias", "a Lei de Falências", f"{PLANALTO}/_ato2004-2006/2005/lei/l11101.htm",
          r"\blei de falencias?\b|\blei de recuperacao (?:judicial|de empresas)\b", "11101"),
    Norma("sociedades_anonimas", "a Lei das S.A.", f"{PLANALTO}/leis/l6404consol.htm",
          r"\blei das s ?a\b|\blei das sociedades (?:anonimas|por acoes)\b", "6404"),
    Norma("registros_publicos", "a Lei de Registros Públicos", f"{PLANALTO}/leis/l6015compilada.htm",
          r"\blei de registros publicos\b|\blrp\b", "6015"),
    Norma("incorporacoes", "a Lei de Incorporações", f"{PLANALTO}/leis/l4591.htm",
          r"\blei de incorporac\w*(?: imobiliarias?)?\b|\blei do condominio\b", "4591"),
    Norma("bem_de_familia", "a Lei do Bem de Família", f"{PLANALTO}/leis/l8009.htm", r"\blei do bem de familia\b",
          "8009"),
    Norma("divorcio", "a Lei do Divórcio", f"{PLANALTO}/leis/l6515.htm", r"\blei do divorcio\b", "6515"),
    Norma("alienacao_parental", "a Lei da Alienação Parental", f"{PLANALTO}/_ato2007-2010/2010/lei/l12318.htm",
          r"\blei da alienacao parental\b|\balienacao parental\b", "12318"),
    Norma("direitos_autorais", "a Lei de Direitos Autorais", f"{PLANALTO}/leis/l9610.htm",
          r"\blei de direitos? autora\w*\b", "9610"),
    Norma("propriedade_industrial", "a Lei de Propriedade Industrial", f"{PLANALTO}/leis/l9279.htm",
          r"\blei de propriedade industrial\b|\blei de patentes\b", "9279"),
    Norma("cheque", "a Lei do Cheque", f"{PLANALTO}/leis/l7357.htm", r"\blei do cheque\b", "7357"),
    Norma("duplicatas", "a Lei das Duplicatas", f"{PLANALTO}/leis/l5474.htm", r"\blei das? duplicatas?\b", "5474"),
    Norma("alienacao_fiduciaria", "a Lei da Alienação Fiduciária de Imóveis", f"{PLANALTO}/leis/l9514.htm",
          r"\balienacao fiduciaria de (?:bens )?imoveis\b|\blei do sfi\b", "9514"),
    Norma("liberdade_economica", "a Lei da Liberdade Econômica", f"{PLANALTO}/_ato2019-2022/2019/lei/l13874.htm",
          r"\blei da liberdade economica\b", "13874"),
    Norma("planos_de_saude", "a Lei dos Planos de Saúde", f"{PLANALTO}/leis/l9656.htm",
          r"\blei dos planos de saude\b", "9656"),
    Norma("lgpd", "a LGPD", f"{PLANALTO}/_ato2015-2018/2018/lei/l13709.htm",
          r"\blgpd\b|\blei geral de protecao de dados\b", "13709"),
    Norma("marco_civil", "o Marco Civil da Internet", f"{PLANALTO}/_ato2011-2014/2014/lei/l12965.htm",
          r"\bmarco civil\w*(?: da internet)?\b", "12965"),
    # ---- trabalho e previdência
    Norma("domesticas", "a Lei das Empregadas Domésticas (LC 150)", f"{PLANALTO}/leis/lcp/lcp150.htm",
          r"\blei d[ao]s? (?:empregad[ao]s? )?domestic\w*\b|\bpec das domesticas\b", "150", tipo="lc"),
    Norma("fgts", "a Lei do FGTS", f"{PLANALTO}/leis/l8036consol.htm", r"\blei do fgts\b", "8036"),
    Norma("beneficios", "a Lei de Benefícios da Previdência", f"{PLANALTO}/leis/l8213cons.htm",
          r"\blei de beneficios(?: da previdencia)?\b|\blei dos beneficios previdenciarios\b", "8213"),
    Norma("custeio", "a Lei de Custeio da Seguridade Social", f"{PLANALTO}/leis/l8212cons.htm",
          r"\blei de custeio(?: da (?:seguridade|previdencia))?\b|\blei organica da seguridade\b", "8212"),
    Norma("loas", "a LOAS", f"{PLANALTO}/leis/l8742.htm", r"\bloas\b|\blei organica da assistencia social\b", "8742"),
    Norma("greve", "a Lei de Greve", f"{PLANALTO}/leis/l7783.htm", r"\blei de greve\b", "7783"),
    Norma("terceirizacao", "a Lei do Trabalho Temporário e da Terceirização", f"{PLANALTO}/leis/l6019.htm",
          r"\blei d[ao] (?:trabalho temporario|terceirizacao)\b", "6019"),
    Norma("estagio", "a Lei do Estágio", f"{PLANALTO}/_ato2007-2010/2008/lei/l11788.htm", r"\blei do estagio\b", "11788"),
    # ---- tributário, eleitoral, ambiental, saúde e educação
    Norma("iss", "a Lei Complementar do ISS (LC 116)", f"{PLANALTO}/leis/lcp/lcp116.htm",
          r"\blei (?:complementar )?do iss\b", "116", tipo="lc"),
    Norma("kandir", "a Lei Kandir (LC 87)", f"{PLANALTO}/leis/lcp/lcp87.htm", r"\blei kandir\b", "87", tipo="lc"),
    Norma("eleicoes", "a Lei das Eleições", f"{PLANALTO}/leis/l9504.htm", r"\blei das eleicoes\b|\blei eleitoral\b",
          "9504"),
    Norma("inelegibilidade", "a Lei das Inelegibilidades (LC 64)", f"{PLANALTO}/leis/lcp/lcp64.htm",
          r"\blei das inelegibilidades\b|\blei da ficha limpa\b|\bficha limpa\b", "64", tipo="lc"),
    Norma("partidos", "a Lei dos Partidos Políticos", f"{PLANALTO}/leis/l9096.htm", r"\blei dos partidos(?: politicos)?\b",
          "9096"),
    Norma("meio_ambiente", "a Política Nacional do Meio Ambiente", f"{PLANALTO}/leis/l6938.htm",
          r"\bpolitica nacional do meio ambiente\b|\bpnma\b", "6938"),
    Norma("snuc", "a Lei do SNUC", f"{PLANALTO}/leis/l9985.htm", r"\bsnuc\b|\bunidades de conservacao\b", "9985"),
    Norma("residuos", "a Política Nacional de Resíduos Sólidos", f"{PLANALTO}/_ato2007-2010/2010/lei/l12305.htm",
          r"\bresiduos solidos\b|\bpnrs\b", "12305"),
    Norma("sus", "a Lei do SUS", f"{PLANALTO}/leis/l8080.htm", r"\blei do sus\b|\blei organica da saude\b", "8080"),
    Norma("ldb", "a LDB", f"{PLANALTO}/leis/l9394.htm", r"\bldb\b|\blei de diretrizes e bases(?: da educacao)?\b",
          "9394"),
]
NORMA_CF = next(n for n in NORMAS if n.chave == "cf")
NORMA_ADCT = next(n for n in NORMAS if n.chave == "adct")

# Abordagens das explicações, uma diferente a cada "não entendi".
ESTRATEGIAS = [
    "de forma simples e direta, com um exemplo do dia a dia",
    "com uma comparação com uma situação comum e bem concreta, diferente do exemplo anterior",
    "contando uma historinha curta com personagens (por exemplo, a Ana e o vizinho), mostrando a regra acontecendo",
    "passo a passo, em partes bem curtas, uma ideia por frase",
    "como se explicasse para uma criança de 10 anos, com palavras bem simples",
    "respondendo, em ordem, a quem a regra vale, o que ela manda, quando vale e o que acontece se não cumprir",
]

ORDINAIS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5, "sexto": 6, "setimo": 7,
            "oitavo": 8, "nono": 9, "decimo": 10}
RE_ARTIGO = re.compile(
    r"\b(?:artigo|art)\.?\s*(?:n(?:umero|o|º)?\.?\s*)?"
    r"(\d{1,2}\.\d{3}|\d{1,4}|" + "|".join(ORDINAIS) + r")(?:\s*[ºo°])?(?:\s*-\s*([a-z])\b)?"
)
RE_INCISO = re.compile(r"\binciso\s+([ivxlc]+|\d{1,3})\b")
RE_PARAGRAFO = re.compile(r"\bparagrafo\s+(unico|\d{1,2}|" + "|".join(ORDINAIS) + r")\b")
RE_LEI_NUMERO = re.compile(r"\b(lei complementar|lc|lei|decreto(?:[- ]lei)?)\s*(?:n(?:umero|o|º)?\.?\s*)?"
                           r"(\d{1,2}\.?\d{3}|\d{2,3})(?:\s*(?:/|de)\s*(\d{4}|\d{2}))?\b")
RE_LER = re.compile(r"\b(?:dit[ae]\w*|le|leia|ler|lendo|recit\w*|qual (?:e )?o texto|fala o texto|me fala o texto)\b")
RE_EXPLICAR = re.compile(
    r"\b(?:explic\w*|do que (?:se )?trata\w*|o que (?:diz|fala|significa|quer dizer|determina|garante)|resum\w*|"
    r"me ajuda a entender|entender|sobre o que e)\b"
)
RE_CRIAR = re.compile(r"\b(?:faz|faca|fazer|cria|crie|gera|gere|monta|monte|prepara|prepare)\s+(?:um|uma|me um|me uma)\b")
RE_REEXPLICAR = re.compile(
    r"\b(?:nao entendi|nao ficou claro|nao to entendendo|nao estou entendendo|ainda (?:nao|to sem) entend\w*|"
    r"como assim|de outr[oa] (?:jeito|forma|maneira)|de (?:um )?(?:jeito|forma|modo) diferente|"
    r"(?:explica|fala|diz)\w* (?:de novo|melhor|outra vez|diferente|mais simples|com (?:um )?exemplo)|"
    r"simplifica\w*|(?:me )?da um exemplo|tenta de novo)\b"
)
FRASES_DE_NOVO = ["Tranquilo, vou tentar de outro jeito.", "Sem problema, deixa eu explicar diferente.",
                  "Bora de novo, agora de um jeito mais fácil."]
FRASES_INICIO = ["Deixa comigo: {rotulo}.", "Bora lá, {rotulo}.", "Olha só, {rotulo}."]
RE_CONTINUAR = re.compile(r"^(?:continua\w*|pode continuar|segue|siga|le o resto|e o resto|resto|mais|"
                          r"continua lendo|termina de ler|sim|pode)\b")
RE_REPETIR = re.compile(r"\b(?:le|leia|dita|repete|repita) (?:de novo|outra vez|novamente)\b|\brepete o (?:artigo|texto)\b")
# Súmulas, OJs e precedentes normativos ("súmula 7 do STJ", "súmula vinculante 11", "OJ 191 da SDI-1").
_NUMERO = r"\s*(?:n(?:umero|o|º)?\.?\s*)?"
RE_SUMULA = re.compile(rf"\b(?:(?:sumulas? vinculantes?|sv){_NUMERO}(?P<nv>\d{{1,4}})"
                       rf"|(?:ojs?|orientac\w* jurisprudenc\w*)(?: transitori\w*)?{_NUMERO}(?P<no>\d{{1,4}})"
                       rf"|(?:precedentes? normativos?|pn){_NUMERO}(?P<np>\d{{1,4}})"
                       rf"|sumulas?{_NUMERO}(?P<ns>\d{{1,4}}))\b")
RE_TRIBUNAL = re.compile(r"\b(?P<stf>stf|supremo(?: tribunal federal)?)\b|\b(?P<stj>stj|superior tribunal de justica)\b|"
                         r"\b(?P<tst>tst|tribunal superior do trabalho)\b")
RE_ORGAO_OJ = re.compile(r"\bsb?di\s*-?\s*(?P<n>1|2|ii|i|um|dois)\b|\b(?P<sdc>sdc)\b|\b(?P<pleno>pleno|tribunal pleno)\b")
RE_EXEMPLO = re.compile(r"\b(?:exemplos?|caso pratico|casos praticos|caso concreto|casos concretos|na pratica|caso real|"
                        r"casos reais|julgados|como (?:o \w+ |os tribunais |a justica )?(?:aplic|us)a\w*)\b")
RE_OUTRO_EXEMPLO = re.compile(r"\b(?:outro|mais um|novo) (?:exemplo|caso)\b|\boutros exemplos\b")
RE_SITUACAO = re.compile(r"\b(?:ainda (?:vale|esta valendo|e valida|esta em vigor)|esta (?:cancelad|revogad|superad)\w*|"
                         r"foi (?:cancelad|revogad|superad)\w*|continua valendo|esta valendo)\b")
# Busca pelo assunto: "tem súmula sobre dano moral?", "qual artigo do Código Civil fala de usucapião?",
# "procura no vade-mécum legítima defesa".
_SOBRE = (r"(?:sobre|de|do|da|dos|das|(?:que )?(?:fal|trat|diz|cuid)\w*(?: de| do| da| dos| das| sobre)?|a respeito d\w+|"
          r"acerca d\w+|para|pra)")
RE_BUSCA_SUMULA = re.compile(
    r"\b(?:tem|tenho|existe|existem|ha|qual|quais|alguma|algum|procura|busca|pesquisa|acha|lista|me (?:fala|diz|mostra|da)\w*)\b"
    r".{0,25}?\b(?:sumulas?|sumulas? vinculantes?|ojs?|orientac\w* jurisprudenc\w*|jurisprudencia)\b"
    r"(?:\s+(?:vinculantes?|do (?:stf|stj|tst|supremo)|no (?:stf|stj|tst)))*\s+" + _SOBRE + r"\s+(?P<assunto>.{3,})")
RE_BUSCA_ARTIGO = re.compile(
    r"\b(?:qual|quais|que|em que|onde|procura|busca|acha|tem algum)\b.{0,20}?\b(?:artigos?|dispositivos?)\b(?P<onde>.{0,60}?)"
    r"\b(?:fala\w*|trata\w*|preve\w*|define\w*|regula\w*|diz\w*|cuida\w*|disciplina\w*|esta|estao)\s+" + _SOBRE
    + r"?\s*(?P<assunto>.{3,})")
RE_BUSCA_VADE = re.compile(r"\b(?:procura|busca|pesquisa|acha|o que (?:tem|diz))\w*\b.{0,15}?\bvade ?-?mecum\b\s*"
                           + _SOBRE + r"?\s*(?P<assunto>.{3,})")
RE_VADE_MECUM = re.compile(r"\bvade ?-?mecum\b")
RE_ATUALIZAR_VADE = re.compile(r"\b(?:atualiz|baix|sincroniz|renov|carreg)\w*\b")
RE_ESCOLHA = re.compile(r"\b(?:a |o )?(?P<n>primeir|segund|terceir|quart|quint|sext|setim|oitav|ultim)[oa]\b")
POSICOES = {"primeir": 0, "segund": 1, "terceir": 2, "quart": 3, "quint": 4, "sext": 5, "setim": 6, "oitav": 7, "ultim": -1}
SEGUNDOS_DA_BUSCA = 5 * 60
SITUACOES_FALADAS = {"cancelada": "foi cancelada", "revogada": "foi revogada", "superada": "foi superada",
                     "alterada": "teve a redação alterada"}
COMO_CHAMAR = {"sumula": "essa súmula", "vinculante": "essa súmula", "oj": "essa OJ", "pn": "esse precedente"}


def situacao_dita(sumula: dict) -> str:
    """"foi cancelada" ("foi cancelado" no precedente normativo), ou "" se ela vale como está."""
    situacao = SITUACOES_FALADAS.get(sumula.get("situacao") or "", "")
    if sumula.get("tipo") == "pn" and situacao.endswith("ada"):
        situacao = situacao[:-1] + "o"
    return situacao
RE_ANOTACAO = re.compile(
    r"\s*\((?:Reda[çc][ãa]o dada|Inclu[íi]d[oa]|Vide|Revogad[oa]|Regulamento|Vig[êe]ncia|Produ[çc][ãa]o de efeito|"
    r"Renumerad[oa]|Acrescid[oa]|Alterad[oa]|Promulga[çc][ãa]o|Express[ãa]o)[^)]*\)", re.IGNORECASE
)
ROMANOS = [(100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


@dataclass
class RespostaLei:
    leitura: str = ""  # o que falar agora (o texto da lei, ou uma pergunta)
    painel: str = ""  # o texto completo para mostrar
    explicar: object = None  # função sem argumentos que devolve a explicação (chama a IA)
    titulo: str = ""


@dataclass
class Assunto:
    norma: Norma | None  # None numa súmula
    rotulo: str  # "o artigo 49 do Código de Defesa do Consumidor", "a Súmula 7 do STJ"
    texto: list  # linhas do trecho (com as anotações de redação)
    pedido: object
    foco: str = ""
    explicacoes: list = field(default_factory=list)
    lido_ate: int = 0  # quantas linhas já foram lidas
    quando: float = 0.0
    fonte: str = "site do Planalto"
    chave: str = ""  # onde o exemplo fica guardado no vade-mécum ("artigo:cp:121", "sumula:stj:sumula::7")
    julgados: list = field(default_factory=list)  # casos reais que o tribunal pôs na súmula (STF)
    relacionadas: list = field(default_factory=list)  # as súmulas que citam o artigo
    exemplos: list = field(default_factory=list)


@dataclass
class Busca:
    assunto: str
    resultados: list
    quando: float


@dataclass
class Pendencia:
    tipo: str  # qual_lei | qual_tribunal
    dados: dict
    ate: float


# ------------------------------------------------------------------ texto da lei
def linhas_do_html(conteudo: bytes) -> list[str]:
    """Linhas de texto da página, sem o que está riscado (revogado) e sem as tags.

    O HTML do Planalto é malformado (<b><p></b>...), então vai por expressões regulares em vez de
    árvore. As páginas antigas dizem ISO-8859-1 mas são Windows-1252 (travessão, aspas).
    """
    if conteudo[:2] in (b"\xff\xfe", b"\xfe\xff"):  # algumas páginas (a da Lei Maria da Penha) vêm em UTF-16
        texto = conteudo.decode("utf-16", "replace")
    else:
        try:
            texto = conteudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = conteudo.decode("cp1252", "replace")
    texto = re.sub(r"(?is)<(strike|del)\b[^>]*>.*?</\1\s*>", " ", texto)
    texto = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)  # as quebras de linha do HTML caem no meio das frases
    texto = re.sub(r"(?i)<br\s*/?>|</p\s*>|</h\d\s*>|</div\s*>|</tr\s*>|</li\s*>|</table\s*>", "\n", texto)
    texto = html_util.unescape(re.sub(r"(?s)<[^>]+>", " ", texto))
    # Páginas antigas escrevem o ordinal com um "o" sobrescrito à parte: "§ 1 o" / "Art. 2 o" -> "§ 1º".
    texto = re.sub(r"((?:§|Art\.?)\s*\d+)\s+o\b", r"\1º", texto)
    linhas = [linha for linha in (re.sub(r"\s+", " ", parte).strip() for parte in texto.split("\n")) if linha]
    return [_artigo_padronizado(linha) for linha in linhas]


def _artigo_padronizado(linha: str) -> str:
    """"Art . 1º" (com espaço antes do ponto, nas páginas antigas, como a da Lei do Cheque) vira "Art. 1º"."""
    return re.sub(r"^Art\s+\.\s*", "Art. ", linha)


def _numero_do_artigo(numero: str, letra: str = "") -> str:
    numero = str(numero).replace(".", "")
    if len(numero) > 3:
        numero = f"{numero[:-3]}.{numero[-3:]}"  # 1228 -> 1.228, como no texto da lei
    return f"{numero}-{letra.upper()}" if letra else numero


def trecho_do_artigo(linhas: list[str], numero: str) -> list[str] | None:
    """As linhas do artigo (do "Art. 5º" até o próximo artigo ou título), ou None."""
    inicio = re.compile(rf"^Art\.?\s*{re.escape(numero)}(?!\d)(?!-[A-Z]\b)\s*(?:º|°|o\b|\.|-|–|—|\s)")
    proximo = re.compile(r"^(?:Art\.?\s*\d|CAP[IÍ]TULO|T[IÍ]TULO|SE[ÇC][ÃA]O\b|Se[çc][ãa]o\s+[IVXL]|SUBSE[ÇC][ÃA]O|LIVRO|"
                         r"PARTE\s+(?:GERAL|ESPECIAL)|ATO DAS DISPOSI)")
    i = next((i for i, linha in enumerate(linhas) if inicio.match(linha)), None)
    if i is None:
        return None
    fim = next((j for j in range(i + 1, len(linhas)) if proximo.match(linhas[j])), len(linhas))
    return linhas[i:min(fim, i + 400)]


def inicio_da_lei(linhas: list[str], limite: int = 3500) -> list[str]:
    """Ementa e primeiros artigos (para "do que se trata a LGPD?")."""
    i = next((i for i, linha in enumerate(linhas) if re.match(r"^(?:Art\.?\s*1\s*(?:º|°|o\b|\.))", linha)), None)
    if i is None:
        return linhas[:40]
    comeco = max(0, i - 6)  # a ementa ("Dispõe sobre...") vem logo antes do art. 1º
    saida, total = [], 0
    for linha in linhas[comeco:]:
        saida.append(linha)
        total += len(linha)
        if total > limite:
            break
    return saida


def _romano(numero: int) -> str:
    saida = ""
    for valor, letras in ROMANOS:
        while numero >= valor:
            saida, numero = saida + letras, numero - valor
    return saida


def trecho_do_inciso(linhas: list[str], inciso: str) -> list[str] | None:
    romano = (_romano(int(inciso)) if inciso.isdigit() else inciso).upper()
    i = next((i for i, linha in enumerate(linhas) if re.match(rf"^{romano}\s*[-–—]", linha)), None)
    if i is None:
        return None
    fim = next((j for j in range(i + 1, len(linhas)) if re.match(r"^(?:[IVXLC]+\s*[-–—]|§|Parágrafo único)", linhas[j])),
               len(linhas))
    return linhas[i:fim]


def sem_anotacoes(texto: str) -> str:
    return re.sub(r"\s{2,}", " ", RE_ANOTACAO.sub("", texto)).strip()


def texto_para_falar(linhas: list[str]) -> str:
    """O texto da lei como se fala: sem as notas de redação, "Art." vira "Artigo", "§ 1º" vira "Parágrafo 1º"."""
    falas = []
    for linha in linhas:
        linha = sem_anotacoes(linha)
        if not linha:
            continue
        linha = re.sub(r"^Art\.?\s*", "Artigo ", linha)
        linha = re.sub(r"§§", "Parágrafos", linha)
        linha = re.sub(r"§\s*", "Parágrafo ", linha)
        linha = re.sub(r"^([IVXLC]+)\s*[-–—]\s*", lambda m: f"Inciso {_valor_romano(m.group(1))}: ", linha)
        linha = re.sub(r"^([a-z])\)\s*", r"Alínea \1: ", linha)
        linha = re.sub(r"\bn[º°]\s*", "número ", linha)
        linha = re.sub(r"\bPena\s*[-–—]\s*", "Pena, ", linha)
        falas.append(linha if linha.endswith((".", ":", ";", "?", "!", ",")) else linha + ".")
    return " ".join(falas)


def _valor_romano(romano: str) -> int:
    valores = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for atual, seguinte in zip(romano, romano[1:] + " "):
        valor = valores[atual]
        total += -valor if seguinte in valores and valores[seguinte] > valor else valor
    return total


class TextoDasLeis:
    """Baixa e guarda (por 6 h) as páginas do Planalto, já em linhas."""

    def __init__(self, baixar=None):
        self._baixar = baixar or self._baixar_da_internet
        self._cache: dict[str, tuple[float, list]] = {}
        self._trava = threading.Lock()

    @staticmethod
    def _baixar_da_internet(url: str) -> bytes:
        import requests

        resposta = requests.get(url, headers=CABECALHOS, timeout=20)
        resposta.raise_for_status()
        return resposta.content

    def linhas(self, url: str) -> list[str]:
        with self._trava:
            guardado = self._cache.get(url)
            if guardado and time.time() - guardado[0] < SEGUNDOS_CACHE:
                return guardado[1]
        linhas = linhas_do_html(self._baixar(url))
        with self._trava:
            self._cache[url] = (time.time(), linhas)
        return linhas


# Leis conhecidas (número, ano): o número cresce com o tempo, então dá para estimar o ano de outra lei.
# (A busca do Bing devolvia resultados genéricos para buscas automáticas; o Planalto responde 404 rápido.)
MARCOS_DAS_LEIS = [(8078, 1990), (9099, 1995), (10406, 2002), (10741, 2003), (11340, 2006), (12965, 2014),
                   (13105, 2015), (13709, 2018), (14133, 2021), (14181, 2021), (15191, 2025), (15388, 2026)]
# Pastas do Planalto para as leis a partir de 2004 (_ato2019-2022/2021/lei/l14181.htm).
PERIODOS_PLANALTO = [(2004, 2006), (2007, 2010), (2011, 2014), (2015, 2018), (2019, 2022), (2023, 2026),
                     (2027, 2030), (2031, 2034)]


def ano_estimado(numero: int) -> int:
    for (n1, a1), (n2, a2) in zip(MARCOS_DAS_LEIS, MARCOS_DAS_LEIS[1:]):
        if n1 <= numero <= n2:
            return round(a1 + (a2 - a1) * (numero - n1) / (n2 - n1))
    if numero < MARCOS_DAS_LEIS[0][0]:
        return 1990
    n, a = MARCOS_DAS_LEIS[-1]
    return a + (numero - n) // 400  # ~400 leis por ano nos últimos anos


def candidatos_planalto(numero: str, ano: str = "", tipo: str = "lei") -> list[str]:
    """Endereços prováveis da norma no Planalto, do mais para o menos provável."""
    n = int(numero)
    if tipo == "decreto":  # os antigos ficam em decreto/antigos (o 20.910/1932, da prescrição contra a Fazenda)
        return [f"{PLANALTO}/decreto/antigos/d{n}.htm", f"{PLANALTO}/decreto/d{n}.htm", f"{PLANALTO}/decreto/D{n}.htm"]
    if tipo == "decreto-lei":  # os de três algarismos com zero na frente: del0201.htm
        return [f"{PLANALTO}/decreto-lei/del{n:04d}compilado.htm", f"{PLANALTO}/decreto-lei/del{n:04d}.htm",
                f"{PLANALTO}/decreto-lei/del{n}compilado.htm", f"{PLANALTO}/decreto-lei/del{n}.htm"][0 if n < 1000 else 2:]
    if tipo == "lc":
        return [f"{PLANALTO}/leis/lcp/lcp{n}compilado.htm", f"{PLANALTO}/leis/lcp/lcp{n}.htm",
                f"{PLANALTO}/leis/lcp/Lcp{n}.htm"]
    if n < 10400:  # até 2001, numa pasta só (a 8.112 é "l8112cons.htm"); as de 2001 também em leis_2001
        return ([f"{PLANALTO}/leis/l{n}compilado.htm", f"{PLANALTO}/leis/l{n}cons.htm", f"{PLANALTO}/leis/l{n}.htm"]
                + ([f"{PLANALTO}/leis/leis_2001/l{n}.htm"] if n >= 10100 else []))
        return [f"{PLANALTO}/leis/l{n}compilado.htm", f"{PLANALTO}/leis/l{n}cons.htm", f"{PLANALTO}/leis/l{n}.htm"]
    estimado = int(ano) if ano else ano_estimado(n)
    anos = [estimado] if ano else [estimado, estimado + 1, estimado - 1, estimado + 2, estimado - 2]
    urls = []
    for a in anos:
        if a <= 2003:
            urls += [f"{PLANALTO}/leis/{a}/l{n}.htm", f"{PLANALTO}/leis/{a}/l{n // 1000}.{n % 1000:03d}.htm"]
            if a <= 2001:
                urls.append(f"{PLANALTO}/leis/leis_{a}/l{n}.htm")
        else:
            periodo = next((f"_ato{i}-{f}" for i, f in PERIODOS_PLANALTO if i <= a <= f), None)
            if periodo:
                urls.append(f"{PLANALTO}/{periodo}/{a}/lei/l{n}.htm")
    return urls


# ---------------------------------------------------------------- pedido falado
@dataclass
class PedidoLei:
    norma: Norma | None
    artigo: str = ""  # "5", "1.228", "121-A"; vazio = a lei inteira
    inciso: str = ""
    paragrafo: str = ""
    ler: bool = False
    explicar: bool = False
    exemplo: bool = False  # "me dá um exemplo do artigo 121 do Código Penal"


NOMES_DOS_TIPOS = {"decreto-lei": "o Decreto-Lei", "decreto": "o Decreto", "lc": "a Lei Complementar"}


def _ano_completo(ano: str) -> str:
    """"90" -> "1990", "21" -> "2021" (o ano das citações "Lei 8.112/90")."""
    if len(ano) != 2:
        return ano
    return f"20{ano}" if int(ano) <= time.localtime().tm_year % 100 else f"19{ano}"


def _norma_pelo_numero(lei_numero: re.Match, normas=NORMAS) -> Norma | None:
    """A norma de "lei 13.709" (do catálogo, se estiver nele; senão, uma achada pelo endereço provável)."""
    numero = lei_numero.group(2).replace(".", "")
    ano = _ano_completo(lei_numero.group(3) or "")
    palavra = lei_numero.group(1)
    if palavra in ("lc", "lei complementar"):
        tipo = "lc"
    elif palavra.startswith("decreto"):
        tipo = "decreto-lei" if palavra.endswith("lei") else "decreto"
    else:
        tipo = "lei"
    no_catalogo = next((n for n in normas if n.numero == numero and (n.tipo == "lc") == (tipo == "lc")), None)
    if no_catalogo:
        return no_catalogo
    if len(numero) < 4 and tipo == "lei":
        return None  # "lei 13": um número curto solto não é citação de lei
    nome = f"{NOMES_DOS_TIPOS.get(tipo, 'a Lei')} {_numero_do_artigo(numero)}"
    return Norma(f"{tipo}_{numero}", nome + (f", de {ano}" if ano else ""), "", "", numero, ano, tipo)


def norma_citada(texto: str, normas=NORMAS) -> Norma | None:
    """A norma do catálogo citada pelo nome no texto (já sem acentos). Com várias, a do trecho mais longo:
    "código de processo penal militar" é o CPPM, não o CPP nem o Código Penal."""
    melhor, tamanho = None, 0
    for norma in normas:
        if not norma.apelidos:
            continue
        for achado in re.finditer(norma.apelidos, texto):
            if len(achado.group(0)) > tamanho:
                melhor, tamanho = norma, len(achado.group(0))
    return melhor


def norma_por_texto(texto: str, normas=NORMAS) -> Norma | None:
    """A norma citada num texto qualquer ("CPC", "CF/88", "Lei 8.112/90"), ou None."""
    _, t = preparar(texto or "")
    norma = norma_citada(t, normas)
    if norma:
        return norma
    achado = RE_LEI_NUMERO.search(t)
    return _norma_pelo_numero(achado, normas) if achado else None


def texto_oficial_citado(frase: str, textos=None) -> tuple[str, str] | None:
    """('o artigo 500 do Código Civil', texto oficial) quando a frase cita um artigo de uma lei achável; senão None.

    Usado pela ficha de estudo ("faz um resumo do artigo 500 do Código Civil"), para partir da lei de verdade.
    """
    _, t = preparar(frase or "")
    artigo, norma = RE_ARTIGO.search(t), norma_por_texto(frase)
    if not (artigo and norma):
        return None
    numero = _numero_do_artigo(ORDINAIS.get(artigo.group(1), artigo.group(1)), artigo.group(2) or "")
    agente = AgenteLeis(textos=textos or TextoDasLeis(), explicador=lambda *_: "")
    trecho = trecho_do_artigo(agente._linhas(norma), numero)
    if not trecho:
        return None
    return f"o artigo {numero} {_de(norma.nome)}", "\n".join(sem_anotacoes(linha) for linha in trecho)


def interpretar(texto: str, normas=NORMAS) -> PedidoLei | None:
    """O pedido sobre uma lei (texto já sem acentos), ou None se não for isso."""
    if RE_CRIAR.search(texto):
        return None  # "faz um PDF sobre o artigo 5": fica com os agentes
    artigo = RE_ARTIGO.search(texto)
    norma = norma_citada(texto, normas)
    lei_numero = RE_LEI_NUMERO.search(texto)
    if norma is None and lei_numero:
        norma = _norma_pelo_numero(lei_numero, normas)
    if not artigo and norma is None:
        return None
    if not artigo and not (RE_LER.search(texto) or RE_EXPLICAR.search(texto)):
        return None  # cita a lei de passagem ("a CLT é antiga")
    pedido = PedidoLei(norma=norma)
    if artigo:
        bruto = artigo.group(1)
        pedido.artigo = _numero_do_artigo(ORDINAIS.get(bruto, bruto), artigo.group(2) or "")
    inciso, paragrafo = RE_INCISO.search(texto), RE_PARAGRAFO.search(texto)
    pedido.inciso = inciso.group(1) if inciso else ""
    if paragrafo:
        valor = paragrafo.group(1)
        pedido.paragrafo = "único" if valor == "unico" else str(ORDINAIS.get(valor, valor))
    # "O que diz o artigo 49?" pede o texto; "o que diz a LGPD?" (a lei inteira) pede só a explicação.
    pedido.ler = bool(RE_LER.search(texto)) or (bool(artigo) and bool(re.search(r"\bo que (?:diz|fala)\b", texto)))
    pedido.explicar = bool(RE_EXPLICAR.search(texto)) or not pedido.ler
    if pedido.ler and not RE_EXPLICAR.search(texto):
        pedido.explicar = True  # "dita o artigo": lê e, depois, explica
    pedido.exemplo = bool(RE_EXEMPLO.search(texto))
    return pedido


def pedido_de_sumula(texto: str) -> dict | None:
    """{"tribunal", "tipo", "numero", "orgao"} de "súmula 7 do STJ", "SV 11", "OJ 191 da SDI-1" (texto sem acentos)."""
    achado = RE_SUMULA.search(texto)
    if not achado or RE_CRIAR.search(texto):
        return None
    tipo, numero = next((t, achado.group(g)) for t, g in (("vinculante", "nv"), ("oj", "no"), ("pn", "np"), ("sumula", "ns"))
                        if achado.group(g))
    tribunal = tribunal_citado(texto)
    if tipo == "vinculante":
        tribunal = "stf"
    elif tipo in ("oj", "pn"):
        tribunal = "tst"
    orgao = ""
    if tipo == "oj":
        sigla = RE_ORGAO_OJ.search(texto)
        if sigla and sigla.group("sdc"):
            orgao = "SDC"
        elif sigla and sigla.group("pleno"):
            orgao = "TP"
        elif sigla and sigla.group("n"):
            orgao = "SDI2" if sigla.group("n") in ("2", "ii", "dois") else "SDI1"
        if re.search(r"\btransitori\w*", texto):  # as transitórias da SDI-1 têm numeração própria
            orgao = "SDI1T"
    return {"tribunal": tribunal, "tipo": tipo, "numero": int(numero), "orgao": orgao}


def tribunal_citado(texto: str) -> str:
    achado = RE_TRIBUNAL.search(texto)
    return next((g for g in ("stf", "stj", "tst") if achado.group(g)), "") if achado else ""


def pedido_de_busca(texto: str) -> dict | None:
    """{"assunto", "tipos", "tribunal", "norma"} de "tem súmula sobre dano moral?", "qual artigo do CC fala de
    usucapião?" e "procura no vade-mécum legítima defesa" (texto sem acentos), ou None."""
    if RE_CRIAR.search(texto) or RE_ARTIGO.search(texto) or RE_SUMULA.search(texto):
        return None  # com número é o pedido de um artigo ou de uma súmula
    for expressao, tipos in ((RE_BUSCA_SUMULA, ("sumulas",)), (RE_BUSCA_ARTIGO, ("artigos",)),
                             (RE_BUSCA_VADE, ("sumulas", "artigos"))):
        achado = expressao.search(texto)
        if not achado:
            continue
        assunto = re.sub(r"\b(?:no|na|do|da) (?:stf|stj|tst|supremo|vade ?-?mecum)\b|[?!.]+$", "", achado.group("assunto"))
        assunto = re.sub(r"^(?:o|a|os|as|um|uma)\s+", "", assunto.strip()).strip(" ,")
        if len(assunto) < 3:
            return None
        onde = achado.groupdict().get("onde") or ""
        norma = norma_citada(onde) if onde else None
        if norma is None and "artigos" in tipos:
            norma = norma_citada(assunto)  # "qual artigo fala de usucapião no código civil?"
            if norma:
                assunto = re.sub(norma.apelidos, "", assunto).strip(" ,")
                assunto = re.sub(r"\s+(?:no|na|do|da|em)$", "", assunto).strip()
        return {"assunto": assunto, "tipos": tipos, "tribunal": tribunal_citado(texto), "norma": norma}
    return None


# ------------------------------------------------------------------- o agente
class AgenteLeis:
    def __init__(self, textos: TextoDasLeis | None = None, explicador=None, candidatos=candidatos_planalto,
                 relogio=time.time, vade=None, avisar=None):
        self.textos = textos or TextoDasLeis()
        self.explicador = explicador or explicar_com_ia  # função(prompt, contexto) -> texto
        self.candidatos = candidatos  # endereços prováveis das leis fora do catálogo
        self.relogio = relogio
        self.vade = vade  # o vade-mécum do PC (None = o de CAMINHO_VADE_MECUM)
        self.avisar = avisar  # fim da atualização do vade-mécum
        self.assunto: Assunto | None = None
        self.pendencia: Pendencia | None = None
        self.busca: Busca | None = None
        # Outra conversa depois da explicação: "não entendi" passa a ser sobre ela, não sobre a lei.
        self._interrompido = False
        self._trava = threading.RLock()

    def e_seguimento(self, texto: str) -> bool:
        """'Não entendi', 'continua', 'me dá um exemplo', 'a segunda'... sobre o assunto de agora (para aceitar sem
        o nome no meio da conversa)."""
        _, t = preparar(texto or "")
        t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", t).strip()
        if self._busca_ativa() and RE_ESCOLHA.search(t):
            return True
        if not self._assunto_ativo():
            return False
        falta_ler = self.assunto.lido_ate < len(self.assunto.texto)
        return bool(RE_REEXPLICAR.search(t) or (falta_ler and RE_CONTINUAR.match(t)) or RE_REPETIR.search(t)
                    or RE_EXEMPLO.search(t))

    def responder(self, texto: str) -> RespostaLei | None:
        if not (texto or "").strip():
            return None
        with self._trava:
            try:
                resposta = self._responder(texto)
                self._interrompido = resposta is None
                return resposta
            except Exception as exc:
                logger.exception("Falha no agente de leis")
                return RespostaLei(leitura=f"Não consegui pegar o texto da lei agora: {exc}")

    def _assunto_ativo(self) -> bool:
        return (bool(self.assunto) and not self._interrompido
                and self.relogio() - self.assunto.quando < SEGUNDOS_DO_ASSUNTO)

    def _busca_ativa(self) -> bool:
        return bool(self.busca) and not self._interrompido and self.relogio() - self.busca.quando < SEGUNDOS_DA_BUSCA

    def _vade(self):
        """O vade-mécum do PC, ou None se o banco não abrir (o agente segue pela internet)."""
        if self.vade is not None:
            return self.vade
        try:
            return vade_mecum.vade_padrao()
        except Exception as exc:
            logger.warning("Vade-mécum indisponível: %s", exc)
            return None

    def _responder(self, texto: str) -> RespostaLei | None:
        _, t = preparar(texto)
        t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", t).strip()
        if not t:
            return None

        if self.pendencia and self.pendencia.ate > self.relogio():
            pendencia, self.pendencia = self.pendencia, None
            if pendencia.tipo == "qual_lei":
                norma = norma_citada(t)
                if norma:
                    return self._atender(PedidoLei(norma=norma, **pendencia.dados))
            elif pendencia.tipo == "qual_tribunal":
                tribunal = tribunal_citado(t) or next((s for s in ("stf", "stj", "tst") if re.search(rf"\b{s}\b", t)), "")
                if tribunal:
                    return self._atender_sumula(dict(pendencia.dados, tribunal=tribunal), t)
        self.pendencia = None

        busca = pedido_de_busca(t)
        if busca:
            busca["assunto"] = _com_acentos(texto, busca["assunto"])  # para falar "usucapião", não "usucapiao"
            return self._buscar(busca)
        if RE_VADE_MECUM.search(t):
            return self._sobre_o_vade_mecum(t)

        sumula = pedido_de_sumula(t)
        if sumula:
            if not sumula["tribunal"]:
                self.pendencia = Pendencia("qual_tribunal", sumula, self.relogio() + 120)
                return RespostaLei(leitura=f"A Súmula {sumula['numero']} de qual tribunal? Do STF, do STJ ou do TST?")
            return self._atender_sumula(sumula, t)

        pedido = interpretar(t)
        if pedido:
            if pedido.norma is None:  # "explica o artigo 5": de qual lei?
                if self._assunto_ativo() and self.assunto.norma is not None:
                    pedido.norma = self.assunto.norma
                else:
                    self.pendencia = Pendencia("qual_lei", {k: v for k, v in vars(pedido).items() if k != "norma"},
                                               self.relogio() + 120)
                    return RespostaLei(leitura=f"O artigo {pedido.artigo} de qual lei? Da Constituição, do Código "
                                               "Penal, do Código Civil, da CLT...?")
            return self._atender(pedido)

        if self._busca_ativa():
            escolha = RE_ESCOLHA.search(t)
            if escolha:
                return self._escolher(escolha, t)
        if self._assunto_ativo():
            if RE_REPETIR.search(t):
                self.assunto.lido_ate = 0
                return self._ler(self.assunto)
            if RE_CONTINUAR.match(t) and self.assunto.lido_ate < len(self.assunto.texto):
                return self._ler(self.assunto)
            if RE_EXEMPLO.search(t) or RE_OUTRO_EXEMPLO.search(t):
                assunto = self.assunto
                return RespostaLei(leitura=random.choice(FRASES_EXEMPLO), titulo=assunto.rotulo,
                                   explicar=lambda: self._exemplo(assunto, pedido_agora=texto))
            if RE_REEXPLICAR.search(t):
                return self._reexplicar(texto)
        return None

    # ------------------------------------------------------------- atender
    def _linhas(self, norma: Norma) -> list[str]:
        """O texto da norma: do vade-mécum do PC (sem internet) e, se não estiver lá ou estiver velho, do Planalto
        (e aí fica guardado). Sem internet, vale o guardado, mesmo velho."""
        vade = self._vade()
        guardado = None
        if vade is not None:
            try:
                guardado = vade.linhas(norma.chave)
            except Exception as exc:
                logger.warning("Vade-mécum ilegível (%s): %s", norma.chave, exc)
        if guardado and self.relogio() - guardado[1] < _dias_do_vade() * 86400:
            return guardado[0]
        try:
            return self._baixar_linhas(norma, vade)
        except Exception:
            if guardado:
                return guardado[0]
            raise

    def _baixar_linhas(self, norma: Norma, vade) -> list[str]:
        base = NORMA_CF if norma.chave == "adct" else norma
        if base.url:
            url, linhas = base.url, self.textos.linhas(base.url)
        else:
            for url in self.candidatos(base.numero, base.ano, base.tipo):
                try:
                    linhas = self.textos.linhas(url)
                    break
                except Exception as exc:  # 404: tenta o próximo endereço provável
                    logger.debug("Lei fora do endereço %s: %s", url, exc)
            else:
                raise LookupError(f"não achei {norma.nome} no site do Planalto")
        guardar = [(base, linhas)]
        if base.chave == "cf":  # a página traz também o ADCT, que recomeça a contagem dos artigos
            constituicao, adct = vade_mecum.separar_constituicao(linhas)
            guardar = [(NORMA_CF, constituicao)] + ([(NORMA_ADCT, adct)] if adct else [])
            linhas = adct if norma.chave == "adct" else constituicao
            if not linhas:
                raise LookupError("não achei o ADCT na página da Constituição")
        if vade is not None:
            for parte, texto in guardar:
                try:
                    vade.guardar_norma(parte.chave, parte.nome, url, texto)
                except Exception as exc:
                    logger.warning("Não guardei %s no vade-mécum: %s", parte.nome, exc)
        return linhas

    def _atender(self, pedido: PedidoLei) -> RespostaLei:
        norma = pedido.norma
        linhas = self._linhas(norma)
        if pedido.artigo:
            trecho = trecho_do_artigo(linhas, pedido.artigo)
            if not trecho:
                return RespostaLei(leitura=f"Não achei o artigo {pedido.artigo} {_em(norma.nome)}. Confere o número pra mim?")
            rotulo = f"o artigo {pedido.artigo} {_de(norma.nome)}"
            foco = ""
            if pedido.inciso:
                inciso = trecho_do_inciso(trecho, pedido.inciso)
                if inciso:
                    rotulo = f"o inciso {pedido.inciso.upper()} do artigo {pedido.artigo} {_de(norma.nome)}"
                    foco = f"A pessoa perguntou especificamente do inciso {pedido.inciso.upper()}."
                    trecho = [trecho[0]] + inciso  # o começo do artigo dá o contexto
            elif pedido.paragrafo:
                foco = f"A pessoa perguntou especificamente do parágrafo {pedido.paragrafo}."
        else:
            trecho = inicio_da_lei(linhas)
            rotulo = norma.nome
            foco = "A pessoa quer saber do que trata a lei como um todo: explique o objetivo e as ideias principais."
        chave = f"artigo:{norma.chave}:{pedido.artigo}" if pedido.artigo else f"lei:{norma.chave}"
        if pedido.artigo and pedido.inciso:
            chave += f":{pedido.inciso}"
        relacionadas = self._sumulas_do_artigo(norma, pedido.artigo) if pedido.artigo else []
        self.assunto = assunto = Assunto(norma=norma, rotulo=rotulo, texto=trecho, pedido=pedido, foco=foco,
                                         quando=self.relogio(), chave=chave, relacionadas=relacionadas)
        acao = (lambda: self._exemplo(assunto)) if pedido.exemplo else (lambda: self._explicar(assunto))
        if pedido.ler:
            resposta = self._ler(assunto)
            if pedido.explicar or pedido.exemplo:
                resposta.explicar = acao
            resposta.painel += _painel_das_relacionadas(relacionadas)
            return resposta
        painel = "\n".join(trecho)
        frase_inicio = random.choice(FRASES_EXEMPLO if pedido.exemplo else FRASES_INICIO).format(rotulo=rotulo)
        return RespostaLei(leitura=frase_inicio,  # enquanto a IA escreve
                           painel=f"{_maiuscula(rotulo)} (texto oficial do Planalto):\n{painel}"
                                  + _painel_das_relacionadas(relacionadas),
                           explicar=acao, titulo=rotulo)

    def _sumulas_do_artigo(self, norma: Norma, numero: str, limite: int = 5) -> list[dict]:
        """As súmulas do vade-mécum que citam o artigo ("art. 65, III, d, do Código Penal")."""
        vade = self._vade()
        if vade is None or not numero:
            return []
        try:
            candidatas = vade.sumulas_que_citam(numero.replace(".", " ").replace("-", " "))
        except Exception as exc:
            logger.debug("Súmulas do artigo: %s", exc)
            return []
        citacao = re.compile(rf"\barts?\.?\s*(?:n[º°o.]?\s*)?(?:[\d.]+(?:-[A-Z])?[º°]?\s*(?:,|e)\s*)*"
                             rf"{re.escape(numero)}(?![\d.]*\d)(?!-[A-Za-z]\b)", re.IGNORECASE)
        achadas = []
        for sumula in candidatas:
            for citado in citacao.finditer(sumula["texto"] or ""):
                if _primeira_norma(sumula["texto"][citado.end():citado.end() + 140]) is norma:
                    achadas.append(sumula)
                    break
        achadas.sort(key=lambda s: (s["situacao"] not in ("vigente", ""), {"stf": 0, "stj": 1, "tst": 2}.get(s["tribunal"], 3),
                                    s["tipo"] != "vinculante"))
        return achadas[:limite]

    # ------------------------------------------------------------- súmulas
    def _atender_sumula(self, pedido: dict, t: str) -> RespostaLei:
        tribunal, tipo, numero, orgao = pedido["tribunal"], pedido["tipo"], pedido["numero"], pedido.get("orgao", "")
        vade = self._vade()
        sumula = None
        if vade is not None:
            try:
                sumula = vade.sumula(tribunal, tipo, numero, orgao)
            except Exception as exc:
                logger.warning("Vade-mécum ilegível (súmula): %s", exc)
        rotulo = vade_mecum.rotulo_da_sumula(tribunal, tipo, numero, (sumula or {}).get("orgao") or orgao)
        if not sumula:
            return RespostaLei(leitura=self._sumula_que_falta(vade, tribunal, tipo, rotulo))
        partes = _partes_da_sumula(sumula["texto"])
        assunto = Assunto(norma=None, rotulo=rotulo, texto=partes, pedido=pedido, quando=self.relogio(),
                          fonte=FONTES_DOS_TRIBUNAIS.get(tribunal, tribunal.upper()),
                          chave=f"sumula:{vade_mecum.chave_da_sumula(tribunal, tipo, numero, sumula['orgao'])}",
                          julgados=[j for j in sumula["julgados"] if j.get("texto")], foco=_foco_da_sumula(sumula))
        self.assunto = assunto
        situacao = situacao_dita(sumula)
        painel = _painel_da_sumula(rotulo, sumula)
        if RE_SITUACAO.search(t) and not RE_EXPLICAR.search(t) and not RE_EXEMPLO.search(t):
            if sumula["situacao"] == "alterada":
                fala = f"Sim, {rotulo} continua valendo, mas teve a redação alterada."
            else:
                fala = f"Não: {rotulo} {situacao}." if situacao else f"Sim, {rotulo} continua valendo."
            if situacao and sumula.get("observacao"):
                fala += f" {_resumo_falado(sumula['observacao'], 260)}"  # "convertida na Súmula 448"
            return RespostaLei(leitura=fala, painel=painel, titulo=rotulo)
        resposta = self._ler(assunto)
        resposta.leitura = resposta.leitura.replace(", segundo o texto oficial:", " diz:", 1)
        if situacao:
            resposta.leitura += f" Atenção: {COMO_CHAMAR.get(tipo, 'essa súmula')} {situacao}."
        resposta.painel = painel
        exemplo = bool(RE_EXEMPLO.search(t))
        resposta.explicar = (lambda: self._exemplo(assunto)) if exemplo else (lambda: self._explicar(assunto))
        return resposta

    def _sumula_que_falta(self, vade, tribunal: str, tipo: str, rotulo: str) -> str:
        nome = vade_mecum.NOMES_DOS_TRIBUNAIS.get(tribunal, tribunal.upper())
        total = 0
        if vade is not None:
            try:
                total = sum(n for chave, n in vade.total()["sumulas"].items() if chave.startswith(tribunal + "_"))
            except Exception:
                total = 0
        if total:
            return f"Não achei {rotulo} no vade-mécum. Confere o número pra mim?"
        if vade_mecum.atualizando():
            return f"Ainda estou baixando as súmulas do {nome} para o vade-mécum. Daqui a pouco eu acho."
        return (f"Ainda não tenho as súmulas do {nome} aqui no PC. Diga \"atualiza o vade-mécum\" que eu baixo "
                "(leva uns 15 minutos).")

    # ------------------------------------------------------------- busca
    def _buscar(self, pedido: dict) -> RespostaLei:
        vade = self._vade()
        assunto = pedido["assunto"]
        if vade is None:
            return RespostaLei(leitura="O vade-mécum do PC não abriu agora, então não consigo procurar por assunto.")
        norma = pedido["norma"]
        resultados = vade.buscar(assunto, limite=8, tipos=pedido["tipos"], tribunal=pedido["tribunal"],
                                 norma=norma.chave if norma else "")
        tipo = ("súmulas" if pedido["tipos"] == ("sumulas",) else "artigos" if pedido["tipos"] == ("artigos",)
                else "súmulas e artigos")
        onde = f" {_em(norma.nome)}" if norma else ""
        if not resultados:
            total = vade.total()
            if not total["normas"] and not total["sumulas"]:
                return RespostaLei(leitura="O vade-mécum ainda está vazio. Diga \"atualiza o vade-mécum\" que eu baixo "
                                           "as leis e as súmulas (leva uns 15 minutos).")
            return RespostaLei(leitura=f"Não achei {tipo}{onde} sobre {assunto} no vade-mécum.")
        self.busca = Busca(assunto, resultados, self.relogio())
        # "Primeira, a Súmula 37..." / "Primeiro, o artigo 25..." (o caput sem o "Art. 25 -" de novo)
        falados = [f"{_ORDINAIS_FALADOS[i][r['tipo_resultado'] == 'artigo']}, {r['rotulo']}: "
                   f"{_resumo_falado(re.sub(r'^Art[.]?[^A-Za-zÀ-ú]*', '', r['texto']))}" for i, r in enumerate(resultados[:3])]
        if len(resultados) == 1:
            tipo = "súmula" if resultados[0]["tipo_resultado"] == "sumula" else "artigo"
        so_artigos = all(r["tipo_resultado"] == "artigo" for r in resultados)
        convite = ("explica o primeiro, ou me dá um exemplo do segundo" if so_artigos
                   else "explica a primeira, ou me dá um exemplo da segunda")
        fala = (f"Achei {len(resultados)} {tipo}{onde} sobre {assunto}. " + " ".join(falados)
                + (f" Quer que eu explique algum? É só dizer: {convite}." if so_artigos
                   else f" Quer que eu explique alguma? É só dizer: {convite}."))
        painel = f"Vade-mécum: {tipo}{onde} sobre {assunto}\n\n" + "\n\n".join(
            f"{i + 1}. {_maiuscula(r['rotulo'])}{_situacao_curta(r)}\n{r['texto']}" for i, r in enumerate(resultados))
        return RespostaLei(leitura=fala, painel=painel, titulo=f"Vade-mécum: {assunto}")

    def _escolher(self, escolha: re.Match, t: str) -> RespostaLei:
        resultados = self.busca.resultados
        posicao = POSICOES[escolha.group("n")]
        if posicao >= len(resultados):
            return RespostaLei(leitura=f"Só achei {len(resultados)}. Qual delas?")
        item = resultados[posicao]
        if item["tipo_resultado"] == "sumula":
            pedido = {"tribunal": item["tribunal"], "tipo": item["tipo"], "numero": item["numero"], "orgao": item["orgao"]}
            return self._atender_sumula(pedido, t)
        norma = self._norma_da_chave(item["norma"])
        pedido = PedidoLei(norma=norma, artigo=item["numero"], ler=bool(RE_LER.search(t)), explicar=True,
                           exemplo=bool(RE_EXEMPLO.search(t)))
        return self._atender(pedido)

    def _norma_da_chave(self, chave: str) -> Norma:
        norma = next((n for n in NORMAS if n.chave == chave), None)
        if norma:
            return norma
        guardada = next((n for n in self._vade().normas() if n["chave"] == chave), None)
        return Norma(chave, guardada["nome"] if guardada else chave, guardada["url"] if guardada else "", "")

    # ------------------------------------------------------------- vade-mécum
    def _sobre_o_vade_mecum(self, t: str) -> RespostaLei:
        vade = self._vade()
        if vade is None:
            return RespostaLei(leitura="O vade-mécum do PC não abriu agora.")
        if RE_ATUALIZAR_VADE.search(t):
            if vade_mecum.atualizando():
                return RespostaLei(leitura="Já estou baixando o vade-mécum. Eu aviso quando terminar.")
            vade_mecum.atualizar_em_segundo_plano(vade, ao_terminar=self._ao_atualizar)
            return RespostaLei(leitura="Bora! Vou baixar o vade-mécum em segundo plano: as leis e os códigos do Planalto "
                                       "e as súmulas do STF, do STJ e do TST. Leva uns 15 minutos, e eu aviso quando "
                                       "terminar. Enquanto isso, pode pedir à vontade.")
        total = vade.total()
        if not total["normas"] and not total["sumulas"]:
            situacao = ("Estou baixando agora." if vade_mecum.atualizando()
                        else "Diga \"atualiza o vade-mécum\" que eu baixo as leis e as súmulas.")
            return RespostaLei(leitura=f"O vade-mécum ainda está vazio. {situacao}")
        feitas = vade.atualizacoes()
        quando = max((p["quando"] for p in feitas.values()), default=0)
        data = time.strftime("%d/%m", time.localtime(quando)) if quando else ""
        fala = f"No vade-mécum do PC tenho {vade_mecum.descrever_total(total)}."
        if data:
            fala += f" A última atualização foi em {data}."
        if vade_mecum.atualizando():
            fala += " E estou baixando a atualização agora."
        return RespostaLei(leitura=fala, painel=fala, titulo="Vade-mécum")

    def _ao_atualizar(self, resumo: dict) -> None:
        if not self.avisar:
            return
        erros = sum(len(p["erros"]) for p in resumo.values())
        total = vade_mecum.descrever_total(self._vade().total())
        texto = f"Pronto, o vade-mécum está atualizado: {total}."
        if erros:
            texto += f" {erros} {'item ficou' if erros == 1 else 'itens ficaram'} de fora; tento de novo na próxima."
        try:
            self.avisar(texto)
        except Exception as exc:
            logger.warning("Não avisei o fim do vade-mécum: %s", exc)

    def _ler(self, assunto: Assunto) -> RespostaLei:
        """O próximo pedaço do texto (até LIMITE_LEITURA caracteres, sem cortar uma linha)."""
        inicio = assunto.lido_ate
        fim, total = inicio, 0
        while fim < len(assunto.texto):
            tamanho = len(sem_anotacoes(assunto.texto[fim]))
            if fim > inicio and total + tamanho > LIMITE_LEITURA:
                break
            total += tamanho
            fim += 1
        assunto.lido_ate = fim
        assunto.quando = self.relogio()
        fala = texto_para_falar(assunto.texto[inicio:fim])
        if assunto.norma is None:  # nas súmulas do TST, "I -" é o item I, não um inciso
            fala = re.sub(r"\bInciso (\d+):", r"Item \1:", fala)
        if inicio == 0:
            fala = f"{_maiuscula(assunto.rotulo)}, segundo o texto oficial: {fala}"
        if fim < len(assunto.texto):
            fala += " Tem mais texto, que está todo no painel. Diga continua que eu sigo lendo."
        painel = "\n".join(assunto.texto) if inicio == 0 else "\n".join(assunto.texto[inicio:fim])
        return RespostaLei(leitura=fala, painel=painel, titulo=assunto.rotulo)

    # ------------------------------------------------------------ explicar
    def _prompt(self, assunto: Assunto, estrategia: str, pedido_agora: str = "") -> str:
        partes = self._base_do_prompt(assunto)
        if assunto.explicacoes:
            anteriores = "\n".join(f"- {e}" for e in assunto.explicacoes[-3:])
            partes.append(f"Você já explicou assim e a pessoa não entendeu:\n{anteriores}\n"
                          "Não repita essas frases nem esses exemplos.")
        if pedido_agora:
            partes.append(f'O que a pessoa disse agora: "{pedido_agora}"')
        partes.append(
            f"Explique {estrategia}. Use de 4 a 7 frases curtas, para serem faladas em voz alta: sem markdown, sem "
            "listas e sem juridiquês (se usar um termo técnico, explique). Diga o que a regra determina, para que "
            "serve e como isso aparece na vida real. Baseie-se só no texto acima; se ele não responder algo, diga "
            "que o texto não fala disso. Não leia o texto de novo, ele já foi lido."
        )
        return "\n\n".join(partes)

    def _explicar(self, assunto: Assunto, pedido_agora: str = "") -> str:
        estrategia = ESTRATEGIAS[len(assunto.explicacoes) % len(ESTRATEGIAS)]
        explicacao = self.explicador(self._prompt(assunto, estrategia, pedido_agora), CONTEXTO_EXPLICACAO).strip()
        with self._trava:
            assunto.explicacoes.append(explicacao)
            assunto.quando = self.relogio()
        return explicacao

    def _reexplicar(self, texto: str) -> RespostaLei:
        assunto = self.assunto
        aviso = random.choice(FRASES_DE_NOVO) if assunto.explicacoes else ""
        return RespostaLei(leitura=aviso, titulo=assunto.rotulo,
                           explicar=lambda: self._explicar(assunto, pedido_agora=texto))

    def _base_do_prompt(self, assunto: Assunto) -> list[str]:
        """O texto oficial, o foco e o que o vade-mécum tem em volta (as súmulas que citam o artigo, os casos reais)."""
        texto = "\n".join(sem_anotacoes(linha) for linha in assunto.texto)
        partes = [f"Texto oficial de {assunto.rotulo} (fonte: {assunto.fonte}):\n{texto[:12000]}"]
        if assunto.foco:
            partes.append(assunto.foco)
        if assunto.relacionadas:
            partes.append("Súmulas dos tribunais que citam este dispositivo (mostram como ele é aplicado; cite pelo número "
                          "se ajudar):\n" + "\n".join(f"- {_maiuscula(s['rotulo'])}{_situacao_curta(s)}: {s['texto'][:600]}"
                                                        for s in assunto.relacionadas))
        if assunto.julgados:
            partes.append("Casos reais em que o tribunal aplicou esta súmula (trechos das decisões):\n" + "\n".join(
                f"- {j['texto'][:900]} [{j['referencia']}]" for j in assunto.julgados[:2]))
        return partes

    # ------------------------------------------------------------ exemplos
    def _exemplo(self, assunto: Assunto, pedido_agora: str = "") -> str:
        """Um exemplo do assunto: primeiro os casos reais que o tribunal pôs na súmula, um por vez; depois o exemplo
        guardado no vade-mécum; depois um exemplo novo da IA (o primeiro fica guardado para a próxima vez)."""
        reais = assunto.julgados
        usados = sum(1 for e in assunto.exemplos if e.startswith(PREFIXO_CASO_REAL))
        if usados < len(reais):
            exemplo = self._caso_real(assunto, reais[usados])
        else:
            exemplo = ""
            vade = self._vade()
            if not assunto.exemplos and assunto.chave and vade is not None:
                try:
                    exemplo = vade.exemplo(assunto.chave)
                except Exception as exc:
                    logger.debug("Exemplo guardado: %s", exc)
            if not exemplo:
                exemplo = self.explicador(self._prompt_do_exemplo(assunto, pedido_agora), CONTEXTO_EXEMPLO).strip()
                if exemplo and not assunto.exemplos and assunto.chave and vade is not None:
                    try:
                        vade.guardar_exemplo(assunto.chave, exemplo)
                    except Exception as exc:
                        logger.debug("Não guardei o exemplo: %s", exc)
        with self._trava:
            assunto.exemplos.append(exemplo)
            assunto.quando = self.relogio()
        return exemplo

    def _caso_real(self, assunto: Assunto, julgado: dict) -> str:
        prompt = "\n\n".join([
            f"{_maiuscula(assunto.rotulo)} diz: {' '.join(assunto.texto)}",
            f"Trecho de uma decisão real do tribunal que aplicou essa súmula [{julgado['referencia']}]:\n"
            f"{julgado['texto'][:4000]}",
            "Conte esse caso real em 3 a 5 frases curtas, para serem faladas em voz alta, sem markdown e sem juridiquês: "
            "o que aconteceu, o que o tribunal decidiu e como isso mostra a súmula funcionando. Diga o processo e o ano "
            "(por exemplo: \"no Habeas Corpus 91.952, de 2008\"). Baseie-se só no trecho; não invente nomes nem fatos."])
        try:
            contado = self.explicador(prompt, CONTEXTO_EXEMPLO).strip()
        except Exception as exc:
            logger.warning("A IA não contou o caso real: %s", exc)
            contado = ""
        if not contado:  # sem IA: o trecho da decisão, como está
            contado = f"{_resumo_falado(julgado['texto'], 500)} Isso foi em {julgado['referencia']}"
        return f"{PREFIXO_CASO_REAL} {contado}"

    def _prompt_do_exemplo(self, assunto: Assunto, pedido_agora: str = "") -> str:
        partes = self._base_do_prompt(assunto)
        if assunto.exemplos:
            anteriores = "\n".join(f"- {e}" for e in assunto.exemplos[-3:])
            partes.append(f"Você já deu estes exemplos:\n{anteriores}\nFaça um diferente, com outra situação e outros nomes.")
        if pedido_agora:
            partes.append(f'O que a pessoa disse agora: "{pedido_agora}"')
        partes.append(
            "Dê um exemplo prático, hipotético, de como essa regra funciona na vida real: uma situação concreta com "
            "personagens de nomes comuns, o que a regra manda nesse caso e o resultado. De 4 a 6 frases curtas, para "
            "serem faladas em voz alta, sem markdown e sem juridiquês. Comece deixando claro que é um exemplo (\"Imagina "
            "que...\"). Se houver súmulas acima, pode mostrar como elas resolvem o caso. Baseie-se só no texto acima: "
            "não invente prazos, penas nem exceções que ele não tem.")
        return "\n\n".join(partes)


CONTEXTO_EXPLICACAO = (
    "Você está explicando um trecho de lei brasileira para alguém que não é da área, numa conversa falada, "
    "com o seu jeito leve e acolhedor. Seja fiel ao texto: não invente prazos, penas ou exceções."
)
CONTEXTO_EXEMPLO = (
    "Você está dando exemplos práticos de leis e súmulas brasileiras para alguém que estuda direito, numa conversa "
    "falada, com o seu jeito leve e acolhedor. Seja fiel ao texto: não invente prazos, penas, exceções nem decisões."
)
PREFIXO_CASO_REAL = "Um caso real:"
FRASES_EXEMPLO = ["Bora de exemplo!", "Deixa eu pensar num exemplo bom.", "Exemplo saindo!"]
FONTES_DOS_TRIBUNAIS = {"stf": "portal do STF", "stj": "PDF oficial das súmulas do STJ",
                        "tst": "site de jurisprudência do TST"}
_ORDINAIS_FALADOS = [("Primeira", "Primeiro"), ("Segunda", "Segundo"), ("Terceira", "Terceiro")]  # súmula, artigo


def _dias_do_vade() -> float:
    try:
        import config

        return float(config.VADE_MECUM_DIAS)
    except Exception:
        return 15.0


def _primeira_norma(texto: str) -> Norma | None:
    """A norma citada primeiro no texto (o "do Código Penal" logo depois do "art. 65")."""
    _, t = preparar(texto or "")
    melhor, posicao = None, None
    for norma in NORMAS:
        if not norma.apelidos:
            continue
        achado = re.search(norma.apelidos, t)
        if achado and (posicao is None or achado.start() < posicao[0]
                       or (achado.start() == posicao[0] and len(achado.group(0)) > posicao[1])):
            melhor, posicao = norma, (achado.start(), len(achado.group(0)))
    return melhor


def _com_acentos(original: str, trecho: str) -> str:
    """O trecho (achado no texto sem acentos) como foi escrito: a cópia sem acentos tem o mesmo tamanho."""
    escrito, sem = preparar(original or "")
    inicio = sem.find(trecho)
    return escrito[inicio:inicio + len(trecho)] if inicio >= 0 and len(sem) == len(escrito) else trecho


def _partes_da_sumula(texto: str) -> list[str]:
    """O enunciado em partes para ler aos poucos: os itens das súmulas longas do TST ("I - ...", "II - ...")."""
    partes = re.split(r"\s+(?=[IVX]{1,5}\s*[-–—]\s)", texto or "")
    return [p.strip() for p in partes if p.strip()] or [texto or ""]


def _foco_da_sumula(sumula: dict) -> str:
    partes = []
    if sumula.get("titulo"):
        partes.append(f"Assunto da súmula: {sumula['titulo']}")
    situacao = situacao_dita(sumula)
    if situacao:
        partes.append(f"Atenção: {COMO_CHAMAR.get(sumula.get('tipo'), 'essa súmula')} {situacao}; diga isso na explicação.")
    if sumula.get("observacao"):
        partes.append(f"Observações do tribunal: {sumula['observacao'][:1500]}")
    return "\n".join(partes)


def _situacao_curta(item: dict) -> str:
    situacao = item.get("situacao") or ""
    return f" ({situacao})" if situacao and situacao != "vigente" else ""


def _resumo_falado(texto: str, limite: int = 220) -> str:
    texto = re.sub(r"\s+", " ", sem_anotacoes(texto or "")).strip()
    if len(texto) <= limite:
        return texto if texto.endswith((".", "!", "?")) else texto + "."
    return texto[:limite].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _painel_da_sumula(rotulo: str, sumula: dict) -> str:
    linhas = [f"{_maiuscula(rotulo)}{_situacao_curta(sumula)}"]
    if sumula.get("titulo"):
        linhas.append(sumula["titulo"])
    linhas += ["", sumula["texto"]]
    if sumula.get("observacao"):
        linhas += ["", sumula["observacao"]]
    referencias = [j["referencia"] for j in sumula.get("julgados") or [] if j.get("referencia")]
    if referencias:
        linhas += ["", "Casos em que foi aplicada:"] + [f"- {r}" for r in referencias[:10]]
    if sumula.get("fonte", "").startswith("http"):
        linhas += ["", f"Fonte: {sumula['fonte']}"]
    return "\n".join(linhas)


def _painel_das_relacionadas(relacionadas: list) -> str:
    if not relacionadas:
        return ""
    return "\n\nSúmulas que citam este artigo:\n" + "\n".join(
        f"- {_maiuscula(s['rotulo'])}{_situacao_curta(s)}: {s['texto']}" for s in relacionadas)


def explicar_com_ia(prompt: str, contexto: str) -> str:
    """Na ordem da pesquisa (ORDEM_IA_PESQUISA: Gemini, depois Claude), porque o texto da lei é público; por último, o Ollama."""
    from llm_nuvem import provedores_nuvem, responder_na_nuvem

    registro = []
    texto = responder_na_nuvem(prompt, contexto=contexto, registro=registro, funcao="pesquisa")
    if texto:
        primeira = next(iter(provedores_nuvem("pesquisa")), None)
        if primeira is not None and registro and registro[0] != primeira.nome:
            logger.info("%s indisponível; a explicação da lei veio de %s.", primeira.nome, registro[0])
        return texto
    from llm_clients import llm_clients

    return llm_clients.chat_beatriz(prompt, contexto=contexto, usar_historico=False)


def _de(nome: str) -> str:
    """'o Código Penal' -> 'do Código Penal'; 'a CLT' -> 'da CLT'."""
    if nome.startswith("o "):
        return "do " + nome[2:]
    if nome.startswith("a "):
        return "da " + nome[2:]
    return "de " + nome


def _em(nome: str) -> str:
    """'o Código Penal' -> 'no Código Penal'; 'a Lei 14.181' -> 'na Lei 14.181'."""
    if nome.startswith("o "):
        return "no " + nome[2:]
    if nome.startswith("a "):
        return "na " + nome[2:]
    return "em " + nome


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]
