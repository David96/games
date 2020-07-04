import asyncio
import json
import traceback

class Event:

    def __init__(self, event, per_user, notify_all):
        self.event = event
        self.per_user = per_user
        self.notify_all = notify_all

class GameRoom:

    def __init__(self, game):
        self.game = game(self)
        self.users = {}
        self.creator = None
        self.started = False

    async def send_error(self, socket, message, error_type=''):
        await socket.send(json.dumps({'type': 'error', 'error': error_type, 'msg': message}))

    def send_message(self, message):
        if self.users:
            msg = json.dumps({'type':'message', 'msg': message})
            asyncio.ensure_future(asyncio.wait(
                [socket.send(msg) for socket in self.users.values()]))

    def call_later(self, delay, callback):
        def run_scheduled():
            callback()
            asyncio.ensure_future(self.game.send_dirty())
        asyncio.get_event_loop().call_later(delay, run_scheduled)

    async def join(self, name, socket):
        if name in self.users:
            raise Exception('Name already taken!')
        if not self.users:
            self.creator = name
            await socket.send(json.dumps({'type': 'rights', 'status': 'creator'}))
        # User has to be added here first so it is known for events sent by game.add_player
        self.users[name] = socket
        if not await self.game.add_player(name):
            del self.users[name]
            raise Exception('Game already running.')
        self.send_message('%s joined the game!' % name)
        await socket.send(json.dumps({'type': 'joined'}))
        await self.game.send_dirty()

    async def leave(self, name):
        del self.users[name]
        if self.creator == name and self.users:
            self.creator = list(self.users.keys())[0]
            await self.users[self.creator].send(json.dumps({'type': 'rights', 'status': 'creator'}))
        self.game.remove_player(name)
        await self.game.send_dirty()
        self.send_message('%s left the game!' % name)

        # Reset game if all users are gone so newly joined users don't see the old score board
        if not self.users:
            self.game.reset()
            self.started = False

    async def fire_event(self, sender_name, event):
        if not event.per_user or not event.notify_all:
            data = event.event(sender_name)
        if event.notify_all:
            for name, socket in self.users.items():
                if event.per_user:
                    data = event.event(name)
                if data:
                    await socket.send(data)
        else:
            if data:
                await self.users[sender_name].send(data)

    async def on_message(self, name, message):
        socket = self.users[name]
        data = json.loads(message)
        if not self.started or self.game.game_over:
            if data['action'] != 'start_game':
                await self.send_error(socket, 'You can\'t do shit without starting the game first!')
            elif name != self.creator:
                await self.send_error(socket, 'That\'s not your job to do!')
            else:
                self.started = True
                self.game.start_game()
                await self.game.send_dirty()
        elif data['action'] in self.game.ACTIONS:
            action = self.game.ACTIONS[data['action']]
            action(name, data)
            await self.game.send_dirty()
        else:
            await self.send_error(socket, '%s is not a valid action!' % data['action'],
                                  'invalid_action')
