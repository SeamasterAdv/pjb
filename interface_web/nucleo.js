/* O núcleo do painel: o "reator" de anéis ciano no centro, no estilo de HUD.
 *
 * O desenho é montado aqui (anéis, marcações, barras da voz, colmeias e os textos em arco); quem
 * anima é o estilo.css, pelas classes de estado (repouso, ouvindo, pensando, falando, vitoria) que
 * o app.js põe no recipiente. Tudo usa currentColor: na vitória o núcleo inteiro fica dourado.
 * Os anéis usam pathLength="360", então os tracejados estão em graus.
 */
"use strict";

const ROTULOS_DO_NUCLEO = { repouso: "EM ESPERA", ouvindo: "OUVINDO", pensando: "PROCESSANDO",
                            falando: "RESPONDENDO", vitoria: "MISSÃO CONCLUÍDA" };

function _ponto(raio, graus) {
  const a = (graus * Math.PI) / 180;
  return [(300 + raio * Math.cos(a)).toFixed(1), (300 + raio * Math.sin(a)).toFixed(1)];
}

function _anel(raio, atributos) {
  return `<circle cx="300" cy="300" r="${raio}" pathLength="360" ${atributos}/>`;
}

// Barras em volta do núcleo que se mexem com a voz; cada uma com ritmo e altura próprios.
function _barrasDaVoz(quantidade, raio, comprimento) {
  let html = "";
  for (let i = 0; i < quantidade; i++) {
    const ritmo = `--d:${(0.28 + Math.random() * 0.45).toFixed(2)}s;--a:${(-Math.random()).toFixed(2)}s;--h:${(0.4 + Math.random() * 0.6).toFixed(2)}`;
    // A rotação fica no <g>: o transform do CSS (a animação) substituiria o atributo do próprio retângulo.
    html += `<g transform="rotate(${((360 / quantidade) * i).toFixed(2)} 300 300)">` +
            `<rect class="barra-voz" x="298.9" y="${300 - raio - comprimento}" width="2.2" height="${comprimento}" style="${ritmo}"/></g>`;
  }
  return html;
}

// Hexágonos encaixados (colmeia), como os dos cantos de um HUD.
function _colmeia(x0, y0, raio, celulas, opacidade) {
  const altura = Math.sqrt(3) * raio;
  return celulas.map(([coluna, linha]) => {
    const cx = x0 + coluna * 1.5 * raio, cy = y0 + linha * altura + (coluna % 2 ? altura / 2 : 0);
    const pontos = Array.from({ length: 6 }, (_, i) => {
      const a = (Math.PI / 3) * i;
      return `${(cx + raio * Math.cos(a)).toFixed(1)},${(cy + raio * Math.sin(a)).toFixed(1)}`;
    });
    return `<polygon points="${pontos.join(" ")}" opacity="${opacidade}"/>`;
  }).join("");
}

function _nos(raio, angulos) {
  return angulos.map((graus) => {
    const [x, y] = _ponto(raio, graus);
    return `<circle cx="${x}" cy="${y}" r="7" stroke-width="1" opacity=".7"/>` +
           `<circle cx="${x}" cy="${y}" r="2.6" fill="currentColor" stroke="none"/>`;
  }).join("");
}

function _raiosDeLuz(raios) {
  return raios.map(([graus, de, ate]) => {
    const [x1, y1] = _ponto(de, graus), [x2, y2] = _ponto(ate, graus);
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }).join("");
}

// A varredura do "processando": fatias finas cada vez mais apagadas, como um gradiente cônico.
function _varredura(raio, fatias, abertura) {
  let html = "";
  for (let i = 0; i < fatias; i++) {
    const [x1, y1] = _ponto(raio, -abertura * i), [x2, y2] = _ponto(raio, -abertura * (i + 1));
    html += `<path d="M300 300 L${x1} ${y1} A${raio} ${raio} 0 0 0 ${x2} ${y2} Z" fill="currentColor" stroke="none" ` +
            `opacity="${(0.3 * (1 - i / fatias)).toFixed(3)}"/>`;
  }
  return html + `<line x1="300" y1="300" x2="${300 + raio}" y2="300" stroke-width="1.5"/>`;
}

