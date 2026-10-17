#!/usr/bin/env python3
"""Loopback ingress to internal Docker networks; no container internet gateway is added."""
import asyncio
import ipaddress
import json
from pathlib import Path

CONFIG=Path('/etc/dealpipe-testing/proxy.json')


async def relay(reader,writer):
    try:
        while data:=await reader.read(65536):
            writer.write(data);await writer.drain()
    except (ConnectionError,OSError):pass
    finally:
        writer.close()


async def connect(reader,writer,port):
    try:
        target=json.loads(CONFIG.read_text())[str(port)]
        if not ipaddress.ip_address(target['host']).is_private:raise ValueError('Internal target required')
        upstream_read,upstream_write=await asyncio.wait_for(asyncio.open_connection(target['host'],target['port']),timeout=5)
        await asyncio.gather(relay(reader,upstream_write),relay(upstream_read,writer))
    except (ConnectionError,OSError,ValueError,TimeoutError):writer.close()


async def main():
    servers=[]
    for port in [18766,18785,18786,18788]:
        server=await asyncio.start_server(lambda r,w,p=port:connect(r,w,p),'127.0.0.1',port)
        servers.append(server)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__=='__main__':asyncio.run(main())
