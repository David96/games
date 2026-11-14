import asyncio
import json
import websockets
import websockets.asyncio.server
import random
import string
import sys

from game_room import GameRoom
from wizard.wizard import Wizard
from bots import BotManager

NDIGITS = 6
MAXROOMS = 20
# Time players have to rejoin a room only bots are left in before it is
# deleted together with its bots
REJOIN_TIMEOUT = 10.0
rooms = dict[str, GameRoom]()
# Set in main() once the port is known; every room gets a reference
bot_manager = None

async def send_error(websocket, message, error_type):
    await websocket.send(json.dumps({'type': 'error', 'error': error_type, 'msg': message}))

async def create_room(websocket, data):
    print("Creating new room")
    while (room_id := ''.join(random.choice(string.ascii_uppercase + string.digits)
                              for _ in range(NDIGITS))) in rooms:
        pass
    print(f"ID: {room_id}")
    if len(rooms) >= MAXROOMS:
        await send_error(websocket, 'Too many rooms exist already!', 'too_many_rooms');
        return
    room = GameRoom(Wizard, room_id)
    room.bots = bot_manager
    rooms[room_id] = room
    data['room'] = room_id
    await join_room(websocket, data)

async def join_room(websocket, data):
    name = data['name']
    room = rooms.get(data['room'])
    if not name:
        await send_error(websocket, 'Name must not be empty!', 'empty_name')
    elif not room:
        await send_error(websocket, 'Invalid room ID!', 'invalid_room')
    else:
        print(f"Joining room {room.room_id} as {name}")
        await room.join(name, websocket)

async def _delete_room(room_id: str):
    room = rooms.pop(room_id, None)
    if room:
        for username, _ in room.users.copy().items():
            await room.remove_player(username, True)
        if room.bots:
            room.bots.stop_room(room_id)

def get_user(websocket):
    for room in rooms.values():
        for n, w in room.users.items():
            if w == websocket:
                return n, room
    return None, None

def _only_bots_left(room):
    """Only bots are connected: they never act on their own, so the room
    is dead unless a human rejoins."""
    return bool(room.users) and room.bots and \
        set(room.users) <= set(room.bots.bots_in(room.room_id))

async def _delete_room_if_abandoned(room):
    # Give players time to rejoin before their bots are terminated with
    # the room; a human joining in between cancels the deletion
    await asyncio.sleep(REJOIN_TIMEOUT)
    if room.room_id in rooms and _only_bots_left(room):
        await _delete_room(room.room_id)

ACTIONS = {
        'create_room': create_room,
        'join': join_room
}
async def serve(websocket):
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if 'action' in data and data['action'] in ACTIONS:
                    await ACTIONS[data['action']](websocket, data)
                else:
                    name, room = get_user(websocket)
                    if name and room:
                        await room.on_message(name, message)
            except json.JSONDecodeError as e:
                await send_error(websocket, 'Malformed message! Ignoring.', 'malformed')
            except Exception as e:
                await send_error(websocket, str(e), 'internal_error')
    finally:
        n, room = get_user(websocket)
        if n and room:
            await room.leave(n)
            # Drop the room if nobody is left in it; if only bots are,
            # give players some time to rejoin first
            if not room.users and not room.waiting_for:
                await _delete_room(room.room_id)
            elif _only_bots_left(room):
                asyncio.ensure_future(_delete_room_if_abandoned(room))

async def main():
    global bot_manager
    port = int(sys.argv[1]) if len(sys.argv) == 2 else 6791
    bot_manager = BotManager(url=f"ws://127.0.0.1:{port}")
    try:
        async with websockets.asyncio.server.serve(serve, "localhost", port) as server:
            await server.serve_forever()
    finally:
        bot_manager.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
