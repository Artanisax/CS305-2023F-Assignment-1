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
            'USER': self._USER,
            'PASS': self._PASS,
            'STAT': self._STAT,
            'LIST': self._LIST,
            'RETR': self._RETR,
            'DELE': self._DELE,
            'RSET': self._RSET,
            'NOOP': self._NOOP,
            'QUIT': self._QUIT,
        }
        super(POP3Server, self).__init__(request, client_address, server)
    
    
    def handle(self):
        conn = self.request
        try:
            cmd = ''
            args = []
            self.send('+OK POP3 server ready')
            while cmd != 'QUIT' or len(args) != 0:
                data = conn.recv(1024).decode().strip().split()
                cmd = data[0].upper() if len(data) > 0 else None
                args = data[1:] if len(data) > 1 else []
                
                if cmd not in self.handle_op:
                    self.send('-ERR invalid command')
                    continue
                    
                if self.login:
                    self.handle_op[cmd](args)
                else:
                    if not self.username:
                        if cmd == 'USER':
                            self.handle_op['USER'](args)
                        else:
                            self.send('-ERR need username for login')
                    else:
                        if cmd == 'PASS':
                            self.handle_op['PASS'](args)
                        else:
                            self.send('-ERR need password for login')
        except Exception as e:
            self.send('-ERR unknown error')
        finally:
            conn.close()
            
        
    def send(self, msg):
        self.request.sendall(f'{msg}\r\n'.encode())
    
    
    def _USER(self, args):
        if len(args) != 1:
            self.send('-ERR invalid username')
            return
        
        if self.login:
            self.send('-ERR have logged in')
            return
        
        username = args[0]
        if username in ACCOUNTS:
            self.username = username
            self.send('+OK username confirmed')
        else:
            self.send('-ERR invalid username')
    
    
    def _PASS(self, args):
        if len(args) != 1:
            self.send('-ERR invalid password')
            return
        
        if self.login:
            self.send('-ERR have logged in')
            return
        
        password = args[0]
        if password == ACCOUNTS[self.username]:
            self.login = True
            self.mailbox = MAILBOXES[self.username]
            self.send('+OK successfully logged in')
        else:
            self.send('-ERR wrong password')
    
    
    def _STAT(self, args):
        if len(args) > 0:
            self.send('-ERR invalid arguments')
            return
        
        total = len(self.mailbox)
        tot_size = sum(len(mail) for mail in self.mailbox)
        self.send(f'+OK {total} {tot_size}')
    
    
    def _LIST(self, args):
        if len(args) > 1:
            self.send('-ERR invalid arguments')
            return

        if args:
            idx = int(args[0]) - 1
            if idx in self.pre_del or idx < 0 or idx >= len(self.mailbox):
                self.send(f'-ERR inexsistent email')
            else:
                size = len(self.mailbox[idx])
                self.send(f'+OK {size}')
        else:
            total = len(self.mailbox) - len(self.pre_del)
            tot_size = 0
            for i, mail in enumerate(self.mailbox):
                if i not in self.pre_del:
                    tot_size = tot_size + len(mail)
            self.send(f'+OK {total} messages ({tot_size} bytes)')
            for i, mail in enumerate(self.mailbox, 1):
                if i not in self.pre_del:
                    self.send(f'{i} {len(mail)}')
            self.send('.')


    def _RETR(self, args):
        if len(args) != 1:
            self.send('-ERR invalid arguments')
            return
        
        idx = int(args[0]) - 1
        if idx in self.pre_del or idx < 0 or idx >= len(self.mailbox):
            self.send(f'-ERR inexsistent email')
        else:
            size = len(self.mailbox[idx])
            self.send(f'+OK {size} bytes')
            self.send(f'<{self.mailbox[idx]}>')
            self.send('.')
            

    def _DELE(self, args):
        if len(args) != 1:
            self.send('-ERR invalid arguments')
            return
        
        idx = int(args[0]) - 1
        if idx in self.pre_del or idx < 0 or idx >= len(self.mailbox):
            self.send(f'-ERR inexsistent email')
        else:
            self.pre_del.append(idx)
            self.send('+OK')
    
    
    def _RSET(self, args):
        if len(args) > 0:
            self.send('-ERR invalid arguments')
            return
        
        self.pre_del = []
        self.send('+OK')
    
    
    def _NOOP(self, args):
        if len(args) > 0:
            self.send('-ERR invalid arguments')
            return
        
        self.send('+OK')
    
    
    def _QUIT(self, args):
        for i in self.pre_del:
            del self.mailbox[i]
        self.username = None
        self.login = False
        self.mailbox = None
        self.pre_del = []
        self.send('+OK POP3 server signing off')
        self.request.close()
    