function desenharNucleo(recipiente) {
  const letreiro = "B.E.A.T.R.I.Z ◆ SISTEMA ◆ ".repeat(6);
  recipiente.insertAdjacentHTML("afterbegin", `
<svg viewBox="0 0 600 600" aria-hidden="true">
<defs>
  <radialGradient id="g-brilho-nucleo"><stop offset="0" stop-color="currentColor" stop-opacity=".34"/>
    <stop offset=".38" stop-color="currentColor" stop-opacity=".08"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient>
  <radialGradient id="g-coracao"><stop offset="0" stop-color="#FFFFFF"/><stop offset=".3" stop-color="currentColor" stop-opacity=".95"/>
    <stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient>
  <radialGradient id="g-raio" gradientUnits="userSpaceOnUse" cx="300" cy="300" r="330">
    <stop offset=".1" stop-color="currentColor" stop-opacity=".95"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient>
  <path id="arco-nome" d="M190 300 A110 110 0 0 1 410 300"/>
  <path id="arco-estado" d="M190 300 A110 110 0 0 0 410 300"/>
  <path id="arco-letreiro" d="M300 38 A262 262 0 1 1 299.9 38"/>
</defs>
<circle class="brilho" cx="300" cy="300" r="300" fill="url(#g-brilho-nucleo)"/>
<g fill="none" stroke="currentColor">
  <path class="mira" d="M300 14 V586 M14 300 H586" stroke-width=".8" opacity=".2"/>
  <g stroke="url(#g-raio)" stroke-width="1.3">${_raiosDeLuz([[-62, 60, 300], [28, 110, 330], [118, 80, 300], [-152, 90, 330], [52, 240, 400], [-128, 250, 400]])}</g>
  <g stroke-width="1">
    ${_colmeia(58, 58, 12, [[0, 0], [1, 0], [0, 1], [2, 0], [1, 1]], 0.5)}
    ${_colmeia(512, 520, 12, [[0, 0], [1, -1], [1, 0], [2, 0], [3, 0], [2, 1]], 0.45)}
    ${_colmeia(522, 42, 10, [[0, 0], [1, 0], [2, 0]], 0.3)}
    ${_colmeia(42, 548, 10, [[0, 0], [1, -1], [1, 0]], 0.3)}
  </g>

  <g class="acelera"><g class="gira" style="--t:140s">
    ${_anel(292, 'stroke-width="7" stroke-dasharray=".3 1.7" opacity=".3"')}
    ${_anel(292, 'stroke-width="13" stroke-dasharray=".5 29.5" opacity=".6"')}
    ${_anel(280, 'stroke-width="1" opacity=".3"')}
    ${_anel(280, 'stroke-width="3.5" stroke-dasharray="34 12 6 70 48 20 10 160" opacity=".85"')}
  </g></g>
  <g class="gira inverso" style="--t:220s">
    <text class="letreiro"><textPath href="#arco-letreiro" textLength="1630" lengthAdjust="spacing">${letreiro}</textPath></text>
  </g>
  <g class="acelera inverso"><g class="gira inverso" style="--t:90s">
    ${_anel(245, 'stroke-width="16" stroke-dasharray="58 6 22 28 84 10 44 108" opacity=".16"')}
    ${_anel(253, 'stroke-width="1.2" stroke-dasharray="58 6 22 28 84 10 44 108" opacity=".75"')}
    ${_anel(237, 'stroke-width="1.2" stroke-dasharray="58 6 22 28 84 10 44 108" opacity=".5"')}
  </g></g>
  <g class="gira" style="--t:60s">
    ${_anel(222, 'stroke-width="1" stroke-dasharray="3 3" opacity=".4"')}
    ${_nos(222, [25, 148, 262])}
  </g>
  <g class="voz">${_barrasDaVoz(90, 178, 32)}</g>
  <g class="acelera"><g class="gira" style="--t:26s">
    ${_anel(172, 'stroke-width="1" opacity=".25"')}
    ${_anel(160, 'stroke-width="9" stroke-dasharray="105 255" opacity=".9"')}
    ${_anel(160, 'stroke-width="2" stroke-dasharray="0 130 40 190" opacity=".9"')}
  </g></g>
  <g class="acelera inverso"><g class="gira inverso" style="--t:40s">
    ${_anel(140, 'stroke-width="8" stroke-dasharray=".45 2.55" opacity=".5"')}
    ${_anel(131, 'stroke-width="1" opacity=".35"')}
  </g></g>
  <g class="varredura">${_varredura(240, 14, 3.5)}</g>

  <text class="rotulo-nucleo nome"><textPath href="#arco-nome" startOffset="50%">B.E.A.T.R.I.Z</textPath></text>
  <text class="rotulo-nucleo estado"><textPath href="#arco-estado" startOffset="50%" id="rotulo-estado">${ROTULOS_DO_NUCLEO.repouso}</textPath></text>

  <g class="acelera"><g class="gira" style="--t:18s">
    ${_anel(92, 'stroke-width="1.4" stroke-dasharray="40 8 12 8 70 8 24 8 150 32" opacity=".7"')}
    ${_anel(84, 'stroke-width="1" opacity=".3"')}
  </g></g>
  <g class="gira inverso" style="--t:9s">
    ${_anel(68, 'stroke-width="5" stroke-dasharray="70 12 40 12 180 46" opacity=".85"')}
  </g>
  <circle class="pulso p1" cx="300" cy="300" r="40"/>
  <circle class="pulso p2" cx="300" cy="300" r="40"/>
  <g class="coracao">
    ${_anel(50, 'stroke-width="1" opacity=".5"')}
    ${_anel(36, 'stroke-width="3" opacity=".95"')}
    <circle cx="300" cy="300" r="30" fill="url(#g-coracao)" stroke="none"/>
    <circle cx="300" cy="300" r="7" fill="#FFFFFF" stroke="none"/>
  </g>
</g>
</svg>`);
}

function estadoDoNucleo(recipiente, estado) {
  recipiente.className = `nucleo estado-${estado}`;
  const rotulo = recipiente.querySelector("#rotulo-estado");
  if (rotulo) rotulo.textContent = ROTULOS_DO_NUCLEO[estado] || "";
}
