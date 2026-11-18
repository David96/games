"""Manage AI players (bots) that join rooms over the game's websocket API.

The bots live in one separate process, the bot daemon (wizard-ki/
bot_daemon.py): it hosts any number of bots, each with its own websocket
connection, but all sharing a single loaded policy. The daemon is
expected to be running already - this module is only its client. It
keeps track of the bots it ordered and speaks the daemon's control
protocol, newline-delimited JSON over a persistent TCP connection
(127.0.0.1, by default on the port one above the game server's):

    {"cmd": "add", "room": ROOMID, "name": NAME, "delay": SECONDS}  -> {"ok": true}
    {"cmd": "stop", "name": NAME, "room": ROOMID}                   -> {"ok": true, "stopped": 1}
    {"cmd": "stop_room", "room": ROOMID}                            -> {"ok": true, "stopped": N}
    {"cmd": "ping"}                                                 -> {"ok": true, "bots": N}

Bots are ordinary websocket clients from the daemon's point of view:
they join a room, receive state events and send actions, exactly like a
browser. This module therefore does not know anything about the game at
all - it only orders bots around and stops them again.

Typical use from the server:

    manager = BotManager(url=f"ws://127.0.0.1:{port}")
    manager.add(room_id, "BertieBot")
    ...
    manager.stop(room_id, name)   # bot never joined, timed out
    manager.stop_room(room_id)    # room deleted
    manager.shutdown()           # server shutdown

The local registry only tracks what this manager ordered; the daemon
owns the actual bot lifetimes (a bot ends its game by itself when the
game is over). Entries therefore outlive their bots, which is harmless
for the only consumer, the bots-only room check: stale names are never
part of a room's users anymore. suggest_name() just moves on to the
next themed name.

The daemon is not started here. On shutdown() the control connection is
simply closed: the daemon stays put for a while (its linger), so the
next server instance reconnects to it with the model still loaded.

Configuration can be overridden with the environment variables
WIZARD_BOT_URL, WIZARD_BOT_MAX, WIZARD_BOT_THINK_DELAY and
WIZARD_BOT_CONTROL_PORT. The model checkpoint belongs to the daemon;
per-bot difficulties are not supported.
"""

import json
import os
import socket
import threading
from urllib.parse import urlparse

DEFAULT_URL = "ws://127.0.0.1:6791"
DEFAULT_MAX_BOTS = 8
DEFAULT_THINK_DELAY = 0.8
DEFAULT_CONTROL_HOST = "127.0.0.1"

# Names humans will see in the player list
BOT_NAMES = [
        "BertieBot", "Beep Boop", "NoAI", "Boop Beep", "Wizard.exe", "Botwin"
]


