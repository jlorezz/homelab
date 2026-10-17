#!/usr/bin/env python3
"""Prove the permitted Realtime projection delivers an own-tenant change, without app columns."""
import base64
import hashlib
import json
import secrets
import socket
import struct
import time
import urllib.parse
from pathlib import Path

import psycopg
from backup import private_json,record,HERE
from refresh import local_dsn
from smoke import login


class WebSocket:
    def __init__(self,key):
        self.socket=socket.create_connection(('127.0.0.1',18785),timeout=10)
        nonce=base64.b64encode(secrets.token_bytes(16)).decode()
        route='/realtime/v1/websocket?'+urllib.parse.urlencode({'apikey':key,'vsn':'1.0.0'})
        self.socket.sendall(('GET '+route+' HTTP/1.1\r\nHost: 127.0.0.1:18785\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+nonce+'\r\n\r\n').encode())
        response=b''
        while b'\r\n\r\n' not in response:response+=self.socket.recv(4096)
        header,self.buffer=response.split(b'\r\n\r\n',1)
        if not header.startswith(b'HTTP/1.1 101'):raise ValueError('Realtime WebSocket upgrade failed')
        expected=base64.b64encode(hashlib.sha1((nonce+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        if expected.lower() not in header.lower():raise ValueError('Invalid WebSocket accept response')

    def read(self,size):
        while len(self.buffer)<size:
            block=self.socket.recv(max(4096,size-len(self.buffer)))
            if not block:raise ConnectionError('Realtime socket closed')
            self.buffer+=block
        result=self.buffer[:size];self.buffer=self.buffer[size:];return result

    def send(self,payload,opcode=1):
        data=json.dumps(payload).encode() if opcode==1 else payload
        mask=secrets.token_bytes(4);length=len(data)
        header=bytes([0x80|opcode,0x80|length]) if length<126 else bytes([0x80|opcode,0x80|126])+struct.pack('!H',length)
        self.socket.sendall(header+mask+bytes(b^mask[i%4] for i,b in enumerate(data)))

    def receive(self,timeout):
        self.socket.settimeout(timeout)
        while True:
            first,second=self.read(2);length=second&127
            if length==126:length=struct.unpack('!H',self.read(2))[0]
            elif length==127:length=struct.unpack('!Q',self.read(8))[0]
            if second&128:raise ValueError('Unexpected masked server frame')
            data=self.read(length);opcode=first&15
            if opcode==9:self.send(data,10);continue
            if opcode==8:raise ConnectionError('Realtime socket closed')
            if opcode==1:return json.loads(data)

    def join(self,token):
        self.send({'topic':'realtime:recovery-smoke','event':'phx_join','payload':{'config':{'broadcast':{'ack':False,'self':False},'presence':{'key':''},'postgres_changes':[{'event':'UPDATE','schema':'public','table':'projects'}]},'access_token':token},'ref':'1'})
        deadline=time.monotonic()+20
        acknowledged=False
        while time.monotonic()<deadline:
            message=self.receive(deadline-time.monotonic())
            if message.get('event')=='phx_reply':
                if message['payload'].get('status')!='ok':raise ValueError('Realtime join rejected')
                acknowledged=True
            if message.get('event')=='system':
                if message['payload'].get('status')!='ok':raise ValueError('Realtime changefeed rejected')
                if acknowledged:return
        raise ValueError('Realtime subscription not ready')


def main():
    settings=private_json('/var/lib/dealpipe-testing/settings.json')
    sockets=[]
    try:
        for email in ['tenant-a@testing.example.invalid','tenant-b@testing.example.invalid']:
            connection=WebSocket(settings['TEST_ANON_KEY']);sockets.append(connection)
            connection.join(login(settings,email))
        with psycopg.connect(local_dsn(settings,False)) as conn:
            conn.execute("update public.projects set name='Synthetic Test Project A realtime verification' where id='f2000000-0000-4000-8000-000000000001'")
        deadline=time.monotonic()+15;event=None
        while time.monotonic()<deadline:
            message=sockets[0].receive(deadline-time.monotonic())
            if message.get('event')=='postgres_changes':event=message['payload']['data'];break
        if not event or event['record'].get('id')!='f2000000-0000-4000-8000-000000000001':raise ValueError('Own-tenant event missing')
        if 'name' in event['record']:raise ValueError('Application column leaked into Realtime projection')
        try:
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                message=sockets[1].receive(deadline-time.monotonic())
                if message.get('event')=='postgres_changes':raise ValueError('Cross-tenant realtime event leaked')
        except socket.timeout:pass
        result={'realtime_own_tenant_event':'passed','cross_tenant_event':'denied','application_columns':'absent'}
        config=json.loads((HERE/'config.json').read_text());state=json.loads((Path(config['state_directory'])/'status.json').read_text())
        record(config,'testing_realtime',sanitized_at=state['sanitize_refresh']['at'],**result)
        print(json.dumps(result))
    finally:
        for connection in sockets:connection.socket.close()


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Realtime verification failed; inspect only in the private operator context') from None