class SMTPServer(BaseRequestHandler):
    def __init__(self, request, client_address, server):
        self.debug = None
        self.domain = args.name
        self.authorization = False
        self.mail_from = None
        self.rcpt_to = []
        self.data_content = None
        self.handle_op = {
            'HELO': self._HELO,
            'EHLO': self._HELO,
            'MAIL': self._MAIL,
            'RCPT': self._RCPT,
            'DATA': self._DATA,
            'QUIT': self._QUIT,
        }
        super(SMTPServer, self).__init__(request, client_address, server)
        
        
    def handle(self):
        conn = self.request
        try:
            cmd = ''
            args = []
            self.send(220, 'SMTP server ready')
            while cmd != 'QUIT' or args != '':
                data = conn.recv(1024).decode().strip().split()
                self.debug = data
                cmd = data[0].upper()
                args = data[1:] if len(data) > 1 else []
                if cmd in self.handle_op:
                    self.handle_op[cmd](args)
                else:
                    self.send(500, 'Invalid command')

        except Exception as e:
            self.send(-1, 'An unknown error occurred.')
        finally:
            conn.close()
    
    
    def send(self, code, msg):
        print(self.debug)
        self.request.sendall(f'{code} {msg}\r\n'.encode())
        
    
    def _HELO(self, args):
        if len(args) != 1:
            self.send(501, 'Invalid arguments')

        self.authorization = True
        self.send(250, f'Hello, {args}')
    
    
    def _MAIL(self, args):
        if len(args) != 1:
            self.send(501, 'Invalid arguments')
        if not self.authorization or self.mail_from:
            self.send(503, 'Bad sequence')
        
        self.mail_from = args[0][6: -1] 
        self.send(250, 'Ok')
    
    
    def _RCPT(self, args):
        if len(args) != 1:
            self.send(501, 'Invalid arguments')
        if not self.mail_from:
            self.send(503, 'Bad sequence')

        self.rcpt_to.append(args[0][4: -1]) 
        self.send(250, 'Ok')
        
    
    def _DATA(self, args):
        if len(args) > 0:
            self.send(501, 'Invalid arguments')
        if len(self.rcpt_to) == 0:
            self.send(503, 'Bad sequence')
        
        self.send(354, 'End data with <CR><LF>.<CR><LF>')
        content = ''
        while content.endswith('\r\n.\r\n'):
            content = content + self.request.recv(1024)
        self.send_email()
        self.send(250, 'Ok')
        
    
    def _QUIT(self, args):
        if len(args) > 0:
            self.send(501, 'Invalid arguments')
        
        self.authorization = False
        self.mail_from = None
        self.rcpt_to = []
        self.data_content = ''
        self.send(221, 'SMTP server signing off')
        self.request.close()
        
    
    def send_email(self):
        outsider = {}
        for rcpt in self.rcpt_to:
            domain = rcpt.split('@')[-1]
            server = fdns_query(domain, 'MX')
            if server == self.domain:
                MAILBOXES[rcpt].append(self.data_content)
            else:
                if server in outsider:
                    outsider[server].append(rcpt)
                else:
                    outsider[server] = [rcpt]
        
        if len(outsider):
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            flag = False
            for domain in outsider:
                try:
                    domain = rcpt.split('@')[-1]
                    server = fdns_query(domain, 'MX')
                    host = 'localhost'
                    port = int(fdns_query(server, 'P'))
                    conn.connect((host, port))
                    assert conn.recv(1024).strip().decode().startswith('220')
                    
                    conn.sendall(f'helo {self.domain}\r\n'.encode())
                    assert conn.recv(1024).strip().decode().startswith('250')
                    
                    conn.sendall(f'mail FROM:<{self.mail_from}>\r\r'.encode())
                    assert conn.recv(1024).strip().decode().startswith('250')
                    
                    for rcpt in outsider[domain]:
                        conn.sendall(f'rcpt TO:<{rcpt}>\r\r'.encode())
                        assert conn.recv(1024).strip().decode().startswith('250')
                    
                    conn.sendall(b'data\r\n')
                    assert conn.recv(1024).strip().decode().startswith('354')
                    
                    conn.sendall(self.data_content.encode())
                    assert conn.recv(1024).strip().decode().startswith('250')
                    
                    conn.sendall(b'quit\r\n')
                    assert conn.recv(1024).strip().decode().startswith('221')
                    
                    flag = True
                except AssertionError as e:
                    print('An error occurred when sending emails')
                finally:
                    conn.close()
                
            if not flag:
                MAILBOXES[self.mail_from].append(self.data_content)
                    
        self.rcpt_to = []
        self.data_content = None


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
