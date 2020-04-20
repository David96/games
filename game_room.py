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
        game.init(self)
        self.game = game
        self.users = {}

    async def send_error(self, socket, message, error_type=''):
        await socket.send(json.dumps({'type': 'error', 'error': error_type, 'msg': message}))

    async def send_message(self, message):
        if self.users:
            msg = json.dumps({'type':'message', 'msg': message})
            await asyncio.wait([socket.send(msg) for socket in self.users.values()])

    async def join(self, name, socket):
        if name in self.users:
            return False
        self.users[name] = socket
        events = self.game.add_player(name)
        for event in events:
            await self.fire_event(name, event)
        await self.send_message('%s joined the game!' % name)
        return True

    async def leave(self, name):
        self.game.remove_player(name)
        await self.send_message('%s left the game!' % name)
        del self.users[name]

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


    async def on_message(self, name, message):
        socket = self.users[name]
        try:
            data = json.loads(message)
        except Exception as e:
            await self.send_error(socket, str(e))
            return
        if data['action'] in self.game.ACTIONS:
            try:
                action = self.game.ACTIONS[data['action']]
                await action(name, data)

            except Exception as e:
                await self.send_error(socket, str(e))
                traceback.print_exc()
        else:
            await self.send_error(socket, '%s is not a valid action!' % data['action'],
                                  'invalid_action')
