from importlib import import_module
from typing import TYPE_CHECKING, Dict, Protocol, Union, cast

if TYPE_CHECKING:
    from ..markdown import Markdown

_plugins = {
    "speedup": "grabber._vendor.mistune.plugins.speedup.speedup",
    "strikethrough": "grabber._vendor.mistune.plugins.formatting.strikethrough",
    "mark": "grabber._vendor.mistune.plugins.formatting.mark",
    "insert": "grabber._vendor.mistune.plugins.formatting.insert",
    "superscript": "grabber._vendor.mistune.plugins.formatting.superscript",
    "subscript": "grabber._vendor.mistune.plugins.formatting.subscript",
    "footnotes": "grabber._vendor.mistune.plugins.footnotes.footnotes",
    "table": "grabber._vendor.mistune.plugins.table.table",
    "url": "grabber._vendor.mistune.plugins.url.url",
    "abbr": "grabber._vendor.mistune.plugins.abbr.abbr",
    "def_list": "grabber._vendor.mistune.plugins.def_list.def_list",
    "math": "grabber._vendor.mistune.plugins.math.math",
    "ruby": "grabber._vendor.mistune.plugins.ruby.ruby",
    "task_lists": "grabber._vendor.mistune.plugins.task_lists.task_lists",
    "spoiler": "grabber._vendor.mistune.plugins.spoiler.spoiler",
}


class Plugin(Protocol):
    def __call__(self, md: "Markdown") -> None: ...


_cached_modules: Dict[str, Plugin] = {}

PluginRef = Union[str, Plugin]  # reference to register a plugin


def import_plugin(name: PluginRef) -> Plugin:
    if callable(name):
        return name

    if name in _cached_modules:
        return _cached_modules[name]

    if name in _plugins:
        module_path, func_name = _plugins[name].rsplit(".", 1)
    else:
        module_path, func_name = name.rsplit(".", 1)

    module = import_module(module_path)
    plugin = cast(Plugin, getattr(module, func_name))
    _cached_modules[name] = plugin
    return plugin
