import asyncio
import json
import websockets
import websockets.asyncio.server
import traceback

from game_room import GameRoom
from wizard.wizard import Wizard

wizard = GameRoom(Wizard)

async def serve(websocket):
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

async def main():
    async with websockets.asyncio.server.serve(serve, "localhost", 6791) as server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
