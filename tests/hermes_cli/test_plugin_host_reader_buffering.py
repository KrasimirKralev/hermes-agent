"""A large plugin host reply must be read in chunks, not one read(2) per byte (#156448)."""

import io
import json
import subprocess

import hermes_yaml as yaml
import pytest

from hermes_cli import plugin_host
from hermes_cli import plugins as plugins_mod
from hermes_cli.plugins import PluginManager

REPLY_BYTES = 800 * 1024

BIG_REPLY_PLUGIN = f'''
import json

def register(ctx):
    ctx.register_tool(name="bigreply", toolset="bigreply", schema={{"name": "bigreply",
        "description": "d", "parameters": {{"type": "object", "properties": {{}}}}}},
        handler=lambda args, **kw: json.dumps({{"blob": "x" * {REPLY_BYTES}}}))
'''


class _CountingPipe(io.RawIOBase):
    """The host's stdout pipe, counting the reads the parent issues on it."""

    def __init__(self, raw):
        self._raw, self.reads = raw, 0

    def readable(self):
        return True

    def readinto(self, buffer):
        self.reads += 1
        return self._raw.readinto(buffer)

    def fileno(self):
        return self._raw.fileno()

    def close(self):
        self._raw.close()
        super().close()


@pytest.mark.platforms("posix")
def test_large_host_reply_is_read_in_chunks(tmp_path, monkeypatch):
    from tools.registry import registry

    home = tmp_path / ".hermes"
    plugin_dir = home / "plugins" / "bigreply"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump({"name": "bigreply", "version": "1.0"}),
                                            encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(BIG_REPLY_PLUGIN, encoding="utf-8")
    (home / "config.yaml").write_text(yaml.safe_dump(
        {"plugins": {"enabled": ["bigreply"], "isolation": "host"}}), encoding="utf-8")
    empty_bundled = tmp_path / "bundled"
    empty_bundled.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: empty_bundled)

    pipes = []

    class CountingPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stdout = _CountingPipe(self.stdout)
            pipes.append(self.stdout)

    monkeypatch.setattr(plugin_host.subprocess, "Popen", CountingPopen)
    manager = PluginManager()
    manager.discover_and_load()
    try:
        before = sum(pipe.reads for pipe in pipes)
        reply = json.loads(registry.dispatch("bigreply", {}, scope=manager.scope_key))
        reads = sum(pipe.reads for pipe in pipes) - before
    finally:
        manager.unload()
        manager._plugin_host().shutdown()

    assert len(reply["blob"]) == REPLY_BYTES
    assert reads < REPLY_BYTES // 1024, f"{reads} reads for a {REPLY_BYTES}-byte reply"
