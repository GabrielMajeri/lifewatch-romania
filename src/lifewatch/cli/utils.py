import re

from typer.core import TyperGroup


class AliasGroup(TyperGroup):
    """
    Group base class for Typer, which adds support for command aliases.

    Based on https://github.com/fastapi/typer/issues/132#issuecomment-1789722890
    """

    _CMD_SPLIT_P = re.compile(r" ?[,|] ?")

    def get_command(self, ctx, cmd_name):
        cmd_name = self._group_cmd_name(cmd_name)
        return super().get_command(ctx, cmd_name)

    def _group_cmd_name(self, default_name):
        for cmd in self.commands.values():
            name = cmd.name
            if name and default_name in self._CMD_SPLIT_P.split(name):
                return name
        return default_name
