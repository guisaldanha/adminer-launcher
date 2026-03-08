"""
# Copyright 2026 Guilherme Saldanha
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""

"""
Adminer Launcher
=================================
- Lançador Adminer com PHP embutido, integração com Ollama (LLM local) e layout customizado.
- Janela nativa (pywebview) com funções de navegação.
- Menu: Configurações, Sobre, lista de servidores, Início, Voltar, Avançar, Recarregar, Sair.
- Ao selecionar um servidor do menu, abre o Adminer com login/senha/db pré-preenchidos via JS.
- Salva dados de acesso aos bancos de dados para login rápido.
- Senhas salvas criptografadas com chave do arquivo install.key (AES-256-GCM via cryptography).
- Salvar configurações exibe aviso e fecha o programa (alterações aplicadas no próximo início).
- Logging estruturado; debug:true gera adminer-launcher.log e abre ferramentas do desenvolvedor.
- Suporte multilíngue via arquivos lang/<code>.json; idioma padrão: en_US.

Dependências:
    pip install pywebview pyinstaller cryptography

1. Limpa a pasta dist antiga
Remove-Item -Recurse -Force .\dist -ErrorAction SilentlyContinue

2. Compila o Python para Executável
pyinstaller --name adminer-launcher --noconsole main.py --icon="docs/img/Icone-Adminer-Launcher.ico"

3. Copia a pasta 'lang' para dentro da 'dist\adminer-launcher'
Copy-Item -Path ".\lang" -Destination ".\dist\adminer-launcher\lang" -Recurse -Force

4. Gera o Instalador do Windows
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "InnoSetup\installer-script.iss"

"""

import base64
import datetime
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time

LAUNCHER_VERSION = "1.0.1"
ADMINER_VERSION  = "5.4.2"
PHP_VERSION      = "8.5.3"
DEFAULT_FORMAT_LOG = "%(asctime)s - [%(pathname)s:%(lineno)d]\t- %(levelname)-7s - %(message)s"

# ---------------------------------------------------------------------------
# Utilitários de caminho
# ---------------------------------------------------------------------------

def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_json_path() -> str: return os.path.join(exe_dir(), "config.json")
def install_key_path() -> str: return os.path.join(exe_dir(), "install.key")
def plugins_php_path() -> str: return os.path.join(exe_dir(), "adminer-plugins.php")
def log_file_path()   -> str:  return os.path.join(exe_dir(), "adminer-launcher.log")
def php_exe_path()    -> str:  return os.path.join(exe_dir(), "php", "php.exe")
def lang_dir()        -> str:  return os.path.join(exe_dir(), "lang")

# ---------------------------------------------------------------------------
# Criptografia de senhas (AES-256-GCM via cryptography)
# ---------------------------------------------------------------------------

def _derivar_chave(install_key: str) -> bytes:
    import hashlib
    return hashlib.sha256(install_key.strip().encode("utf-8")).digest()


def carregar_install_key() -> str:
    caminho = install_key_path()
    log.info("Procurando install.key em: %s", caminho)
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as f:
            chave = f.read().strip()
        if chave:
            log.info("install.key carregado com sucesso.")
            return chave
        else:
            log.warning("install.key encontrado mas está vazio.")
            return ""
    log.warning("install.key NÃO encontrado em: %s", caminho)
    return ""


def criptografar_senha(senha: str, install_key: str) -> str:
    if not senha:
        return ""
    if not install_key:
        return f"plain:{senha}"
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        chave  = _derivar_chave(install_key)
        nonce  = os.urandom(12)
        aesgcm = AESGCM(chave)
        ct     = aesgcm.encrypt(nonce, senha.encode("utf-8"), None)
        return "enc:" + base64.b64encode(nonce + ct).decode("utf-8")
    except ImportError:
        log.warning("Módulo 'cryptography' não instalado — senha salva em texto plano.")
        return f"plain:{senha}"
    except Exception as e:
        log.error("Erro ao criptografar senha: %s", e)
        return f"plain:{senha}"


def descriptografar_senha(valor: str, install_key: str) -> str:
    if not valor:
        return ""
    if valor.startswith("plain:"):
        return valor[6:]
    if not valor.startswith("enc:"):
        return valor
    if not install_key:
        log.warning("Senha criptografada encontrada mas install.key ausente.")
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        chave  = _derivar_chave(install_key)
        raw    = base64.b64decode(valor[4:])
        nonce  = raw[:12]
        ct     = raw[12:]
        aesgcm = AESGCM(chave)
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
    except ImportError:
        log.warning("Módulo 'cryptography' não instalado.")
        return ""
    except Exception as e:
        log.error("Erro ao descriptografar senha: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(debug: bool) -> None:
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter(DEFAULT_FORMAT_LOG)
    sh  = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if debug else logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if debug:
        fh = logging.FileHandler(log_file_path(), encoding="utf-8", mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config JSON
# ---------------------------------------------------------------------------

CONFIG_PADRAO: dict = {
    "app": {
        "title":     "Adminer Launcher",
        "port":      8081,
        "width":     1280,
        "height":    800,
        "maximized": False,
        "debug":     False,
        "lang":      "en_US",
    },
    "ollama": {
        "host":  "http://localhost:11434",
        "model": "qwen2.5-coder:3b",
    },
    "servers": {},
}


def carregar_config() -> dict:
    for caminho in (config_json_path(), resource_path("config.json")):
        if os.path.exists(caminho):
            try:
                with open(caminho, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                log.debug("config.json carregado de: %s", caminho)
                cfg = json.loads(json.dumps(CONFIG_PADRAO))
                for secao, valores in dados.items():
                    if isinstance(valores, dict) and secao in cfg and isinstance(cfg[secao], dict):
                        cfg[secao].update(valores)
                    else:
                        cfg[secao] = valores
                return cfg
            except Exception as e:
                log.error("Erro ao carregar config.json: %s", e)
    log.warning("config.json não encontrado — usando valores padrão.")
    return json.loads(json.dumps(CONFIG_PADRAO))


def salvar_config(cfg: dict) -> None:
    caminho = config_json_path()
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log.info("config.json salvo em: %s", caminho)


# ---------------------------------------------------------------------------
# Internacionalização (i18n)
# ---------------------------------------------------------------------------

def carregar_lang(code: str) -> dict:
    """
    Carrega o arquivo lang/<code>.json.
    Se não encontrar, tenta en_US como fallback.
    Retorna dict vazio em último caso.
    """
    for tentativa in (code, "en_US"):
        caminho = os.path.join(lang_dir(), f"{tentativa}.json")
        if os.path.exists(caminho):
            try:
                with open(caminho, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                log.debug("lang '%s' carregado de: %s", tentativa, caminho)
                return dados
            except Exception as e:
                log.error("Erro ao carregar lang '%s': %s", tentativa, e)
    log.warning("Nenhum arquivo de idioma encontrado — usando chaves como fallback.")
    return {}


def listar_langs() -> list:
    """
    Lê o diretório lang/ e retorna lista de dicts {code, name} ordenada pelo código.
    Usa a chave '_lang_name' de cada JSON como nome legível; fallback para o código.
    Sempre inclui en_US mesmo que o diretório esteja vazio.
    """
    langs = []
    d = lang_dir()
    if os.path.exists(d):
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".json"):
                continue
            code = fname[:-5]
            name = code
            try:
                with open(os.path.join(d, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
                name = data.get("_lang_name", code)
            except Exception:
                pass
            langs.append({"code": code, "name": name})
    if not langs:
        langs = [{"code": "en_US", "name": "English"}]
    return langs


# ---------------------------------------------------------------------------
# Aplicar config ao adminer-plugins.php
# ---------------------------------------------------------------------------

DRIVER_LABELS = {"pgsql": "PostgreSQL", "server": "MySQL/MariaDB", "sqlite": "SQLite"}


def _format_server_label(chave: str, host: str, driver: str) -> str:
    name = re.sub(r"([_]\d+)+$", "", chave)
    name = " ".join(w.capitalize() for w in name.split("_") if w)
    tipo = DRIVER_LABELS.get(driver, driver)
    return f"{tipo} — {name} ({host})" if host else f"{tipo} — {name}"


def _build_servers_block(cfg: dict) -> str:
    servers = cfg.get("servers", {})
    if not servers:
        return "new AdminerLoginServers([])"
    entries = []
    for chave, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        host   = srv.get("host", "").strip()
        driver = srv.get("driver", "pgsql").strip()
        desc   = srv.get("label") or _format_server_label(chave, host, driver)
        esc_desc = desc.replace("'", "\\'")
        esc_host = host.replace("'", "\\'")
        entries.append(
            f"        '{esc_desc}'"
            f" => ['server' => '{esc_host}', 'driver' => '{driver}'],"
        )
    return "new AdminerLoginServers([\n" + "\n".join(entries) + "\n    ])"


def aplicar_config_ao_adminer(cfg: dict) -> None:
    caminho = plugins_php_path()
    if not os.path.exists(caminho):
        log.warning("adminer-plugins.php não encontrado em: %s", caminho)
        log.info("Criando adminer-plugins.php básico.")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("<?php\n\n")
            f.write("return array(\n\n")
            f.write("    new AdminerSqlOllama('http://localhost:11434', 'qwen2.5-coder:3b')\n\n")
            f.write(");")
        log.info("adminer-plugins.php criado com conteúdo padrão.")

    with open(caminho, "r", encoding="utf-8") as f:
        conteudo = f.read()
    original = conteudo

    ollama = cfg.get("ollama", {})
    if ollama:
        host  = ollama.get("host",  "http://localhost:11434").strip().replace("'", "\\'")
        model = ollama.get("model", "qwen2.5-coder:3b").strip().replace("'", "\\'")
        _pat_ollama = (
            r"(?m)^(\s*)new\s+AdminerSqlOllama\s*\(\s*"
            r"('([^']*)'|\"([^\"]*)\")\s*,\s*"
            r"('([^']*)'|\"([^\"]*)\")\s*\)"
        )
        novo, n = re.subn(
            _pat_ollama,
            lambda m: m.group(1) + f"new AdminerSqlOllama('{host}', '{model}')",
            conteudo,
        )
        if n:
            conteudo = novo

    novo_bloco = _build_servers_block(cfg)
    novo, n = re.subn(
        r"new\s+AdminerLoginServers\s*\(\s*\[(?:[^\[\]]|\[(?:[^\[\]]|\[[^\[\]]*\])*\])*\]\s*\)",
        novo_bloco,
        conteudo,
        flags=re.DOTALL,
    )
    if n:
        conteudo = novo

    if conteudo != original:
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(conteudo)
        log.info("adminer-plugins.php atualizado.")
    else:
        log.debug("adminer-plugins.php sem alterações.")


# ---------------------------------------------------------------------------
# PHP Server
# ---------------------------------------------------------------------------

def encontrar_adminer(docroot: str) -> str:
    try:
        for f in os.listdir(docroot):
            if not os.path.isfile(os.path.join(docroot, f)):
                continue
            if not f.lower().endswith(".php"):
                continue
            if "adminer" not in f.lower():
                continue
            if f in ("index.php", "adminer-plugins.php"):
                continue
            if re.match(r"^adminer-(\d+[._]\d+[._]\d+)(-[\w]+)?\.php$", f, re.IGNORECASE) or f.lower() == "adminer.php":
                return f
        log.warning("Nenhum arquivo Adminer encontrado em: %s", docroot)
        return ""
    except Exception as e:
        log.error("Erro ao procurar adminer: %s", e)
        return ""


def adminer_url(cfg: dict, port: int) -> str:
    arquivo = encontrar_adminer(exe_dir())
    if arquivo:
        log.info("Abrindo: %s", arquivo)
        return f"http://localhost:{port}/{arquivo}"
    log.error("Arquivo Adminer principal não encontrado.")
    return f"http://localhost:{port}/"


def adminer_url_servidor(cfg: dict, port: int, srv: dict) -> str:
    from urllib.parse import quote

    arquivo = encontrar_adminer(exe_dir())
    base    = f"http://localhost:{port}/{arquivo}" if arquivo else f"http://localhost:{port}/"

    driver   = srv.get("driver",   "pgsql")
    host     = srv.get("host",     "")
    username = srv.get("username", "")
    db       = srv.get("db",       "")
    label    = srv.get("label") or _format_server_label(
        srv.get("_key", ""), host, driver
    )

    def qv(v: str) -> str:
        return quote(str(v), safe="")

    parts = []
    if driver == "sqlite":
        parts.append(f"sqlite={qv(db)}")
        if db:
            parts.append(f"db={qv(db)}")
        if username:
            parts.append(f"username={qv(username)}")
    else:
        if host:
            parts.append(f"{driver}={qv(host)}")
        if username:
            parts.append(f"username={qv(username)}")
        if db:
            parts.append(f"db={qv(db)}")

    if label:
        parts.append(f"auth[server]={qv(label)}")

    query = "&".join(parts)
    url   = f"{base}?{query}" if query else base
    log.debug("adminer_url_servidor: %s", url)
    return url


# ---------------------------------------------------------------------------
# JS de auto-login
# ---------------------------------------------------------------------------

def build_autologin_js(senha: str) -> str:
    if not senha:
        return ""
    senha_escaped = senha.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")
    return f"""
