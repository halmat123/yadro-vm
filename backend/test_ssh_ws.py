
import asyncio

import websockets


async def main():
    async with websockets.connect(
        "ws://127.0.0.1:8000/ws/ssh"
    ) as connection:
        print("SSH WebSocket подключён")

        await asyncio.sleep(1)

        await connection.send(
            "printf 'SSH_WS_%s\\n' WORKS\n"
        )

        output = ""

        async with asyncio.timeout(15):
            while "SSH_WS_WORKS" not in output:
                data = await connection.recv()

                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="replace")
                else:
                    text = data

                print(text, end="", flush=True)
                output += text

        print("\nПроверка SSH WebSocket успешна")


if __name__ == "__main__":
    asyncio.run(main())
