"""Authenticated encrypted cache. Reuse only objects committed from a stable Storage inventory."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets


class Cache:
    def __init__(self,directory,key_file):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700,parents=True,exist_ok=True);self.directory.chmod(0o700)
        key_file=Path(key_file)
        if not key_file.exists():
            fd=os.open(key_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(secrets.token_bytes(32))
        if key_file.is_symlink() or key_file.stat().st_mode&0o077 or key_file.stat().st_uid!=os.geteuid():raise ValueError('Cache key must be private')
        self.key=key_file.read_bytes()
        if len(self.key)!=32:raise ValueError('Unexpected cache key')
        self.used=set();self.pending=[]

    def __enter__(self):return self

    def __exit__(self,*args):self.abort()

    def identity(self,bucket,key,version,metadata,updated_at):
        values=json.dumps([bucket,key,version,metadata,updated_at.isoformat() if updated_at else None],sort_keys=True,separators=(',',':'))
        return hmac.new(self.key,values.encode(),hashlib.sha256).hexdigest()

    def restore(self,identity,destination):
        from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
        path=self.directory/(identity+'.cache')
        if not path.exists():return None
        if path.is_symlink():raise ValueError('Unexpected cache symlink')
        digest=hashlib.sha256();size=0
        with path.open('rb') as source,open(destination,'xb') as target:
            os.chmod(destination,0o600)
            if source.read(4)!=b'DPC1':raise ValueError('Unsupported cache format')
            nonce=source.read(12);source.seek(-16,2);tag=source.read(16);length=source.tell()-32
            source.seek(16)
            decryptor=Cipher(algorithms.AES(self.key),modes.GCM(nonce,tag)).decryptor()
            decryptor.authenticate_additional_data(identity.encode())
            remaining=length
            while remaining:
                block=source.read(min(1024*1024,remaining))
                if not block:raise ValueError('Truncated encrypted cache')
                remaining-=len(block);plain=decryptor.update(block);target.write(plain);digest.update(plain);size+=len(plain)
            tail=decryptor.finalize();target.write(tail);digest.update(tail);size+=len(tail)
        self.used.add(identity+'.cache')
        return {'bytes':size,'sha256':digest.hexdigest()}

    def stage(self,identity,source_file):
        from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
        nonce=secrets.token_bytes(12);pending=self.directory/(identity+'.pending-'+secrets.token_hex(8))
        fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as target,open(source_file,'rb') as source:
            encryptor=Cipher(algorithms.AES(self.key),modes.GCM(nonce)).encryptor()
            encryptor.authenticate_additional_data(identity.encode());target.write(b'DPC1'+nonce)
            while block:=source.read(1024*1024):target.write(encryptor.update(block))
            target.write(encryptor.finalize());target.write(encryptor.tag)
        self.pending.append((pending,self.directory/(identity+'.cache')));self.used.add(identity+'.cache')

    def invalidate(self,identity):
        (self.directory/(identity+'.cache')).unlink(missing_ok=True)

    def commit(self):
        for pending,target in self.pending:pending.replace(target)
        self.pending=[]
        for path in self.directory.iterdir():
            if path.name not in self.used and (path.name.endswith('.cache') or '.pending-' in path.name):path.unlink()

    def abort(self):
        for path,_ in self.pending:path.unlink(missing_ok=True)
        self.pending=[]
