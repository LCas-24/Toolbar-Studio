import configparser
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

WINDOWS = sys.platform.startswith("win")
AQUI = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(AQUI, "cortador.html")
LIB = os.path.join(AQUI, "biblioteca")
LIB_GIF = os.path.join(AQUI, "biblioteca_gif")
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
PASTAS = {
    "lib": (LIB, EXTS),
    "gif": (LIB_GIF, (".gif",)),
}
TIPOS = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
MAX_BYTES = 50 * 1024 * 1024  # limite por arquivo enviado

# Definidos ao iniciar (ver o final do arquivo)
PROFILE_DIR = None
CHROME_DIR = None
TOKEN = secrets.token_urlsafe(24)  # novo a cada execução
HOSTS_OK = set()
ORIGENS_OK = set()

CSS_MODELO = """/* 1. Camada 1: GIF | Camada 2: imagem preenchendo tudo */
#navigator-toolbox {
    background-image: url("fundo.gif"), url("fundo.png") !important;
    background-size: auto 110px, cover !important;
    background-position: right 0px top 0px, 80% center !important;
    background-repeat: no-repeat, no-repeat !important;
    background-color: #1a1a1a !important;
    border: none !important;
}

/* 2. Barras transparentes */
#TabsToolbar,
#nav-bar,
#PersonalToolbar {
    background: transparent !important;
    background-color: transparent !important;
}

/* 3. Abas sem sombreado */
.tabbrowser-tab .tab-stack,
.tabbrowser-tab .tab-background {
    background: transparent !important;
    background-color: transparent !important;
    box-shadow: none !important;
}

/* 4. Campo de pesquisa/URL levemente escurecido */
#urlbar-background {
    background-color: rgba(0, 0, 0, 0.5) !important;
}
"""

RE_TAMANHO = re.compile(r"auto \d{1,4}px, cover")
RE_POSICAO = re.compile(r"right -?\d{1,5}px top -?\d{1,5}px, 80% center")
RESERVADOS = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


# ---------- Perfil do Firefox ----------
def pastas_base():
    if WINDOWS:
        return [os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox")]
    home = os.path.expanduser("~")
    normal = os.path.join(home, ".mozilla", "firefox")
    snap = os.path.join(home, "snap", "firefox", "common", ".mozilla", "firefox")
    flatpak = os.path.join(home, ".var", "app", "org.mozilla.firefox", ".mozilla", "firefox")
    exe = shutil.which("firefox") or ""
    if exe.startswith("/snap"):
        return [snap, normal, flatpak]
    if not exe and shutil.which("flatpak"):
        return [flatpak, normal, snap]
    return [normal, snap, flatpak]


def achar_perfil():
    env = os.environ.get("FIREFOX_PROFILE", "").strip().strip('"')
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    for base in pastas_base():
        ini = os.path.join(base, "profiles.ini")
        if not os.path.isfile(ini):
            continue
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(ini, encoding="utf-8")
            candidatos = []
            for sec in cp.sections():
                if sec.startswith("Install") and cp.has_option(sec, "Default"):
                    candidatos.append((0, cp.get(sec, "Default")))
            for sec in cp.sections():
                if sec.startswith("Profile") and cp.has_option(sec, "Path"):
                    prio = 1 if cp.get(sec, "Default", fallback="0") == "1" else 2
                    candidatos.append((prio, cp.get(sec, "Path")))
            for _, caminho in sorted(candidatos, key=lambda c: c[0]):
                p = os.path.normpath(os.path.join(base, caminho))
                if os.path.isdir(p):
                    return p
        except (configparser.Error, OSError):
            pass
    print("Não consegui achar o perfil do Firefox sozinho.")
    print("No Firefox, abra about:support e copie o caminho de 'Pasta do perfil'.")
    while True:
        p = input("Cole o caminho aqui: ").strip().strip('"')
        if os.path.isdir(p):
            return os.path.abspath(p)
        print("Pasta não encontrada. Tente de novo.")


# ---------- Nomes e arquivos ----------
def limpar_nome(nome):
    nome = os.path.basename(nome.replace("\\", "/"))
    nome = re.sub(r"[^\w\-. ()]", "_", nome).strip(" .")[:100]
    if nome.split(".")[0].upper() in RESERVADOS:
        nome = "_" + nome
    return nome


def nome_valido(nome):
    return (
        bool(nome)
        and len(nome) <= 150
        and nome == os.path.basename(nome)
        and "/" not in nome and "\\" not in nome and ":" not in nome
        and not nome.startswith(".")
    )


def assinatura_ok(ext, d):
    if ext == ".png":
        return d.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return d.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return d[:6] in (b"GIF87a", b"GIF89a")
    if ext == ".webp":
        return d[:4] == b"RIFF" and d[8:12] == b"WEBP"
    if ext == ".bmp":
        return d[:2] == b"BM"
    return False


def ler_favs(pasta):
    try:
        with open(os.path.join(pasta, "favoritos.json"), encoding="utf-8") as f:
            dados = json.load(f)
        return [n for n in dados if isinstance(n, str)] if isinstance(dados, list) else []
    except (OSError, ValueError):
        return []


def gravar_favs(pasta, favs):
    with open(os.path.join(pasta, "favoritos.json"), "w", encoding="utf-8") as f:
        json.dump(favs, f)


def gravar(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "wb") as f:
        f.write(dados)
    os.replace(tmp, caminho)


# ---------- Firefox ----------
def achar_firefox():
    """Devolve o comando que abre o Firefox (lista) ou None."""
    if WINDOWS:
        candidatos = [
            shutil.which("firefox"),
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ]
        for c in candidatos:
            if c and os.path.exists(c):
                return [c]
        return None
    for c in (shutil.which("firefox"), "/snap/bin/firefox"):
        if c and os.path.exists(c):
            return [c]
    if shutil.which("flatpak"):
        r = subprocess.run(["flatpak", "info", "org.mozilla.firefox"], capture_output=True)
        if r.returncode == 0:
            return ["flatpak", "run", "org.mozilla.firefox"]
    return None


def firefox_aberto():
    if WINDOWS:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq firefox.exe"],
            capture_output=True,
        )
        return b"firefox.exe" in r.stdout.lower()
    try:
        return any(
            subprocess.run(["pgrep", "-x", nome], capture_output=True).returncode == 0
            for nome in ("firefox", "firefox-bin")
        )
    except OSError:
        return False


