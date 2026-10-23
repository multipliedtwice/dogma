"""Home routing tests use fake upstreams and a synthetic template only."""
import base64
from email.message import Message
import functools
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import local_home as home
import serve_label_pilot as labels
import serve_listening_proxy as gateway
from test_higgs_listener_feedback import Upstream


class LocalHomeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        template=self.root/'home.html'
        template.write_text('<!doctype html><title>Dogma</title><script id="config" type="application/json">__DOGMA_HOME_CONFIG__</script>')
        patcher=patch.object(home,'TEMPLATE',template); patcher.start(); self.addCleanup(patcher.stop)
        self.upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream); self.upstream.received=[]
        self.proxy=ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler)
        self.proxy.runtime_port=self.proxy.upstream_port=self.upstream.server_port
        self.proxy.runtime_token='synthetic-token'; self.proxy.listener_book_id=''; self.proxy.listener_generation_id=''
        self.authorization='Basic '+base64.b64encode(b'synthetic:fixture').decode()
        self.proxy.authorization=self.authorization.encode()
        self.label=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(labels.Handler,directory=str(self.root)))
        for server in (self.upstream,self.proxy,self.label):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close); self.addCleanup(server.shutdown)

    def request(self,server,method='GET',path='/',auth=True,host=None,body=None,**headers):
        if auth: headers['Authorization']=self.authorization
        headers['Host']=host or f'127.0.0.1:{server.server_port}'
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        connection.request(method,path,body,headers)
        response=connection.getresponse(); value=(response.status,dict(response.getheaders()),response.read())
        connection.close(); return value

    @staticmethod
    def config(raw):
        return json.loads(raw.decode().split('type="application/json">',1)[1].split('</script>',1)[0])

    def test_gateway_root_requires_auth_and_head_matches_get(self):
        self.assertEqual(401,self.request(self.proxy,auth=False)[0])
        status,headers,body=self.request(self.proxy,path='/?ignored=%3Cscript%3E')
        self.assertEqual(200,status)
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertEqual([],self.upstream.received)
        config=self.config(body)
        self.assertEqual('gateway',config['role']); self.assertTrue(config['local'])
        self.assertEqual(3,len(config['labeling']))
        self.assertEqual(f'http://127.0.0.1:{self.proxy.server_port}',config['listeningOrigin'])
        status,head_headers,empty=self.request(self.proxy,method='HEAD')
        self.assertEqual((200,b''),(status,empty))
        self.assertEqual(str(len(body)),head_headers['Content-Length'])

    def test_public_tunnel_host_omits_labeling_even_with_forwarded_lan_address(self):
        status,_,raw=self.request(self.proxy,host='books.example',**{'X-Forwarded-For':'192.168.1.2','X-Forwarded-Proto':'https'})
        self.assertEqual(200,status)
        config=self.config(raw)
        self.assertFalse(config['local']); self.assertEqual([],config['labeling'])
        self.assertEqual('https://books.example',config['listeningOrigin'])
        self.assertNotIn(b'character-rb1',raw); self.assertNotIn(b':1439',raw)

    def test_labels_root_and_head_share_safe_shell_without_directory_listing(self):
        status,headers,raw=self.request(self.label,auth=False)
        self.assertEqual(200,status)
        config=self.config(raw)
        self.assertEqual('labels',config['role'])
        self.assertEqual('http://127.0.0.1:1442',config['listeningOrigin'])
        status,head_headers,empty=self.request(self.label,method='HEAD',auth=False)
        self.assertEqual((200,b''),(status,empty)); self.assertEqual(headers['Content-Length'],head_headers['Content-Length'])
        status,_,raw=self.request(self.label,host='public.example',auth=False)
        self.assertEqual(200,status); config=self.config(raw)
        self.assertEqual([],config['labeling']); self.assertIsNone(config['listeningOrigin'])

    def test_host_injection_is_rejected_and_script_json_is_escaped(self):
        for host in ('evil.example/<script>', 'user@evil.example', 'localhost:0', 'localhost:notaport', 'evil.example\\bad', 'evil.example%3cscript%3e'):
            with self.subTest(host=host):
                status,_,raw=self.request(self.proxy,host=host)
                self.assertEqual(400,status)
        hostile='</script><script>alert(1)</script>&\u2028\u2029'
        escaped=home.safe_json({'value':hostile})
        self.assertNotIn('<',escaped); self.assertNotIn('&',escaped)
        self.assertEqual(hostile,json.loads(escaped)['value'])

    def test_direct_lan_and_ipv6_local_urls_and_loopback_host_guard(self):
        for client,host,local,origin in [('192.168.1.8','192.168.1.3:1440',True,'http://192.168.1.3:1442'),
                                      ('::1','[::1]:1440',True,'http://[::1]:1442'),
                                      ('127.0.0.1','localhost:1440',True,'http://localhost:1442'),
                                      ('127.0.0.1','192.168.1.3:1440',False,None),
                                      ('8.8.8.8','localhost:1440',False,None)]:
            headers=Message(); headers['Host']=host
            config=home.home_config(SimpleNamespace(headers=headers,client_address=(client,1000)),'labels')
            self.assertEqual(local,config['local']); self.assertEqual(origin,config['listeningOrigin'])

    def test_library_api_and_static_asset_routing_are_preserved(self):
        origin=f'http://127.0.0.1:{self.proxy.server_port}'
        status,_,body=self.request(self.proxy,method='POST',path=gateway.PRODUCTION_PAGE+'api',body=b'{"op":"library","data":{}}',
                                   **{'Origin':origin,'Content-Type':'application/json'})
        self.assertEqual(200,status); self.assertIsInstance(json.loads(body),list)
        self.assertEqual(('POST','/api'),self.upstream.received[-1][:2])
        self.assertEqual('Bearer synthetic-token',self.upstream.received[-1][3]['Authorization'])
        self.assertEqual(b'production asset',self.request(self.proxy,path=gateway.PRODUCTION_PAGE+'assets/app.js')[2])


if __name__=='__main__': unittest.main()
