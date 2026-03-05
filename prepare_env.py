import os
import shutil
import urllib.request
import zipfile

PHP_URL = 'https://downloads.php.net/~windows/releases/archives/php-8.5.3-Win32-vs17-x64.zip'
ADMINER_URL = 'https://github.com/vrana/adminer/releases/download/v5.4.2/adminer-5.4.2.php'
ADMINER_CSS_URL = 'https://raw.githubusercontent.com/guisaldanha/adminer-obsidian-amber/main/adminer.css'
PLUGINS = [
    ('https://raw.githubusercontent.com/dg/adminer/refs/heads/master/adminer-plugins/login-without-credentials.php', 'login-without-credentials.php'),
    ('https://raw.githubusercontent.com/guisaldanha/sql-ollama/refs/heads/main/sql-ollama.php', 'sql-ollama.php'),
    ('https://www.adminer.org/download/v5.4.2/plugins/row-numbers.php', 'row-numbers.php'),
    ('https://www.adminer.org/download/v5.4.2/plugins/edit-foreign.php', 'edit-foreign.php'),
]


def download_file(url, dest):
    print(f"Baixando: {url}")
    urllib.request.urlretrieve(url, dest)


def extract_zip(zip_path, dest_path):
    print(f"Extraindo: {zip_path} para {dest_path}")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(dest_path)


def setup_php(base_dir):
    php_dir = os.path.join(base_dir, 'php')
    os.makedirs(php_dir, exist_ok=True)
    zip_path = os.path.join(base_dir, 'php.zip')
    download_file(PHP_URL, zip_path)
    extract_zip(zip_path, php_dir)
    os.remove(zip_path)
    ini_prod = os.path.join(php_dir, 'php.ini-production')
    ini_file = os.path.join(php_dir, 'php.ini')
    if os.path.exists(ini_prod):
        shutil.copyfile(ini_prod, ini_file)
        with open(ini_file, 'r', encoding='utf-8') as f:
            content = f.read()
        content = content.replace('upload_max_filesize = 2M', 'upload_max_filesize = 100M')
        content = content.replace('memory_limit = 128M', 'memory_limit = 512M')
        content = content.replace('post_max_size = 8M', 'post_max_size = 100M')
        for ext in ['mysqli', 'openssl', 'pdo_pgsql', 'pdo_sqlite', 'pgsql', 'sqlite3']:
            content = content.replace(f';extension={ext}', f'extension={ext}')
        content = content.replace(';extension_dir = "ext"', 'extension_dir = "ext"')
        with open(ini_file, 'w', encoding='utf-8') as f:
            f.write(content)
    print('PHP configurado.')


def setup_adminer(base_dir):
    plugins_dir = os.path.join(base_dir, 'adminer-plugins')
    os.makedirs(plugins_dir, exist_ok=True)
    download_file(ADMINER_URL, os.path.join(base_dir, 'adminer-5.4.2.php'))
    download_file(ADMINER_CSS_URL, os.path.join(base_dir, 'adminer.css'))
    for url, fname in PLUGINS:
        download_file(url, os.path.join(plugins_dir, fname))
    print('Adminer e plugins baixados.')


def generate_install_key(base_dir):
    import random
    import string
    key_path = os.path.join(base_dir, 'install.key')
    if not os.path.exists(key_path):
        chars = '0123456789ABCDEF'
        S = ''.join(random.choice(chars) for _ in range(32))
        guid = f'{S[:8]}-{S[8:12]}-{S[12:16]}-{S[16:20]}-{S[20:]}'
        with open(key_path, 'w') as f:
            f.write(guid)
        print(f'Chave de instalação criada: {guid}')
    else:
        print('Chave de instalação já existe.')


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    generate_install_key(base_dir)
    setup_php(base_dir)
    setup_adminer(base_dir)
    print('Ambiente preparado! Agora rode: py main.py')


if __name__ == '__main__':
    main()
