"""Shared navigation shell; private labeling links are limited to local clients."""
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

TEMPLATE = Path(__file__).with_name('local_home.html')
API_PREFIX = '/character-rb2-full/fine-tune-all/elirm-iv-mvp/ch123/'
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


def safe_json(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def request_address(handler):
    hosts = handler.headers.get_all('Host', [])
    if len(hosts) != 1:
        raise ValueError('A single valid Host header is required.')
    raw = hosts[0]
    if not raw or len(raw)>260 or any(c.isspace() or c in '/\\@?#%<>"\'' for c in raw):
        raise ValueError('Invalid Host header.')
    address = urlsplit('http://' + raw)
    hostname = address.hostname
    port = address.port
    if not hostname or address.username or address.password or (port is not None and not 1<=port<=65535):
        raise ValueError('Invalid Host header.')
    try:
        host_ip = ipaddress.ip_address(hostname)
    except ValueError:
        host_ip = None
        if len(hostname)>253 or not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                                       for label in hostname.rstrip('.').split('.')):
            raise ValueError('Invalid Host hostname.')
    client = ipaddress.ip_address(handler.client_address[0])
    if isinstance(client, ipaddress.IPv6Address) and client.ipv4_mapped:
        client = client.ipv4_mapped
    loopback_host = hostname.casefold()=='localhost' or host_ip is not None and host_ip.is_loopback
    local = any(client in network for network in PRIVATE_NETWORKS) or client.is_loopback and loopback_host
    formatted = '[' + hostname + ']' if ':' in hostname else hostname
    scheme = 'https' if client.is_loopback and handler.headers.get('X-Forwarded-Proto')=='https' else 'http'
    origin = scheme + '://' + formatted + (':' + str(port) if port is not None else '')
    return local, formatted, origin


def home_config(handler, role):
    if role not in ('gateway', 'labels'):
        raise ValueError('Invalid home role.')
    local, hostname, origin = request_address(handler)
    labeling = []
    if local:
        labeling = [
            {'id': 'book1', 'title': 'Book 1', 'url': f'http://{hostname}:1439/character-rb1/fine-tune-all/buckets.html'},
            {'id': 'book2', 'title': 'Book 2', 'url': f'http://{hostname}:1439/character-rb2-full/fine-tune-all/'},
            {'id': 'book3', 'title': 'Book 3', 'url': f'http://{hostname}:1440/character-rb3-full/fine-tune-all/'},
        ]
    return {'schema': 1, 'role': role, 'local': bool(local), 'apiPrefix': API_PREFIX,
            'listeningOrigin': origin if role=='gateway' else f'http://{hostname}:1442' if local else None,
            'labeling': labeling}


def serve_home(handler, *, role, head_only=False):
    if urlsplit(handler.path).path != '/':
        return False
    try:
        config = home_config(handler, role)
        template = TEMPLATE.read_text('utf-8')
        if template.count('__DOGMA_HOME_CONFIG__') != 1:
            raise OSError('Home template is unavailable.')
        raw = template.replace('__DOGMA_HOME_CONFIG__', safe_json(config)).encode('utf-8')
    except ValueError as error:
        handler.send_error(400, str(error)); return True
    except OSError:
        handler.send_error(503, 'Home page is unavailable.'); return True
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'private, no-store')
    handler.send_header('Vary', 'Host')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    if not head_only:
        handler.wfile.write(raw)
    return True
