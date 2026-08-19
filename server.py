import asyncio
import websockets

# Множество для хранения всех активных подключений
connected_clients = set()

async def chat_handler(websocket):
    """Функция обрабатывает каждое новое подключение."""
    # Регистрируем нового клиента
    connected_clients.add(websocket)
    print(f"[Сервер]: Новое подключение! Активных пользователей: {len(connected_clients)}")
    
    try:
        # Бесконечный цикл прослушивания сообщений от этого клиента
        async for message in websocket:
            print(f"[Лог]: Получено сообщение -> '{message}'")
            
            # Пересылаем сообщение всем остальным подключенным клиентам
            for client in connected_clients:
                if client != websocket:
                    await client.send(message)
                    
    except websockets.exceptions.ConnectionClosed:
        # Срабатывает, если клиент закрыл терминал или пропал интернет
        pass 
    finally:
        # При отключении клиента удаляем его из множества
        connected_clients.remove(websocket)
        print(f"[Сервер]: Клиент отключился. Активных пользователей: {len(connected_clients)}")

async def main():
    # Запускаем WebSocket-сервер на localhost, порт 8765
    async with websockets.serve(chat_handler, "localhost", 8765):
        print("--- Сервер Velix запущен ---")
        print("Слушаем подключения на ws://localhost:8765...")
        
        # future() работает как бесконечный цикл, не давая серверу завершить работу
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Сервер]: Работа завершена.")
