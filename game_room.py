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

    async def send_message(self, message):
        if self.users:
            msg = json.dumps({'type':'message', 'msg': message})
            await asyncio.wait([socket.send(msg) for socket in self.users.values()])

    async def join(self, name, socket):
        if name in self.users:
            return False
        if not self.users:
            self.creator = name
            await socket.send(json.dumps({'type': 'rights', 'status': 'creator'}))
        self.users[name] = socket
        allowed = await self.game.add_player(name)
        if not allowed:
            return False
        await self.send_message('%s joined the game!' % name)
        return True

    async def leave(self, name):
        del self.users[name]
        if self.creator == name:
            self.creator = list(self.users.keys())[0]
            await self.users[self.creator].send(json.dumps({'type': 'rights', 'status': 'creator'}))
        await self.game.remove_player(name)
        await self.send_message('%s left the game!' % name)

    async def fire_event(self, sender_name, event):
        if not event.per_user or not event.notify_all:
            data = await event.event(sender_name)
        if event.notify_all:
            for name, socket in self.users.items():
                if event.per_user:
                    data = await event.event(name)
                print(data)
                if data:
                    await socket.send(data)
        else:
            print(data)
            if data:
                await self.users[sender_name].send(data)

    async def run_action(self, action, name, data, socket):
        try:
            await action(name, data)
        except Exception as e:
            await self.send_error(socket, str(e))
            traceback.print_exc()

    async def on_message(self, name, message):
        socket = self.users[name]
        try:
            data = json.loads(message)
        except Exception as e:
            await self.send_error(socket, str(e))
            return
        if not self.started or self.game.game_over:
            if data['action'] != 'start_game':
                await self.send_error(socket, 'You can\'t do shit without starting the game first!')
            elif name != self.creator:
                await self.send_error(socket, 'That\'s not your job to do!')
            else:
                self.started = True
                await self.game.start_game()
        elif data['action'] in self.game.ACTIONS:
            action = self.game.ACTIONS[data['action']]
            asyncio.ensure_future(self.run_action(action, name, data, socket))
        else:
            await self.send_error(socket, '%s is not a valid action!' % data['action'],
                                  'invalid_action')
