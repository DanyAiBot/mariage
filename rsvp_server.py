#!/usr/bin/env python3
"""Service RSVP + administration pour la page de mariage de Guillaume & Valentia.

Public (aucune clé) :
  GET  /rsvp              -> {"counts":{...}}            compteurs seulement
  POST /rsvp              -> {"nom","reponse","personnes","enfants","message"}

Administration (en-tête X-Admin-Key) :
  GET  /admin/rsvp        -> liste complète + compteurs
  GET  /admin/export.csv  -> export tableur
  POST /admin/delete      -> {"nom":"..."} supprime une réponse
  POST /admin/reset       -> remet le compteur à zéro

La clé d'administration est dans .admin_key (à côté du script, jamais publiée).
Chaque écriture pousse aussi rsvp.json vers le dépôt GitHub (copie durable).
"""
import json, os, subprocess, threading, re, secrets, csv, io, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "rsvp.json")
KEYF = os.path.join(HERE, ".admin_key")
PORT = int(os.environ.get("RSVP_PORT", "8765"))
LOCK = threading.Lock()

if not os.path.exists(KEYF):
    with open(KEYF, "w") as f:
        f.write(secrets.token_urlsafe(24))
ADMIN_KEY = open(KEYF).read().strip()

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key",
    "Cache-Control": "no-store",
}


def load():
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError
            d.setdefault("rsvp", [])
            return d
    except Exception:
        return {"rsvp": [], "maj": None}


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
        "enfants": sum(int(r.get("enfants") or 0) for r in oui),
        "total_reponses": len(d["rsvp"]),
        "maj": d.get("maj"),
    }


def push_repo():
    env = dict(os.environ, HOME="/opt/data/home",
               HTTPS_PROXY="http://127.0.0.1:8888", HTTP_PROXY="http://127.0.0.1:8888")

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

    def _send(self, code, payload, ctype="application/json; charset=utf-8", extra=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _is_admin(self):
        return self.headers.get("X-Admin-Key", "").strip() == ADMIN_KEY

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p == "/health":
            return self._send(200, {"ok": True})

        if p == "/rsvp":                      # public : compteurs seulement
            with LOCK:
                d = load()
            return self._send(200, {"counts": counts(d)})

        if p.startswith("/admin"):
            if not self._is_admin():
                return self._send(401, {"error": "Clé d'administration invalide."})
            with LOCK:
                d = load()
            if p == "/admin/rsvp":
                return self._send(200, {"rsvp": d["rsvp"], "counts": counts(d)})
            if p == "/admin/export.csv":
                buf = io.StringIO()
                w = csv.writer(buf, delimiter=";")
                w.writerow(["Nom", "Reponse", "Personnes", "Enfants", "Message", "Date"])
                for r in d["rsvp"]:
                    w.writerow([r.get("nom", ""), r.get("reponse", ""), r.get("personnes", ""),
                                r.get("enfants", ""), r.get("message", ""), r.get("quand", "")])
                data = ("\ufeff" + buf.getvalue()).encode("utf-8")
                return self._send(200, data, "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="reponses-mariage.csv"'})
            return self._send(404, {"error": "not found"})

        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/") or "/"

        if p == "/rsvp":                      # réponse d'un invité
            payload = self._body()
            if payload is None:
                return self._send(400, {"error": "JSON invalide"})
            nom = re.sub(r"\s+", " ", str(payload.get("nom") or "")).strip()[:60]
            rep = str(payload.get("reponse") or "").strip().lower()
            try:
                pers = max(1, min(20, int(payload.get("personnes") or 1)))
            except Exception:
                pers = 1
            try:
                enf = max(0, min(20, int(payload.get("enfants") or 0)))
            except Exception:
                enf = 0
            msg = re.sub(r"\s+", " ", str(payload.get("message") or "")).strip()[:400]
            if not nom:
                return self._send(400, {"error": "Il manque le nom."})
            if rep not in ("oui", "non"):
                return self._send(400, {"error": "Réponse invalide."})
            with LOCK:
                d = load()
                d["rsvp"] = [r for r in d["rsvp"] if r.get("nom", "").lower() != nom.lower()]
                d["rsvp"].append({
                    "nom": nom, "reponse": rep,
                    "personnes": pers if rep == "oui" else 0,
                    "enfants": enf if rep == "oui" else 0,
                    "message": msg,
                    "quand": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                })
                d["rsvp"].sort(key=lambda r: r.get("nom", "").lower())
                d["maj"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                save(d)
                c = counts(d)
            push_repo()
            return self._send(200, {"ok": True, "counts": c})

        if p.startswith("/admin"):
            if not self._is_admin():
                return self._send(401, {"error": "Clé d'administration invalide."})
            payload = self._body() or {}
            if p == "/admin/delete":
                nom = str(payload.get("nom") or "").strip().lower()
                with LOCK:
                    d = load()
                    d["rsvp"] = [r for r in d["rsvp"] if r.get("nom", "").lower() != nom]
                    d["maj"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                    save(d)
                    c = counts(d)
                push_repo()
                return self._send(200, {"ok": True, "counts": c})
            if p == "/admin/reset":
                with LOCK:
                    save({"rsvp": [], "maj": None})
                push_repo()
                return self._send(200, {"ok": True, "counts": counts({"rsvp": [], "maj": None})})
            return self._send(404, {"error": "not found"})

        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"RSVP prêt sur :{PORT} -> {DATA}", flush=True)
    print(f"cle admin dans {KEYF}", flush=True)
    srv.serve_forever()
