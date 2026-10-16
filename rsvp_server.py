#!/usr/bin/env python3
"""Service pour la page de mariage de Guillaume & Valentia.

Public (sans clé) :
  GET  /rsvp              -> {"counts":{...}}          compteurs seulement
  POST /rsvp              -> {"nom","reponse","personnes","enfants","message"}
  GET  /contenu           -> copie du contenu du site (secours)

Administration (en-tête X-Admin-Key) :
  GET  /admin/rsvp        -> réponses complètes + compteurs
  GET  /admin/export.csv  -> export tableur
  POST /admin/delete      -> {"nom": "..."}    supprime une réponse
  POST /admin/reset       -> vide les réponses
  GET  /admin/contenu     -> contenu actuel du site
  POST /admin/contenu     -> enregistre le contenu (noms, date, lieu, déroulé, photos)
  POST /admin/photo       -> {"nom":"...","data":"<base64>"} ajoute une photo

Chaque écriture est poussée vers le dépôt GitHub (copie durable).
"""
import json, os, subprocess, threading, re, secrets, csv, io, datetime, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "rsvp.json")
CONT = os.path.join(HERE, "contenu.json")
PHOTOS = os.path.join(HERE, "photos")
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

CHAMPS_TEXTE = ["marie1", "marie2", "dateISO", "dateTexte", "lieu", "adresse",
                "email", "rsvpAvant", "kicker"]


def lire_json(path, defaut):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, (dict, list)) else defaut
    except Exception:
        return defaut


def ecrire_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_rsvp():
    d = lire_json(DATA, {"rsvp": [], "maj": None})
    if not isinstance(d, dict):
        d = {"rsvp": [], "maj": None}
    d.setdefault("rsvp", [])
    return d


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


def push_repo(message="mise à jour"):
    env = dict(os.environ, HOME="/opt/data/home",
               HTTPS_PROXY="http://127.0.0.1:8888", HTTP_PROXY="http://127.0.0.1:8888")

    def run():
        try:
            subprocess.run(["git", "add", "-A"], cwd=HERE, env=env, timeout=90)
            subprocess.run(["git", "-c", "user.email=bot@local", "-c", "user.name=DanyAiBot",
                            "commit", "-qm", message], cwd=HERE, env=env, timeout=60)
            subprocess.run(["git", "push", "-q", "origin", "main"], cwd=HERE, env=env, timeout=180)
        except Exception as e:
            print("push repo échoué:", e, flush=True)

    threading.Thread(target=run, daemon=True).start()


def nettoyer_contenu(src):
    """Garde uniquement les champs connus, bornés : rien d'autre n'est publié."""
    if not isinstance(src, dict):
        return None
    c = {}
    for k in CHAMPS_TEXTE:
        c[k] = re.sub(r"\s+", " ", str(src.get(k) or "")).strip()[:200]
    prog = []
    for ligne in (src.get("programme") or [])[:24]:
        if isinstance(ligne, (list, tuple)) and len(ligne) >= 2:
            prog.append([str(ligne[0])[:12], str(ligne[1])[:80], str(ligne[2] if len(ligne) > 2 else "")[:160]])
    c["programme"] = prog
    photos = []
    for p in (src.get("photos") or [])[:80]:
        if isinstance(p, dict) and str(p.get("src", "")).startswith("photos/"):
            photos.append({"src": str(p["src"])[:200], "legende": str(p.get("legende") or "")[:120]})
    c["photos"] = photos
    return c


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

    def _admin(self):
        return self.headers.get("X-Admin-Key", "").strip() == ADMIN_KEY

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def do_OPTIONS(self):
        self._send(204, b"")

    # ---------------- lecture ----------------
    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p == "/health":
            return self._send(200, {"ok": True})
        if p == "/rsvp":
            with LOCK:
                d = load_rsvp()
            return self._send(200, {"counts": counts(d)})
        if p == "/contenu":
            return self._send(200, lire_json(CONT, {}))

        if p.startswith("/admin"):
            if not self._admin():
                return self._send(401, {"error": "Clé d'administration invalide."})
            with LOCK:
                d = load_rsvp()
            if p == "/admin/rsvp":
                return self._send(200, {"rsvp": d["rsvp"], "counts": counts(d)})
            if p == "/admin/contenu":
                return self._send(200, lire_json(CONT, {}))
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

    # ---------------- écritures ----------------
    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/") or "/"

        if p == "/rsvp":
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
                d = load_rsvp()
                d["rsvp"] = [r for r in d["rsvp"] if r.get("nom", "").lower() != nom.lower()]
                d["rsvp"].append({"nom": nom, "reponse": rep,
                                  "personnes": pers if rep == "oui" else 0,
                                  "enfants": enf if rep == "oui" else 0,
                                  "message": msg,
                                  "quand": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")})
                d["rsvp"].sort(key=lambda r: r.get("nom", "").lower())
                d["maj"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                ecrire_json(DATA, d)
                c = counts(d)
            push_repo("rsvp: mise à jour")
            return self._send(200, {"ok": True, "counts": c})

        if p.startswith("/admin"):
            if not self._admin():
                return self._send(401, {"error": "Clé d'administration invalide."})
            payload = self._body() or {}

            if p == "/admin/delete":
                nom = str(payload.get("nom") or "").strip().lower()
                with LOCK:
                    d = load_rsvp()
                    d["rsvp"] = [r for r in d["rsvp"] if r.get("nom", "").lower() != nom]
                    d["maj"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                    ecrire_json(DATA, d)
                    c = counts(d)
                push_repo("rsvp: suppression d'une réponse")
                return self._send(200, {"ok": True, "counts": c})

            if p == "/admin/reset":
                with LOCK:
                    ecrire_json(DATA, {"rsvp": [], "maj": None})
                push_repo("rsvp: remise à zéro")
                return self._send(200, {"ok": True, "counts": counts({"rsvp": []})})

            if p == "/admin/contenu":
                c = nettoyer_contenu(payload.get("contenu") if "contenu" in payload else payload)
                if c is None:
                    return self._send(400, {"error": "Contenu invalide."})
                if not c["marie1"] or not c["marie2"]:
                    return self._send(400, {"error": "Les deux prénoms sont nécessaires."})
                with LOCK:
                    ecrire_json(CONT, c)
                push_repo("contenu du site: mise à jour")
                return self._send(200, {"ok": True, "contenu": c})

            if p == "/admin/photo":
                nom = re.sub(r"[^a-zA-Z0-9._-]", "-", str(payload.get("nom") or "photo.jpg"))[:80]
                if "." not in nom:
                    nom += ".jpg"
                data = str(payload.get("data") or "")
                if "," in data[:80] and data.strip().startswith("data:"):
                    data = data.split(",", 1)[1]
                try:
                    brut = base64.b64decode(data, validate=False)
                except Exception:
                    return self._send(400, {"error": "Image illisible."})
                if not brut or len(brut) > 8 * 1024 * 1024:
                    return self._send(400, {"error": "Image vide ou trop lourde (max 8 Mo)."})
                os.makedirs(PHOTOS, exist_ok=True)
                horo = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                nom = horo + "-" + nom
                with open(os.path.join(PHOTOS, nom), "wb") as f:
                    f.write(brut)
                push_repo("photos: ajout de " + nom)
                return self._send(200, {"ok": True, "src": "photos/" + nom})

            return self._send(404, {"error": "not found"})

        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"service prêt sur :{PORT}", flush=True)
    srv.serve_forever()
