// Service worker do app: guarda a "casca" (página, estilo, código e ícones) para o app abrir na hora, mesmo com o PC
// fora do alcance. Busca sempre a versão nova primeiro; a guardada só entra sem rede. As chamadas /api/ nunca são guardadas.
const VERSAO = "beatriz-app-3";
const CASCA = ["./", "index.html", "app.css", "app.js", "visual_app.js", "manifest.webmanifest", "icones/icone-180.png", "icones/icone-192.png",
               "/visual/visual_rede.js", "/visual/vendor/three/build/three.module.min.js"];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(VERSAO).then((cache) => cache.addAll(CASCA)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(nomes.filter((n) => n !== VERSAO).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (evento) => {
  const endereco = new URL(evento.request.url);
  if (evento.request.method !== "GET" || endereco.origin !== self.location.origin || endereco.pathname.startsWith("/api/")) return;
  evento.respondWith(
    fetch(evento.request)
      .then((resposta) => {
        const copia = resposta.clone();
        caches.open(VERSAO).then((cache) => cache.put(evento.request, copia));
        return resposta;
      })
      .catch(() => caches.match(evento.request).then((guardada) => guardada || caches.match("index.html"))),
  );
});
