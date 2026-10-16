#!/usr/bin/env python3
"""Petit service RSVP pour la page de mariage de Guillaume & Valentia.

- GET  /rsvp        -> {"rsvp":[...], "counts":{...}}   (lecture publique)
- POST /rsvp        -> body {"nom":"...","reponse":"oui|non","personnes":N}
                       (upsert par nom : la dernière réponse gagne)
- GET  /health      -> ok

Stockage : rsvp.json à côté du script. Chaque écriture pousse aussi le JSON
vers le dépôt GitHub (rsvp.json) pour garder une copie durable, hors du tunnel.
CORS ouvert : la page est servie par GitHub Pages, sur un autre domaine.
"""
import json, os, subprocess, threading, re, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "rsvp.json")
PORT = int(os.environ.get("RSVP_PORT", "8765"))
LOCK = threading.Lock()

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
}


def load():
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {"rsvp": []}
    except Exception:
        return {"rsvp": []}


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def counts(d):
    oui = [r for r in d["rsvp"] if r.get("reponse") == "oui"]
    non = [r for r in d["rsvp"] if r.get("reponse") == "non"]
    return {
        "foyers_oui": len(oui),
        "foyers_non": len(non),
        "personnes": sum(int(r.get("personnes") or 1) for r in oui),
        "total_reponses": len(d["rsvp"]),
        "maj": d.get("maj"),
    }


def push_repo():
    """Copie durable du JSON dans le dépôt GitHub (best effort, en tâche de fond)."""
    env = dict(os.environ, HOME="/opt/data/home",
               HTTPS_PROXY="http://127.0.0.1:8888", HTTP_PROXY="http://127.0.0.1:8888",
               GIT_HTTP_LOW_SPEED_LIMIT="1", GIT_HTTP_LOW_SPEED_TIME="30")

    def run():
        try:
            subprocess.run(["git", "add", "rsvp.json"], cwd=HERE, env=env, timeout=60)
            subprocess.run(["git", "-c", "user.email=bot@local", "-c", "user.name=DanyAiBot",
                            "commit", "-qm", "rsvp: mise à jour"], cwd=HERE, env=env, timeout=60)
            subprocess.run(["git", "push", "-q", "origin", "main"], cwd=HERE, env=env, timeout=120)
        except Exception as e:
            print("push repo échoué:", e, flush=True)

    threading.Thread(target=run, daemon=True).start()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, {"ok": True})
        if self.path.startswith("/rsvp"):
            with LOCK:
                d = load()
            return self._send(200, {"rsvp": d["rsvp"], "counts": counts(d)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/rsvp"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "JSON invalide"})

        nom = re.sub(r"\s+", " ", str(payload.get("nom") or "")).strip()[:60]
        rep = str(payload.get("reponse") or "").strip().lower()
        try:
            pers = max(1, min(20, int(payload.get("personnes") or 1)))
        except Exception:
            pers = 1
        if not nom:
            return self._send(400, {"error": "Il manque le nom."})
        if rep not in ("oui", "non"):
            return self._send(400, {"error": "Réponse invalide."})

        with LOCK:
            d = load()
            d["rsvp"] = [r for r in d["rsvp"] if r.get("nom", "").lower() != nom.lower()]
            d["rsvp"].append({
                "nom": nom, "reponse": rep, "personnes": pers if rep == "oui" else 0,
                "quand": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
            })
            d["rsvp"].sort(key=lambda r: r.get("nom", "").lower())
            d["maj"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            save(d)
        push_repo()
        with LOCK:
            d = load()
        return self._send(200, {"ok": True, "rsvp": d["rsvp"], "counts": counts(d)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"RSVP prêt sur :{PORT} -> {DATA}", flush=True)
    srv.serve_forever()
