import asyncio
import json
import websockets
import traceback

from game_room import GameRoom
from wizard.wizard import Wizard

wizard = GameRoom(Wizard)

async def serve(websocket, path):
    name = None
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if 'action' in data and data['action'] == 'join':
                    name = data['name']
                    if not name:
                        await wizard.send_error(websocket, 'Name must not be empty!', 'empty_name')
                    else:
                        await wizard.join(name, websocket)
                elif name:
                    await wizard.on_message(name, message)
            except json.JSONDecodeError as e:
                await wizard.send_error(websocket, 'Malformed message! Ignoring.')
            except Exception as e:
                await wizard.send_error(websocket, str(e))
                traceback.print_exc()
    finally:
        if name:
            await wizard.leave(name)


start_server = websockets.serve(serve, "localhost", 6791)

asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
