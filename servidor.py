import configparser
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

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
MAX_CAMADAS = 20  # limite de imagens + GIFs aplicados ao mesmo tempo

# Definidos ao iniciar (ver o final do arquivo)
PROFILE_DIR = None
CHROME_DIR = None
TOKEN = secrets.token_urlsafe(24)  # novo a cada execução
HOSTS_OK = set()
ORIGENS_OK = set()
LOCK = threading.Lock()  # o servidor atende vários pedidos ao mesmo tempo; o estado é alterado um por vez

# Marcas que delimitam, dentro do userChrome.css, o trecho gerenciado pela ferramenta.
# Tudo fora delas (suas outras regras) nunca é tocado.
MARC_INI = "/* TOOLBAR-STUDIO:INICIO (gerado pela ferramenta; nao edite entre as marcas) */"
MARC_FIM = "/* TOOLBAR-STUDIO:FIM */"

# Resto do tema, escrito só quando o userChrome.css ainda não existe
CSS_BASE = """/* 2. Barras transparentes */
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

# Valores aceitos para tamanho e posição de cada camada. Tudo que chega do painel
# (e tudo que é lido do arquivo de estado) é validado aqui antes de ir para o CSS.
# GIF: altura em px, encostado à direita
RE_TAM_GIF = re.compile(r"auto \d{1,4}px")
RE_POS_GIF = re.compile(r"right (-?\d{1,5})px top (-?\d{1,5})px")
# Imagem: "cover" (estabilizada, preenche a barra) ou tamanho/posição livres em px
RE_TAM_PNG = re.compile(r"cover|\d{1,5}px \d{1,5}px")
RE_POS_PNG = re.compile(r"80% center|-?\d{1,6}px -?\d{1,6}px")
RE_ARQ = re.compile(r"(ts_[0-9a-f]{8}|fundo)\.(png|gif)")
RE_ID = re.compile(r"[0-9a-f]{8}|legado-png|legado-gif")
RESERVADOS = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


# ---------- Perfil do Firefox ----------
def achar_perfil():
    env = os.environ.get("FIREFOX_PROFILE", "").strip().strip('"')
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    base = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox")
    ini = os.path.join(base, "profiles.ini")
    if os.path.isfile(ini):
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


def ja_na_pasta(pasta, exts, dados):
    """True se a pasta já tem um arquivo com exatamente o mesmo conteúdo.
    Evita a biblioteca ficar com cópias quando a mesma imagem é aberta várias vezes."""
    for nome in os.listdir(pasta):
        caminho = os.path.join(pasta, nome)
        if nome.lower().endswith(exts) and os.path.isfile(caminho) and os.path.getsize(caminho) == len(dados):
            with open(caminho, "rb") as f:
                if f.read() == dados:
                    return True
    return False


# ---------- Firefox ----------
def achar_firefox():
    candidatos = [
        shutil.which("firefox"),
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ]
    for c in candidatos:
        if c and os.path.exists(c):
            return c
    return None


def firefox_aberto():
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq firefox.exe"],
        capture_output=True,
    )
    return b"firefox.exe" in r.stdout.lower()


def reiniciar_firefox():
    exe = achar_firefox()
    if not exe:
        print("Não achei o firefox.exe. Abra o Firefox manualmente.")
        return
    # fecha de forma normal (preserva a sessão); força só se travar
    subprocess.run(["taskkill", "/IM", "firefox.exe"], capture_output=True)
    for _ in range(20):
        time.sleep(0.5)
        if not firefox_aberto():
            break
    else:
        subprocess.run(["taskkill", "/F", "/IM", "firefox.exe"], capture_output=True)
        time.sleep(1)
    subprocess.Popen([exe, "-profile", PROFILE_DIR])


# ---------- Camadas (várias imagens e GIFs ao mesmo tempo) ----------
# O estado fica em toolbar-studio.json, dentro da pasta chrome do perfil:
# {"offset": px, "camadas": [{"id", "tipo": "png"|"gif", "arq", "nome", "bs", "bp"}, ...]}
# As camadas ficam na ordem em que foram adicionadas; a ordem de empilhamento
# no CSS é calculada em ordenadas().
def camada_valida(c):
    """Confere o formato de uma camada antes de usar o nome do arquivo e os
    valores de tamanho/posição no CSS."""
    if not isinstance(c, dict):
        return False
    tipo, arq, cid, bs, bp = (c.get(k) for k in ("tipo", "arq", "id", "bs", "bp"))
    if tipo not in ("png", "gif") or not all(isinstance(v, str) for v in (arq, cid, bs, bp)):
        return False
    if not RE_ARQ.fullmatch(arq) or not arq.endswith("." + tipo) or not RE_ID.fullmatch(cid):
        return False
    if tipo == "gif":
        return bool(RE_TAM_GIF.fullmatch(bs) and RE_POS_GIF.fullmatch(bp))
    return bool(RE_TAM_PNG.fullmatch(bs) and RE_POS_PNG.fullmatch(bp))


def ordenadas(camadas):
    """Ordem de empilhamento do CSS (a primeira fica por cima): primeiro os GIFs,
    depois as imagens, e dentro de cada grupo a mais recente por cima."""
    gifs = [c for c in camadas if c["tipo"] == "gif"][::-1]
    imgs = [c for c in camadas if c["tipo"] == "png"][::-1]
    return gifs + imgs


def importar_legado():
    """Primeira execução desta versão: aproveita o fundo.png / fundo.gif e os
    valores do userChrome.css antigo, para nada sumir da barra."""
    estado = {"offset": 0, "camadas": []}
    try:
        with open(os.path.join(CHROME_DIR, "userChrome.css"), encoding="utf-8") as f:
            css = f.read()
    except OSError:
        css = ""
    if os.path.isfile(os.path.join(CHROME_DIR, "fundo.png")):
        estado["camadas"].append({"id": "legado-png", "tipo": "png", "arq": "fundo.png",
                                  "nome": "fundo.png", "bs": "cover", "bp": "80% center"})
    if os.path.isfile(os.path.join(CHROME_DIR, "fundo.gif")):
        m_tam = re.search(r"background-size:\s*auto (\d{1,4})px", css)
        m_pos = re.search(r"right (-?\d{1,5})px top (-?\d{1,5})px", css)
        estado["camadas"].append({
            "id": "legado-gif", "tipo": "gif", "arq": "fundo.gif", "nome": "fundo.gif",
            "bs": f"auto {m_tam.group(1)}px" if m_tam else "auto 110px",
            "bp": f"right {m_pos.group(1)}px top {m_pos.group(2)}px" if m_pos else "right 0px top 0px",
        })
    # deslocamento que você antes colocava à mão: right calc(1078px + 50px)
    m_off = re.search(r"calc\(\s*-?\d+px\s*\+\s*(\d{1,4})px\s*\)", css)
    if m_off:
        estado["offset"] = min(3000, int(m_off.group(1)))
    estado["camadas"] = [c for c in estado["camadas"] if camada_valida(c)]
    return estado


def carregar_estado():
    try:
        with open(os.path.join(CHROME_DIR, "toolbar-studio.json"), encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        dados = None
    if not isinstance(dados, dict):
        return importar_legado()
    offset = dados.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 3000:
        offset = 0
    camadas = dados.get("camadas")
    camadas = [c for c in camadas if camada_valida(c)] if isinstance(camadas, list) else []
    return {"offset": offset, "camadas": camadas}


def gerar_bloco(estado):
    """Monta o trecho do userChrome.css com todas as camadas, já na ordem de empilhamento."""
    camadas = ordenadas(estado["camadas"])
    linhas = ["#navigator-toolbox {"]
    if camadas:
        n = len(camadas)
        linhas.append("    background-image: " + ", ".join(f'url("{c["arq"]}")' for c in camadas) + " !important;")
        linhas.append("    background-size: " + ", ".join(c["bs"] for c in camadas) + " !important;")
        linhas.append("    background-position: " + ", ".join(c["bp"] for c in camadas) + " !important;")
        linhas.append("    background-repeat: " + ", ".join(["no-repeat"] * n) + " !important;")
    linhas += ["    background-color: #1a1a1a !important;", "    border: none !important;", "}"]
    # Com a barra de favoritos escondida, os GIFs são deslocados para a esquerda
    off = estado["offset"]
    if off and any(c["tipo"] == "gif" for c in camadas):
        posicoes = []
        for c in camadas:
            m = RE_POS_GIF.fullmatch(c["bp"]) if c["tipo"] == "gif" else None
            posicoes.append(f"right calc({m.group(1)}px + {off}px) top {m.group(2)}px" if m else c["bp"])
        linhas += ["", '#navigator-toolbox:has(#PersonalToolbar[collapsed="true"]) {',
                   "    background-position: " + ", ".join(posicoes) + " !important;", "}"]
    return MARC_INI + "\n" + "\n".join(linhas) + "\n" + MARC_FIM + "\n"


def escrever_css(estado):
    """Grava o trecho gerenciado no userChrome.css sem mexer no resto do arquivo."""
    caminho = os.path.join(CHROME_DIR, "userChrome.css")
    bloco = gerar_bloco(estado)
    if not os.path.exists(caminho):
        os.makedirs(CHROME_DIR, exist_ok=True)
        txt = bloco + "\n" + CSS_BASE
    else:
        with open(caminho, encoding="utf-8") as f:
            txt = f.read()
        shutil.copy(caminho, caminho + ".bak")
        ini, fim = txt.find(MARC_INI), txt.find(MARC_FIM)
        if ini != -1 and fim > ini:
            txt = txt[:ini] + bloco.rstrip("\n") + txt[fim + len(MARC_FIM):]
        else:
            # Arquivo da versão antiga: troca a regra do fundo pelo trecho gerenciado
            # e apaga a regra manual da barra de favoritos escondida (agora gerada aqui)
            txt = re.sub(r'#navigator-toolbox:has\(#PersonalToolbar\[collapsed="true"\]\)\s*\{[^}]*\}\s*', "", txt)
            txt, n = re.subn(r"#navigator-toolbox\s*\{[^}]*background-image[^}]*\}",
                             lambda m: bloco.rstrip("\n"), txt, count=1)
            if n == 0:
                txt = bloco + "\n" + txt
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(txt)


def aplicar_estado(estado):
    """Salva o estado e regenera o CSS (chamar com LOCK preso)."""
    os.makedirs(CHROME_DIR, exist_ok=True)
    gravar(os.path.join(CHROME_DIR, "toolbar-studio.json"), json.dumps(estado, indent=1).encode("utf-8"))
    escrever_css(estado)


def apagar_arquivo_camada(c):
    try:
        os.remove(os.path.join(CHROME_DIR, c["arq"]))
    except OSError:
        pass


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
        if caminho == "/estado":
            with LOCK:
                return self.responder(200, json.dumps(carregar_estado()).encode())
        if caminho.startswith("/camada/"):
            # só entrega arquivos que são camadas do estado atual
            arq = unquote(caminho[len("/camada/"):])
            with LOCK:
                ok = any(c["arq"] == arq for c in carregar_estado()["camadas"])
            alvo = os.path.join(CHROME_DIR, arq)
            if not ok or not os.path.isfile(alvo):
                return self.responder(404, b"{}")
            with open(alvo, "rb") as f:
                return self.responder(200, f.read(), TIPOS.get(os.path.splitext(arq)[1].lower(), "application/octet-stream"))
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
            if ja_na_pasta(pasta, exts, dados):
                return self.msg(200, "Já estava na biblioteca.")
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

    def camada_add(self, q):
        """Adiciona uma imagem ou GIF como nova camada, sem apagar as que já existem."""
        tipo = q.get("tipo", [""])[0]
        if tipo not in ("png", "gif"):
            return self.msg(400, "Tipo inválido.")
        dados = self.ler_corpo()
        if dados is None or not assinatura_ok("." + tipo, dados):
            return self.msg(400, "Arquivo inválido ou grande demais.")
        bs = q.get("bs", [""])[0]
        bp = q.get("bp", [""])[0]
        if tipo == "png":
            # sem parâmetros = imagem estabilizada (preenche a barra)
            bs = bs or "cover"
            bp = bp or "80% center"
        cid = secrets.token_hex(4)
        camada = {"id": cid, "tipo": tipo, "arq": f"ts_{cid}.{tipo}",
                  "nome": limpar_nome(q.get("nome", [""])[0])[:100], "bs": bs, "bp": bp}
        if not camada_valida(camada):
            return self.msg(400, "Posição inválida.")
        try:
            with LOCK:
                estado = carregar_estado()
                if len(estado["camadas"]) >= MAX_CAMADAS:
                    return self.msg(400, f"Limite de {MAX_CAMADAS} camadas. Remova alguma antes.")
                os.makedirs(CHROME_DIR, exist_ok=True)
                gravar(os.path.join(CHROME_DIR, camada["arq"]), dados)
                estado["camadas"].append(camada)
                aplicar_estado(estado)
        except OSError as e:
            print("Erro de arquivo:", type(e).__name__)
            return self.msg(500, "Erro ao gravar na pasta do perfil do Firefox.")
        self.msg(200, "Camada adicionada. O Firefox vai reiniciar agora.")
        threading.Thread(target=reiniciar_firefox, daemon=True).start()

    def camada_del(self, q):
        """Remove uma camada (ou todas, com id=todas) e o arquivo dela."""
        cid = q.get("id", [""])[0]
        try:
            with LOCK:
                estado = carregar_estado()
                if cid == "todas":
                    alvos = list(estado["camadas"])
                else:
                    alvos = [c for c in estado["camadas"] if c["id"] == cid]
                if not alvos:
                    return self.msg(404, "Camada não encontrada.")
                estado["camadas"] = [c for c in estado["camadas"] if c not in alvos]
                aplicar_estado(estado)
                for c in alvos:
                    apagar_arquivo_camada(c)
        except OSError as e:
            print("Erro de arquivo:", type(e).__name__)
            return self.msg(500, "Erro ao remover a camada.")
        self.msg(200, "Removida. O Firefox vai reiniciar agora.")
        threading.Thread(target=reiniciar_firefox, daemon=True).start()

    def definir_offset(self, q):
        """Deslocamento dos GIFs quando a barra de favoritos está escondida."""
        bruto = q.get("px", [""])[0]
        if not re.fullmatch(r"\d{1,4}", bruto) or int(bruto) > 3000:
            return self.msg(400, "Valor inválido.")
        try:
            with LOCK:
                estado = carregar_estado()
                estado["offset"] = int(bruto)
                aplicar_estado(estado)
        except OSError as e:
            print("Erro de arquivo:", type(e).__name__)
            return self.msg(500, "Erro ao gravar na pasta do perfil do Firefox.")
        self.msg(200, "Deslocamento salvo. O Firefox vai reiniciar agora.")
        threading.Thread(target=reiniciar_firefox, daemon=True).start()

    def do_POST(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if not self.autorizado(q):
            return self.responder(403, b"{}")
        partes = url.path.strip("/").split("/")
        if len(partes) == 2 and partes[0] in PASTAS and partes[1] in ("add", "del", "fav"):
            return self.lib_post(q, partes[0], partes[1])
        if url.path == "/camada/add":
            return self.camada_add(q)
        if url.path == "/camada/del":
            return self.camada_del(q)
        if url.path == "/offset":
            return self.definir_offset(q)
        self.responder(404, b"{}")


def abrir_navegador(url):
    # Edge, para a página não fechar junto com o Firefox
    try:
        subprocess.Popen(["cmd", "/c", "start", "msedge", url])
    except OSError:
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
