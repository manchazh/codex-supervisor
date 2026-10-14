"""Cross-platform fake WebSocket-over-stdio probe tests. No Codex or network."""

import sys
import unittest

from quota_probe.rpc import ProbeError, _request, read_quota_once
from quota_probe.summary import summary_lines


FAKE_WS = r"""
import base64, hashlib, json, os, struct, sys, time
inp=sys.stdin.buffer; out=sys.stdout.buffer
def exact(n):
    result=bytearray()
    while len(result)<n:
        chunk=inp.read(n-len(result))
        if not chunk:
            raise EOFError
        result.extend(chunk)
    return bytes(result)
buf=bytearray()
while not buf.endswith(b"\r\n\r\n"):
    buf.extend(exact(1))
key=None
for line in buf.decode("ascii").split("\r\n"):
    if line.lower().startswith("sec-websocket-key:"):
        key=line.split(":",1)[1].strip()
if not key:
    sys.exit(1)
accept=base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
out.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\nSec-WebSocket-Accept: "+accept+"\r\n\r\n").encode())
out.flush()
seen=[]
def read_frame():
    a,b=exact(2)
    n=b & 0x7f
    if n==126: n=struct.unpack("!H",exact(2))[0]
    if n==127: n=struct.unpack("!Q",exact(8))[0]
    assert b & 0x80, "client MUST mask"
    mask=exact(4)
    raw=exact(n)
    return json.loads(bytes(x ^ mask[i%4] for i,x in enumerate(raw)))
def send(obj):
    payload=json.dumps(obj).encode("utf8")
    head=b"\x81"+(bytes([len(payload)]) if len(payload)<126 else b"\x7e"+struct.pack("!H",len(payload)))
    out.write(head+payload);out.flush()
while True:
    obj=read_frame()
    method=obj["method"]
    seen.append(method)
    if method=="initialize":
        send({"id":obj["id"],"result":{"userAgent":"fake"}})
    elif method=="initialized":
        send({"method":"account/updated","params":{"email":"SECRET@EXAMPLE.COM"}})
    elif method=="account/rateLimits/read":
        send({"id":obj["id"],"result":{
          "seen":seen,
          "rateLimits":{
            "primary":{"usedPercent":63,"windowDurationMins":300,"resetsAt":1780000000},
            "secondary":{"usedPercent":34,"windowDurationMins":10080,"resetsAt":1780100000}
          },
          "accountEmail":"SECRET@EXAMPLE.COM",
          "threadId":"SECRET-SESSION-UUID"
        }})
        break
"""


def fake_command(script=FAKE_WS):
    return [sys.executable, "-u", "-c", script]


class RpcTests(unittest.TestCase):
    def test_single_read_only_request_after_real_websocket_handshake(self):
        result=read_quota_once(command=fake_command(),timeout=5)
        self.assertEqual(result["seen"], [
            "initialize", "initialized", "account/rateLimits/read",
        ])
        output="\n".join(summary_lines(result))
        self.assertIn("37%",output)
        self.assertIn("66%",output)
        self.assertNotIn("SECRET",output)
        self.assertNotIn("EXAMPLE",output)

    def test_non_whitelisted_methods_rejected(self):
        for method in ("turn/start","thread/resume","account/login/start",
                       "account/rateLimitResetCredit/consume","thread/interrupt","account/logout"):
            with self.subTest(method=method):
                with self.assertRaises(ProbeError):
                    _request(method)

    def test_child_disconnect_becomes_error(self):
        with self.assertRaises(ProbeError):
            read_quota_once(command=fake_command("import sys; sys.exit(0)"),timeout=2)

    def test_invalid_websocket_upgrade_is_reported_without_raw_data(self):
        broken = r"""
import sys
stream=sys.stdin.buffer
while not (x:=stream.readline()).endswith(b"\r\n"):
    pass
sys.stdout.buffer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
sys.stdout.buffer.flush()
"""
        with self.assertRaises(ProbeError) as context:
            read_quota_once(command=fake_command(broken), timeout=2)
        self.assertIn("WebSocket upgrade",str(context.exception))

    def test_server_rpc_error_redacted(self):
        # Valid WebSocket handshake and an error payload with sensitive text.
        broken=FAKE_WS.replace(
            'send({"id":obj["id"],"result":{"userAgent":"fake"}})',
            'send({"id":obj["id"],"error":{"message":"SECRET internal credential"}});break'
        )
        with self.assertRaises(ProbeError) as context:
            read_quota_once(command=fake_command(broken),timeout=3)
        self.assertNotIn("SECRET",str(context.exception))

    def test_invalid_timeout(self):
        for value in (0,-10,1000,"foo",None,False,float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                read_quota_once(command=fake_command(),timeout=value)


class SummaryTests(unittest.TestCase):
    def test_missing_rate_limits_does_not_dump_response(self):
        lines=summary_lines({"account":{"email":"private@example.com"},"raw_token":"SECRET"})
        output=" ".join(lines)
        self.assertNotIn("private@example.com",output)
        self.assertNotIn("SECRET",output)

    def test_bad_percent_and_timestamp_not_printed(self):
        lines=summary_lines({"rateLimits":{
            "primary":{"usedPercent":float("nan"),"resetsAt":"UNTRUSTED SECRET"},
            "secondary":{"usedPercent":900,"resetsAt":10**40}
        }})
        output=" ".join(lines)
        self.assertIn("неизвестно",output)
        self.assertNotIn("UNTRUSTED SECRET",output)
        self.assertNotIn("900%",output)

    def test_null_window(self):
        lines=summary_lines({"rateLimits":{"primary":None,"secondary":None}})
        self.assertEqual(len(lines),3)
        self.assertIn("нет данных",lines[0])


if __name__=="__main__":
    unittest.main()
