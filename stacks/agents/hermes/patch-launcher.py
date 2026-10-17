from pathlib import Path
import subprocess
import sys

root = Path('/opt/hermes')
source = root / 'hermes_cli/_launchers.py'
before = '    python = python or resolve_store_python(root) or Path(sys.executable)\n'
after = '''    if python is None:
        from hermes_cli.steward import read_install_stamp
        bundled_python = root / ".venv" / "bin" / "python"
        if (read_install_stamp(root).get("distribution") == "docker"
                and bundled_python.is_file()):
            python = bundled_python
        else:
            python = resolve_store_python(root) or Path(sys.executable)
'''
text = source.read_text()
assert text.count(before) == 1, 'Upstream launcher changed; review this patch'
source.write_text(text.replace(before, after))
sys.path.insert(0, str(root))
from hermes_cli._launchers import runtime_command

command = runtime_command(root, ['--version'])
assert command[0] == '/opt/hermes/.venv/bin/python'
subprocess.run(command, check=True)
