import asyncio
import json
import websockets
import websockets.asyncio.server
import random
import string
import sys

from game_room import GameRoom
from wizard.wizard import Wizard

NDIGITS = 6
MAXROOMS = 20
rooms = dict[str, GameRoom]()

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
    rooms[room_id] = GameRoom(Wizard, room_id)
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
    room = rooms[room_id]
    if room:
        for username, _ in room.users.copy().items():
            await room.remove_player(username, True)
        rooms.pop(room_id)

def get_user(websocket):
    for room in rooms.values():
        for n, w in room.users.items():
            if w == websocket:
                return n, room
    return None, None

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
            # Only drop the room if nobody is expected to rejoin it either
            if not room.users and not room.waiting_for:
                await _delete_room(room.room_id)

async def main():
    port = int(sys.argv[1]) if len(sys.argv) == 2 else 6791
    async with websockets.asyncio.server.serve(serve, "localhost", port) as server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