def fechar_firefox(forcar=False):
    try:
        if WINDOWS:
            subprocess.run(["taskkill"] + (["/F"] if forcar else []) + ["/IM", "firefox.exe"], capture_output=True)
        else:
            for nome in ("firefox", "firefox-bin"):
                subprocess.run(["pkill"] + (["-9"] if forcar else []) + ["-x", nome], capture_output=True)
    except OSError:
        pass


def reiniciar_firefox():
    cmd = achar_firefox()
    if not cmd:
        print("Não achei o Firefox. Abra-o manualmente.")
        return
    # fecha de forma normal (preserva a sessão); força só se travar
    fechar_firefox()
    for _ in range(20):
        time.sleep(0.5)
        if not firefox_aberto():
            break
    else:
        fechar_firefox(forcar=True)
        time.sleep(1)
    extra = {} if WINDOWS else {"start_new_session": True, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    subprocess.Popen(cmd + ["-profile", PROFILE_DIR], **extra)


def garantir_css():
    caminho = os.path.join(CHROME_DIR, "userChrome.css")
    if not os.path.exists(caminho):
        os.makedirs(CHROME_DIR, exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(CSS_MODELO)
    return caminho


def ajustar_css(tamanho, posicao):
    caminho = garantir_css()
    with open(caminho, encoding="utf-8") as f:
        txt = f.read()
    shutil.copy(caminho, caminho + ".bak")
    txt = re.sub(r"background-size:[^;]*;", lambda m: f"background-size: {tamanho} !important;", txt, count=1)
    txt = re.sub(r"background-position:[^;]*;", lambda m: f"background-position: {posicao} !important;", txt, count=1)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(txt)


# ---------- Servidor ----------
CSP_PAGINA = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src 'self' blob: data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def log_message(self, *args):
        pass

    def responder(self, codigo, corpo, tipo="application/json"):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP_PAGINA if tipo.startswith("text/html") else "default-src 'none'")
        self.end_headers()
        self.wfile.write(corpo)

    def msg(self, codigo, texto):
        self.responder(codigo, json.dumps({"msg": texto}).encode())

    def autorizado(self, q):
        if self.headers.get("Host", "") not in HOSTS_OK:
            return False
        origem = self.headers.get("Origin")
        if origem and origem not in ORIGENS_OK:
            return False
        t = self.headers.get("X-Token") or q.get("t", [""])[0]
        return hmac.compare_digest(t.encode(), TOKEN.encode())

    def ler_corpo(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BYTES:
            return None
        return self.rfile.read(n)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if not self.autorizado(q):
            return self.responder(403, b"{}")
        caminho = url.path
        if caminho == "/":
            with open(HTML, "rb") as f:
                pagina = f.read().replace(b"__TOKEN__", TOKEN.encode())
            return self.responder(200, pagina, "text/html; charset=utf-8")
        for k, (pasta, exts) in PASTAS.items():
            if caminho == "/" + k:
                os.makedirs(pasta, exist_ok=True)
                nomes = sorted(n for n in os.listdir(pasta) if n.lower().endswith(exts) and nome_valido(n))
                favs = [n for n in ler_favs(pasta) if n in nomes]
                return self.responder(200, json.dumps({"nomes": nomes, "favs": favs}).encode())
            if caminho.startswith("/" + k + "/"):
                nome = unquote(caminho[len(k) + 2:])
                alvo = os.path.join(pasta, nome)
                ext = os.path.splitext(nome)[1].lower()
                if not nome_valido(nome) or ext not in exts or not os.path.isfile(alvo):
                    return self.responder(404, b"{}")
                with open(alvo, "rb") as f:
                    return self.responder(200, f.read(), TIPOS.get(ext, "application/octet-stream"))
        self.responder(404, b"{}")

    def lib_post(self, q, k, acao):
        pasta, exts = PASTAS[k]
        os.makedirs(pasta, exist_ok=True)
        bruto = q.get("nome", [""])[0]
        nome = limpar_nome(bruto) if acao == "add" else bruto
        ext = os.path.splitext(nome)[1].lower()
        if not nome_valido(nome) or ext not in exts:
            return self.msg(400, "Arquivo inválido.")
        alvo = os.path.join(pasta, nome)
        try:
            if acao == "fav":
                if not os.path.isfile(alvo):
                    return self.msg(404, "Não encontrado.")
                favs = ler_favs(pasta)
                if nome in favs:
                    favs.remove(nome)
                else:
                    favs.append(nome)
                gravar_favs(pasta, favs)
                return self.msg(200, "ok")
            if acao == "del":
                if os.path.isfile(alvo):
                    os.remove(alvo)
                gravar_favs(pasta, [n for n in ler_favs(pasta) if n != nome])
                return self.msg(200, "Removida.")
            dados = self.ler_corpo()
            if dados is None or not assinatura_ok(ext, dados):
                return self.msg(400, "Arquivo inválido ou grande demais.")
            base, _ = os.path.splitext(nome)
            n = 1
            while os.path.exists(alvo):
                alvo = os.path.join(pasta, f"{base}_{n}{ext}")
                n += 1
            gravar(alvo, dados)
        except OSError as e:
            print("Erro de arquivo:", type(e).__name__)
            return self.msg(500, "Erro ao mexer nos arquivos da biblioteca.")
        self.msg(200, "Adicionada.")

    def do_POST(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if not self.autorizado(q):
            return self.responder(403, b"{}")
        partes = url.path.strip("/").split("/")
        if len(partes) == 2 and partes[0] in PASTAS and partes[1] in ("add", "del", "fav"):
            return self.lib_post(q, partes[0], partes[1])
        if url.path not in ("/apply", "/remover"):
            return self.responder(404, b"{}")
        tipo = q.get("tipo", [""])[0]
        if tipo not in ("png", "gif"):
            return self.msg(400, "Tipo inválido.")
        if url.path == "/remover":
            alvo = os.path.join(CHROME_DIR, f"fundo.{tipo}")
            try:
                if not os.path.exists(alvo):
                    return self.msg(200, f"Não existe fundo.{tipo} para remover.")
                os.remove(alvo)
            except OSError as e:
                print("Erro de arquivo:", type(e).__name__)
                return self.msg(500, "Erro ao remover o arquivo.")
            self.msg(200, f"fundo.{tipo} removido. O Firefox vai reiniciar agora.")
            threading.Thread(target=reiniciar_firefox, daemon=True).start()
            return
        dados = self.ler_corpo()
        if dados is None or not assinatura_ok("." + tipo, dados):
            return self.msg(400, "Arquivo inválido ou grande demais.")
        bs = q.get("bs", ["auto 110px, cover"])[0]
        bp = q.get("bp", ["right 0px top 0px, 80% center"])[0]
        if tipo == "gif" and not (RE_TAMANHO.fullmatch(bs) and RE_POSICAO.fullmatch(bp)):
            return self.msg(400, "Posição inválida.")
        try:
            garantir_css()
            gravar(os.path.join(CHROME_DIR, f"fundo.{tipo}"), dados)
            if tipo == "gif":
                ajustar_css(bs, bp)
        except OSError as e:
            print("Erro de arquivo:", type(e).__name__)
            return self.msg(500, "Erro ao gravar na pasta do perfil do Firefox.")
        self.msg(200, "Aplicado. O Firefox vai reiniciar agora.")
        threading.Thread(target=reiniciar_firefox, daemon=True).start()


def abrir_navegador(url):
    # Outro navegador que não o Firefox, para a página não fechar junto com ele
    if WINDOWS:
        try:
            subprocess.Popen(["cmd", "/c", "start", "msedge", url])
            return
        except OSError:
            pass
    else:
        for nome in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
                     "chromium", "chromium-browser", "brave-browser", "epiphany", "falkon"):
            exe = shutil.which(nome)
            if exe:
                subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return
        print("Não achei outro navegador além do Firefox. Se a página abrir nele, ela fecha quando o Firefox")
        print("reiniciar; é só abrir de novo pelo endereço acima.")
    webbrowser.open(url)


if __name__ == "__main__":
    PROFILE_DIR = achar_perfil()
    CHROME_DIR = os.path.join(PROFILE_DIR, "chrome")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # só local, porta livre aleatória
    porta = srv.server_address[1]
    HOSTS_OK.update({f"127.0.0.1:{porta}", f"localhost:{porta}"})
    ORIGENS_OK.update({f"http://127.0.0.1:{porta}", f"http://localhost:{porta}"})
    url = f"http://127.0.0.1:{porta}/?t={TOKEN}"
    print("Perfil do Firefox encontrado.")
    print("Cortador rodando. Se a página não abrir sozinha, copie este endereço no navegador:")
    print(url)
    print("(Ctrl+C para parar)")
    abrir_navegador(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
