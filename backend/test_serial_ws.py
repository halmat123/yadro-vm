
import asyncio

import websockets


async def main():
    async with websockets.connect(
        "ws://127.0.0.1:8000/ws/serial"
    ) as connection:
        print("WebSocket подключён")

        await connection.send("\r")

        while True:
            data = await asyncio.wait_for(
                connection.recv(),
                timeout=10
            )

            if isinstance(data, bytes):
                text = data.decode("utf-8", errors="replace")
            else:
                text = data

            print(text, end="", flush=True)

            if "login:" in text:
                break


if __name__ == "__main__":
    asyncio.run(main())
