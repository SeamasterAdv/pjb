/* Desenho da mascote (a Beatriz) que anda pela tela (mascote.html).
 * As classes das partes (cabeca, orelha, olho, pata, coxa, cauda...) são as que o CSS anima. */
"use strict";

const MASCOTE_SVG = `<svg id="svg-mascote" viewBox="0 0 400 440" role="img" aria-label="Beatriz, a guardiã do sol">
<defs>
<linearGradient id="g-pelo" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFFBF5"/><stop offset="1" stop-color="#E6DBD2"/></linearGradient>
<linearGradient id="g-pata" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F3EBE3"/><stop offset=".5" stop-color="#D2C8CC"/><stop offset="1" stop-color="#6E6670"/></linearGradient>
<linearGradient id="g-orelha-e" x1="1" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#F6EEE6"/><stop offset=".55" stop-color="#CFC6CB"/><stop offset="1" stop-color="#4E4854"/></linearGradient>
<linearGradient id="g-orelha-d" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#F6EEE6"/><stop offset=".55" stop-color="#CFC6CB"/><stop offset="1" stop-color="#4E4854"/></linearGradient>
<linearGradient id="g-rabo" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#F3EBE3"/><stop offset=".45" stop-color="#C9BFC5"/><stop offset="1" stop-color="#55505C"/></linearGradient>
<radialGradient id="g-olho" cx=".45" cy=".4" r=".65"><stop offset="0" stop-color="#FFE48A"/><stop offset=".6" stop-color="#F5B82E"/><stop offset="1" stop-color="#C9871B"/></radialGradient>
<radialGradient id="g-aura"><stop offset="0" stop-color="#8F6BE0" stop-opacity=".45"/><stop offset=".6" stop-color="#3FA9FF" stop-opacity=".12"/><stop offset="1" stop-color="#3FA9FF" stop-opacity="0"/></radialGradient>
<linearGradient id="g-ouro" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFE7A3"/><stop offset="1" stop-color="#C98F1E"/></linearGradient>
</defs>
<circle class="aura" cx="200" cy="250" r="190" fill="url(#g-aura)"/>
<ellipse class="sombra" cx="200" cy="404" rx="96" ry="11" fill="#05020A" opacity=".55"/>
<g class="faiscas" aria-hidden="true">
<g class="orbita o1"><circle cx="200" cy="40" r="3.2"/></g>
<g class="orbita o2"><circle cx="310" cy="120" r="2.6"/></g>
<g class="orbita o3"><circle cx="96" cy="150" r="2.2"/></g>
<g class="orbita o4"><circle cx="290" cy="220" r="2.8"/></g>
</g>
<g class="pulo">
<g class="cauda">
<path d="M228 380 Q262 396 294 384 Q322 378 342 356 Q366 340 372 310 Q384 284 374 254 Q380 226 360 202 Q348 184 324 176 Q336 200 332 222 Q342 248 330 272 Q326 298 306 316 Q286 334 260 342 Q244 348 228 350 Z" fill="url(#g-rabo)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/>
<path d="M344 236 Q354 254 348 272 M330 300 Q344 306 356 296 M298 340 Q310 350 326 346 M352 206 Q362 220 362 236" stroke="#FFFFFF" stroke-opacity=".5" stroke-width="2" fill="none" stroke-linecap="round"/>
</g>
<g class="corpo">
<g class="coxa esq"><ellipse cx="150" cy="356" rx="38" ry="36" fill="url(#g-pata)"/></g>
<g class="coxa dir"><ellipse cx="250" cy="356" rx="38" ry="36" fill="url(#g-pata)"/></g>
<path d="M200 204 C240 204 262 244 268 288 Q278 298 269 310 Q278 324 267 336 C262 368 244 388 200 392 C156 388 138 368 133 336 Q122 324 131 310 Q122 298 132 288 C138 244 160 204 200 204 Z" fill="url(#g-pelo)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/>
<g class="pata esq"><path d="M160 306 C158 340 159 370 163 394 L194 394 C196 370 196 340 192 306 Z" fill="url(#g-pata)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/><ellipse cx="178" cy="396" rx="18" ry="8.5" fill="#5E5864"/><path d="M172 391 L172 401 M183 391 L183 401" stroke="#3A3640" stroke-width="1.5" stroke-linecap="round"/></g>
<g class="pata dir"><path d="M240 306 C242 340 241 370 237 394 L206 394 C204 370 204 340 208 306 Z" fill="url(#g-pata)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/><ellipse cx="222" cy="396" rx="18" ry="8.5" fill="#5E5864"/><path d="M217 391 L217 401 M228 391 L228 401" stroke="#3A3640" stroke-width="1.5" stroke-linecap="round"/></g>
<path d="M146 212 Q158 236 150 256 Q166 250 170 272 Q184 258 192 282 Q200 264 208 282 Q216 258 230 272 Q234 250 250 256 Q242 236 254 212 Q200 232 146 212 Z" fill="#FFFFFF" stroke="#D8CCC6" stroke-width="1.4" stroke-linejoin="round"/>
<path d="M170 238 Q176 250 172 262 M200 246 L200 262 M230 238 Q224 250 228 262" stroke="#E4DAD2" stroke-width="2" fill="none" stroke-linecap="round"/>
</g>
<g class="cabeca">
<g class="orelha esq">
<path d="M150 120 C132 88 118 50 110 14 C140 32 168 60 184 98 Z" fill="url(#g-orelha-e)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/>
<path d="M156 112 C144 86 134 60 128 38 C146 54 164 74 174 100 Z" fill="#F1D2C5"/>
<path d="M150 104 Q144 92 138 86 M158 100 Q152 86 148 76 M164 98 Q162 88 158 80" stroke="#FFFFFF" stroke-width="2.2" fill="none" stroke-linecap="round"/>
</g>
<g class="orelha dir">
<path d="M250 120 C268 88 282 50 290 14 C260 32 232 60 216 98 Z" fill="url(#g-orelha-d)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/>
<path d="M244 112 C256 86 266 60 272 38 C254 54 236 74 226 100 Z" fill="#F1D2C5"/>
<path d="M250 104 Q256 92 262 86 M242 100 Q248 86 252 76 M236 98 Q238 88 242 80" stroke="#FFFFFF" stroke-width="2.2" fill="none" stroke-linecap="round"/>
</g>
<path d="M200 92 C236 92 262 112 266 142 C278 150 282 162 272 170 C282 178 274 192 262 190 C252 206 228 214 200 214 C172 214 148 206 138 190 C126 192 118 178 128 170 C118 162 122 150 134 142 C138 112 164 92 200 92 Z" fill="url(#g-pelo)" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/>
<path d="M190 98 Q193 80 204 72 Q201 86 210 97 Z" fill="#FFFBF5" stroke="#BCAEB0" stroke-width="1.6" stroke-linejoin="round"/>
<path d="M184 98 Q192 106 190 116 M200 94 L200 108 M216 98 Q208 106 210 116" stroke="#E6DBD2" stroke-width="2" fill="none" stroke-linecap="round"/>
<path d="M166 129 Q173 124 181 127 M219 127 Q227 124 234 129" stroke="#8E8590" stroke-width="2.2" fill="none" stroke-linecap="round"/>
<ellipse cx="160" cy="180" rx="10" ry="5.5" fill="#F4B6B0" opacity=".55"/><ellipse cx="240" cy="180" rx="10" ry="5.5" fill="#F4B6B0" opacity=".55"/>
<g class="olho esq">
<path d="M160 156 C160 142 169 136 178 136 C188 136 195 144 195 156 C195 168 187 175 177 175 C168 175 160 168 160 156 Z" fill="url(#g-olho)"/>
<ellipse class="pupila" cx="178" cy="157" rx="5" ry="8.5" fill="#2A1A10"/>
<circle cx="183.5" cy="148" r="4.6" fill="#FFFFFF"/><circle cx="172" cy="166" r="2" fill="#FFFFFF"/>
<path d="M157 151 C163 136 186 132 197 146" stroke="#3A2A2A" stroke-width="3.6" fill="none" stroke-linecap="round"/>
<path d="M158 150 L150 144" stroke="#3A2A2A" stroke-width="2.4" stroke-linecap="round"/>
</g>
<g class="olho dir">
<path d="M205 156 C205 144 212 136 222 136 C231 136 240 142 240 156 C240 168 232 175 223 175 C213 175 205 168 205 156 Z" fill="url(#g-olho)"/>
<ellipse class="pupila" cx="222" cy="157" rx="5" ry="8.5" fill="#2A1A10"/>
<circle cx="227.5" cy="148" r="4.6" fill="#FFFFFF"/><circle cx="216" cy="166" r="2" fill="#FFFFFF"/>
<path d="M203 146 C214 132 237 136 243 151" stroke="#3A2A2A" stroke-width="3.6" fill="none" stroke-linecap="round"/>
<path d="M242 150 L250 144" stroke="#3A2A2A" stroke-width="2.4" stroke-linecap="round"/>
</g>
<path d="M195 178 Q200 176 205 178 Q203 183 200 184 Q197 183 195 178 Z" fill="#9E7A7E"/>
<path class="boca-fechada" d="M200 184 Q196 191 190 189 M200 184 Q204 191 210 189" stroke="#6E5458" stroke-width="2" fill="none" stroke-linecap="round"/>
<ellipse class="boca-aberta" cx="200" cy="191" rx="5" ry="4.5" fill="#7A4A50"/>
<path d="M156 206 Q200 224 244 206" stroke="#7A55B0" stroke-width="5" fill="none" stroke-linecap="round"/>
<circle class="pingente" cx="200" cy="222" r="6.5" fill="url(#g-ouro)" stroke="#B8871B" stroke-width="1"/>
<circle cx="200" cy="222" r="2.2" fill="#3FA9FF"/>
</g>
</g>
</svg>`;

function desenharMascote(recipiente) {
  recipiente.insertAdjacentHTML("afterbegin", MASCOTE_SVG);
}
