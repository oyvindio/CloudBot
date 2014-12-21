import asyncio
import time
import logging
import re
import os
import gc

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.schema import MetaData

from concurrent.futures import FIRST_COMPLETED

import cloudbot
from cloudbot.config import Config
from cloudbot.reloader import PluginReloader
from cloudbot.plugin import PluginManager
from cloudbot.event import Event, CommandEvent, RegexEvent, EventType
from cloudbot.dialect.irc.client import IRCClient
from cloudbot.util import botvars, formatting

from cloudbot.state import Network
from cloudbot.state import Server


logger = logging.getLogger("cloudbot")


def clean_name(n):
    """strip all spaces and capitalization
    :type n: str
    :rtype: str
    """
    return re.sub('[^A-Za-z0-9_]+', '', n.replace(" ", "_"))


class CloudBot:
    """
    :type start_time: float
    :type running: bool
    :type connections: list[Connection | IRCClient]
    :type data_dir: bytes
    :type config: core.config.Config
    :type plugin_manager: PluginManager
    :type reloader: PluginReloader
    :type db_engine: sqlalchemy.engine.Engine
    :type db_factory: sqlalchemy.orm.session.sessionmaker
    :type db_session: sqlalchemy.orm.scoping.scoped_session
    :type db_metadata: sqlalchemy.sql.schema.MetaData
    :type loop: asyncio.events.AbstractEventLoop
    :type stopped_future: asyncio.Future
    :param: stopped_future: Future that will be given a result when the bot has stopped.
    """

    def __init__(self, loop=asyncio.get_event_loop()):
        # basic variables
        self.loop = loop
        self.start_time = time.time()
        self.running = True
        # future which will be called when the bot stops
        self.stopped_future = asyncio.Future(loop=self.loop)

        # stores each bot server connection
        self.connections = []
        self.networks = {}

        # for plugins
        self.logger = logger

        # declare and create data folder
        self.data_dir = os.path.abspath('data')
        if not os.path.exists(self.data_dir):
            logger.debug("Data folder not found, creating.")
            os.mkdir(self.data_dir)

        # set up config
        self.config = Config(self)
        logger.debug("Config system initialised.")

        # log developer mode
        if cloudbot.dev_mode.get("plugin_reloading"):
            logger.info("Enabling developer option: plugin reloading.")
        if cloudbot.dev_mode.get("config_reloading"):
            logger.info("Enabling developer option: config reloading.")
        if cloudbot.dev_mode.get("console_debug"):
            logger.info("Enabling developer option: console debug.")
        if cloudbot.dev_mode.get("file_debug"):
            logger.info("Enabling developer option: file debug")

        # setup db
        db_path = self.config.get('database', 'sqlite:///cloudbot.db')
        self.db_engine = create_engine(db_path)
        self.db_factory = sessionmaker(bind=self.db_engine)
        self.db_session = scoped_session(self.db_factory)
        self.db_metadata = MetaData()
        # set botvars.metadata so plugins can access when loading
        botvars.metadata = self.db_metadata
        logger.debug("Database system initialised.")

        # Bot initialisation complete
        logger.debug("Bot setup completed.")

        # create bot connections
        self.create_connections()

        if cloudbot.dev_mode.get("plugin_reloading"):
            self.reloader = PluginReloader(self)

        self.plugin_manager = PluginManager(self)

    def run(self, loop):
        #asyncio.async(self._run(loop), loop=loop)
        try:
            loop.run_until_complete(self._run(loop))
        except (KeyboardInterrupt, SystemExit):
            self.stop(loop)

    @asyncio.coroutine
    def _run(self, loop):
        # Load plugins
        yield from self.plugin_manager.load_all(os.path.abspath("plugins"))

        # Run a manual garbage collection cycle, to clean up any unused objects created during initialization
        gc.collect()

        # If we we're stopped while loading plugins, cancel that and just stop
        if not self.running:
            logger.info("Killed while loading, exiting")
            return

        if cloudbot.dev_mode.get("plugin_reloading"):
            # start plugin reloader
            self.reloader.start(os.path.abspath("plugins"))

        # TODO less hard-coded here would be nice
        clients = []


        for network in self.networks.values():
            clients.append(network.client_class(self, loop, network))

        # TODO hmm this feels slightly janky; should this all be done earlier
        # perhaps
        self.connections = clients

        # TODO gracefully handle failed connections, and only bail entirely if
        # they all fail?
        yield from asyncio.gather(*[client.connect() for client in clients])

        coros = {client: asyncio.Task(client.read_event()) for client in clients}
        while True:
            done, pending = yield from asyncio.wait(
                coros.values(),
                return_when=FIRST_COMPLETED)


            # Replace any coros that finished with fresh ones for the next run
            # of the loop
            for client, coro in coros.items():
                if coro in done:
                    coros[client] = asyncio.Task(client.read_event())

            # Evaluate all the tasks that completed (probably just one)
            for d in done:
                event = yield from d
                if event:
                    print("PLS PROCESS: ", d)
                    yield from self.process(event)


    def add_network(self, network):
        # TODO check for dupes!
        self.networks[network.name] = network

    def create_connections(self):
        """ Create a BotConnection for all the networks defined in the config """
        for conf in self.config['connections']:
            # strip all spaces and capitalization from the connection name
            readable_name = conf['name']
            name = clean_name(readable_name)

            # hardcoded, for now
            client_class = IRCClient

            network = Network(name, conf)
            network.add_preferred_nick(conf['nick'])

            network.add_server(
                conf['connection']['server'],
                conf['connection'].get('port', 6667),
                tls = conf['connection'].get('ssl', False),
                password="",
            )

            for channel in conf['channels']:
                network.add_autojoin(channel)

            network.client_class = client_class
            self.add_network(network)

            logger.debug("[{}] Created connection.".format(readable_name))

    @asyncio.coroutine
    def stop(self, reason=None, *, restart=False):
        """quits all networks and shuts the bot down"""
        logger.info("Stopping bot.")

        if cloudbot.dev_mode.get("config_reloading"):
            logger.debug("Stopping config reloader.")
            self.config.stop()

        if cloudbot.dev_mode.get("plugin_reloading"):
            logger.debug("Stopping plugin reloader.")
            self.reloader.stop()

        for connection in self.connections:
            if not connection.connected:
                # Don't quit a connection that hasn't connected
                continue
            logger.debug("[{}] Closing connection.".format(connection.readable_name))

            connection.quit(reason)

        yield from asyncio.sleep(1.0)  # wait for 'QUIT' calls to take affect

        for connection in self.connections:
            if not connection.connected:
                # Don't close a connection that hasn't connected
                continue
            connection.close()

        self.running = False
        # Give the stopped_future a result, so that run() will exit
        self.stopped_future.set_result(restart)

    @asyncio.coroutine
    def restart(self, reason=None):
        """shuts the bot down and restarts it"""
        yield from self.stop(reason=reason, restart=True)


    @asyncio.coroutine
    def process(self, event):
        """
        :type event: Event
        """
        print("processing")

        run_before_tasks = []
        tasks = []
        command_prefix = event.conn.config.get('command_prefix', '.')

        # Raw IRC hook
        for raw_hook in self.plugin_manager.catch_all_triggers:
            # run catch-all coroutine hooks before all others - TODO: Make this a plugin argument
            if not raw_hook.threaded:
                run_before_tasks.append(
                    self.plugin_manager.launch(raw_hook, Event(hook=raw_hook, base_event=event)))
            else:
                tasks.append(self.plugin_manager.launch(raw_hook, Event(hook=raw_hook, base_event=event)))
        if event.irc_command in self.plugin_manager.raw_triggers:
            for raw_hook in self.plugin_manager.raw_triggers[event.irc_command]:
                tasks.append(self.plugin_manager.launch(raw_hook, Event(hook=raw_hook, base_event=event)))

        # Event hooks
        if event.type in self.plugin_manager.event_type_hooks:
            for event_hook in self.plugin_manager.event_type_hooks[event.type]:
                tasks.append(self.plugin_manager.launch(event_hook, Event(hook=event_hook, base_event=event)))

        if event.type is EventType.message:
            print(event.content)
            # Commands
            if event.chan.lower() == event.nick.lower():  # private message, no command prefix
                command_re = r'(?i)^(?:[{}]?|{}[,;:]+\s+)(\w+)(?:$|\s+)(.*)'.format(command_prefix, event.conn.nick)
            else:
                command_re = r'(?i)^(?:[{}]|{}[,;:]+\s+)(\w+)(?:$|\s+)(.*)'.format(command_prefix, event.conn.nick)

            match = re.match(command_re, event.content)

            if match:
                command = match.group(1).lower()
                if command in self.plugin_manager.commands:
                    command_hook = self.plugin_manager.commands[command]
                    command_event = CommandEvent(hook=command_hook, text=match.group(2).strip(),
                                                 triggered_command=command, base_event=event)
                    tasks.append(self.plugin_manager.launch(command_hook, command_event))
                else:
                    potential_matches = []
                    for potential_match, plugin in self.plugin_manager.commands.items():
                        if potential_match.startswith(command):
                            potential_matches.append((potential_match, plugin))
                    if potential_matches:
                        if len(potential_matches) == 1:
                            command_hook = potential_matches[0][1]
                            command_event = CommandEvent(hook=command_hook, text=match.group(2).strip(),
                                                         triggered_command=command, base_event=event)
                            tasks.append(self.plugin_manager.launch(command_hook, command_event))
                        else:
                            event.notice("Possible matches: {}".format(
                                formatting.get_text_list([command for command, plugin in potential_matches])))

            # Regex hooks
            for regex, regex_hook in self.plugin_manager.regex_hooks:
                match = regex.search(event.content)
                if match:
                    regex_event = RegexEvent(hook=regex_hook, match=match, base_event=event)
                    tasks.append(self.plugin_manager.launch(regex_hook, regex_event))

        # Run the tasks
        yield from asyncio.gather(*run_before_tasks, loop=self.loop)
        yield from asyncio.gather(*tasks, loop=self.loop)
