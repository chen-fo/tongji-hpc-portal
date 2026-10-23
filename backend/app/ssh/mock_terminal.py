"""Deterministic terminal simulator. It never executes commands on the gateway Mac."""

import asyncio
import base64
import posixpath


def event_frame(nonce: str, kind: str, cwd: str, value: str) -> str:
    data = base64.b64encode(f"{kind}\0{cwd}\0{value}".encode()).decode()
    return f"\x1b]777;PORTAL;{nonce};{data}\x07"


class MockTerminal:
    def __init__(self, home: str, nonce: str):
        self.home = self.cwd = home
        self.nonce = nonce
        self.stdout = self.stdin = self
        self.buffer = ""
        self.closed = False
        self.output = asyncio.Queue()
        self.output.put_nowait(
            "Mock SSH terminal (simulation only)\r\n"
            + event_frame(nonce, "status", home, "enabled")
            + "$ "
        )

    async def read(self, _size=4096):
        return await self.output.get()

    def write(self, data: str):
        for char in data:
            if char == "\x04":
                self.close()
                return
            if char == "\x03":
                self.buffer = ""
                self.output.put_nowait("^C\r\n$ ")
            elif char in {"\x7f", "\b"}:
                self.buffer = self.buffer[:-1]
            elif char in {"\r", "\n"}:
                command, self.buffer = self.buffer, ""
                if not command:
                    self.output.put_nowait("\r\n$ ")
                    continue
                self.output.put_nowait(
                    "\r\n" + event_frame(self.nonce, "command", self.cwd, command)
                )
                if command == "pwd":
                    output = self.cwd
                elif command == "cd" or command == "cd ~":
                    self.cwd = self.home
                    output = ""
                elif command.startswith("cd "):
                    target = command[3:].replace("~", self.home, 1)
                    self.cwd = posixpath.normpath(posixpath.join(self.cwd, target))
                    output = ""
                elif command.startswith("echo "):
                    output = command[5:]
                elif command == "exit":
                    self.close()
                    return
                else:
                    output = "[Mock] command accepted; no real command executed"
                self.output.put_nowait(output + "\r\n$ ")
            else:
                self.buffer += char
                self.output.put_nowait(char)

    async def drain(self):
        pass

    def change_terminal_size(self, cols, rows):
        self.size = (cols, rows)

    def close(self):
        if not self.closed:
            self.closed = True
            self.output.put_nowait("")

    async def wait_closed(self):
        pass
