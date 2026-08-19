import asyncio
import websockets
import sys

async def receive_messages(websocket):
    """Фоновая задача для получения сообщений от сервера."""
    try:
        async for message in websocket:
            # Убрали жестко заданное слово "[Друг]"
            # Теперь мы просто выводим то, что пришло (там уже будет никнейм)
            print(f"\r{message}\n> ", end="", flush=True)
    except websockets.exceptions.ConnectionClosed:
        print("\n[Система]: Соединение с сервером потеряно.")
        sys.exit()

async def send_messages(websocket, nickname):
    """Задача для чтения текста с клавиатуры и отправки на сервер."""
    while True:
        message = await asyncio.to_thread(input, "> ")
        
        if message.lower() in ['/exit', '/quit']:
            print("Выход из Velix...")
            await websocket.close()
            sys.exit()
            
        if message.strip():
            # Прикрепляем никнейм к сообщению перед отправкой
            formatted_message = f"[{nickname}]: {message}"
            await websocket.send(formatted_message)

async def main():
    print(f"--- Добро пожаловать в Velix ---")
    
    # 1. Запрашиваем никнейм
    nickname = input("Введите ваш никнейм: ").strip()
    if not nickname:
        nickname = "Аноним" # Если пользователь просто нажал Enter
        
    # 2. Запрашиваем IP сервера
    server_ip = input("Введите IP-адрес сервера (нажмите Enter для localhost): ").strip()
    if not server_ip:
        server_ip = "localhost"
    
    uri = f"ws://{server_ip}:8765"
    print(f"Подключение к {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"[Система]: Успешно подключено как {nickname}! Можно писать сообщения. (для выхода введите /exit)\n")
            
            # Передаем никнейм в функцию отправки
            receive_task = asyncio.create_task(receive_messages(websocket))
            send_task = asyncio.create_task(send_messages(websocket, nickname))
            
            await asyncio.gather(receive_task, send_task)
            
    except ConnectionRefusedError:
        print("\n[Ошибка]: Сервер недоступен. Проверьте, запущен ли он.")

if __name__ == "__main__":
    asyncio.run(main())
