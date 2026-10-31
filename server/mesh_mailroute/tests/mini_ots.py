"""A stand-in for OpenTAKServer 1.7.13's EUD side, on a REAL RabbitMQ broker.
Follows the OTS source (eud_handler/EudHandler.py, cot_parser/cot_parser.py, app.py):
  - exchanges declared like app.py (dms direct, groups topic, firehose fanout, ...)
  - connection identity = first CoT with <contact callsign> (parse_device_info); platforms
    "OpenTAK ICU" / "Meshtastic" / "DMRCOT" get no mailbox
  - mailboxes: queue_declare(callsign), queue_declare(uid); bind groups __ANON__.OUT -> uid (TCP),
    dms uid -> uid, dms callsign -> callsign; consume both queues
  - on_message: send body["cot"] to the socket unless body["uid"] == this connection's uid
  - publish_cot: every CoT -> firehose {"uid": connection uid, "cot"}; then route_cot:
      <dest callsign=X> -> dms / X;  <dest uid=Y> (Y != sender) -> dms / Y;  no dest -> groups __ANON__.OUT
    all with expiration OTS_RABBITMQ_TTL
  - disconnect: unbind its own bindings only (queues stay)
Plain TCP (like OTS's TCP port, no TLS/users)."""
import json, re, socket, threading
import pika

TTL = "86400000"
SKIP = ("OpenTAK ICU", "Meshtastic", "DMRCOT")


def setup_exchanges():
    c = pika.BlockingConnection(pika.ConnectionParameters("localhost")); ch = c.channel()
    ch.exchange_declare("dms", durable=True, exchange_type="direct")
    ch.exchange_declare("cot_parser", durable=True, exchange_type="direct")
    ch.exchange_declare("chatrooms", durable=True, exchange_type="direct")
    ch.exchange_declare("missions", durable=True, exchange_type="topic")
    ch.exchange_declare("groups", durable=True, exchange_type="topic")
    ch.exchange_declare("firehose", durable=True, exchange_type="fanout")
    c.close()


class Eud:
    def __init__(self, server, sock, addr):
        self.server, self.sock, self.addr = server, sock, addr
        self.uid = self.callsign = None
        self.bound = []
        self.pub = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
        self.pch = self.pub.channel()
        self.consumer = None
        self.stop = threading.Event()
        self.send_lock = threading.Lock()

    def handle(self):
        buf = b""
        try:
            while True:
                d = self.sock.recv(65536)
                if not d:
                    break
                buf += d
                while b"</event>" in buf:
                    i = buf.index(b"</event>") + 8
                    ev, buf = buf[:i], buf[i:]
                    j = ev.find(b"<event")
                    if j >= 0:
                        self.event(ev[j:].decode("utf-8", "replace"))
        except OSError:
            pass
        finally:
            self.close()

    def event(self, cot):
        if self.uid is None and "<contact" in cot and 'callsign="' in cot:
            self.identify(cot)
        self.pch.basic_publish(exchange="firehose", routing_key="", body=json.dumps({"uid": self.uid, "cot": cot}),
                               properties=pika.BasicProperties(expiration=TTL))
        self.route(cot)

    def identify(self, cot):
        self.uid = re.search(r'<event\b[^>]*?\suid="([^"]+)"', cot).group(1)
        self.callsign = re.search(r'<contact\b[^>]*?\scallsign="([^"]+)"', cot).group(1)
        m = re.search(r'platform="([^"]*)"', cot)
        if m and m.group(1) in SKIP:
            return
        ch = self.pch
        ch.queue_declare(queue=self.callsign); ch.queue_declare(queue=self.uid)
        for ex, q, rk in (("groups", self.uid, "__ANON__.OUT"), ("dms", self.uid, self.uid), ("dms", self.callsign, self.callsign)):
            ch.queue_bind(exchange=ex, queue=q, routing_key=rk); self.bound.append((ex, q, rk))
        self.consumer = threading.Thread(target=self.consume, daemon=True); self.consumer.start()
        self.server.log.append(("identify", self.callsign, self.uid))

    def consume(self):
        c = pika.BlockingConnection(pika.ConnectionParameters("localhost")); ch = c.channel()
        def on_message(_c, _m, _p, body):
            b = json.loads(body)
            if b["uid"] != self.uid:
                with self.send_lock:
                    try: self.sock.sendall(b["cot"].encode())
                    except OSError: pass
        ch.basic_consume(queue=self.callsign, on_message_callback=on_message, auto_ack=True)
        ch.basic_consume(queue=self.uid, on_message_callback=on_message, auto_ack=True)
        while not self.stop.is_set():
            c.process_data_events(time_limit=0.1)
        c.close()

    def route(self, cot):
        props = pika.BasicProperties(expiration=TTL)
        body = json.dumps({"uid": self.uid, "cot": cot})
        dest_cs = re.findall(r'<dest\b[^>]*?\scallsign="([^"]+)"', cot)
        dest_uid = re.findall(r'<dest\b[^>]*?\suid="([^"]+)"', cot)
        if dest_cs or dest_uid:
            for cs in dest_cs:
                self.pch.basic_publish(exchange="dms", routing_key=cs, body=body, properties=props)
            for u in dest_uid:
                if u != self.uid:
                    self.pch.basic_publish(exchange="dms", routing_key=u, body=body, properties=props)
            return
        self.pch.basic_publish(exchange="groups", routing_key="__ANON__.OUT", body=body, properties=props)

    def close(self):
        self.stop.set()
        if self.consumer:
            self.consumer.join(2)
        try:
            for ex, q, rk in self.bound:
                self.pch.queue_unbind(exchange=ex, queue=q, routing_key=rk)
            self.pub.close()
        except Exception:
            pass
        try: self.sock.close()
        except OSError: pass
        self.server.log.append(("disconnect", self.callsign, self.uid))


class MiniOTS:
    def __init__(self, port=0):
        setup_exchanges()
        self.log = []
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port)); self.srv.listen(20); self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try: s, a = self.srv.accept()
            except OSError: return
            e = Eud(self, s, a)
            threading.Thread(target=e.handle, daemon=True).start()