(function() {{
    var _t = 0;
    function _fill() {{
        var campo = document.querySelector('input[name="auth[password]"]');
        if (!campo) {{
            if (_t++ < 50) {{ setTimeout(_fill, 100); }}
            return;
        }}
        campo.value = '{senha_escaped}';
        var form = campo.closest('form');
        if (form) {{
            var btn = form.querySelector('input[type=submit], button[type=submit]');
            if (btn) btn.click();
        }}
    }}
    _fill();
}})();
"""


# ---------------------------------------------------------------------------
# Helpers para serialização de servidores para JS
# ---------------------------------------------------------------------------

def servidores_para_js(cfg: dict, install_key: str) -> str:
    servers = cfg.get("servers", {})
    lista   = []
    for chave, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        senha_enc   = srv.get("password", "")
        senha_dec   = descriptografar_senha(senha_enc, install_key) if senha_enc else ""
        label_final = srv.get("label") or _format_server_label(chave, srv.get("host", ""), srv.get("driver", "pgsql"))
        lista.append({
            "key":      chave,
            "label":    label_final,
            "driver":   srv.get("driver", "pgsql"),
            "host":     srv.get("host", ""),
            "username": srv.get("username", ""),
            "db":       srv.get("db", ""),
            "has_pass": bool(senha_dec),
            "password": senha_dec,
        })
    return json.dumps(lista, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Menubar JS (injetada na janela principal a cada carregamento)
# ---------------------------------------------------------------------------

def build_menubar_js(servidores_json: str, lang: dict) -> str:
    lang_json = json.dumps(lang, ensure_ascii=False)
    return (
        "(function() {\n"
        "  if (document.getElementById('_apt_menubar_host')) return;\n"
        "  const BAR_H = 36;\n"
        "  const SERVERS = " + servidores_json + ";\n"
        "  const LANG = " + lang_json + ";\n"
        "  function t(k) { return LANG[k] !== undefined ? LANG[k] : k; }\n"
        + r"""
  const host = document.createElement('div');
  host.id = '_apt_menubar_host';
  Object.assign(host.style, {
    position: 'fixed',
    top: '0', left: '0', right: '0',
    height: BAR_H + 'px',
    zIndex: '2147483647',
    contain: 'layout style',
  });

  const shadow = host.attachShadow({ mode: 'open' });

  const shadowStyle = document.createElement('style');
  shadowStyle.textContent = `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :host { display: block; width: 100%; height: 100%; }
    .bar {
      display: flex; align-items: center; width: 100%; height: 100%;
      background: #18181f; border-bottom: 1px solid #2e2e45;
      padding-left: 8px; gap: 4px;
      font-family: 'Segoe UI', system-ui, sans-serif;
      user-select: none; -webkit-user-select: none;
    }
    button {
      all: unset; box-sizing: border-box; color: #cdd6f4; font-size: 13px;
      padding: 0 14px; height: 28px; line-height: 28px; cursor: pointer;
      border-radius: 6px; white-space: nowrap;
      display: inline-flex; align-items: center; transition: background 0.15s;
    }
    button:hover { background: #2a2a3d; }
    .dd-wrap { position: relative; display: inline-block; }
    .dropdown {
      display: none; position: absolute; top: 32px; left: 0;
      background: #1e1e2e; border: 1px solid #3a3a55; border-radius: 8px;
      min-width: 240px; z-index: 1; box-shadow: 0 8px 24px rgba(0,0,0,.5);
      padding: 4px 0;
    }
    .dropdown.open { display: block; }
    .dropdown button {
      display: block; width: 100%; box-sizing: border-box; text-align: left;
      padding: 8px 12px; height: auto; line-height: 1.4; border-radius: 0;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .dropdown button:first-child { border-radius: 6px 6px 0 0; }
    .dropdown button:last-child  { border-radius: 0 0 6px 6px; }
    .dropdown button:only-child  { border-radius: 6px; }
  `;
  shadow.appendChild(shadowStyle);

  const bar = document.createElement('div');
  bar.className = 'bar';
  shadow.appendChild(bar);

  function makeBtn(label, clickFn) {
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.addEventListener('click', clickFn);
    return btn;
  }

  const FIXED_BUTTONS = [
    { key: 'menu.settings', action: 'settings' },
    { key: 'menu.about',    action: 'about'    },
  ];
  FIXED_BUTTONS.forEach(def => {
    bar.appendChild(makeBtn(t(def.key), () => window.pywebview.api.menu_action(def.action)));
  });

  if (SERVERS && SERVERS.length > 0) {
    const wrapper   = document.createElement('div');
    wrapper.className = 'dd-wrap';
    let dropdownOpen  = false;
    const dropdown  = document.createElement('div');
    dropdown.className = 'dropdown';

    const trigger = makeBtn(t('menu.servers'), () => {
      dropdownOpen = !dropdownOpen;
      dropdown.classList.toggle('open', dropdownOpen);
    });

    SERVERS.forEach(srv => {
      const driverIcon = {pgsql: '🐘', server: '🐬', sqlite: '📁'}[srv.driver] || '🗄️';
      const item = makeBtn(driverIcon + ' ' + srv.label, () => {
        dropdownOpen = false;
        dropdown.classList.remove('open');
        window.pywebview.api.menu_action('servidor:' + srv.key);
      });
      dropdown.appendChild(item);
    });

    shadow.addEventListener('click', (e) => {
      if (!wrapper.contains(e.target)) { dropdownOpen = false; dropdown.classList.remove('open'); }
    });
    document.addEventListener('click', (e) => {
      if (!host.contains(e.target)) { dropdownOpen = false; dropdown.classList.remove('open'); }
    });

    wrapper.appendChild(trigger);
    wrapper.appendChild(dropdown);
    bar.appendChild(wrapper);
  }

  const NAV_BUTTONS = [
    { key: 'menu.home',    action: 'inicial' },
    { key: 'menu.back',    action: 'back'    },
    { key: 'menu.forward', action: 'forward' },
    { key: 'menu.reload',  action: 'reload'  },
    { key: 'menu.quit',    action: 'quit'    },
  ];
  NAV_BUTTONS.forEach(def => {
    bar.appendChild(makeBtn(t(def.key), () => window.pywebview.api.menu_action(def.action)));
  });

  document.body.appendChild(host);

  const globalStyle = document.createElement('style');
  globalStyle.id = '_apt_style';
  globalStyle.textContent = `
    * { -webkit-user-select: text !important; user-select: text !important; }
    #_apt_menubar_host { -webkit-user-select: none !important; user-select: none !important; }
    #page { position: relative; margin-top: 4px; }
  `;
  document.head.appendChild(globalStyle);

  function _aptWrap() {
    if (document.getElementById('page')) return;
    const content = document.getElementById('content');
    const foot    = document.getElementById('foot');
    if (!content && !foot) return;
    const page = document.createElement('div');
    page.id = 'page';
    const ref = content || foot;
    document.body.insertBefore(page, ref);
    if (content) page.appendChild(content);
    if (foot)    page.appendChild(foot);
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', _aptWrap);
  else
    _aptWrap();

})();
"""
    )


# ---------------------------------------------------------------------------
# Download interceptor JS (injetado na janela principal a cada carregamento)
# ---------------------------------------------------------------------------

def build_download_interceptor_js(lang: dict) -> str:
    lang_json = json.dumps(lang, ensure_ascii=False)
    return (
        "(function() {\n"
        "  const LANG = " + lang_json + ";\n"
        "  function t(k) { return LANG[k] !== undefined ? LANG[k] : k; }\n"
        + r"""
  /* ── Toast flutuante ──────────────────────────────────────────────────── */
  var _toastEl = document.getElementById('_apt_dl_toast');
  if (!_toastEl) {
    _toastEl = document.createElement('div');
    _toastEl.id = '_apt_dl_toast';
    Object.assign(_toastEl.style, {
      position:     'fixed',
      bottom:       '24px',
      right:        '24px',
      background:   '#1e1e26',
      border:       '1px solid #2d2d38',
      borderRadius: '12px',
      padding:      '16px 20px',
      fontFamily:   "'Segoe UI', system-ui, sans-serif",
      fontSize:     '14px',
      color:        '#F6F7F9 !important',
      zIndex:       '2147483646',
      minWidth:     '320px',
      maxWidth:     '460px',
      boxShadow:    '0 12px 40px rgba(0,0,0,.6)',
      opacity:      '0',
      transform:    'translateY(14px)',
      transition:   'opacity .3s, transform .3s',
      pointerEvents:'none',
      lineHeight:   '1.6',
    });
    document.body.appendChild(_toastEl);
  }

  var _toastTimer = null;

  function _showToast(isError) {
    var el = document.getElementById('_apt_dl_toast');
    if (!el) return;
    if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
    el.style.borderColor   = isError ? '#e06c75' : '#3a3a55';
    el.style.background    = isError ? '#2a1a1e' : '#1e1e26';
    el.style.pointerEvents = 'auto';
    el.style.opacity       = '1';
    el.style.transform     = 'translateY(0)';
    _toastTimer = setTimeout(function() {
      el.style.opacity   = '0';
      el.style.transform = 'translateY(14px)';
      setTimeout(function() { el.style.pointerEvents = 'none'; }, 320);
      _toastTimer = null;
    }, 8000);
  }

  function _makeLink(text, color, clickFn) {
    var a = document.createElement('a');
    a.href        = '#';
    a.textContent = text;
    a.style.cssText = 'color:#dcdcdc !important;text-decoration:underline;cursor:pointer;';
    a.addEventListener('click', function(e) { e.preventDefault(); clickFn(); });
    return a;
  }

  function _makeEl(tag, css, text) {
    var el = document.createElement(tag);
    if (css)             el.style.cssText = css;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  /* Chamado pelo Python após salvar o arquivo */
  window._aptShowDownloadToast = function(filename, filepath, folderPath, error) {
    var el = document.getElementById('_apt_dl_toast');
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    var row = _makeEl('div', 'display:flex;align-items:flex-start;gap:10px;');

    if (error) {
      var icon  = _makeEl('span', 'font-size:20px;line-height:1;', '\u274C');
      var body  = _makeEl('div', '');
      var title = _makeEl('strong', 'color:#e06c75;', t('toast.error_title'));
      var msg   = _makeEl('div', 'font-size:12px;color:#F6F7F9 !important;margin-top:3px;', error);
      body.appendChild(title);
      body.appendChild(msg);
      row.appendChild(icon);
      row.appendChild(body);
      el.appendChild(row);
      _showToast(true);
      return;
    }

    var icon = _makeEl('span', 'font-size:20px;line-height:1;flex-shrink:0;', '\u2705');
    var body = _makeEl('div', 'flex:1;min-width:0;');

    var line1 = _makeEl('div', 'margin-bottom:5px;font-weight:600;word-break:break-all;');
    if (filepath) {
      var fileLink = _makeLink(filename, '#c4b5fd', function() {
        window.pywebview.api.open_file(filepath);
      });
      fileLink.style.fontWeight = '600';
      fileLink.style.wordBreak  = 'break-all';
      line1.appendChild(fileLink);
    } else {
      line1.textContent = filename || t('toast.open_file');
    }

    var line2 = _makeEl('div', 'font-size:12px;color:#F6F7F9 !important;');
    line2.appendChild(document.createTextNode(t('toast.saved_in') + ' '));
    if (folderPath) {
      var folderLink = _makeLink('backups', '#a78bfa', function() {
        window.pywebview.api.open_exports_folder();
      });
      line2.appendChild(folderLink);
    } else {
      line2.appendChild(document.createTextNode('backups'));
    }

    if (filepath) {
      var line3 = _makeEl('div', 'font-size:11px;color:#F6F7F9 !important;opacity:0.6;margin-top:4px;word-break:break-all;', filepath);
      body.appendChild(line1);
      body.appendChild(line2);
      body.appendChild(line3);
    } else {
      body.appendChild(line1);
      body.appendChild(line2);
    }

    row.appendChild(icon);
    row.appendChild(body);
    el.appendChild(row);
    _showToast(false);
  };

  /* ── Interceptor de export ───────────────────────────────────────────── */
  function _isExportSubmit(e) {
    if (window.location.search.includes('dump=')) return true;
    var btn = e.submitter;
    if (btn && btn.name === 'export') return true;
    return false;
  }

  function _interceptForm(form) {
    if (form._aptDlIntercepted) return;
    form._aptDlIntercepted = true;

    form.addEventListener('submit', async function(e) {
      if (!_isExportSubmit(e)) return;
      e.preventDefault();
      e.stopImmediatePropagation();

      /* Toast de progresso */
      (function() {
        var el = document.getElementById('_apt_dl_toast');
        if (!el) return;
        if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
        while (el.firstChild) el.removeChild(el.firstChild);
        var row  = _makeEl('div', 'display:flex;align-items:center;gap:10px;');
        var icon = _makeEl('span', 'font-size:18px;', '\u23F3');
        var txt  = _makeEl('span', 'color:#F6F7F9 !important;', t('toast.generating'));
        row.appendChild(icon); row.appendChild(txt);
        el.appendChild(row);
        el.style.borderColor   = '#3a3a55';
        el.style.background    = '#1e1e26';
        el.style.pointerEvents = 'auto';
        el.style.opacity       = '1';
        el.style.transform     = 'translateY(0)';
      })();

      try {
        var formData = new FormData(form);
        if (e.submitter && e.submitter.name) {
          formData.append(e.submitter.name, e.submitter.value || '');
        }
        var action   = form.action || window.location.href;
        var response = await fetch(action, {
          method:      'POST',
          body:        formData,
          credentials: 'include',
        });

        var cd = response.headers.get('Content-Disposition') || '';
        var ct = response.headers.get('Content-Type')        || '';

        /* Resposta de texto ("abrir") → exibe em overlay */
        if (!cd.includes('attachment') && ct.includes('text/')) {
          var textContent = await response.text();
          var el = document.getElementById('_apt_dl_toast');
          if (el) { el.style.opacity = '0'; el.style.transform = 'translateY(14px)'; el.style.pointerEvents = 'none'; }
          if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
          _aptShowTextOverlay(textContent, cd, ct);
          return;
        }

        /* Resposta sem attachment → descarta silenciosamente */
        if (!cd.includes('attachment')) {
          var el = document.getElementById('_apt_dl_toast');
          if (el) { el.style.opacity = '0'; el.style.transform = 'translateY(14px)'; el.style.pointerEvents = 'none'; }
          if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
          return;
        }

        /* Arquivo para download */
        var blob   = await response.blob();
        var reader = new FileReader();
        reader.onload = function() {
          var b64 = reader.result.split(',')[1];
          window.pywebview.api.save_export(b64, cd);
        };
        reader.onerror = function() {
          window._aptShowDownloadToast(null, null, null, t('toast.read_error'));
        };
        reader.readAsDataURL(blob);

      } catch (err) {
        window._aptShowDownloadToast(null, null, null, err.message || t('toast.unknown_error'));
      }
    }, true);
  }

  /* ── Overlay de texto para opção "abrir" ────────────────────────────── */
  function _aptShowTextOverlay(content, cd, ct) {
    var existing = document.getElementById('_apt_text_overlay');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    var m        = cd.match(/filename[^;=\n]*=([\"']?)([^;\n\"']+)\1/);
    var filename = m ? m[2].trim() : 'export';

    var contentType = ct.includes('csv') ? 'CSV' : ct.includes('html') ? 'HTML' : 'SQL';

    var overlay = document.createElement('div');
    overlay.id = '_apt_text_overlay';
    Object.assign(overlay.style, {
      position:      'fixed',
      top:           '36px', left: '0', right: '0', bottom: '0',
      background:    '#0f0f14',
      zIndex:        '2147483645',
      display:       'flex',
      flexDirection: 'column',
      fontFamily:    "'Segoe UI', system-ui, sans-serif",
    });

    var bar = document.createElement('div');
    Object.assign(bar.style, {
      display:        'flex',
      alignItems:     'center',
      justifyContent: 'space-between',
      padding:        '10px 16px',
      background:     '#1e1e26',
      borderBottom:   '1px solid #2d2d38',
      flexShrink:     '0',
      gap:            '12px',
    });

    var titleEl = document.createElement('span');
    titleEl.textContent = '\uD83D\uDCC4 ' + filename;
    Object.assign(titleEl.style, {
      color: '#F6F7F9', fontSize: '13px', fontWeight: '600',
      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    });

    var btnGroup = document.createElement('div');
    Object.assign(btnGroup.style, { display: 'flex', gap: '8px', flexShrink: '0' });

    var btnCopy = document.createElement('button');
    btnCopy.textContent = t('toast.copy');
    _styleOverlayBtn(btnCopy, '#2a2a3d', '#F6F7F9');
    btnCopy.addEventListener('click', function() {
      navigator.clipboard.writeText(content).then(function() {
        btnCopy.textContent = t('toast.copied');
        setTimeout(function() { btnCopy.textContent = t('toast.copy'); }, 2000);
      }).catch(function() {
        btnCopy.textContent = t('toast.copy_error');
        setTimeout(function() { btnCopy.textContent = t('toast.copy'); }, 2000);
      });
    });

    var btnSave = document.createElement('button');
    btnSave.textContent = t('toast.save');
    _styleOverlayBtn(btnSave, '#3a2a5a', '#c4b5fd');
    btnSave.addEventListener('click', function() {
      try {
        var b64    = btoa(unescape(encodeURIComponent(content)));
        var fakeCd = 'attachment; filename=' + filename;
        window.pywebview.api.save_export(b64, fakeCd);
      } catch(err) {
        window._aptShowDownloadToast(null, null, null, t('toast.save_error') + ' ' + err.message);
      }
    });

    var btnClose = document.createElement('button');
    btnClose.textContent = t('toast.close');
    _styleOverlayBtn(btnClose, '#3a1a1a', '#e06c75');
    btnClose.addEventListener('click', function() {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    });

    btnGroup.appendChild(btnCopy);
    btnGroup.appendChild(btnSave);
    btnGroup.appendChild(btnClose);
    bar.appendChild(titleEl);
    bar.appendChild(btnGroup);

    var pre = document.createElement('pre');
    Object.assign(pre.style, {
      flex:       '1',
      margin:     '0',
      padding:    '16px 20px',
      overflowY:  'auto',
      overflowX:  'auto',
      color:      '#F6F7F9',
      fontSize:   '12.5px',
      lineHeight: '1.6',
      fontFamily: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
      whiteSpace: 'pre',
      background: '#0f0f14',
    });
    pre.textContent = content;

    var lineCount = (content.match(/\n/g) || []).length + 1;
    var counter   = document.createElement('div');
    Object.assign(counter.style, {
      padding:    '6px 16px',
      background: '#1e1e26',
      borderTop:  '1px solid #2d2d38',
      color:      '#6e7080',
      fontSize:   '11px',
      flexShrink: '0',
    });
    counter.textContent = contentType + ' \u2022 ' + lineCount + ' ' + t('overlay.lines') + ' \u2022 ' +
      (content.length > 1024
        ? (content.length / 1024).toFixed(1) + ' KB'
        : content.length + ' bytes');

    overlay.appendChild(bar);
    overlay.appendChild(pre);
    overlay.appendChild(counter);
    document.body.appendChild(overlay);
  }

  function _styleOverlayBtn(btn, bg, color) {
    Object.assign(btn.style, {
      background:   bg,
      color:        color,
      border:       '1px solid ' + color + '33',
      borderRadius: '6px',
      padding:      '6px 14px',
      fontSize:     '12px',
      fontWeight:   '600',
      cursor:       'pointer',
      fontFamily:   'inherit',
      whiteSpace:   'nowrap',
      transition:   'opacity .15s',
    });
    btn.addEventListener('mouseover', function() { btn.style.opacity = '0.8'; });
    btn.addEventListener('mouseout',  function() { btn.style.opacity = '1'; });
  }

  function _interceptAllForms() {
    document.querySelectorAll('form').forEach(_interceptForm);
  }

  _interceptAllForms();

  var _observer = new MutationObserver(function(mutations) {
    mutations.forEach(function(m) {
      m.addedNodes.forEach(function(node) {
        if (node.nodeType !== 1) return;
        if (node.tagName === 'FORM') _interceptForm(node);
        node.querySelectorAll && node.querySelectorAll('form').forEach(_interceptForm);
      });
    });
  });
  _observer.observe(document.body, { childList: true, subtree: true });

  /* ── Re-executa <script nonce> bloqueados pelo CSP do pywebview ──────── */
  function _rerunNonceScript(scriptEl) {
    if (scriptEl._aptRerun) return;
    if (!scriptEl.hasAttribute('nonce')) return;
    var code = scriptEl.textContent || '';
    if (!code.trim()) return;
    scriptEl._aptRerun = true;

    var replacement = document.createElement('script');
    replacement._aptRerun = true;

    /* Envolve o código em IIFE por dois motivos:
     * 1. let/const dentro de função têm escopo próprio — nunca conflitam
     *    com declarações do script original que o pywebview já executou.
     *    (let/const no topo de um <script> não são acessíveis entre scripts,
     *     então o comportamento externo permanece idêntico.)
     * 2. Shimamos document.currentScript via own-property configurável para
     *    que qsl() do Adminer continue lendo previousElementSibling
     *    corretamente. O finally remove a own-property, restaurando o getter
     *    do protótipo automaticamente. */
    var uid = '_aptCS' + (Math.random() * 1e9 | 0);
    window[uid] = replacement;
    replacement.textContent =
      '(function(){' +
        'var _el=window["' + uid + '"];delete window["' + uid + '"];' +
        'Object.defineProperty(document,"currentScript",{get:function(){return _el;},configurable:true,enumerable:false});' +
        'try{' + code + '}finally{try{delete document.currentScript;}catch(e){}}' +
      '})();';

    if (scriptEl.parentNode) {
      scriptEl.parentNode.replaceChild(replacement, scriptEl);
    }
  }

  document.querySelectorAll('script[nonce]').forEach(_rerunNonceScript);

  var _nonceObserver = new MutationObserver(function(mutations) {
    mutations.forEach(function(m) {
      m.addedNodes.forEach(function(node) {
        if (!node || node.nodeType !== 1) return;
        if (node.tagName === 'SCRIPT') {
          _rerunNonceScript(node);
        } else if (node.querySelectorAll) {
          node.querySelectorAll('script[nonce]').forEach(_rerunNonceScript);
        }
      });
    });
  });
  _nonceObserver.observe(document.documentElement, { childList: true, subtree: true });

})();
"""
    )


# ---------------------------------------------------------------------------
# HTML — Configurações
# ---------------------------------------------------------------------------

# Template raw: usa __LANG_JSON__ e __LANGS_JSON__ como marcadores Python;
# não é f-string, portanto CSS e JS podem usar {} sem escaping.
_SETTINGS_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:#18181f; --surface:#1e1e26; --border:#2d2d38;
    --accent:#7c6af7; --accent-h:#9580ff;
    --danger:#e06c75; --text:#c8cdd8; --muted:#6e7080;
    --input-bg:#131318; --r:8px;
  }
  body { font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg); color:var(--text); padding:24px 24px 90px; }
  h1   { font-size:1.15rem; font-weight:700; color:#fff; margin-bottom:24px; }
  h2   { font-size:.68rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:var(--accent); margin-bottom:12px; padding-bottom:6px; border-bottom:1px solid var(--border); }
  .section { margin-bottom:28px; }
  .field   { margin-bottom:14px; }
  label    { display:block; font-size:.8rem; color:var(--muted); margin-bottom:5px; }
  input[type=text],input[type=number],input[type=password] {
    display:block; width:100%; padding:8px 12px;
    background:var(--input-bg); border:1px solid var(--border);
    border-radius:var(--r); color:var(--text); font-size:.9rem;
    font-family:inherit; outline:none; transition:border-color .2s;
    -webkit-appearance:none; appearance:none;
  }
  input:focus { border-color:var(--accent); }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; }
  .toggle { display:flex; align-items:center; gap:10px; font-size:.88rem; cursor:pointer; user-select:none; }
  .toggle input[type=checkbox] { width:38px; height:20px; -webkit-appearance:none; appearance:none; background:var(--border); border-radius:20px; position:relative; cursor:pointer; transition:background .2s; flex-shrink:0; padding:0; }
  .toggle input[type=checkbox]::after { content:''; position:absolute; width:14px; height:14px; background:#fff; border-radius:50%; top:3px; left:3px; transition:left .2s; }
  .toggle input[type=checkbox]:checked { background:var(--accent); }
  .toggle input[type=checkbox]:checked::after { left:21px; }
  .flags { display:flex; gap:24px; margin-top:6px; }
  .server-list { display:flex; flex-direction:column; gap:10px; }
  .srv { background:var(--surface); border:1px solid var(--border); border-radius:var(--r); padding:12px; }
  .srv-head { display:flex; gap:8px; align-items:center; margin-bottom:10px; }
  .srv-head input { flex:1; font-weight:600; }
  select {
    display:block; width:100%; padding:8px 10px;
    background:var(--input-bg); border:1px solid var(--border);
    border-radius:var(--r); color:var(--text); font-size:.88rem;
    font-family:inherit; outline:none; cursor:pointer;
    -webkit-appearance:none; appearance:none;
  }
  select:focus { border-color:var(--accent); }
  .btn-icon { background:none; border:1px solid var(--border); border-radius:var(--r); color:var(--muted); width:32px; height:32px; display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:.95rem; flex-shrink:0; transition:background .15s,color .15s,border-color .15s; font-family:inherit; }
  .btn-icon.del:hover { background:var(--danger); border-color:var(--danger); color:#fff; }
  .btn-add { margin-top:10px; padding:8px 14px; background:none; border:1px dashed var(--border); border-radius:var(--r); color:var(--muted); font-size:.85rem; cursor:pointer; width:100%; transition:border-color .2s,color .2s; font-family:inherit; }
  .btn-add:hover { border-color:var(--accent); color:var(--accent); }
  .footer { position:fixed; bottom:0; left:0; right:0; background:var(--bg); padding:14px 24px; display:flex; gap:10px; justify-content:flex-end; border-top:1px solid var(--border); }
  .btn { padding:9px 22px; border-radius:var(--r); font-size:.88rem; font-weight:600; cursor:pointer; border:none; transition:opacity .2s,transform .1s; font-family:inherit; }
  .btn:active { transform:scale(.97); }
  .btn-primary   { background:var(--accent); color:#fff; }
  .btn-primary:hover { background:var(--accent-h); }
  .btn-secondary { background:var(--surface); color:var(--text); border:1px solid var(--border); }
  .btn-secondary:hover { background:var(--border); }
  .toast { position:fixed; bottom:72px; right:20px; background:var(--accent); color:#fff; padding:10px 18px; border-radius:var(--r); font-size:.85rem; font-weight:600; opacity:0; transform:translateY(8px); transition:opacity .3s,transform .3s; pointer-events:none; z-index:9999; }
  .toast.show { opacity:1; transform:translateY(0); }
  .pass-hint { font-size:.72rem; color:var(--muted); margin-top:3px; }
</style>
</head>
<body>
<h1 data-i18n="settings.h1">⚙️ Settings — Adminer Launcher</h1>

<div class="section">
  <h2 data-i18n="settings.section.app">Application</h2>
  <div class="grid2">
    <div class="field"><label data-i18n="settings.app.window_title">Window title</label><input type="text" id="app_title"></div>
    <div class="field"><label data-i18n="settings.app.php_port">PHP server port</label><input type="number" id="app_port" min="1024" max="65535"></div>
    <div class="field"><label data-i18n="settings.app.width">Width (px)</label><input type="number" id="app_width" min="640"></div>
    <div class="field"><label data-i18n="settings.app.height">Height (px)</label><input type="number" id="app_height" min="400"></div>
  </div>
  <div class="flags">
    <label class="toggle"><input type="checkbox" id="app_maximized"> <span data-i18n="settings.app.maximized">Start maximized</span></label>
    <label class="toggle"><input type="checkbox" id="app_debug"> <span data-i18n="settings.app.debug">Debug mode (generates log)</span></label>
  </div>
</div>

<div class="section">
  <h2 data-i18n="settings.section.language">Language</h2>
  <div class="field" style="max-width:280px;">
    <label data-i18n="settings.lang.label">Interface language</label>
    <select id="app_lang"></select>
  </div>
</div>

<div class="section">
  <h2 data-i18n="settings.section.ollama">Ollama — Local SQL Assistant</h2>
  <div class="grid2">
    <div class="field"><label data-i18n="settings.ollama.host">Host</label><input type="text" id="ollama_host" placeholder="http://localhost:11434"></div>
    <div class="field"><label data-i18n="settings.ollama.model">Model</label><input type="text" id="ollama_model" placeholder="qwen2.5-coder:3b"></div>
  </div>
</div>

<div class="section">
  <h2 data-i18n="settings.section.servers">Servers</h2>
  <div class="server-list" id="server-list"></div>
  <button class="btn-add" id="btn-add-server" data-i18n="settings.server.add">＋ Add server</button>
</div>

<div class="toast" id="toast"></div>

<div class="footer">
  <button class="btn btn-secondary" id="btn-cancel" data-i18n="settings.btn.cancel">Cancel</button>
  <button class="btn btn-primary"   id="btn-save"   data-i18n="settings.btn.save">💾 Save and restart</button>
</div>

<script>
const LANG  = __LANG_JSON__;
const LANGS = __LANGS_JSON__;
function t(k) { return LANG[k] !== undefined ? LANG[k] : k; }

/* Aplica data-i18n ao DOM */
function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
}

/* Popula o select de idiomas */
function populateLangs(currentCode) {
  const sel = document.getElementById('app_lang');
  sel.innerHTML = '';
  LANGS.forEach(l => {
    const opt = document.createElement('option');
    opt.value = l.code;
    opt.textContent = l.name;
    if (l.code === currentCode) opt.selected = true;
    sel.appendChild(opt);
  });
}

const DRIVERS = ['pgsql','server','sqlite'];
const DRV_LBL = {pgsql:'PostgreSQL', server:'MySQL/MariaDB', sqlite:'SQLite'};

function toast(msg, ok=true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.background = ok ? 'var(--accent)' : '#e06c75';
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}

function esc(s) { return String(s??'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

function addServerRow(srv={}) {
  const list   = document.getElementById('server-list');
  const card   = document.createElement('div');
  card.className = 'srv';
  const driver = srv.driver || 'pgsql';
  const opts   = DRIVERS.map(d => `<option value="${d}"${d===driver?' selected':''}>${DRV_LBL[d]}</option>`).join('');
  card.innerHTML = `
    <div class="srv-head">
      <input type="text" class="srv-label" placeholder="${esc(t('settings.server.display_name_placeholder'))}" value="${esc(srv.label||'')}">
      <button class="btn-icon del" title="${esc(t('settings.server.remove'))}" onclick="this.closest('.'+'srv').remove()">🗑</button>
    </div>
    <div class="grid2">
      <div class="field"><label>${esc(t('settings.server.host'))}</label>
        <input type="text" class="srv-host" placeholder="${esc(t('settings.server.host_placeholder'))}" value="${esc(srv.host||'')}">
      </div>
      <div class="field"><label>${esc(t('settings.server.driver'))}</label>
        <select class="srv-driver">${opts}</select>
      </div>
    </div>
    <div class="grid3">
      <div class="field"><label>${esc(t('settings.server.user'))}</label>
        <input type="text" class="srv-user" placeholder="${esc(t('settings.server.user_placeholder'))}" value="${esc(srv.username||'')}">
      </div>
      <div class="field"><label>${esc(t('settings.server.password'))}</label>
        <input type="password" class="srv-pass" placeholder="••••••••" value="${esc(srv._pass_plain||'')}">
        <p class="pass-hint">${esc(t('settings.server.password_hint'))}</p>
      </div>
      <div class="field"><label>${esc(t('settings.server.db'))}</label>
        <input type="text" class="srv-db" placeholder="${esc(t('settings.server.db_placeholder'))}" value="${esc(srv.db||'')}">
      </div>
    </div>`;
  list.appendChild(card);
}

document.getElementById('btn-add-server').addEventListener('click', () => addServerRow({}));

document.getElementById('btn-cancel').addEventListener('click', () => window.pywebview.api.fechar());

document.getElementById('btn-save').addEventListener('click', salvar);

async function load() {
  const cfg = await window.pywebview.api.get_config();
  document.getElementById('app_title').value       = cfg.app?.title     ?? 'Adminer Launcher';
  document.getElementById('app_port').value        = cfg.app?.port      ?? 8081;
  document.getElementById('app_width').value       = cfg.app?.width     ?? 1280;
  document.getElementById('app_height').value      = cfg.app?.height    ?? 800;
  document.getElementById('app_maximized').checked = cfg.app?.maximized === true || cfg.app?.maximized === 'true';
  document.getElementById('app_debug').checked     = cfg.app?.debug     === true || cfg.app?.debug     === 'true';
  document.getElementById('ollama_host').value     = cfg.ollama?.host   ?? '';
  document.getElementById('ollama_model').value    = cfg.ollama?.model  ?? '';
  populateLangs(cfg.app?.lang ?? 'en_US');

  const list = document.getElementById('server-list');
  list.innerHTML = '';
  for (const srv of Object.values(cfg.servers ?? {})) {
    addServerRow(srv);
  }
}

async function salvar() {
  const servers = {};
  let erros = [];
  document.querySelectorAll('.srv').forEach(card => {
    const label = card.querySelector('.srv-label')?.value.trim();
    if (!label) { erros.push(t('settings.error.empty_label')); return; }
    const key = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
    servers[key] = {
      label,
      host:        card.querySelector('.srv-host')?.value.trim()  || '',
      driver:      card.querySelector('.srv-driver')?.value       || 'pgsql',
      username:    card.querySelector('.srv-user')?.value.trim()  || '',
      _pass_plain: card.querySelector('.srv-pass')?.value         || '',
      db:          card.querySelector('.srv-db')?.value.trim()    || '',
    };
  });

  if (erros.length) { toast('❌ ' + erros[0], false); return; }

  const cfg = {
    app: {
      title:    document.getElementById('app_title').value,
      port:     parseInt(document.getElementById('app_port').value)   || 8081,
      width:    parseInt(document.getElementById('app_width').value)  || 1280,
      height:   parseInt(document.getElementById('app_height').value) || 800,
      maximized: document.getElementById('app_maximized').checked,
      debug:     document.getElementById('app_debug').checked,
      lang:      document.getElementById('app_lang').value,
    },
    ollama: {
      host:  document.getElementById('ollama_host').value,
      model: document.getElementById('ollama_model').value,
    },
    servers,
  };

  const ok = await window.pywebview.api.salvar_config(cfg);
  if (ok) { toast(t('settings.success.saved')); setTimeout(() => window.pywebview.api.on_settings_saved(), 1400); }
  else    { toast(t('settings.error.save_failed'), false); }
}

document.addEventListener('DOMContentLoaded', applyI18n);
window.addEventListener('pywebviewready', load);
</script>
</body>
</html>
"""


def build_settings_html(lang: dict, current_lang: str, available_langs: list) -> str:
    """Injeta as traduções e a lista de idiomas no template HTML de configurações."""
    html = _SETTINGS_HTML_TEMPLATE
    html = html.replace("__LANG_JSON__",  json.dumps(lang,            ensure_ascii=False))
    html = html.replace("__LANGS_JSON__", json.dumps(available_langs, ensure_ascii=False))
    return html


# ---------------------------------------------------------------------------
# HTML — Sobre
# ---------------------------------------------------------------------------

_ABOUT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>About — Adminer Launcher</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
  :root{--bg:#18181f;--surface:#1e1e26;--border:#2d2d38;--accent:#7c6af7;--text:#c8cdd8;--muted:#6e7080;--r:10px;}
  body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    min-height:100vh;padding:32px 24px;text-align:center;}
  .logo{width:64px;height:64px;margin-bottom:20px;
    background:linear-gradient(135deg,#7c6af7,#9580ff);border-radius:16px;
    display:flex;align-items:center;justify-content:center;font-size:34px;
    box-shadow:0 4px 20px rgba(124,106,247,.35);}
  h1{font-size:1.4rem;font-weight:700;color:#fff;margin-bottom:6px;}
  a{color:#98b1ff;text-decoration:underline;}
  .version{display:inline-block;background:var(--surface);border:1px solid var(--border);
    border-radius:20px;padding:3px 14px;font-size:.78rem;color:var(--muted);margin-bottom:28px;}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
    padding:20px 24px;width:100%;max-width:360px;margin-bottom:28px;text-align:left;}
  .row{display:flex;flex-direction:column;gap:14px;}
  .item label{display:block;font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:4px;}
  .item span{font-size:.88rem;color:var(--text);line-height:1.5;}
  .btn{padding:10px 36px;background:var(--accent);color:#fff;border:none;border-radius:var(--r);
    font-size:.9rem;font-weight:600;cursor:pointer;font-family:inherit;transition:background .2s,transform .1s;}
  .btn:hover{background:#9580ff;}
  .btn:active{transform:scale(.97);}
</style>
</head>
<body>
  <div class="logo">🗄️</div>
  <h1>Adminer Launcher</h1>
  <span class="version">v__LAUNCHER_VERSION__</span>
  <div class="card">
    <div class="row">
      <div class="item"><label data-i18n="about.launcher_version">Launcher Version</label><span>v__LAUNCHER_VERSION__</span></div>
      <div class="item"><label data-i18n="about.adminer_version">Adminer Version</label><span>v__ADMINER_VERSION__</span></div>
      <div class="item"><label data-i18n="about.php_version">PHP Version</label><span>v__PHP_VERSION__</span></div>
      <div class="item"><label data-i18n="about.developed_by">Developed by</label><span>Guilherme Saldanha</span></div>
      <div class="item"><label data-i18n="about.site">Website</label><span><a href="https://guisaldanha.com" target="_blank">https://guisaldanha.com</a></span></div>
      <div class="item"><label data-i18n="about.github">GitHub</label><span><a href="https://github.com/guisaldanha" target="_blank">https://github.com/guisaldanha</a></span></div>
      <div class="item"><label data-i18n="about.goal">Goal</label>
        <span data-i18n="about.goal_text">Lightweight database manager — PostgreSQL, MySQL and SQLite — with web interface via Adminer running locally.</span>
      </div>
      <div class="item"><label data-i18n="about.languages">Languages</label>
        <span>PHP · Python · JavaScript</span>
      </div>
    </div>
  </div>
  <button class="btn" id="btn-close" data-i18n="about.close">Close</button>
<script>
const LANG = __LANG_JSON__;
function t(k) { return LANG[k] !== undefined ? LANG[k] : k; }
function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
}
document.addEventListener('DOMContentLoaded', applyI18n);
document.getElementById('btn-close').addEventListener('click', () => window.pywebview.api.fechar());
</script>
</body>
</html>
"""


def build_about_html(lang: dict) -> str:
    """Injeta versões e traduções no template HTML Sobre."""
    html = _ABOUT_HTML_TEMPLATE
    html = html.replace("__LANG_JSON__",        json.dumps(lang, ensure_ascii=False))
    html = html.replace("__LAUNCHER_VERSION__", LAUNCHER_VERSION)
    html = html.replace("__ADMINER_VERSION__",  ADMINER_VERSION)
    html = html.replace("__PHP_VERSION__",      PHP_VERSION)
    return html


# ---------------------------------------------------------------------------
# APIs JS
# ---------------------------------------------------------------------------

class SimpleCloseAPI:
    def __init__(self):
        self._window = None

    def set_window(self, w):
        self._window = w

    def fechar(self):
        if self._window:
            self._window.destroy()


class SettingsAPI:
    def __init__(self, cfg: dict, install_key: str, state: dict):
        self._cfg         = cfg
        self._install_key = install_key
        self._state       = state
        self._window      = None

    def set_window(self, w):
        self._window = w

    def get_config(self) -> dict:
        import copy
        cfg_view = copy.deepcopy(self._cfg)
        for chave, srv in cfg_view.get("servers", {}).items():
            enc = srv.get("password", "")
            srv["_pass_plain"] = descriptografar_senha(enc, self._install_key) if enc else ""
        return cfg_view

    def salvar_config(self, dados: dict) -> bool:
        try:
            servers_raw   = dados.get("servers", {})
            servers_final = {}
            for key, srv in servers_raw.items():
                chave = key.strip().replace(" ", "_")
                if not chave:
                    continue
                senha_plain       = srv.pop("_pass_plain", "") or ""
                srv["password"]   = criptografar_senha(senha_plain, self._install_key)
                servers_final[chave] = srv

            dados["servers"] = servers_final

            app = dados.get("app", {})
            app["port"]      = int(app.get("port",  8081))
            app["width"]     = int(app.get("width", 1280))
            app["height"]    = int(app.get("height", 800))
            app["maximized"] = bool(app.get("maximized", False))
            app["debug"]     = bool(app.get("debug",     False))
            app["lang"]      = str(app.get("lang", "en_US"))

            self._cfg.clear()
            self._cfg.update(dados)
            salvar_config(self._cfg)
            log.info("Configurações salvas.")
            return True
        except Exception as e:
            log.error("Falha ao salvar: %s", e)
            return False

    def on_settings_saved(self):
        log.info("Configurações salvas. Reiniciando para aplicar.")
        try:
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen([sys.executable] + sys.argv[1:], **kwargs)
            log.info("Nova instância iniciada: %s", sys.executable)
        except Exception as e:
            log.error("Erro ao reiniciar o programa: %s", e)

        if self._window:
            try:
                self._window.destroy()
            except Exception as e:
                log.warning("Erro ao fechar janela de configurações: %s", e)

        sw_about = self._state.get("sw_about_ref")
        if sw_about:
            try:
                sw_about.destroy()
            except Exception as e:
                log.warning("Erro ao fechar janela 'Sobre': %s", e)
            self._state["sw_about_ref"] = None

        jp = self._state.get("janela")
        if jp:
            try:
                jp.destroy()
            except Exception as e:
                log.warning("Erro ao fechar janela principal: %s", e)

    def fechar(self):
        if self._window:
            self._window.destroy()


class MainAPI:
    def __init__(self, state: dict, abrir_configuracoes_fn, abrir_sobre_fn):
        self._state       = state
        self._abrir_cfg   = abrir_configuracoes_fn
        self._abrir_sobre = abrir_sobre_fn

    def abrir_servidor(self, key: str) -> None:
        janela      = self._state.get("janela")
        cfg         = self._state.get("cfg")
        install_key = self._state.get("install_key", "")
        port        = cfg.get("app", {}).get("port", 8081)

        srv = cfg.get("servers", {}).get(key)
        if not srv or not janela:
            log.warning("abrir_servidor: servidor '%s' não encontrado.", key)
            return

        srv_com_key       = dict(srv)
        srv_com_key["_key"] = key
        url         = adminer_url_servidor(cfg, port, srv_com_key)
        senha_enc   = srv.get("password", "")
        senha_plain = descriptografar_senha(senha_enc, install_key) if senha_enc else ""
        autologin_js = build_autologin_js(senha_plain)

        log.info("Abrindo servidor '%s': %s", key, url)

        def _navegar():
            if autologin_js:
                _injetado = [False]

                def _on_loaded_autologin():
                    if _injetado[0]:
                        return
                    _injetado[0] = True
                    try:
                        janela.events.loaded -= _on_loaded_autologin
                    except Exception:
                        pass
                    try:
                        janela.evaluate_js(autologin_js)
                        log.debug("autologin_js injetado.")
                    except Exception as e:
                        log.debug("autologin_js erro: %s", e)

                janela.events.loaded += _on_loaded_autologin

            time.sleep(0.05)
            janela.load_url(url)

        threading.Thread(target=_navegar, daemon=True).start()

    def save_export(self, base64_data: str, content_disposition: str) -> None:
        def _save():
            janela = self._state.get("janela")
            try:
                file_bytes = base64.b64decode(base64_data)

                m             = re.search(r'filename[^;=\n]*=(["\']?)([^;\n"\']+)\1', content_disposition)
                original_name = m.group(2).strip() if m else "backup.sql"

                backups_dir = os.path.join(exe_dir(), "backups")
                os.makedirs(backups_dir, exist_ok=True)

                timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
                filename  = f"backup-{timestamp}-{original_name}"
                filepath  = os.path.join(backups_dir, filename)

                with open(filepath, "wb") as f:
                    f.write(file_bytes)

                log.info("Export salvo: %s", filepath)

                if janela:
                    safe_name   = filename.replace("\\", "\\\\").replace("'", "\\'")
                    safe_path   = filepath.replace("\\", "\\\\").replace("'", "\\'")
                    safe_folder = backups_dir.replace("\\", "\\\\").replace("'", "\\'")
                    janela.evaluate_js(
                        f"window._aptShowDownloadToast('{safe_name}', '{safe_path}', '{safe_folder}', null);"
                    )
            except Exception as exc:
                log.error("Erro ao salvar export: %s", exc)
                if janela:
                    err_msg = str(exc)[:120].replace("\\", "\\\\").replace("'", "\\'")
                    janela.evaluate_js(
                        f"window._aptShowDownloadToast(null, null, null, '{err_msg}');"
                    )

        threading.Thread(target=_save, daemon=True).start()

    def open_file(self, filepath: str) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(filepath)          # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", filepath])
            else:
                subprocess.Popen(["xdg-open", filepath])
            log.info("Arquivo aberto: %s", filepath)
        except Exception as exc:
            log.error("Erro ao abrir arquivo: %s", exc)

    def open_exports_folder(self) -> None:
        backups_dir = os.path.join(exe_dir(), "backups")
        os.makedirs(backups_dir, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(backups_dir)       # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", backups_dir])
            else:
                subprocess.Popen(["xdg-open", backups_dir])
            log.info("Pasta backups aberta: %s", backups_dir)
        except Exception as exc:
            log.error("Erro ao abrir pasta backups: %s", exc)

    def menu_action(self, action: str) -> None:
        janela = self._state.get("janela")
        cfg    = self._state.get("cfg")
        log.debug("menu_action: %s", action)

        if action.startswith("servidor:"):
            key = action[9:]
            self.abrir_servidor(key)
            return

        if action == "settings":
            threading.Thread(target=self._abrir_cfg,   daemon=True).start()
        elif action == "about":
            threading.Thread(target=self._abrir_sobre, daemon=True).start()
        elif action == "inicial":
            if janela and cfg:
                port  = cfg.get("app", {}).get("port", 8081)
                _url  = adminer_url(cfg, port)
                threading.Thread(
                    target=lambda: (time.sleep(0.05), janela.load_url(_url)),
                    daemon=True
                ).start()
        elif action == "minimize":
            if janela:
                janela.minimize()
        elif action == "maximize":
            if janela:
                janela.toggle_fullscreen()
        elif action in ("close", "quit"):
            for ref_key in ("sw_cfg_ref", "sw_about_ref"):
                sw = self._state.get(ref_key)
                if sw:
                    try:
                        sw.destroy()
                    except Exception:
                        pass
                    self._state[ref_key] = None
            if janela:
                janela.destroy()
        elif action == "back":
            janela.evaluate_js("if (history.length > 1) history.back();")
        elif action == "forward":
            janela.evaluate_js("history.forward();")
        elif action == "reload":
            janela.evaluate_js("location.reload();")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import webview

    cfg_pre = carregar_config()
    setup_logging(cfg_pre.get("app", {}).get("debug", False))

    log.info("Adminer Launcher v%s iniciando...", LAUNCHER_VERSION)

    cfg         = cfg_pre
    install_key = carregar_install_key()
    port        = cfg.get("app", {}).get("port",      8081)
    title       = cfg.get("app", {}).get("title",     "Adminer Launcher")
    width       = cfg.get("app", {}).get("width",     1280)
    height      = cfg.get("app", {}).get("height",    800)
    maximized   = cfg.get("app", {}).get("maximized", False)
    debug       = cfg.get("app", {}).get("debug",     False)
    lang_code   = cfg.get("app", {}).get("lang",      "en_US")

    lang            = carregar_lang(lang_code)
    available_langs = listar_langs()

    log.info("porta=%d | %dx%d | '%s' | debug=%s | lang=%s", port, width, height, title, debug, lang_code)

    aplicar_config_ao_adminer(cfg)

    state: dict = {
        "proc_php":     None,
        "janela":       None,
        "cfg":          cfg,
        "install_key":  install_key,
        "lang":         lang,
        "sw_cfg_ref":   None,
        "sw_about_ref": None,
    }

    proc_php = iniciar_php(port)
    state["proc_php"] = proc_php
    if not aguardar_servidor("localhost", port):
        log.error("PHP não respondeu na porta %d.", port)
        encerrar_php(proc_php)
        sys.exit(1)
    log.info("PHP pronto em http://localhost:%d", port)

    # ── Janela de configurações ───────────────────────────────────────────
    sw_cfg_ref = [None]

    def abrir_configuracoes():
        log.info("Abrindo configurações.")
        if sw_cfg_ref[0] is not None:
            try:
                sw_cfg_ref[0].show()
                return
            except Exception:
                sw_cfg_ref[0] = None

        # Recarrega lang caso o usuário tenha mudado no meio da sessão
        _lang_code = cfg.get("app", {}).get("lang", "en_US")
        _lang      = carregar_lang(_lang_code)
        _avail     = listar_langs()

        api = SettingsAPI(cfg, install_key, state)
        sw  = webview.create_window(
            title  = _lang.get("settings.title", "Settings — Adminer Launcher"),
            html   = build_settings_html(_lang, _lang_code, _avail),
            js_api = api,
            width=720, height=860, resizable=True, on_top=True,
        )
        api.set_window(sw)
        sw_cfg_ref[0]         = sw
        state["sw_cfg_ref"]   = sw
        sw.events.closed += lambda: (
            sw_cfg_ref.__setitem__(0, None),
            state.__setitem__("sw_cfg_ref", None),
        )

    # ── Janela Sobre ──────────────────────────────────────────────────────
    sw_about_ref = [None]

    def abrir_sobre():
        log.info("Abrindo Sobre.")
        if sw_about_ref[0] is not None:
            try:
                sw_about_ref[0].show()
                return
            except Exception:
                sw_about_ref[0] = None

        _lang_code = cfg.get("app", {}).get("lang", "en_US")
        _lang      = carregar_lang(_lang_code)

        api = SimpleCloseAPI()
        sw  = webview.create_window(
            title  = _lang.get("about.title", "About — Adminer Launcher"),
            html   = build_about_html(_lang),
            js_api = api,
            width=420, height=500, resizable=False, on_top=True,
        )
        api.set_window(sw)
        sw_about_ref[0]         = sw
        state["sw_about_ref"]   = sw
        sw.events.closed += lambda: (
            sw_about_ref.__setitem__(0, None),
            state.__setitem__("sw_about_ref", None),
        )

    # ── Janela principal ──────────────────────────────────────────────────
    main_api = MainAPI(state, abrir_configuracoes, abrir_sobre)
    url_ini  = adminer_url(cfg, port)
    log.info("URL inicial: %s", url_ini)

    try:
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = debug
    except Exception:
        pass

    janela = webview.create_window(
        title     = title,
        url       = url_ini,
        width     = width,
        height    = height,
        maximized = maximized,
        frameless = False,
        resizable = True,
        easy_drag = False,
        js_api    = main_api,
    )
    state["janela"] = janela

    def _on_loaded():
        servidores_json = servidores_para_js(cfg, install_key)
        _lang_code      = cfg.get("app", {}).get("lang", "en_US")
        _lang           = carregar_lang(_lang_code)
        janela.evaluate_js(build_menubar_js(servidores_json, _lang))
        log.debug("Menubar injetada com %d servidor(es).", len(cfg.get("servers", {})))
        janela.evaluate_js(build_download_interceptor_js(_lang))
        log.debug("Download interceptor injetado.")

    janela.events.loaded += _on_loaded
    janela.events.closed += lambda: (
        log.info("Janela fechada."),
        encerrar_php(state["proc_php"]),
    )

    log.info("Iniciando webview...")
    try:
        webview.start(debug=True, private_mode=True)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt recebido.")
    finally:
        encerrar_php(state["proc_php"])
        log.info("Encerrado.")


def aguardar_servidor(host: str, port: int, timeout: int = 15) -> bool:
    inicio = time.time()
    while time.time() - inicio < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    log.error("Timeout aguardando %s:%d.", host, port)
    return False


def iniciar_php(port: int) -> subprocess.Popen:
    php     = php_exe_path()
    docroot = exe_dir()
    if not os.path.exists(php):
        log.error("PHP não encontrado: %s", php)
        sys.exit(1)
    log.debug("Docroot '%s': %s", docroot, os.listdir(docroot))
    args   = [php, "-S", f"localhost:{port}"]
    kwargs = {"cwd": docroot}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    log.info("PHP | porta=%d | docroot=%s", port, docroot)
    proc = subprocess.Popen(args, **kwargs)
    log.debug("PHP PID=%d", proc.pid)
    return proc


def encerrar_php(proc) -> None:
    if proc and proc.poll() is None:
        log.info("Encerrando PHP PID=%d...", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    try:
        import webview  # noqa: F401
    except ImportError:
        print("[ERRO] pywebview não instalado. Execute: pip install pywebview")
        sys.exit(1)
    main()