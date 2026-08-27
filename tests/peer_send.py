"""Отдельный процесс-сосед: заводит группу и отправляет текст с вложением."""
import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol
import websockets

# Подпись PNG и немного нулей: собираем из чисел, чтобы не возиться с
# экранированием в исходнике
PICTURE = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + bytes(500)


async def main():
    async with websockets.connect("ws://localhost:8765",
                                  max_size=protocol.MAX_FRAME_SIZE) as ws:
        await ws.send(protocol.register_message("lena", "пароль123", "Лена"))
        answer = protocol.decode(await ws.recv())
        if answer["type"] != "welcome":
            await ws.send(protocol.login_message("lena", "пароль123"))
            answer = protocol.decode(await ws.recv())

        # Общего чата нет: находим соседей среди людей и зовём их в группу
        people = None
        while (people or {}).get("type") != "people":
            people = protocol.decode(await ws.recv())
        others = [person["id"] for person in people["items"]
                  if person["id"] != answer["user"]["id"]]

        await ws.send(protocol.group_request("Общая", others))
        frame = None
        while (frame or {}).get("type") != "conversation":
            frame = protocol.decode(await ws.recv())
        room = frame["item"]["id"]

        await ws.send(protocol.text_message("Лена", "привет из другого клиента", room))
        await ws.send(protocol.media_header("Лена", "image", "битая.png",
                                            len(PICTURE), room))
        await ws.send(PICTURE)
        await asyncio.sleep(1.0)

asyncio.run(main())
print("сосед отправил")
