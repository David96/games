import asyncio
import json
import websockets

from game_room import GameRoom
from wizard.wizard import Wizard

wizard = GameRoom(Wizard)

async def serve(websocket, path):
    name = None
    try:
        async for message in websocket:
            data = json.loads(message)
            if 'action' in data and data['action'] == 'join':
                name = data['name']
                if not name:
                    await wizard.send_error(websocket, 'Name must not be empty!', 'empty_name')
                elif not await wizard.join(name, websocket):
                    await wizard.send_error(websocket,
                            "Name already taken or game currently running.", "name_taken")
            elif name:
                await wizard.on_message(name, message)
    finally:
        await wizard.leave(name)


start_server = websockets.serve(serve, "localhost", 6791)

asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
