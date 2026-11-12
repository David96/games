"""Manage AI players (bots) that join rooms over the game's websocket API.

The bots themselves live in a separate project (wizard-ki-v2) and are
ordinary websocket clients: they create or join a room, receive state
events and send actions, exactly like a browser. This module therefore
does not know anything about the game at all - it only spawns the bot
launcher process, keeps track of it and terminates it again.

The launcher (wizard-ki-v2/run_bot.py) is expected to accept:

    python run_bot.py --url ws://localhost:6791 --room ROOMID --name NAME
                      --checkpoint PATH [--think-delay SECONDS]

and to exit by itself when the room disappears, it gets kicked or the
server goes away. Restarting a bot after a finished game is the
launcher's job as well; it simply reconnects and joins the same room.

Typical use from the server:

    manager = BotManager(url=f"ws://127.0.0.1:{port}")
    manager.add(room_id, "Merlin", "normal")
    ...
    manager.stop(room_id, name)   # kicked
    manager.stop_room(room_id)    # room deleted
    manager.shutdown()            # server shutdown

Configuration can be overridden with the environment variables
WIZARD_AI_DIR, WIZARD_AI_PYTHON, WIZARD_BOT_URL, WIZARD_BOT_MAX and
WIZARD_BOT_CHECKPOINTS (difficulty=path,difficulty=path).
"""

import os
import subprocess
import sys

DEFAULT_AI_DIR = os.path.expanduser("~/Dokumente/Projekte/wizard-ki-v2")
DEFAULT_LAUNCHER = "run_bot.py"
DEFAULT_URL = "ws://127.0.0.1:6791"
DEFAULT_MAX_BOTS = 8
DEFAULT_THINK_DELAY = 0.8

# Difficulty is just a checkpoint of the shared policy
DEFAULT_CHECKPOINTS = {
    "easy": "training_actor_critic/shared_policy-v2-12000.weights.h5",
    "normal": "training_actor_critic/shared_policy-v2-24000.weights.h5",
    "hard": "training_actor_critic/shared_policy-v2-36000.weights.h5",
}

# Names humans will see in the player list
BOT_NAMES = [
    "Merlin", "Morgana", "Radagast", "Circe", "Gandalf", "Morgause",
    "Prospero", "Nimue", "Alatar", "Pallando", "Talisman", "Yennefer",
]


def _parse_checkpoints(spec):
    checkpoints = dict(DEFAULT_CHECKPOINTS)
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        difficulty, path = entry.split("=", 1)
        checkpoints[difficulty.strip()] = path.strip()
    return checkpoints


class BotManager:

    def __init__(self, ai_dir=None, python=None, url=None, max_bots=None,
                 think_delay=None, checkpoints=None):
        env = os.environ
        self.ai_dir = ai_dir or env.get("WIZARD_AI_DIR", DEFAULT_AI_DIR)
        self.python = python or env.get("WIZARD_AI_PYTHON", sys.executable)
        self.url = url or env.get("WIZARD_BOT_URL", DEFAULT_URL)
        self.max_bots = int(env.get("WIZARD_BOT_MAX", max_bots or DEFAULT_MAX_BOTS))
        if think_delay is None:
            think_delay = float(env.get("WIZARD_BOT_THINK_DELAY", DEFAULT_THINK_DELAY))
        self.think_delay = think_delay
        self.checkpoints = dict(DEFAULT_CHECKPOINTS)
        if "WIZARD_BOT_CHECKPOINTS" in env:
            self.checkpoints.update(_parse_checkpoints(env["WIZARD_BOT_CHECKPOINTS"]))
        if checkpoints:
            self.checkpoints.update(checkpoints)
        self.launcher = os.path.join(self.ai_dir, DEFAULT_LAUNCHER)
        # room id -> {player name: process}
        self._procs = {}

    # -- queries ---------------------------------------------------------

    def is_bot(self, room_id, name):
        self._prune()
        return name in self._procs.get(room_id, {})

    def bots_in(self, room_id):
        self._prune()
        return list(self._procs.get(room_id, {}))

    def count(self):
        self._prune()
        return sum(len(procs) for procs in self._procs.values())

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

    def add(self, room_id, name, difficulty="normal"):
        """Spawn a bot process that joins <room_id> as <name>."""
        self._prune()
        if not room_id or not name:
            print("BotManager: room id and name are required")
            return False
        if self.is_bot(room_id, name):
            print(f"BotManager: {name} is already playing in room {room_id}")
            return False
        if self.count() >= self.max_bots:
            print(f"BotManager: refusing bot, limit of {self.max_bots} reached")
            return False
        checkpoint = self.checkpoints.get(difficulty)
        if not checkpoint:
            print(f"BotManager: unknown difficulty {difficulty!r}")
            return False
        if not os.path.isfile(self.launcher):
            print(f"BotManager: bot launcher not found: {self.launcher}")
            return False
        command = [
            self.python, self.launcher,
            "--url", self.url,
            "--room", room_id,
            "--name", name,
            "--checkpoint", os.path.join(self.ai_dir, checkpoint),
            "--think-delay", str(self.think_delay),
        ]
        try:
            proc = subprocess.Popen(command, cwd=self.ai_dir)
        except OSError as error:
            print(f"BotManager: could not start bot {name}: {error}")
            return False
        self._procs.setdefault(room_id, {})[name] = proc
        print(f"BotManager: started bot {name} in room {room_id} (pid {proc.pid})")
        return True

    def stop(self, room_id, name, timeout=3.0):
        """Terminate one bot, e.g. after it was kicked."""
        self._prune()
        proc = self._procs.get(room_id, {}).pop(name, None)
        if proc is None:
            return False
        self._terminate(proc, timeout)
        print(f"BotManager: stopped bot {name} in room {room_id}")
        return True

    def stop_room(self, room_id, timeout=3.0):
        """Terminate all bots of a room, e.g. when the room is deleted."""
        self._prune()
        procs = self._procs.pop(room_id, {})
        for proc in procs.values():
            self._terminate(proc, timeout)
        if procs:
            print(f"BotManager: stopped {len(procs)} bot(s) in room {room_id}")
        return len(procs)

    def shutdown(self, timeout=3.0):
        """Terminate every bot (server shutdown)."""
        for room_id in list(self._procs):
            self.stop_room(room_id, timeout)

    # -- internals -------------------------------------------------------

    def _prune(self):
        """Forget bots whose process already exited on its own."""
        for room_id, procs in list(self._procs.items()):
            for name, proc in list(procs.items()):
                if proc.poll() is not None:
                    print(f"BotManager: bot {name} in room {room_id} exited "
                          f"with code {proc.returncode}")
                    del procs[name]
            if not procs:
                del self._procs[room_id]

    @staticmethod
    def _terminate(proc, timeout):
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except OSError:
            pass
