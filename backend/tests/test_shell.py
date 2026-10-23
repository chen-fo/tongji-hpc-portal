import os
import pty
import select
import subprocess
import time

from app.services.audit import AuditStreamParser
from app.ssh.shell import shell_command


def test_real_bash_hook_cwd_cd_tilde_multiline_and_environment(tmp_path):
    official = tmp_path / "official"
    home = official / "user/test01"
    home.mkdir(parents=True)
    rc = official / ".bashrc"
    rc.write_text(
        'export HPC_MARKER=retained\nconda() { printf "conda stub\\n"; }\nmodule() { :; }\nPROMPT_COMMAND="printf \'PROMPT_OK\\n\'"\n'
    )
    baseline = rc.read_bytes()
    master, slave = pty.openpty()
    parser = AuditStreamParser("testnonce")
    process = subprocess.Popen(
        ["/bin/bash", "-c", shell_command(str(official), str(home), "testnonce", True)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "HOME": str(official), "TERM": "xterm"},
        start_new_session=True,
    )
    os.close(slave)
    events, output = [], ""

    def read_until(predicate):
        nonlocal output
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    data = os.read(master, 65536).decode(errors="replace")
                except OSError:
                    break
                visible, found = parser.feed(data)
                output += visible
                events.extend(found)
                if predicate():
                    return
        raise AssertionError(f"Bash check incomplete: events={events}; output={output[-3000:]}")

    try:
        read_until(lambda: any(kind == "status" for kind, _, _ in events))
        os.write(
            master,
            b'pwd\ncd /tmp\npwd\ncd ~\npwd\nprintf "line one\nline two\\n"\nfalse; printf "EXIT=%s\\n" "$?"\nprintf "ENV=%s\\n" "$HPC_MARKER"\n',
        )
        read_until(lambda: "ENV=retained\r\n" in output)
        commands = [(cwd, value) for kind, cwd, value in events if kind == "command"]
        assert (str(home), "pwd") in commands
        assert (str(home), "cd /tmp") in commands
        assert ("/tmp", "pwd") in commands
        assert ("/tmp", "cd ~") in commands
        assert any("line one\nline two" in value for _, value in commands)
        assert any(
            kind == "environment" and "result=preserved" in value for kind, _, value in events
        )
        assert "PROMPT_OK" in output
        assert "EXIT=1\r\n" in output
        assert rc.read_bytes() == baseline
        assert not (home / ".bash_history").exists()
        os.write(master, b"exit\n")
        read_until(lambda: any(kind == "command" and value == "exit" for kind, _, value in events))
        os.close(master)
        master = None
        process.wait(timeout=3)
    finally:
        if master is not None:
            os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
