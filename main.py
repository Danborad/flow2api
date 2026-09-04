"""Flow2API - Main Entry Point"""
import socket

from src.main import app
import uvicorn

if __name__ == "__main__":
    from src.core.config import config

    sockets = []
    host = config.server_host
    port = config.server_port

    if host in {"0.0.0.0", "::", "[::]", ""}:
        ipv4_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ipv4_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ipv4_socket.bind(("0.0.0.0", port))
        ipv4_socket.listen(2048)
        ipv4_socket.setblocking(False)
        sockets.append(ipv4_socket)

        try:
            ipv6_socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            ipv6_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv6_socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            ipv6_socket.bind(("::", port))
            ipv6_socket.listen(2048)
            ipv6_socket.setblocking(False)
            sockets.append(ipv6_socket)
        except OSError as exc:
            print(f"IPv6 listener unavailable, continuing with IPv4 only: {exc}")

        print(f"Server running on IPv4 0.0.0.0:{port} and IPv6 [::]:{port}")
    else:
        uvicorn.run("src.main:app", host=host, port=port, reload=False)
        raise SystemExit

    server_config = uvicorn.Config("src.main:app", host=host, port=port, reload=False)
    uvicorn.Server(server_config).run(sockets=sockets)
