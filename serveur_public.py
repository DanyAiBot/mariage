#!/usr/bin/env python3
"""Sert les fichiers PUBLICS du site mariage sur le port 8766 (pour un tunnel).

Liste blanche stricte : rien d'autre n'est accessible, en particulier ni
`.admin_key`, ni `rsvp.json` (noms des invités), ni les scripts, ni `.git`.
"""
import os, re, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PUBLIC_PORT", "8766"))

AUTORISES = {"index.html", "admin.html", "alliances.svg", "contenu.json"}


def resoudre(chemin):
    """Renvoie le chemin absolu autorisé, ou None."""
    chemin = chemin.split("?")[0].split("#")[0]
    chemin = chemin.lstrip("/")
    if chemin in ("", "/"):
        chemin = "index.html"
    if chemin in AUTORISES:
        return os.path.join(HERE, chemin)
    if re.fullmatch(r"photos/[A-Za-z0-9._-]{1,120}", chemin):
        return os.path.join(HERE, chemin)
    return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        cible = resoudre(self.path)
        if not cible or not os.path.isfile(cible) or "/." in cible:
            return self._erreur(404, "not found")
        ctype = mimetypes.guess_type(cible)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("image/svg+xml", "application/json"):
            ctype += "; charset=utf-8"
        with open(cible, "rb") as f:
            corps = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def _erreur(self, code, texte):
        corps = texte.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"site public servi sur :{PORT} (liste blanche: {sorted(AUTORISES)} + photos/)", flush=True)
    srv.serve_forever()
