from contextlib import asynccontextmanager
import socket
import subprocess
import threading
import time
import asyncio
import logging
import paramiko

from config import (
    CONTAINERIZED,
    QEMU_LOG,
    SSH_HOST,
    SSH_PORT,
    VM_START_TIMEOUT,
    VM_STOP_TIMEOUT,
)
from fastapi import FastAPI, HTTPException, WebSocket
from ssh_client import connect_ssh
from start_vm import build_command, SERIAL_SOCKET

from qmp_client import QMP_SOCKET, read_reply, send_command


@asynccontextmanager
async def lifespan(_app):
    if CONTAINERIZED:
        cleanup_stale_sockets()

    yield

    if CONTAINERIZED:
        await asyncio.to_thread(shutdown_container_vm)


app = FastAPI(lifespan=lifespan)
VM_START_LOCK = threading.Lock()
GUEST_READY_COMMAND = "systemctl is-active --quiet systemd-logind"
GUEST_READY_LOG_CHANNEL = "paramiko.transport.guest-ready"
GUEST_READY_LOGGER = logging.getLogger(GUEST_READY_LOG_CHANNEL)
GUEST_READY_LOGGER.addHandler(logging.NullHandler())
GUEST_READY_LOGGER.propagate = False


def stopped_status():
    return {
        "running": False,
        "status": "stopped",
        "guest_ready": False,
    }


@app.get("/healthz")
def healthcheck():
    return {"status": "ok"}


def ssh_available():
    try:
        with socket.create_connection((SSH_HOST, SSH_PORT), timeout=0.5) as connection:
            connection.settimeout(0.5)
            return connection.recv(64).startswith(b"SSH-")

    except OSError:
        return False


def guest_ready():
    if not ssh_available():
        return False

    try:
        client = connect_ssh(
            timeout=1,
            banner_timeout=1,
            auth_timeout=1,
            log_channel=GUEST_READY_LOG_CHANNEL,
        )

        _stdin, stdout, _stderr = client.exec_command(
            GUEST_READY_COMMAND,
            timeout=2,
        )

        return stdout.channel.recv_exit_status() == 0

    except (EOFError, OSError, paramiko.SSHException):
        return False

    finally:
        if "client" in locals():
            client.close()


def wait_for_vm_stop(timeout=VM_STOP_TIMEOUT):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if not qmp_available():
            return True

        time.sleep(0.2)

    return False


def reap_process(process):
    process.wait()


@app.get("/api/vm/status")
def get_vm_status():
    if not QMP_SOCKET.exists():
        return stopped_status()

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(QMP_SOCKET))

            with connection.makefile("r", encoding="utf-8") as reader:
                read_reply(reader)

                send_command(connection, reader, "qmp_capabilities")

                response = send_command(
                    connection,
                    reader,
                    "query-status"
                )

                qemu_status = response["return"]

                if not qemu_status.get("running"):
                    return {
                        **qemu_status,
                        "guest_ready": False,
                    }

                is_guest_ready = guest_ready()

                return {
                    **qemu_status,
                    "qemu_status": qemu_status["status"],
                    "status": "running" if is_guest_ready else "booting",
                    "guest_ready": is_guest_ready,
                }

    except ConnectionRefusedError:
        return stopped_status()

    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(
            status_code=503,
            detail=str(error)
        ) from error


@app.post("/api/vm/reboot")
def reboot_vm():
    if not QMP_SOCKET.exists():
        raise HTTPException(
            status_code=409,
            detail="Виртуальная машина не запущена"
        )

    if not guest_ready():
        raise HTTPException(
            status_code=409,
            detail="Debian ещё загружается"
        )

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(QMP_SOCKET))

            with connection.makefile("r", encoding="utf-8") as reader:
                read_reply(reader)

                send_command(connection, reader, "qmp_capabilities")

                send_command(connection, reader, "system_reset")

        return {"message": "Команда перезагрузки отправлена"}

    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=str(error)
        ) from error


@app.post("/api/vm/stop")
def stop_vm():
    if not QMP_SOCKET.exists():
        raise HTTPException(
            status_code=409,
            detail="Виртуальная машина не запущена"
        )

    if not guest_ready():
        raise HTTPException(
            status_code=409,
            detail="Debian ещё загружается"
        )

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(QMP_SOCKET))

            with connection.makefile("r", encoding="utf-8") as reader:
                read_reply(reader)

                send_command(connection, reader, "qmp_capabilities")

                send_command(connection, reader, "system_powerdown")

        if not wait_for_vm_stop():
            raise HTTPException(
                status_code=504,
                detail=f"Debian не завершила работу за {VM_STOP_TIMEOUT} секунд"
            )

        if CONTAINERIZED:
            cleanup_stale_sockets()

        return {"message": "Виртуальная машина выключена"}

    except ConnectionRefusedError:
        raise HTTPException(
            status_code=409,
            detail="QEMU не запущен"
        )

    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail=str(error)
        ) from error


