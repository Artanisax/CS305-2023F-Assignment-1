from __future__ import annotations

from argparse import ArgumentParser
# from email.mime.text import MIMEText
from queue import Queue
import socket
from socketserver import BaseServer, ThreadingTCPServer, BaseRequestHandler
from threading import Thread

import tomli


def student_id() -> int:
    return 12110524  # TODO: replace with your SID


parser = ArgumentParser()
parser.add_argument('--name', '-n', type=str, required=True)
parser.add_argument('--smtp', '-s', type=int)
parser.add_argument('--pop', '-p', type=int)

args = parser.parse_args()

with open('data/config.toml', 'rb') as f:
    _config = tomli.load(f)
    SMTP_PORT = args.smtp or int(_config['server'][args.name]['smtp'])
    POP_PORT = args.pop or int(_config['server'][args.name]['pop'])
    ACCOUNTS = _config['accounts'][args.name]
    MAILBOXES = {account: [] for account in ACCOUNTS.keys()}

with open('data/fdns.toml', 'rb') as f:
    FDNS = tomli.load(f)

ThreadingTCPServer.allow_reuse_address = True


def fdns_query(domain: str, type_: str) -> str | None:
    domain = domain.rstrip('.') + '.'
    return FDNS[type_][domain]


class POP3Server(BaseRequestHandler):
    def __init__(self, request, client_address, server):
        self.username = None
        self.login = False
        self.mailbox = None
        self.pre_del = []
        self.handle_op = {
            'USER': self.handle_USER,
            'PASS': self.handle_PASS,
            'STAT': self.handle_STAT,
            'LIST': self.handle_LIST,
            'RETR': self.handle_RETR,
            'DELE': self.handle_DELE,
            'RSET': self.handle_RSET,
            'NOOP': self.handle_NOOP,
            'QUIT': self.handle_QUIT,
        }
        super(POP3Server, self).__init__(request, client_address, server)
    
    def handle(self):
        conn = self.request
        # TODO
        try:
            self.send("+OK POP3 server ready")
            while True:
                data = conn.recv(1024).decode("utf-8").strip().split()
                
                if len(data) == 0:
                    continue
                
                print(data)
                cmd = data[0].upper() if len(data) > 0 else None
                args = data[1:] if len(data) > 1 else []
                
                if cmd not in self.handle_op:
                    self.send("-ERR invalid command")
                    continue
                    
                if self.login:
                    self.handle_op[cmd](args)
                else:
                    if not self.username:
                        if cmd == "USER":
                            self.handle_op["USER"](args)
                        else:
                            self.send("-ERR need username for login")
                    else:
                        if cmd == "PASS":
                            self.handle_op["PASS"](args)
                        else:
                            self.send("-ERR need password for login")
        except Exception as e:
            self.send("-ERR an unknown error occurred")
        finally:
            conn.close()
            
        
    def send(self, msg):
        self.request.send(f"{msg}\r\n".encode("utf-8"))
    
    def handle_USER(self, args):
        if len(args) != 1:
            self.send("-ERR invalid username")
            return
        
        if self.login:
            self.send("-ERR have logged in")
            return
        
        username = args[0]
        if username in ACCOUNTS:
            self.username = username
            self.send("+OK username confirmed")
        else:
            self.send("-ERR invalid username")
    
    def handle_PASS(self, args):
        if len(args) != 1:
            self.send("-ERR invalid password")
            return
        
        if self.login:
            self.send("-ERR have logged in")
            return
        
        password = args[0]
        if password == ACCOUNTS[self.username]:
            self.login = True
            self.mailbox = MAILBOXES[self.username]
            self.send("+OK successfully logged in")
        else:
            self.send("-ERR wrong password")
    
    def handle_STAT(self, args):
        if len(args) > 0:
            self.send("-ERR invalid arguments")
            return
        
        total = len(self.mailbox)
        tot_size = sum(len(mail) for mail in self.mailbox)
        self.send(f"+OK {total} {tot_size}")
    
    def handle_LIST(self, args):
        if len(args) > 1:
            self.send("-ERR invalid arguments")
            return

        if args:
            idx = int(args[0]) - 1
            if idx in self.pre_del or idx < 0 or idx >= len(self.mailbox):
                self.send(f"-ERR inexsistent email")
            else:
                size = len(self.mailbox[idx])
                self.send(f"+OK {size}")
        else:
            total = len(self.mailbox) - len(self.pre_del)
            tot_size = 0
            for i, mail in enumerate(self.mailbox):
                if i not in self.pre_del:
                    tot_size = tot_size + len(mail)
            self.send(f"+OK {total} messages ({tot_size} bytes)")
            for i, mail in enumerate(self.mailbox, 1):
                if i not in self.pre_del:
                    self.send(f"{i} {len(mail)}")
            self.send(".")

    def handle_RETR(self, args):
        if len(args) != 1:
            self.send("-ERR invalid arguments")
            return
        
        idx = int(args[0]) - 1
        if idx in self.pre_del or idx < 0 or idx >= len(self.mailbox):
            self.send(f"-ERR inexsistent email")
        else:
            size = len(self.mailbox[idx])
            self.send(f"+OK {size} bytes")
            self.send(f"<{self.mailbox[idx]}>")
            self.send(".")
            

    def handle_DELE(self, args):
        if len(args) != 1:
            self.send("-ERR invalid arguments")
            return
        
        idx = int(args[0]) - 1
        if idx in self.pre_del or idx < 0 or idx >= len(self.mailbox):
            self.send(f"-ERR inexsistent email")
        else:
            self.pre_del.append(idx)
            self.send("+OK")
    
    def handle_RSET(self, args):
        if len(args) > 0:
            self.send("-ERR invalid arguments")
            return
        
        self.pre_del.clear()
        self.send("+OK")
    
    def handle_NOOP(self, args):
        if len(args) > 0:
            self.send("-ERR invalid arguments")
            return
        
        self.send("+OK")
    
    def handle_QUIT(self, args):
        for i in self.pre_del:
            del self.mailbox[i]
        self.username = None
        self.login = False
        self.mailbox = None
        self.pre_del = []
        self.send("+OK POP3 server signing off")
        self.request.close()
    

class SMTPServer(BaseRequestHandler):
    def handle(self):
        conn = self.request
        # TODO


if __name__ == '__main__':
    try:
        if student_id() % 10000 == 0:
            raise ValueError('Invalid student ID')

        smtp_server = ThreadingTCPServer(('', SMTP_PORT), SMTPServer)
        pop_server = ThreadingTCPServer(('', POP_PORT), POP3Server)
        Thread(target=smtp_server.serve_forever).start()
        Thread(target=pop_server.serve_forever).start()
    
    except KeyboardInterrupt:
        smtp_server.shutdown()
        pop_server.shutdown()
        smtp_server.server_close()
        pop_server.server_close()
