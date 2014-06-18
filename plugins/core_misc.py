import asyncio
import socket

from cloudbot import hook

socket.setdefaulttimeout(10)


# Auto-join on Invite (Configurable, defaults to True)
@asyncio.coroutine
@hook.irc_raw('INVITE')
def invite(irc_paramlist, conn):
    """
    :type irc_paramlist: list[str]
    :type conn: cloudbot.core.connection.Client
    """
    invite_join = conn.config.get('invite_join', True)
    if invite_join:
        conn.join(irc_paramlist[-1])