def qmp_available():
    if not QMP_SOCKET.exists():
        return False

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(str(QMP_SOCKET))

            greeting = connection.recv(4096)

            return b'"QMP"' in greeting

    except OSError:
        return False


def cleanup_stale_sockets():
    if qmp_available():
        return

    for path in (QMP_SOCKET, SERIAL_SOCKET):
        path.unlink(missing_ok=True)


def request_qmp_powerdown():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(QMP_SOCKET))

        with connection.makefile("r", encoding="utf-8") as reader:
            read_reply(reader)
            send_command(connection, reader, "qmp_capabilities")
            send_command(connection, reader, "system_powerdown")


def shutdown_container_vm():
    if qmp_available():
        try:
            request_qmp_powerdown()
            wait_for_vm_stop()
        except (OSError, ValueError, RuntimeError):
            pass

    cleanup_stale_sockets()


@app.post("/api/vm/start")
def start_vm():
    with VM_START_LOCK:
        if QMP_SOCKET.exists() or SERIAL_SOCKET.exists():
            if qmp_available():
                raise HTTPException(
                    status_code=409,
                    detail="Виртуальная машина уже запущена"
                )

            if CONTAINERIZED:
                cleanup_stale_sockets()
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Обнаружены старые сокеты QEMU. Необходимо проверить состояние процесса"
                )

        try:
            with open(QEMU_LOG, "ab") as log:
                process = subprocess.Popen(
                    build_command(),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=not CONTAINERIZED
                )

                threading.Thread(
                    target=reap_process,
                    args=(process,),
                    daemon=True,
                    name=f"qemu-reaper-{process.pid}",
                ).start()

        except OSError as error:
            raise HTTPException(
                status_code=500,
                detail=str(error)
            ) from error

        deadline = time.monotonic() + VM_START_TIMEOUT

        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise HTTPException(
                    status_code=500,
                    detail=f"QEMU завершился с кодом {process.returncode}. Проверь {QEMU_LOG}"
                )

            if qmp_available():
                return {
                    "message": "QEMU запущен, Debian загружается",
                    "pid": process.pid
                }

            time.sleep(0.2)

        raise HTTPException(
            status_code=503,
            detail=f"QEMU не ответил по QMP. Проверь {QEMU_LOG}"
        )


@app.websocket("/ws/serial")
async def serial_terminal(websocket: WebSocket):
    await websocket.accept()

    try:
        reader, writer = await asyncio.open_unix_connection(
            str(SERIAL_SOCKET)
        )
    except OSError:
        await websocket.close(code=1011)
        return

    async def read_serial():
        while True:
            data = await reader.read(4096)

            if not data:
                break

            await websocket.send_bytes(data)

    async def write_serial():
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")

            if data is None:
                data = (message.get("text") or "").encode("utf-8")

            if data:
                writer.write(data)
                await writer.drain()

    tasks = [
        asyncio.create_task(read_serial()),
        asyncio.create_task(write_serial()),
    ]

    try:
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        writer.close()
        await writer.wait_closed()


def create_ssh_session():
    client = connect_ssh(timeout=10)

    try:
        channel = client.invoke_shell(
            term="xterm",
            width=100,
            height=30
        )

        return client, channel

    except Exception:
        client.close()
        raise


@app.websocket("/ws/ssh")
async def ssh_terminal(websocket: WebSocket):
    await websocket.accept()

    try:
        client, channel = await asyncio.to_thread(
            create_ssh_session
        )

    except (OSError, paramiko.SSHException) as error:
        print(f"Ошибка SSH: {error}", flush=True)
        await websocket.close(code=1011)
        return

    async def read_ssh():
        while not channel.closed:
            if channel.recv_ready():
                data = channel.recv(4096)

                if not data:
                    break

                await websocket.send_bytes(data)

            elif channel.exit_status_ready():
                break

            else:
                await asyncio.sleep(0.05)

    async def write_ssh():
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")

            if data is None:
                data = (message.get("text") or "").encode("utf-8")

            if data:
                await asyncio.to_thread(channel.sendall, data)

    tasks = [
        asyncio.create_task(read_ssh()),
        asyncio.create_task(write_ssh())
    ]

    try:
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        channel.close()
        client.close()