class BotManager:

    def __init__(self, url=None, max_bots=None, think_delay=None):
        env = os.environ
        self.url = url or env.get("WIZARD_BOT_URL", DEFAULT_URL)
        self.max_bots = int(env.get("WIZARD_BOT_MAX", max_bots or DEFAULT_MAX_BOTS))
        if think_delay is None:
            think_delay = float(env.get("WIZARD_BOT_THINK_DELAY", DEFAULT_THINK_DELAY))
        self.think_delay = think_delay
        # The daemon defaults to the port one above the game server's
        game_port = urlparse(self.url).port or 6791
        self.control_host = DEFAULT_CONTROL_HOST
        self.control_port = int(env.get("WIZARD_BOT_CONTROL_PORT", game_port + 1))
        # room id -> set of bot names ordered from the daemon
        self._bots = {}
        # The persistent control connection, opened lazily on first use
        self._control = None

    # -- queries ---------------------------------------------------------

    def is_bot(self, room_id, name):
        return name in self._bots.get(room_id, ())

    def bots_in(self, room_id):
        return list(self._bots.get(room_id, ()))

    def count(self):
        return sum(len(names) for names in self._bots.values())

    def suggest_name(self, taken=()):
        """Pick a themed name that is not in use in the room."""
        taken_lower = {name.lower() for name in taken}
        for base in BOT_NAMES:
            if base.lower() not in taken_lower:
                return base
        number = 2
        while f"Bot {number}" in taken:
            number += 1
        return f"Bot {number}"

    # -- lifecycle -------------------------------------------------------

    def add(self, room_id, name):
        """Order a bot from the daemon; it joins <room_id> as <name>."""
        if not room_id or not name:
            print("BotManager: room id and name are required")
            return False
        if self.is_bot(room_id, name):
            print(f"BotManager: {name} is already playing in room {room_id}")
            return False
        if self.count() >= self.max_bots:
            print(f"BotManager: refusing bot, limit of {self.max_bots} reached")
            return False
        if not self._send({"cmd": "add", "room": room_id, "name": name,
                           "delay": self.think_delay}):
            return False
        self._bots.setdefault(room_id, set()).add(name)
        print(f"BotManager: started bot {name} in room {room_id} (daemon)")
        return True

    def stop(self, room_id, name):
        """Stop one bot, e.g. when it never joined."""
        if name not in self._bots.get(room_id, ()):
            return False
        self._send({"cmd": "stop", "room": room_id, "name": name})
        self._forget(room_id, name)
        print(f"BotManager: stopped bot {name} in room {room_id}")
        return True

    def stop_room(self, room_id):
        """Stop all bots of a room, e.g. when the room is deleted."""
        names = self._bots.pop(room_id, set())
        if names:
            self._send({"cmd": "stop_room", "room": room_id})
            print(f"BotManager: stopped {len(names)} bot(s) in room {room_id}")
        return len(names)

    def shutdown(self):
        """Server shutdown: close the control connection. The daemon
        keeps running (it lingers) so the next server instance can
        reconnect to it; its bots end with the game server's own
        connections anyway."""
        self._disconnect()

    # -- daemon control connection ---------------------------------------

    def _send(self, command):
        """Send one control command, connecting on first use. Returns
        False if the daemon cannot be reached at all."""
        control = self._ensure_connected()
        if control is None:
            return False
        try:
            control.sendall((json.dumps(command) + "\n").encode())
            return True
        except OSError:
            print("BotManager: lost the connection to the bot daemon")
            self._disconnect()
            return False

    def _ensure_connected(self):
        """The daemon expects a single persistent control connection;
        open it on first use and keep it until the daemon goes away."""
        if self._control is not None:
            return self._control
        try:
            # The timeout only bounds the connect; _read_replies treats
            # it as idle on an established connection
            self._control = socket.create_connection(
                (self.control_host, self.control_port), timeout=2.0)
        except OSError as error:
            print(f"BotManager: cannot reach the bot daemon at "
                  f"{self.control_host}:{self.control_port}: {error}")
            return None
        threading.Thread(target=self._read_replies, daemon=True).start()
        return self._control

    def _read_replies(self):
        """Drain the daemon's replies on a side thread. Nobody waits for
        them (the join timeout covers missing bots), but they must not
        clog the connection; failures are logged for the journal."""
        control = self._control
        buffer = b""
        try:
            while True:
                try:
                    chunk = control.recv(4096)
                except socket.timeout:
                    continue    # just idle, the connection is fine
                if not chunk:
                    break       # the daemon closed the connection
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._handle_reply(line)
        except OSError:
            pass
        finally:
            self._control = None
            try:
                control.close()
            except OSError:
                pass

    @staticmethod
    def _handle_reply(line):
        try:
            reply = json.loads(line.decode("utf-8"))
        except ValueError:
            return
        if not reply.get("ok"):
            print(f"BotManager: daemon: {reply}")

    def _disconnect(self):
        control, self._control = self._control, None
        if control is not None:
            try:
                control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                control.close()
            except OSError:
                pass

    def _forget(self, room_id, name):
        names = self._bots.get(room_id)
        if names is not None:
            names.discard(name)
            if not names:
                del self._bots[room_id]
