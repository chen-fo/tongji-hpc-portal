"""Bounded TCP fallback across a direct SSH host's DNS addresses."""

import asyncio
import socket


async def open_direct_socket(host: str, port: int, family: int) -> socket.socket:
    """Race resolved addresses, without authenticating to multiple SSH servers.

    The caller supplies the overall timeout and hands the winning socket to
    AsyncSSH with the original hostname, preserving its known_hosts policy.
    """
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(addresses))
    if not addresses:
        raise OSError("SSH host has no TCP addresses")

    sockets = []

    async def attempt(address, delay):
        await asyncio.sleep(delay)
        af, kind, protocol, _, endpoint = address
        sock = socket.socket(af, kind, protocol)
        sockets.append(sock)
        sock.setblocking(False)
        await loop.sock_connect(sock, endpoint)
        return sock

    # A stalled first IPv4 address must not block the remaining IPv4 addresses.
    tasks = [
        asyncio.create_task(attempt(address, index * 0.25))
        for index, address in enumerate(addresses)
    ]
    winner = None
    last_error = None
    try:
        for completed in asyncio.as_completed(tasks):
            try:
                winner = await completed
                break
            except OSError as exc:
                last_error = exc
        if winner is None:
            raise last_error or OSError("All SSH TCP addresses failed")
    finally:
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            # Also close the winner if cancellation interrupts ownership transfer.
            for sock in sockets:
                sock.close()
            raise
        for sock in sockets:
            if sock is not winner:
                sock.close()
    return winner
