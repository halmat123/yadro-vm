
import json
import socket
import sys

from config import QMP_SOCKET

COMMANDS = {
    "status": "query-status",
    "reboot": "system_reset",
    "stop": "system_powerdown",
}


def read_reply(reader):
    while True:
        line = reader.readline()

        if not line:
            raise ConnectionError("QEMU закрыл соединение")

        response = json.loads(line)

        if "event" in response:
            print("Событие QEMU:", response["event"])
            continue

        return response


def send_command(connection, reader, command):
    request = {"execute": command}
    data = json.dumps(request) + "\n"

    connection.sendall(data.encode("utf-8"))

    response = read_reply(reader)

    if "error" in response:
        raise RuntimeError(response["error"])

    print(f"{command}: {response}")
    return response


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action not in COMMANDS:
        raise SystemExit("Доступные команды: status, reboot, stop")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(QMP_SOCKET))

        with connection.makefile("r", encoding="utf-8") as reader:
            greeting = read_reply(reader)
            print("Подключение к QMP установлено")
            print("Версия QEMU:", greeting["QMP"]["version"]["qemu"])

            send_command(connection, reader, "qmp_capabilities")
            send_command(connection, reader, COMMANDS[action])


if __name__ == "__main__":
    main()
