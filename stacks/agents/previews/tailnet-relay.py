#!/usr/bin/env python3
"""Carry preview TLS from Docker to the NAS's own Tailscale Serve listeners."""
import argparse
import asyncio

async def relay(reader, writer):
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()

async def connect(reader, writer, target, port):
    try:
        remote_reader, remote_writer = await asyncio.wait_for(asyncio.open_connection(target, port), timeout=5)
        await asyncio.gather(relay(reader, remote_writer), relay(remote_reader, writer))
    except (ConnectionError, OSError, TimeoutError):
        writer.close()

async def main(args):
    servers = []
    for port in (4101, 4102):
        servers.append(await asyncio.start_server(lambda r,w,p=port: connect(r,w,args.target,p), args.listen, port))
    await asyncio.gather(*(server.serve_forever() for server in servers))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen',default='172.17.0.1')
    parser.add_argument('--target',default='100.125.180.36')
    asyncio.run(main(parser.parse_args()))
